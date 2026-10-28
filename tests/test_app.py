import io
import json
import re
import sqlite3
import tempfile
import unittest
import time
from contextlib import closing
from pathlib import Path
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import create_app, schedule, monthly_receipts, late_charges
from werkzeug.security import generate_password_hash


class FinanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.database=str(Path(self.tmp.name)/'test.sqlite3')
        self.app=create_app({'TESTING':True,'DATABASE':self.database,'SECRET_KEY':'test-secret-key','TRUSTED_HOSTS':['localhost']})
        self.client=self.app.test_client()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('INSERT INTO users(username,password) VALUES (?,?)',('admin',generate_password_hash('test-password-123')))
            conn.execute('DELETE FROM assets')
            conn.execute('DELETE FROM contracts')
            for ticker,price in [('PETR4',55.44),('VALE3',67.63)]:
                asset={'ticker':ticker,'nome':'Ativo de teste','classe':'ACOES','qtd':1,'pm':50,'atual':price}
                conn.execute('INSERT INTO assets(payload) VALUES (?)',(json.dumps(asset),))

    def tearDown(self):
        self.tmp.cleanup()

    def csrf(self,path='/login'):
        response=self.client.get(path)
        match=re.search(rb'name="csrf_token" value="([^"]+)"',response.data)
        self.assertIsNotNone(match,(path,response.status_code,response.data[:300]))
        return match[1].decode()

    def login(self):
        return self.client.post('/login',data={'username':'admin','password':'test-password-123','csrf_token':self.csrf()})

    def test_private_routes_and_csrf(self):
        for path in ['/','/carteira','/recebiveis','/fluxo','/metas','/dados','/exportar/json','/exportar/csv']:
            self.assertEqual(self.client.get(path).status_code,302,path)
        self.assertEqual(self.client.post('/login',data={'username':'admin','password':'test-password-123'}).status_code,400)
        self.assertEqual(self.client.get('/login',headers={'Host':'evil.example'}).status_code,400)

    def test_pages_render_and_security_headers(self):
        self.login()
        for path in ['/','/carteira','/recebiveis','/fluxo','/metas','/dados','/ativo/novo','/ativo/1','/contrato/novo','/exportar/json','/exportar/csv']:
            r=self.client.get(path)
            self.assertEqual(r.status_code,200,path)
            self.assertEqual(r.headers['X-Frame-Options'],'DENY')
            self.assertIn("script-src 'self'",r.headers['Content-Security-Policy'])
            self.assertEqual(r.headers['Cache-Control'],'no-store')

    def test_asset_validation_xss_and_persistence(self):
        self.login()
        token=self.csrf('/ativo/novo')
        values={'csrf_token':token,'ticker':'<script>alert(1)</script>','nome':'Teste','classe':'RF','qtd':'2','pm':'10.25','atual':'11.50'}
        self.assertEqual(self.client.post('/ativo/novo',data=values).status_code,302)
        r=self.client.get('/carteira')
        self.assertIn(b'&lt;script&gt;',r.data)
        self.assertNotIn(b'<script>alert(1)</script>',r.data)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            before=conn.execute('SELECT COUNT(*) FROM assets').fetchone()[0]
        values['qtd']='NaN'
        self.client.post('/ativo/novo',data=values)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM assets').fetchone()[0],before)

    def test_backup_roundtrip_atomic_invalid_import(self):
        self.login()
        backup=self.client.get('/exportar/json').json
        token=self.csrf('/dados')
        response=self.client.post('/dados',data={'csrf_token':token,'confirm':'SUBSTITUIR','backup':(io.BytesIO(json.dumps(backup).encode()),'backup.json')})
        self.assertEqual(response.status_code,302)
        from validation import validate_backup
        self.assertEqual(validate_backup(self.client.get('/exportar/json').json,date.today())['emprestimos'],validate_backup(backup,date.today())['emprestimos'])
        before=self.client.get('/exportar/json').json['ativos']
        backup['ativos'][0]['qtd']='Infinity'
        r=self.client.post('/dados',data={'csrf_token':self.csrf('/dados'),'confirm':'SUBSTITUIR','backup':(io.BytesIO(json.dumps(backup).encode()),'bad.json')})
        self.assertIn('Nenhum dado foi alterado'.encode(),r.data)
        self.assertEqual(self.client.get('/exportar/json').json['ativos'],before)

    def test_logout_revokes_stolen_session(self):
        self.login()
        stolen=self.client.get_cookie('session').value
        self.client.post('/logout',data={'csrf_token':self.csrf('/metas')})
        self.client.set_cookie('session',stolen)
        self.assertEqual(self.client.get('/').status_code,302)

    def test_login_throttled(self):
        token=self.csrf()
        for _ in range(5):
            r=self.client.post('/login',data={'csrf_token':token,'username':'wrong','password':'wrong'})
            self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.post('/login',data={'csrf_token':token,'username':'wrong','password':'wrong'}).status_code,429)

    def test_payment_idempotency_and_future_rejected(self):
        self.login()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            e={'tomador':'Teste','tipo':'UNICO','status':'ATIVO','inicio':'2020-01-10','parcelas':1,'valorMensal':'99.99'}
            conn.execute('INSERT INTO contracts(id,payload) VALUES (?,?)',(99,json.dumps(e)))
        token=self.csrf('/fluxo')
        for _ in range(2):
            self.client.post('/pagamento',data={'csrf_token':token,'key':'2020-01-99'})
        with closing(sqlite3.connect(self.database)) as conn, conn:
            rows=conn.execute('SELECT amount FROM payments WHERE key=?',('2020-01-99',)).fetchall()
            self.assertEqual(rows,[('99.99',)])
        self.client.post('/pagamento',data={'csrf_token':token,'key':'2020-01-99','undo':'1'})
        with closing(sqlite3.connect(self.database)) as conn, conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM payments').fetchone()[0],0)

    def test_schedule_dates_and_amortization_history(self):
        e={'id':1,'tomador':'Teste','tipo':'JUROS_COM_AMORTIZACAO','status':'ATIVO','inicio':'2026-01-31','parcelas':3,'principal':'1000','principalAtual':'500','taxa':'2','pagamentosExtras':[{'data':'2026-02-10','valor':'500'}]}
        rows=schedule([e])
        self.assertEqual([r['due'] for r in rows],['2026-01-31','2026-02-28','2026-03-31'])
        self.assertEqual([r['amount'] for r in rows],[Decimal('20.00'),Decimal('10.00'),Decimal('10.00')])

    def test_monthly_receipts_groups_values_and_handles_year_boundary(self):
        rows=[dict(due=d,amount=Decimal(a),paid=Decimal(p)) for d,a,p in [
            ('2026-11-30','999','0'),('2026-12-10','100.10','100.10'),
            ('2026-12-20','200.20','0'),('2027-01-01','50.50','0'),
            ('2027-06-01','888','0')]]
        months=monthly_receipts(rows,date(2026,12,27))
        self.assertEqual(len(months),6)
        self.assertEqual(months[0],dict(key='2026-12',label='Dezembro / 2026',total=Decimal('300.30'),paid=Decimal('100.10'),pending=Decimal('200.20')))
        self.assertEqual(months[1]['total'],Decimal('50.50'))
        self.assertEqual(months[-1]['key'],'2027-05')
        self.assertEqual(months[-1]['total'],0)
        self.assertEqual(sum(m['total'] for m in months),Decimal('350.80'))

    def test_quote_update_keeps_token_on_server_and_preserves_failed_prices(self):
        self.login()
        token=self.csrf('/carteira')
        with patch.dict('os.environ',{'BRAPI_TOKEN':'test-token-do-not-expose'}), patch('quotes.fetch_quotes',return_value={'PETR4':'60.01'}) as mocked:
            self.assertEqual(self.client.post('/cotacoes',data={'csrf_token':token}).status_code,302)
            self.assertEqual(mocked.call_args.args[1],'test-token-do-not-expose')
        r=self.client.get('/carteira')
        self.assertNotIn(b'test-token-do-not-expose',r.data)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            rows=[json.loads(r[0]) for r in conn.execute('SELECT payload FROM assets')]
        self.assertEqual(next(a for a in rows if a['ticker']=='PETR4')['atual'],'60.01')
        self.assertEqual(next(a for a in rows if a['ticker']=='VALE3')['atual'],67.63)
        self.assertEqual(self.client.post('/cotacoes',data={'csrf_token':token}).status_code,302)

    def test_contract_and_contribution_workflows(self):
        self.login()
        token=self.csrf('/contrato/novo')
        r=self.client.post('/contrato/novo',data={'csrf_token':token,'moraPct':'0','multaPct':'0','tomador':'Novo contrato','tipo':'PARCELADO','principal':'1000','valorMensal':'110','taxa':'0','parcelas':'10','inicio':'2026-01-31','risco':'baixo'})
        self.assertEqual(r.status_code,302)
        backup=self.client.get('/exportar/json').json
        e=backup['emprestimos'][-1]
        self.assertEqual(len(schedule([e])),10)
        r=self.client.post('/aporte',data={'csrf_token':self.csrf('/metas'),'amount':'200','day':'2020-01-01','category':'RF'})
        self.assertEqual(r.status_code,302)
        self.assertEqual(self.client.get('/exportar/json').json['aportes'][0]['amount'],'200.00')

    def test_dollar_private_cached_and_global(self):
        self.assertEqual(self.client.get('/api/dolar').status_code,401)
        self.login()
        quote={'bid':'5.1234','change':'0.42','timestamp':int(time.time())}
        with patch('quotes.fetch_dollar',return_value=quote) as provider:
            for _ in range(2):
                response=self.client.get('/api/dolar')
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json['quote']['bid'],'5.1234')
                self.assertFalse(response.json['stale'])
            provider.assert_called_once()
        for route in ['/','/carteira','/recebiveis','/fluxo','/metas','/dados','/ativo/novo']:
            self.assertIn(b'id="dollar-quote"',self.client.get(route).data)

    def test_dollar_failure_keeps_last_quote_and_retries_later(self):
        self.login()
        quote={'bid':'5.10','change':'-0.12','timestamp':int(time.time())}
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('INSERT INTO exchange_rates VALUES (?,?,?)',('USD-BRL',json.dumps(quote),0))
        with patch('quotes.fetch_dollar',side_effect=OSError('offline')) as provider:
            for _ in range(2):
                r=self.client.get('/api/dolar')
                self.assertEqual(r.status_code,200)
                self.assertEqual(r.json['quote']['bid'],'5.10')
                self.assertTrue(r.json['stale'])
            provider.assert_called_once()

    def test_dollar_unavailable_without_inventing_value(self):
        self.login()
        with patch('quotes.fetch_dollar',side_effect=OSError('offline')) as provider:
            for _ in range(2):
                r=self.client.get('/api/dolar')
                self.assertEqual(r.status_code,503)
                self.assertIsNone(r.json['quote'])
            provider.assert_called_once()

    def insert_loan(self, contract_id=90):
        e=dict(id=contract_id,tomador='Contrato sintético',tipo='JUROS_COM_AMORTIZACAO',status='ATIVO',risco='baixo',principal='1000',principalAtual='1000',taxa='2',valorMensal='20',parcelas=3,inicio='2026-01-15',fim='2026-03-15')
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('INSERT INTO contracts(id,payload) VALUES (?,?)',(contract_id,json.dumps(e)))
        return e

    def test_contract_edit_and_protected_terms_after_receipt(self):
        self.login()
        self.insert_loan()
        values=dict(csrf_token=self.csrf('/contrato/90/editar'),moraPct='0',multaPct='0',tomador='Nome corrigido',apelido='Apelido',tipo='JUROS_COM_AMORTIZACAO',principal='1000',principalAtual='1000',taxa='2',valorMensal='20',parcelas='3',inicio='2026-01-15',fim='2026-03-15',risco='medio')
        self.assertEqual(self.client.post('/contrato/90/editar',data=values).status_code,302)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('INSERT INTO payments VALUES (?,?,?)',('2026-01-90','20.00','2026-01-15'))
        values['taxa']='3'
        r=self.client.post('/contrato/90/editar',data=values)
        self.assertEqual(r.status_code,200)
        self.assertIn('preservar o histórico'.encode(),r.data)
        e=self.client.get('/exportar/json').json['emprestimos'][0]
        self.assertEqual(e['taxa'],'2')
        values['taxa']='2'
        values['tomador']='Outro nome'
        self.assertEqual(self.client.post('/contrato/90/editar',data=values).status_code,302)
        self.assertEqual(self.client.get('/exportar/json').json['emprestimos'][0]['tomador'],'Outro nome')

    def test_combined_payment_idempotency_recalculation_and_reversal(self):
        self.login()
        self.insert_loan()
        with patch('app.today',return_value=date(2026,2,15)):
            token=self.csrf('/contrato/90/pagamento')
            values=dict(csrf_token=token,key='2026-01-90',paid_on='2026-02-15',amount='120')
            self.assertEqual(self.client.post('/contrato/90/pagamento',data=values).status_code,302)
            self.client.post('/contrato/90/pagamento',data=values)
            backup=self.client.get('/exportar/json').json
            e=backup['emprestimos'][0]
            self.assertEqual(e['principalAtual'],'900.00')
            self.assertEqual(len(e['pagamentosExtras']),1)
            self.assertEqual(e['pagamentosExtras'][0]['juros'],'20.00')
            self.assertEqual(e['pagamentosExtras'][0]['valor'],'100.00')
            self.assertEqual(backup['recebimentosConfirmados']['2026-01-90'],'20.00')
            self.assertEqual(schedule([e])[-1]['amount'],Decimal('18.00'))
            self.assertIn(b'120,00',self.client.get('/recebiveis').data)
            self.client.post('/pagamento',data={'csrf_token':token,'key':'2026-01-90','undo':'1'})
            self.assertIn('2026-01-90',self.client.get('/exportar/json').json['recebimentosConfirmados'])
            self.client.post('/contrato/90/historico/desfazer',data={'csrf_token':token,'count':'1'})
            backup=self.client.get('/exportar/json').json
            self.assertEqual(backup['emprestimos'][0]['principalAtual'],'1000.00')
            self.assertEqual(backup['emprestimos'][0]['pagamentosExtras'],[])
            self.assertEqual(backup['recebimentosConfirmados'],{})

    def test_combined_payment_rejects_underpayment_and_future_date(self):
        self.login()
        self.insert_loan()
        with patch('app.today',return_value=date(2026,2,15)):
            token=self.csrf('/contrato/90/pagamento')
            for amount,day in [('15','2026-02-15'),('20','2026-02-16'),('2000','2026-02-15')]:
                self.client.post('/contrato/90/pagamento',data=dict(csrf_token=token,key='2026-01-90',paid_on=day,amount=amount))
            e=self.client.get('/exportar/json').json['emprestimos'][0]
            self.assertEqual(e['principalAtual'],'1000')
            self.assertEqual(e['pagamentosExtras'],[])

    def test_delete_contract_removes_only_its_payments(self):
        self.login()
        self.insert_loan(9)
        self.insert_loan(90)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.executemany('INSERT INTO payments VALUES (?,?,?)',[('2026-01-9','20','2026-01-15'),('2026-01-90','20','2026-01-15')])
        token=self.csrf('/recebiveis')
        self.assertEqual(self.client.post('/contrato/9/excluir',data={'csrf_token':token}).status_code,400)
        self.assertEqual(self.client.post('/contrato/9/excluir',data={'csrf_token':token,'confirm':'9'}).status_code,302)
        backup=self.client.get('/exportar/json').json
        self.assertEqual([e['id'] for e in backup['emprestimos']],[90])
        self.assertEqual(backup['recebimentosConfirmados'],{'2026-01-90':'20'})

    def test_new_html_payment_history_import_preserves_breakdown(self):
        from validation import validate_backup
        e=self.insert_loan()
        e['principalAtual']='900'
        e['pagamentosExtras']=[dict(data='2026-01-15',valorPago=120,juros=20,amortizacao=100,principalAntes=1000,principalDepois=900)]
        backup=dict(versao=2,ativos=[],emprestimos=[e],aportes=[],recebimentosConfirmados={})
        result=validate_backup(backup,date(2026,2,15))
        p=result['emprestimos'][0]['pagamentosExtras'][0]
        self.assertEqual(p['valor'],'100')
        self.assertEqual(p['juros'],'20')
        self.assertEqual(p['principalDepois'],'900')
        self.login()
        token=self.csrf('/dados')
        self.assertEqual(self.client.post('/dados',data={'csrf_token':token,'confirm':'SUBSTITUIR','backup':(io.BytesIO(json.dumps(backup).encode()),'backup.json')}).status_code,302)
        with patch('app.today',return_value=date(2026,2,15)):
            r=self.client.get('/recebiveis')
            self.assertIn(b'120,00',r.data)
            self.assertIn(b'900,00',r.data)

    def test_combined_full_repayment_marks_settled_and_can_be_reversed(self):
        self.login()
        self.insert_loan()
        with patch('app.today',return_value=date(2026,1,15)):
            token=self.csrf('/contrato/90/pagamento')
            self.client.post('/contrato/90/pagamento',data=dict(csrf_token=token,key='2026-01-90',paid_on='2026-01-15',amount='1020'))
            backup=self.client.get('/exportar/json').json
            self.assertEqual(backup['emprestimos'][0]['status'],'QUITADO')
            self.assertEqual(len(schedule(backup['emprestimos'])),1)
            self.assertEqual(schedule(backup['emprestimos'])[0]['amount'],Decimal('20.00'))
            self.client.post('/contrato/90/historico/desfazer',data={'csrf_token':token,'count':'1'})
            self.assertEqual(self.client.get('/exportar/json').json['emprestimos'][0]['status'],'ATIVO')

    def test_partial_legacy_interest_keeps_remaining_amount(self):
        self.login()
        e=self.insert_loan()
        e['pagamentosExtras']=[dict(data='2026-01-15',valorPago=5,juros=5,amortizacao=0,principalAntes=1000,principalDepois=1000)]
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('UPDATE contracts SET payload=? WHERE id=90',(json.dumps(e),))
        with patch('app.today',return_value=date(2026,1,15)):
            r=self.client.get('/fluxo')
            self.assertIn(b'Parcial',r.data)
            self.assertIn(b'5,00',r.data)
            token=self.csrf('/contrato/90/pagamento')
            r=self.client.post('/contrato/90/pagamento',data=dict(csrf_token=token,key='2026-01-90',paid_on='2026-01-15',amount='15'))
            self.assertEqual(r.status_code,302)
            backup=self.client.get('/exportar/json').json
            self.assertEqual(backup['recebimentosConfirmados']['2026-01-90'],'20.00')
            self.assertEqual(backup['emprestimos'][0]['principalAtual'],'1000.00')

    def insert_cheques(self):
        e=dict(id=80,tomador='Cheques sintéticos',tipo='CHEQUES',status='ATIVO',risco='medio',principal='330',inicio='2027-01-01',cheques=[dict(data='2027-01-10',valor='110'),dict(data='2027-01-20',valor='220')])
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('INSERT INTO contracts(id,payload) VALUES (?,?)',(80,json.dumps(e)))

    def test_cheque_status_transitions_keep_each_cheque_and_totals(self):
        self.login()
        self.insert_cheques()
        with patch('app.today',return_value=date(2026,10,27)):
            token=self.csrf('/recebiveis')
            values=dict(csrf_token=token,key='2027-01-80-c1',status='late')
            self.assertEqual(self.client.post('/contrato/80/cheque',data=values).status_code,302)
            e=self.client.get('/exportar/json').json['emprestimos'][0]
            self.assertEqual(e['situacaoCheques'],{'2027-01-80-c1':'EM_ATRASO'})
            self.assertIn(b'Em atraso',self.client.get('/recebiveis').data)
            values['status']='paid'
            for _ in range(2):
                r=self.client.post('/contrato/80/cheque',data=values)
                self.assertTrue(r.location.endswith('#schedule-80'))
            backup=self.client.get('/exportar/json').json
            self.assertEqual(backup['recebimentosConfirmados'],{'2027-01-80-c1':'220.00'})
            self.assertEqual(len(backup['emprestimos'][0]['cheques']),2)
            self.assertEqual(backup['emprestimos'][0]['situacaoCheques'],{})
            values['status']='on_time'
            self.client.post('/contrato/80/cheque',data=values)
            backup=self.client.get('/exportar/json').json
            self.assertEqual(backup['recebimentosConfirmados'],{})
            self.assertEqual(backup['emprestimos'][0]['situacaoCheques'],{'2027-01-80-c1':'NO_PRAZO'})

    def test_cheque_status_backup_roundtrip_and_invalid_targets(self):
        self.login()
        self.insert_cheques()
        self.insert_loan()
        token=self.csrf('/recebiveis')
        for path,key,status,expected in [('/contrato/80/cheque','2027-01-80-c0','invalid',400),('/contrato/80/cheque','2027-01-90-c0','paid',404),('/contrato/90/cheque','2026-01-90','paid',404)]:
            self.assertEqual(self.client.post(path,data=dict(csrf_token=token,key=key,status=status)).status_code,expected)
        self.assertEqual(self.client.post('/contrato/80/cheque',data=dict(key='2027-01-80-c0',status='paid')).status_code,400)
        self.client.post('/contrato/80/cheque',data=dict(csrf_token=token,key='2027-01-80-c0',status='late'))
        backup=self.client.get('/exportar/json').json
        self.client.post('/dados',data={'csrf_token':token,'confirm':'SUBSTITUIR','backup':(io.BytesIO(json.dumps(backup).encode()),'backup.json')})
        restored=self.client.get('/exportar/json').json
        self.assertEqual(restored['emprestimos'][0]['situacaoCheques'],{'2027-01-80-c0':'EM_ATRASO'})

    def test_schedule_confirmations_for_all_non_cheque_types(self):
        self.login()
        token=self.csrf('/recebiveis')
        for index,kind in enumerate(['PARCELADO','JUROS_COM_AMORTIZACAO','JUROS_MENSAIS','UNICO'],100):
            e=dict(id=index,tomador='Parcela de teste',tipo=kind,status='ATIVO',risco='baixo',principal='1000',taxa='2',valorMensal='20',parcelas=1,inicio='2090-01-15',fim='2090-01-15')
            with closing(sqlite3.connect(self.database)) as conn, conn:
                conn.execute('INSERT INTO contracts(id,payload) VALUES (?,?)',(index,json.dumps(e)))
            data=dict(csrf_token=token,key=f'2090-01-{index}',source='schedule')
            for _ in range(2):
                response=self.client.post('/pagamento',data=data)
                self.assertTrue(response.location.endswith(f'#schedule-{index}'))
            backup=self.client.get('/exportar/json').json
            self.assertEqual(backup['recebimentosConfirmados'][data['key']],'20.00')
            self.assertEqual(len(schedule([next(c for c in backup['emprestimos'] if c['id']==index)])),1)
            self.client.post('/pagamento',data=dict(data,undo='1'))
            self.assertNotIn(data['key'],self.client.get('/exportar/json').json['recebimentosConfirmados'])

    def test_dollar_uses_daily_reference_when_primary_fails(self):
        self.login()
        reference=dict(bid='5.12',change=None,timestamp=int(time.time()),date=date.today().isoformat(),source='Frankfurter / BCE',kind='daily')
        with patch('quotes._fetch_awesome_dollar',side_effect=OSError('rate limited')),patch('quotes._fetch_reference_dollar',return_value=reference) as fallback:
            r=self.client.get('/api/dolar')
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json['source'],'Frankfurter / BCE')
            self.assertEqual(r.json['quote']['kind'],'daily')
            self.assertIsNone(r.json['quote']['change'])
            fallback.assert_called_once()

    def test_dollar_failure_retries_after_one_minute(self):
        self.login()
        with patch('quotes.fetch_dollar',side_effect=OSError('offline')):
            r=self.client.get('/api/dolar')
            self.assertEqual(r.json['retry_after'],60)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('UPDATE exchange_rates SET attempted=?',(time.time()-61,))
        quote=dict(bid='5.1',change='0.2',timestamp=int(time.time()),source='AwesomeAPI')
        with patch('quotes.fetch_dollar',return_value=quote) as provider:
            r=self.client.get('/api/dolar')
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json['retry_after'],300)
            provider.assert_called_once()

    def test_daily_reference_validates_pair_and_numeric_value(self):
        from quotes import _fetch_reference_dollar
        for base,rate in [('EUR','5'),('USD','NaN'),('USD','-1')]:
            with patch('quotes._currency_json',return_value=dict(base=base,quote='BRL',rate=rate,date='2026-01-01')):
                with self.assertRaises(ValueError):
                    _fetch_reference_dollar()

    def configure_late_fees(self, contract_id=90, mora='1', multa='2'):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            e=json.loads(conn.execute('SELECT payload FROM contracts WHERE id=?',(contract_id,)).fetchone()[0])
            e.update(moraPct=mora,multaPct=multa)
            conn.execute('UPDATE contracts SET payload=? WHERE id=?',(json.dumps(e),contract_id))

    def test_late_fee_formula_single_fine_and_simple_daily_interest(self):
        contract=dict(moraPct='1',multaPct='2')
        for day,expected in [(date(2025,12,31),'1000.00'),(date(2026,1,1),'1000.00'),(date(2026,1,16),'1025.00'),(date(2026,1,31),'1030.00'),(date(2026,2,15),'1035.00')]:
            charge=late_charges(contract,'1000','2026-01-01',day)
            self.assertEqual(charge['amount'],Decimal(expected))
        charge=late_charges(contract,'1000','2026-01-01',date(2026,1,31),'400')
        self.assertEqual(charge['fine_amount'],Decimal('12.00'))
        self.assertEqual(charge['late_interest'],Decimal('6.00'))
        self.assertEqual(late_charges({},'1000','2026-01-01',date(2026,1,31))['amount'],1000)

    def test_contract_missing_fee_warning_and_explicit_zero_configuration(self):
        self.login()
        self.insert_loan()
        self.assertIn('Configuração de atraso incompleta'.encode(),self.client.get('/recebiveis').data)
        values=dict(csrf_token=self.csrf('/contrato/90/editar'),tomador='Contrato sintético',tipo='JUROS_COM_AMORTIZACAO',principal='1000',principalAtual='1000',taxa='2',valorMensal='20',parcelas='3',inicio='2026-01-15',fim='2026-03-15',risco='baixo')
        self.assertEqual(self.client.post('/contrato/90/editar',data=values).status_code,200)
        values.update(moraPct='0',multaPct='0')
        self.assertEqual(self.client.post('/contrato/90/editar',data=values).status_code,302)
        self.assertNotIn('Configuração de atraso incompleta'.encode(),self.client.get('/recebiveis').data)
        values['moraPct']='NaN'
        self.assertEqual(self.client.post('/contrato/90/editar',data=values).status_code,200)
        self.assertEqual(self.client.get('/exportar/json').json['emprestimos'][0]['moraPct'],'0')

    def test_confirmed_late_charges_freeze_backup_and_reversal(self):
        self.login()
        self.insert_loan()
        self.configure_late_fees()
        with patch('app.today',return_value=date(2026,1,30)):
            token=self.csrf('/recebiveis')
            self.assertIn(b'20,50',self.client.get('/recebiveis').data)
            self.client.post('/pagamento',data=dict(csrf_token=token,key='2026-01-90'))
            backup=self.client.get('/exportar/json').json
            self.assertEqual(backup['recebimentosConfirmados']['2026-01-90'],'20.50')
            detail=backup['emprestimos'][0]['encargosConfirmados']['2026-01-90']
            self.assertEqual(detail,dict(base='20.00',multa='0.40',mora='0.10',dias=15,data='2026-01-30'))
            self.assertEqual(self.client.post('/dados',data={'csrf_token':token,'confirm':'SUBSTITUIR','backup':(io.BytesIO(json.dumps(backup).encode()),'backup.json')}).status_code,302)
        with patch('app.today',return_value=date(2026,2,10)):
            self.assertIn(b'20,50',self.client.get('/recebiveis').data)
            self.client.post('/pagamento',data=dict(csrf_token=token,key='2026-01-90'))
            self.assertEqual(self.client.get('/exportar/json').json['recebimentosConfirmados']['2026-01-90'],'20.50')
            self.client.post('/pagamento',data=dict(csrf_token=token,key='2026-01-90',undo='1'))
            self.assertIn(b'20,57',self.client.get('/recebiveis').data)
            self.assertEqual(self.client.get('/exportar/json').json['emprestimos'][0].get('encargosConfirmados'),{})

    def test_combined_payment_separates_late_charges_from_amortization(self):
        self.login()
        self.insert_loan()
        self.configure_late_fees()
        with patch('app.today',return_value=date(2026,1,30)):
            token=self.csrf('/contrato/90/pagamento')
            r=self.client.post('/contrato/90/pagamento',data=dict(csrf_token=token,key='2026-01-90',paid_on='2026-01-20',amount='120.43'))
            self.assertEqual(r.status_code,302)
            backup=self.client.get('/exportar/json').json
            e=backup['emprestimos'][0]
            self.assertEqual(e['principalAtual'],'900.00')
            p=e['pagamentosExtras'][0]
            self.assertEqual(p['juros'],'20.00')
            self.assertEqual(p['multa'],'0.40')
            self.assertEqual(p['mora'],'0.03')
            self.assertEqual(p['valor'],'100.00')
            self.assertEqual(backup['recebimentosConfirmados']['2026-01-90'],'20.43')
            self.assertEqual(self.client.post('/dados',data={'csrf_token':token,'confirm':'SUBSTITUIR','backup':(io.BytesIO(json.dumps(backup).encode()),'backup.json')}).status_code,302)

    def test_cheque_confirmation_includes_late_charges(self):
        self.login()
        self.insert_cheques()
        self.configure_late_fees(80)
        with patch('app.today',return_value=date(2027,1,25)):
            self.client.post('/contrato/80/cheque',data=dict(csrf_token=self.csrf('/recebiveis'),key='2027-01-80-c0',status='paid'))
            backup=self.client.get('/exportar/json').json
            self.assertEqual(backup['recebimentosConfirmados']['2027-01-80-c0'],'112.75')
            self.assertEqual(backup['emprestimos'][0]['encargosConfirmados']['2027-01-80-c0']['dias'],15)


if __name__=='__main__':
    unittest.main()
