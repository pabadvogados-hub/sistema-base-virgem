""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import usuario_atual
from ..database import get_db, Usuario
from ..whatsapp.models import Conversa, Fluxo, Pipeline

router = APIRouter(prefix='/api/dashboard', tags=['dashboard'])


@router.get('/resumo')
async def resumo(user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    agora = datetime.utcnow()
    inicio_hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_semana = inicio_hoje - timedelta(days=6)
    inicio_serie = inicio_hoje - timedelta(days=13)

    async def _count(where=None):
        q = select(func.count(Conversa.id))
        if where is not None:
            q = q.where(where)
        return (await db.execute(q)).scalar() or 0

    total = await _count()
    nao_lidas = await _count(Conversa.nao_lidas > 0)
    novos_hoje = await _count(Conversa.criado_em >= inicio_hoje)
    novos_semana = await _count(Conversa.criado_em >= inicio_semana)

    tag_rows = (await db.execute(
        select(Conversa.tag, func.count(Conversa.id)).group_by(Conversa.tag))).all()
    por_tag = {}
    for t, c in tag_rows:
        t = (t or '').strip()
        if t:
            por_tag[t] = c

    fx = (await db.execute(
        select(Fluxo.tipo, func.count(Fluxo.id)).where(Fluxo.ativo == True).group_by(Fluxo.tipo))).all()
    fluxos_ativos = {t: c for t, c in fx}

    pipe = (await db.execute(select(Pipeline.id).where(Pipeline.nome == 'Produção'))).scalar_one_or_none()
    em_producao, producao_fases = 0, {}
    if pipe:
        em_producao = await _count(Conversa.pipeline_id == pipe)
        pf = (await db.execute(
            select(Conversa.etapa, func.count(Conversa.id))
            .where(Conversa.pipeline_id == pipe).group_by(Conversa.etapa))).all()
        producao_fases = {e: c for e, c in pf if e}

    serie_rows = (await db.execute(
        select(func.date(Conversa.criado_em), func.count(Conversa.id))
        .where(Conversa.criado_em >= inicio_serie)
        .group_by(func.date(Conversa.criado_em)))).all()
    mapa = {str(d): c for d, c in serie_rows}
    serie = []
    for i in range(14):
        dia = (inicio_serie + timedelta(days=i)).date()
        serie.append({'dia': dia.isoformat(), 'valor': mapa.get(dia.isoformat(), 0)})

    return {
        'conversas_total': total,
        'nao_lidas': nao_lidas,
        'novos_hoje': novos_hoje,
        'novos_semana': novos_semana,
        'fluxos_ativos': fluxos_ativos,
        'fluxos_total': sum(fluxos_ativos.values()),
        'em_producao': em_producao,
        'producao_fases': producao_fases,
        'por_tag': por_tag,
        'serie_novos': serie,
    }


# ---------------------------------------------------------------------------
# PAINEL DE METAS NA TV (pedido do kickoff de 09/11/2026)
# Leitura pura, sem login interativo: a TV abre /painel-metas.html?token=...
# e esta rota so responde se o token bater com PAINEL_METAS_TOKEN do .env.
# Tudo que depende de tabela inexistente devolve 0 e entra em "fonte_pendente".
# ---------------------------------------------------------------------------
import hmac
import os
import re
import sqlite3
from pathlib import Path

from fastapi import HTTPException, Query

_DB_PAINEL = Path(__file__).resolve().parents[2] / 'sistema_esc.db'


def _painel_db() -> str:
    url = os.getenv('DATABASE_URL', '')
    if url.startswith('sqlite') and ':///' in url:
        p = Path(url.split(':///', 1)[1])
        if not p.is_absolute():
            p = Path(__file__).resolve().parents[2] / p
        return str(p)
    return str(_DB_PAINEL)


def _int_env(nome, padrao):
    try:
        return int(float((os.getenv(nome) or str(padrao)).replace(',', '.')))
    except ValueError:
        return padrao


def _float_env(nome, padrao=0.0):
    try:
        return float((os.getenv(nome) or str(padrao)).replace(',', '.'))
    except ValueError:
        return padrao


def _tem_tabela(c, nome):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nome,)).fetchone())


def _colunas(c, nome):
    return {r[1] for r in c.execute(f'PRAGMA table_info({nome})')}


