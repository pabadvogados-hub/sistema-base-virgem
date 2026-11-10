""
import sys
import asyncio
from pathlib import Path
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import requer_financeiro
from ..database import Usuario

PROJ = Path(__file__).parent.parent.parent.parent.parent
_preparado = False


def _prep():
    ""
    global _preparado
    if _preparado:
        return
    from dotenv import load_dotenv
    load_dotenv(str(PROJ / 'config' / '.env'))
    p = str(PROJ / 'INTEGRACOES')
    if p not in sys.path:
        sys.path.insert(0, p)
    _preparado = True


router = APIRouter(prefix='/api/financeiro', tags=['financeiro'])


def _mapa_clientes(az):
    ""
    m, off = {}, 0
    while True:
        d = az._request('GET', '/customers', params={'limit': 100, 'offset': off})
        regs = (d or {}).get('data', []) or []
        for c in regs:
            m[c.get('id')] = c.get('name', '') or ''
        if not (d or {}).get('hasMore') or not regs:
            break
        off += 100
    return m


def _termos_nao_cobrar():
    ""
    p = PROJ / 'FINANCEIRO' / 'clientes_nao_cobrar.txt'
    termos = []
    if p.exists():
        for linha in p.read_text(encoding='utf-8').splitlines():
            linha = linha.strip()
            if linha and not linha.startswith('#'):
                termos.append(linha.lower())
    return termos


def _excluir(nome, cid, termos):
    n = (nome or '').lower()
    c = (cid or '').lower()
    return any(t in n or t == c for t in termos)


def _fmt(lst, nomes, situacao):
    out = []
    for p in lst:
        cid = p.get('customer')
        out.append({
            'id': p.get('id'),
            'cliente': nomes.get(cid) or p.get('description') or cid or '—',
            'descricao': p.get('description') or '',
            'valor': p.get('value') or 0,
            'vencimento': p.get('dueDate'),
            'status': p.get('status'),
            'link': p.get('invoiceUrl') or p.get('bankSlipUrl') or '',
            'situacao': situacao,
        })
    return out


@router.get('/cobrancas')
async def cobrancas(user: Usuario = Depends(requer_financeiro)):
    ""
    def _run():
        _prep()
        import asaas_integration as az
        hoje = date.today()
        ini_mes = hoje.replace(day=1).isoformat()
        vencidas = az.listar_cobrancas(status='OVERDUE')
        a_vencer = az.listar_cobrancas(status='PENDING')
        pagas = (az.listar_cobrancas(status='RECEIVED', due_date_ge=ini_mes, due_date_le=hoje.isoformat())
                 + az.listar_cobrancas(status='CONFIRMED', due_date_ge=ini_mes, due_date_le=hoje.isoformat()))
        nomes = _mapa_clientes(az)

        termos = _termos_nao_cobrar()
        def _mostra(p):
            cid = p.get('customer')
            return not _excluir(nomes.get(cid, ''), cid, termos)
        n_venc_bruto, n_avenc_bruto = len(vencidas), len(a_vencer)
        vencidas = [p for p in vencidas if _mostra(p)]
        a_vencer = [p for p in a_vencer if _mostra(p)]
        ocultos = (n_venc_bruto - len(vencidas)) + (n_avenc_bruto - len(a_vencer))

        v = _fmt(vencidas, nomes, 'VENCIDA')
        a = _fmt(a_vencer, nomes, 'A_VENCER')
        pg = _fmt(pagas, nomes, 'PAGA')
        soma = lambda l: round(sum((x['valor'] or 0) for x in l), 2)
        return {
            'ocultos_nao_cobrar': ocultos,
            'resumo': {
                'vencidas': {'qtd': len(v), 'total': soma(v)},
                'a_vencer': {'qtd': len(a), 'total': soma(a)},
                'pagas_mes': {'qtd': len(pg), 'total': soma(pg)},
            },
            'vencidas': sorted(v, key=lambda x: x['vencimento'] or ''),
            'a_vencer': sorted(a, key=lambda x: x['vencimento'] or ''),
            'pagas': sorted(pg, key=lambda x: x['vencimento'] or '', reverse=True)[:60],
        }
    return await asyncio.to_thread(_run)


