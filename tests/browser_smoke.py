"""QA opcional: requer Playwright e Chromium já instalados no ambiente."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
import secrets
import sqlite3
import tempfile
import threading
import json
import time
from datetime import timedelta
from unittest.mock import patch
from contextlib import closing
from pathlib import Path
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect
from app import create_app, today

def main():
    with tempfile.TemporaryDirectory() as folder:
        database=str(Path(folder)/'browser.sqlite3')
        app=create_app({'TESTING':True,'DATABASE':database})
        password=secrets.token_urlsafe(24)
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute('INSERT INTO users(username,password,email) VALUES (?,?,?)',('preview',generate_password_hash(password),'preview@example.com'))
            contract={'tomador':'Contrato de teste','tipo':'PARCELADO','status':'ATIVO','risco':'baixo','principal':800,'valorMensal':110,'parcelas':8,'inicio':'2026-11-10','fim':'2027-06-10'}
            conn.execute('INSERT INTO contracts(id,payload) VALUES (?,?)',(9901,json.dumps(contract)))
            interest_contract=dict(tomador='Juros de teste',tipo='JUROS_COM_AMORTIZACAO',status='ATIVO',risco='medio',principal='1000',principalAtual='1000',taxa='2',valorMensal='20',parcelas=3,inicio='2020-01-15',fim='2020-03-15')
            conn.execute('INSERT INTO contracts(id,payload) VALUES (?,?)',(9902,json.dumps(interest_contract)))
            cheques=dict(tomador='Cheques de teste',tipo='CHEQUES',status='ATIVO',risco='baixo',principal='330',inicio='2027-01-01',cheques=[dict(data='2027-01-10',valor='110'),dict(data='2027-01-20',valor='220')])
            conn.execute('INSERT INTO contracts(id,payload) VALUES (?,?)',(9903,json.dumps(cheques)))
            fee_due=(today()-timedelta(days=15)).isoformat()
            fee_contract=dict(tomador='Contrato com encargos',tipo='PARCELADO',status='ATIVO',risco='baixo',principal='900',valorMensal='1000',parcelas=1,inicio=fee_due,fim=fee_due,moraPct='1',multaPct='2')
            conn.execute('INSERT INTO contracts(id,payload) VALUES (?,?)',(9904,json.dumps(fee_contract)))
        server=make_server('127.0.0.1',5058,app)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch('notifications.send_message',return_value=123), sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
                page.route('**/api/dolar',lambda route:route.fulfill(json={'quote':{'bid':'5.1234','change':'0.42','timestamp':int(time.time())},'stale':False,'source':'AwesomeAPI'}))
                errors=[]
                page.on('pageerror',lambda error: errors.append(str(error)))
                page.on('console',lambda message: errors.append(message.text) if message.type=='error' else None)
                page.goto('http://127.0.0.1:5058/login')
                page.get_by_label('Usuário',exact=True).fill('preview')
                page.get_by_label('Senha',exact=True).fill(password)
                page.get_by_role('button',name='Entrar no painel').click()
                page.wait_for_url('http://127.0.0.1:5058/')
                page.wait_for_function("document.getElementById('dollar-value').textContent.includes('5,1234')")
                Path('artifacts').mkdir(exist_ok=True)
                page.screenshot(path='artifacts/dashboard-desktop.png',full_page=True)
                page.get_by_role('button',name='Configurações da conta',exact=True).click()
                settings=page.get_by_role('dialog',name='Configurações',exact=True)
                expect(settings).to_be_visible()
                assert settings.get_by_label('Login',exact=True).input_value()=='preview'
                assert settings.get_by_label('Login',exact=True).get_attribute('readonly') is not None
                assert settings.get_by_label('E-mail de cadastro',exact=True).input_value()=='preview@example.com'
                settings.get_by_role('button',name='Configurar Telegram',exact=True).click()
                settings.locator('[name="bot_token"]').fill('123456789:'+('A'*35))
                settings.locator('[name="chat_id"]').fill('-1001234567890')
                settings.get_by_role('button',name='Salvar alteração',exact=True).click()
                page.wait_for_url('**/#settings-telegram')
                settings=page.get_by_role('dialog',name='Configurações',exact=True)
                expect(settings.locator('.telegram-status')).to_contain_text('Não verificado')
                settings.get_by_role('button',name='Enviar mensagem de teste',exact=True).click()
                page.wait_for_load_state('networkidle')
                settings=page.get_by_role('dialog',name='Configurações',exact=True)
                expect(settings.locator('.telegram-status .badge')).to_have_text('Verificado')
                assert ('123456789:'+('A'*35)) not in page.content()
                page.screenshot(path='artifacts/telegram-settings.png',full_page=False)
                settings.get_by_role('button',name='Fechar configurações',exact=True).click()
                page.goto('http://127.0.0.1:5058/extrato')
                page.locator('[name="amount"]').fill('500')
                page.locator('[name="origin"]').fill('Saldo inicial de teste')
                page.get_by_role('button',name='Adicionar saldo',exact=True).click()
                page.wait_for_url('**/extrato')
                expect(page.locator('.wallet-value')).to_have_text('R$\u00a0500,00')
                page.goto('http://127.0.0.1:5058/ativo/novo')
                for name,value in [('ticker','PRUMO-TEST'),('nome','Posição de teste'),('qtd','2'),('pm','100'),('atual','105')]:
                    page.locator(f'[name="{name}"]').fill(value)
                expect(page.locator('#purchase-cost')).to_have_text('R$\u00a0200,00')
                expect(page.locator('#purchase-remaining')).to_have_text('R$\u00a0300,00')
                page.get_by_role('button',name='Salvar posição',exact=True).click()
                page.wait_for_url('**/carteira')
                expect(page.locator('.wallet-value')).to_have_text('R$\u00a0300,00')
                page.get_by_role('button',name='Configurar alerta de PRUMO-TEST',exact=True).click()
                alert=page.get_by_role('dialog',name='Alerta · PRUMO-TEST',exact=True)
                alert.locator('[name="enabled"]').check()
                alert.locator('[name="threshold"]').fill('5')
                alert.get_by_role('button',name='Salvar alerta',exact=True).click()
                page.wait_for_url('**/carteira')
                expect(page.get_by_role('button',name='Configurar alerta de PRUMO-TEST',exact=True)).to_have_attribute('title','Alerta ativado')
                page.goto('http://127.0.0.1:5058/extrato')
                expect(page.get_by_text('Saldo inicial de teste',exact=True)).to_be_visible()
                expect(page.get_by_text('Compra · PRUMO-TEST · 2 unidades',exact=True)).to_be_visible()
                for route in ['carteira','recebiveis','fluxo','metas','dados','ativo/novo','contrato/novo']:
                    response=page.goto('http://127.0.0.1:5058/'+route)
                    assert response.status==200,(route,response.status)
                page.goto('http://127.0.0.1:5058/recebiveis')
                trigger=page.get_by_role('button',name='Ver cronograma de Contrato de teste',exact=True)
                assert trigger.inner_text()=='8'
                trigger.click()
                dialog=page.get_by_role('dialog',name='Contrato de teste',exact=True)
                assert dialog.is_visible()
                assert dialog.locator('tbody tr').count()==8
                assert '10/11/2026' in dialog.inner_text() and '10/06/2027' in dialog.inner_text()
                assert '880,00' in dialog.inner_text()
                dialog.locator('tbody tr').first.get_by_role('button',name='Pagamento confirmado',exact=True).click()
                page.wait_for_url('**/recebiveis#schedule-9901')
                dialog=page.get_by_role('dialog',name='Contrato de teste',exact=True)
                assert dialog.is_visible() and dialog.locator('tbody tr').count()==8
                assert dialog.locator('tbody tr').first.locator('.badge').inner_text()=='Pago'
                page.once('dialog',lambda confirm:confirm.accept())
                dialog.locator('tbody tr').first.get_by_role('button',name='Desfazer pagamento',exact=True).click()
                page.wait_for_load_state('networkidle')
                dialog=page.get_by_role('dialog',name='Contrato de teste',exact=True)
                assert dialog.is_visible()
                assert dialog.locator('tbody tr').first.locator('.badge').inner_text()!='Pago'
                trigger=page.get_by_role('button',name='Ver cronograma de Contrato de teste',exact=True)
                page.keyboard.press('Escape')
                assert not dialog.is_visible()
                expect(trigger).to_be_focused()
                interest_card=page.locator('article.contract').filter(has=page.get_by_role('heading',name='Juros de teste',exact=True))
                interest_card.get_by_role('button',name='Histórico',exact=True).click()
                history=page.get_by_role('dialog',name='Histórico · Juros de teste',exact=True)
                assert history.is_visible()
                history.get_by_role('button',name='Fechar histórico').click()
                interest_card.get_by_role('link',name='Editar',exact=True).click()
                assert page.get_by_label('Tomador / nome',exact=True).input_value()=='Juros de teste'
                page.locator('[name="moraPct"]').fill('0')
                page.locator('[name="multaPct"]').fill('0')
                page.get_by_role('button',name='Salvar alterações',exact=True).click()
                page.wait_for_url('**/recebiveis')
                interest_card=page.locator('article.contract').filter(has=page.get_by_role('heading',name='Juros de teste',exact=True))
                assert interest_card.locator('.charge-warning').count()==0
                page.goto('http://127.0.0.1:5058/contrato/9902/pagamento')
                page.get_by_label('Valor total recebido (R$)',exact=True).fill('120')
                assert '100,00' in page.locator('#preview-reduction').inner_text()
                assert '900,00' in page.locator('#preview-balance').inner_text()
                page.once('dialog',lambda confirm:confirm.accept())
                page.get_by_role('button',name='Confirmar pagamento',exact=True).click()
                page.wait_for_url('**/recebiveis')
                interest_card=page.locator('article.contract').filter(has=page.get_by_role('heading',name='Juros de teste',exact=True))
                interest_card.get_by_role('button',name='Histórico',exact=True).click()
                history=page.get_by_role('dialog',name='Histórico · Juros de teste',exact=True)
                assert '120,00' in history.inner_text() and '900,00' in history.inner_text()
                history.get_by_role('button',name='Fechar histórico').click()
                page.goto('http://127.0.0.1:5058/')
                page.get_by_role('button',name='Alternar tema').click()
                page.screenshot(path='artifacts/dashboard-dark.png',full_page=True)
                page.get_by_role('button',name='Alternar tema').click()
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.evaluate('[...document.querySelectorAll("body *")].filter(e=>e.getBoundingClientRect().right>innerWidth).map(e=>[e.tagName,e.className,e.getBoundingClientRect().right])')
                page.screenshot(path='artifacts/dashboard-mobile.png',full_page=True)
                page.get_by_role('button',name='Abrir navegação').click()
                page.get_by_role('link',name='Meus investimentos',exact=False).click()
                page.wait_for_url('**/carteira')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Overflow mobile na carteira'
                page.goto('http://127.0.0.1:5058/recebiveis')
                page.get_by_role('button',name='Ver cronograma de Contrato de teste',exact=True).click()
                dialog=page.get_by_role('dialog',name='Contrato de teste',exact=True)
                assert dialog.is_visible()
                assert dialog.evaluate('(el)=>el.getBoundingClientRect().right <= innerWidth')
                page.screenshot(path='artifacts/schedule-mobile.png',full_page=True)
                dialog.get_by_role('button',name='Fechar cronograma').click()
                assert not dialog.is_visible()
                page.get_by_role('button',name='Ver cronograma de Cheques de teste',exact=True).click()
                cheque_dialog=page.get_by_role('dialog',name='Cheques de teste',exact=True)
                first=cheque_dialog.locator('tbody tr').first
                first.get_by_role('button',name='Pagamento confirmado',exact=True).click()
                page.wait_for_url('**/recebiveis#schedule-9903')
                cheque_dialog=page.get_by_role('dialog',name='Cheques de teste',exact=True)
                assert cheque_dialog.is_visible()
                assert cheque_dialog.locator('tbody tr').count()==2
                first=cheque_dialog.locator('tbody tr').first
                assert first.locator('.badge').inner_text()=='Pago'
                page.once('dialog',lambda confirm:confirm.accept())
                first.get_by_role('button',name='Em atraso',exact=True).click()
                page.wait_for_load_state('networkidle')
                cheque_dialog=page.get_by_role('dialog',name='Cheques de teste',exact=True)
                assert cheque_dialog.is_visible()
                assert cheque_dialog.locator('tbody tr').first.locator('.badge').inner_text()=='Em atraso'
                cheque_dialog.locator('tbody tr').first.get_by_role('button',name='No prazo',exact=True).click()
                page.wait_for_load_state('networkidle')
                cheque_dialog=page.get_by_role('dialog',name='Cheques de teste',exact=True)
                assert cheque_dialog.locator('tbody tr').first.locator('.badge').inner_text()=='No prazo'
                page.screenshot(path='artifacts/cheques-mobile.png',full_page=True)
                cheque_dialog.get_by_role('button',name='Fechar cronograma').click()
                page.get_by_role('button',name='Ver cronograma de Contrato com encargos',exact=True).click()
                fee_dialog=page.get_by_role('dialog',name='Contrato com encargos',exact=True)
                assert '1.025,00' in fee_dialog.inner_text()
                assert '20,00' in fee_dialog.inner_text() and '5,00' in fee_dialog.inner_text()
                fee_dialog.get_by_role('button',name='Pagamento confirmado',exact=True).click()
                page.wait_for_url('**/recebiveis#schedule-9904')
                fee_dialog=page.get_by_role('dialog',name='Contrato com encargos',exact=True)
                assert fee_dialog.locator('.badge').inner_text()=='Pago'
                assert '1.025,00' in fee_dialog.inner_text()
                expect(page.locator('#undo-toast')).to_be_visible()
                assert fee_dialog.locator('#undo-toast').count()==1
                page.locator('#undo-form').get_by_role('button',name='Desfazer',exact=True).click()
                page.wait_for_url('**/recebiveis')
                expect(page.locator('#undo-toast')).to_have_count(0)
                page.get_by_role('button',name='Ver cronograma de Contrato com encargos',exact=True).click()
                fee_dialog=page.get_by_role('dialog',name='Contrato com encargos',exact=True)
                assert fee_dialog.locator('.badge').inner_text()!='Pago'
                fee_dialog.get_by_role('button',name='Pagamento confirmado',exact=True).click()
                page.wait_for_url('**/recebiveis#schedule-9904')
                expect(page.locator('#undo-toast')).to_be_visible()
                expect(page.locator('#undo-toast')).to_have_count(0,timeout=6500)
                page.screenshot(path='artifacts/late-charges-mobile.png',full_page=True)
                page.route('**/api/dolar',lambda route:route.fulfill(json={'quote':{'bid':'5.2345','change':None,'timestamp':int(time.time()),'kind':'daily','source':'Frankfurter / BCE'},'stale':False,'source':'Frankfurter / BCE'}))
                page.goto('http://127.0.0.1:5058/')
                expect(page.locator('#dollar-value')).to_have_text('R$\u00a05,2345')
                expect(page.locator('#dollar-status')).to_contain_text('Referência diária')
                expect(page.locator('#dollar-change')).to_be_hidden()
                page.get_by_role('button',name='Abrir navegação',exact=True).click()
                page.get_by_role('button',name='Configurações da conta',exact=True).click()
                settings=page.get_by_role('dialog',name='Configurações',exact=True)
                expect(settings).to_be_visible()
                assert settings.evaluate('(el)=>el.getBoundingClientRect().right <= innerWidth')
                settings.get_by_role('tab',name='Integração Telegram',exact=True).click()
                expect(settings.locator('.telegram-status .badge')).to_have_text('Verificado')
                page.screenshot(path='artifacts/telegram-settings-mobile.png',full_page=False)
                settings.get_by_role('button',name='Fechar configurações',exact=True).click()
                assert not errors, errors
                browser.close()
                print('Browser QA: desktop, mobile, dark mode, navigation and console OK.')
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

if __name__=='__main__':
    main()
