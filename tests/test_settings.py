import json
import re
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch
import test_app
import notifications
from werkzeug.security import check_password_hash
from werkzeug.security import generate_password_hash

TOKEN='123456789:'+('A'*35)
CHAT='-1009876543210'

class SettingsTests(unittest.TestCase):
    setUp=test_app.FinanceTests.setUp
    tearDown=test_app.FinanceTests.tearDown
    csrf=test_app.FinanceTests.csrf
    login=test_app.FinanceTests.login

    def save_telegram(self,token=TOKEN,chat=CHAT):
        return self.client.post('/configuracoes/telegram',data=dict(csrf_token=self.csrf('/'),bot_token=token,chat_id=chat,return_to='/carteira'))

    def verify_telegram(self):
        with patch('notifications.send_message',return_value=123) as sender:
            r=self.client.post('/configuracoes/telegram/testar',data=dict(csrf_token=self.csrf('/'),return_to='/carteira'))
            sender.assert_called_once_with(TOKEN,CHAT,'Prumo: mensagem de teste. A integração com este chat está funcionando.')
            return r

    def test_account_fields_read_only_and_email_migration(self):
        self.login()
        with closing(sqlite3.connect(self.database)) as conn,conn:
            conn.execute('UPDATE users SET email=? WHERE username=?',('cadastro@example.com','admin'))
        r=self.client.get('/')
        self.assertIn(b'cadastro@example.com',r.data)
        self.assertIn(b'aria-readonly="true"',r.data)
        self.assertIn('Configurações da conta'.encode(),r.data)

    def test_password_checks_and_revokes_old_sessions(self):
        self.login()
        old=self.client.get_cookie('session').value
        token=self.csrf('/')
        values=dict(csrf_token=token,current_password='wrong',new_password='new-password-456',confirm_password='new-password-456')
        self.client.post('/configuracoes/senha',data=values)
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertTrue(check_password_hash(conn.execute('SELECT password FROM users').fetchone()[0],'test-password-123'))
        values['current_password']='test-password-123'
        self.assertEqual(self.client.post('/configuracoes/senha',data=values).status_code,302)
        self.assertEqual(self.client.get('/').status_code,200)
        stolen=self.app.test_client()
        stolen.set_cookie('session',old)
        self.assertEqual(stolen.get('/').status_code,302)
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertTrue(check_password_hash(conn.execute('SELECT password FROM users').fetchone()[0],'new-password-456'))

    def test_telegram_saved_encrypted_and_verified_only_after_test(self):
        self.login()
        r=self.save_telegram()
        self.assertTrue(r.location.endswith('#settings-telegram'))
        with closing(sqlite3.connect(self.database)) as conn:
            encrypted,verified=conn.execute('SELECT token_enc,verified FROM telegram_settings').fetchone()
            self.assertNotIn(TOKEN,encrypted)
            self.assertEqual(verified,0)
        self.assertNotIn(TOKEN.encode(),self.client.get('/carteira').data)
        self.verify_telegram()
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT verified FROM telegram_settings').fetchone()[0],1)
        self.save_telegram(token='',chat='-1009999999999')
        with closing(sqlite3.connect(self.database)) as conn:
            encrypted,verified=conn.execute('SELECT token_enc,verified FROM telegram_settings').fetchone()
            self.assertEqual(notifications.decrypt_token(self.app.config['SECRET_KEY'],encrypted),TOKEN)
            self.assertEqual(verified,0)

    def test_failed_test_never_marks_verified_and_validation_blocks_bad_target(self):
        self.login()
        self.save_telegram()
        with patch('notifications.send_message',side_effect=notifications.TelegramError('Falha de envio')):
            self.client.post('/configuracoes/telegram/testar',data=dict(csrf_token=self.csrf('/')))
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT verified FROM telegram_settings').fetchone()[0],0)
        self.save_telegram(token='https://evil.test/token',chat='https://evil.test')
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT chat_id FROM telegram_settings').fetchone()[0],CHAT)

    def test_alert_requires_verified_integration_and_is_per_user(self):
        self.login()
        data=dict(csrf_token=self.csrf('/carteira'),enabled='1',threshold='5')
        self.client.post('/ativo/1/alerta',data=data)
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM price_alerts').fetchone()[0],0)
        self.save_telegram()
        self.verify_telegram()
        self.client.post('/ativo/1/alerta',data=data)
        with closing(sqlite3.connect(self.database)) as conn:
            threshold,baseline,enabled=conn.execute('SELECT threshold,baseline,enabled FROM price_alerts').fetchone()
            self.assertEqual((threshold,baseline,enabled),('5','55.44',1))
        self.assertIn('Alerta ativado'.encode(),self.client.get('/carteira').data)
        self.client.post('/ativo/1/alerta',data=dict(csrf_token=data['csrf_token'],threshold='5'))
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT enabled FROM price_alerts').fetchone()[0],0)

    def test_alerts_send_once_each_direction_using_fixed_reference(self):
        self.login()
        self.save_telegram()
        self.verify_telegram()
        self.client.post('/ativo/1/alerta',data=dict(csrf_token=self.csrf('/carteira'),enabled='1',threshold='5'))
        with self.app.app_context(),closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory=sqlite3.Row
            for price,expected in [('56',0),('60',1),('61',0),('50',1),('49',0)]:
                asset=json.loads(conn.execute('SELECT payload FROM assets WHERE id=1').fetchone()[0])
                asset['atual']=price
                conn.execute('UPDATE assets SET payload=? WHERE id=1',(json.dumps(asset),))
                conn.commit()
                notifications.queue_alerts(conn)
                with patch('notifications.send_message',return_value=123) as sender:
                    self.assertEqual(notifications.deliver_alerts(conn,self.app.config['SECRET_KEY']),expected)
                    self.assertEqual(sender.call_count,expected)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM alert_deliveries WHERE status='sent'").fetchone()[0],2)

    def test_send_response_requires_correct_chat_and_success(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,*args): return json.dumps(dict(ok=True,result=dict(message_id=1,chat=dict(id=-1000000000)))).encode()
        with patch('notifications.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=Response()
            with self.assertRaises(notifications.TelegramError):
                notifications.send_message(TOKEN,CHAT,'Teste')

    def test_settings_posts_require_csrf(self):
        self.login()
        for route in ['/configuracoes/senha','/configuracoes/telegram','/configuracoes/telegram/testar','/ativo/1/alerta']:
            self.assertEqual(self.client.post(route,data={}).status_code,400)

    def test_telegram_and_alert_settings_are_not_shared_between_users(self):
        self.login()
        self.save_telegram()
        self.verify_telegram()
        self.client.post('/ativo/1/alerta',data=dict(csrf_token=self.csrf('/carteira'),enabled='1',threshold='5'))
        with closing(sqlite3.connect(self.database)) as conn,conn:
            conn.execute('INSERT INTO users(username,password,email) VALUES (?,?,?)',('other',generate_password_hash('other-password-123'),'other@example.com'))
        other=self.app.test_client()
        csrf=re.search(rb'name="csrf_token" value="([^"]+)"',other.get('/login').data)[1].decode()
        other.post('/login',data=dict(csrf_token=csrf,username='other',password='other-password-123'))
        r=other.get('/carteira')
        self.assertNotIn(CHAT.encode(),r.data)
        self.assertNotIn(b'Alerta ativado',r.data)
        self.assertNotIn(TOKEN.encode(),r.data)
