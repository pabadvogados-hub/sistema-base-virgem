# -*- coding: utf-8 -*-
"""Modulo COMERCIAL do Sistema Escritório Modelo: metas, vendedores, funil, contratacao, ticket e provisao, AO VIVO.

A cada acesso, a informacao vem atualizada ate a hora do acesso. Menu proprio, so para administradores.

Nada aqui usa IA: e conta em cima do que o sistema ja registra. A cada acesso:
  CRM (sqlite)        leads, ligacoes (nota autor='ligacao'), WhatsApp dos vendedores, funil, perdas
  esteira             contratos (contratacao_state.json, pedido_em; perdido_em fica de fora) e a etapa do card
  sistema juridico    opcional (cache de 10 min). Sem integracao configurada, a parte financeira fica vazia.
  Meta                cache de no maximo 10 min (comercial_painel_cache.json); o botao Atualizar renova na hora
  base de analise     comercial_base.json (premissas, metas, ticket). Sem o arquivo, valem as metas do .env.

Vendedores: duas vagas configuraveis no .env (COMERCIAL_VENDEDOR_1/2 = nome, COMERCIAL_VENDEDOR_1/2_EMAIL),
com comissao por contrato distribuido (COMERCIAL_COMISSAO_1/2).
"""
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta

import requests
from fastapi import APIRouter, Depends, Query

from ..deps import requer_admin, usuario_atual
from ..whatsapp import coluna_lead

router = APIRouter(prefix='/api/comercial', tags=['comercial'])

BANCO = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
ESTADO_CONTRATACAO = '/opt/escritorio/SYNC/contratacao_state.json'
PRODUTOS = ('trabalhista',)
DIAS_PARADO = 2                                           # alerta de quem fechou e esta parado ha mais de 2 dias
ESTADO_COMERCIAL = '/opt/escritorio/SYNC/comercial_state.json'      # robo da mesa: 'dona_atribuida' (quem conduziu)
BASE = '/opt/escritorio/SYNC/comercial_base.json'
CACHE = '/opt/escritorio/SYNC/comercial_painel_cache.json'
ENV = '/opt/escritorio/config/.env'
INTEGRACOES = '/opt/escritorio/INTEGRACOES'
VALIDADE = 600                      # sistema juridico e Meta: no maximo 10 min
INICIO = date.fromisoformat(os.getenv('COMERCIAL_INICIO', '2026-11-01'))   # comeco da serie do comercial
PIPES_ESC = (10, 11, 18, 20, 31)    # Reclamantes, Clientes, Producao, Contratacao, Palestra (ver whatsapp/funis.py)
# Duas vagas de vendedor, configuraveis no .env (a equipe pode comecar sem vendedor; as vagas ficam prontas)
# Fonte unica: VENDEDORES="Nome:email:telefone:meta:9-18,..." (a mesma do SYNC/roteamento_vendedoras.py).
# Reserva: COMERCIAL_VENDEDOR_1/2 e COMERCIAL_VENDEDOR_1/2_EMAIL.
_VEND = [[x.strip() for x in item.split(':')] for item in (os.getenv('VENDEDORES') or '').split(',') if item.strip()]
_VEND = [v for v in _VEND if v and v[0]]
V1 = (_VEND[0][0] if len(_VEND) > 0 else os.getenv('COMERCIAL_VENDEDOR_1') or 'Vendedor 1').strip()
V2 = (_VEND[1][0] if len(_VEND) > 1 else os.getenv('COMERCIAL_VENDEDOR_2') or 'Vendedor 2').strip()
VENDEDORAS = (V1, V2)
_E1 = (_VEND[0][1] if len(_VEND) > 0 and len(_VEND[0]) > 1 else os.getenv('COMERCIAL_VENDEDOR_1_EMAIL') or '')
_E2 = (_VEND[1][1] if len(_VEND) > 1 and len(_VEND[1]) > 1 else os.getenv('COMERCIAL_VENDEDOR_2_EMAIL') or '')
EMAIL_VENDEDORA = {e.strip().lower(): n for e, n in ((_E1, V1), (_E2, V2)) if e.strip()}
TAXA_CONTRATO = {V1: 'com_v1', V2: 'com_v2'}
MOEDAS_EQUIPE_INICIO = date.fromisoformat(os.getenv('MOEDAS_INICIO', '2026-11-01'))   # programa de moedas da equipe: conta a partir daqui
MOEDAS_EQUIPE_VALOR = {'lig': 1, 'contrato': 10, 'dist': 20, 'manha': 3, 'dia': 5, 'seq3': 10, 'seq5': 25}
MOEDAS_EQUIPE_NIVEIS = [(0, 'Bronze', 1.0), (300, 'Prata', 1.1), (500, 'Ouro', 1.25), (800, 'Diamante', 1.5)]
MOEDAS_EQUIPE_PATAMARES = [(100, 'Pausa'), (300, 'Cuidado'), (700, 'Encontro'), (1500, 'Grande experiência')]   # trilha de recompensas
MOEDAS_EQUIPE_EXEMPLOS = {'Pausa': 'café especial, cinema, livraria, sobremesa', 'Cuidado': 'unha, massagem, dia de beleza, almoço especial',
                    'Encontro': 'jantar a dois, teatro, show, passeio', 'Grande experiência': 'dia de spa, um curso, um eletrônico'}
TAREFA_PROTOCOLO = int(os.getenv('COMERCIAL_TAREFA_PROTOCOLO', '0') or 0)   # tipo de tarefa PROTOCOLO no sistema juridico (0 = nao usa)
CONV = 'onsite_conversion.messaging_conversation_started_7d'
TIPOS_RECLAMANTE = {'RECLAMANTE', 'RECLAMANTE - RECLAMATÓRIA TRABALHISTA', 'RECLAMANTE - TRABALHISTA',
                    'RECLAMATÓRIA TRABALHISTA', 'TRABALHISTA'}
HON = {'HONORÁRIOS FINAIS', 'HONORÁRIOS INICIAIS', 'HONORÁRIOS CONSULTORIAS', 'HONORÁRIOS POR MENSALIDADE', 'TMP',
       'HONORÁRIOS DE SUCUMBÊNCIA', 'AUDIENCIA FISICA'}
EXITO = {'HONORÁRIOS FINAIS', 'HONORÁRIOS DE SUCUMBÊNCIA'}
AVULSO = {'HONORÁRIOS INICIAIS', 'AUDIENCIA FISICA'}
FERIADOS = {date(2026, 10, 12), date(2026, 11, 2), date(2026, 11, 15), date(2026, 11, 20), date(2026, 12, 25),
            date(2027, 1, 1), date(2027, 2, 8), date(2027, 2, 9), date(2027, 3, 26), date(2027, 4, 21),
            date(2027, 5, 1), date(2027, 5, 27), date(2027, 9, 7), date(2027, 10, 12), date(2027, 11, 2),
            date(2027, 11, 15), date(2027, 11, 20), date(2027, 12, 25)}
PERDAS = {'Perdido - Sem resposta': 'Não respondeu à régua', 'Perdido - Sem interesse': 'Disse que não quer',
          'Lead Desqualificado': 'Desqualificado (sem caso ou já tem advogado)', 'Perdido': 'Perdido'}
# Rotulo legivel de cada origem de campanha (ver whatsapp/funis.py)
ANUNCIO = {'Campanha Fast-food': 'Fast-food', 'Campanha Supermercado': 'Supermercado',
           'Campanha Acidente de Trabalho': 'Acidente de trabalho', 'Campanha Geral': 'Trabalhista geral',
           'Campanha Rescisão Indireta': 'Palestra rescisão indireta', 'Campanha Formulario': 'Formulário'}
EMPRESA = re.compile(r'\b(LTDA|EIRELI|EPP|S/?A)\b|TRANSPORT|LOGIST|ENGENHARIA|EQUIPAMENTO|GEST[AÃ]O|COM[EÉ]RCIO|IND[UÚ]STRIA',
                     re.I)
PLACEHOLDER = re.compile(r'nao informado|sobrenome|a confirmar', re.I)
MESES = ('jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez')
DIAS_SEM = ('seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom')

_TRAVA = threading.Lock()
_MEM = {}                           # copia do cache na memoria do servico (1 worker so)


