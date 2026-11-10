"""Marketing: visao geral, campanhas do Meta Ads e ranking de anuncios.

Reusa INTEGRACOES/meta_ads_integration para o lado do Meta e le o proprio banco
do Sistema Escritório Modelo para o lado que importa de verdade: o lead que chegou no CRM.
"""
import asyncio
import calendar
import os
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from ..database import Usuario
from ..deps import usuario_atual

PROJETO_ROOT = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJETO_ROOT / 'INTEGRACOES'))
import meta_ads_integration as meta  # noqa: E402

_preparado = False


def _prep():
    """Carrega o config/.env central (uma vez).

    O app le o .env pelo pydantic-settings, que NAO exporta nada para o
    os.environ. Sem isto, meta_ads_integration.get_token() volta None e a tela
    inteira mostra zero. Mesmo jeito do financeiro.py e do processos.py.
    """
    global _preparado
    if _preparado:
        return
    from dotenv import load_dotenv
    load_dotenv(str(PROJETO_ROOT / 'config' / '.env'))
    _preparado = True


router = APIRouter(prefix='/api/campanhas', tags=['campanhas'])

PERIODOS = {'today', 'yesterday', 'last_7d', 'last_14d', 'last_30d', 'maximum'}
BANCO = Path(__file__).parent.parent.parent / 'sistema_esc.db'
PIPE_RECLAMANTES = 10
# campanhas criadas pela nossa gestao (pelo prefixo no nome); o resto e de agencia/heranca. Ajuste no .env.
NOSSAS = tuple(x.strip().upper() for x in os.getenv('CAMPANHAS_NOSSAS', '[ESC],[TESTE]').split(',') if x.strip())
FUSO = '-3 hours'                       # criado_em e UTC, Brasilia e -3
CACHE_SEG = 240                         # a tela recarrega a cada 60s; nao martelar a Meta
_cache = {}


def _teto():
    """A verba do mes inteiro: a soma do teto de cada produto (senao a tela acusa estouro que nao existe)."""
    mao = os.getenv('TETO_MIDIA_MES')
    if mao:
        return float(mao)
    try:
        from .campanhas_produto import TETO_PADRAO, _teto as teto_do_produto
        return round(sum(teto_do_produto(p) for p in TETO_PADRAO), 2)
    except Exception:
        return 1500.0


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


def _linha(rows):
    """Normaliza uma linha de insights da conta."""
    r = (rows or [{}])[0]
    gasto = _brl(r.get('spend'))
    conv = meta.conversas(r.get('actions'))
    return {'gasto': gasto, 'conversas': conv,
            'custo': round(gasto / conv, 2) if conv else 0,
            'cliques': int(r.get('clicks') or 0),
            'impressoes': int(r.get('impressions') or 0),
            'alcance': int(r.get('reach') or 0),
            'ctr': _brl(r.get('ctr'))}


# ---------------------------------------------------------------- CRM


def _de_campanha(origem, primeira_fala):
    """O lead veio de anuncio? O board nao serve de criterio (cada linha tem o seu):
    vale a mesma regra que cada produto usa na sua pasta."""
    from .campanhas_produto import produto_do_lead
    return produto_do_lead(origem, primeira_fala) is not None


def _crm():
    """Leads que o anuncio trouxe, em qualquer board, por dia e por origem."""
    hoje = date.today()
    d7 = hoje - timedelta(days=6)
    d14 = hoje - timedelta(days=13)
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
            (FUSO, FUSO, d14.isoformat())).fetchall()
        por_dia, contas = {}, {}
        for d, origem, primeira in linhas:
            if not _de_campanha(origem, primeira):
                continue
            por_dia[d] = por_dia.get(d, 0) + 1
            if d >= d7.isoformat():
                contas[origem] = contas.get(origem, 0) + 1
        origens = [{'origem': o, 'total': n} for o, n in
                   sorted(contas.items(), key=lambda x: -x[1])]
    except Exception:
        con.close()
        return vazio
    con.close()
    ontem = (hoje - timedelta(days=1)).isoformat()
    return {'hoje': por_dia.get(hoje.isoformat(), 0),
            'ontem': por_dia.get(ontem, 0),
            'sete': sum(v for k, v in por_dia.items() if k >= d7.isoformat()),
            'por_origem': origens,
            'por_dia': por_dia}


# ---------------------------------------------------------------- Meta


