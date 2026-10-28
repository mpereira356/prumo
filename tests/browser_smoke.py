"""QA opcional: requer Playwright e Chromium já instalados no ambiente."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
import secrets
import sqlite3
import tempfile
import threading
import json
import time
from contextlib import closing
from pathlib import Path
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from app import create_app

def main():
    with tempfile.TemporaryDirectory() as folder:
        database=str(Path(folder)/'browser.sqlite3')
        app=create_app({'TESTING':True,'DATABASE':database})
        password=secrets.token_urlsafe(24)
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute('INSERT INTO users(username,password) VALUES (?,?)',('preview',generate_password_hash(password)))
            contract={'tomador':'Contrato de teste','tipo':'PARCELADO','status':'ATIVO','risco':'baixo','principal':800,'valorMensal':110,'parcelas':8,'inicio':'2026-11-10','fim':'2027-06-10'}
            conn.execute('INSERT INTO contracts(id,payload) VALUES (?,?)',(9901,json.dumps(contract)))
            interest_contract=dict(tomador='Juros de teste',tipo='JUROS_COM_AMORTIZACAO',status='ATIVO',risco='medio',principal='1000',principalAtual='1000',taxa='2',valorMensal='20',parcelas=3,inicio='2020-01-15',fim='2020-03-15')
            conn.execute('INSERT INTO contracts(id,payload) VALUES (?,?)',(9902,json.dumps(interest_contract)))
            cheques=dict(tomador='Cheques de teste',tipo='CHEQUES',status='ATIVO',risco='baixo',principal='330',inicio='2027-01-01',cheques=[dict(data='2027-01-10',valor='110'),dict(data='2027-01-20',valor='220')])
            conn.execute('INSERT INTO contracts(id,payload) VALUES (?,?)',(9903,json.dumps(cheques)))
        server=make_server('127.0.0.1',5058,app)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
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
                page.keyboard.press('Escape')
                assert not dialog.is_visible()
                assert trigger.evaluate('(el)=>el===document.activeElement')
                interest_card=page.locator('article.contract').filter(has=page.get_by_role('heading',name='Juros de teste',exact=True))
                interest_card.get_by_role('button',name='Histórico',exact=True).click()
                history=page.get_by_role('dialog',name='Histórico · Juros de teste',exact=True)
                assert history.is_visible()
                history.get_by_role('button',name='Fechar histórico').click()
                interest_card.get_by_role('link',name='Editar',exact=True).click()
                assert page.get_by_label('Tomador / nome',exact=True).input_value()=='Juros de teste'
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
                page.get_by_role('link',name='Minha carteira',exact=False).click()
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
                assert not errors, errors
                browser.close()
                print('Browser QA: desktop, mobile, dark mode, navigation and console OK.')
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

if __name__=='__main__':
    main()
