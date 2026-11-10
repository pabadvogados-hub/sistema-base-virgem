# -*- coding: utf-8 -*-
"""Marketing por PRODUTO: a Visao geral vira a pasta central e cada produto tem o painel inteiro
dentro dela: Trabalhista (reclamante, linha fixa) e Empresarial (linha do Dr. Titular).

Os endpoints daqui devolvem EXATAMENTE o mesmo formato dos de `campanhas.py`, so que filtrados
por produto. Assim a tela reaproveita o mesmo desenho:

  GET /api/campanhas/produtos                              resumo dos dois, para os quadrados
  GET /api/campanhas/produto/visao?p=empresarial           = /visao
  GET /api/campanhas/produto/dashboard?p=empresarial       = /dashboard
  GET /api/campanhas/produto/anuncios?p=empresarial        = /anuncios
  GET /api/campanhas/produto/serie?p=empresarial           = /serie

Classificacao, sem ninguem precisar marcar nada na mao:
- campanha do Meta, pelo NOME;
- lead do CRM, pela FRASE com que chegou ou pela linha (Empresas).
"""
import asyncio
import calendar
import json
import os
import sqlite3
import sys
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from ..database import Usuario
from ..deps import usuario_atual

PROJETO_ROOT = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJETO_ROOT / 'INTEGRACOES'))
import meta_ads_integration as meta  # noqa: E402

router = APIRouter(prefix='/api/campanhas', tags=['campanhas'])
BANCO = Path(__file__).parent.parent.parent / 'sistema_esc.db'
FUSO = '-3 hours'
CACHE_SEG = 240
_cache = {}
_preparado = False

# A CLASSIFICAÇÃO MORA NO MÓDULO DO PRODUTO: uma lista só, em app/produto_card.py, para o painel
# e o card nunca discordarem.
from ..produto_card import FRASES, NOMES, produto_da_campanha, produto_da_frase  # noqa: E402
from ..produto_card import produto_do_lead  # noqa: E402,F401  (campanhas.py importa daqui)
from ..produto_card import EMPRESARIAL as EMP, OUTROS, ROTULO, _sa  # noqa: E402

RT = 'trabalhista'          # o nome curto que este arquivo usa; no módulo é TRABALHISTA
TETO_PADRAO = {RT: float(os.getenv('TETO_TRABALHISTA', '1500') or 1500), EMP: float(os.getenv('TETO_EMPRESARIAL', '0') or 0)}


def _prep():
    global _preparado
    if _preparado:
        return
    from dotenv import load_dotenv
    load_dotenv(str(PROJETO_ROOT / 'config' / '.env'))
    _preparado = True


# produto_da_campanha, produto_da_frase, produto_do_lead e _sa: importados de produto_card


def _teto(p):
    return float(os.getenv('TETO_%s' % p.upper(), TETO_PADRAO.get(p, 1500.0)))


def _cacheado(chave, fn):
    agora = time.time()
    hit = _cache.get(chave)
    if hit and agora - hit[0] < CACHE_SEG:
        return hit[1]
    valor = fn()
    _cache[chave] = (agora, valor)
    return valor


def _brl(x):
    return round(float(x or 0), 2)


# ---------------------------------------------------------------- Meta filtrado


def _campanhas(p=None):
    """Campanhas da conta, com o produto de cada uma."""
    fora = []
    for c in meta.listar_campanhas(ativas=False):
        c = dict(c, produto=produto_da_campanha(c.get('name') or ''))
        if p is None or c['produto'] == p:
            fora.append(c)
    return fora


def _insights(ids, level='account', date_preset='last_7d', fields=None, time_increment=None):
    """Insights da conta restritos a estas campanhas (filtering do próprio Meta:
    uma chamada só, em vez de uma por campanha)."""
    if not ids:
        return []
    campos = fields or 'spend,impressions,reach,clicks,ctr,actions'
    params = {'level': level, 'fields': campos, 'date_preset': date_preset, 'limit': 500,
              'filtering': json.dumps([{'field': 'campaign.id', 'operator': 'IN', 'value': ids}])}
    if time_increment:
        params['time_increment'] = time_increment
    return meta._get('%s/insights' % meta.CONTA_PADRAO, params).get('data', [])


def _linha(rows):
    r = (rows or [{}])[0]
    gasto = _brl(r.get('spend'))
    conv = meta.conversas(r.get('actions'))
    return {'gasto': gasto, 'conversas': conv,
            'custo': round(gasto / conv, 2) if conv else 0,
            'cliques': int(r.get('clicks') or 0),
            'impressoes': int(r.get('impressions') or 0),
            'alcance': int(r.get('reach') or 0),
            'ctr': _brl(r.get('ctr'))}


