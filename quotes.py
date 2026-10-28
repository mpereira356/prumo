"""Integração opcional: credencial somente no servidor, destino fixo e sem redirects."""
import json
import re
import urllib.request
import time
import os
from datetime import date, datetime, timezone
from decimal import Decimal

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def fetch_quotes(symbols, token):
    symbols=sorted(set(s for s in symbols if re.fullmatch(r'[A-Z]{4}[0-9]{1,2}',s)))
    if not symbols or len(symbols)>100:
        raise ValueError('Seleção de ativos inválida ou maior que 100 símbolos.')
    req=urllib.request.Request('https://brapi.dev/api/quote/'+','.join(symbols),headers={'Authorization':'Bearer '+token,'Accept':'application/json','User-Agent':'Prumo/1.0'})
    opener=urllib.request.build_opener(NoRedirect())
    with opener.open(req,timeout=10) as response:
        raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:
            raise ValueError('Resposta muito grande.')
        payload=json.loads(raw)
    prices={}
    for item in payload.get('results',[]):
        symbol=item.get('symbol','').removesuffix('.SA')
        price=Decimal(str(item.get('regularMarketPrice')))
        if symbol in symbols and price.is_finite() and 0<price<=1000000000:
            prices[symbol]=str(price)
    return prices

def _currency_json(url, extra_headers=None):
    headers={'Accept':'application/json','User-Agent':'Prumo/1.0'}
    headers.update(extra_headers or {})
    req=urllib.request.Request(url,headers=headers)
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=4) as response:
        raw=response.read(65537)
        if len(raw)>65536:
            raise ValueError('Resposta muito grande.')
        return json.loads(raw)

def _fetch_awesome_dollar():
    key=os.environ.get('AWESOMEAPI_TOKEN')
    item=_currency_json('https://economia.awesomeapi.com.br/json/last/USD-BRL',{'x-api-key':key} if key else None)['USDBRL']
    bid=Decimal(str(item['bid']))
    change=Decimal(str(item['pctChange']))
    timestamp=int(item['timestamp'])
    if not bid.is_finite() or not 0<bid<1000000 or not change.is_finite() or abs(change)>1000 or not 0<timestamp<=time.time()+300:
        raise ValueError('Cotação inválida.')
    return {'bid':str(bid),'change':str(change),'timestamp':timestamp,'source':'AwesomeAPI','kind':'intraday'}

def _fetch_reference_dollar():
    item=_currency_json('https://api.frankfurter.dev/v2/providers/ecb/rate/USD/BRL')
    bid=Decimal(str(item['rate']))
    day=date.fromisoformat(item['date'])
    if item.get('base')!='USD' or item.get('quote')!='BRL' or not bid.is_finite() or not 0<bid<1000000 or day>datetime.now(timezone.utc).date():
        raise ValueError('Referência diária inválida.')
    timestamp=int(datetime(day.year,day.month,day.day,tzinfo=timezone.utc).timestamp())
    return dict(bid=str(bid),change=None,timestamp=timestamp,date=day.isoformat(),source='Frankfurter / BCE',kind='daily')

def fetch_dollar():
    try:
        return _fetch_awesome_dollar()
    except Exception:
        # Referência diária explicitamente identificada; não simula preço intradiário.
        return _fetch_reference_dollar()