# ------------------------------------------------------------------ utilidades
def _json(caminho, padrao):
    try:
        with open(caminho, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return padrao


def _con():
    c = sqlite3.connect('file:%s?mode=ro' % BANCO, uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _norm(t):
    t = unicodedata.normalize('NFKD', str(t or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z ]', ' ', t).split()


def _utc(d):
    """Inicio do dia d em Brasilia, no relogio do banco (UTC)."""
    return (datetime.combine(d, datetime.min.time()) + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')


def _br(ts):
    """Hora do banco (UTC) -> datetime de Brasilia."""
    try:
        return datetime.fromisoformat(str(ts).replace('T', ' ')[:19]) - timedelta(hours=3)
    except Exception:
        return None


def _iso(ts):
    try:
        return datetime.fromisoformat(str(ts)[:19].replace('T', ' '))
    except Exception:
        return None


def brl(v, casas=0):
    s = f'{float(v or 0):,.{casas}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return 'R$ ' + s


def brl_curto(v):
    v = float(v or 0)
    if abs(v) >= 1e6:
        return 'R$ ' + f'{v / 1e6:.2f}'.replace('.', ',') + ' mi'
    if abs(v) >= 1e4:
        return 'R$ ' + f'{v / 1e3:.0f}' + ' mil'
    return brl(v)


def pct(v, casas=0):
    if v is None:
        return '·'
    return f'{v * 100:.{casas}f}'.replace('.', ',') + '%'


def dm(d):
    return d.strftime('%d/%m') if d else '·'


def mes_rot(m):
    """'2026-10' -> 'out/26'."""
    return '%s/%s' % (MESES[int(m[5:7]) - 1], m[2:4])


def _dias_uteis(ini, fim):
    n, d = 0, ini
    while d <= fim:
        if d.weekday() < 5 and d not in FERIADOS:
            n += 1
        d += timedelta(days=1)
    return n


def _fim_mes(d):
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)


def _add_meses(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


CURTO = {'hoje': 'hoje', 'semana': 'esta semana', 'semana_passada': 'semana passada', 'mes': 'este mês', 'mes_passado': 'mês passado'}

# vocabulario do produto: a tela e a mesma, so as palavras mudam
VOC = {
    'trabalhista': {
        'produto': 'Trabalhista', 'ico': '⚖', 'contrato': 'contrato', 'contratos': 'contratos', 'Contrato': 'Contrato',
        'Contratos': 'Contratos', 'fechado': 'fechado', 'fechados': 'fechados', 'tit_contratos': 'Contratos fechados',
        'libera': 'libera quando o card entra em Distribuição', 'libera_quando': 'quando o card entra em Distribuição',
        'libera_card': 'Cada card que entra em Distribuição libera', 'Em_dist': 'Em distribuição', 'dist_txt': 'em Distribuição',
        'dist_desde': 'em Distribuição desde', 'a_dist': 'à distribuição', 'virar': 'virar processo', 'processos': 'processos',
        'fluxo': 'Do Fechou à Produção', 'honorario': 'honorário esperado', 'anuncios': 'campanhas do Meta',
        'sit': ['Já saiu', 'Trabalha', 'Não disse'], 'sit_txt': ['já saiu', 'trabalha', 'não disse'],
        'sit_pl': ['já saíram', 'trabalham', 'não disseram'], 'sit_card': ['Já saíram da empresa', 'Ainda trabalham', 'Não disseram'],
        'primeira': '1ª ligação ao demitido', 'parte_sit': 'Parte dos leads que já saiu da empresa', 'conv_quem': 'reclamantes',
        'fases': [['dist', '⚖', 'Em distribuição'], ['revisao', '✎', 'Inicial em revisão'], ['zapsign', '✍', 'No ZapSign'],
                  ['docs', '⌛', 'Esperando documentos'], ['perdido', '✕', 'Perdidos']],
        'desde_inicio': 'desde agosto', 'status_dist': 'em distribuição', 'nenhum_esperando': 'Nenhum contrato esperando a Distribuição.',
        'com_tit': 'Comissões: quando o card entra em Distribuição',
        'com_sub': 'Comissão por caso que entra em Distribuição (valores por vendedor no .env). A comissão entra no mês em que o card é '
                   'colocado em Distribuição: atraso do jurídico no protocolo não atrasa o comercial. Toque na linha para ver contrato a contrato.',
        'a_liberar': 'a liberar quando o card entrar em Distribuição'},
}
VOC['trabalhista'].update({
    'fluxo_sub': 'Quanto tempo o cliente leva do Fechou até o card chegar na Produção, pela semana em que fechou, e quantos se perdem no caminho. Toque nos números para ver caso a caso.',
    'fluxo_nota': 'Na semana em andamento a média conta só quem já chegou na Produção; quem ainda está na contratação aparece na última coluna.',
    'chegaram': 'Chegaram na Produção', 'na_dest': 'Na Produção', 'parados_sub': 'ainda na contratação: os que mais correm risco',
    'ate_dist': 'a distribuição', 'funil_sub': 'De lead ao card em Distribuição.', 'trab_sub': 'convertem bem menos · voltam na revisita',
    'nd_sub': 'a IA pergunta na 1ª resposta', 'conv_sit': 'Reclamante, por situação', 'tempo_tit': 'Tempo no fluxo de contratação',
    'acompanha': 'Cada contrato que você acompanha até a Distribuição é comissão garantida no seu mês.',
    'sua_comissao': '💰 Sua comissão libera quando o card entra em Distribuição'})


def _periodo(chave, hoje):
    seg = hoje - timedelta(days=hoje.weekday())
    if chave == 'hoje':
        return hoje, hoje, 'Hoje'
    if chave == 'semana_passada':
        a, b = seg - timedelta(days=7), seg - timedelta(days=1)
        return a, b, 'Semana passada · %s a %s' % (dm(a), dm(b))
    if chave == 'mes':
        a = hoje.replace(day=1)
        return a, hoje, '%s até hoje' % mes_rot(a.isoformat()).capitalize()
    if chave == 'mes_passado':
        b = hoje.replace(day=1) - timedelta(days=1)
        return b.replace(day=1), b, mes_rot(b.isoformat()).capitalize()
    return seg, hoje, 'Esta semana · %s a %s' % (dm(seg), dm(hoje))


def _quem(texto):
    """Primeiro nome de quem ligou/escreveu, com as vendedoras normalizadas."""
    n = _norm(str(texto or '').split('@')[0].replace('.', ' '))
    if not n:
        return ''
    for v in VENDEDORAS:
        if n[0] == _norm(v)[0]:
            return v
    return n[0].capitalize()


def _nome(t):
    t = re.sub(r'[^\w\s\'.-]', '', str(t or ''), flags=re.U).strip()
    return ' '.join(w.capitalize() if w.isupper() or w.islower() else w for w in t.split()) or '(sem nome)'


# ------------------------------------------------------------------ base de analise (premissas e o que nao muda no dia)
def _base():
    b = _json(BASE, {})
    b.setdefault('premissas', {})
    b.setdefault('metas', [])
    return b


def _meta_mes(base, mes):
    """Meta do escritorio e de cada vendedora no mes 'AAAA-MM' (escada; depois contr_2027)."""
    P = base['premissas']
    for m in base['metas']:
        if m['mes'] == mes:
            return {'meta': m['meta'], V1: m['v1'], V2: m['v2'], 'dias': m['dias'],
                    'verba': m.get('verba') or P.get('verba', 1500)}
    alvo = P.get('contr_2027', 30) if mes >= '2027-01' else 0
    raf = round(alvo * P.get('part_v1', 0.5))
    d0 = date(int(mes[:4]), int(mes[5:7]), 1)
    return {'meta': alvo, V1: raf, V2: alvo - raf, 'dias': _dias_uteis(d0, _fim_mes(d0)),
            'verba': P.get('verba', 1500)}


def _meta_periodo(base, ini, fim, chave):
    """Meta do periodo dia util a dia util: cada dia vale a meta do seu mes / dias uteis do mes. Antes da escada
    (setembro e antes) usa o 1o degrau como referencia."""
    primeiro = min((m['mes'] for m in base['metas']), default='2026-10')
    total, d = 0.0, ini
    while d <= fim:
        if d.weekday() < 5 and d not in FERIADOS:
            m = max(d.strftime('%Y-%m'), primeiro)
            d0 = d.replace(day=1)
            total += _meta_mes(base, m)[chave] / max(1, _dias_uteis(d0, _fim_mes(d0)))
        d += timedelta(days=1)
    return total


def _escada(base):
    """O que cada degrau da escada pede (FUNIL REVERSO da planilha, mix alvo)."""
    P = base['premissas']
    g = lambda k, d=0: float(P.get(k, d) or 0)
    conv_b = g('mix_alvo') * g('conv_dem') + (1 - g('mix_alvo')) * g('conv_trab')
    base_ini = g('base', 577)
    out = []
    for m in base['metas']:
        meta, du = m['meta'], m['dias'] or 21
        contr_base = base_ini * g('conv_base')
        novos = max(0.0, meta - contr_base)
        leads_b = novos / conv_b if conv_b else 0
        sugerida = max(g('verba'), -(-leads_b * g('cpl') // 100) * 100)
        verba = m.get('verba_aprovada') or m.get('verba') or sugerida
        leads_c = verba / g('cpl') if g('cpl') else 0
        lig = leads_c * g('tentativas') + base_ini * g('lig_base')
        out.append({'mes': m['mes'], 'meta': meta, 'v1': m['v1'], 'v2': m['v2'], 'dias': du,
                    'base_ini': round(base_ini), 'da_base': round(contr_base, 1), 'novos': round(novos, 1),
                    'verba': verba, 'leads': round(leads_c), 'lig_dia': round(lig / du),
                    'cap': int(g('cap_raf') + g('cap_mar')), 'producao': round(meta * g('pct_dist')),
                    'cap_prod': int(g('cap_prod'))})
        base_ini = max(0.0, base_ini * (1 - g('perda_base')) - contr_base + leads_c * g('fica_base'))
    return out


# ------------------------------------------------------------------ sistema jurídico e Meta (cache de 10 min)
def _env(chave):
    try:
        for linha in open(ENV, encoding='utf-8', errors='replace'):
            if linha.startswith(chave + '='):
                return linha.split('=', 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return os.getenv(chave, '')


def _advbox():
    # Integracao financeira opcional (ADVBOX). Sem token, o painel segue sem a parte financeira.
    if not _env('ADVBOX_TOKEN'):
        return {'transacoes': [], 'processos': []}
    if INTEGRACOES not in sys.path:
        sys.path.insert(0, INTEGRACOES)
    from dotenv import load_dotenv
    load_dotenv(ENV)
    import advbox_integration as adv
    trans = adv.listar_transacoes({'date_due_start': '2024-01-01', 'date_due_end': '2027-12-31'})
    leves = []
    for t in trans:
        if t.get('entry_type') != 'income' or t.get('category') not in HON or float(t.get('amount') or 0) < 1:
            continue
        leves.append({'v': float(t['amount']), 'cat': t['category'], 'pg': (t.get('date_payment') or '')[:10],
                      'vc': (t.get('date_due') or '')[:10], 'nome': t.get('name') or '',
                      'proc': t.get('process_number') or ''})
    procs, off = [], 0
    while True:
        d = adv._request('GET', '/lawsuits', params={'limit': 1000, 'offset': off})
        lote = (d or {}).get('data') or []
        for p in lote:
            procs.append({'type': p.get('type') or '', 'cnj': p.get('process_number') or '',
                          'dist': str(p.get('process_date') or '')[:10], 'criado': str(p.get('created_at') or '')[:10],
                          'clientes': [c.get('name') or '' for c in (p.get('customers') or [])],
                          'etapa': p.get('stage') or '', 'id': p.get('id'),
                          'pasta': p.get('folder') or ''})
        if len(lote) < 1000:
            break
        off += 1000
    # tarefa de PROTOCOLO no sistema juridico: a peca vai para protocolo antes de o card entrar em
    # Distribuicao no CRM; sem isto a comissao da mesa ficaria invisivel no relatorio.
    protocolo = {}
    try:
        for t in (adv.listar_tarefas(task_id=TAREFA_PROTOCOLO, limit=100) or []):
            pid = str(t.get('lawsuits_id') or t.get('lawsuit_id') or '')
            quando = str(t.get('start_date') or t.get('date_deadline') or t.get('created_at') or '')[:10]
            if pid and (pid not in protocolo or (quando and quando < protocolo[pid])):
                protocolo[pid] = quando
    except Exception:
        protocolo = {}
    for p in procs:
        p['protocolo_em'] = protocolo.get(str(p.get('id') or ''), '')
    return {'transacoes': leves, 'processos': procs}


def _meta_ads():
    tok = _env('META_ADS_TOKEN')
    acc = _env('META_ADS_ACCOUNT') or _env('META_AD_ACCOUNT')
    if acc and not acc.startswith('act_'):
        acc = 'act_' + acc
    dias, url = {}, 'https://graph.facebook.com/v21.0/%s/insights' % acc
    params = {'fields': 'spend,actions', 'access_token': tok, 'time_increment': 1, 'limit': 500,
              'time_range': json.dumps({'since': str(INICIO), 'until': str(date.today())})}
    while url:
        r = requests.get(url, params=params, timeout=30).json()
        if 'error' in r:
            raise RuntimeError(r['error'].get('message', 'erro do Meta')[:160])
        for x in r.get('data', []):
            conv = sum(int(float(a['value'])) for a in (x.get('actions') or []) if a.get('action_type') == CONV)
            dias[x['date_start']] = [float(x.get('spend') or 0), conv]
        url, params = (r.get('paging') or {}).get('next'), None
    return dias


def _fontes(forcar=False):
    """sistema jurídico e Meta com no maximo 10 min. 'forcar' (botao Atualizar) renova se tiver mais de 1 min."""
    c = _MEM.get('c') or _json(CACHE, {})
    limite = 60 if forcar else VALIDADE
    if time.time() - c.get('ts', 0) < limite:
        return c
    with _TRAVA:
        c = _MEM.get('c') or _json(CACHE, {})
        if time.time() - c.get('ts', 0) < limite:
            return c
        novo, erros = dict(c), {}
        try:
            novo['advbox'] = _advbox()
            novo['advbox_em'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        except Exception as e:
            erros['advbox'] = str(e)[:160]
        try:
            novo['meta'] = _meta_ads()
            novo['meta_em'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        except Exception as e:
            erros['meta'] = str(e)[:160]
        novo['erros'], novo['ts'] = erros, time.time()
        _MEM['c'] = novo
        try:
            # o servico roda como www-data, que nao grava na pasta SYNC: o arquivo do cache foi criado uma vez
            # com dono www-data e e regravado no lugar (a trava acima garante um escritor so)
            with open(CACHE, 'w', encoding='utf-8') as fh:
                json.dump(novo, fh, ensure_ascii=False)
        except Exception:
            pass
        return novo


# ------------------------------------------------------------------ esteira de contratacao + distribuicao no sistema jurídico
FASES = {'dist': 'Em distribuição ou distribuído', 'revisao': 'Inicial em revisão', 'zapsign': 'Contrato no ZapSign',
         'docs': 'Esperando documentos', 'perdido': 'Perdido no CRM', 'outro': 'Outra etapa'}


def quem_conduziu(con, cid, ate):
    """Vendedora que conduziu a venda. Pontos de cada vendedora
    ANTES do card entrar em "Fechou!" (ate = hora do banco, UTC): ligacao pela DAPI que ela fez = 3, mensagem dela no
    WhatsApp = 1, card movido por ela = 1. O que a producao conversa depois do Fechou nao conta.
    Empate: quem falou por ultimo. Nenhuma vendedora falou: None (fica a dona do card)."""
    pts, ult = {}, {}

    def soma(v, p, t):
        if v in VENDEDORAS:
            pts[v] = pts.get(v, 0) + p
            ult[v] = max(ult.get(v, ''), str(t))
    for txt, t in con.execute("select coalesce(conteudo,''), criado_em from wa_mensagens where conversa_id=? and "
                              "autor='ligacao' and conteudo like '%iniciada por%' and replace(criado_em,'T',' ') < ?",
                              (cid, ate)):
        m = re.search(r'iniciada por (.+?) para', txt)
        if m:
            soma(_quem(m.group(1)), 3, t)
    for a, t in con.execute("select autor, criado_em from wa_mensagens where conversa_id=? and origem='atendente' "
                            "and autor like '%@%' and replace(criado_em,'T',' ') < ?", (cid, ate)):
        soma(_quem(a), 1, t)
    for q, t in con.execute("select quem, criado_em from wa_board_hist where conversa_id=? and origem='ui' "
                            "and quem like '%@%' and replace(criado_em,'T',' ') < ?", (cid, ate)):
        soma(_quem(q), 1, t)
    if not pts:
        return None
    return max(pts, key=lambda v: (pts[v], ult[v]))


def _fase(card):
    if not card:
        return 'outro', 'Card não encontrado (duplicado?)'
    pipe, etapa = card['pipeline_id'], card['etapa'] or ''
    e = ' '.join(_norm(etapa))
    if 'perdido' in e:
        return 'perdido', 'Perdido no CRM'
    if pipe == 11 or 'distribuid' in e:
        return 'dist', etapa
    if 'distribuicao' in e:
        return 'dist', 'Em distribuição'                 # etapa "Distribuicao" da Producao: libera a comissao
    if 'revis' in e:
        return 'revisao', 'Inicial em revisão'
    if 'zapsign' in e:
        return 'zapsign', 'Contrato no ZapSign'
    if 'document' in e:
        return 'docs', etapa
    if pipe == 10:
        return 'outro', 'Voltou para Leads: ' + etapa
    return 'outro', etapa


def _nome_limpo(c):
    """Nome do contrato, ou o do WhatsApp quando o do contrato saiu com placeholder (ex.: 'Heitor sobrenome nao informado')."""
    nc = c.get('nome_contrato') or ''
    return c.get('nome_wa') or nc if (not nc or PLACEHOLDER.search(' '.join(_norm(nc)))) else nc


CONECTIVO = ('e', 'de', 'da', 'do', 'das', 'dos', 'em', 'y')


def _empresa(pasta):
    """Empresa (a reclamada) pelo nome da pasta do processo: 'FULANO X EMPRESA' vira 'EMPRESA'.
    Sigla curta em maiuscula fica como esta (JSL, VLI, MJL, LTDA), conectivo vai para minuscula."""
    partes = re.split(r'\s+[xX]\s+', str(pasta or ''), maxsplit=1)
    if len(partes) < 2 or not partes[1].strip():
        return ''
    saida = []
    for w in partes[1].split():
        limpo = ' '.join(_norm(w))
        if limpo in CONECTIVO:
            saida.append(limpo)
        elif w.isupper() and len(w) <= 5 and w.isalpha():
            saida.append(w)
        else:
            saida.append(_nome(w))
    return ' '.join(saida)


def _casa_manual(m, c, a):
    """Liberacao que a Advogada registrou na base (`comissao_manual`): casa pelo id do processo; sem id,
    por cliente e empresa."""
    if m.get('processo') and a.get('id'):
        return str(m['processo']) == str(a['id'])
    alvo = ' '.join(_norm(m.get('cliente'))[:2])
    if not alvo or alvo not in ' '.join(_norm(_nome_limpo(c))):
        return False
    emp = ' '.join(_norm(m.get('empresa')))
    return not emp or emp in ' '.join(_norm(a.get('empresa')))


def _distribuicao(contratos, processos):
    """Casa o contrato com o processo de reclamante no sistema jurídico pelo nome do cliente (o do contrato; senao o do
    WhatsApp). Mesma regra do atualizar_ate_hoje.py. Sem CNJ = ainda nao distribuido."""
    cand = [p for p in processos if p['type'] in TIPOS_RECLAMANTE and p['criado'] >= '2026-07-15']
    for c in contratos:
        if c.get('advbox_fixo'):
            achou = c['advbox_fixo']
            c['advbox'], c['cnj'] = achou, achou.get('cnj') or ''
            c['distribuido_em'] = ((achou.get('dist') or '')[:10] or None) if c['cnj'] else None
            if c['cnj']:
                c['fase'], c['etapa'] = 'dist', 'Distribuído (fora da esteira)'
            continue
        alvo = _norm(_nome_limpo(c))
        achou, acoes = None, []
        for p in cand:
            for cli in p['clientes']:
                n = _norm(cli)
                if alvo and alvo[0] in n and (len(alvo) == 1 or any(t in n for t in alvo[1:] if len(t) > 2)):
                    if not achou or (p['cnj'] and not achou['cnj']):
                        achou = {'cliente': cli, 'cnj': p['cnj'], 'dist': p['dist'], 'etapa': p['etapa']}
                    # uma entrada por ACAO: a mesma pessoa pode ter mais de um processo (contra duas
                    # empresas), e cada um pontua quando libera
                    if not any(str(x.get('id')) == str(p.get('id')) for x in acoes):
                        acoes.append({'id': p.get('id'), 'empresa': _empresa(p.get('pasta')),
                                      'cnj': p['cnj'], 'dist': p['dist'], 'etapa': p['etapa'],
                                      'pasta': p.get('pasta') or '', 'criado': p['criado'],
                                      'protocolo_em': p.get('protocolo_em') or ''})
                    break
        c['acoes'] = sorted(acoes, key=lambda a: a.get('criado') or '')
        c['advbox'] = achou
        c['cnj'] = (achou or {}).get('cnj') or ''
        c['distribuido_em'] = None
        if c['cnj']:
            c['distribuido_em'] = (achou.get('dist') or '')[:10] or None
            c['fase'], c['etapa'] = 'dist', 'Distribuído'


def _meta_gr_dias():
    """Mantido por compatibilidade: nao ha segundo produto na conta do Meta. Sempre vazio."""
    return {}

def _base_produto(base, produto):
    """Base de analise do produto (um produto so: trabalhista)."""
    return base

def _rateio(b, f):
    """Parte da mesa e das ferramentas que cabe ao produto (pela proporcao de ligacoes)."""
    if f >= 0.999:
        return b
    b = dict(b)
    P = dict(b['premissas'])
    for k in ('sal_v1', 'ajuda_v1', 'custo_v2', 'telefonia', 'zapsign', 'api_ia', 'vps'):
        P[k] = float(P.get(k) or 0) * f
    b['premissas'] = P
    return b


def _fluxo_agrega(L, hoje, D, semanas, T=None):
    """Agrega o fluxo por semana em que fechou."""
    T = T or {'chegou': 'chegou na Produção', 'col': 'Chegou na Produção', 'casos': 'Do Fechou à Produção: caso a caso (%d)',
              'perd_sub': 'Card em Perdido, perdido na esteira ou de volta aos Leads sem chegar na Produção.',
              'parado_tit': 'Na contratação há mais de %d dias (%d)',
              'parado_sub': 'Ainda podem chegar na Produção: são os que mais correm risco.'}
    if not L:
        return None
    seg = hoje - timedelta(days=hoje.weekday())
    sem = []
    for i in range(semanas - 1, -1, -1):
        a = seg - timedelta(days=7 * i)
        g = [x for x in L if a <= x['fechou'].date() <= a + timedelta(days=6)]
        ds = [x['dias'] for x in g if x['sit'] == 'producao']
        sem.append({'rot': ('esta semana' if i == 0 else 'semana passada' if i == 1 else 'semana de ' + dm(a)) +
                           ' (' + dm(a) + ' a ' + dm(min(a + timedelta(days=6), hoje)) + ')',
                    'n': len(g), 'producao': len(ds), 'dias': round(sum(ds) / len(ds), 1) if ds else None,
                    'perdido': sum(1 for x in g if x['sit'] == 'perdido'),
                    'contratacao': sum(1 for x in g if x['sit'] == 'contratacao')})
    sit_rot = {'producao': T['chegou'], 'perdido': 'perdido', 'contratacao': 'na contratação', 'outro': 'outro board'}
    linha = lambda x: [x['fechou'].strftime('%d/%m %H:%M'), x['cliente'],
                       x['producao'].strftime('%d/%m %H:%M') if x['producao'] else '·',
                       x['dias'] if x['dias'] is not None else '·', sit_rot[x['sit']] + ' · ' + x['etapa']]
    cols = ['Fechou em', 'Cliente', T['col'], 'Dias', 'Situação · etapa agora']
    L.sort(key=lambda x: x['fechou'], reverse=True)
    perd = [x for x in L if x['sit'] == 'perdido']
    par = [x for x in L if x['sit'] == 'contratacao' and x['parado'] > DIAS_PARADO]
    D['fluxo_casos'] = {'tit': T['casos'] % len(L), 'cols': cols, 'rows': [linha(x) for x in L],
                        'sub': 'Todos os cards que entraram em "Fechou" desde %s.' % dm(min(x['fechou'] for x in L).date())}
    D['fluxo_perdidos'] = {'tit': 'Disseram que iam fechar e se perderam (%d)' % len(perd), 'cols': cols,
                           'rows': [linha(x) for x in perd], 'sub': T['perd_sub']}
    D['fluxo_parados'] = {'tit': T['parado_tit'] % (DIAS_PARADO, len(par)), 'cols': cols,
                          'rows': [linha(x) for x in par], 'sub': T['parado_sub']}
    return {'n': len(L), 'desde': dm(min(x['fechou'] for x in L).date()), 'semanas': sem,
            'producao': sum(1 for x in L if x['sit'] == 'producao'), 'perdido': len(perd),
            'contratacao': sum(1 for x in L if x['sit'] == 'contratacao'), 'parados': len(par), 'dias_parado': DIAS_PARADO}


def _contratos(con, base, processos, hoje):
    est = dict(_json(ESTADO_CONTRATACAO, {}).get('conversas') or {})
    # "Fechou" na hora: o robo da contratacao registra o contrato a cada 30 min, mas o card entra na
    # etapa "Fechou!" antes (de 9 a 33 min antes nos casos de 28/09 a 02/10). Conta pelo card; quando o robo registrar,
    # fica a entrada dele (mesma conversa, sem contar duas vezes).
    for r in con.execute("select conversa_id cid, min(criado_em) t from wa_board_hist where para_etapa like 'Fechou%' "
                         "and bloqueado=0 and replace(criado_em,'T',' ') >= ? group by conversa_id",
                         (_utc(hoje - timedelta(days=3)),)):
        quando = _br(r['t'])
        if quando and str(r['cid']) not in est:
            est[str(r['cid'])] = {'pedido_em': quando.isoformat(), 'nome': '', 'provisorio': True}
    ids = [int(k) for k in est if str(k).isdigit()]
    cards = {}
    if ids:
        q = ('select id, pipeline_id, etapa, atendente_email, contato_nome, criado_em, origem from wa_conversas '
             'where id in (%s)' % ','.join('?' * len(ids)))
        cards = {r['id']: r for r in con.execute(q, ids)}
    estreia = base.get('estreia_v1', str(INICIO))
    fechou_em = {str(r[0]): str(r[1]).replace('T', ' ')[:19] for r in con.execute(
        "select conversa_id, min(criado_em) from wa_board_hist where para_etapa like 'Fechou%' and bloqueado=0 "
        "group by conversa_id")}
    atribuidos = (_json(ESTADO_COMERCIAL, {}).get('dona_atribuida') or {})
    out = []
    for cid, v in est.items():
        pedido = (v.get('pedido_em') or '')[:10]
        if not pedido:
            continue
        card = cards.get(int(cid)) if str(cid).isdigit() else None
        dona = _quem(card['atendente_email']) if card and card['atendente_email'] else ''
        if card and str(cid) not in atribuidos and pedido >= str(hoje - timedelta(days=3)):
            # contrato novo e de quem conduziu a venda; o robo da mesa grava a dona no card em ate 20 min
            ate = fechou_em.get(str(cid)) or _utc(date.fromisoformat(pedido) + timedelta(days=1))
            conduziu = quem_conduziu(con, int(cid), ate)
            if conduziu:
                dona = conduziu
        # a dona que ela fixou vale sobre o calculo. O `atendente_email` do
        # card guarda quem atendeu por ultimo, nao quem vendeu, e o recalculo por
        # quem_conduziu() so roda nos 3 primeiros dias: sem isso, um contrato antigo fica no
        # nome de quem mexeu no card depois.
        fixada = (base.get('dona_contrato') or {}).get(str(cid))
        if fixada:
            dona = fixada
        vend = dona if dona in VENDEDORAS else None
        quem = dona or 'sem dona'
        if vend == V1 and pedido < estreia:
            vend, quem = None, V1 + ' (antes da estreia)'
        fase, etapa = _fase(card)
        criado = _br(card['criado_em']) if card else None
        dpedido = date.fromisoformat(pedido)
        out.append({'cid': str(cid), 'pedido': pedido, 'data': dpedido, 'perdido': bool(v.get('perdido_em')),
                    'nome_wa': v.get('nome') or (card['contato_nome'] if card else '') or '',
                    'nome_contrato': v.get('nome_contrato') or '',
                    'cliente': _nome(v.get('nome_contrato') or v.get('nome') or (card['contato_nome'] if card else '')),
                    'vendedora': vend, 'quem': quem, 'fase': fase, 'etapa': etapa,
                    'origem': ANUNCIO.get(card['origem'], card['origem']) if card and card['origem'] else 'Orgânico',
                    'da_base': bool(criado and criado.date() < dpedido - timedelta(days=7)),
                    'dias': (hoje - dpedido).days, 'tem_card': card is not None})
    # Duplicata: a mesma pessoa em duas entradas da esteira (ex.: cliente que trocou de
    # numero) conta uma vez so. Mesmo nome e sobrenome; sai a entrada cujo card ja nao existe no CRM.
    grupos = {}
    for c in out:
        grupos.setdefault(' '.join(_norm(_nome_limpo(c))[:2]), []).append(c)
    for g in grupos.values():
        if len(g) > 1 and any(x['tem_card'] for x in g):
            for x in g:
                x['duplicado'] = not x['tem_card']
    _distribuicao(out, processos)
    fora = _fora_da_esteira(con, base, processos, out, hoje)   # depende do casamento da esteira acima
    _distribuicao(fora, processos)                              # so copia o processo que ja veio do sistema jurídico
    out += fora
    # SEGUNDO CONTRATO DO MESMO CARD. A lista acima vem do contratacao_state,
    # que guarda uma entrada por CONVERSA: cliente antigo que volta e fecha um caso NOVO usa o
    # mesmo card e o 2o contrato ficava invisivel. Entram aqui, depois da deduplicacao por nome, porque dois contratos do mesmo
    # cliente seriam lidos como duplicata; e sem advbox, para nao casarem com o processo do
    # caso anterior. A comissao continua liberando pelo historico do card: _liberacao so olha
    # entrada em Distribuicao com data >= a do pedido, ou seja, a do caso novo.
    for x in (base.get('contratos_extras') or []):
        pedido = str(x.get('pedido_em') or '')[:10]
        cid_x = str(x.get('cid') or '')
        if not pedido or not cid_x.isdigit():
            continue
        card = cards.get(int(cid_x)) or con.execute(
            'select id, pipeline_id, etapa, atendente_email, contato_nome, criado_em, origem '
            'from wa_conversas where id=?', (int(cid_x),)).fetchone()
        fase_x, etapa_x = _fase(card)
        dped = date.fromisoformat(pedido)
        vend_x = x.get('vendedora') if x.get('vendedora') in VENDEDORAS else None
        out.append({'cid': '%s:%s' % (cid_x, x.get('sufixo') or 2), 'card_id': cid_x,
                    'pedido': pedido, 'data': dped, 'perdido': bool(x.get('perdido')),
                    'nome_wa': (card['contato_nome'] if card else '') or '',
                    'nome_contrato': x.get('cliente') or '',
                    'cliente': _nome(x.get('cliente') or (card['contato_nome'] if card else '')),
                    'vendedora': vend_x, 'quem': vend_x or 'sem dona',
                    'fase': fase_x, 'etapa': x.get('etapa') or etapa_x,
                    'origem': (ANUNCIO.get(card['origem'], card['origem'])
                               if card and card['origem'] else 'Org\u00e2nico'),
                    'da_base': True, 'dias': (hoje - dped).days, 'tem_card': card is not None,
                    'advbox': None, 'cnj': '', 'distribuido_em': None, 'extra': True})
    for c in out:
        if c['advbox'] and c['advbox'].get('cliente'):
            c['cliente'] = _nome(re.sub(r'\bLEAD\b', '', c['advbox']['cliente'], flags=re.I))
        elif re.search(r'nao informado|sobrenome|\{\{', ' '.join(_norm(c['nome_contrato'])) + c['nome_contrato']):
            c['cliente'] = _nome(c['nome_wa'])          # nome do contrato com cara de placeholder: usa o do WhatsApp
        if c.get('duplicado'):
            c['perdido'], c['fase'], c['etapa'] = True, 'perdido', 'Duplicado (a mesma pessoa em outra entrada)'
        elif c['perdido']:
            c['fase'], c['etapa'] = 'perdido', 'Perdido na esteira'
        elif c['fase'] == 'perdido':
            # quem desistiu sai da contagem e da fila de comissao da vendedora (card em Perdido no CRM)
            c['perdido'], c['etapa'] = True, 'Desistiu (card em Perdido no CRM)'
    _liberacao(con, out, base.get('comissao_manual'))
    return sorted(out, key=lambda c: (c['pedido'], c['cid']))


def _liberacao(con, contratos, manuais=None):
    """Comissao da vendedora: ela pontua e recebe quando o card e colocado em Distribuicao (etapa
    "Distribuicao" da Producao, ou "Distribuida"), para que atraso do juridico no protocolo nao atrase o comercial.
    Data = primeira entrada do card nessas etapas DEPOIS do fechamento (historico do quadro, hora de Brasilia). O board
    Clientes nao conta: o card passa por la por outros motivos (testemunha, clique em "Novo Cliente"). Sem historico: a
    data de distribuicao do processo no sistema jurídico (contrato fora da esteira)."""
    entradas = {}
    for r in con.execute("select conversa_id, criado_em from wa_board_hist where bloqueado=0 and para_pipeline=18 and "
                         "para_etapa like '%istribui%'"):
        quando = _br(r[1])
        if quando:
            entradas.setdefault(str(r[0]), []).append(quando.date().isoformat())
    man = list(manuais or [])
    for c in contratos:
        quando = min([d for d in entradas.get(str(c.get('card_id') or c['cid']), []) if d >= c['pedido']] or [None])
        if c['perdido']:
            c['liberado'], c['liberado_em'] = False, None
        elif quando:
            c['liberado'], c['liberado_em'] = True, quando
        elif c['cnj']:
            c['liberado'], c['liberado_em'] = True, c['distribuido_em']
        else:
            c['liberado'], c['liberado_em'] = False, None
        # ---------- por ACAO: cada processo do cliente pontua quando libera
        acoes = c.get('acoes') or []
        for a in acoes:
            a['liberado'], a['liberado_em'], a['motivo'] = False, None, 'aguardando distribuição'
            if c['perdido']:
                a['motivo'] = 'fora da comissão'
                continue
            manual = next((m for m in man if _casa_manual(m, c, a)), None)
            if a.get('cnj'):
                a['liberado'] = True
                a['liberado_em'] = (a.get('dist') or '')[:10] or None
                a['motivo'] = 'distribuída'
            elif a.get('protocolo_em'):
                a['liberado'], a['liberado_em'] = True, a['protocolo_em']
                a['motivo'] = 'encaminhada para protocolo'
            elif manual:
                a['liberado'] = True
                a['liberado_em'] = str(manual.get('liberado_em') or '')[:10] or None
                a['motivo'] = manual.get('motivo') or 'liberada pela Advogada'
        if acoes and not c['perdido']:
            if c['liberado'] and not any(a['liberado'] for a in acoes):
                acoes[0]['liberado'], acoes[0]['liberado_em'] = True, c['liberado_em']
                acoes[0]['motivo'] = 'card em Distribuição'
            libs = [a for a in acoes if a['liberado']]
            c['acoes_liberadas'] = len(libs)
            if libs and not c['liberado']:
                datas = [a['liberado_em'] for a in libs if a['liberado_em']]
                c['liberado'] = True
                c['liberado_em'] = min(datas) if datas else None
        else:
            c['acoes_liberadas'] = 1 if c['liberado'] else 0


def _fora_da_esteira(con, base, processos, esteira, hoje):
    """Contratos fechados FORA da esteira: processo de reclamante aberto no sistema jurídico desde o inicio da
    serie, de pessoa fisica que nao passou pela esteira. Conta uma vez por cliente: data do 1o processo do periodo, numero
    CNJ do que tiver. Excecoes em base['fora_esteira_excluir'] (ex.: a parte contraria de um acordo, execucao de cliente
    antigo). Dona do card e origem vem da conversa do CRM com o mesmo nome."""
    excluir = {' '.join(_norm(x[0] if isinstance(x, list) else x)) for x in base.get('fora_esteira_excluir', [])}
    ja = {' '.join(_norm((c['advbox'] or {}).get('cliente'))) for c in esteira if c.get('advbox')}
    ja |= {' '.join(_norm(_nome_limpo(c))[:2]) for c in esteira}
    grupos = {}
    for p in processos:
        if p['type'] not in TIPOS_RECLAMANTE or p['criado'] < str(INICIO):
            continue
        for cli in p['clientes']:
            nn = ' '.join(_norm(cli))
            if (not nn or EMPRESA.search(cli or '') or PLACEHOLDER.search(' '.join(_norm(cli))) or nn in excluir
                    or nn in ja or ' '.join(nn.split()[:2]) in ja):
                continue
            grupos.setdefault(nn, {'nome': cli, 'procs': []})['procs'].append(p)
    if not grupos:
        return []
    cards = {}
    for r in con.execute("select id, contato_nome, atendente_email, origem, criado_em from wa_conversas"):
        t = _norm(r['contato_nome'])
        if len(t) >= 2:
            cards.setdefault(' '.join(t[:2]), r)
    estreia = base.get('estreia_v1', str(INICIO))
    out = []
    for nn, g in grupos.items():
        procs = sorted(g['procs'], key=lambda p: p['criado'])
        com_cnj = [p for p in procs if p['cnj']]
        pedido = procs[0]['criado'][:10]
        card = cards.get(' '.join(nn.split()[:2]))
        dona = _quem(card['atendente_email']) if card and card['atendente_email'] else ''
        vend = dona if dona in VENDEDORAS and not (dona == V1 and pedido < estreia) else None
        criado = _br(card['criado_em']) if card else None
        dpedido = date.fromisoformat(pedido)
        ref = com_cnj[0] if com_cnj else procs[0]
        out.append({'cid': 'advbox:' + nn, 'pedido': pedido, 'data': dpedido, 'perdido': False,
                    'nome_wa': '', 'nome_contrato': g['nome'], 'cliente': _nome(re.sub(r'\bLEAD\b', '', g['nome'], flags=re.I)),
                    'vendedora': vend, 'quem': (dona or 'sem card') + ' · fora da esteira',
                    'fase': 'dist' if com_cnj else 'outro',
                    'etapa': ('Distribuído' if com_cnj else (procs[0]['etapa'] or 'no sistema jurídico').capitalize()) + ' (fora da esteira)',
                    'origem': (ANUNCIO.get(card['origem'], card['origem']) if card and card['origem'] else 'Fora da esteira'),
                    'da_base': bool(criado and criado.date() < dpedido - timedelta(days=7)), 'dias': (hoje - dpedido).days,
                    'tem_card': card is not None, 'fora_esteira': True, 'card_id': card['id'] if card else None,
                    'advbox_fixo': {'cliente': g['nome'], 'cnj': ref['cnj'], 'dist': ref['dist'], 'etapa': ref['etapa']}})
    return out


def _perda_distribuicao(contratos, hoje, dias=30):
    """Do fechamento a distribuicao: dos contratos fechados ha mais de `dias` dias (safra madura),
    quantos viraram processo distribuido, quantos desistiram ou se perderam e quantos seguem parados. Duplicata nao
    conta. A desistencia vira a margem da previsibilidade: para a meta virar processo, e preciso fechar mais."""
    maduros = [c for c in contratos if c['pedido'] >= str(INICIO) and c['data'] <= hoje - timedelta(days=dias)
               and not c.get('duplicado')]
    n = len(maduros)
    dist = [c for c in maduros if c['liberado']]
    perd = [c for c in maduros if c['perdido']]
    par = [c for c in maduros if c not in dist and c not in perd]
    sit = lambda c: 'chegou à distribuição' if c in dist else ('desistiu ou se perdeu' if c in perd else 'parado: ' + c['etapa'])
    return {'n': n, 'distribuido': len(dist) / n if n else 0.0, 'desistiu': len(perd) / n if n else 0.0,
            'parado': len(par) / n if n else 0.0, 'dias': dias,
            'linhas': [[dm(c['data']), c['cliente'], c['quem'], sit(c)] for c in sorted(maduros, key=lambda c: c['pedido'])]}


def _comissao(c, P, txt='a liberar quando o card entrar em Distribuição'):
    """(valor, situacao, mes_da_comissao) do contrato: card colocado em Distribuicao."""
    if not c['vendedora'] or c['perdido']:
        return 0.0, 'sem comissão da mesa', None
    taxa = float(P.get(TAXA_CONTRATO[c['vendedora']], 0) or 0)
    if c['liberado']:
        n = max(1, int(c.get('acoes_liberadas') or 1))
        return taxa * n, 'liberada' if n == 1 else 'liberada (%d ações)' % n, (c['liberado_em'] or '')[:7] or None
    return 0.0, txt, None


# ------------------------------------------------------------------ CRM: ligacoes, contatos, leads, perdas
def _ligacoes(con, ini, fim):
    """Ligacoes pela DAPI: 'iniciada por X para +55..' e depois 'encerrada (m:ss de conversa..)' ou '(nao atendida..)'."""
    abertas, out = {}, []
    q = ("select m.conversa_id cid, m.criado_em t, coalesce(m.conteudo,'') txt, v.contato_nome nome, v.etapa etapa "
         "from wa_mensagens m left join wa_conversas v on v.id=m.conversa_id where m.autor='ligacao' "
         "and replace(m.criado_em,'T',' ') >= ? and replace(m.criado_em,'T',' ') < ? order by m.id")
    for r in con.execute(q, (_utc(ini), _utc(fim + timedelta(days=1)))):
        m = re.search(r'iniciada por (.+?) para', r['txt'])
        if m:
            ch = {'cid': r['cid'], 'quando': _br(r['t']), 'quem': _quem(m.group(1)), 'lead': _nome(r['nome']),
                  'etapa': r['etapa'] or '', 'status': 'sem desfecho', 'dur': ''}
            abertas[r['cid']] = ch
            out.append(ch)
        elif 'encerrada' in r['txt'] and r['cid'] in abertas:
            ch = abertas.pop(r['cid'])
            if 'nao atendida' in ' '.join(_norm(r['txt'])):
                ch['status'] = 'não atendeu'
            else:
                d = re.search(r'(\d+:\d+) de conversa', r['txt'])
                ch['status'], ch['dur'] = 'atendeu', (d.group(1) if d else '')
    return out


def _whatsapp(con, ini, fim):
    """Leads que cada vendedora contatou pelo WhatsApp do CRM no periodo."""
    out = []
    for v in VENDEDORAS:
        q = ("select m.conversa_id cid, v.contato_nome nome, v.etapa etapa, count(*) n, min(m.criado_em) t1, "
             "max(m.criado_em) t2 from wa_mensagens m join wa_conversas v on v.id=m.conversa_id where m.autor like ? "
             "and replace(m.criado_em,'T',' ') >= ? and replace(m.criado_em,'T',' ') < ? group by m.conversa_id")
        for r in con.execute(q, (v.lower() + '%', _utc(ini), _utc(fim + timedelta(days=1)))):
            resp = con.execute("select count(*) n from wa_mensagens where conversa_id=? and origem='contato' "
                               "and replace(criado_em,'T',' ') >= ?", (r['cid'], str(r['t1']).replace('T', ' '))
                               ).fetchone()['n']
            out.append({'cid': r['cid'], 'quem': v, 'lead': _nome(r['nome']), 'etapa': r['etapa'] or '',
                        'n': r['n'], 'quando': _br(r['t1']), 'respondeu': resp > 0})
    return out


def _situacoes(con, ids):
    """Situacao do lead: 'Já saiu' se o termometro diz saiu OU a conversa tem as expressoes do fluxo
    "voce ja saiu?" (o termometro troca a situacao por 'fechou'/'contatado' quando o lead avanca e o dado se perde);
    'Trabalha' se o termometro diz trabalhando e nao ha sinal de saida; senao 'Não disse'."""
    ids = list(ids)
    term = {r['conversa_id']: r['situacao'] or '' for r in con.execute('select conversa_id, situacao from lead_termometro')}
    falas = {}
    for i in range(0, len(ids), 500):
        lote = ids[i:i + 500]
        q = ("select conversa_id, conteudo from wa_mensagens where origem='contato' and conversa_id in (%s)"
             % ','.join('?' * len(lote)))
        for r in con.execute(q, lote):
            falas.setdefault(r['conversa_id'], []).append({'role': 'user', 'content': r['conteudo'] or ''})
    out = {}
    for cid in ids:
        t = term.get(cid, '')
        saiu = t == 'saiu'
        if not saiu:
            try:
                saiu = coluna_lead.saiu_no_papo(falas.get(cid, []))
            except Exception:
                saiu = False
        out[cid] = 'Já saiu' if saiu else ('Trabalha' if t == 'trabalhando' else 'Não disse')
    return out


def _leads(con, ini, fim, pipes=PIPES_ESC, sitfn=None):
    q = ("select id, contato_nome, criado_em, e_reclamante, coalesce(tag,'') tag, coalesce(origem,'') origem, "
         "etapa, pipeline_id, coalesce(atendente_email,'') dona from wa_conversas where pipeline_id in (%s) "
         "and replace(criado_em,'T',' ') >= ? and replace(criado_em,'T',' ') < ?" % ','.join(map(str, pipes)))
    linhas = list(con.execute(q, (_utc(ini), _utc(fim + timedelta(days=1)))))
    sit = (sitfn or _situacoes)(con, [r['id'] for r in linhas])
    out = []
    for r in linhas:
        out.append({'cid': r['id'], 'lead': _nome(r['contato_nome']), 'quando': _br(r['criado_em']),
                    'reclamante': bool(r['e_reclamante']) or 'RECLAMANTE' in r['tag'].upper(),
                    'anuncio': r['origem'].startswith('Campanha'),
                    'origem': ANUNCIO.get(r['origem'], r['origem']) if r['origem'] else 'Orgânico/sem origem',
                    'situacao': sit.get(r['id'], 'Não disse'),
                    'etapa': r['etapa'] or '', 'dona': _quem(r['dona']) if r['dona'] else ''})
    return out


def _perdas(con, ini, fim):
    q = ("select h.conversa_id cid, h.para_etapa etapa, h.quem quem, h.criado_em t, v.contato_nome nome "
         "from wa_board_hist h left join wa_conversas v on v.id=h.conversa_id where h.bloqueado=0 "
         "and (h.para_etapa like 'Perdido%' or h.para_etapa in ('Lead Desqualificado', 'Desqualificado')) "
         "and replace(h.criado_em,'T',' ') >= ? and replace(h.criado_em,'T',' ') < ? order by h.id")
    ult = {}
    for r in con.execute(q, (_utc(ini), _utc(fim + timedelta(days=1)))):
        ult[r['cid']] = {'lead': _nome(r['nome']), 'motivo': PERDAS.get(r['etapa'], r['etapa']),
                         'quem': _quem(r['quem']) if '@' in str(r['quem'] or '') else 'automação',
                         'quando': _br(r['t'])}
    return list(ult.values())


def _conversao(con, contratos, hoje, produto='trabalhista'):
    """Conversao por produto, anuncio e situacao (mesma regra do painel das 18h)."""
    fechou = {int(c['cid']): c['pedido'] for c in contratos if not c['perdido'] and c['cid'].isdigit()}
    fechou.update({c['card_id']: c['pedido'] for c in contratos if c.get('fora_esteira') and c.get('card_id') and not c['perdido']})
    ini_mes = hoje.replace(day=1)
    g = {}
    q = ("select id, criado_em, e_reclamante, coalesce(tag,'') tag, coalesce(origem,'') origem from wa_conversas "
         "where pipeline_id in (%s) and replace(criado_em,'T',' ') >= ?"
         % ','.join(map(str, PIPES_ESC)))
    linhas = list(con.execute(q, (_utc(INICIO),)))
    sit = _situacoes(con, [r['id'] for r in linhas])
    for r in linhas:
        dia = _br(r['criado_em'])
        if not dia:
            continue
        mot = bool(r['e_reclamante']) or 'RECLAMANTE' in r['tag'].upper()
        chaves = [('produto', 'Reclamante' if mot else 'Outros assuntos'),
                  ('anuncio', ANUNCIO.get(r['origem'], r['origem']) if r['origem'] else 'Orgânico/sem origem')]
        if mot:
            chaves.append(('situacao', sit.get(r['id'], 'Não disse')))
        for jan in ('desde', 'mes'):
            if jan == 'mes' and dia.date() < ini_mes:
                continue
            for dim, nome in chaves:
                x = g.setdefault((dim, nome), {'desde': [0, 0], 'mes': [0, 0]})[jan]
                x[0] += 1
                x[1] += 1 if r['id'] in fechou else 0
    out = {'produto': [], 'anuncio': [], 'situacao': []}
    for (dim, nome), x in sorted(g.items(), key=lambda i: -i[1]['desde'][0]):
        out[dim].append({'nome': nome, 'leads': x['desde'][0], 'contratos': x['desde'][1],
                         'pct': x['desde'][1] / x['desde'][0] if x['desde'][0] else None,
                         'leads_mes': x['mes'][0], 'contratos_mes': x['mes'][1],
                         'pct_mes': x['mes'][1] / x['mes'][0] if x['mes'][0] else None})
    return out


def _primeira_ligacao(con, leads, alvo='Já saiu'):
    """Minutos entre o lead chegar e a 1a ligacao pela DAPI, para quem ja saiu da empresa."""
    out = []
    for l in leads:
        if l['situacao'] != alvo or not l['quando']:
            continue
        r = con.execute("select min(criado_em) t from wa_mensagens where conversa_id=? and autor='ligacao' "
                        "and conteudo like '%iniciada por%'", (l['cid'],)).fetchone()
        t = _br(r['t']) if r and r['t'] else None
        out.append({'lead': l['lead'], 'chegou': l['quando'],
                    'min': max(0, int((t - l['quando']).total_seconds() // 60)) if t else None})
    return out


# ------------------------------------------------------------------ provisao
def _fechamento_2026(transacoes, hoje, P):
    ini_ano, fim_ano = date(hoje.year, 1, 1), date(hoje.year, 12, 31)
    it = {'rec': [], 'fixo': [], 'exito': [], 'atras': [], 'avulso': []}
    for t in transacoes:
        if t['pg']:
            if t['pg'][:4] == str(hoje.year):
                it['rec'].append(t)
                if t['cat'] in AVULSO:
                    it['avulso'].append(t)
        elif t['vc']:
            dvc = date.fromisoformat(t['vc'])
            if hoje <= dvc <= fim_ano:
                it['exito' if t['cat'] in EXITO else 'fixo'].append(t)
            elif ini_ano <= dvc < hoje:
                it['atras'].append(t)
    soma = {k: sum(t['v'] for t in L) for k, L in it.items()}
    decorridos, restantes = (hoje - ini_ano).days + 1, (fim_ano - hoje).days
    avulso = soma['avulso'] / decorridos * restantes
    tl, ta = float(P.get('taxa_lanc', 0.9)), float(P.get('taxa_atraso', 0.5))
    linhas = [('rec', 'Recebido em %d até agora' % hoje.year, soma['rec'], 1.0),
              ('fixo', 'Lançado a receber até 31/12: mensalidades, consultorias e TMP', soma['fixo'], tl),
              ('exito', 'Lançado a receber até 31/12: êxito (parcelas de acordo e execução)', soma['exito'], tl),
              ('atras', 'Atrasados de %d (venceram e não foram pagos)' % hoje.year, soma['atras'], ta),
              ('avulso', 'Iniciais e audiências até 31/12, no ritmo do ano', avulso, 1.0)]
    return {'linhas': [{'k': k, 'item': n, 'valor': v, 'parte': p, 'entra': v * p} for k, n, v, p in linhas],
            'itens': it, 'total': sum(v * p for _, _, v, p in linhas)}


def _data_br(iso):
    """'2026-10-01' vira '01/10'. Data vazia ou estranha nao derruba a tabela."""
    try:
        return dm(date.fromisoformat(str(iso)[:10]))
    except Exception:
        return '·'


def _linhas_comissao(contratos, taxa, P, txt='a liberar quando o card entrar em Distribuição'):
    """Uma linha por ACAO, com a empresa e o motivo da liberacao. Contrato sem processo no sistema jurídico
    continua com uma linha so, como antes."""
    out = []
    for c in reversed(contratos):
        acoes = c.get('acoes') or []
        if not acoes:
            valor, sit, _ = _comissao(c, P, txt)
            out.append([dm(c['data']), c['cliente'], c['cnj'] or '·',
                        _data_br(c['liberado_em']) if c['liberado_em'] else ('data a lançar' if c['liberado'] else '·'),
                        brl(valor) if c['liberado'] else brl(taxa) + ' a liberar',
                        sit if c['liberado'] else txt])
            continue
        for a in acoes:
            alvo = ' · '.join(x for x in (a.get('empresa'), a.get('cnj')) if x) or '·'
            out.append([dm(c['data']), c['cliente'], alvo,
                        _data_br(a.get('liberado_em')) if a.get('liberado_em') else ('data a lançar' if a.get('liberado') else '·'),
                        brl(taxa) if a.get('liberado') else brl(taxa) + ' a liberar',
                        a.get('motivo') or ''])
    return out


def _contratos_por_mes(contratos):
    n = {}
    for c in contratos:
        if not c['perdido']:
            n[c['pedido'][:7]] = n.get(c['pedido'][:7], 0) + 1
    return n


def _provisao(base, fech, contratos, hoje):
    P = base['premissas']
    g = lambda k, d=0: float(P.get(k, d) or 0)
    piso = int(g('ano_piso', 2027))
    valor = g('ticket') * g('pct_dist') * g('taxa_exito')
    fat = ((6, g('p6')), (12, g('p12')), (24, g('p24')), (36, g('p36')))
    real = _contratos_por_mes(contratos)
    mes_atual = hoje.strftime('%Y-%m')
    meses, d = [], INICIO
    while d <= date(2028, 12, 1):
        m = d.strftime('%Y-%m')
        meta = _meta_mes(base, m)['meta'] if m >= '2026-10' else None
        r = real.get(m, 0) if m <= mes_atual else None
        if m < mes_atual:
            usado, como = r, 'realizado'
        elif m == mes_atual:
            usado, como = max(r or 0, meta or 0), 'maior entre realizado e meta'
        else:
            usado, como = meta or 0, 'meta'
        anos = [(max((_add_meses(d, k)).year, piso), p) for k, p in fat]
        meses.append({'mes': m, 'real': r, 'meta': meta, 'usado': usado, 'como': como, 'valor': usado * valor,
                      'anos': anos})
        d = _add_meses(d, 1)
    tab = []
    for ano in (2026, 2027, 2028, 2029):
        b = (base.get('anos') or {}).get(str(ano), {})
        if ano == 2026:
            if fech:
                L = {x['k']: x['entra'] for x in fech['linhas']}
                col = {'C': L['rec'], 'D': L['fixo'] + L['atras'] + L['avulso'], 'E': 0, 'F': 0, 'G': 0,
                       'H': L['exito'], 'I': 0, 'J': 0}
            else:
                col = {k: b.get(k, 0) for k in 'CDEFGHIJ'}
        else:
            col = {k: b.get(k, 0) for k in 'CDEFGHI'}
            col['J'] = sum(m['valor'] * p for m in meses for a, p in m['anos'] if a == ano)
        total = sum(col.values())
        tab.append({'ano': ano, **col, 'K': total, 'L': g('meta_fat', 1500000),
                    'M': total / g('meta_fat', 1500000) if g('meta_fat', 1500000) else 0})
    return {'anos': tab, 'meses': meses, 'valor_contrato': valor}


# ------------------------------------------------------------------ custo por contrato
def _custo_periodo(base, ini, fim, contratos, meta_dias, hoje):
    """Custo do comercial num periodo qualquer, dia a dia: cada custo mensal entra pela fracao dos dias (mes de 30 dias
    = 1/30 por dia), a midia e o gasto real do Meta nos dias e a comissao e a parte que distribui dos contratos do
    periodo. Mesmos itens do custo por contrato do mes (mes fechado da o mesmo numero)."""
    P = base['premissas']
    g = lambda k, d=0: float(P.get(k, d) or 0)
    estreia = date.fromisoformat(base.get('estreia_v1', str(INICIO)))
    fim = min(fim, hoje)
    itens = {}

    def soma(nome, v):
        itens[nome] = itens.get(nome, 0.0) + v
    d = ini
    while d <= fim:
        mes = d.strftime('%Y-%m')
        dm_ = _fim_mes(d).day
        soma('Mídia no Meta Ads', (meta_dias.get(str(d)) or [0, 0])[0])
        for x in (base.get('custos_extra') or {}).get(mes, []):
            soma(x[0], x[1] / dm_)
        conteudo = float(((base.get('custos_mes') or {}).get(mes) or {}).get('conteudo', g('conteudo')))
        soma('Conteúdo: mídias sociais (vídeos)', conteudo / dm_)
        if d >= estreia:
            soma(V1 + ': salário, encargos e ajuda de custo', (g('sal_v1') * (1 + g('encargos')) + g('ajuda_v1')) / dm_)
        soma(V2 + ': remuneração', g('custo_v2') / dm_)
        soma('Ferramentas (WhatsApp, ZapSign, IA, servidor)',
             (g('telefonia') + g('zapsign') + g('api_ia') + g('vps') * g('vps_parte')) / dm_)
        d += timedelta(days=1)
    per = [c for c in contratos if not c['perdido'] and ini <= c['data'] <= fim]
    com = sum(g(TAXA_CONTRATO[c['vendedora']]) for c in per if c['vendedora']) * g('pct_dist')
    soma('Comissões por contrato (parte que distribui)', com)
    total = sum(itens.values())
    return {'itens': [[k, v] for k, v in itens.items() if v], 'total': total, 'contratos': len(per),
            'custo': total / len(per) if per else None}


def _custo_mes(base, mes, modo, contratos, meta_dias, hoje):
    """Itens de custo do comercial no mes. modo: 'real' (mes fechado), 'hoje' (mes corrente ate hoje), 'meta'."""
    P = base['premissas']
    g = lambda k, d=0: float(P.get(k, d) or 0)
    d0 = date(int(mes[:4]), int(mes[5:7]), 1)
    d1 = _fim_mes(d0)
    ate = min(d1, hoje) if modo == 'hoje' else d1
    dias_mes = d1.day
    frac = 1.0 if modo != 'hoje' else ate.day / dias_mes
    estreia = date.fromisoformat(base.get('estreia_v1', str(INICIO)))
    ini_raf = max(d0, estreia)
    frac_raf = 1.0 if modo == 'meta' else max(0, (ate - ini_raf).days + 1) / dias_mes
    mm = _meta_mes(base, mes)
    if modo == 'meta':
        midia = mm['verba']
        n_raf, n_mar = mm[V1], mm[V2]
        com_contr = (n_raf * g('com_v1') + n_mar * g('com_v2')) * g('pct_dist')
        n = mm['meta']
    else:
        midia = sum(v[0] for k, v in meta_dias.items() if k[:7] == mes and k <= str(ate))
        do_mes = [c for c in contratos if c['pedido'][:7] == mes and c['pedido'] <= str(ate) and not c['perdido']]
        n_raf = sum(1 for c in do_mes if c['vendedora'] == V1)
        n_mar = sum(1 for c in do_mes if c['vendedora'] == V2)
        com_contr = (n_raf * g('com_v1') + n_mar * g('com_v2')) * g('pct_dist')
        n = len(do_mes)
    itens = [('Mídia no Meta Ads', midia, 'Meta Ads' if modo != 'meta' else 'verba do mês (aba METAS)')]
    if modo != 'meta':
        itens += [(x[0], x[1], x[2]) for x in (base.get('custos_extra') or {}).get(mes, [])]
    conteudo = float(((base.get('custos_mes') or {}).get(mes) or {}).get('conteudo', g('conteudo')))
    itens += [('Conteúdo: mídias sociais do escritório (vídeos)', conteudo * frac, 'premissas'),
              (V1 + ': salário, encargos e ajuda de custo',
               (g('sal_v1') * (1 + g('encargos')) + g('ajuda_v1')) * frac_raf,
               'encargos estimados em %s' % pct(g('encargos'))),
              (V2 + ': remuneração', g('custo_v2') * frac, 'premissas'),
              (('Comissões por contrato distribuído (' + V1 + ' %s, ' + V2 + ' %s)') % (brl(g('com_v1')), brl(g('com_v2'))), com_contr,
               'parte que distribui (%s)' % pct(g('pct_dist'))),
              ('Número central do WhatsApp', g('telefonia') * frac, 'sistema jurídico'),
              ('ZapSign', g('zapsign') * frac, 'sistema jurídico'),
              ('IA do atendimento (API)', g('api_ia') * frac, 'sistema jurídico'),
              ('Servidor (parte do comercial)', g('vps') * g('vps_parte') * frac, 'metade atribuída ao comercial')]
    total = sum(x[1] for x in itens)
    esperado = g('ticket') * g('pct_dist') * g('taxa_exito')
    return {'mes': mes, 'modo': modo, 'itens': itens, 'total': total, 'contratos': n,
            'custo': total / n if n else None, 'esperado': esperado,
            'razao': (total / n) / esperado if n and esperado else None}


# ------------------------------------------------------------------ montagem do painel
def _fluxo_contratacao(hoje, D, semanas=4):
    """Do "Fechou" ate a Producao, pelo historico do quadro (comeca em 04/09). Para cada card que
    entrou em "Fechou": quando chegou na Producao (board 18) e o desfecho. Perdido = card em Perdido, perdido na
    esteira ou de volta ao board de Leads sem ter chegado na Producao. Por semana em que fechou: quantos, quantos ja
    chegaram, tempo medio ate chegar (so de quem chegou), perdidos e quem segue na contratacao."""
    est = _json(ESTADO_CONTRATACAO, {}).get('conversas') or {}
    con = sqlite3.connect('file:%s?mode=ro' % BANCO, uri=True, timeout=5)
    try:
        H = {}
        for cid, p, e, t in con.execute("select conversa_id, para_pipeline, coalesce(para_etapa,''), criado_em "
                                        "from wa_board_hist where bloqueado=0"):
            q = _br(t)
            if q:
                H.setdefault(cid, []).append((p, e, q))
        atual = {r[0]: (r[1], r[2] or '', r[3] or '') for r in
                 con.execute("select id, pipeline_id, etapa, contato_nome from wa_conversas")}
    finally:
        con.close()
    L = []
    for cid, ev in H.items():
        f = [q for p, e, q in ev if e.startswith('Fechou')]
        if not f:
            continue
        tf = min(f)
        tp = min([q for p, e, q in ev if p == 18 and q >= tf] or [None])
        pa, ea, nome = atual.get(cid, (None, '', ''))
        s = est.get(str(cid)) or {}
        if s.get('perdido_em') or 'erdido' in ea or (not tp and pa == 10):
            sit = 'perdido'
        elif tp:
            sit = 'producao'
        elif pa == 20:
            sit = 'contratacao'
        else:
            sit = 'outro'
        L.append({'fechou': tf, 'producao': tp, 'sit': sit, 'etapa': ea,
                  'cliente': _nome(s.get('nome_contrato') or s.get('nome') or nome),
                  'dias': round((tp - tf).total_seconds() / 86400, 1) if tp else None,
                  'parado': (hoje - tf.date()).days if sit == 'contratacao' else None})
    return _fluxo_agrega(L, hoje, D, semanas)


def montar(periodo='semana', forcar=False, produto='trabalhista'):
    agora = datetime.now()
    hoje = agora.date()
    base = _base()
    P = base['premissas']
    g = lambda k, d=0: float(P.get(k, d) or 0)
    fontes = _fontes(forcar)
    adv = fontes.get('advbox') or {}
    meta_dias = fontes.get('meta') or {}
    produto = 'trabalhista'
    V = VOC[produto]
    S = V['sit']
    ini, fim, rotulo = _periodo(periodo, hoje)
    du_per = max(1, _dias_uteis(ini, min(fim, hoje)))
    D = {}                                    # listas de detalhe: chave -> {tit, sub, cols, rows, nota}

    con = _con()
    try:
        do_produto = lambda x: True
        pipes = PIPES_ESC
        contratos = _contratos(con, base, adv.get('processos') or [], hoje)
        leads = _leads(con, ini, fim)
        lig = [x for x in _ligacoes(con, ini, fim) if do_produto(x)]
        zap = [x for x in _whatsapp(con, ini, fim) if do_produto(x)]
        perdas = _perdas(con, ini, fim)
        conversao = _conversao(con, contratos, hoje, produto)
        prim = _primeira_ligacao(con, leads, S[0])
        # previsibilidade: janela movel dos ultimos 14 dias ate hoje e as ligacoes do mes corrente
        ini14 = hoje - timedelta(days=13)
        lig14 = [x for x in _ligacoes(con, ini14, hoje) if do_produto(x)]
        lig_mes_todas = [x for x in _ligacoes(con, hoje.replace(day=1), hoje) if do_produto(x)]
        # rateio: a mesa e as ferramentas se dividem pela proporcao de ligacoes de cada produto
        share = {}
        for x in _ligacoes(con, INICIO, hoje):
            if x['quem'] in VENDEDORAS and x['quando']:
                t = share.setdefault(x['quando'].strftime('%Y-%m'), [0, 0])
                t[1] += 1
                t[0] += 1 if do_produto(x) else 0
        fila = {}
        q14 = ("select count(*) n, sum(case when coalesce(origem,'') like 'Campanha%%' then 1 else 0 end) a "
               "from wa_conversas where pipeline_id in (%s) and replace(criado_em,'T',' ') >= ? "
               "and replace(criado_em,'T',' ') < ?" % ','.join(map(str, pipes)))
        r14 = con.execute(q14, (_utc(ini14), _utc(hoje + timedelta(days=1)))).fetchone()
        leads14, anuncio14 = r14['n'] or 0, r14['a'] or 0
    finally:
        con.close()
    ini_mes = hoje.replace(day=1)
    bp = _base_produto(base, produto)                # metas, ticket e premissas do produto
    P = bp['premissas']                              # g() passa a ler as premissas do produto

    def fator(mk):
        t = share.get(mk) or share.get(hoje.strftime('%Y-%m')) or [0, 0]
        return (t[0] / t[1]) if t[1] else 1.0

    # ---------- contratos do periodo
    validos = [c for c in contratos if not c['perdido']]
    per = [c for c in validos if ini <= c['data'] <= fim]
    valor = g('ticket') * g('pct_dist') * g('taxa_exito')

    def linha_contr(c):
        return [dm(c['data']), c['cliente'], c['quem'], c['etapa'], c['origem'] + (' · base' if c['da_base'] else '')]
    cols_c = ['Data', 'Cliente', 'Quem fechou', 'Etapa hoje', 'Origem']
    D['contratos'] = {'tit': '%s · %s (%d)' % (V['tit_contratos'], rotulo.lower(), len(per)),
                      'sub': 'Entrada no fluxo de contratação, sem os perdidos. "base" = lead que já estava no CRM há mais de 7 dias.',
                      'cols': cols_c, 'rows': [linha_contr(c) for c in reversed(per)]}
    D['carteira'] = {'tit': 'Carteira contratada · %s' % rotulo.lower(),
                     'sub': '%s × %s esperados por contrato (ticket %s × %s que distribui × %s de êxito).'
                            % (len(per), brl(valor), brl(g('ticket')), pct(g('pct_dist')), pct(g('taxa_exito'))),
                     'cols': ['Data', 'Cliente', 'Valor esperado'],
                     'rows': [[dm(c['data']), c['cliente'], brl(valor)] for c in reversed(per)]}

    # ---------- metas: mes corrente e periodo
    mes = hoje.strftime('%Y-%m')
    mm = _meta_mes(bp, mes)
    du_mes = _dias_uteis(ini_mes, _fim_mes(hoje))
    du_passados = _dias_uteis(ini_mes, hoje)
    do_mes = [c for c in validos if c['pedido'][:7] == mes]
    ritmo = len(do_mes) / du_passados * du_mes if du_passados else 0
    mm_per = _meta_mes(bp, max(min(fim, hoje).strftime('%Y-%m'), min((m['mes'] for m in bp['metas']), default=mes)))
    quem_meta = []
    for nome, chave in (('Escritório', 'meta'), (V1, V1), (V2, V2)):
        alvo_mes = mm_per[chave]
        alvo = _meta_periodo(bp, ini, min(fim, hoje), chave)
        feitos = len(per) if nome == 'Escritório' else sum(1 for c in per if c['vendedora'] == nome)
        feitos_mes = len(do_mes) if nome == 'Escritório' else sum(1 for c in do_mes if c['vendedora'] == nome)
        proj = feitos_mes / du_passados * du_mes if du_passados else 0
        quem_meta.append({'quem': nome, 'meta_mes': alvo_mes, 'meta_per': round(alvo, 1), 'feitos': feitos,
                          'pct': feitos / alvo if alvo else None,
                          'mes_meta': mm[chave], 'mes_feitos': feitos_mes, 'mes_proj': round(proj, 1),
                          'mes_pct': proj / mm[chave] if mm[chave] else None})
    D['ritmo'] = {'tit': 'Ritmo de %s: %d %s até hoje' % (mes_rot(mes), len(do_mes), V['contratos']),
                  'sub': '%d dias úteis passados de %d. No ritmo atual o mês fecha com ~%d (meta %d).'
                         % (du_passados, du_mes, round(ritmo), mm['meta']),
                  'cols': cols_c, 'rows': [linha_contr(c) for c in reversed(do_mes)]}

    # ---------- previsibilidade: o que for necessario para bater a meta, pelas ultimas 2 semanas
    # e pela perda entre fechar e distribuir. Uma coluna por meta real (escritorio e cada vendedor), sem degraus
    # abaixo ("meta decrescente nao faz sentido"). Janela movel de 14 dias, recalculada a cada acesso.
    c14 = [c for c in validos if ini14 <= c['data'] <= hoje]
    perda = _perda_distribuicao(contratos, hoje)
    margem = 1 - perda['desistiu'] if perda['n'] and perda['desistiu'] < 0.9 else 1.0
    gasto14 = sum(v[0] for k, v in meta_dias.items() if str(ini14) <= k <= str(hoje))
    gasto30 = sum(v[0] for k, v in meta_dias.items() if str(hoje - timedelta(days=29)) <= k <= str(hoje))
    cpl14 = gasto14 / anuncio14 if anuncio14 else None
    parte_anuncio = anuncio14 / leads14 if leads14 else None
    leads_por = leads14 / len(c14) if c14 else None
    du14 = max(1, _dias_uteis(ini14, hoje))
    hoje_util = hoje.weekday() < 5 and hoje not in FERIADOS
    dias_daqui = max(1, du_mes - du_passados + (1 if hoje_util else 0))
    colunas = []
    for rot, quem, meta_q in (('Escritório', None, mm['meta']), (V1, V1, mm[V1]),
                              (V2, V2, mm[V2])):
        cs = [c for c in c14 if quem is None or c['vendedora'] == quem]
        ls = [x for x in lig14 if quem is None or x['quem'] == quem]
        regua = 'da mesa' if quem is None else 'dela'
        if quem and not cs:                       # sem contrato dela na janela: regua da mesa
            cs, ls, regua = c14, lig14, 'da mesa'
        lig_por = len(ls) / len(cs) if cs else None
        taxa_at = sum(1 for x in ls if x['status'] == 'atendeu') / len(ls) if ls else None
        fechar = int(math.ceil(round(meta_q / margem, 6))) if meta_q else 0    # para meta_q virarem processo
        feitos = len([c for c in do_mes if quem is None or c['vendedora'] == quem])
        faltam = max(0, fechar - feitos)
        lig_mes = [x for x in lig_mes_todas if quem is None or x['quem'] == quem]
        lig_faltam = faltam * lig_por if lig_por else None
        lig_hoje = sum(1 for x in lig_mes if x['quando'] and x['quando'].date() == hoje)   # como na mesa: hoje conta o que ja fez
        col = {'rot': rot, 'meta': meta_q, 'fechar': fechar, 'feitos': feitos, 'faltam': faltam, 'regua': regua,
               'lig_por_contrato': lig_por, 'taxa_atendimento': taxa_at,
               'lig_faltam': round(lig_faltam) if lig_faltam is not None else None,
               'lig_dia': round((lig_faltam + lig_hoje) / dias_daqui, 1) if lig_faltam is not None else None,
               'lig_dia_atual': round(len(lig_mes) / max(1, du_passados), 1),
               'at_dia': round(lig_faltam / dias_daqui * taxa_at, 1) if lig_faltam is not None and taxa_at is not None else None}
        if quem is None and leads_por:
            leads_faltam = faltam * leads_por
            col.update({'leads_dia': round(leads_faltam / dias_daqui, 1), 'leads_dia_atual': round(leads14 / du14, 1),
                        'verba_mes': round(fechar * leads_por * parte_anuncio * cpl14) if parte_anuncio and cpl14 else None,
                        'verba_atual': round(gasto30)})
        colunas.append(col)
    previsao = {'de': dm(ini14), 'ate': dm(hoje), 'dias_daqui': dias_daqui, 'perda': perda, 'margem': margem,
                'leads': leads14, 'leads_anuncio': anuncio14, 'gasto': gasto14, 'cpl': cpl14, 'leads_por_contrato': leads_por,
                'contratos': len(c14), 'ligacoes': len(lig14),
                'atendidas': sum(1 for x in lig14 if x['status'] == 'atendeu'), 'colunas': colunas,
                'mes': {'rotulo': mes_rot(mes), 'meta': mm['meta'], 'feitos': len(do_mes)}}
    D['perda_dist'] = {'tit': 'Do fechamento à distribuição: contratos fechados há mais de 30 dias (%d)' % perda['n'],
                       'sub': 'Distribuídos %s · desistiram ou se perderam %s · ainda parados %s. A previsibilidade usa a '
                              'desistência como margem: para a meta virar processo, é preciso fechar mais.'
                              % (pct(perda['distribuido']), pct(perda['desistiu']), pct(perda['parado'])),
                       'cols': ['Fechou em', 'Cliente', 'Quem', 'Situação'],
                       'rows': perda['linhas']}
    D['previsao'] = {'tit': 'Previsibilidade: o que aconteceu de %s a %s' % (dm(ini14), dm(hoje)),
                     'sub': 'Janela móvel dos últimos 14 dias, recalculada a cada acesso.',
                     'cols': ['Medida', 'Valor'],
                     'rows': [['Ligações pela DAPI (todas)', len(lig14)],
                              ['Atendidas', sum(1 for x in lig14 if x['status'] == 'atendeu')],
                              ['Contratos fechados (todos)', len(c14)],
                              ['Leads novos que chegaram', leads14], ['Leads de anúncio', anuncio14],
                              ['Leads novos por contrato', '·' if not leads_por else '%.1f' % leads_por],
                              ['Gasto no Meta (14 dias)', brl(gasto14)], ['Custo por lead de anúncio', brl(cpl14, 2) if cpl14 else '·'],
                              ['Gasto no Meta (30 dias)', brl(gasto30)]]}

    # ---------- contratos por dia (grafico)
    por_dia, d = [], ini
    while d <= min(fim, hoje):
        doD = [c for c in per if c['data'] == d]
        if d.weekday() < 5 or doD:
            por_dia.append({'dia': '%s %02d' % (DIAS_SEM[d.weekday()], d.day), 'n': len(doD),
                            'quem': ', '.join('%s (%s)' % (c['cliente'].split()[0], c['quem']) for c in doD)})
        d += timedelta(days=1)

    # ---------- custo por contrato e conversao do periodo
    cp = _custo_periodo(_rateio(bp, fator(min(fim, hoje).strftime('%Y-%m'))), ini, fim, contratos, meta_dias, hoje)
    D['custo_periodo'] = {'tit': 'Custo do comercial · %s' % rotulo.lower(),
                          'sub': 'Custos mensais pela fração dos dias do período; mídia pelo gasto real do Meta nos dias.',
                          'cols': ['Item', 'Valor'],
                          'rows': [[i[0], brl(i[1])] for i in cp['itens']] +
                                  [['Total', brl(cp['total'])], ['Contratos fechados', cp['contratos']],
                                   ['Custo por contrato', brl(cp['custo']) if cp['custo'] else '·']]}

    # ---------- leads
    n_leads = len(leads)
    D['conversao_det'] = {'tit': 'Conversão · %s: %d contratos ÷ %d leads novos' % (rotulo.lower(), len(per), n_leads),
                          'sub': 'Abaixo, a conversão por safra: contratos dos leads que chegaram desde 01/08, por produto, '
                                 'anúncio e situação (e no mês).',
                          'cols': ['Recorte', 'Leads', 'Contratos', 'Conversão', 'No mês'],
                          'rows': [[('%s: %s' % (rot, x['nome'])), x['leads'], x['contratos'], pct(x['pct'], 1),
                                    '%d/%d (%s)' % (x['contratos_mes'], x['leads_mes'], pct(x['pct_mes'], 1))]
                                   for rot, dim in (('Produto', 'produto'), ('Anúncio', 'anuncio'), ('Situação', 'situacao'))
                                   for x in conversao[dim]]}
    sit = {k: sum(1 for l in leads if l['situacao'] == k) for k in S}
    disse = sit[S[0]] + sit[S[1]]
    leads_anuncio = sum(1 for l in leads if l['anuncio'])
    gasto_per = sum(v[0] for k, v in meta_dias.items() if str(ini) <= k <= str(fim))
    conv_meta = sum(v[1] for k, v in meta_dias.items() if str(ini) <= k <= str(fim))

    # ---------- retorno projetado (Visao geral) e retorno real (aba Provisao): cada coisa em seu lugar.
    # ROAS = receita / midia do Meta; ROI = (receita - custo do comercial) / custo do comercial, com o mesmo custo do
    # cartao "Custo por contrato". Projetado = contratos fechados no periodo x ticket medio. Real = o que de fato entrou.
    def _ret(receita, midia, custo):
        return {'receita': receita, 'midia': midia, 'custo': custo, 'lucro': receita - custo,
                'roas': receita / midia if midia else None, 'roi': (receita - custo) / custo if custo else None}

    def _vezes(x):
        return '·' if x is None else (('%.1f' % x).replace('.', ',') if x < 10 else brl(x)[3:]) + 'x'

    def _pctm(x):                                    # 3.810% com ponto de milhar
        return '·' if x is None else brl(x * 100)[3:] + '%'
    ret = dict(_ret(len(per) * valor, gasto_per, cp['total']), n=len(per), ticket=valor)
    D['retorno'] = {'tit': 'Retorno projetado pelo ticket médio · %s' % rotulo.lower(),
                    'sub': 'Contratos fechados no período × ticket médio. O que de fato entrou está na aba Provisão.',
                    'cols': ['Item', 'Valor', 'De onde vem'],
                    'rows': [['Receita projetada', brl(ret['receita']),
                              ('%d contrato(s) × %s esperados por contrato (ticket %s × %s que distribui × %s de êxito)'
                               % (len(per), brl(valor), brl(g('ticket')), pct(g('pct_dist')), pct(g('taxa_exito'))))],
                             ['Mídia no Meta Ads', brl(gasto_per), 'gasto real do Meta nos dias do período'],
                             ['Custo do comercial', brl(cp['total']), 'mídia, conteúdo, equipe, comissões e ferramentas (o mesmo do custo por contrato)'],
                             ['ROAS', _vezes(ret['roas']), 'receita dividida pela mídia'],
                             ['ROI', _pctm(ret['roi']), 'receita menos custo, dividido pelo custo']]}

    # real, safra por safra (safra = contratos fechados no mes; investido = custo do comercial daquele mes).
    # Exito PAGO no sistema juridico nos processos da safra, pelo numero do processo, e cada processo conta uma vez so;
    # garantido = exito lancado a receber. Sem integracao do sistema juridico, fica zerado.
    so_dig = lambda s: re.sub(r'\D', '', s or '')
    if True:
        ex_pago, ex_aberto = {}, {}
        for t_ in adv.get('transacoes') or []:
            if t_['cat'] in EXITO and t_['proc']:
                alvo = ex_pago if t_['pg'] else ex_aberto
                alvo[so_dig(t_['proc'])] = alvo.get(so_dig(t_['proc']), 0) + t_['v']
        vistos, primeiro = set(), set()
        for c_ in sorted(validos, key=lambda x_: (x_['pedido'], str(x_['cid']))):
            k_ = so_dig(c_.get('cnj'))
            if k_ and k_ not in vistos:
                vistos.add(k_)
                primeiro.add(c_['cid'])
        unico = lambda c_: c_.get('cnj') and c_['cid'] in primeiro
        entrou_c = lambda c_: ex_pago.get(so_dig(c_['cnj']), 0) if unico(c_) else 0.0
        garant_c = lambda c_: ex_aberto.get(so_dig(c_['cnj']), 0) if unico(c_) else 0.0
        obs_c = lambda c_: ('ainda sem processo' if not c_.get('cnj') else
                            'mesmo processo de outro contrato (conta uma vez)' if not unico(c_) else c_['cnj'])
    linhas_s, mk = [], INICIO.strftime('%Y-%m')
    while mk <= mes:
        cs = [c_ for c_ in validos if c_['pedido'][:7] == mk]
        md_ = sum(v[0] for k, v in meta_dias.items() if k[:7] == mk)
        if cs or md_ > 0:
            fat = fator(mk)                          # rateio por ligacoes
            inv = _custo_mes(_rateio(bp, fat), mk, 'real' if mk < mes else 'hoje', contratos, meta_dias, hoje)['total']
            ent, gar = sum(entrou_c(c_) for c_ in cs), sum(garant_c(c_) for c_ in cs)
            linhas_s.append({'mes': mk, 'rot': mes_rot(mk), 'contratos': len(cs), 'midia': md_, 'investido': inv,
                             'entrou': ent, 'garantido': gar, 'recuperado': ent / inv if inv else None,
                             'roi': (ent - inv) / inv if inv else None})
        mk = _add_meses(date.fromisoformat(mk + '-01'), 1).strftime('%Y-%m')
    tot = {k: sum(x[k] for x in linhas_s) for k in ('contratos', 'midia', 'investido', 'entrou', 'garantido')}
    tot.update(_ret(tot['entrou'], tot['midia'], tot['investido']), recuperado=tot['entrou'] / tot['investido'] if tot['investido'] else None)
    da_serie = [c_ for c_ in validos if linhas_s and c_['pedido'][:7] >= linhas_s[0]['mes']]
    rr = {'safras': linhas_s, 'tot': tot, 'desde': linhas_s[0]['rot'] if linhas_s else None,
          'com_dinheiro': sum(1 for c_ in da_serie if entrou_c(c_) > 0)}
    D['safras'] = {'tit': 'Trabalhista: o que de fato entrou, contrato por contrato (%d)' % len(da_serie),
                   'sub': 'Êxito pago e a receber no sistema jurídico, pelo número do processo; cada processo conta uma vez.',
                   'cols': ['Safra', 'Cliente', 'Já entrou', 'Garantido a receber', 'Observação'],
                   'rows': [[mes_rot(c_['pedido'][:7]), c_['cliente'], brl(entrou_c(c_)), brl(garant_c(c_)), obs_c(c_)]
                            for c_ in reversed(da_serie)]}
    rows_l = [[l['quando'].strftime('%d/%m %H:%M') if l['quando'] else '', l['lead'], l['origem'], l['situacao'],
               l['etapa'], l['dona'] or '·'] for l in sorted(leads, key=lambda x: x['quando'] or agora, reverse=True)]
    cols_l = ['Chegou', 'Lead', 'Origem', 'Situação', 'Etapa', 'Card']
    D['leads'] = {'tit': 'Leads novos · %s (%d)' % (rotulo.lower(), n_leads),
                  'sub': 'Conversas que chegaram nos boards do Escritório Modelo. Situação = o que o lead disse sobre o emprego.',
                  'cols': cols_l, 'rows': rows_l}
    for k, chave in ((S[0], 'leads_saiu'), (S[1], 'leads_trab'), (S[2], 'leads_nd')):
        D[chave] = {'tit': '%s · %d leads' % (k, sit[k]), 'cols': cols_l,
                    'rows': [r for r in rows_l if r[3] == k]}
    D['midia'] = {'tit': 'Mídia · %s' % rotulo.lower(),
                  'sub': 'Gasto do Meta Ads no período e conversas iniciadas no WhatsApp.',
                  'cols': ['Dia', 'Gasto', 'Conversas'],
                  'rows': [[dm(date.fromisoformat(k)), brl(v[0], 2), v[1]] for k, v in sorted(meta_dias.items())
                           if str(ini) <= k <= str(fim)]}

    # ---------- mesa: ligacoes, WhatsApp, contratos e comissao por vendedora
    rows_lig = [[ch['quando'].strftime('%d/%m %H:%M') if ch['quando'] else '', ch['quem'], ch['lead'],
                 ch['status'] + (' · ' + ch['dur'] if ch['dur'] else ''), ch['etapa']] for ch in reversed(lig)]
    cols_lig = ['Quando', 'Quem ligou', 'Lead', 'Resultado', 'Etapa do card']
    D['ligacoes'] = {'tit': 'Ligações pela DAPI · %s (%d)' % (rotulo.lower(), len(lig)),
                     'sub': 'Nota de ligação no card: quem ligou, para quem e se atendeu. Ligação não gera comissão.',
                     'cols': cols_lig, 'rows': rows_lig}
    cols_z = ['Quando', 'Lead', 'Mensagens', 'Respondeu', 'Etapa']
    mesa = []
    for v in VENDEDORAS:
        L = [x for x in lig if x['quem'] == v]
        at = [x for x in L if x['status'] == 'atendeu']
        Z = [x for x in zap if x['quem'] == v]
        C = [c for c in per if c['vendedora'] == v]
        contatados = {x['cid'] for x in L} | {x['cid'] for x in Z}
        alvo = next(q['meta_per'] for q in quem_meta if q['quem'] == v)
        mesa.append({'quem': v, 'ligacoes': len(L), 'por_dia': round(len(L) / du_per, 1), 'atendidas': len(at),
                     'pct_atendidas': len(at) / len(L) if L else None, 'whatsapp': len(Z),
                     'contatados': len(contatados), 'contratos': len(C), 'meta_per': alvo,
                     'pct_meta': len(C) / alvo if alvo else None})
        D['lig_' + v.lower()] = {'tit': 'Ligações da %s · %s (%d, %d atendidas)' % (v, rotulo.lower(), len(L), len(at)),
                                 'cols': cols_lig, 'rows': [r for r in rows_lig if r[1] == v]}
        D['zap_' + v.lower()] = {'tit': 'WhatsApp da %s · %s (%d leads)' % (v, rotulo.lower(), len(Z)),
                                 'sub': 'Leads que ela escreveu pelo CRM no período.', 'cols': cols_z,
                                 'rows': [[x['quando'].strftime('%d/%m %H:%M') if x['quando'] else '', x['lead'], x['n'],
                                           'sim' if x['respondeu'] else 'não', x['etapa']]
                                          for x in sorted(Z, key=lambda x: x['quando'] or agora, reverse=True)]}
        D['contr_' + v.lower()] = {'tit': 'Contratos da %s · %s (%d)' % (v, rotulo.lower(), len(C)), 'cols': cols_c,
                                   'rows': [linha_contr(c) for c in reversed(C)]}
    outros_lig = [x for x in lig if x['quem'] not in VENDEDORAS]
    D['lig_outros'] = {'tit': 'Ligações de outras pessoas · %s (%d)' % (rotulo.lower(), len(outros_lig)),
                       'cols': cols_lig, 'rows': [r for r in rows_lig if r[1] not in VENDEDORAS]}

    # ---------- comissoes (card colocado em Distribuicao, no mes em que entrou)
    desde = [c for c in contratos if c['pedido'] >= str(INICIO)]
    com = {}
    for v in VENDEDORAS:
        L = [c for c in desde if c['vendedora'] == v and not c['perdido']]
        taxa = g(TAXA_CONTRATO[v])
        lib = [c for c in L if c['liberado']]
        lib_mes = [c for c in lib if (c['liberado_em'] or '')[:7] == mes]
        # a comissao conta por ACAO liberada: contrato sem processo no sistema jurídico vale 1
        n_acoes = sum(len(c.get('acoes') or []) or 1 for c in L)
        n_lib = sum(int(c.get('acoes_liberadas') or 0) for c in L)
        n_lib_mes = sum(int(c.get('acoes_liberadas') or 0) for c in lib_mes)
        com[v] = {'fechados': len(L), 'distribuidos': len(lib), 'liberada': taxa * n_lib,
                  'liberada_mes': taxa * n_lib_mes, 'a_liberar': taxa * max(0, n_acoes - n_lib),
                  'taxa': taxa, 'desde': 'desde a estreia (%s)' % dm(date.fromisoformat(
                      base.get('estreia_v1', str(INICIO)))) if v == V1 else 'desde agosto'}
        D['com_' + v.lower()] = {
            'tit': 'Comissões da %s: %s liberados, %s a liberar' % (v, brl(com[v]['liberada']), brl(com[v]['a_liberar'])),
            'sub': '%s por contrato que entra em Distribuição. Não há comissão por atendimento ou ligação.' % brl(taxa),
            'cols': ['Fechou em', 'Cliente', 'Ação (empresa) e processo', 'Liberada em', 'Comissão', 'Situação'],
            'rows': _linhas_comissao(L, taxa, P, V['a_liberar']),
            'nota': 'A comissão entra no mês em que a ação é liberada: atraso do jurídico no '
                    'protocolo não atrasa o comercial. Conta POR AÇÃO, então a mesma pessoa com dois processos pontua '
                    'duas vezes, cada um no mês em que libera. Libera por: processo com número, tarefa de PROTOCOLO no '
                    'sistema jurídico, liberação registrada na base, ou card em Distribuição no CRM.'}
    fora = [c for c in desde if not c['vendedora'] and not c['perdido']]
    desistiu = [c for c in desde if c['perdido']]
    fora_est = [c for c in contratos if c.get('fora_esteira')]
    D['fora_esteira'] = {'tit': 'Contratos fechados fora da esteira (%d)' % len(fora_est),
                         'sub': 'Processo novo de reclamante aberto direto no sistema jurídico, sem passar pela esteira de contratação. '
                                'Conta uma vez por cliente. Exceções na base de análise (fora_esteira_excluir).',
                         'cols': ['Aberto em', 'Cliente', 'Card', 'Processo', 'Origem'],
                         'rows': [[dm(c['data']), c['cliente'], c['quem'], c['cnj'] or '·', c['origem']] for c in reversed(fora_est)]}
    D['com_desistiu'] = {'tit': 'Fora da contagem e da comissão (%d)' % len(desistiu),
                         'sub': 'Quem desistiu (card em Perdido), perdido na esteira ou duplicado.',
                         'cols': ['Fechou em', 'Cliente', 'Quem', 'Motivo'],
                         'rows': [[dm(c['data']), c['cliente'], c['quem'], c['etapa']] for c in reversed(desistiu)]}
    D['com_fora'] = {'tit': 'Contratos sem comissão da mesa (%d)' % len(fora),
                     'sub': 'Fechados por quem não é vendedor (advogados) ou antes da estreia do vendedor.',
                     'cols': ['Fechou em', 'Cliente', 'Quem', 'Etapa'],
                     'rows': [[dm(c['data']), c['cliente'], c['quem'], c['etapa']] for c in reversed(fora)]}

    # ---------- funil, perdas, 1a ligacao
    contatados = {x['cid'] for x in lig if x['quem'] in VENDEDORAS} | {x['cid'] for x in zap}
    atenderam = {x['cid'] for x in lig if x['status'] == 'atendeu'}
    dist_per = [c for c in per if c['liberado']]
    funil = [{'k': 'leads', 'rot': 'Leads novos', 'n': n_leads},
             {'k': 'contatados', 'rot': 'Falaram com a mesa', 'n': len(contatados)},
             {'k': 'atenderam', 'rot': 'Atenderam a ligação', 'n': len(atenderam)},
             {'k': 'contratos', 'rot': V['Contratos'], 'n': len(per)},
             {'k': 'dist_per', 'rot': V['Em_dist'], 'n': len(dist_per)}]
    nomes = {}
    for x in lig + zap:
        nomes[x['cid']] = (x['lead'], x['etapa'])
    D['contatados'] = {'tit': 'Leads que a mesa contatou · %s (%d)' % (rotulo.lower(), len(contatados)),
                       'sub': 'Ligação pelo WhatsApp ou mensagem de um vendedor no período.',
                       'cols': ['Lead', 'Etapa'], 'rows': sorted([list(nomes[c]) for c in contatados if c in nomes])}
    D['atenderam'] = {'tit': 'Leads que atenderam a ligação · %s (%d)' % (rotulo.lower(), len(atenderam)),
                      'cols': ['Lead', 'Etapa'], 'rows': sorted([list(nomes[c]) for c in atenderam if c in nomes])}
    D['dist_per'] = {'tit': 'Contratos do período já distribuídos (%d)' % len(dist_per), 'cols': cols_c,
                     'rows': [linha_contr(c) for c in dist_per]}
    motivos = {}
    for p in perdas:
        motivos[p['motivo']] = motivos.get(p['motivo'], 0) + 1
    D['perdas'] = {'tit': 'Cards que foram para Perdido ou Desqualificado · %s (%d)' % (rotulo.lower(), len(perdas)),
                   'cols': ['Quando', 'Lead', 'Motivo', 'Quem moveu'],
                   'rows': [[p['quando'].strftime('%d/%m %H:%M') if p['quando'] else '', p['lead'], p['motivo'], p['quem']]
                            for p in sorted(perdas, key=lambda x: x['quando'] or agora, reverse=True)]}
    mins = [x['min'] for x in prim if x['min'] is not None]
    D['primeira'] = {'tit': '%s · %s' % (V['primeira'], rotulo.lower()),
                     'sub': 'Do lead chegar no WhatsApp até a 1ª ligação pela DAPI. Meta: até 1h.',
                     'cols': ['Chegou', 'Lead', 'Ligou em'],
                     'rows': [[x['chegou'].strftime('%d/%m %H:%M'), x['lead'],
                               ('%d min' % x['min'] if x['min'] < 120 else '%.1f h' % (x['min'] / 60)) if x['min'] is not None else 'sem ligação']
                              for x in prim]}

    # ---------- contratacao (estado de cada contrato)
    ativos = [c for c in contratos if c['pedido'] >= str(INICIO)]
    grupos = {k: [c for c in ativos if c['fase'] == k] for k in FASES}
    grupos['perdido'] += [c for c in ativos if c['perdido'] and c not in grupos['perdido']]

    def status(c):
        if c['perdido'] or c['fase'] == 'perdido':
            return 'perdido'
        if c['fase'] == 'dist':
            return V['status_dist']
        if c['fase'] == 'revisao' or (c['fase'] == 'zapsign' and c['dias'] < 7):
            return 'andando'
        if c['fase'] == 'zapsign':
            return 'atenção'                     # contrato parado no ZapSign ha uma semana ou mais
        return 'travado' if c['dias'] >= 4 else 'atenção'
    fluxo = [{'data': dm(c['data']), 'cliente': c['cliente'], 'quem': c['quem'], 'etapa': c['etapa'],
              'dias': c['dias'], 'status': status(c), 'cnj': c['cnj']} for c in reversed(ativos)]
    rot_fases = {x[0]: x[2] for x in V['fases']}
    for k, rot in FASES.items():
        D['fase_' + k] = {'tit': '%s (%d)' % (rot, len(grupos[k])), 'cols': ['Fechou em', 'Cliente', 'Quem fechou', 'Etapa', 'Dias'],
                          'rows': [[dm(c['data']), c['cliente'], c['quem'], c['etapa'], c['dias']] for c in reversed(grupos[k])]}
    em_fluxo = [c for c in ativos if status(c) in ('andando', 'atenção', 'travado')]
    fx = _fluxo_contratacao(hoje, D)                 # do Fechou a Producao

    # ---------- provisao
    fech = _fechamento_2026(adv['transacoes'], hoje, P) if adv.get('transacoes') else None
    prov = _provisao(base, fech, contratos, hoje)
    COLS = [('C', 'Já recebido em 2026'), ('D', 'Receita fixa'), ('E', 'Outras carteiras (estoque)'),
            ('F', 'Outras áreas: casos novos'), ('G', 'Sentenças'), ('H', 'Execuções, acordos e ganhos antigos'),
            ('I', 'Carteira em andamento'), ('J', 'Contratos novos')]
    for a in prov['anos']:
        D['ano_%d' % a['ano']] = {'tit': '%d: %s (%s da meta)' % (a['ano'], brl_curto(a['K']), pct(a['M'])),
                                  'cols': ['Fonte', 'Valor'],
                                  'rows': [[n, brl(a[k])] for k, n in COLS if a[k]] + [['Total', brl(a['K'])]]}
    sent = [s for s in base.get('sentencas', []) if s['classe'] != 'realizado']
    rot_cls = {'provavel': 'provável', 'possivel': 'possível', 'acordo_em_pagamento': 'acordo em pagamento'}
    cols_s = ['Classe', 'Cliente', 'Processo', 'Base do cálculo', 'Conservador', 'Base', 'Otimista', 'Caixa']

    def lin_s(s):
        return [rot_cls.get(s['classe'], s['classe']), s['ref'] or '·', s['cnj'] or '·', s['fonte'][:60],
                brl(s['cons']), brl(s['base']), brl(s['otim']), s['semestre']]
    D['prov_G'] = {'tit': 'Sentenças: carteira ganha (%d)' % len(sent),
                   'sub': 'Sem desconto de recuperação judicial (no TRT6 só a 2ª reclamada está em RJ). Apuração de %s.' % base.get('apuracao', ''),
                   'cols': cols_s, 'rows': [lin_s(s) for s in sorted(sent, key=lambda s: -s['base'])]}
    for k in ('provavel', 'possivel', 'acordo_em_pagamento'):
        L = [s for s in sent if s['classe'] == k]
        D['sent_' + k] = {'tit': 'Sentenças: %s (%d)' % (rot_cls[k], len(L)), 'cols': cols_s,
                          'rows': [lin_s(s) for s in sorted(L, key=lambda s: -s['base'])]}
    D['prov_J'] = {'tit': 'Contratos novos mês a mês',
                   'sub': 'Realizado nos meses que passaram; no mês corrente e depois, o maior entre realizado e meta. '
                          'Cada contrato vale %s esperados e entra no caixa 25%% em ~6, ~12, ~24 e ~36 meses.' % brl(prov['valor_contrato']),
                   'cols': ['Mês', 'Realizado', 'Meta', 'Usado', 'Valor', 'Como'],
                   'rows': [[mes_rot(m['mes']), m['real'] if m['real'] is not None else '·',
                             m['meta'] if m['meta'] is not None else '·', m['usado'], brl(m['valor']), m['como']]
                            for m in prov['meses']]}
    D['prov_E'] = {'tit': 'Outras carteiras: estoque × média histórica',
                   'cols': ['Área', 'Ativos', 'Médio por encerrado', 'Valor do estoque'],
                   'rows': [[o[0], o[1], brl(o[4]), brl(o[5])] for o in base.get('outras', [])],
                   'nota': 'Entra no caixa pela régua de quartis a partir de %d.' % int(g('ano_piso', 2027))}
    D['prov_F'] = {'tit': 'Outras áreas: casos novos',
                   'sub': 'Completa o estoque até o ritmo histórico de %s por ano (reclamada, cível e parcerias, média de 2 anos).' % brl(g('exito_outras')),
                   'cols': ['Ano', 'Ritmo histórico', 'Estoque', 'Casos novos'],
                   'rows': [[a['ano'], brl(g('exito_outras')), brl(a['E']), brl(a['F'])] for a in prov['anos'] if a['ano'] > 2026]}
    D['prov_I'] = {'tit': 'Carteira em andamento (distribuídos, sem sentença)',
                   'cols': ['Grupo', 'Processos', 'Valor por processo', 'Total'],
                   'rows': [[x[0], x[1], brl(x[3]), brl(x[4])] for x in base.get('grupos', [])]}
    D['prov_D'] = {'tit': 'Receita fixa (o que não é êxito)',
                   'sub': 'Ano corrente pelo que está lançado no sistema jurídico; depois %s por ano (premissa).' % brl(g('receita_fixa'))}
    D['prov_H'] = {'tit': 'Execuções, acordos e ganhos antigos',
                   'sub': '2026: parcelas de êxito lançadas no sistema jurídico até 31/12. 2027: ganhos antigos, conta prudente de 30/09.'}
    for k, lst in (base.get('listas') or {}).items():
        D[k] = lst
    if 'prov_fixa' in D:
        D['prov_D'].update({k: D['prov_fixa'][k] for k in ('cols', 'rows') if k in D['prov_fixa']})
    if 'prov_antigos' in D:
        D['prov_H'].update({k: D['prov_antigos'][k] for k in ('cols', 'rows') if k in D['prov_antigos']})
    D['prov_C'] = {'tit': 'Já recebido em %d' % hoje.year, 'cols': ['Categoria', 'Recebido'], 'rows': []}
    if fech:
        cats = {}
        for t in fech['itens']['rec']:
            cats[t['cat']] = cats.get(t['cat'], 0) + t['v']
        D['prov_C']['rows'] = [[k.capitalize(), brl(v)] for k, v in sorted(cats.items(), key=lambda i: -i[1])]
        for k in ('fixo', 'exito', 'atras'):
            L = sorted(fech['itens'][k], key=lambda t: t['vc'])
            D['fech_' + k] = {'tit': next(x['item'] for x in fech['linhas'] if x['k'] == k) + ' (%d)' % len(L),
                              'cols': ['Vencimento', 'Cliente', 'Categoria', 'Valor'],
                              'rows': [[dm(date.fromisoformat(t['vc'])), _nome(t['nome']), t['cat'].capitalize(), brl(t['v'], 2)]
                                       for t in L]}
    sem = {}
    for s in sent:
        sem[s['semestre']] = sem.get(s['semestre'], 0) + s['base']
    por_classe = []
    for k in ('provavel', 'possivel', 'acordo_em_pagamento'):
        L = [s for s in sent if s['classe'] == k]
        por_classe.append({'k': k, 'rot': rot_cls[k], 'n': len(L), 'cons': sum(s['cons'] for s in L),
                           'base': sum(s['base'] for s in L), 'otim': sum(s['otim'] for s in L)})

    # ---------- custo por contrato
    meses_c = []
    m0 = date(2026, 9, 1)
    d = max(m0, _add_meses(ini_mes, -1))
    while d <= max(date(2026, 12, 1), _add_meses(ini_mes, 2)):
        mk = d.strftime('%Y-%m')
        if d < ini_mes:
            meses_c.append(_custo_mes(_rateio(bp, fator(mk)), mk, 'real', contratos, meta_dias, hoje))
        elif d == ini_mes:
            meses_c.append(_custo_mes(_rateio(bp, fator(mk)), mk, 'hoje', contratos, meta_dias, hoje))
            meses_c.append(_custo_mes(_rateio(bp, fator(mk)), mk, 'meta', contratos, meta_dias, hoje))
        else:
            meses_c.append(_custo_mes(_rateio(bp, fator(mk)), mk, 'meta', contratos, meta_dias, hoje))
        d = _add_meses(d, 1)
        if len(meses_c) >= 5:
            break
    real_c = next((x for x in meses_c if x['modo'] == 'real'), None)
    if real_c:
        D['custo_real'] = {'tit': 'Custo por contrato: %s (real)' % mes_rot(real_c['mes']),
                           'cols': ['Item', 'Valor', 'De onde vem'],
                           'rows': [[i[0], brl(i[1]), i[2]] for i in real_c['itens']] +
                                   [['Total', brl(real_c['total']), ''], ['Contratos', real_c['contratos'], 'esteira de contratação'],
                                    ['Custo por contrato', brl(real_c['custo'] or 0), '']]}

    # ---------- escada
    escada = _escada(base)
    reais = _contratos_por_mes(contratos)
    for e in escada:
        e['realizado'] = reais.get(e['mes'], 0) if e['mes'] <= mes else None

    erros = fontes.get('erros') or {}
    out = {
        'produto': produto, 'rot': V,
        'gerado_em': agora.strftime('%Y-%m-%d %H:%M:%S'),
        'periodo': {'chave': periodo, 'de': str(ini), 'ate': str(fim), 'rotulo': rotulo, 'dias_uteis': du_per,
                    'curto': CURTO.get(periodo) or rotulo.split(' ')[0].lower()},
        'fontes': {'crm': agora.strftime('%d/%m %H:%M'),
                   'esteira': datetime.fromtimestamp(os.path.getmtime(ESTADO_CONTRATACAO)).strftime('%d/%m %H:%M')
                   if os.path.exists(ESTADO_CONTRATACAO) else None,
                   'advbox': fontes.get('advbox_em'), 'meta': fontes.get('meta_em'),
                   'base': base.get('apuracao'), 'erros': erros},
        'visao': {'custo': cp, 'retorno': ret, 'retorno_real': rr, 'conversao': len(per) / n_leads if n_leads else None,
                  'conv_reclamante': next((x['pct'] for x in conversao['produto'] if x['nome'] == 'Reclamante'), None),
                  'contratos': len(per), 'por_quem': {q: sum(1 for c in per if c['quem'] == q) for q in sorted({c['quem'] for c in per})},
                  'da_base': sum(1 for c in per if c['da_base']), 'carteira': len(per) * valor, 'valor_contrato': valor,
                  'leads': n_leads, 'sit': sit, 'leads_anuncio': leads_anuncio, 'gasto': gasto_per,
                  'conversas_meta': conv_meta, 'cpl': gasto_per / leads_anuncio if leads_anuncio else None,
                  'mes': {'mes': mes, 'rotulo': mes_rot(mes), 'contratos': len(do_mes), 'meta': mm['meta'],
                          'faltam': max(0, mm['meta'] - len(do_mes)), 'ritmo': round(ritmo, 1),
                          'du_mes': du_mes, 'du_passados': du_passados,
                          'por_dia_falta': round(max(0, mm['meta'] - len(do_mes)) / max(1, du_mes - du_passados), 1)},
                  'quem_meta': quem_meta, 'por_dia': por_dia, 'fila_ligar': None},
        'escada': escada, 'previsao': previsao,
        'mesa': mesa, 'comissoes': com, 'lig_outros': len(outros_lig),
        'funil': funil, 'perdas': sorted(motivos.items(), key=lambda i: -i[1]),
        'demitidos': {'saiu': sit[S[0]], 'trab': sit[S[1]], 'nd': sit[S[2]],
                      'pct_saiu': sit[S[0]] / disse if disse else None,
                      'pct_nd': sit[S[2]] / n_leads if n_leads else None,
                      'alvo': g('mix_alvo'), 'teto_nd': g('max_nao_inf'),
                      'mediana_min': statistics.median(mins) if mins else None,
                      'em_1h': sum(1 for x in mins if x <= 60), 'sem_ligacao': sum(1 for x in prim if x['min'] is None),
                      'n': len(prim)},
        'conversao': conversao,
        'fluxo_contratacao': fx,
        'contratacao': {'n': len(ativos), 'contagem': {k: len(v) for k, v in grupos.items()}, 'fluxo': fluxo,
                        'media_dias': round(sum(c['dias'] for c in em_fluxo) / len(em_fluxo), 1) if em_fluxo else None,
                        'em_fluxo': len(em_fluxo)},
        'provisao': {'anos': prov['anos'], 'cols': COLS, 'fechamento': fech['linhas'] if fech else None,
                     'fechamento_total': fech['total'] if fech else None,
                     'media': {k: sum(a[k] for a in prov['anos'][1:]) / 3 for k in 'CDEFGHIJKL'},
                     'sentencas': por_classe, 'semestres': sorted(sem.items()),
                     'top': [[s['ref'], s['base']] for s in sorted(sent, key=lambda s: -s['base'])[:7]],
                     'outras': base.get('outras', []), 'valor_contrato': prov['valor_contrato']},
        'ticket': {'areas': base.get('ticket', []), 'safras': base.get('safras', [])},
        'custos': [{'mes': x['mes'], 'rot': mes_rot(x['mes']) + {'real': ' (real)', 'hoje': ' até hoje', 'meta': ' (meta)'}[x['modo']],
                    'modo': x['modo'], 'itens': [[i[0], i[1]] for i in x['itens']], 'total': x['total'],
                    'contratos': x['contratos'], 'custo': x['custo'], 'razao': x['razao']} for x in meses_c],
        'premissas': bp.get('premissas_lista', []),
        'drill': D,
    }
    return out


def montar_mesa(vendedora, forcar=False, produto='trabalhista'):
    """Mesa de UM vendedor para o Inicio dele: o que fez hoje, na semana e no mes, a meta real
    dele, a previsibilidade (quantas ligacoes faltam pela regua das ultimas 2 semanas: a dela,
    ou a da mesa enquanto ela nao tiver contrato na janela), a comissao dela e as medalhas por marco da meta."""
    agora = datetime.now()
    hoje = agora.date()
    base = _base()
    P = base['premissas']
    g = lambda k, d=0: float(P.get(k, d) or 0)
    fontes = _fontes(forcar)
    adv = fontes.get('advbox') or {}
    ini_mes, ini14 = hoje.replace(day=1), hoje - timedelta(days=13)
    seg = hoje - timedelta(days=hoje.weekday())
    produto = 'trabalhista'
    con = _con()
    try:
        do_produto = lambda x: True
        contratos = _contratos(con, base, adv.get('processos') or [], hoje)
        lig14 = [x for x in _ligacoes(con, min(ini14, ini_mes), hoje) if do_produto(x)]
        zap_hoje = [x for x in _whatsapp(con, hoje, hoje) if x['quem'] == vendedora and do_produto(x)]
    finally:
        con.close()
    base = _base_produto(base, produto)
    P = base['premissas']
    validos = [c for c in contratos if not c['perdido']]
    minhas = [c for c in validos if c['vendedora'] == vendedora]
    minhas_lig = [x for x in lig14 if x['quem'] == vendedora]

    def lig_entre(lst, a, b):
        return [x for x in lst if x['quando'] and a <= x['quando'].date() <= b]
    l_hoje, l_sem, l_mes = (lig_entre(minhas_lig, hoje, hoje), lig_entre(minhas_lig, seg, hoje),
                            lig_entre(minhas_lig, ini_mes, hoje))
    at = lambda lst: sum(1 for x in lst if x['status'] == 'atendeu')
    mes = hoje.strftime('%Y-%m')
    mm = _meta_mes(base, mes)
    meta = mm[vendedora]
    du_mes = _dias_uteis(ini_mes, _fim_mes(hoje))
    du_passados = _dias_uteis(ini_mes, hoje)
    hoje_util = hoje.weekday() < 5 and hoje not in FERIADOS
    dias_daqui = max(1, du_mes - du_passados + (1 if hoje_util else 0))       # dias uteis a partir de hoje, com hoje
    do_mes = [c for c in minhas if c['pedido'][:7] == mes]
    proj = len(do_mes) / du_passados * du_mes if du_passados else 0
    # regua: a dela, se ela fechou na janela; senao a da mesa (todas as ligacoes e contratos da janela)
    c14 = [c for c in minhas if ini14 <= c['data'] <= hoje]
    l14 = lig_entre(minhas_lig, ini14, hoje)
    regua = 'dela'
    if not c14:
        regua = 'da mesa'
        c14 = [c for c in validos if ini14 <= c['data'] <= hoje]
        l14 = lig_entre(lig14, ini14, hoje)
    lig_por = len(l14) / len(c14) if c14 else None
    taxa_at = at(l14) / len(l14) if l14 else None
    # o que for necessario para a meta virar processo: 1 em cada 3 se perde antes de distribuir
    perda = _perda_distribuicao(contratos, hoje)
    margem = 1 - perda['desistiu'] if perda['n'] and perda['desistiu'] < 0.9 else 1.0
    fechar = int(math.ceil(round(meta / margem, 6))) if meta else 0
    faltam = max(0, fechar - len(do_mes))
    lig_faltam = faltam * lig_por if lig_por else None
    meta_dia = (lig_faltam + len(l_hoje)) / dias_daqui if lig_faltam is not None else None   # hoje conta o que ja fez
    taxa = g(TAXA_CONTRATO[vendedora])
    desde = [c for c in minhas if c['pedido'] >= str(INICIO)]
    lib = [c for c in desde if c['liberado']]
    acao = {'docs': 'cobrar os documentos do cliente', 'zapsign': 'cobrar a assinatura no ZapSign',
            'revisao': 'inicial com a produção', 'outro': 'conferir com a equipe'}
    pend = sorted([c for c in desde if not c['liberado'] and not c['perdido']], key=lambda c: c['pedido'])
    marcos = [(0.25, 'bronze', '🥉'), (0.5, 'prata', '🥈'), (0.75, 'ouro', '🥇'), (1.0, 'troféu', '🏆')]
    pct_meta = len(do_mes) / meta if meta else 0
    # para a carteira moedas da equipe: todos os contratos dela (os perdidos tambem: a moeda do Fechou nao volta) e as liberacoes
    pc = {'contratos': [(c['cid'], c['data']) for c in contratos if c['vendedora'] == vendedora and c.get('data')],
          'lib': [(c['cid'], c['liberado_em']) for c in minhas if c['liberado'] and c['liberado_em']]}
    return {
        '_pc': pc, 'produto': produto, 'rot': VOC[produto],
        'vendedora': vendedora, 'gerado_em': agora.strftime('%Y-%m-%d %H:%M:%S'),
        'mes': {'mes': mes, 'rotulo': mes_rot(mes), 'meta': meta, 'feitos': len(do_mes), 'faltam': faltam, 'pct': pct_meta,
                'fechar': fechar, 'margem': margem,
                'proj': round(proj, 1), 'pct_proj': proj / meta if meta else None,
                'du_mes': du_mes, 'du_passados': du_passados, 'dias_daqui': dias_daqui},
        'hoje': {'ligacoes': len(l_hoje), 'atendidas': at(l_hoje), 'whatsapp': len(zap_hoje),
                 'contratos': sum(1 for c in minhas if c['data'] == hoje),
                 'meta_ligacoes': round(meta_dia) if meta_dia is not None else None,
                 'meta_atendidas': round(meta_dia * taxa_at) if meta_dia is not None and taxa_at is not None else None},
        'semana': {'ligacoes': len(l_sem), 'atendidas': at(l_sem),
                   'contratos': sum(1 for c in minhas if seg <= c['data'] <= hoje)},
        'regua': {'de': regua, 'janela': '%s a %s' % (dm(ini14), dm(hoje)), 'ligacoes': len(l14), 'atendidas': at(l14),
                  'contratos': len(c14), 'lig_por_contrato': lig_por, 'taxa_atendimento': taxa_at,
                  'lig_faltam': round(lig_faltam) if lig_faltam is not None else None,
                  'lig_mes_feitas': len(l_mes)},
        'comissao': {'taxa': taxa, 'liberada_mes': taxa * sum(1 for c in lib if (c['liberado_em'] or '')[:7] == mes),
                     'liberada': taxa * len(lib), 'a_liberar': taxa * (len(desde) - len(lib)), 'fechados': len(desde),
                     'distribuidos': len(lib)},
        'medalhas': [{'pct': m[0], 'nome': m[1], 'ico': m[2], 'ganhou': pct_meta >= m[0]} for m in marcos],
        # comissao quando o card entra em Distribuicao: o que esta em jogo e o que ela pode fazer
        'distribuicao': {'em_jogo': taxa * len(pend), 'taxa': taxa,
                         'pendentes': [{'id': c['cid'], 'cliente': c['cliente'].split()[0] if c['cliente'] else '',
                                        'data': dm(c['data']), 'dias': c['dias'], 'etapa': c['etapa'],
                                        'acao': acao.get(c['fase'], 'conferir com a equipe')} for c in pend],
                         'distribuidos': [{'id': c['cid'], 'cliente': c['cliente'].split()[0] if c['cliente'] else '',
                                           'em': dm(date.fromisoformat(c['liberado_em'])) if c['liberado_em'] else 'data a lançar'}
                                          for c in sorted(lib, key=lambda c: c['liberado_em'] or '', reverse=True)]},
        'contratos': [{'id': c['cid'], 'data': dm(c['data']), 'cliente': c['cliente'].split()[0] if c['cliente'] else '',
                       'etapa': c['etapa']} for c in sorted(do_mes, key=lambda c: c['pedido'], reverse=True)],
    }


def _mesa_completa(vendedora):
    """Mesa do vendedor com a carteira de moedas."""
    r = montar_mesa(vendedora)
    pcs = [r.pop('_pc', None)]
    r['produtos'] = {}
    r['comissao_total_mes'] = r['comissao']['liberada_mes']
    metas = [('trab', r.get('mes') or {}, 'contratos')]
    try:
        r['slcoin'] = _slcoin(vendedora, [x for x in pcs if x], metas)
    except Exception as e:                           # a carteira nunca derruba a mesa
        r['slcoin'] = {'ativo': False, 'erro': str(e)[:160]}
    return r


def _slcoin(vendedora, pcs, metas=()):
    """Carteira de moedas da equipe, calculada do que ja existe, sem gravar nada:
    ligacao atendida com 2 min ou mais = 1; contrato fechado = 10; card em Distribuicao = 20;
    missoes do dia (so dia util): Comeco forte (ligacoes ate as 11h >= metade da media diaria dela, minimo 3) = 3 e
    Dia cheio (atendidas no dia > a media diaria dela, minimo 2) = 5; sequencia de dias uteis com Dia cheio: 3 = 10 e
    5 = 25 (e recomeca). As medias sao dos 10 dias uteis anteriores, entao a missao sobe quando ela melhora.
    Nivel do mes pelo que ganhou no mes anterior: Bronze, Prata (300) x1,1, Ouro (500) x1,25, Diamante (800) x1,5.
    Resgates sao da caixa de RH (ainda nao existem)."""
    agora = datetime.now()
    hoje, ini = agora.date(), MOEDAS_EQUIPE_INICIO
    if hoje < ini:
        return {'ativo': False, 'inicio': ini.strftime('%d/%m')}
    con = _con()
    try:
        ligs = [x for x in _ligacoes(con, ini - timedelta(days=21), hoje) if x['quem'] == vendedora and x['quando']]
        email = [k for k, v in EMAIL_VENDEDORA.items() if v == vendedora]
        try:                                          # ficha do RH (contrato de dados moedas da equipe, 22/10): colaborador_id
            rh = con.execute('select id from rh_colaboradores where lower(email)=?', (email[0],)).fetchone() if email else None
        except sqlite3.Error:
            rh = None
    finally:
        con.close()

    def seg(dur):
        try:
            m_, s_ = str(dur).split(':')
            return int(m_) * 60 + int(s_)
        except Exception:
            return 0

    dia = {}
    for x in ligs:
        e = dia.setdefault(x['quando'].date(), {'lig': 0, 'at': 0, 'manha': 0, 'valem': []})
        e['lig'] += 1
        e['manha'] += 1 if x['quando'].hour < 11 else 0
        if x['status'] == 'atendeu':
            e['at'] += 1
            if seg(x['dur']) >= 120:                 # piso de 2 min
                e['valem'].append(x)
    util = lambda d: d.weekday() < 5 and d not in FERIADOS

    def alvos(d):
        ant = [d - timedelta(days=i) for i in range(1, 22) if util(d - timedelta(days=i))][:10]
        ml = sum(dia.get(x, {}).get('lig', 0) for x in ant) / len(ant) if ant else 0
        ma = sum(dia.get(x, {}).get('at', 0) for x in ant) / len(ant) if ant else 0
        return max(3, int(math.ceil(ml / 2))), max(2, int(ma) + 1)

    ev = []
    def soma(id_, d, tipo, desc, quando=None):
        vence = date(d.year + (d.month + 5) // 12, (d.month + 5) % 12 + 1, min(d.day, 28))   # vale 6 meses (regulamento)
        ev.append({'id': id_, 'dia': d, 'tipo': tipo, 'moedas': MOEDAS_EQUIPE_VALOR[tipo], 'desc': desc,
                   'hora': quando.strftime('%H:%M') if quando else '', 'expira_em': vence.isoformat()})
    for d in sorted(dia):
        if d < ini:
            continue
        for x in dia[d]['valem']:
            soma('lig:%s:%s' % (x['cid'], x['quando'].strftime('%H%M%S')), d, 'lig',
                 'Ligação atendida' + (' · ' + x['lead'].split()[0] if x['lead'] else ''), x['quando'])
    for p in pcs:
        for cid, dt in p.get('contratos') or []:
            if dt >= ini:
                soma('contrato:%s' % cid, dt, 'contrato', 'Contrato fechado')
        for cid, em in p.get('lib') or []:
            d = date.fromisoformat(str(em)[:10])
            if d >= ini:
                soma('dist:%s' % cid, d, 'dist', 'Comissão liberada')
    missoes, seq, d = [], 0, ini
    while d <= hoje:
        if util(d):
            am, ad = alvos(d)
            e = dia.get(d, {'lig': 0, 'at': 0, 'manha': 0})
            ok_m, ok_d = e['manha'] >= am, e['at'] >= ad
            if ok_m:
                soma('manha:%s' % d, d, 'manha', 'Missão Começo forte')
            if ok_d:
                soma('dia:%s' % d, d, 'dia', 'Missão Dia cheio')
                seq += 1
                if seq == 3:
                    soma('seq3:%s' % d, d, 'seq3', 'Sequência de 3 dias')
                elif seq == 5:
                    soma('seq5:%s' % d, d, 'seq5', 'Semana cheia: 5 dias seguidos')
                    seq = 0
            elif d < hoje:
                seq = 0                                       # hoje ainda pode cumprir: so quebra depois que o dia passa
            if d == hoje:
                missoes = [{'id': 'manha', 'nome': 'Começo forte', 'desc': '%d ligações até as 11h' % am,
                            'feito': e['manha'], 'alvo': am, 'moedas': MOEDAS_EQUIPE_VALOR['manha'], 'ok': ok_m},
                           {'id': 'dia', 'nome': 'Dia cheio', 'desc': '%d atendidas hoje (mais que a sua média)' % ad,
                            'feito': e['at'], 'alvo': ad, 'moedas': MOEDAS_EQUIPE_VALOR['dia'], 'ok': ok_d}]
        d += timedelta(days=1)
    # nivel e multiplicador de cada mes pelo que ganhou (sem multiplicador) no mes anterior
    base = {}
    for x in ev:
        k = x['dia'].strftime('%Y-%m')
        base[k] = base.get(k, 0) + x['moedas']
    def nivel(v):
        n = MOEDAS_EQUIPE_NIVEIS[0]
        for lim in MOEDAS_EQUIPE_NIVEIS:
            if v >= lim[0]:
                n = lim
        return n
    mes = hoje.strftime('%Y-%m')
    ant_mes = (hoje.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
    saldo = 0
    for k, v in base.items():
        prev = (date(int(k[:4]), int(k[5:]), 1) - timedelta(days=1)).strftime('%Y-%m')
        saldo += int(round(v * nivel(base.get(prev, 0))[2]))
    atual, este = nivel(base.get(ant_mes, 0)), base.get(mes, 0)
    prox = [n for n in MOEDAS_EQUIPE_NIVEIS if n[0] > este]
    # atendidas por contrato nas ultimas 2 semanas, os dois produtos juntos (para o aviso: quanto falta para o proximo)
    i14 = hoje - timedelta(days=13)
    at14 = sum(v['at'] for k, v in dia.items() if k >= i14)
    c14 = sum(1 for p in pcs for _, dt in (p.get('contratos') or []) if dt >= i14)
    hoje_ev = [dict(x, dia=x['dia'].isoformat()) for x in ev if x['dia'] == hoje]
    # conquistas: a tela explode em moedas na primeira vez que cada uma aparece
    conq = []
    for lim, nome_n, mult_n in MOEDAS_EQUIPE_NIVEIS[1:]:
        if este >= lim:
            conq.append({'id': 'nivel:%s:%s' % (mes, nome_n), 'tipo': 'nivel', 'titulo': 'Nível %s destravado!' % nome_n,
                         'sub': 'Você juntou %d moedas da equipe em %s. No mês que vem, cada moeda sua vale ×%s.'
                                % (este, mes_rot(mes).lower(), str(mult_n).replace('.', ','))})
    for prod, mm, palavra in metas:
        if mm.get('meta') and (mm.get('feitos') or 0) >= mm['meta']:
            conq.append({'id': 'meta:%s:%s' % (prod, mm.get('mes')), 'tipo': 'meta', 'titulo': 'Meta batida!',
                         'sub': '%d de %d %s em %s. Daqui pra frente é recorde!'
                                % (mm['feitos'], mm['meta'], palavra, (mm.get('rotulo') or '').lower())})
    for lim, nome_p in MOEDAS_EQUIPE_PATAMARES:              # trilha do RH (especificacao das moedas, 22/10): uma vez por patamar
        if saldo >= lim:
            conq.append({'id': 'patamar:%s' % nome_p, 'tipo': 'patamar', 'titulo': 'Você chegou ao patamar %s!' % nome_p,
                         'sub': 'Sua trilha acendeu mais um degrau. Continue juntando para viver a experiência que você escolher.'})
    return {'ativo': True, 'inicio': ini.strftime('%d/%m'), 'saldo': saldo, 'colaborador_id': rh[0] if rh else None,
            'nivel': {'nome': atual[1], 'mult': atual[2]},
            'mes': {'ganhas_base': este, 'proximo': ({'nome': prox[0][1], 'mult': prox[0][2], 'falta': prox[0][0] - este}
                                                     if prox else None)},
            'missoes': missoes, 'sequencia': seq, 'hoje': hoje_ev, 'ganhas_hoje': sum(x['moedas'] for x in hoje_ev),
            'conquistas': conq,
            # trilha de premios (patamares e exemplos configurados acima): quanto falta para cada um
            'trilha': [{'nome': n_, 'lim': l_, 'ok': saldo >= l_, 'falta': max(0, l_ - saldo), 'exemplos': MOEDAS_EQUIPE_EXEMPLOS.get(n_, '')}
                       for l_, n_ in MOEDAS_EQUIPE_PATAMARES],
            'at_hoje': dia.get(hoje, {}).get('at', 0),
            'at_por_contrato': round(at14 / c14) if c14 and at14 else None}


def _resumo_produtos(rs):
    """Resumo de cada produto para os cartoes e o consolidado (so o que faz sentido somar)."""
    res = {}
    for p, r in rs.items():
        v, pv = r['visao'], r['previsao']
        esc = (pv.get('colunas') or [{}])[0]
        res[p] = {'rot': r['rot'], 'vendas': v['contratos'], 'leads': v['leads'], 'leads_anuncio': v['leads_anuncio'],
                  'ligacoes': sum(x['ligacoes'] for x in r['mesa']) + (r.get('lig_outros') or 0),
                  'atendidas': sum(x['atendidas'] for x in r['mesa']), 'conversao': v['conversao'], 'gasto': v['gasto'],
                  'custo': v['custo'], 'mes': v['mes'], 'quem_meta': v['quem_meta'],
                  'lig_dia': esc.get('lig_dia'), 'lig_dia_atual': esc.get('lig_dia_atual'), 'fechar': esc.get('fechar'),
                  'comissao_mes': sum(c['liberada_mes'] for c in r['comissoes'].values()),
                  'em_jogo': sum(c['a_liberar'] for c in r['comissoes'].values()),
                  'perdidos': sum(x[1] for x in r['perdas']), 'fila_ligar': v.get('fila_ligar')}
    t = res['trabalhista']
    soma = lambda k: (t.get(k) or 0)
    custo_total = (t['custo'] or {}).get('total', 0)
    cons = {'vendas': soma('vendas'), 'leads': soma('leads'), 'ligacoes': soma('ligacoes'), 'atendidas': soma('atendidas'),
            'gasto': soma('gasto'), 'comissao_mes': soma('comissao_mes'), 'em_jogo': soma('em_jogo'),
            'perdidos': soma('perdidos'), 'custo_total': custo_total,
            'custo_venda': custo_total / soma('vendas') if soma('vendas') else None,
            'mes_vendas': t['mes']['contratos'], 'mes_meta': t['mes']['meta']}
    return res, cons


def montar_geral(periodo='semana', forcar=False):
    """Resumo do produto (rota /geral)."""
    rs = {p: montar(periodo, forcar and p == 'trabalhista', p) for p in PRODUTOS}
    res, cons = _resumo_produtos(rs)
    ref = rs['trabalhista']
    return {'geral': True, 'gerado_em': ref['gerado_em'], 'periodo': ref['periodo'], 'fontes': ref['fontes'],
            'produtos': res, 'consolidado': cons, 'drill': {}}


def montar_unico(periodo='semana', forcar=False):
    """Painel do Comercial (um produto so: trabalhista)."""
    return montar(periodo, forcar, 'trabalhista')


@router.get('/minha-mesa')
def minha_mesa(vendedora: str = Query(None, description='so para admin: nome do vendedor (COMERCIAL_VENDEDOR_1/2)'),
               user=Depends(usuario_atual)):
    """O vendedor logado ve so a mesa dele; admin pode escolher. Quem nao e vendedor recebe vendedora=None."""
    dona = _quem(getattr(user, 'email', '') or '')
    if getattr(user, 'papel', '') == 'admin' and vendedora in VENDEDORAS:
        return _mesa_completa(vendedora)
    if dona in VENDEDORAS:
        return _mesa_completa(dona)
    return {'vendedora': None}


@router.get('/painel')
def painel(periodo: str = Query('semana', description='hoje | semana | semana_passada | mes | mes_passado'),
           atualizar: bool = Query(False, description='renova sistema jurídico e Meta agora (se tiverem mais de 1 min)'),
           produto: str = Query('trabalhista', description='trabalhista | todos'),
           user=Depends(requer_admin)):
    # def (e nao async): roda em thread. O servico tem 1 worker so; consulta pesada em async congelaria o sistema.
    per = periodo if periodo in ('hoje', 'semana', 'semana_passada', 'mes', 'mes_passado') else 'semana'
    if produto == 'todos':
        return montar_unico(per, forcar=atualizar)
    return montar(per, forcar=atualizar, produto=produto if produto in PRODUTOS else 'trabalhista')


@router.get('/geral')
def geral(periodo: str = Query('semana', description='hoje | semana | semana_passada | mes | mes_passado'),
          atualizar: bool = Query(False), user=Depends(requer_admin)):
    """Visao geral do Comercial."""
    return montar_geral(periodo if periodo in ('hoje', 'semana', 'semana_passada', 'mes', 'mes_passado') else 'semana',
                        forcar=atualizar)