def _painel_adv(entry_type):
    ""
    from datetime import timedelta
    _prep()
    import advbox_integration as adv
    hoje = date.today()
    ini = (hoje - timedelta(days=60)).isoformat()
    fim = (hoje + timedelta(days=21)).isoformat()
    tx = adv.listar_transacoes({'entry_type': entry_type, 'date_due_start': ini, 'date_due_end': fim})

    def item(t):
        return {
            'id': t.get('id'),
            'cliente': t.get('name') or '',
            'descricao': t.get('description') or t.get('category') or '—',
            'categoria': t.get('category') or '',
            'valor': t.get('amount') or 0,
            'vencimento': t.get('date_due'),
            'pago': bool(t.get('date_payment')),
            'data_pagamento': t.get('date_payment'),
            'banco': (t.get('debit_bank') or t.get('credit_bank') or ''),
            'responsavel': t.get('responsible') or '',
        }
    itens = [item(t) for t in tx]
    hs = hoje.isoformat()
    vencidas = [x for x in itens if not x['pago'] and (x['vencimento'] or '') < hs]
    a_vencer = [x for x in itens if not x['pago'] and (x['vencimento'] or '') >= hs]
    pagas = [x for x in itens if x['pago']]
    soma = lambda l: round(sum((x['valor'] or 0) for x in l), 2)
    return {
        'resumo': {
            'vencidas': {'qtd': len(vencidas), 'total': soma(vencidas)},
            'a_vencer': {'qtd': len(a_vencer), 'total': soma(a_vencer)},
            'pagas': {'qtd': len(pagas), 'total': soma(pagas)},
        },
        'vencidas': sorted(vencidas, key=lambda x: x['vencimento'] or ''),
        'a_vencer': sorted(a_vencer, key=lambda x: x['vencimento'] or ''),
        'pagas': sorted(pagas, key=lambda x: x['data_pagamento'] or '', reverse=True)[:60],
    }


@router.get('/contas-pagar')
async def contas_pagar(user: Usuario = Depends(requer_financeiro)):
    ""
    return await asyncio.to_thread(_painel_adv, 'expense')


@router.get('/contas-receber')
async def contas_receber(user: Usuario = Depends(requer_financeiro)):
    ""
    return await asyncio.to_thread(_painel_adv, 'income')


class BoletoReq(BaseModel):
    payment_id: str


@router.post('/enviar-boleto')
async def enviar_boleto(req: BoletoReq, user: Usuario = Depends(requer_financeiro)):
    ""
    def _dados():
        _prep()
        import asaas_integration as az
        pay = az._request('GET', f'/payments/{req.payment_id}')
        cust = az.obter_cliente(pay.get('customer')) if pay else None
        return pay, cust
    pay, cust = await asyncio.to_thread(_dados)
    if not pay or not cust:
        raise HTTPException(404, 'Cobranca ou cliente nao encontrado no Asaas')

    dig = ''.join(c for c in str(cust.get('mobilePhone') or cust.get('phone') or '') if c.isdigit())
    if not dig:
        raise HTTPException(400, 'Cliente sem telefone cadastrado no Asaas')
    if not dig.startswith('55'):
        dig = '55' + dig

    nome = (cust.get('name') or 'cliente').split()[0].title()
    valor = pay.get('value') or 0
    d = (pay.get('dueDate') or '')
    venc = f'{d[8:10]}/{d[5:7]}/{d[0:4]}' if len(d) == 10 else d
    link = pay.get('invoiceUrl') or pay.get('bankSlipUrl') or ''
    valor_br = f'{valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    msg = (
        f'Querido(a) {nome}, tudo bem?\n\n'
        f'Apenas um lembrete amigável: seu boleto do Escritório Modelo Advogados vence em {venc}, '
        f'no valor de R$ {valor_br}.\n\n'
        f'Para sua comodidade, o link para pagamento está aqui:\n{link}\n\n'
        f'Qualquer dúvida, estou à disposição.\nCom carinho,\nFinanceiro | Escritório Modelo Advogados'
    )

    from ..whatsapp import zapi
    from ..whatsapp.models import NumeroWhatsapp
    from ..database import AsyncSessionLocal
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        numero = (await db.execute(select(NumeroWhatsapp).where(NumeroWhatsapp.id == 1))).scalar_one_or_none()
    if not numero:
        raise HTTPException(500, 'Numero do escritorio nao configurado')
    ok, info = await asyncio.to_thread(zapi.enviar_texto, numero, dig, msg)
    if not ok:
        raise HTTPException(502, f'Falha ao enviar pelo WhatsApp: {info}')
    return {'ok': True, 'telefone': dig, 'cliente': cust.get('name')}