def _soma(rows):
    """Soma várias linhas de insights (quando o filtro devolve uma por campanha)."""
    if not rows:
        return _linha([])
    gasto = sum(float(r.get('spend') or 0) for r in rows)
    conv = sum(meta.conversas(r.get('actions')) for r in rows)
    cliques = sum(int(r.get('clicks') or 0) for r in rows)
    imp = sum(int(r.get('impressions') or 0) for r in rows)
    alc = sum(int(r.get('reach') or 0) for r in rows)
    return {'gasto': round(gasto, 2), 'conversas': conv,
            'custo': round(gasto / conv, 2) if conv else 0,
            'cliques': cliques, 'impressoes': imp, 'alcance': alc,
            'ctr': round(100.0 * cliques / imp, 2) if imp else 0}


def _orcamento_ligado(ids):
    """Quanto por dia está ligado agora nestas campanhas."""
    total, itens = 0.0, []
    for cid in ids:
        c = meta._get(cid, {'fields': 'name,effective_status,daily_budget'})
        if c.get('effective_status') == 'ACTIVE' and c.get('daily_budget'):
            v = int(c['daily_budget']) / 100
            itens.append({'nome': (c.get('name') or '')[:44], 'lado': 'nossa', 'valor': v})
            total += v
            continue
        for a in meta._get('%s/adsets' % cid, {'fields': 'name,effective_status,daily_budget',
                                               'limit': 60}).get('data', []):
            if a.get('effective_status') != 'ACTIVE' or not a.get('daily_budget'):
                continue
            v = int(a['daily_budget']) / 100
            itens.append({'nome': (a.get('name') or '')[:44], 'lado': 'nossa', 'valor': v})
            total += v
    itens.sort(key=lambda x: x['valor'], reverse=True)
    return {'por_dia': round(total, 2), 'no_mes': round(total * 30, 2), 'itens': itens}


# ---------------------------------------------------------------- CRM


def _crm(p, desde):
    """Leads do produto no CRM, pela frase com que chegaram."""
    hoje = date.today()
    vazio = {'hoje': 0, 'ontem': 0, 'sete': 0, 'por_origem': [], 'por_dia': {}}
    try:
        con = sqlite3.connect('file:%s?mode=ro' % BANCO, uri=True, timeout=15)
    except Exception:
        return vazio
    try:
        linhas = con.execute(
            "select date(datetime(v.criado_em, ?)) d, coalesce(nullif(v.origem,''),'(sem origem)'), "
            "       (select m.conteudo from wa_mensagens m where m.conversa_id=v.id "
            "          and m.origem='contato' order by m.id limit 1) "
            "  from wa_conversas v where date(datetime(v.criado_em, ?)) >= ?",
            (FUSO, FUSO, desde)).fetchall()
    except Exception:
        con.close()
        return vazio
    con.close()
    por_dia, origens = {}, {}
    for dia, origem, primeira in linhas:
        if produto_do_lead(origem, primeira) != p:
            continue
        por_dia[dia] = por_dia.get(dia, 0) + 1
        origens[origem] = origens.get(origem, 0) + 1
    d7 = (hoje - timedelta(days=6)).isoformat()
    return {'hoje': por_dia.get(hoje.isoformat(), 0),
            'ontem': por_dia.get((hoje - timedelta(days=1)).isoformat(), 0),
            'sete': sum(v for k, v in por_dia.items() if k >= d7),
            'por_origem': [{'origem': o, 'total': n} for o, n in
                           sorted(origens.items(), key=lambda x: -x[1])],
            'por_dia': por_dia}


# ---------------------------------------------------------- o que o robo qualificou

# a coluna de cada produto que vira dinheiro: e dela que sai a fila da ligacao
COLUNA_DE_OURO = {EMP: ('Reunião Marcada', 'Proposta Enviada', 'Negociação'), RT: ('Lead Quente', 'Lead Qualificado')}


