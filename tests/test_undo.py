import io
import json
import re
import sqlite3
import time
import unittest
from contextlib import closing
import test_app


class UndoTests(unittest.TestCase):
    setUp=test_app.FinanceTests.setUp
    tearDown=test_app.FinanceTests.tearDown
    csrf=test_app.FinanceTests.csrf
    login=test_app.FinanceTests.login
    insert_loan=test_app.FinanceTests.insert_loan
    configure_late_fees=test_app.FinanceTests.configure_late_fees

    def undo_form(self,path='/metas'):
        response=self.client.get(path)
        self.assertIn(b'id="undo-toast"',response.data)
        token=re.search(rb'name="token" value="([^"]+)"',response.data)[1].decode()
        csrf=re.search(rb'name="csrf_token" value="([^"]+)"',response.data)[1].decode()
        return dict(token=token,csrf_token=csrf)

    def add_contribution(self,amount='123'):
        self.client.post('/aporte',data=dict(csrf_token=self.csrf('/metas'),amount=amount,day='2020-01-01',category='RF'))

    def test_undo_once_with_exact_five_second_window(self):
        self.login()
        self.add_contribution()
        form=self.undo_form()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            activated,expires=conn.execute('SELECT activated,expires FROM undo_actions').fetchone()
            self.assertEqual(expires-activated,5)
        self.client.get('/metas')
        with closing(sqlite3.connect(self.database)) as conn, conn:
            self.assertEqual(conn.execute('SELECT expires FROM undo_actions').fetchone()[0],expires)
        self.assertEqual(self.client.post('/desfazer',data=form).status_code,302)
        self.assertEqual(self.client.get('/exportar/json').json['aportes'],[])
        self.assertNotIn(b'id="undo-toast"',self.client.get('/metas').data)
        self.client.post('/desfazer',data=form)
        self.assertEqual(self.client.get('/exportar/json').json['aportes'],[])

    def test_expired_window_does_not_change_data(self):
        self.login()
        self.add_contribution()
        form=self.undo_form()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('UPDATE undo_actions SET expires=?',(time.time()-1,))
        self.client.post('/desfazer',data=form)
        self.assertEqual(len(self.client.get('/exportar/json').json['aportes']),1)

    def test_refuses_to_overwrite_a_later_change(self):
        self.login()
        self.add_contribution('100')
        first=self.undo_form()
        self.add_contribution('200')
        second=self.undo_form()
        self.client.post('/desfazer',data=first)
        self.assertEqual(len(self.client.get('/exportar/json').json['aportes']),2)
        self.client.post('/desfazer',data=second)
        self.assertEqual([a['amount'] for a in self.client.get('/exportar/json').json['aportes']],['100.00'])

    def test_session_ownership_and_csrf(self):
        self.login()
        self.add_contribution()
        form=self.undo_form()
        self.assertEqual(self.client.post('/desfazer',data={'token':form['token']}).status_code,400)
        other=self.app.test_client()
        login_csrf=re.search(rb'name="csrf_token" value="([^"]+)"',other.get('/login').data)[1].decode()
        other.post('/login',data=dict(csrf_token=login_csrf,username='admin',password='test-password-123'))
        own_csrf=re.search(rb'name="csrf_token" value="([^"]+)"',other.get('/metas').data)[1].decode()
        other.post('/desfazer',data=dict(csrf_token=own_csrf,token=form['token']))
        self.assertEqual(len(self.client.get('/exportar/json').json['aportes']),1)
        self.client.post('/desfazer',data=form)
        self.assertEqual(self.client.get('/exportar/json').json['aportes'],[])

    def test_deleted_contract_restores_payment_and_charge_history(self):
        self.login()
        self.insert_loan()
        self.configure_late_fees()
        token=self.csrf('/recebiveis')
        self.client.post('/pagamento',data=dict(csrf_token=token,key='2026-01-90'))
        before=self.client.get('/exportar/json').json
        self.client.post('/contrato/90/excluir',data=dict(csrf_token=token,confirm='90'))
        self.assertEqual(self.client.get('/exportar/json').json['emprestimos'],[])
        self.client.post('/desfazer',data=self.undo_form('/recebiveis'))
        after=self.client.get('/exportar/json').json
        self.assertEqual(after['emprestimos'],before['emprestimos'])
        self.assertEqual(after['recebimentosConfirmados'],before['recebimentosConfirmados'])

    def test_import_restores_all_financial_data(self):
        self.login()
        self.insert_loan()
        before=self.client.get('/exportar/json').json
        backup=dict(before,ativos=[],emprestimos=[])
        self.client.post('/dados',data={'csrf_token':self.csrf('/dados'),'confirm':'SUBSTITUIR','backup':(io.BytesIO(json.dumps(backup).encode()),'backup.json')})
        self.assertEqual(self.client.get('/exportar/json').json['ativos'],[])
        self.client.post('/desfazer',data=self.undo_form('/'))
        after=self.client.get('/exportar/json').json
        for field in ('ativos','emprestimos','settings','aportes','recebimentosConfirmados'):
            self.assertEqual(after[field],before[field])

    def test_payment_undo_restores_charge_details_together(self):
        self.login()
        self.insert_loan()
        self.configure_late_fees()
        self.client.post('/pagamento',data=dict(csrf_token=self.csrf('/recebiveis'),key='2026-01-90',source='schedule'))
        form=self.undo_form('/recebiveis')
        self.client.post('/desfazer',data=form)
        backup=self.client.get('/exportar/json').json
        self.assertEqual(backup['recebimentosConfirmados'],{})
        self.assertNotIn('encargosConfirmados',backup['emprestimos'][0])