def _orcamento_ligado():
    """Quanto por dia esta ligado agora, somando campanha ou conjuntos."""
    total = 0.0
    itens = []
    for c in meta._get('%s/campaigns' % meta.CONTA_PADRAO,
                       {'fields': 'name,effective_status,daily_budget',
                        'limit': 100}).get('data', []):
        if c.get('effective_status') != 'ACTIVE':
            continue
        nome = c.get('name') or ''
        lado = 'nossa' if any(x in nome.upper() for x in NOSSAS) else 'agencia'
        if c.get('daily_budget'):
            v = int(c['daily_budget']) / 100
            itens.append({'nome': nome[:44], 'lado': lado, 'valor': v})
            total += v
            continue
        for a in meta._get('%s/adsets' % c['id'],
                           {'fields': 'name,effective_status,daily_budget',
                            'limit': 60}).get('data', []):
            if a.get('effective_status') != 'ACTIVE' or not a.get('daily_budget'):
                continue
            v = int(a['daily_budget']) / 100
            itens.append({'nome': (a.get('name') or '')[:44], 'lado': lado, 'valor': v})
            total += v
    itens.sort(key=lambda x: x['valor'], reverse=True)
    return {'por_dia': round(total, 2), 'no_mes': round(total * 30, 2), 'itens': itens}


def _montar_visao():
    hoje = date.today()
    ontem = hoje - timedelta(days=1)
    nod = calendar.monthrange(hoje.year, hoje.month)[1]
    campos = 'spend,impressions,reach,clicks,ctr,actions'

    mes = _linha(meta.insights(meta.CONTA_PADRAO, level='account',
                               date_preset='this_month', fields=campos))
    dia = _linha(meta.insights(meta.CONTA_PADRAO, level='account',
                               date_preset='today', fields=campos))
    serie_raw = meta.insights(meta.CONTA_PADRAO, level='account',
                              date_preset='last_14d', time_increment=1, fields=campos)
    crm = _crm()

    serie = []
    ontem_meta = None
    for r in serie_raw:
        d = r.get('date_start')
        item = {'data': d, 'gasto': _brl(r.get('spend')),
                'meta': meta.conversas(r.get('actions')),
                'crm': crm['por_dia'].get(d, 0)}
        serie.append(item)
        if d == ontem.isoformat():
            ontem_meta = {'gasto': item['gasto'], 'conversas': item['meta'],
                          'custo': round(item['gasto'] / item['meta'], 2) if item['meta'] else 0}
    if ontem_meta is None:
        ontem_meta = _linha(meta.insights(meta.CONTA_PADRAO, level='account',
                                          date_preset='yesterday', fields=campos))

    teto = _teto()
    projecao = round(mes['gasto'] / hoje.day * nod, 2) if hoje.day else 0

    campanhas = []
    for c in meta.listar_campanhas(ativas=True):
        r = meta.resumo_campanha(c['id'], date_preset='this_month') or {}
        campanhas.append({'id': c['id'], 'nome': c.get('name', ''),
                          'status': c.get('effective_status', ''),
                          'gasto': _brl(r.get('gasto')),
                          'conversas': int(r.get('conversas') or 0),
                          'custo': _brl(r.get('custo_por_conversa')),
                          'nossa': any(x in (c.get('name') or '').upper() for x in NOSSAS)})
    campanhas.sort(key=lambda x: x['gasto'], reverse=True)

    return {
        'atualizado_em': int(time.time()),
        'hoje': dia, 'ontem': ontem_meta,
        'mes': dict(mes, dia_do_mes=hoje.day, dias_do_mes=nod,
                    por_dia=round(mes['gasto'] / hoje.day, 2) if hoje.day else 0,
                    projecao=projecao, teto=teto,
                    estouro=round(projecao - teto, 2) if projecao > teto else 0),
        'orcamento': _orcamento_ligado(),
        'crm': {k: v for k, v in crm.items() if k != 'por_dia'},
        'serie': serie,
        'campanhas': campanhas,
    }


GERENCIADOR = ('https://adsmanager.facebook.com/adsmanager/manage/ads'
               '?act=%s&selected_ad_ids=%s')


def _detalhes_dos_anuncios(ids):
    """Previa, miniatura e status de cada anuncio, em lote (ate 50 por chamada)."""
    det = {}
    for i in range(0, len(ids), 50):
        lote = [x for x in ids[i:i + 50] if x]
        if not lote:
            continue
        r = meta._get('', {'ids': ','.join(lote),
                           'fields': 'name,effective_status,preview_shareable_link,'
                                     'creative{thumbnail_url,video_id}'})
        if isinstance(r, dict):
            det.update(r)
    return det