def _qualificacao(p, desde):
    """Para onde o robo mandou os leads do produto. O numero que interessa a mesa
    nao e quantos chegaram, e quantos chegaram COM o problema que a gente resolve."""
    vazio = {'total': 0, 'colunas': [], 'ouro': 0, 'ouro_nome': '',
             'atendidos': 0, 'ligados': 0, 'board': ''}
    try:
        con = sqlite3.connect('file:%s?mode=ro' % BANCO, uri=True, timeout=15)
    except Exception:
        return vazio
    try:
        linhas = con.execute(
            "select coalesce(b.nome,''), coalesce(v.etapa,''), coalesce(v.origem,''), "
            "       (select m.conteudo from wa_mensagens m where m.conversa_id=v.id "
            "          and m.origem='contato' order by m.id limit 1), "
            "       exists (select 1 from wa_mensagens m where m.conversa_id=v.id and m.origem='ia'), "
            "       exists (select 1 from wa_mensagens m where m.conversa_id=v.id "
            "               and m.origem='nota' and m.autor='ligacao') "
            "  from wa_conversas v left join wa_pipelines b on b.id=v.pipeline_id "
            " where date(datetime(v.criado_em, ?)) >= ?",
            (FUSO, desde)).fetchall()
    except Exception:
        con.close()
        return vazio
    con.close()
    contas, boards, total, atendidos, ligados = {}, {}, 0, 0, 0
    for board, etapa, origem, primeira, tem_ia, tem_lig in linhas:
        if produto_do_lead(origem, primeira) != p:
            continue
        total += 1
        chave = etapa or '(sem coluna)'
        contas[chave] = contas.get(chave, 0) + 1
        boards[board] = boards.get(board, 0) + 1
        atendidos += 1 if tem_ia else 0
        ligados += 1 if tem_lig else 0
    marcas = COLUNA_DE_OURO.get(p, ())
    ouro = sum(n for c, n in contas.items() if any(m in c for m in marcas))
    principal = max(boards.items(), key=lambda x: x[1])[0] if boards else ''
    return {'total': total, 'ouro': ouro, 'ouro_nome': (marcas[0] if marcas else ''),
            'atendidos': atendidos, 'ligados': ligados, 'board': principal,
            'colunas': [{'coluna': c, 'n': n, 'ouro': any(m in c for m in marcas)}
                        for c, n in sorted(contas.items(), key=lambda x: -x[1])]}


# ---------------------------------------------------------------- montagens


def _montar_visao(p):
    hoje = date.today()
    ontem = hoje - timedelta(days=1)
    nod = calendar.monthrange(hoje.year, hoje.month)[1]
    camps = _campanhas(p)
    ids = [c['id'] for c in camps]

    mes = _soma(_insights(ids, date_preset='this_month'))
    dia = _soma(_insights(ids, date_preset='today'))
    crm = _crm(p, (hoje - timedelta(days=13)).isoformat())

    serie, ontem_meta = [], None
    for r in _insights(ids, date_preset='last_14d', time_increment=1):
        d = r.get('date_start')
        achou = next((x for x in serie if x['data'] == d), None)
        if achou:
            achou['gasto'] = round(achou['gasto'] + _brl(r.get('spend')), 2)
            achou['meta'] += meta.conversas(r.get('actions'))
            continue
        serie.append({'data': d, 'gasto': _brl(r.get('spend')),
                      'meta': meta.conversas(r.get('actions')), 'crm': crm['por_dia'].get(d, 0)})
    serie.sort(key=lambda x: x['data'] or '')
    for x in serie:
        x['crm'] = crm['por_dia'].get(x['data'], 0)
        if x['data'] == ontem.isoformat():
            ontem_meta = {'gasto': x['gasto'], 'conversas': x['meta'],
                          'custo': round(x['gasto'] / x['meta'], 2) if x['meta'] else 0}
    if ontem_meta is None:
        ontem_meta = _soma(_insights(ids, date_preset='yesterday'))

    teto = _teto(p)
    projecao = round(mes['gasto'] / hoje.day * nod, 2) if hoje.day else 0
    lista = []
    for c in camps:
        r = _soma(_insights([c['id']], date_preset='this_month'))
        lista.append({'id': c['id'], 'nome': c.get('name', ''),
                      'status': c.get('effective_status', ''),
                      'gasto': r['gasto'], 'conversas': r['conversas'],
                      'custo': r['custo'], 'nossa': True})
    lista.sort(key=lambda x: x['gasto'], reverse=True)

    return {'atualizado_em': int(time.time()), 'produto': p, 'rotulo': ROTULO.get(p, p),
            'hoje': dia, 'ontem': ontem_meta,
            'mes': dict(mes, dia_do_mes=hoje.day, dias_do_mes=nod,
                        por_dia=round(mes['gasto'] / hoje.day, 2) if hoje.day else 0,
                        projecao=projecao, teto=teto,
                        estouro=round(projecao - teto, 2) if projecao > teto else 0),
            'orcamento': _orcamento_ligado(ids),
            'crm': {k: v for k, v in crm.items() if k != 'por_dia'},
            'qualificacao': _qualificacao(p, (hoje - timedelta(days=13)).isoformat()),
            'serie': serie, 'campanhas': lista}


