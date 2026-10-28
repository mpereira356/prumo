"""Validação estrita dos backups anteriores e atuais; sem executar conteúdo enviado."""
from datetime import date
from decimal import Decimal, InvalidOperation
import re

CLASSES={'ACOES','FIIs','TESOURO','RF','PREV','BDR','ETF','COE'}
TYPES={'PARCELADO','CHEQUES','JUROS_COM_AMORTIZACAO','JUROS_MENSAIS','UNICO'}

def numeric(value, low=0, high=100000000000):
    try:
        n=Decimal(str(value))
        if not n.is_finite() or not Decimal(str(low))<=n<=Decimal(str(high)):
            raise ValueError()
        return str(n)
    except (InvalidOperation,ValueError,TypeError):
        raise ValueError('Valor numérico fora do limite.')

def string(value, maxlen=150):
    if not isinstance(value,str) or not value.strip() or len(value)>maxlen:
        raise ValueError('Campo de texto inválido.')
    return value.strip()

def iso(value):
    return date.fromisoformat(string(value,10)).isoformat()

def integer(value, low=1, high=480):
    if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high:
        raise ValueError('Identificador ou quantidade inválida.')
    return value

def normalize_extra(p):
    if not isinstance(p,dict):
        raise ValueError('Pagamento inválido.')
    result={'data':iso(p['data']),'valor':numeric(p.get('valor',p.get('amortizacao',0)))}
    for k in ('valorPago','juros','amortizacao','principalAntes','principalDepois','multa','mora'):
        if k in p:
            result[k]=numeric(p[k])
    if p.get('payment_key'):
        result['payment_key']=string(p['payment_key'],100)
    if 'juros' in result:
        principal=Decimal(result['valor'])
        interest=Decimal(result['juros'])
        result.setdefault('amortizacao',str(principal))
        charges=Decimal(result.get('multa',0))+Decimal(result.get('mora',0))
        result.setdefault('valorPago',str(principal+interest+charges))
        if abs(Decimal(result['valorPago'])-principal-interest-charges)>Decimal('.01'):
            raise ValueError('Pagamento com juros e amortização inconsistentes.')
    return result

