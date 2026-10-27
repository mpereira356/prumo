"""Integração opcional: credencial somente no servidor, destino fixo e sem redirects."""
import json
import re
import urllib.request
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
