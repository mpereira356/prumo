import io
import json
import sqlite3
import unittest
from contextlib import closing
from datetime import date
from unittest.mock import patch
import test_app
import wallet
from concurrent.futures import ThreadPoolExecutor

class WalletTests(unittest.TestCase):
    setUp=test_app.FinanceTests.setUp
    tearDown=test_app.FinanceTests.tearDown
    csrf=test_app.FinanceTests.csrf
    login=test_app.FinanceTests.login
    insert_loan=test_app.FinanceTests.insert_loan
    configure_late_fees=test_app.FinanceTests.configure_late_fees
    insert_cheques=test_app.FinanceTests.insert_cheques

    def cash(self):
        with closing(sqlite3.connect(self.database)) as conn:
            return wallet.balance(conn)

    def deposit(self,amount='100',origin='Saldo inicial'):
        return self.client.post('/carteira/adicionar',data=dict(csrf_token=self.csrf('/extrato'),amount=amount,origin=origin))

    def buy(self,quantity='1',price='50'):
        return self.client.post('/ativo/novo',data=dict(csrf_token=self.csrf('/ativo/novo'),ticker='TEST11',nome='Investimento de teste',classe='RF',qtd=quantity,pm=price,atual=price))

    def test_zero_opening_and_manual_origin_in_statement(self):
        self.login()
        self.assertEqual(self.cash(),0)
        self.deposit('123.45','Transferência da conta pessoal')
        self.assertEqual(self.cash(),12345)
        self.assertIn('Transferência da conta pessoal'.encode(),self.client.get('/extrato').data)
        self.deposit('-1')
        self.assertEqual(self.cash(),12345)

    def test_purchase_cannot_exceed_balance_and_uses_acquisition_cost(self):
        self.login()
        self.assertEqual(self.buy().status_code,200)
        self.deposit('100')
        self.assertEqual(self.buy('3','50').status_code,200)
        self.assertEqual(self.cash(),10000)
        self.assertEqual(self.buy('2','50').status_code,302)
        self.assertEqual(self.cash(),0)
        self.assertEqual(self.buy('0.01','1').status_code,200)
        ledger=self.client.get('/exportar/json').json['wallet']['entries']
        self.assertEqual([r['amount_cents'] for r in ledger],[10000,-10000])

    def test_receipt_with_fees_is_credited_once_and_reversed(self):
        self.login()
        self.insert_loan()
        self.configure_late_fees()
        with patch('app.today',return_value=date(2026,1,30)):
            values=dict(csrf_token=self.csrf('/recebiveis'),key='2026-01-90')
            self.client.post('/pagamento',data=values)
            self.client.post('/pagamento',data=values)
            self.assertEqual(self.cash(),2050)
            self.client.post('/pagamento',data=dict(values,undo='1'))
            self.assertEqual(self.cash(),0)

    def test_combined_interest_and_amortization_credit_exact_total(self):
        self.login()
        self.insert_loan()
        with patch('app.today',return_value=date(2026,1,15)):
            token=self.csrf('/recebiveis')
            self.client.post('/contrato/90/pagamento',data=dict(csrf_token=token,key='2026-01-90',paid_on='2026-01-15',amount='120'))
            self.assertEqual(self.cash(),12000)
            self.client.post('/contrato/90/historico/desfazer',data=dict(csrf_token=token,count='1'))
            self.assertEqual(self.cash(),0)

    def test_spent_receipt_cannot_be_reversed_or_contract_deleted(self):
        self.login()
        self.insert_loan()
        token=self.csrf('/recebiveis')
        self.client.post('/pagamento',data=dict(csrf_token=token,key='2026-01-90'))
        self.assertEqual(self.cash(),2000)
        self.buy('1','20')
        self.assertEqual(self.cash(),0)
        self.client.post('/pagamento',data=dict(csrf_token=token,key='2026-01-90',undo='1'))
        self.client.post('/contrato/90/excluir',data=dict(csrf_token=token,confirm='90'))
        backup=self.client.get('/exportar/json').json
        self.assertEqual(len(backup['emprestimos']),1)
        self.assertEqual(backup['recebimentosConfirmados'],{'2026-01-90':'20.00'})
        self.assertEqual(self.cash(),0)

    def test_sale_credits_real_received_value_and_quantity_cannot_bypass_purchase(self):
        self.login()
        token=self.csrf('/ativo/1')
        r=self.client.post('/ativo/1',data=dict(csrf_token=token,ticker='PETR4',nome='Teste',classe='ACOES',qtd='2',pm='50',atual='70'))
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.cash(),0)
        self.client.post('/ativo/1/resgatar',data=dict(csrf_token=token,qtd='0.5',preco='60'))
        self.assertEqual(self.cash(),3000)
        asset=self.client.get('/exportar/json').json['ativos'][0]
        self.assertEqual(asset['qtd'],'0.5')
        self.client.post('/ativo/1/resgatar',data=dict(csrf_token=token,qtd='1',preco='60'))
        self.assertEqual(self.cash(),3000)

    def test_wallet_backup_roundtrip_and_legacy_import_start_zero(self):
        self.login()
        self.deposit('250')
        self.buy('2','50')
        backup=self.client.get('/exportar/json').json
        self.assertEqual(backup['versao'],4)
        self.assertEqual(self.cash(),15000)
        data=lambda b:dict(csrf_token=self.csrf('/dados'),confirm='SUBSTITUIR',backup=(io.BytesIO(json.dumps(b).encode()),'backup.json'))
        self.assertEqual(self.client.post('/dados',data=data(backup)).status_code,302)
        self.assertEqual(self.cash(),15000)
        self.assertEqual(self.client.get('/exportar/json').json['wallet'],backup['wallet'])
        bad=json.loads(json.dumps(backup))
        bad['wallet']['entries'][1]['amount_cents']=-999999
        self.assertEqual(self.client.post('/dados',data=data(bad)).status_code,200)
        self.assertEqual(self.cash(),15000)
        old=dict(backup,versao=3)
        old.pop('wallet')
        self.assertEqual(self.client.post('/dados',data=data(old)).status_code,302)
        self.assertEqual(self.cash(),0)

    def test_activation_does_not_credit_historical_receipts(self):
        self.insert_loan()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.row_factory=sqlite3.Row
            conn.execute('INSERT INTO payments VALUES (?,?,?)',('2026-01-90','20','2026-01-15'))
            conn.execute("DELETE FROM settings WHERE key='wallet_initialized'")
            wallet.initialize(conn)
            self.assertEqual(wallet.balance(conn),0)
            wallet.sync_receipts(conn)
            self.assertEqual(wallet.balance(conn),0)
            conn.execute('INSERT INTO payments VALUES (?,?,?)',('2026-02-90','20','2026-02-15'))
            wallet.sync_receipts(conn)
            self.assertEqual(wallet.balance(conn),2000)

    def test_undo_purchase_restores_both_asset_and_wallet(self):
        import re
        self.login()
        self.deposit('100')
        before=self.client.get('/exportar/json').json
        self.buy('1','80')
        self.assertEqual(self.cash(),2000)
        response=self.client.get('/carteira')
        token=re.search(rb'name="token" value="([^"]+)"',response.data)[1].decode()
        self.client.post('/desfazer',data=dict(csrf_token=self.csrf('/carteira'),token=token))
        after=self.client.get('/exportar/json').json
        self.assertEqual(self.cash(),10000)
        self.assertEqual(after['ativos'],before['ativos'])
        self.assertEqual(after['wallet'],before['wallet'])

    def test_simultaneous_purchases_cannot_spend_same_balance_twice(self):
        self.login()
        self.deposit('100')
        token=self.csrf('/ativo/novo')
        cookie=self.client.get_cookie('session').value
        def purchase(index):
            client=self.app.test_client()
            client.set_cookie('session',cookie)
            return client.post('/ativo/novo',data=dict(csrf_token=token,ticker=f'TEST{index}',nome='Compra concorrente',classe='RF',qtd='1',pm='80',atual='80')).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(purchase,[1,2]))
        self.assertEqual(sorted(results),[200,302])
        self.assertEqual(self.cash(),2000)
