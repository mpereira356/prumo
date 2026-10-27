"""Painel pessoal: execute `flask --app app create-admin` antes de entrar."""
import calendar
import csv
import hashlib
import io
import json
import os
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

import click
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for, Response
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import SecurityError

ROOT = Path(__file__).resolve().parent
CLASSES = {'ACOES': 'Ações', 'FIIs': 'Fundos imobiliários', 'TESOURO': 'Tesouro Direto', 'RF': 'Renda fixa', 'PREV': 'Previdência', 'BDR': 'BDRs', 'ETF': 'ETFs', 'COE': 'COE'}
TYPES = {'PARCELADO': 'Parcelado', 'CHEQUES': 'Cheques', 'JUROS_COM_AMORTIZACAO': 'Juros e amortização', 'JUROS_MENSAIS': 'Juros mensais', 'UNICO': 'Pagamento único'}

def money(value):
    return Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)

def number(value, minimum=0, maximum=100000000000):
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d < minimum or d > maximum:
            raise ValueError()
        return d
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Informe um número válido dentro do limite permitido.')

def text(value, limit=150):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('Preencha os campos de texto respeitando o limite de caracteres.')
    return value.strip()

def today():
    # Offset de Brasília, independente do fuso horário do servidor.
    return datetime.now(ZoneInfo('America/Sao_Paulo')).date()

def add_month(d, months):
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))

def schedule(contracts):
    rows = []
    for e in contracts:
        if e.get('status') not in ('ATIVO','QUITADO'):
            continue
        start = date.fromisoformat(e['inicio'])
        if e['tipo'] == 'CHEQUES':
            for i, c in enumerate(e.get('cheques', [])):
                rows.append(dict(key=f"{c['data'][:7]}-{e['id']}-c{i}", contract=e['id'], name=e.get('apelido') or e['tomador'], due=c['data'], amount=money(c['valor']), kind='Cheque'))
        else:
            count = e.get('parcelas', 1)
            if e['tipo'] == 'JUROS_MENSAIS':
                end = date.fromisoformat(e['fim']) if e.get('fim') else add_month(today(), 36)
                count = max(0, (end.year-start.year)*12+end.month-start.month+1)
            for i in range(count):
                due = add_month(start, i)
                if e.get('fim') and due > date.fromisoformat(e['fim']):
                    break
                value = money(e.get('valorMensal', 0))
                if e['tipo'] == 'JUROS_COM_AMORTIZACAO':
                    balance = Decimal(str(e.get('principalAtual', e['principal'])))
                    # Reconstitui o saldo anterior para preservar juros históricos.
                    balance += sum((Decimal(str(p['valor'])) for p in e.get('pagamentosExtras', []) if p['data'] > due.isoformat()), Decimal(0))
                    value = money(balance * Decimal(str(e['taxa'])) / 100)
                if i == 0 and e.get('primeiraParcela'):
                    value = money(e['primeiraParcela'])
                if value > 0:
                    rows.append(dict(key=f"{due:%Y-%m}-{e['id']}", contract=e['id'], name=e.get('apelido') or e['tomador'], due=due.isoformat(), amount=value, kind=TYPES[e['tipo']]))
    return sorted(rows, key=lambda r: (r['due'], r['key']))

