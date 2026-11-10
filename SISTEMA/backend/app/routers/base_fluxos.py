# -*- coding: utf-8 -*-
"""Fluxo de reaquecimento da base: o canal de marketing que não paga mídia.

Do lado pago a gente já sabe quanto custa um lead; deste lado não se enxergava nada.
Este painel responde: a base está devolvendo lead? está valendo a pena?

Cada robô que cutuca a base guarda o que fez num arquivo próprio, cada um do seu
jeito. Aqui eles viram uma linguagem só: abordados, responderam, quantos deram o
sinal que interessa e quantos viraram contrato. O contrato sai da MESMA fonte do
painel comercial (a entrada no vigia da contratação), para não nascer um segundo
placar divergente.

"""
import asyncio
import json
import os
import re
import sqlite3
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from ..database import Usuario
from ..deps import usuario_atual

router = APIRouter(prefix='/api/campanhas', tags=['campanhas'])
SYNC = '/opt/escritorio/SYNC'
BANCO = Path(__file__).parent.parent.parent / 'sistema_esc.db'
FUSO = '-3 hours'
CACHE_SEG = 180
_cache = {}

# o que cada voto/resposta significa: o sinal que vira dinheiro vem primeiro
SINAL = {
    'saiu': ('saiu', 'já saiu da empresa'),
}