def validate_backup(payload, today):
    if not isinstance(payload,dict) or payload.get('versao') not in (2,3,4):
        raise ValueError('Versão não suportada. Use um backup v2, v3 ou v4.')
    result={'ativos':[],'emprestimos':[],'recebimentosConfirmados':{},'aportes':[],'settings':{}}
    for field,limit in [('ativos',1000),('emprestimos',200),('aportes',5000)]:
        if not isinstance(payload.get(field,[]),list) or len(payload.get(field,[]))>limit:
            raise ValueError('Quantidade de registros fora do limite.')
    asset_alert_refs=set()
    for a in payload.get('ativos',[]):
        if not isinstance(a,dict):
            raise ValueError('Ativo inválido.')
        entry={k:string(a[k],80) for k in ('ticker','nome','classe')}
        if entry['classe'] not in CLASSES:
            raise ValueError('Classe de ativo inválida.')
        entry.update({k:numeric(a[k],.000001 if k=='qtd' else 0) for k in ('qtd','pm','atual')})
        if a.get('updated_at'):
            entry['updated_at']=iso(a['updated_at'])
        if a.get('alertRef'):
            if not isinstance(a['alertRef'],str) or not re.fullmatch(r'[a-f0-9]{32}',a['alertRef']):
                raise ValueError('Referência de investimento inválida.')
            if a['alertRef'] in asset_alert_refs:
                raise ValueError('Referência duplicada entre investimentos.')
            asset_alert_refs.add(a['alertRef'])
            entry['alertRef']=a['alertRef']
        result['ativos'].append(entry)
    ids=set()
    wallet_refs=set()
    schedule_limit=0
    for e in payload.get('emprestimos',[]):
        if not isinstance(e,dict):
            raise ValueError('Contrato inválido.')
        entry={k:string(e[k]) for k in ('tomador','tipo','status')}
        entry['id']=integer(e['id'],1,2147483647)
        if e.get('walletRef'):
            if not isinstance(e['walletRef'],str) or not re.fullmatch(r'[a-f0-9]{32}',e['walletRef']):
                raise ValueError('Referência financeira do contrato inválida.')
            if e['walletRef'] in wallet_refs:
                raise ValueError('Referência financeira duplicada entre contratos.')
            wallet_refs.add(e['walletRef'])
            entry['walletRef']=e['walletRef']
        if entry['id'] in ids or entry['tipo'] not in TYPES or entry['status'] not in ('ATIVO','QUITADO'):
            raise ValueError('Contrato duplicado ou tipo inválido.')
        ids.add(entry['id'])
        entry['inicio']=iso(e['inicio'])
        entry['fim']=iso(e['fim']) if e.get('fim') else None
        entry['risco']=e.get('risco','medio')
        if entry['risco'] not in ('baixo','medio','alto'):
            raise ValueError('Risco inválido.')
        entry['parcelas']=integer(e.get('parcelas') or 1)
        schedule_limit+=entry['parcelas']
        if schedule_limit>20000:
            raise ValueError('Cronograma muito grande.')
        for k in ('principal','principalAtual','valorMensal','taxa','primeiraParcela'):
            if k in e and e[k] is not None:
                entry[k]=numeric(e[k],0,100 if k=='taxa' else 100000000000)
        for k in ('moraPct','multaPct'):
            if k in e:
                entry[k]=numeric(e[k],0,100)
        entry.setdefault('principal','0')
        entry.setdefault('valorMensal','0')
        entry.setdefault('taxa','0')
        if e.get('apelido'):
            entry['apelido']=string(e['apelido'])
        entry['obs']=string(e['obs'],1000) if e.get('obs') else ''
        if e.get('amortizacaoInicio'):
            entry['amortizacaoInicio']=iso(e['amortizacaoInicio'])
        if 'reinvestir' in e:
            if not isinstance(e['reinvestir'],bool):
                raise ValueError('Preferência de reinvestimento inválida.')
            entry['reinvestir']=e['reinvestir']
        if e.get('classe'):
            entry['classe']=string(e['classe'],80)
        entry['cheques']=[]
        if not isinstance(e.get('cheques',[]),list) or len(e.get('cheques',[]))>100:
            raise ValueError('Lista de cheques inválida.')
        for c in e.get('cheques',[]):
            if not isinstance(c,dict):
                raise ValueError('Cheque inválido.')
            entry['cheques'].append({'data':iso(c['data']),'valor':numeric(c['valor'],.01)})
        if entry['tipo']=='CHEQUES' and not entry['cheques']:
            raise ValueError('Contrato sem cheques.')
        entry['pagamentosExtras']=[]
        if not isinstance(e.get('pagamentosExtras',[]),list) or len(e.get('pagamentosExtras',[]))>1000:
            raise ValueError('Lista de amortizações inválida.')
        for p in e.get('pagamentosExtras',[]):
            if not isinstance(p,dict):
                raise ValueError('Amortização inválida.')
            entry['pagamentosExtras'].append(normalize_extra(p))
        if entry['tipo']=='JUROS_MENSAIS' and entry['fim']:
            start,end=date.fromisoformat(entry['inicio']),date.fromisoformat(entry['fim'])
            if (end.year-start.year)*12+end.month-start.month>480:
                raise ValueError('Cronograma excede 480 meses.')
        result['emprestimos'].append(entry)
    # Importação local evita dependência circular na inicialização do app.
    from app import schedule
    known={r['key'] for r in schedule(result['emprestimos'])}
    originals={e['id']:e for e in payload.get('emprestimos',[])}
    for e in result['emprestimos']:
        states=originals[e['id']].get('situacaoCheques',{})
        if not isinstance(states,dict) or len(states)>100:
            raise ValueError('Situações de cheques inválidas.')
        valid_keys={r['key'] for r in schedule([e])} if e['tipo']=='CHEQUES' else set()
        if any(key not in valid_keys or value not in ('EM_ATRASO','NO_PRAZO') for key,value in states.items()):
            raise ValueError('Situação sem correspondência com o cheque.')
        if states:
            e['situacaoCheques']=states
    payments=payload.get('recebimentosConfirmados',{})
    if not isinstance(payments,dict) or len(payments)>20000:
        raise ValueError('Lista de pagamentos inválida.')
    for key,value in payments.items():
        if key not in known:
            raise ValueError('Pagamento sem correspondência no cronograma.')
        result['recebimentosConfirmados'][key]=numeric(value,.01)
    for e in result['emprestimos']:
        details=originals[e['id']].get('encargosConfirmados',{})
        valid_keys={r['key'] for r in schedule([e])}
        if not isinstance(details,dict) or len(details)>480:
            raise ValueError('Detalhamento de encargos inválido.')
        snapshots={}
        for key,snapshot in details.items():
            if key not in valid_keys or key not in payments or not isinstance(snapshot,dict):
                raise ValueError('Encargos sem recebimento correspondente.')
            record={k:numeric(snapshot[k]) for k in ('base','multa','mora')}
            record['data']=iso(snapshot['data'])
            record['dias']=integer(snapshot['dias'],0,3660000)
            if abs(sum(Decimal(record[k]) for k in ('base','multa','mora'))-Decimal(str(payments[key])))>Decimal('.01'):
                raise ValueError('Total de encargos diferente do recebimento.')
            snapshots[key]=record
        if snapshots:
            e['encargosConfirmados']=snapshots
    mapping={'CDB':'RF','Renda Fixa':'RF','Ações':'ACOES','Tesouro':'TESOURO','Previdência':'PREV'}
    for a in payload.get('aportes',[]):
        if not isinstance(a,dict):
            raise ValueError('Aporte inválido.')
        category=a.get('category',a.get('classe','RF'))
        category=mapping.get(category,category)
        day=iso(a.get('day',a.get('data')))
        if category not in CLASSES or date.fromisoformat(day)>today:
            raise ValueError('Aporte com data ou classe inválida.')
        result['aportes'].append({'amount':numeric(a.get('amount',a.get('valor')),.01),'day':day,'category':category})
    settings=payload.get('settings',{})
    if not isinstance(settings,dict):
        raise ValueError('Configurações inválidas.')
    if 'goal' in settings:
        result['settings']['goal']=numeric(settings['goal'],1)
    if 'rate' in settings:
        result['settings']['rate']=numeric(settings['rate'],0,10)
    if payload['versao']==4:
        from wallet import validate_state
        result['wallet']=validate_state(payload.get('wallet'))
    return result
