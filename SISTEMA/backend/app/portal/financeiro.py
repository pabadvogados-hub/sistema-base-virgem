""
import time

ABERTOS = ('PENDING', 'OVERDUE', 'AWAITING_RISK_ANALYSIS')
PAGOS = ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH')

IGNORAR = ('REFUNDED', 'REFUND_REQUESTED', 'CHARGEBACK_REQUESTED',
           'CHARGEBACK_DISPUTE', 'AWAITING_CHARGEBACK_REVERSAL', 'DELETED')

MAX_PAGOS = 24

TTL_ASAAS = 120
_CACHE_ASAAS = {}


MESES_PAGOS_SEM_CONSULTORIA = 3


def _e_consultoria(cobrancas) -> bool:
    ""
    for c in cobrancas or []:
        if 'CONSULTORIA' in ((c.get('description') or '').upper()):
            return True
    return False


def _dentro_dos_meses(item, meses) -> bool:
    ""
    import datetime
    d = item.get('pago_em') or item.get('vencimento') or ''
    if len(d) != 10:
        return False
    try:
        dt = datetime.date(int(d[6:10]), int(d[3:5]), int(d[0:2]))
    except ValueError:
        return False
    return (datetime.date.today() - dt).days <= meses * 31


def _brl(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _data_br(iso):
    d = (iso or '')[:10]
    return '%s/%s/%s' % (d[8:10], d[5:7], d[:4]) if len(d) == 10 else ''


def _dias_para(iso):
    try:
        alvo = time.mktime(time.strptime((iso or '')[:10], '%Y-%m-%d'))
        return int((alvo - time.time()) // 86400) + 1
    except Exception:
        return None


def montar(cobrancas: list) -> dict:
    ""
    abertos, pagos = [], []
    for c in cobrancas or []:
        st = (c.get('status') or '').upper()
        if st in IGNORAR or c.get('deleted'):
            continue

        item = {
            'id': c.get('id'),
            'descricao': (c.get('description') or 'Honorários').strip(),
            'valor': _brl(c.get('value')),
            'vencimento': _data_br(c.get('dueDate')),
            'boleto': c.get('bankSlipUrl') or '',
            'fatura': c.get('invoiceUrl') or '',
            'tipo': c.get('billingType') or '',
        }
        if st in ABERTOS:
            dias = _dias_para(c.get('dueDate'))
            item['dias'] = dias
            item['vencido'] = (dias is not None and dias < 0)
            item['_ordem'] = c.get('dueDate') or ''
            abertos.append(item)
        elif st in PAGOS:
            item['pago_em'] = _data_br(c.get('paymentDate') or c.get('clientPaymentDate'))
            item['comprovante'] = c.get('transactionReceiptUrl') or ''
            item['_ordem'] = c.get('paymentDate') or c.get('dueDate') or ''
            pagos.append(item)

    abertos.sort(key=lambda x: x['_ordem'])
    pagos.sort(key=lambda x: x['_ordem'], reverse=True)
    for x in abertos + pagos:
        x.pop('_ordem', None)

    consultoria = _e_consultoria(cobrancas)
    if consultoria:
        mostrar, rotulo = pagos[:MAX_PAGOS], ''
    else:
        mostrar = [p for p in pagos if _dentro_dos_meses(p, MESES_PAGOS_SEM_CONSULTORIA)]
        rotulo = 'últimos %d meses' % MESES_PAGOS_SEM_CONSULTORIA
        mostrar = mostrar[:MAX_PAGOS]

    return {
        'em_aberto': abertos,
        'pagos': mostrar,
        'consultoria': consultoria,
        'pagos_recorte': rotulo,
        'resumo': {
            'aberto_qtd': len(abertos),
            'aberto_valor': round(sum(x['valor'] for x in abertos), 2),
            'vencidos': sum(1 for x in abertos if x['vencido']),
            'pagos_qtd': len(mostrar),
            'pagos_valor': round(sum(x['valor'] for x in mostrar), 2),
        },
    }


CONFIG_ENV = '/opt/escritorio/config/.env'


def _garantir_token() -> bool:
    ""
    import os
    if os.getenv('ASAAS_API_TOKEN'):
        return True
    try:
        from dotenv import load_dotenv
        load_dotenv(CONFIG_ENV)
    except Exception:
        return False
    return bool(os.getenv('ASAAS_API_TOKEN'))


def do_cliente(documento: str) -> dict:
    ""
    import sys
    if '/opt/escritorio' not in sys.path:
        sys.path.insert(0, '/opt/escritorio')
    if not _garantir_token():
        return {'tem': False, 'motivo': 'sem_acesso'}
    from INTEGRACOES import asaas_integration as az

    doc = ''.join(ch for ch in (documento or '') if ch.isdigit())
    if not doc:
        return {'tem': False}
    _em_cache = _CACHE_ASAAS.get(doc)
    if _em_cache and (time.time() - _em_cache[0]) < TTL_ASAAS:
        return _em_cache[1]
    try:
        clientes = az.listar_clientes(cpf=doc) or []
    except (Exception, SystemExit):
        return {'tem': False, 'motivo': 'falhou'}
    if not clientes:
        return {'tem': False}

    cobrancas = []
    for c in clientes:
        try:
            cobrancas.extend(az.listar_cobrancas(customer=c.get('id')) or [])
        except (Exception, SystemExit):
            continue

    d = montar(cobrancas)
    d['tem'] = bool(d['em_aberto'] or d['pagos'])
    _CACHE_ASAAS[doc] = (time.time(), d)
    return d
