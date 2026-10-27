import io
import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import create_app, schedule
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
        r=self.client.post('/contrato/novo',data={'csrf_token':token,'tomador':'Novo contrato','tipo':'PARCELADO','principal':'1000','valorMensal':'110','taxa':'0','parcelas':'10','inicio':'2026-01-31','risco':'baixo'})
        self.assertEqual(r.status_code,302)
        backup=self.client.get('/exportar/json').json
        e=backup['emprestimos'][-1]
        self.assertEqual(len(schedule([e])),10)
        r=self.client.post('/aporte',data={'csrf_token':self.csrf('/metas'),'amount':'200','day':'2020-01-01','category':'RF'})
        self.assertEqual(r.status_code,302)
        self.assertEqual(self.client.get('/exportar/json').json['aportes'][0]['amount'],'200.00')


if __name__=='__main__':
    unittest.main()