def _montar_anuncios(periodo: str):
    campos = ('ad_id,ad_name,adset_name,campaign_name,spend,impressions,'
              'clicks,ctr,actions')
    rows = meta.insights(meta.CONTA_PADRAO, level='ad', date_preset=periodo, fields=campos)
    conta = meta.CONTA_PADRAO.replace('act_', '')
    ids = [r.get('ad_id') for r in rows if r.get('ad_id')]
    det = _detalhes_dos_anuncios(ids) if ids else {}
    itens = []
    for r in rows:
        gasto = _brl(r.get('spend'))
        conv = meta.conversas(r.get('actions'))
        if gasto <= 0 and conv == 0:
            continue
        aid = r.get('ad_id') or ''
        d = det.get(aid) or {}
        cre = d.get('creative') or {}
        itens.append({'id': aid,
                      'anuncio': r.get('ad_name') or 'sem nome',
                      'conjunto': r.get('adset_name') or '',
                      'campanha': r.get('campaign_name') or '',
                      'nossa': any(x in (r.get('campaign_name') or '').upper() for x in NOSSAS),
                      'status': d.get('effective_status') or '',
                      'link': d.get('preview_shareable_link') or '',
                      'gerenciador': (GERENCIADOR % (conta, aid)) if aid else '',
                      'thumb': cre.get('thumbnail_url') or '',
                      'gasto': gasto, 'conversas': conv,
                      'custo': round(gasto / conv, 2) if conv else None,
                      'cliques': int(r.get('clicks') or 0),
                      'ctr': _brl(r.get('ctr'))})
    itens.sort(key=lambda x: (x['custo'] is None, x['custo'] or 0))
    return {'periodo': periodo, 'anuncios': itens}


def _montar_dashboard(periodo: str) -> dict:
    camps = meta.listar_campanhas(ativas=True)
    itens = []
    total = {'gasto': 0.0, 'conversas': 0, 'impressoes': 0, 'alcance': 0, 'cliques': 0}
    for c in camps:
        r = meta.resumo_campanha(c['id'], date_preset=periodo) or {}
        r = {
            'gasto': r.get('gasto', 0), 'conversas': r.get('conversas', 0),
            'custo_por_conversa': r.get('custo_por_conversa', 0),
            'impressoes': r.get('impressoes', 0), 'alcance': r.get('alcance', 0),
            'cliques': r.get('cliques', 0), 'ctr': r.get('ctr', 0), 'cpc': r.get('cpc', 0),
            'id': c['id'], 'nome': c.get('name', ''),
            'status': c.get('effective_status', ''),
        }
        itens.append(r)
        total['gasto'] += r['gasto']
        total['conversas'] += r['conversas']
        total['impressoes'] += r['impressoes']
        total['alcance'] += r['alcance']
        total['cliques'] += r['cliques']
    total['gasto'] = round(total['gasto'], 2)
    total['custo_por_conversa'] = round(total['gasto'] / total['conversas'], 2) if total['conversas'] else 0
    total['ctr'] = round(total['cliques'] / total['impressoes'] * 100, 2) if total['impressoes'] else 0
    total['cpc'] = round(total['gasto'] / total['cliques'], 2) if total['cliques'] else 0
    itens.sort(key=lambda x: x['gasto'], reverse=True)
    return {'periodo': periodo, 'campanhas': itens, 'total': total}


def _montar_serie(dias: int) -> dict:
    preset = 'last_30d' if dias > 14 else ('last_14d' if dias > 7 else 'last_7d')
    rows = meta.insights(meta.CONTA_PADRAO, level='account', date_preset=preset, time_increment=1)
    serie = []
    for r in rows:
        serie.append({
            'data': r.get('date_start'),
            'gasto': round(float(r.get('spend', 0)), 2),
            'conversas': meta.conversas(r.get('actions')),
        })
    return {'serie': serie}


# ---------------------------------------------------------------- rotas


@router.get('/visao')
async def visao(user: Usuario = Depends(usuario_atual)):
    _prep()
    if not meta.get_token():
        return {'erro': 'META_ADS_TOKEN ausente no config/.env', 'crm': _crm()}
    return await asyncio.to_thread(_cacheado, 'visao', _montar_visao)


@router.get('/anuncios')
async def anuncios(periodo: str = Query('last_7d'), user: Usuario = Depends(usuario_atual)):
    _prep()
    if periodo not in PERIODOS:
        periodo = 'last_7d'
    if not meta.get_token():
        return {'periodo': periodo, 'anuncios': [], 'erro': 'META_ADS_TOKEN ausente no config/.env'}
    return await asyncio.to_thread(_cacheado, 'ads:' + periodo,
                                   lambda: _montar_anuncios(periodo))


@router.get('/dashboard')
async def dashboard(periodo: str = Query('today'), user: Usuario = Depends(usuario_atual)):
    _prep()
    if periodo not in PERIODOS:
        periodo = 'today'
    if not meta.get_token():
        return {'periodo': periodo, 'campanhas': [], 'total': {}, 'erro': 'META_ADS_TOKEN ausente no config/.env'}
    return await asyncio.to_thread(_montar_dashboard, periodo)


@router.get('/serie')
async def serie(dias: int = Query(14), user: Usuario = Depends(usuario_atual)):
    _prep()
    if not meta.get_token():
        return {'serie': []}
    return await asyncio.to_thread(_montar_serie, dias)