def _montar_painel(mes: str) -> dict:
    ini = mes + '-01'
    a, m = int(mes[:4]), int(mes[5:7])
    fim = '%04d-%02d-01' % (a + (m // 12), m % 12 + 1)
    rx_prot = re.compile(os.getenv('PAINEL_ETAPA_PROTOCOLO', 'DISTRIBU|PROTOCOL'), re.I)
    rx_cont = re.compile(os.getenv('PAINEL_ETAPA_CONTRATO', 'ASSINAD|FECHOU|FECHADO|CONTRATO'), re.I)
    meta_prot = _int_env('PAINEL_META_PROTOCOLOS', 30)
    meta_casos = _int_env('PAINEL_META_CASOS_NOVOS', 100)
    bonus_contrato = _float_env('PAINEL_BONUS_POR_CONTRATO', 0.0)
    bonus_protocolo = _float_env('PAINEL_BONUS_POR_PROTOCOLO', 0.0)
    pendente = []
    saida = {'mes': mes, 'gerado_em': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
             'metas': {'protocolos': meta_prot, 'casos_novos': meta_casos}}
    path = _painel_db()
    if not Path(path).exists():
        pendente.append('banco')
        saida.update({'protocolos': 0, 'contratos': 0, 'casos_novos': 0, 'leads': 0, 'conversao_pct': 0.0,
                      'leads_por_canal': [], 'ranking': [], 'processos_cadastrados': 0,
                      'bonificacao_total': 0.0, 'fonte_pendente': pendente})
        return saida
    c = sqlite3.connect(path, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        nomes = {}
        if _tem_tabela(c, 'usuarios'):
            for r in c.execute('SELECT email, nome FROM usuarios'):
                if r['email']:
                    nomes[r['email'].lower()] = r['nome'] or r['email']

        prot_ids, cont_por_conv = set(), {}
        tem_quadro = _tem_tabela(c, 'wa_board_hist')
        if tem_quadro:
            for r in c.execute("SELECT conversa_id, para_etapa, quem FROM wa_board_hist "
                               "WHERE bloqueado=0 AND criado_em >= ? AND criado_em < ? ORDER BY id", (ini, fim)):
                etapa = r['para_etapa'] or ''
                if rx_prot.search(etapa):
                    prot_ids.add(r['conversa_id'])
                elif rx_cont.search(etapa) and r['conversa_id'] not in cont_por_conv:
                    q = (r['quem'] or '').lower()
                    cont_por_conv[r['conversa_id']] = q if '@' in q and not q.startswith('auto:') else ''
        else:
            pendente.append('wa_board_hist')

        leads, canais, dono_conv = 0, {}, {}
        if _tem_tabela(c, 'wa_conversas'):
            cols = _colunas(c, 'wa_conversas')
            for r in c.execute("SELECT origem, COUNT(*) n FROM wa_conversas WHERE criado_em >= ? AND criado_em < ? "
                               "GROUP BY origem", (ini, fim)):
                k = (r['origem'] or '').strip() or 'WhatsApp direto'
                canais[k] = canais.get(k, 0) + r['n']
                leads += r['n']
            if cont_por_conv and 'atendente_email' in cols:
                ids = list(cont_por_conv)
                for i in range(0, len(ids), 500):
                    lote = ids[i:i + 500]
                    for r in c.execute('SELECT id, atendente_email FROM wa_conversas WHERE id IN (%s)'
                                       % ','.join('?' * len(lote)), lote):
                        dono_conv[r['id']] = (r['atendente_email'] or '').lower()
        else:
            pendente.append('wa_conversas')

        processos_mes = 0
        if _tem_tabela(c, 'jur_processos'):
            for r in c.execute('SELECT data_processo, criado_em FROM jur_processos'):
                d = (r['data_processo'] or '').strip()
                if re.fullmatch(r'\d{2}/\d{2}/\d{4}', d):
                    d = d[6:10] + '-' + d[3:5] + '-' + d[0:2]
                d = d or str(r['criado_em'] or '')[:10]
                if ini <= d[:10] < fim:
                    processos_mes += 1
        else:
            pendente.append('jur_processos')

        # protocolo = card que andou para distribuicao/protocolo no quadro; sem quadro, processos do mes
        protocolos = len(prot_ids) if tem_quadro else processos_mes
        contratos = len(cont_por_conv)

        rank = {}
        for cid, quem in cont_por_conv.items():
            k = quem or dono_conv.get(cid) or 'automacao'
            rank.setdefault(k, {'contratos': 0, 'moedas': 0})
            rank[k]['contratos'] += 1
        if _tem_tabela(c, 'slcoin_extrato') and _tem_tabela(c, 'rh_colaboradores'):
            for r in c.execute("SELECT lower(co.email) email, SUM(e.quantidade) q FROM slcoin_extrato e "
                               "JOIN rh_colaboradores co ON co.id = e.colaborador_id "
                               "WHERE e.tipo='ganho' AND e.data_credito >= ? AND e.data_credito < ? "
                               "GROUP BY co.email", (ini, fim)):
                if r['email']:
                    rank.setdefault(r['email'], {'contratos': 0, 'moedas': 0})
                    rank[r['email']]['moedas'] = int(r['q'] or 0)
        else:
            pendente.append('slcoin_extrato')
        ranking = []
        for email, v in rank.items():
            nome = 'Automação' if email == 'automacao' else nomes.get(email, email.split('@')[0].title())
            ranking.append({'nome': nome, 'contratos': v['contratos'], 'moedas': v['moedas'],
                            'bonus': round(v['contratos'] * bonus_contrato, 2)})
        ranking.sort(key=lambda x: (-x['contratos'], -x['moedas'], x['nome']))

        saida.update({
            'protocolos': protocolos,
            'contratos': contratos,
            'casos_novos': contratos,
            'leads': leads,
            'conversao_pct': round(100.0 * contratos / leads, 1) if leads else 0.0,
            'leads_por_canal': sorted(({'canal': k, 'leads': v} for k, v in canais.items()),
                                      key=lambda x: -x['leads']),
            'ranking': ranking[:10],
            'processos_cadastrados': processos_mes,
            'bonificacao_total': round(contratos * bonus_contrato + protocolos * bonus_protocolo, 2),
            'fonte_pendente': pendente,
        })
        return saida
    finally:
        c.close()


@router.get('/painel-metas')
def painel_metas(token: str = Query(''), mes: str = Query('')):
    """Numeros do painel da TV. So leitura; protegido pelo token do .env."""
    esperado = os.getenv('PAINEL_METAS_TOKEN', '')
    if not esperado:
        raise HTTPException(503, 'Painel desligado: defina PAINEL_METAS_TOKEN no .env')
    if not hmac.compare_digest(token or '', esperado):
        raise HTTPException(401, 'Token invalido')
    if not re.fullmatch(r'\d{4}-\d{2}', mes or ''):
        mes = datetime.now().strftime('%Y-%m')
    return _montar_painel(mes)