def _montar_dashboard(p, periodo):
    camps = _campanhas(p)
    itens = []
    total = {'gasto': 0.0, 'conversas': 0, 'impressoes': 0, 'alcance': 0, 'cliques': 0}
    for c in camps:
        r = _soma(_insights([c['id']], date_preset=periodo))
        item = {'id': c['id'], 'nome': c.get('name', ''), 'status': c.get('effective_status', ''),
                'gasto': r['gasto'], 'conversas': r['conversas'],
                'custo_por_conversa': r['custo'], 'impressoes': r['impressoes'],
                'alcance': r['alcance'], 'cliques': r['cliques'], 'ctr': r['ctr'],
                'cpc': round(r['gasto'] / r['cliques'], 2) if r['cliques'] else 0}
        itens.append(item)
        for k in ('gasto', 'conversas', 'impressoes', 'alcance', 'cliques'):
            total[k] += item[k if k != 'conversas' else 'conversas']
    total['gasto'] = round(total['gasto'], 2)
    total['custo_por_conversa'] = round(total['gasto'] / total['conversas'], 2) if total['conversas'] else 0
    total['ctr'] = round(total['cliques'] / total['impressoes'] * 100, 2) if total['impressoes'] else 0
    total['cpc'] = round(total['gasto'] / total['cliques'], 2) if total['cliques'] else 0
    itens.sort(key=lambda x: x['gasto'], reverse=True)
    return {'periodo': periodo, 'produto': p, 'rotulo': ROTULO.get(p, p),
            'campanhas': itens, 'total': total}


def _montar_anuncios(p, periodo):
    from .campanhas import _detalhes_dos_anuncios, GERENCIADOR
    ids = [c['id'] for c in _campanhas(p)]
    campos = 'ad_id,ad_name,adset_name,campaign_name,spend,impressions,clicks,ctr,actions'
    rows = _insights(ids, level='ad', date_preset=periodo, fields=campos)
    conta = meta.CONTA_PADRAO.replace('act_', '')
    aids = [r.get('ad_id') for r in rows if r.get('ad_id')]
    det = _detalhes_dos_anuncios(aids) if aids else {}
    itens = []
    for r in rows:
        gasto = _brl(r.get('spend'))
        conv = meta.conversas(r.get('actions'))
        if gasto <= 0 and conv == 0:
            continue
        aid = r.get('ad_id') or ''
        d = det.get(aid) or {}
        cre = d.get('creative') or {}
        itens.append({'id': aid, 'anuncio': r.get('ad_name') or 'sem nome',
                      'conjunto': r.get('adset_name') or '', 'campanha': r.get('campaign_name') or '',
                      'nossa': True, 'status': d.get('effective_status') or '',
                      'link': d.get('preview_shareable_link') or '',
                      'gerenciador': (GERENCIADOR % (conta, aid)) if aid else '',
                      'thumb': cre.get('thumbnail_url') or '',
                      'gasto': gasto, 'conversas': conv,
                      'custo': round(gasto / conv, 2) if conv else None,
                      'cliques': int(r.get('clicks') or 0), 'ctr': _brl(r.get('ctr'))})
    itens.sort(key=lambda x: (x['custo'] is None, x['custo'] or 0))
    return {'periodo': periodo, 'produto': p, 'rotulo': ROTULO.get(p, p), 'anuncios': itens}


def _montar_serie(p, dias):
    preset = 'last_30d' if dias > 14 else ('last_14d' if dias > 7 else 'last_7d')
    ids = [c['id'] for c in _campanhas(p)]
    junta = {}
    for r in _insights(ids, date_preset=preset, time_increment=1):
        d = r.get('date_start')
        x = junta.setdefault(d, {'data': d, 'gasto': 0.0, 'conversas': 0})
        x['gasto'] = round(x['gasto'] + _brl(r.get('spend')), 2)
        x['conversas'] += meta.conversas(r.get('actions'))
    return {'serie': [junta[k] for k in sorted(junta)]}