def _json(nome, padrao=None):
    try:
        with open(os.path.join(SYNC, nome), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return padrao if padrao is not None else {}


def _dia(txt):
    """'2026-10-06 19:16' ou '2026-10-06T...' -> '2026-10-06'."""
    return (str(txt or '')[:10]) or None


def _contratos_por_conversa():
    """{conversa_id: data} pela entrada no vigia da contratação, igual ao painel comercial."""
    est = _json('contratacao_state.json', {'conversas': {}})
    fora = {}
    for cid, c in (est.get('conversas') or {}).items():
        quando = (c.get('pedido_em') or '')[:10]
        if quando:
            fora[str(cid)] = quando
    return fora


def _nomes_das_conversas(ids):
    """Nome e etapa atual de cada card, para a lista de quem respondeu."""
    if not ids:
        return {}
    try:
        con = sqlite3.connect('file:%s?mode=ro' % BANCO, uri=True, timeout=15)
    except Exception:
        return {}
    fora = {}
    try:
        for i in range(0, len(ids), 400):
            lote = ids[i:i + 400]
            q = ('select id, coalesce(contato_nome,""), coalesce(etapa,""), pipeline_id '
                 'from wa_conversas where id in (%s)' % ','.join('?' * len(lote)))
            for cid, nome, etapa, pipe in con.execute(q, lote):
                fora[str(cid)] = {'nome': nome, 'etapa': etapa, 'pipeline_id': pipe}
    except Exception:
        pass
    con.close()
    return fora


def _no_periodo(dia, desde):
    return bool(dia) and dia >= desde


def _ligacoes(ids, desde):
    """Ligações feitas para estes leads no período. A ligação fica como nota na
    conversa (autor='ligacao'), que é o registro que a D-API devolve."""
    ids = [int(i) for i in ids if str(i).isdigit()]
    if not ids:
        return {'tentativas': 0, 'atendidas': 0, 'pessoas': 0}
    try:
        con = sqlite3.connect('file:%s?mode=ro' % BANCO, uri=True, timeout=15)
    except Exception:
        return {'tentativas': 0, 'atendidas': 0, 'pessoas': 0}
    tent, atend, gente = 0, 0, set()
    try:
        for i in range(0, len(ids), 400):
            lote = ids[i:i + 400]
            q = ("select conversa_id, conteudo from wa_mensagens where origem='nota' and autor='ligacao' "
                 "and date(datetime(criado_em, ?)) >= ? and conversa_id in (%s)" % ','.join('?' * len(lote)))
            for cid, texto in con.execute(q, [FUSO, desde] + lote):
                t = texto or ''
                if 'iniciada' in t:
                    tent += 1
                    gente.add(cid)
                elif 'de conversa' in t:
                    m = re.search(r'\((\d+):(\d+) de conversa', t)
                    if m and int(m.group(1)) * 60 + int(m.group(2)) >= 60:
                        atend += 1
    except Exception:
        pass
    con.close()
    return {'tentativas': tent, 'atendidas': atend, 'pessoas': len(gente)}


def _fechou_depois(contratos, cid, dia_do_toque):
    """Só conta o contrato que veio DEPOIS do toque do robô. Sem isso o fluxo levaria
    crédito por contrato que já estava fechando por outro caminho."""
    quando = contratos.get(str(cid))
    return bool(quando) and bool(dia_do_toque) and quando >= dia_do_toque


def _ja_saiu(desde, contratos):
    """Pergunta ao lead antigo se ele já saiu da empresa."""
    est = _json('fluxo_saiu_state.json')
    env = est.get('enviados') or {}
    abordados = [(cid, v) for cid, v in env.items() if _no_periodo(_dia(v.get('enviado')), desde)]
    responderam = [(cid, v) for cid, v in abordados if (v.get('resultado') or '').strip()]
    saiu = [(cid, v) for cid, v in responderam if v.get('resultado') == 'saiu']
    trabalhando = [c for c, v in responderam if v.get('resultado') == 'trabalhando']
    pararam = [c for c, v in responderam if v.get('resultado') == 'parar']
    fechou = [cid for cid, v in abordados if _fechou_depois(contratos, cid, _dia(v.get('enviado')))]
    return {
        'chave': 'ja_saiu', 'produto': 'trabalhista', 'nome': 'Já saiu da empresa?',
        'oque_faz': 'Pergunta ao lead parado se ele continua na empresa. Quem saiu vira ligação na hora para o comercial.',
        'quando': 'de 15 em 15 minutos, dias úteis das 9h às 17h',
        'abordados': len(abordados), 'responderam': len(responderam),
        'sinal_nome': SINAL['saiu'][1], 'sinal': len(saiu),
        'extras': [{'rotulo': 'ainda na firma (revisita em 60 dias)', 'n': len(trabalhando)},
                   {'rotulo': 'pediram para parar', 'n': len(pararam)}],
        'votos': [], 'contratos': len(fechou),
        'por_dia': {d: n for d, n in sorted((est.get('dias') or {}).items()) if d >= desde},
        'quentes': [cid for cid, _v in saiu],
        'ids_do_fluxo': [cid for cid, _v in abordados],
    }


def _fila_vendedora(desde, contratos, produto=None):
    """A fila diária que o comercial trabalha (leads parados que já conversaram)."""
    est = _json('comercial_state.json')
    leads = est.get('leads') or {}
    dentro = [(cid, v) for cid, v in leads.items() if _no_periodo(_dia(v.get('ultimo_envio')), desde)]
    com_hist = [(cid, v) for cid, v in dentro if (v.get('historico') or [])]
    fechou = [cid for cid, v in dentro if _fechou_depois(contratos, cid, _dia(v.get('ultimo_envio')))]
    return {
        'chave': 'fila', 'produto': produto or 'trabalhista', 'nome': 'Fila diária do comercial',
        'oque_faz': 'Separa todo dia os leads parados que já conversaram, para o comercial ligar.',
        'quando': 'às 9h, de segunda a sexta',
        'abordados': len(dentro), 'responderam': len(com_hist),
        'sinal_nome': 'com retorno registrado', 'sinal': len(com_hist),
        'extras': [], 'votos': [], 'contratos': len(fechou), 'por_dia': {}, 'quentes': [],
        'ids_do_fluxo': [cid for cid, _v in dentro],
    }


def _montar(dias, produto=None):
    """produto: 'trabalhista', 'empresarial' ou None para todos.
    O reaquecimento nao e um canal a parte: e uma parte de cada produto."""
    desde = (date.today() - timedelta(days=dias - 1)).isoformat()
    contratos = _contratos_por_conversa()
    fluxos = [_ja_saiu(desde, contratos), _fila_vendedora(desde, contratos, produto)]
    if produto:
        fluxos = [f for f in fluxos if f.get('produto') == produto]

    quentes_ids = []
    for f in fluxos:
        quentes_ids += f.pop('quentes', [])
    cards = _nomes_das_conversas([int(c) for c in quentes_ids if str(c).isdigit()])
    quentes = [{'conversa_id': c, 'nome': cards.get(str(c), {}).get('nome') or '(sem nome)',
                'etapa': cards.get(str(c), {}).get('etapa') or '',
                'fechou': c in contratos} for c in quentes_ids]

    total = {'ligacoes': 0, 'atendidas': 0, 'abordados': sum(f['abordados'] for f in fluxos),
             'responderam': sum(f['responderam'] for f in fluxos),
             'sinal': sum(f['sinal'] for f in fluxos),
             'contratos': sum(f['contratos'] for f in fluxos)}
    total['taxa_resposta'] = (round(100.0 * total['responderam'] / total['abordados'], 1)
                              if total['abordados'] else 0)
    for f in fluxos:
        lig = _ligacoes(f.pop('ids_do_fluxo', []), desde)
        f['ligacoes'] = lig['tentativas']
        f['atendidas'] = lig['atendidas']
        f['pessoas_chamadas'] = lig['pessoas']
        f['taxa_resposta'] = round(100.0 * f['responderam'] / f['abordados'], 1) if f['abordados'] else 0
        f['taxa_sinal'] = round(100.0 * f['sinal'] / f['abordados'], 1) if f['abordados'] else 0
    total['ligacoes'] = sum(f.get('ligacoes') or 0 for f in fluxos)
    total['atendidas'] = sum(f.get('atendidas') or 0 for f in fluxos)
    return {'desde': desde, 'dias': dias, 'produto': produto, 'fluxos': fluxos, 'total': total,
            'quentes': quentes, 'atualizado_em': int(time.time())}


@router.get('/base')
async def base(dias: int = Query(30, ge=1, le=365), p: str = Query(None),
               user: Usuario = Depends(usuario_atual)):
    """Reaquecimento da base, dentro do produto: o que a base devolve sem pagar mídia."""
    p = p if p in ('trabalhista', 'empresarial') else None
    chave = 'base:%d:%s' % (dias, p or 'todos')
    agora = time.time()
    hit = _cache.get(chave)
    if hit and agora - hit[0] < CACHE_SEG:
        return hit[1]
    valor = await asyncio.to_thread(_montar, dias, p)
    _cache[chave] = (agora, valor)
    return valor
