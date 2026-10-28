"""Telegram privado por usuário e alertas sobre preços já disponíveis no Prumo."""
import base64
import hashlib
import json
import re
import time
import urllib.request
from decimal import Decimal
from cryptography.fernet import Fernet, InvalidToken
from quotes import NoRedirect

class TelegramError(ValueError):
    pass

def cipher(secret):
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'prumo.telegram.v1:'+secret.encode()).digest()))

def encrypt_token(secret,token):
    validate_token(token)
    return cipher(secret).encrypt(token.encode()).decode()

def decrypt_token(secret,encrypted):
    try:
        return cipher(secret).decrypt(encrypted.encode()).decode()
    except (InvalidToken,ValueError,UnicodeError):
        raise TelegramError('Não foi possível abrir o token salvo. Informe o token novamente.') from None

def validate_token(token):
    if not isinstance(token,str) or not re.fullmatch(r'\d{5,20}:[A-Za-z0-9_-]{20,120}',token):
        raise TelegramError('Informe um Bot Token válido fornecido pelo BotFather.')

def validate_chat(chat):
    if not isinstance(chat,str) or not re.fullmatch(r'-?[1-9]\d{0,19}',chat):
        raise TelegramError('Informe o Chat ID numérico do grupo, como -1001234567890.')

def send_message(token,chat,message):
    validate_token(token)
    validate_chat(chat)
    data=json.dumps(dict(chat_id=chat,text=message)).encode()
    req=urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage',data=data,headers={'Content-Type':'application/json','User-Agent':'Prumo/1.0'},method='POST')
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=5) as response:
            raw=response.read(65537)
            if len(raw)>65536:
                raise ValueError()
            payload=json.loads(raw)
        result=payload.get('result',{})
        if payload.get('ok') is not True or int(result.get('chat',{}).get('id',0))!=int(chat) or type(result.get('message_id')) is not int or result['message_id']<=0:
            raise ValueError()
        return result['message_id']
    except Exception:
        # Exceções HTTP contêm a URL do bot: nunca as propaga para logs/HTML.
        raise TelegramError('Envio não confirmado. Confira token, Chat ID e a permissão do bot para enviar mensagens ao grupo.') from None

def asset_ref(asset):
    return asset.get('alertRef',f'legacy:{asset["id"]}')

def queue_alerts(conn,refs=None):
    assets={asset_ref(dict(json.loads(r['payload']),id=r['id'])):dict(json.loads(r['payload']),id=r['id']) for r in conn.execute('SELECT * FROM assets')}
    for job in conn.execute("SELECT id,asset_ref FROM alert_deliveries WHERE status='pending'").fetchall():
        if job['asset_ref'] not in assets:
            conn.execute("UPDATE alert_deliveries SET status='canceled' WHERE id=?",(job['id'],))
    alerts=conn.execute('SELECT a.* FROM price_alerts a JOIN telegram_settings t ON t.user_id=a.user_id WHERE a.enabled=1 AND t.verified=1').fetchall()
    for alert in alerts:
        if refs is not None and alert['asset_ref'] not in refs:
            continue
        asset=assets.get(alert['asset_ref'])
        if not asset:
            continue
        baseline=Decimal(alert['baseline'])
        price=Decimal(str(asset['atual']))
        if baseline<=0 or price<=0:
            continue
        change=(price-baseline)*100/baseline
        threshold=Decimal(alert['threshold'])
        direction='up' if change>=threshold else 'down' if change<=-threshold else None
        if direction:
            conn.execute('INSERT OR IGNORE INTO alert_deliveries(user_id,asset_ref,version,direction,price,ticker,created,status) VALUES (?,?,?,?,?,?,?,?)',(alert['user_id'],alert['asset_ref'],alert['version'],direction,str(price),asset['ticker'],time.time(),'pending'))
    conn.commit()

def deliver_alerts(conn,secret,limit=5):
    sent=0
    for _ in range(limit):
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT d.*,a.baseline,a.threshold,t.token_enc,t.chat_id FROM alert_deliveries d JOIN price_alerts a ON a.user_id=d.user_id AND a.asset_ref=d.asset_ref AND a.version=d.version JOIN telegram_settings t ON t.user_id=d.user_id WHERE d.status='pending' AND a.enabled=1 AND t.verified=1 ORDER BY d.id LIMIT 1").fetchone()
        if not row:
            conn.commit()
            break
        conn.execute("UPDATE alert_deliveries SET status='sending' WHERE id=?",(row['id'],))
        conn.commit()
        try:
            token=decrypt_token(secret,row['token_enc'])
            baseline=Decimal(row['baseline'])
            price=Decimal(row['price'])
            variation=(price-baseline)*100/baseline
            word='subiu' if row['direction']=='up' else 'caiu'
            message=f'Prumo · Alerta de investimento\n{row["ticker"]} {word} {abs(variation):.2f}% em relação à referência configurada.\nReferência: R$ {baseline:.4f}\nPreço observado: R$ {price:.4f}\nLimite: {row["threshold"]}%.'
            message_id=send_message(token,row['chat_id'],message)
            conn.execute("UPDATE alert_deliveries SET status='sent',message_id=? WHERE id=?",(message_id,row['id']))
            sent+=1
        except TelegramError:
            conn.execute("UPDATE alert_deliveries SET status='failed' WHERE id=?",(row['id'],))
        conn.commit()
    return sent