def create_app(test_config=None):
    instance_path=os.environ.get('PRUMO_INSTANCE_PATH')
    app = Flask(__name__, instance_relative_config=True, **({'instance_path':str(Path(instance_path).resolve())} if instance_path else {}))
    Path(app.instance_path).mkdir(parents=True,exist_ok=True)
    production = os.environ.get('APP_ENV') == 'production'
    key = os.environ.get('SECRET_KEY')
    if production and (not key or len(key) < 32 or key.startswith('gere-')):
        raise RuntimeError('Produção exige SECRET_KEY aleatória com pelo menos 32 caracteres.')
    if not key:
        key_file = Path(app.instance_path) / 'secret.key'
        try:
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        key = key_file.read_text()
    app.config.update(SECRET_KEY=key, DATABASE=str(Path(app.instance_path)/'financeiro.sqlite3'),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=production,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=30), MAX_CONTENT_LENGTH=2*1024*1024,
        MAX_FORM_MEMORY_SIZE=256*1024, MAX_FORM_PARTS=100,
        TRUSTED_HOSTS=os.environ.get('TRUSTED_HOSTS', 'localhost,127.0.0.1').split(','), PRODUCTION=production)
    if test_config:
        app.config.update(test_config)
    CSRFProtect(app)

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(app.config['DATABASE'], timeout=10)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys=ON')
        return g.db

    @app.teardown_appcontext
    def close_db(error=None):
        if 'db' in g:
            g.pop('db').close()

    with app.app_context():
        db().executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS assets(id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS contracts(id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payments(key TEXT PRIMARY KEY, amount TEXT NOT NULL, paid_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS contributions(id INTEGER PRIMARY KEY, amount TEXT NOT NULL, day TEXT NOT NULL, category TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts(bucket TEXT NOT NULL, stamp REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS attempts_bucket ON attempts(bucket, stamp);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL, stamp TEXT NOT NULL);
        ''')
        if not db().execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone():
            seed=ROOT/'data/initial.json'
            if not seed.is_file():
                seed=ROOT/'data/initial.example.json'
            initial = json.loads(seed.read_text(encoding='utf-8'))
            db().executemany('INSERT INTO assets(payload) VALUES (?)', [(json.dumps(a),) for a in initial['ativos']])
            db().executemany('INSERT INTO contracts(id,payload) VALUES (?,?)', [(e['id'],json.dumps(e)) for e in initial['emprestimos']])
            db().executemany('INSERT INTO settings VALUES (?,?)', [('initialized','1'),('goal','300000'),('rate','0.8')])
            db().commit()

    def audit(action):
        db().execute('INSERT INTO audit(user_id,action,stamp) VALUES (?,?,?)', (g.user['id'] if g.user else None, action, datetime.now().isoformat()))

    def limited(bucket, limit, seconds):
        now = time.time()
        conn = db()
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('DELETE FROM attempts WHERE stamp < ?', (now-3600,))
        count = conn.execute('SELECT COUNT(*) FROM attempts WHERE bucket=? AND stamp>?', (bucket,now-seconds)).fetchone()[0]
        if count >= limit:
            conn.commit()
            abort(429)
        conn.execute('INSERT INTO attempts VALUES (?,?)',(bucket,now))
        conn.commit()

    @app.before_request
    def load_user():
        g.user = None
        # Também protege ambientes que ainda executam Flask 3.0.
        if request.host.split(':',1)[0].lower() not in app.config['TRUSTED_HOSTS']:
            abort(400)
        token = session.get('token')
        if token:
            g.user = db().execute('SELECT users.* FROM users JOIN sessions ON users.id=sessions.user_id WHERE sessions.token=? AND expires>?', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if request.method == 'POST' and g.user:
            limited(f"write:{g.user['id']}", 120, 60)

    @app.after_request
    def headers(response):
        response.headers.update({'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
            'X-Content-Type-Options':'nosniff', 'X-Frame-Options':'DENY', 'Referrer-Policy':'no-referrer',
            'Permissions-Policy':'camera=(), microphone=(), geolocation=()', 'Cache-Control':'no-store'})
        if app.config['PRODUCTION']:
            response.headers['Strict-Transport-Security']='max-age=31536000'
        return response

    def protected(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return redirect(url_for('login'))
            return fn(*args, **kwargs)
        return wrapped

    @app.template_filter('brl')
    def brl(value):
        return 'R$ ' + f'{money(value):,.2f}'.replace(',','_').replace('.',',').replace('_','.')

    @app.template_filter('day')
    def day(value):
        return date.fromisoformat(str(value)).strftime('%d/%m/%Y')

    @app.context_processor
    def context():
        return dict(classes=CLASSES, types=TYPES, current_user=g.get('user'), today=today().isoformat())

    @app.cli.command('create-admin')
    @click.option('--username', prompt='Usuário')
    @click.password_option(confirmation_prompt=True)
    def create_admin(username, password):
        """Cria acesso localmente; não há cadastro público."""
        if len(password) < 12 or len(password) > 128:
            raise click.ClickException('Use uma senha entre 12 e 128 caracteres.')
        try:
            db().execute('INSERT INTO users(username,password) VALUES (?,?)', (text(username,80),generate_password_hash(password,method='scrypt')))
            db().commit()
        except (sqlite3.IntegrityError,ValueError):
            raise click.ClickException('Usuário inválido ou já existente.')
        click.echo('Acesso criado. Entre no painel com este usuário e senha.')

    @app.cli.command('reset-password')
    @click.option('--username', prompt='Usuário')
    @click.password_option(confirmation_prompt=True)
    def reset_password(username, password):
        if not 12 <= len(password) <= 128:
            raise click.ClickException('Use uma senha entre 12 e 128 caracteres.')
        user=db().execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
        if not user:
            raise click.ClickException('Usuário não encontrado.')
        db().execute('UPDATE users SET password=? WHERE id=?',(generate_password_hash(password),user['id']))
        db().execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
        db().commit()
        click.echo('Senha atualizada e sessões revogadas.')

    @app.route('/login', methods=['GET','POST'])
    def login():
        if request.method == 'POST':
            limited('login:'+str(request.remote_addr), 5, 300)
            username = request.form.get('username','')[:80]
            user = db().execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
            password = request.form.get('password','')
            # Sempre calcula o hash, inclusive para usuários inexistentes.
            valid = check_password_hash(user['password'] if user else app.config['DUMMY_HASH'], password[:128])
            if user and valid and len(password)<=128:
                session.clear()
                token = secrets.token_urlsafe(32)
                session['token']=token
                session.permanent=True
                db().execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
                db().execute('INSERT INTO sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],time.time()+1800))
                db().commit()
                return redirect(url_for('dashboard'))
            flash('Usuário ou senha inválidos.', 'error')
        return render_template('login.html')

    app.config['DUMMY_HASH'] = generate_password_hash(secrets.token_urlsafe(24))

    @app.post('/logout')
    @protected
    def logout():
        db().execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(session['token'].encode()).hexdigest(),))
        db().commit()
        session.clear()
        return redirect(url_for('login'))

    def records(table):
        assert table in ('assets','contracts')
        return [dict(json.loads(r['payload']),id=r['id']) for r in db().execute(f'SELECT * FROM {table} ORDER BY id')]

    def financial():
        assets, contracts = records('assets'), records('contracts')
        for a in assets:
            a['market_value']=str(money(Decimal(str(a['qtd']))*Decimal(str(a['atual']))))
        rows=schedule(contracts)
        payments={r['key']:money(r['amount']) for r in db().execute('SELECT * FROM payments')}
        active_ids={e['id'] for e in contracts if e['status']=='ATIVO'}
        rows=[r for r in rows if r['contract'] in active_ids or r['key'] in payments]
        for r in rows:
            r['paid']=payments.get(r['key'],Decimal(0))
            if r['paid']:
                r['amount']=r['paid']
            r['overdue']=r['due']<today().isoformat() and not r['paid']
        invested=sum((money(Decimal(str(a['qtd']))*Decimal(str(a['atual']))) for a in assets),Decimal(0))
        cost=sum((money(Decimal(str(a['qtd']))*Decimal(str(a['pm']))) for a in assets),Decimal(0))
        pending=sum((r['amount']-r['paid'] for r in rows),Decimal(0))
        principal=sum((money(e.get('principalAtual',e.get('principal',0))) for e in contracts if e['status']=='ATIVO' and e['tipo'] in ('JUROS_COM_AMORTIZACAO','JUROS_MENSAIS')),Decimal(0))
        allocation={CLASSES[c]:sum((money(Decimal(str(a['qtd']))*Decimal(str(a['atual']))) for a in assets if a['classe']==c),Decimal(0)) for c in CLASSES}
        settings={r['key']:r['value'] for r in db().execute('SELECT * FROM settings')}
        amortized=sum((money(p['valor']) for e in contracts for p in e.get('pagamentosExtras',[])),Decimal(0))
        return dict(assets=assets, contracts=contracts, rows=rows, invested=invested,cost=cost, gain=invested-cost,
            pending=pending, principal=principal, allocation=allocation, settings=settings,
            total=invested+pending+principal, paid=sum(payments.values(),Decimal(0))+amortized,
            contributions=[dict(r) for r in db().execute('SELECT * FROM contributions ORDER BY day DESC,id DESC')])

    @app.get('/')
    @protected
    def dashboard():
        data=financial()
        data['upcoming']=[r for r in data['rows'] if not r['paid']][:6]
        data['overdue']=sum((r['amount'] for r in data['rows'] if r['overdue']),Decimal(0))
        data['monthly']=sum((r['amount'] for r in data['rows'] if r['due'][:7]==today().strftime('%Y-%m')),Decimal(0))
        return render_template('dashboard.html',page='dashboard',title='Visão geral',**data)

    @app.get('/carteira')
    @protected
    def portfolio():
        data=financial()
        q=request.args.get('q','').strip()[:100]
        category=request.args.get('classe','')
        data['assets']=[a for a in data['assets'] if (not category or a['classe']==category) and (not q or q.lower() in (a['nome']+' '+a['ticker']).lower())]
        return render_template('portfolio.html',page='portfolio',title='Minha carteira',q=q,category=category,**data)

    @app.post('/cotacoes')
    @protected
    def refresh_quotes():
        token=os.environ.get('BRAPI_TOKEN')
        if not token:
            flash('Configure uma nova BRAPI_TOKEN no ambiente do servidor para atualizar cotações.','error')
            return redirect(url_for('portfolio'))
        limited('quotes',1,300)
        assets=[a for a in records('assets') if a['classe'] in ('ACOES','FIIs','BDR','ETF')]
        try:
            from quotes import fetch_quotes
            prices=fetch_quotes([a['ticker'].upper() for a in assets],token)
            count=0
            for a in assets:
                if a['ticker'].upper() in prices:
                    a['atual']=prices[a['ticker'].upper()]
                    a['updated_at']=today().isoformat()
                    a['valor']=str(money(Decimal(str(a['qtd']))*Decimal(a['atual'])))
                    db().execute('UPDATE assets SET payload=? WHERE id=?',(json.dumps(a),a['id']))
                    count+=1
            audit('quotes_refreshed')
            db().commit()
            flash(f'{count} posições atualizadas; {len(assets)-count} sem cotação mantiveram o último preço.','success' if count else 'error')
        except Exception:
            # Não publica detalhes do provedor, URLs ou credenciais nos logs/HTML.
            db().rollback()
            flash('Provedor indisponível, limite do plano ou credencial inválida. Os preços anteriores foram preservados.','error')
        return redirect(url_for('portfolio'))

    @app.route('/ativo/novo',methods=['GET','POST'])
    @app.route('/ativo/<int:asset_id>',methods=['GET','POST'])
    @protected
    def asset_form(asset_id=None):
        asset=next((a for a in records('assets') if a['id']==asset_id),None)
        if asset_id and not asset:
            abort(404)
        if request.method=='POST':
            try:
                a={k:text(request.form.get(k,''),80) for k in ('ticker','nome','classe')}
                if a['classe'] not in CLASSES:
                    raise ValueError('Classe inválida.')
                for k in ('qtd','pm','atual'):
                    a[k]=str(number(request.form.get(k),Decimal('.000001') if k=='qtd' else 0))
                a['valor']=str(money(Decimal(a['qtd'])*Decimal(a['atual'])))
                a['updated_at']=today().isoformat()
                if asset_id:
                    db().execute('UPDATE assets SET payload=? WHERE id=?',(json.dumps(a),asset_id))
                else:
                    db().execute('INSERT INTO assets(payload) VALUES (?)',(json.dumps(a),))
                audit('asset_saved')
                db().commit()
                flash('Posição salva. Os totais foram recalculados.','success')
                return redirect(url_for('portfolio'))
            except ValueError as e:
                flash(str(e),'error')
                asset=request.form
        return render_template('asset_form.html',asset=asset or {},page='portfolio',title='Editar posição' if asset_id else 'Nova posição')

    @app.post('/ativo/<int:asset_id>/excluir')
    @protected
    def delete_asset(asset_id):
        db().execute('DELETE FROM assets WHERE id=?',(asset_id,))
        audit('asset_deleted')
        db().commit()
        flash('Posição excluída.','success')
        return redirect(url_for('portfolio'))

    @app.get('/recebiveis')
    @protected
    def receivables():
        return render_template('receivables.html',page='receivables',title='Recebíveis',**financial())

    @app.route('/contrato/novo',methods=['GET','POST'])
    @protected
    def contract_form():
        if request.method=='POST':
            try:
                e={k:text(request.form.get(k,''),150) for k in ('tomador','tipo','inicio')}
                if e['tipo'] not in TYPES:
                    raise ValueError('Tipo inválido.')
                start=date.fromisoformat(e['inicio'])
                e.update(principal=str(number(request.form.get('principal'))),valorMensal=str(number(request.form.get('valorMensal'))),taxa=str(number(request.form.get('taxa','0'),0,100)),status='ATIVO',risco=request.form.get('risco','medio'),obs=request.form.get('obs','')[:1000])
                e['parcelas']=int(request.form.get('parcelas','1'))
                if not 1<=e['parcelas']<=480 or e['risco'] not in ('baixo','medio','alto'):
                    raise ValueError('Informe de 1 a 480 parcelas e um risco válido.')
                if e['tipo']=='UNICO':
                    e['parcelas']=1
                if e['tipo']=='CHEQUES':
                    e['cheques']=[]
                    for line in request.form.get('cheques','').splitlines():
                        if not line.strip():
                            continue
                        due,value=line.strip().split(';')
                        date.fromisoformat(due)
                        e['cheques'].append({'data':due,'valor':str(number(value,Decimal('.01')))})
                    if not 1<=len(e['cheques'])<=100:
                        raise ValueError('Informe de 1 a 100 cheques.')
                elif e['tipo']!='JUROS_COM_AMORTIZACAO' and number(e['valorMensal'])<=0:
                    raise ValueError('A parcela deve ser maior que zero.')
                elif e['tipo']=='JUROS_COM_AMORTIZACAO' and (number(e['principal'])<=0 or number(e['taxa'])<=0):
                    raise ValueError('Informe principal e taxa maiores que zero.')
                e['fim']=add_month(start,e['parcelas']-1).isoformat()
                cur=db().execute('INSERT INTO contracts(payload) VALUES (?)',(json.dumps(e),))
                e['id']=cur.lastrowid
                db().execute('UPDATE contracts SET payload=? WHERE id=?',(json.dumps(e),e['id']))
                audit('contract_created')
                db().commit()
                flash('Contrato adicionado ao cronograma.','success')
                return redirect(url_for('receivables'))
            except (ValueError,TypeError,InvalidOperation):
                flash('Verifique os campos. Use datas válidas e cheques no formato indicado.','error')
        return render_template('contract_form.html',page='receivables',title='Novo contrato')

    @app.post('/contrato/<int:contract_id>/amortizar')
    @protected
    def amortize(contract_id):
        db().execute('BEGIN IMMEDIATE')
        e=next((e for e in records('contracts') if e['id']==contract_id),None)
        if not e or e['tipo'] not in ('JUROS_COM_AMORTIZACAO','JUROS_MENSAIS'):
            abort(404)
        try:
            principal=money(e.get('principalAtual',e['principal']))
            value=money(number(request.form.get('amount'),Decimal('.01'),principal))
            # Recebimentos já confirmados guardam o valor original.
            e['principalAtual']=str(principal-value)
            e.setdefault('pagamentosExtras',[]).append({'data':today().isoformat(),'valor':str(value)})
            db().execute('UPDATE contracts SET payload=? WHERE id=?',(json.dumps(e),contract_id))
            audit('principal_amortized')
            db().commit()
            flash('Amortização registrada. Juros futuros recalculados.','success')
        except ValueError as err:
            db().rollback()
            flash(str(err),'error')
        return redirect(url_for('receivables'))

    @app.get('/fluxo')
    @protected
    def cashflow():
        data=financial()
        months=[]
        for i in range(36):
            d=add_month(today().replace(day=1),i)
            rows=[r for r in data['rows'] if r['due'].startswith(d.strftime('%Y-%m'))]
            months.append(dict(label=d.strftime('%m/%Y'),rows=rows,total=sum((r['amount'] for r in rows),Decimal(0))))
        return render_template('cashflow.html',page='cashflow',title='Fluxo de caixa',months=months,**data)

    @app.post('/pagamento')
    @protected
    def payment():
        key=request.form.get('key','')
        row=next((r for r in schedule(records('contracts')) if r['key']==key),None)
        if not row:
            abort(404)
        if request.form.get('undo')=='1':
            db().execute('DELETE FROM payments WHERE key=?',(key,))
            audit('payment_reversed')
        else:
            if row['due']>today().isoformat():
                flash('Confirme o recebimento na data de vencimento ou após ela.','error')
                return redirect(url_for('cashflow'))
            db().execute('INSERT OR IGNORE INTO payments VALUES (?,?,?)',(key,str(row['amount']),today().isoformat()))
            audit('payment_confirmed')
        db().commit()
        flash('Recebimento atualizado.','success')
        return redirect(url_for('cashflow'))

    @app.route('/metas',methods=['GET','POST'])
    @protected
    def goals():
        if request.method=='POST':
            try:
                goal=number(request.form.get('goal'),1)
                rate=number(request.form.get('rate'),0,10)
                db().executemany('UPDATE settings SET value=? WHERE key=?',[(str(goal),'goal'),(str(rate),'rate')])
                audit('goals_updated')
                db().commit()
                flash('Meta atualizada.','success')
            except ValueError as e:
                flash(str(e),'error')
            return redirect(url_for('goals'))
        data=financial()
        goal=Decimal(data['settings']['goal'])
        data['progress']=min(100,round(data['invested']/goal*100,1))
        data['projection']=money(data['invested']*(1+Decimal(data['settings']['rate'])/100)**12)
        return render_template('goals.html',page='goals',title='Metas e aportes',**data)

    @app.post('/aporte')
    @protected
    def contribution():
        try:
            value=money(number(request.form.get('amount'),Decimal('.01')))
            d=date.fromisoformat(request.form.get('day',''))
            category=request.form.get('category','RF')
            if category not in CLASSES or d>today():
                raise ValueError('Classe ou data inválida.')
            db().execute('INSERT INTO contributions(amount,day,category) VALUES (?,?,?)',(str(value),d.isoformat(),category))
            audit('contribution_added')
            db().commit()
            flash('Aporte registrado. Atualize a posição na carteira para refletir a compra.','success')
        except ValueError as e:
            flash(str(e),'error')
        return redirect(url_for('goals'))

    @app.route('/dados',methods=['GET','POST'])
    @protected
    def data_management():
        if request.method=='POST':
            try:
                if request.form.get('confirm')!='SUBSTITUIR':
                    raise ValueError('Digite SUBSTITUIR para confirmar a importação.')
                upload=request.files.get('backup')
                if not upload:
                    raise ValueError('Selecione o backup JSON.')
                from validation import validate_backup
                backup=validate_backup(json.loads(upload.read()), today())
                # Valida tudo antes de iniciar a substituição atômica.
                for table in ('assets','contracts','payments','contributions'):
                    db().execute(f'DELETE FROM {table}')
                db().executemany('INSERT INTO assets(payload) VALUES (?)',[(json.dumps(a),) for a in backup['ativos']])
                db().executemany('INSERT INTO contracts(id,payload) VALUES (?,?)',[(e['id'],json.dumps(e)) for e in backup['emprestimos']])
                db().executemany('INSERT INTO payments VALUES (?,?,?)',[(k,str(v),today().isoformat()) for k,v in backup['recebimentosConfirmados'].items()])
                db().executemany('INSERT INTO contributions(amount,day,category) VALUES (?,?,?)',[(a['amount'],a['day'],a['category']) for a in backup['aportes']])
                for k in ('goal','rate'):
                    if k in backup['settings']:
                        db().execute('UPDATE settings SET value=? WHERE key=?',(backup['settings'][k],k))
                audit('backup_imported')
                db().commit()
                flash('Backup validado e importado.','success')
                return redirect(url_for('dashboard'))
            except (ValueError,TypeError,KeyError,OverflowError,InvalidOperation,UnicodeError) as err:
                db().rollback()
                flash('Backup inválido. Nenhum dado foi alterado. '+str(err)[:180],'error')
        return render_template('data.html',page='data',title='Backup e migração')

    @app.get('/exportar/<kind>')
    @protected
    def export(kind):
        if kind=='json':
            payload=dict(versao=3,exportadoEm=datetime.now().isoformat(),ativos=records('assets'),emprestimos=records('contracts'),
                recebimentosConfirmados={r['key']:r['amount'] for r in db().execute('SELECT * FROM payments')},
                aportes=[dict(r) for r in db().execute('SELECT * FROM contributions')],settings={r['key']:r['value'] for r in db().execute('SELECT * FROM settings')})
            return Response(json.dumps(payload,ensure_ascii=False,indent=2),mimetype='application/json',headers={'Content-Disposition':'attachment; filename=prumo-backup.json'})
        if kind!='csv':
            abort(404)
        out=io.StringIO()
        writer=csv.writer(out,delimiter=';')
        writer.writerow(['Ticker','Nome','Classe','Quantidade','Preço médio','Preço atual'])
        for a in records('assets'):
            vals=[str(a[k]) for k in ('ticker','nome','classe','qtd','pm','atual')]
            writer.writerow(["'"+v if v.startswith(('=','+','-','@','\t','\r','\n')) else v for v in vals])
        return Response('\ufeff'+out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=prumo-carteira.csv'})

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return render_template('error.html',title='Sessão do formulário expirou',message='Recarregue a página e tente novamente.'),400

    @app.errorhandler(SecurityError)
    def untrusted_host(error):
        # O roteador não existe para Hosts rejeitados: não usa url_for aqui.
        return Response('Host não autorizado.',status=400,mimetype='text/plain')

    for status,message in {400:'Requisição inválida.',404:'Página ou registro não encontrado.',413:'O conteúdo excedeu o limite de 2 MB.',429:'Muitas tentativas. Aguarde alguns minutos.',500:'Não foi possível concluir. Tente novamente.'}.items():
        def handler(error, status=status,message=message):
            if status==500:
                db().rollback()
            return render_template('error.html',title=str(status),message=message),status
        app.register_error_handler(status,handler)
    return app

app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1',port=5000,debug=False)