def _montar_resumo():
    """Os dois quadrados da Visão geral: um por produto, com o mês corrente.

    `produto` vai com o rótulo por extenso e `slug` com o nome curto, que é o que
    os endpoints de dentro do produto recebem. A lista de campanhas vem junto para
    a tela não precisar de uma segunda chamada só para mostrá-las.
    """
    hoje = date.today()
    desde = hoje.replace(day=1).isoformat()
    fora = []
    for p in (RT, EMP):
        camps = _campanhas(p)
        ids = [c['id'] for c in camps]
        r = _soma(_insights(ids, date_preset='this_month'))
        crm = _crm(p, desde)
        leads = sum(crm['por_dia'].values())
        ativas = sum(1 for c in camps if c.get('effective_status') == 'ACTIVE')
        lista = []
        for c in camps:
            rc = _soma(_insights([c['id']], date_preset='this_month'))
            if not rc['gasto'] and not rc['conversas'] and c.get('effective_status') != 'ACTIVE':
                continue
            lista.append({'id': c['id'], 'nome': (c.get('name') or '')[:60],
                          'status': c.get('effective_status', ''),
                          'gasto': rc['gasto'], 'conversas': rc['conversas'],
                          'custo': rc['custo'] or None})
        lista.sort(key=lambda x: -x['gasto'])
        fora.append({'produto': ROTULO[p], 'slug': p, 'rotulo': ROTULO[p],
                     'gasto': r['gasto'], 'conversas': r['conversas'],
                     'custo_por_conversa': r['custo'] or None,
                     'leads_no_crm': leads, 'leads_hoje': crm['hoje'],
                     'custo_por_lead': round(r['gasto'] / leads, 2) if leads else None,
                     'aproveitamento': round(100.0 * leads / r['conversas'], 1) if r['conversas'] else None,
                     'campanhas': lista, 'quantas_campanhas': len(camps),
                     'campanhas_ativas': ativas, 'teto': _teto(p),
                     'percentual_do_teto': round(100.0 * r['gasto'] / _teto(p), 1) if _teto(p) else 0})
    return {'mes': hoje.strftime('%Y-%m'), 'produtos': fora, 'atualizado_em': int(time.time())}


# ---------------------------------------------------------------- rotas


def _valida(p):
    return p if p in (RT, EMP, OUTROS) else RT


@router.get('/produtos')
async def produtos(user: Usuario = Depends(usuario_atual)):
    """Resumo dos dois produtos, para os quadrados da Visão geral."""
    _prep()
    if not meta.get_token():
        return {'erro': 'META_ADS_TOKEN ausente no config/.env', 'produtos': []}
    return await asyncio.to_thread(_cacheado, 'resumo', _montar_resumo)


@router.get('/produto/visao')
async def produto_visao(p: str = Query('trabalhista'), user: Usuario = Depends(usuario_atual)):
    _prep()
    p = _valida(p)
    if not meta.get_token():
        return {'erro': 'META_ADS_TOKEN ausente no config/.env'}
    return await asyncio.to_thread(_cacheado, 'visao:' + p, lambda: _montar_visao(p))


@router.get('/produto/dashboard')
async def produto_dashboard(p: str = Query('trabalhista'), periodo: str = Query('today'),
                            user: Usuario = Depends(usuario_atual)):
    _prep()
    p = _valida(p)
    if periodo not in ('today', 'yesterday', 'last_7d', 'last_14d', 'last_30d', 'maximum'):
        periodo = 'today'
    if not meta.get_token():
        return {'periodo': periodo, 'campanhas': [], 'total': {}}
    return await asyncio.to_thread(_montar_dashboard, p, periodo)


@router.get('/produto/anuncios')
async def produto_anuncios(p: str = Query('trabalhista'), periodo: str = Query('last_7d'),
                           user: Usuario = Depends(usuario_atual)):
    _prep()
    p = _valida(p)
    if periodo not in ('today', 'yesterday', 'last_7d', 'last_14d', 'last_30d', 'maximum'):
        periodo = 'last_7d'
    if not meta.get_token():
        return {'periodo': periodo, 'anuncios': []}
    return await asyncio.to_thread(_cacheado, 'ads:%s:%s' % (p, periodo),
                                   lambda: _montar_anuncios(p, periodo))


@router.get('/produto/serie')
async def produto_serie(p: str = Query('trabalhista'), dias: int = Query(14),
                        user: Usuario = Depends(usuario_atual)):
    _prep()
    p = _valida(p)
    if not meta.get_token():
        return {'serie': []}
    return await asyncio.to_thread(_cacheado, 'serie:%s:%d' % (p, dias),
                                   lambda: _montar_serie(p, dias))
