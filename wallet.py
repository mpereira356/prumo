"""Caixa em centavos, com extrato e conciliação idempotente dos recebimentos."""
import json
import re
import secrets
from datetime import datetime, date
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_HALF_UP
from validation import normalize_extra

class WalletError(ValueError):
    pass

def cents(value):
    return int((Decimal(str(value))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))

def balance(conn):
    return sum(row[0] for row in conn.execute('SELECT amount_cents FROM wallet_entries'))

def entry(conn, amount, kind, description, reference=''):
    if not amount:
        return
    if balance(conn)+amount<0:
        raise WalletError('Saldo insuficiente na Carteira. Resgate investimentos ou adicione saldo antes desta operação.')
    if abs(amount)>10**17:
        raise WalletError('Valor acima do limite de movimentação.')
    conn.execute('INSERT INTO wallet_entries(amount_cents,kind,description,reference,created) VALUES (?,?,?,?,?)',(amount,kind,description,reference,datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat()))

def receipt_sources(conn):
    contracts={r['id']:json.loads(r['payload']) for r in conn.execute('SELECT * FROM contracts')}
    payments={r['key']:cents(r['amount']) for r in conn.execute('SELECT * FROM payments')}
    legacy={}
    result={}
    for contract_id,e in contracts.items():
        ref=e.get('walletRef',f'contract:{contract_id}')
        name=e.get('apelido') or e['tomador']
        for index,raw in enumerate(e.get('pagamentosExtras',[])):
            p=normalize_extra(raw)
            if not p.get('payment_key') and p.get('juros'):
                key=f"{p['data'][:7]}-{contract_id}"
                legacy[key]=legacy.get(key,0)+cents(p['juros'])
            if cents(p['valor']):
                result[f'{ref}:principal:{index}']=(cents(p['valor']),f'Amortização · {name} · {date.fromisoformat(p["data"]).strftime("%d/%m/%Y")}')
    for key,value in legacy.items():
        payments[key]=max(payments.get(key,0),value)
    for key,amount in payments.items():
        match=re.fullmatch(r'(\d{4})-(\d{2})-(\d+)(?:-c(\d+))?',key)
        e=contracts.get(int(match[3])) if match else None
        ref=e.get('walletRef',f'contract:{match[3]}') if e else 'legacy'
        name=(e.get('apelido') or e['tomador']) if e else 'Recebimento anterior'
        period=f'{match[2]}/{match[1]}' if match else ''
        installment=f'cheque {int(match[4])+1} · ' if match and match[4] is not None else ''
        result[f'{ref}:payment:{key}']=(amount,f'Recebimento · {name} · {installment}{period}')
    return result

def initialize(conn, force=False, credit_existing=False):
    if not force and conn.execute("SELECT 1 FROM settings WHERE key='wallet_initialized'").fetchone():
        return
    for row in conn.execute('SELECT * FROM contracts').fetchall():
        e=json.loads(row['payload'])
        if not e.get('walletRef'):
            e['walletRef']=secrets.token_hex(16)
            conn.execute('UPDATE contracts SET payload=? WHERE id=?',(json.dumps(e),row['id']))
    for reference,(amount,description) in receipt_sources(conn).items():
        conn.execute('INSERT INTO wallet_sources VALUES (?,?,?)',(reference,0 if credit_existing else amount,amount if credit_existing else 0))
        if credit_existing:
            entry(conn,amount,'receipt','Saldo inicial de '+description.lower(),reference)
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('wallet_initialized','1')")

def sync_receipts(conn):
    desired=receipt_sources(conn)
    previous={r['reference']:dict(r) for r in conn.execute('SELECT * FROM wallet_sources')}
    changes=[]
    for reference in desired.keys()|previous.keys():
        amount,description=desired.get(reference,(0,None))
        if description is None:
            latest=conn.execute("SELECT description FROM wallet_entries WHERE reference=? AND kind='receipt' ORDER BY id DESC LIMIT 1",(reference,)).fetchone()
            description=latest[0].removeprefix('Estorno · ') if latest else 'Recebimento removido ou desfeito'
        old=previous.get(reference,dict(baseline_cents=0,credited_cents=0))
        target=max(0,amount-old['baseline_cents'])
        delta=target-old['credited_cents']
        changes.append((reference,old['baseline_cents'],target,delta,description))
    # Confere o efeito líquido antes de gravar qualquer lançamento.
    if balance(conn)+sum(c[3] for c in changes)<0:
        raise WalletError('O recebimento já foi usado em investimentos. Resgate ou adicione saldo antes de desfazê-lo ou excluir o contrato.')
    for reference,baseline,target,delta,description in sorted(changes,key=lambda c:-c[3]):
        entry(conn,delta,'receipt',description if delta>=0 else 'Estorno · '+description,reference)
        conn.execute('INSERT INTO wallet_sources VALUES (?,?,?) ON CONFLICT(reference) DO UPDATE SET credited_cents=excluded.credited_cents',(reference,baseline,target))

def validate_state(data):
    if not isinstance(data,dict) or not isinstance(data.get('entries'),list) or not isinstance(data.get('sources'),list) or max(len(data['entries']),len(data['sources']))>20000:
        raise ValueError('Extrato da Carteira inválido ou acima do limite.')
    entries=[]
    ids=set()
    receipt_totals={}
    running=0
    for row in sorted(data['entries'],key=lambda r:r.get('id',0) if isinstance(r,dict) else 0):
        if not isinstance(row,dict) or set(row)!=set(('id','amount_cents','kind','description','reference','created')):
            raise ValueError('Movimentação inválida.')
        if type(row['id']) is not int or not 0<row['id']<2**63 or row['id'] in ids or type(row['amount_cents']) is not int or not 0<abs(row['amount_cents'])<=10**17:
            raise ValueError('Identificador ou valor da movimentação inválido.')
        ids.add(row['id'])
        if row['kind'] not in ('deposit','receipt','purchase','sale') or not isinstance(row['description'],str) or not 1<=len(row['description'])<=500 or not isinstance(row['reference'],str) or len(row['reference'])>300:
            raise ValueError('Descrição ou tipo da movimentação inválido.')
        stamp=datetime.fromisoformat(row['created'])
        if stamp.tzinfo is None:
            raise ValueError('Horário do extrato deve informar o fuso.')
        if row['kind'] in ('deposit','sale') and row['amount_cents']<0 or row['kind']=='purchase' and row['amount_cents']>0:
            raise ValueError('Sinal da movimentação inválido.')
        running+=row['amount_cents']
        if running<0:
            raise ValueError('Extrato possui saldo negativo.')
        if row['kind']=='receipt':
            receipt_totals[row['reference']]=receipt_totals.get(row['reference'],0)+row['amount_cents']
        entries.append(dict(row))
    sources=[]
    refs=set()
    for row in data['sources']:
        if not isinstance(row,dict) or set(row)!=set(('reference','baseline_cents','credited_cents')):
            raise ValueError('Conciliação da Carteira inválida.')
        ref=row['reference']
        if not isinstance(ref,str) or not 1<=len(ref)<=300 or ref in refs or any(type(row[k]) is not int or not 0<=row[k]<=10**17 for k in ('baseline_cents','credited_cents')):
            raise ValueError('Referência ou valor de conciliação inválido.')
        if receipt_totals.get(ref,0)!=row['credited_cents']:
            raise ValueError('Recebimentos e extrato não conferem.')
        refs.add(ref)
        sources.append(dict(row))
    if any(ref not in refs for ref in receipt_totals):
        raise ValueError('Movimentação sem origem de recebimento.')
    return dict(entries=entries,sources=sources)
