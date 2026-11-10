#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vigia de audiencias: onde a audiencia mora de verdade, e a providencia da
Dra. Advogada uma semana antes.

POR QUE EXISTE. Em 14/09/2026 o Ataíde (1000303-41.2025.5.18.0051, expectativa
de R$ 1,79 mi) nao compareceu a audiencia UNA. O robo de lembretes rodava as 7h
todo dia desde 03/09 e nunca mandou nada: ele le a agenda Google do escritorio,
e a equipe registra audiencia como TAREFA no ADVBOX. A do Ataíde estava no
ADVBOX desde 24/08; a agenda tinha 3 audiencias, nenhuma dele. O log dizia
"enviados: 0 | sem destinatario: 0", o que parece "nada a fazer".

Em 08/09 foi o caso B x empresa B: pedimos audiencia telepresencial em 19/08,
o despacho nao saiu, e a equipe se deslocou ate Recife. Nada vigiava o pedido.

O QUE FAZ, todo dia antes dos lembretes:
  1. junta as audiencias futuras das tres fontes que existem hoje e grava em
     `audiencia_registro`, que o lembretes_compromisso.py passa a ler:
       - tarefas do ADVBOX do tipo AUDIENCIA (onde a equipe registra);
       - intimacoes de audiencia designada, lidas pelo compromisso.py;
       - agendas Google (a do escritorio e a "ADVBOX agenda").
  2. a 7 dias corridos da audiencia, abre no ADVBOX a tarefa PREPARACAO PARA
     AUDIENCIA para a Dra. Advogada, com o checklist da Dra. Advogada preenchido
     com o que o sistema consegue ver.

O que o sistema nao ve, ele escreve que nao viu. Nunca marca item como ok.

28/09/2026 - A AUDIENCIA NASCE NO SISTEMA (migracao do ADVBOX):
  - fontes novas: a pauta do tribunal que o SYNC traz (ja pegou duas que nao
    estavam em lugar nenhum, uma delas a tres dias), as tarefas de audiencia
    lancadas no proprio sistema e o que a equipe lanca ou corrige na tela
    Agenda > Audiencias (fonte 'sistema', que manda sobre as outras);
  - linha lancada ou corrigida na tela (editado_local=1) nao e sobrescrita nem
    dada como substituida por aqui;
  - audiencia futura, sem divergencia, que nao esta na agenda central, entra
    nela sozinha (app/audiencias_nucleo.py). Se ja houver evento da equipe, so
    liga. Evento nosso acompanha a redesignacao;
  - a tarefa da Dra. Advogada nao depende mais do ADVBOX: processo sem id la, ou
    com a chave das tarefas em 'sistema', abre no Sistema Escritório Modelo; e nao abre de
    novo se ja existe tarefa de preparacao aberta para o processo;
  - ADVBOX sem resposta nao para mais tudo: as outras fontes seguem, so nada
    sai do registro nessa rodada.

Uso:
    python3 audiencias_vigia.py                  # simulacao: mostra tudo
    python3 audiencias_vigia.py --previa NNN     # checklist de um processo
    python3 audiencias_vigia.py --aplicar        # grava registro e cria tarefas
"""
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata

BASE = '/opt/escritorio/SISTEMA/backend'
DB = BASE + '/sistema_esc.db'
sys.path.insert(0, BASE)
sys.path.insert(0, '/opt/escritorio/INTEGRACOES')

for _linha in open('/opt/escritorio/config/.env', encoding='utf-8'):
    _linha = _linha.strip()
    if _linha and not _linha.startswith('#') and '=' in _linha:
        _k, _v = _linha.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

import requests                                              # noqa: E402
import advbox_integration as AB                              # noqa: E402
import tarefas_esc as TP                                     # noqa: E402
from app import agenda_google as AG                          # noqa: E402
from app import audiencias_nucleo as N                       # noqa: E402
from app import andamentos_nucleo as AND                      # noqa: E402
from app import audiencias_sync as AS                         # noqa: E402  (07/11/2026: livro do tribunal)
from app.controladoria_motor import compromisso as CP        # noqa: E402
from app.controladoria_motor.feriados import eh_dia_util     # noqa: E402

APLICAR = '--aplicar' in sys.argv
PREVIA = sys.argv[sys.argv.index('--previa') + 1] if '--previa' in sys.argv else None
HOJE = dt.date.today()
JANELA_DIAS = 400
DIAS_ANTES = 7

PAPEL_PRODUCAO, PAPEL_TITULAR = 'revisora', 'titular'   # papeis (juridico/equipe.json)
TASK_PREPARACAO = 'PREPARACAO PARA AUDIENCIA'
SYNC = 'https://sync.atendedireito.app/v1'
CNJ = re.compile(r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}')
SEMANA = ['segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira',
          'sexta-feira', 'sábado', 'domingo']


def norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', s).upper().strip()


def br(iso):
    t = str(iso or '')[:10].split('-')
    return '%s/%s/%s' % (t[2], t[1], t[0]) if len(t) == 3 else str(iso or '')


# ---------------------------------------------------------------------------
# leitura de data e modalidade em texto livre
# ---------------------------------------------------------------------------
_DATA = re.compile(r'(\d{1,2})[./](\d{1,2})[./](\d{2,4})')
_HORA = re.compile(r'(\d{1,2})\s*(?:h|:)\s*(\d{2})')
_REDESIGNA = re.compile(r'REDESIGN|ALTERE|REMARC|REAJUST|NOVA DATA|ADIAD')


def datas_no_texto(txt):
    """(data, hora, veio_depois_de_PARA) para cada data completa do texto."""
    txt = str(txt or '')
    out = []
    for m in _DATA.finditer(txt):
        d, mes, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
        ano = ano + 2000 if ano < 100 else ano
        try:
            quando = dt.date(ano, mes, d)
        except ValueError:
            continue
        hora = ''
        h = _HORA.search(txt[m.end():m.end() + 16])
        if h and int(h.group(1)) < 24:
            hora = '%02d:%s' % (int(h.group(1)), h.group(2))
        antes = norm(txt[max(0, m.start() - 14):m.start()])
        out.append((quando, hora, antes.endswith('PARA')))
    return out


def data_futura(txt):
    """A data da audiencia num texto de tarefa.

    Quando ha redesignacao ("alterei a data de 09.11 para 16/11/2026"), vale a
    data que vem depois de "para". Sem isso, a primeira data futura.
    """
    fut = [x for x in datas_no_texto(txt)
           if HOJE <= x[0] <= HOJE + dt.timedelta(days=JANELA_DIAS)]
    if not fut:
        return None, ''
    marcadas = [x for x in fut if x[2]]
    x = marcadas[-1] if marcadas else fut[0]
    return x[0], x[1]


_DATA_CURTA = re.compile(r'(?<![\d/.])(\d{1,2})[./](\d{1,2})(?![./]?\d)')


def _hora_depois(t, fim):
    h = _HORA.search(t[fim:fim + 16])
    return '%02d:%s' % (int(h.group(1)), h.group(2)) if h and int(h.group(1)) < 24 else ''


def datas_de_audiencia(txt, referencia=None):
    '''Datas citadas perto de "audiencia" num comentario, com o ano deduzido.

    O ano de "06.10" sai da data do COMENTARIO, nao de hoje. Deduzir a partir
    de hoje transformou audiencia ja realizada em audiencia do ano que vem: o
    Severino apareceu com audiencia em 08/09/2027.
    '''
    t = str(txt or '')
    if 'AUDI' not in norm(t):
        return []
    ref = referencia or HOJE
    out = []
    for m in _DATA.finditer(t):
        if 'AUDI' not in norm(t[max(0, m.start() - 120):m.end() + 40]):
            continue
        d0, mes, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
        ano = ano + 2000 if ano < 100 else ano
        try:
            out.append((dt.date(ano, mes, d0), _hora_depois(t, m.end())))
        except ValueError:
            pass
    for m in _DATA_CURTA.finditer(t):
        if 'AUDI' not in norm(t[max(0, m.start() - 120):m.end() + 40]):
            continue
        try:
            d = dt.date(ref.year, int(m.group(2)), int(m.group(1)))
            if d < ref:
                d = dt.date(ref.year + 1, d.month, d.day)
        except ValueError:
            continue
        out.append((d, _hora_depois(t, m.end())))
    return out


def data_em_comentario(txt, referencia=None):
    '''(data futura, hora, so_ha_data_passada).'''
    todas = datas_de_audiencia(txt, referencia)
    fut = sorted((d, h) for d, h in todas
                 if HOJE <= d <= HOJE + dt.timedelta(days=JANELA_DIAS))
    if fut:
        return fut[0][0], fut[0][1], False
    return None, '', any(d < HOJE for d, _ in todas)


def modalidade(txt):
    t = norm(txt)
    if 'HIBRID' in t or 'MISTA' in t:
        return 'hibrida'
    if re.search(r'TELEPRESENCIAL|VIDEO|VIRTUAL|ZOOM|ONLINE', t):
        return 'virtual'
    if 'PRESENCIAL' in t:
        return 'presencial'
    return ''


def subtipo(nome):
    n = norm(nome).replace('_', ' ')
    if 'INSTRUCAO' in n:
        return 'instrucao'
    if re.search(r'\bUNA\b', n):
        return 'una'
    if 'INICIAL' in n:
        return 'inicial'
    if 'CONCILIA' in n:
        return 'conciliacao'
    return 'audiencia'


# ---------------------------------------------------------------------------
# as tres fontes
# ---------------------------------------------------------------------------
def historico_com_paciencia(lid, tentativas=3):
    '''Historico do processo, do mais novo para o mais antigo, ou None.

    O ADVBOX devolve lista vazia quando esgota as tentativas por rate limit.
    Todo processo com card de audiencia tem ao menos o proprio card no
    historico, entao vazio aqui e falta de resposta, nao falta de registro.
    '''
    for i in range(tentativas):
        try:
            h = AB.listar_historico(lid)
        except Exception:
            h = None
        if h:
            return sorted(h, key=lambda x: str(x.get('created_at')), reverse=True)
        time.sleep(20 * (i + 1))
    return None


def de_advbox():
    """Tarefas cujo TIPO comeca com AUDIENCIA. E onde a equipe registra.

    "AVISAR CLIENTE AUDIENCIA", "PREPARACAO PARA AUDIENCIA" (esta propria
    tarefa) e afins ficam de fora: nao comecam com AUDIENCIA.
    """
    tarefas = {}

    def junta(lista):
        for t in lista or []:
            if t.get('id'):
                tarefas[t['id']] = t

    ini = (HOJE - dt.timedelta(days=120)).isoformat()
    fim = (HOJE + dt.timedelta(days=JANELA_DIAS)).isoformat()
    for kw in ({'date_start': ini, 'date_end': fim},
               {'completed_start': ini, 'completed_end': HOJE.isoformat()}):
        try:
            junta(AB.listar_tarefas(limit=100, **kw))
        except Exception as e:
            print('  ADVBOX %s: %s' % (list(kw)[0], str(e)[:80]))
    for u in ((AB.carregar_settings() or {}).get('users') or []):
        try:
            junta(AB.listar_tarefas(user_id=u.get('id'), limit=100))
        except Exception:
            pass

    achados, sem_data = [], {}
    for t in tarefas.values():
        if not norm(t.get('task')).startswith('AUDIENCIA'):
            continue
        lw = t.get('lawsuit') or {}
        num = lw.get('process_number') or ''
        if not CNJ.fullmatch(num):
            continue
        quando, hora = data_futura(t.get('notes'))
        if not quando:
            sem_data[num] = t
            continue
        achados.append({
            'processo': num, 'data': quando.isoformat(), 'hora': hora,
            'subtipo': subtipo(t.get('task')), 'modalidade': modalidade(t.get('notes')),
            'local': '', 'lawsuit_id': t.get('lawsuits_id') or lw.get('id'),
            'fonte': 'advbox', 'quando_fonte': str(t.get('created_at') or ''),
            'redesignacao': bool(_REDESIGNA.search(norm(t.get('notes')))),
        })
    # Card de audiencia sem data: procurar nos comentarios do processo. Se nem
    # la houver data, o card vai para a lista de "corrigir", nunca some.
    datados = {a['processo'] for a in achados}
    nao_resolvidas = []
    for num, t in sem_data.items():
        if num in datados:
            continue
        lid = t.get('lawsuits_id') or (t.get('lawsuit') or {}).get('id')
        # card com data passada no proprio texto ("06/07_ 12H45", com ou sem
        # ano): a audiencia ja aconteceu e ninguem fechou o card. Nao e
        # pendencia de data. Sem ano, o ano sai da data de criacao do card.
        try:
            ref_card = dt.date.fromisoformat(str(t.get('created_at') or '')[:10])
        except ValueError:
            ref_card = None
        nas_notas = datas_de_audiencia('audiencia ' + str(t.get('notes') or ''), ref_card)
        if nas_notas and all(d < HOJE for d, _ in nas_notas):
            continue
        quando, hora, passada = None, '', False
        if lid:
            # linha do tempo do sistema primeiro; o ADVBOX so se ela estiver vazia
            hist = AND.historico_local(lid, num, DB) or historico_com_paciencia(lid)
            if hist is None:
                # sem resposta nao e "card sem data": o Diosme sumiu assim
                nao_resolvidas.append((num, 'o ADVBOX não respondeu ao ler o histórico; '
                                            'o registro anterior foi mantido'))
                continue
            for x in hist:
                try:
                    ref = dt.date.fromisoformat(str(x.get('created_at') or '')[:10])
                except ValueError:
                    ref = None
                quando, hora, passada = data_em_comentario(x.get('comments'), ref)
                if quando or passada:
                    break
        if not quando:
            if not passada:
                nao_resolvidas.append((num, re.sub(r'\s+', ' ', t.get('notes') or '')[:80]))
            continue
        achados.append({
            'processo': num, 'data': quando.isoformat(), 'hora': hora,
            'subtipo': subtipo(t.get('task')), 'modalidade': modalidade(t.get('notes')),
            'local': '', 'lawsuit_id': lid, 'fonte': 'advbox-comentario',
            'quando_fonte': str(t.get('created_at') or ''), 'redesignacao': False,
        })
    return achados, len(tarefas), nao_resolvidas


def de_intimacoes(con):
    """Intimacoes de audiencia designada que ainda estao na fila."""
    row = con.execute("SELECT dados FROM sync_cache WHERE chave='intimacoes'").fetchone()
    if not row:
        return []
    out = []
    for it in (json.loads(row[0]).get('intimacoes') or []):
        m = it.get('motor') or {}
        # 06/10/2026 (a equipe 86458): despacho composto, o ato principal e outro prazo e a
        # audiencia vem em m['audiencia']; sem isto ela nunca entrava na agenda
        if m.get('ato') != 'AUDIENCIA DESIGNADA' and not (m.get('audiencia') or {}).get('data'):
            continue
        try:
            c = CP.extrair(it.get('texto') or '', it.get('orgao') or '')
            dt.date.fromisoformat(c.get('data') or '')
        except Exception:
            continue
        if not c.get('tem') or c.get('tipo') != 'audiencia' or c['data'] < HOJE.isoformat():
            continue
        conf = m.get('conferencia') or {}
        out.append({
            'processo': it.get('processo'), 'data': c['data'], 'hora': c.get('hora') or '',
            'subtipo': conf.get('subtipo') or '',
            'modalidade': conf.get('modalidade') or c.get('modalidade') or '',
            'local': c.get('local') or '', 'lawsuit_id': None,
            'fonte': 'intimacao', 'quando_fonte': str(it.get('data') or '')[:10],
            'redesignacao': 'REDESIGN' in norm(it.get('texto')),
        })
    return out


def _data_hora_br(valor):
    """'2026-09-30T16:50:00+00:00' -> ('2026-09-30', '13:50'). O SYNC manda a
    hora da pauta em UTC (aviso da Advogada, 28/09/2026)."""
    try:
        from zoneinfo import ZoneInfo
        fuso = ZoneInfo('America/Sao_Paulo')
    except Exception:
        fuso = dt.timezone(dt.timedelta(hours=-3))
    s = str(valor or '')
    try:
        d = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return s[:10], ''
    if d.tzinfo is not None:
        d = d.astimezone(fuso)
    return d.strftime('%Y-%m-%d'), (d.strftime('%H:%M') if len(s) > 10 else '')


def de_sync(con):
    """Pauta do tribunal que o SYNC traz no painel (audiencias proximas).

    Pegou em 28/09/2026 duas audiencias que nao estavam no ADVBOX, na agenda
    nem nas intimacoes: 9482677-14.2026.5.02.3844 (02/10) e
    9556581-40.2023.5.10.9709 (05/10). O local que vem aqui e a vara, nao o
    endereco nem o link: nao vai para o lembrete do cliente (que diria "Local:
    3a Vara" numa audiencia virtual).
    """
    row = con.execute("SELECT dados FROM sync_cache WHERE chave='dashboard'").fetchone()
    if not row:
        return []
    out = []
    for a in ((json.loads(row[0]).get('audiencias') or {}).get('proximas') or []):
        num = str(a.get('processo') or '').strip()
        if not CNJ.fullmatch(num):
            continue
        dia, hora = _data_hora_br(a.get('data_audiencia') or a.get('data'))
        if not dia or dia < HOJE.isoformat():
            continue
        desc = a.get('descricao') or ''
        # tudo antes do "(dd/mm/aaaa hh:mm ...)": "de conciliacao (conhecimento) por
        # videoconferencia" tem parentese antes, e cortar no primeiro perdia a modalidade
        cabeca = re.sub(r'\(\s*\d{2}/\d{2}/\d{4}.*$', '', desc)
        out.append({
            'processo': num, 'data': dia, 'hora': hora, 'subtipo': subtipo(cabeca),
            'modalidade': modalidade(cabeca), 'local': '', 'lawsuit_id': None,
            'fonte': 'sync', 'quando_fonte': HOJE.isoformat(),
            'redesignacao': 'REDESIGN' in norm(desc),
        })
    return out


def _dia_de(valor):
    """criado_em das tarefas: carimbo Unix na tela, texto em alguns robos."""
    try:
        return time.strftime('%Y-%m-%d', time.localtime(float(valor)))
    except (TypeError, ValueError):
        return str(valor or '')[:10]


def de_tarefas_locais(con):
    """Tarefas de audiencia lancadas no proprio sistema (tipo comeca com AUDIENCIA).
    As que vieram do ADVBOX ja entram por de_advbox()."""
    out = []
    try:
        linhas = con.execute("SELECT * FROM tarefas WHERE status='aberta' "
                             "AND COALESCE(origem,'') != 'advbox'").fetchall()
    except sqlite3.OperationalError:
        return out
    for t in linhas:
        if not norm(t['tipo']).startswith('AUDIENCIA'):
            continue
        num = (t['processo_numero'] or '').strip()
        if not CNJ.fullmatch(num):
            continue
        texto = '%s %s' % (t['titulo'] or '', t['descricao'] or '')
        quando, hora = None, ''
        try:
            d = dt.date.fromisoformat(str(t['data'] or '')[:10])
            if HOJE <= d <= HOJE + dt.timedelta(days=JANELA_DIAS):
                quando, hora = d, (t['hora'] or '')
        except ValueError:
            pass
        if not quando:
            quando, hora = data_futura(texto)
        if not quando:
            continue
        out.append({
            'processo': num, 'data': quando.isoformat(), 'hora': hora,
            'subtipo': subtipo(t['tipo']), 'modalidade': modalidade(texto), 'local': '',
            'lawsuit_id': None, 'fonte': 'tarefa',
            'quando_fonte': _dia_de(t['criado_em']),
            'redesignacao': bool(_REDESIGNA.search(norm(texto))),
        })
    return out


def de_sistema(con):
    """O que a equipe lancou ou corrigiu na tela: manda sobre as outras fontes."""
    out = []
    for r in con.execute("SELECT * FROM audiencia_registro WHERE editado_local=1 "
                         "AND status='ativa' AND data>=?", (HOJE.isoformat(),)):
        out.append({
            'processo': r['processo'], 'data': r['data'], 'hora': r['hora'] or '',
            'subtipo': r['subtipo'] or 'audiencia', 'modalidade': r['modalidade'] or '',
            'local': r['local'] or '', 'lawsuit_id': r['lawsuit_id'], 'fonte': 'sistema',
            'quando_fonte': str(r['atualizado_em'] or '')[:10], 'redesignacao': False,
        })
    return out


def de_agendas():
    """Eventos de audiencia com numero de processo, em qualquer agenda do token."""
    out = []
    try:
        svc = AG.servico()
        cals = svc.calendarList().list().execute().get('items') or []
    except Exception as e:
        print('  agenda Google: %s' % str(e)[:80])
        return out
    for cal in cals:
        nome = cal.get('summary') or ''
        if re.search(r'feriad|famil', nome, re.I):
            continue
        try:
            itens = svc.events().list(
                calendarId=cal['id'], timeMin=HOJE.isoformat() + 'T00:00:00Z',
                timeMax=(HOJE + dt.timedelta(days=JANELA_DIAS)).isoformat() + 'T00:00:00Z',
                singleEvents=True, orderBy='startTime', maxResults=500).execute().get('items') or []
        except Exception:
            continue
        for e in itens:
            titulo = e.get('summary') or ''
            t = norm(titulo)
            if 'AUDIENCIA' not in t or t.split('_')[0] in ('PRAZO', 'PF PRAZO'):
                continue
            desc = e.get('description') or ''
            m = CNJ.search(titulo + ' ' + desc)
            if not m:
                continue   # sem processo nao ha a quem lembrar
            ini = e.get('start') or {}
            out.append({
                'processo': m.group(0),
                'data': ini.get('date') or (ini.get('dateTime') or '')[:10],
                # dia inteiro vem 00:00: hora falsa nao pode ir ao cliente
                'hora': ((ini.get('dateTime') or '')[11:16]).replace('00:00', ''),
                'subtipo': subtipo(titulo),
                'modalidade': modalidade(titulo + ' ' + desc + ' ' + (e.get('location') or '')),
                'local': e.get('location') or '', 'lawsuit_id': None,
                'fonte': 'agenda', 'quando_fonte': str(e.get('updated') or '')[:10],
                'redesignacao': False,
            })
    return out


# ---------------------------------------------------------------------------
# consolidacao: uma linha por audiencia
# ---------------------------------------------------------------------------
def consolidar(achados):
    """Uma audiencia por data. Fontes discordando viram aviso, nao silencio.

    Regras, na ordem:
      - o que a equipe lancou ou corrigiu na tela (fonte 'sistema'): vale ele,
        e a data diferente das outras fontes vira aviso;
      - todas as fontes com a mesma data: vale ela;
      - o ADVBOX (ou tarefa do sistema) tem duas tarefas de tipos diferentes
        (inicial e instrucao) com datas diferentes: sao duas, valem as duas;
      - alguma tarefa registra redesignacao: vale a mais recente;
      - a pauta oficial (tribunal pelo SYNC, ou a intimacao) tem data: vale a
        mais recente dela;
      - senao vale a do ADVBOX, porque e a equipe quem mantem, e a divergencia
        vai escrita na tarefa da Dra. Advogada.
    """
    por_proc = {}
    for a in achados:
        por_proc.setdefault(a['processo'], []).append(a)
    final = []
    for proc, lst in por_proc.items():
        datas = sorted({a['data'] for a in lst})
        sis = sorted({a['data'] for a in lst if a['fonte'] == 'sistema'})
        adv = [a for a in lst if a['fonte'] in ('advbox', 'tarefa')]
        ofi = sorted((a for a in lst if a['fonte'] in ('sync', 'intimacao')),
                     key=lambda a: a['quando_fonte'])
        por_sub = {}
        for a in adv:
            por_sub.setdefault(a['subtipo'], set()).add(a['data'])
        diverg = ''
        if sis:
            escolhidas = sis
            outras = [d for d in datas if d not in sis]
            if outras:
                diverg = ('Lançada ou corrigida no sistema; outra fonte traz %s. Conferir.'
                          % ', '.join(br(d) for d in outras))
        elif len(datas) == 1:
            escolhidas = datas
        elif (len(por_sub) > 1 and all(len(v) == 1 for v in por_sub.values())
              and len({min(v) for v in por_sub.values()}) == len(por_sub)):
            escolhidas = sorted(min(v) for v in por_sub.values())
        else:
            redes = sorted((a for a in adv if a['redesignacao']),
                           key=lambda a: a['quando_fonte'])
            if redes:
                escolhidas = [redes[-1]['data']]
                diverg = ('Fontes com datas diferentes (%s); valeu a redesignação registrada '
                          'na tarefa.' % ', '.join(br(d) for d in datas))
            elif ofi:
                escolhidas = [ofi[-1]['data']]
                diverg = ('Fontes com datas diferentes (%s); valeu a pauta do tribunal. '
                          'Conferir e corrigir a tarefa.' % ', '.join(br(d) for d in datas))
            else:
                base = sorted({a['data'] for a in adv}) or datas
                escolhidas = [base[0]]
                diverg = ('Fontes com datas diferentes (%s). Conferir qual vale antes '
                          'de avisar o cliente.' % ', '.join(br(d) for d in datas))
        for d in escolhidas:
            mesmas = [a for a in lst if a['data'] == d]

            def primeiro(campo, ordem):
                for f in ordem:
                    for a in mesmas:
                        if a['fonte'] == f and a.get(campo):
                            return a[campo]
                return ''

            final.append({
                'processo': proc, 'data': d,
                # hora, modalidade e local: o despacho e a fonte oficial
                'hora': primeiro('hora', ('sistema', 'intimacao', 'sync', 'agenda', 'advbox',
                                          'tarefa', 'advbox-comentario')),
                'modalidade': primeiro('modalidade', ('sistema', 'intimacao', 'advbox', 'tarefa',
                                                      'sync', 'agenda', 'advbox-comentario')),
                'local': primeiro('local', ('sistema', 'intimacao', 'agenda')),
                'subtipo': primeiro('subtipo', ('sistema', 'advbox', 'tarefa', 'advbox-comentario',
                                                'intimacao', 'sync', 'agenda')) or 'audiencia',
                'lawsuit_id': primeiro('lawsuit_id', ('advbox', 'advbox-comentario', 'sistema')),
                'fontes': sorted({a['fonte'] for a in mesmas}),
                'divergencia': diverg or (
                    'Data lida de um comentário do ADVBOX, não do card de audiência: '
                    'corrigir o card.' if all(a['fonte'] == 'advbox-comentario' for a in mesmas)
                    else ''),
            })
    return final


# ---------------------------------------------------------------------------
# o que o sistema consegue ver para o checklist
# ---------------------------------------------------------------------------
_drive = None
_sync_todas = None


def roteiro_no_drive(url):
    """Arquivo com "roteiro" no nome na pasta do caso, ate duas subpastas."""
    global _drive
    m = re.search(r'folders/([A-Za-z0-9_-]+)', url or '')
    if not m:
        return None, 'pasta do caso não está cadastrada'
    try:
        if _drive is None:
            from app.portal.dados import _drive_service
            _drive = _drive_service()

        def filhos(fid):
            return _drive.files().list(
                q="'%s' in parents and trashed=false" % fid,
                fields='files(id,name,mimeType,modifiedTime)', pageSize=200,
                supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get('files', [])

        fila = [(x, 0) for x in filhos(m.group(1))]
        achou, vistos = [], 0
        while fila and vistos < 500:
            x, prof = fila.pop(0)
            vistos += 1
            if 'ROTEIRO' in norm(x['name']):
                achou.append(x)
            if 'folder' in x['mimeType'] and prof < 2:
                fila += [(y, prof + 1) for y in filhos(x['id'])]
        return achou, None
    except Exception as e:
        return None, 'o Drive não respondeu (%s)' % str(e)[:60]


def intimacoes_do_processo(numero):
    """Textos que o SYNC ainda guarda desse processo. Ele guarda cerca de um mes."""
    global _sync_todas
    h = {'Authorization': 'Bearer ' + os.environ.get('SYNC_API_TOKEN', '')}
    if _sync_todas is None:
        _sync_todas = []
        for pag in range(1, 30):
            try:
                d = requests.get(SYNC + '/intimacoes', headers=h, timeout=60,
                                 params={'status': 'todas', 'pagina': pag}).json() or {}
            except Exception:
                break
            lote = d.get('intimacoes') or []
            _sync_todas += lote
            if not lote or len(_sync_todas) >= (d.get('total') or 0):
                break
    out = []
    for it in _sync_todas:
        if it.get('processo') != numero:
            continue
        txt = it.get('texto') or ''
        if txt.rstrip().endswith(('…', '...')):
            try:
                txt = (requests.get(SYNC + '/intimacoes/%d' % int(it['id']), headers=h,
                                    timeout=60).json() or {}).get('texto') or txt
            except Exception:
                pass
        out.append((str(it.get('data') or '')[:10], txt))
    return out


def despacho_sobre_modalidade(textos):
    """Primeiro deferimento ou indeferimento que fala de modalidade da audiencia."""
    for data, txt in sorted(textos, reverse=True):
        t = norm(txt)
        for m in re.finditer(r'INDEFIRO|INDEFERID|DEFIRO|DEFERID|DEFERIMENTO', t):
            janela = t[max(0, m.start() - 250):m.end() + 250]
            if re.search(r'TELEPRESENCIAL|VIRTUAL|VIDEOCONFER|HIBRID|PRECATORI|REMOT', janela):
                return data, ('indeferiu' if 'INDEF' in m.group(0) else 'deferiu')
    return None


def cliente_do_processo(con, numero):
    p = con.execute('SELECT id FROM jur_processos WHERE numero=?', (numero,)).fetchone()
    if not p:
        return {}
    try:
        par = con.execute(
            "SELECT nome FROM processo_contato WHERE ativo=1 AND tipo='cliente_do_parceiro' "
            "AND (processo_numero=? OR cliente_id IN "
            "(SELECT cliente_id FROM jur_processo_partes WHERE processo_id=?))",
            (numero, p['id'])).fetchone()
    except sqlite3.OperationalError:
        par = None
    for r in con.execute(
            "SELECT cl.nome, cl.celular, cl.telefone FROM jur_processo_partes pp "
            "JOIN jur_clientes cl ON cl.id = pp.cliente_id WHERE pp.processo_id=? "
            "AND pp.cliente_id IS NOT NULL AND UPPER(COALESCE(pp.papel,'')) NOT LIKE 'PARTE CONTR%'",
            (p['id'],)):
        return {'nome': r['nome'], 'telefone': (r['celular'] or r['telefone'] or '').strip(),
                'parceiro': par['nome'] if par else None}
    return {'parceiro': par['nome'] if par else None}


def existe_no_whatsapp(con, tel):
    # 24/09/2026: a consulta phone-exists era da Z-API, que caiu; a D-API do escritorio nao tem
    # essa consulta aqui. None = "nao foi possivel conferir" (nunca afirmar que nao existe).
    return None


def checklist(con, aud):
    """Texto da tarefa e os alertas que merecem aparecer no resumo do dia."""
    num = aud['processo']
    alertas = []
    tit = con.execute('SELECT cliente, parte_adversa, pasta_drive FROM advbox_titularidade '
                      'WHERE numero=?', (num,)).fetchone()
    hist = []
    sem_resposta = False
    # 28/09/2026: a linha do tempo do processo (jur_andamentos: copia do ADVBOX
    # feita toda madrugada + o que a equipe escreve no sistema) vem primeiro.
    # O ADVBOX so e chamado se nao houver nada guardado do processo.
    h = AND.historico_local(aud.get('lawsuit_id'), num, DB)
    if not h and aud.get('lawsuit_id'):
        h = historico_com_paciencia(aud['lawsuit_id'])
    if h is None:
        sem_resposta = True
    else:
        hist = list(reversed(h))   # do mais antigo para o mais novo

    def achados(rx, n):
        L = [x for x in hist if re.search(rx, norm(x.get('comments')))]
        return [(str(x.get('created_at'))[:10], re.sub(r'\s+', ' ', x.get('comments') or '')[:170])
                for x in L[-n:]]

    d = dt.date.fromisoformat(aud['data'])
    faltam = (d - HOJE).days
    rot_sub = {'inicial': 'INICIAL', 'una': 'UNA', 'instrucao': 'DE INSTRUÇÃO'}.get(aud['subtipo'], '')
    cab = ['PROVIDÊNCIAS DE AUDIÊNCIA (uma semana antes)']
    if tit:
        cab.append('%s x %s' % (N.limpar_nome(tit['cliente']).title(),
                                N.limpar_nome(tit['parte_adversa']).title()))
    cab.append('Processo %s' % num)
    cab.append('Audiência %s em %s (%s)%s%s' % (
        rot_sub, br(aud['data']), SEMANA[d.weekday()],
        ' às %s' % aud['hora'] if aud.get('hora') else '',
        ', %s' % aud['modalidade'] if aud.get('modalidade') else ''))
    if aud.get('divergencia'):
        cab.append('ATENÇÃO: %s' % aud['divergencia'])
        alertas.append('datas divergentes')
    if sem_resposta:
        cab.append('ATENÇÃO: o ADVBOX não respondeu ao ler o histórico. Os itens 1, 3 e 4 '
                   'abaixo NÃO foram conferidos pelo sistema: conferir direto no card.')
        alertas.append('histórico não lido')
    cab += ['', 'Dra. Advogada, conferir os quatro pontos e responder neste card. O sistema '
                'preencheu o que conseguiu ver; o que está como não encontrado não quer '
                'dizer que não exista, quer dizer que ele não achou.', '']

    # 1. documentos
    L = ['1. DOCUMENTOS', 'Tudo o que precisávamos juntar já está nos autos?']
    junt = achados(r'JUNTAD|JUNTAR|ROL DE TESTEMUNHA|PROTOCOLAD|DOCUMENT', 3)
    if junt:
        L.append('Últimos registros de juntada e protocolo na linha do tempo do processo:')
        L += ['  %s: %s' % (br(a), b) for a, b in junt]
        if any(re.search(r'AINDA NAO PROTOCOL|NAO PROTOCOLAD|PRONTO.{0,40}PROTOCOL', norm(b))
               for _, b in junt):
            L.append('ATENÇÃO: há registro de peça pronta e ainda não protocolada.')
            alertas.append('peça pronta sem protocolo')
    else:
        L.append('Nenhum registro de juntada ou protocolo na linha do tempo do processo.')

    # 2. roteiro
    L += ['', '2. ROTEIRO DE AUDIÊNCIA']
    arqs, erro = roteiro_no_drive(tit['pasta_drive'] if tit else '')
    if arqs:
        L += ['Encontrado na pasta do caso: "%s" (%s).' % (x['name'], br(x['modifiedTime']))
              for x in arqs[:3]]
    else:
        L.append('NÃO ENCONTRADO na pasta do caso nem nas subpastas%s. Pelo POP 18 ele fica '
                 'em ATOS INTERNOS.' % (' (%s)' % erro if erro else ''))
        alertas.append('sem roteiro')

    # 3. cliente e testemunhas
    L += ['', '3. CLIENTE E TESTEMUNHAS CIENTES']
    cli = cliente_do_processo(con, num)
    if cli.get('parceiro'):
        L.append('Cliente do parceiro: quem avisa o cliente é %s. Confirmar com ele.'
                 % cli['parceiro'])
    if cli.get('nome'):
        ex = existe_no_whatsapp(con, cli.get('telefone'))
        L.append('Cliente: %s, telefone %s: %s.' % (
            N.limpar_nome(cli['nome']).title(), cli.get('telefone') or 'NÃO CADASTRADO',
            'existe no WhatsApp' if ex else ('NÃO EXISTE no WhatsApp' if ex is False
                                            else 'não foi possível conferir')))
        if ex is False or not cli.get('telefone'):
            alertas.append('cliente sem WhatsApp')
    else:
        L.append('Nenhum cliente ligado a este processo no cadastro.')
        alertas.append('sem cliente no cadastro')
    marcos = [r[0] for r in con.execute(
        'SELECT marco FROM agenda_lembretes WHERE processo=? ORDER BY enviado_em', (num,))]
    L.append('Lembretes automáticos já enviados: %s.' % (', '.join(marcos) or 'nenhum ainda'))
    if aud['subtipo'] == 'inicial':
        L.append('Audiência inicial: nenhuma testemunha é ouvida nesta data.')
    else:
        test = achados(r'TESTEMUNHA|POP ?18', 1)
        L.append('Testemunhas, último registro na linha do tempo: %s' % (
            '%s: %s' % (br(test[0][0]), test[0][1]) if test else 'nenhum registro encontrado.'))
        if not test:
            alertas.append('sem registro de testemunha')

    # 4. modalidade
    L += ['', '4. MODALIDADE (virtual, telepresencial, híbrida ou precatória)']
    L.append('Como aparece no registro: %s.' % (aud.get('modalidade') or 'não informada'))
    pedido = achados(r'\bPEDI(DO|MOS|R)\b|SOLICIT|REQUER|REITER', 20)
    pedido = [x for x in pedido
              if re.search(r'TELEPRESENCIAL|ONLINE|HIBRID|VIRTUAL|VIDEOCONFER|PRECATORI|REMOT',
                           norm(x[1]))]
    if pedido:
        L.append('Pedido nosso de mudança: %s: %s' % (br(pedido[-1][0]), pedido[-1][1]))
        desp = despacho_sobre_modalidade(intimacoes_do_processo(num))
        if desp:
            L.append('Despacho sobre a modalidade: o juízo %s em %s.' % (desp[1].upper(), br(desp[0])))
        else:
            L.append('Despacho sobre o pedido: NÃO ENCONTRADO nas publicações que o SYNC guarda '
                     '(cerca do último mês).')
            L.append('ATENÇÃO: pedido sem despacho a %d dias da audiência. Foi o que aconteceu no '
                     'caso caso B x empresa B. Balcão virtual hoje, e decidir com a Dra. Advogada '
                     'se haverá deslocamento.' % faltam)
            alertas.append('pedido de modalidade sem despacho')
    else:
        L.append('Nenhum pedido nosso de mudança de modalidade na linha do tempo.')

    L += ['', 'Se algo não estiver pronto, escrever aqui o que falta e para quem foi encaminhado.']
    return '\n'.join(cab + L), alertas


# ---------------------------------------------------------------------------
def prazo_da_tarefa(data_aud):
    """Dois dias uteis a partir de hoje, sem passar da vespera da audiencia.

    A tarefa existe para dar tempo de reagir: pedido sem despacho, testemunha
    sumida, roteiro por fazer. Prazo no proprio dia da audiencia nao serve.
    """
    fim, n = HOJE, 2
    while n:
        fim += dt.timedelta(days=1)
        if eh_dia_util(fim):
            n -= 1
    vespera = data_aud - dt.timedelta(days=1)
    return max(HOJE, min(fim, vespera))


def _con():
    # mesmas tabelas e colunas da tela (app/audiencias_nucleo.py)
    return N.con(DB)


RESPONSAVEL_ESC = 5                      # Dra. Advogada no Sistema Escritório Modelo
TIPO_PREPARACAO = 'PREPARACAO PARA AUDIENCIA'      # sem acento (fase 2, T1); a leitura usa LIKE 'PREPARA%AUDI%' e pega as duas grafias


def preparacao_aberta(con, processo):
    """Tarefa de preparacao ja aberta para o processo, em qualquer origem (a mao,
    robo, espelho do ADVBOX). Em 28/09 a do Allan (05/10) ja tinha sido aberta a
    mao no sistema: sem isto, o vigia abriria a segunda."""
    try:
        r = con.execute("SELECT id FROM tarefas WHERE processo_numero=? AND status='aberta' "
                        "AND tipo LIKE 'PREPARA%AUDI%' ORDER BY id DESC LIMIT 1",
                        (processo,)).fetchone()
        return r['id'] if r else None
    except sqlite3.OperationalError:
        return None


def agenda_central(con, auds):
    """Poe na agenda central o que falta e leva evento nosso junto na redesignacao.
    Na simulacao o registro nao foi gravado: olha a lista consolidada de hoje."""
    try:
        eventos = AG.listar(dias_atras=2, dias_frente=JANELA_DIAS + 30)
    except Exception as e:
        print('ALERTA: agenda central indisponível (%s). Nada foi para a agenda hoje.' % str(e)[:80])
        return
    hoje = HOJE.isoformat()
    # 1. evento que o sistema criou para uma data que saiu do registro:
    #    vai para a data nova, se houver exatamente uma sem evento
    for v in con.execute("SELECT * FROM audiencia_registro WHERE status IN ('substituida') "
                         "AND evento_nosso=1 AND COALESCE(evento_id,'') != ''").fetchall():
        novas = [dict(r) for r in con.execute(
            "SELECT * FROM audiencia_registro WHERE processo=? AND status='ativa' AND data>=? "
            "AND COALESCE(evento_id,'') = ''", (v['processo'], hoje))]
        if len(novas) != 1:
            continue
        n = novas[0]
        print('  agenda: %s %s -> %s (redesignada; evento movido)' % (v['processo'], br(v['data']), br(n['data'])))
        if not APLICAR:
            continue
        try:
            N.mover_evento(v['evento_id'], n, 'vigia', True)
        except Exception as e:
            print('    FALHOU ao mover: %s' % str(e)[:100])
            continue
        con.execute("UPDATE audiencia_registro SET evento_id=?, evento_link=?, evento_nosso=1 "
                    "WHERE processo=? AND data=?", (v['evento_id'], v['evento_link'], n['processo'], n['data']))
        con.execute("UPDATE audiencia_registro SET evento_id=NULL, evento_link=NULL, evento_nosso=0 "
                    "WHERE processo=? AND data=?", (v['processo'], v['data']))
        con.commit()
    # 2. audiencia futura fora da agenda
    criadas = ligadas = conferir = 0
    if APLICAR:
        linhas = [dict(r) for r in con.execute(
            "SELECT * FROM audiencia_registro WHERE status='ativa' AND data>=? "
            "AND COALESCE(evento_id,'') = '' ORDER BY data", (hoje,))]
    else:
        ja = {(r['processo'], r['data']) for r in con.execute(
            "SELECT processo, data FROM audiencia_registro WHERE COALESCE(evento_id,'') != ''")}
        linhas = [dict(a, fontes=','.join(a['fontes'])) for a in auds
                  if a['data'] >= hoje and (a['processo'], a['data']) not in ja]
    for a in linhas:
        ev = N.achar_evento(a, eventos)
        fontes = set(str(a['fontes'] or '').split(','))
        if ev:
            ligadas += 1
            print('  agenda: %s %s já estava na agenda (ligada)' % (br(a['data']), a['processo']))
            if APLICAR:
                N.ligar(con, a, ev, nosso=(ev.get('marcador') == N.marcador(a)))
            continue
        if a['divergencia'] or fontes <= {'advbox-comentario', ''}:
            conferir += 1
            print('  agenda: %s %s FORA da agenda: conferir antes (%s)'
                  % (br(a['data']), a['processo'], a['divergencia'] or 'só em comentário'))
            continue
        criadas += 1
        print('  agenda: %s %s %s -> criar "%s"' % (br(a['data']), a['hora'] or '', a['processo'], N.titulo(a)))
        if APLICAR:
            try:
                N.agendar(con, a, eventos, por='vigia de audiências')
            except Exception as e:
                print('    FALHOU: %s' % str(e)[:120])
    if APLICAR:
        con.commit()
    print('agenda central: %d criadas, %d ligadas a evento que já existia, %d para conferir'
          % (criadas, ligadas, conferir))


def livros_do_tribunal(con, procs):
    """07/11/2026 (Advogada): o livro de cada processo no SYNC (andamentos designada/cancelada/realizada).
    Processo que falhar fica sem livro e NÃO é tocado: falha de API nunca cancela audiência."""
    procs = set(procs)
    procs |= {r[0] for r in con.execute(
        "SELECT DISTINCT processo FROM audiencia_registro WHERE data >= date('now','-30 day')")}
    for r in con.execute("SELECT tipo, processo_numero FROM tarefas WHERE status='aberta' "
                         "AND COALESCE(processo_numero,'') != ''"):
        if AS.eh_tarefa_de_audiencia(r[0]):
            m = re.search(r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}', r[1] or '')
            if m:
                procs.add(m.group(0))
    from app.portal import dados as PD
    livros, falhas = {}, 0
    for p in sorted(procs):
        try:
            d = PD.sync_get('/processos/%s' % p)
            d = d.get('data', d) if isinstance(d, dict) else {}
            lv = AS.livro((d or {}).get('movimentos'))
            if lv:
                livros[p] = lv
        except Exception as e:
            falhas += 1
            print('  livro do tribunal: %s falhou (%s); fica como está' % (p, str(e)[:80]))
        time.sleep(0.3)
    print('livro do tribunal (SYNC): %d processos lidos, %d com andamento de audiência, %d falhas'
          % (len(procs), len(livros), falhas))
    return livros


def tarefas_de_audiencia(con):
    out = []
    for r in con.execute("SELECT id, tipo, data, hora, processo_numero, evento_id FROM tarefas "
                         "WHERE status='aberta' AND COALESCE(processo_numero,'') != ''"):
        if not AS.eh_tarefa_de_audiencia(r['tipo']):
            continue
        m = re.search(r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}', r['processo_numero'] or '')
        if m and r['data']:
            resp = {x[0] for x in con.execute("SELECT usuario_id FROM tarefa_responsaveis "
                                              "WHERE tarefa_id=? AND concluido_em IS NULL", (r['id'],))}
            out.append({'id': r['id'], 'tipo': r['tipo'], 'data': str(r['data'])[:10], 'hora': r['hora'],
                        'processo': m.group(0), 'evento_id': r['evento_id'], 'resp': resp})
    return out


FLAVIA_ESC = 6


def conferencia_do_tribunal(con, livros, julgamentos, conferir_sync, mudancas):
    """Depois da agenda: evento de audiência cancelada sai da agenda (o lembrete ao cliente sai da agenda),
    a aba de tarefas acompanha o tribunal e a Advogada recebe UM resumo do que mudou e do que conferir."""
    hoje = HOJE.isoformat()
    conferir = [('conf|%s|%s|%s' % (c['processo'], c['data'], c['motivo'][:40]), '%s %s: %s' % (br(c['data']), c['processo'], c['motivo']))
                for c in conferir_sync]
    # 1. agenda: audiência cancelada pelo tribunal, com evento ainda na agenda
    for r in con.execute("SELECT * FROM audiencia_registro WHERE status IN ('cancelada','substituida') "
                         "AND data >= ? AND COALESCE(evento_id,'') != ''", (hoje,)).fetchall():
        if r['processo'] not in livros:
            continue
        if r['evento_nosso'] and r['status'] == 'cancelada':
            print('  agenda: %s %s cancelada no tribunal -> cancelar o evento do sistema' % (br(r['data']), r['processo']))
            mudancas.append('%s %s: audiência cancelada pelo tribunal; evento tirado da agenda.' % (br(r['data']), r['processo']))
            if APLICAR:
                try:
                    AG.cancelar(r['evento_id'])
                    con.execute("UPDATE audiencia_registro SET evento_id=NULL, evento_link=NULL, evento_nosso=0 "
                                "WHERE processo=? AND data=?", (r['processo'], r['data']))
                    con.commit()
                except Exception as e:
                    print('    FALHOU ao cancelar: %s' % str(e)[:100])
        else:
            conferir.append(('agenda|%s|%s' % (r['processo'], r['data']),
                             '%s %s: o tribunal %s, mas o evento continua na agenda nesta data (%s). '
                             'Cancelar ou mover na agenda.' % (
                                 br(r['data']), r['processo'],
                                 'remarcou' if r['status'] == 'substituida' else 'cancelou',
                                 'criado pela equipe' if not r['evento_nosso'] else 'o vigia não achou a data nova')))
    # 2. aba de tarefas
    acoes = AS.plano_tarefas(tarefas_de_audiencia(con), livros, hoje)
    if APLICAR:
        acoes = AS.sem_realizada_repetida(con, acoes)
    for a in acoes:
        linha = 'tarefa #%d: %s' % (a['tarefa_id'], a['comentario'])
        print('  tarefas: %s -> %s' % (a['acao'], linha))
        if a['acao'] == 'conferir':
            conferir.append(('tarefa|%d|%s' % (a['tarefa_id'], a['comentario'][:60]), linha))
        elif a['acao'] != 'realizada':          # 09/11/2026 (Advogada): a realizada fica na tarefa, para a Advogada conferir
            mudancas.append(linha)
    if APLICAR:
        n = AS.aplicar_tarefas(con, acoes, time.time())
        AS.registrar_avisos(con, [('realizada|%d' % a['tarefa_id'], '') for a in acoes if a['acao'] == 'realizada'],
                            time.strftime('%Y-%m-%d %H:%M:%S'))
        print('  tarefas: %d alteradas' % n)
    # 3. resumo da Advogada (uma tarefa só, e só se houver novidade)
    julg = [('julg|%s|%s' % (j['processo'], j['data']), '%s %s às %s: %s'
             % (br(j['data']), j['processo'], j['hora'], j['desc'][:120])) for j in julgamentos]
    if APLICAR:
        conferir, julg = AS.so_novos(con, conferir), AS.so_novos(con, julg)
    texto = AS.texto_resumo(mudancas, [t for _, t in conferir], [t for _, t in julg])
    print()
    print('RESUMO PARA A ADVOGADA: %s' % ('nenhuma novidade, não abre tarefa' if not texto else ''))
    if texto:
        print(texto)
    if APLICAR and texto:
        turno = 'tarde' if time.localtime().tm_hour >= 12 else 'manha'
        TP.criar_local('ACOMPANHAMENTO', 'Audiências conferidas no tribunal (SYNC): %d mudança(s), %d para conferir'
                       % (len(mudancas), len(conferir)), texto, [FLAVIA_ESC],
                       origem='robo', origem_ref='aud-sync|%s|%s' % (hoje, turno),
                       criado_por='Vigia de audiências (SYNC)')
        AS.registrar_avisos(con, conferir + julg, time.strftime('%Y-%m-%d %H:%M:%S'))


def main():
    con = _con()
    print('=' * 72)
    print('VIGIA DE AUDIÊNCIAS %s %s' % (HOJE.isoformat(), '(APLICAR)' if APLICAR else '(simulação)'))

    adv, total_tarefas, sem_data = de_advbox()
    intim = de_intimacoes(con)
    sync = de_sync(con)
    tar = de_tarefas_locais(con)
    sis = de_sistema(con)
    agenda = de_agendas()
    print('fontes: ADVBOX %d (de %d tarefas lidas) | tribunal (SYNC) %d | intimações %d | '
          'tarefas do sistema %d | lançadas no sistema %d | agendas %d'
          % (len(adv), total_tarefas, len(sync), len(intim), len(tar), len(sis), len(agenda)))
    adv_ok = total_tarefas > 0
    if not adv_ok:
        # ADVBOX sem resposta nao pode virar "nenhuma audiencia": foi assim que
        # o robo de lembretes ficou mudo por 11 dias. As outras fontes seguem,
        # mas nesta rodada nada e sobrescrito nem sai do registro.
        print('ALERTA: o ADVBOX não devolveu nenhuma tarefa. Só entra audiência nova; '
              'nada é sobrescrito nem sai do registro hoje.')

    auds = consolidar(adv + intim + sync + tar + sis + agenda)
    for a in auds:
        cli, adv_nome, lid = N.partes(con, a['processo'])
        a['lawsuit_id'] = a['lawsuit_id'] or lid
        a['cliente'], a['adversa'] = cli, adv_nome

    # 07/11/2026 (Advogada): o livro do tribunal corrige a lista (cancelada e realizada saem, remarcada entra)
    livros = livros_do_tribunal(con, {a['processo'] for a in auds})
    auds, novas_sync, julgamentos_sync, conferir_sync = AS.ajustar_auds(auds, livros, HOJE.isoformat())
    for n in novas_sync:
        cli, adv_nome, lid = N.partes(con, n['processo'])
        auds.append({'processo': n['processo'], 'data': n['data'], 'hora': n['hora'],
                     'modalidade': modalidade(n['tipo']), 'local': '',
                     'subtipo': subtipo(n['tipo']) or 'audiencia', 'lawsuit_id': lid,
                     'fontes': ['sync'], 'divergencia': '', 'cliente': cli, 'adversa': adv_nome})
        print('  livro do tribunal: %s %s %s designada no tribunal, entra na lista' % (br(n['data']), n['hora'], n['processo']))
    # 09/11/2026 (Advogada): mesmo horário exato em processos diferentes vai para a conferência da Advogada
    conferir_sync += AS.conflitos_de_horario(auds, livros)
    mudancas_sync = []
    auds.sort(key=lambda a: a['data'])
    print()
    print('AUDIÊNCIAS FUTURAS: %d' % len(auds))
    for a in auds:
        print('  %s %-5s | %-11s %-10s | %s | %-30s | fontes %s%s' % (
            br(a['data']), a['hora'] or '', a['subtipo'], a['modalidade'] or '-',
            a['processo'], (a['cliente'] or '')[:30], '+'.join(a['fontes']),
            ' | ' + a['divergencia'] if a['divergencia'] else ''))

    if sem_data:
        print()
        print('CARDS DE AUDIÊNCIA SEM DATA OU NÃO CONFERIDOS: %d' % len(sem_data))
        for num, nota in sem_data:
            print('  %s | %s' % (num, nota))

    if PREVIA:
        alvo = [a for a in auds if a['processo'] == PREVIA]
        if not alvo:
            print('\nprocesso %s não tem audiência futura no registro' % PREVIA)
        for a in alvo:
            texto, al = checklist(con, a)
            print('\n' + '-' * 72 + '\n' + texto + '\n' + '-' * 72)
            print('alertas: %s | prazo da tarefa: %s' % (al or 'nenhum', br(prazo_da_tarefa(
                dt.date.fromisoformat(a['data'])).isoformat())))
        return 0

    if APLICAR:
        agora = time.strftime('%Y-%m-%d %H:%M:%S')

        # linha lancada/corrigida na tela (editado_local=1): hora, tipo,
        # modalidade, local e status sao da equipe, nao do vigia
        def guarda(campo):
            return ('%s=CASE WHEN COALESCE(editado_local,0)=1 THEN %s ELSE excluded.%s END'
                    % (campo, campo, campo))

        sql = ("INSERT INTO audiencia_registro (processo, data, hora, subtipo, modalidade, local, "
               "cliente, adversa, lawsuit_id, fontes, divergencia, status, visto_em, origem) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,'ativa',?,'vigia') ")
        if adv_ok:
            sql += ("ON CONFLICT(processo, data) DO UPDATE SET %s, %s, %s, %s, cliente=excluded.cliente, "
                    "adversa=excluded.adversa, lawsuit_id=COALESCE(excluded.lawsuit_id, lawsuit_id), "
                    "fontes=excluded.fontes, divergencia=excluded.divergencia, "
                    "status=CASE WHEN COALESCE(editado_local,0)=1 THEN status ELSE 'ativa' END, "
                    "visto_em=excluded.visto_em"
                    % (guarda('hora'), guarda('subtipo'), guarda('modalidade'), guarda('local')))
        else:
            sql += "ON CONFLICT(processo, data) DO UPDATE SET visto_em=excluded.visto_em"
        for a in auds:
            con.execute(sql, (a['processo'], a['data'], a['hora'], a['subtipo'], a['modalidade'],
                              a['local'], a['cliente'], a['adversa'], a['lawsuit_id'],
                              ','.join(a['fontes']), a['divergencia'], agora))
        if adv_ok:
            # Processo visto hoje: outras datas futuras dele saem ja, como
            # substituidas (redesignacao). Isso impede lembrete com a data velha.
            for proc in {a['processo'] for a in auds}:
                validas = [a['data'] for a in auds if a['processo'] == proc]
                con.execute("UPDATE audiencia_registro SET status='substituida' "
                            "WHERE processo=? AND data>=? AND status='ativa' AND COALESCE(editado_local,0)=0 "
                            "AND data NOT IN (%s)" % ','.join('?' * len(validas)),
                            [proc, HOJE.isoformat()] + validas)
            # Processo NAO visto hoje: pode ser rodada ruim do ADVBOX. So cai depois
            # de tres dias sem aparecer; uma falha de API nao apaga audiencia.
            corte = (dt.datetime.now() - dt.timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')
            con.execute("UPDATE audiencia_registro SET status='nao_vista' "
                        "WHERE data>=? AND status='ativa' AND COALESCE(editado_local,0)=0 AND visto_em < ?",
                        (HOJE.isoformat(), corte))
        con.commit()

    # 07/11/2026 (Advogada): o estado escrito pelo tribunal vale no registro (cancelada, realizada, remarcada).
    # Vem ANTES da agenda: assim o evento da data cancelada vai para a data nova.
    reg = [dict(r) for r in con.execute(
        "SELECT processo, data, status, COALESCE(editado_local,0) AS editado_local FROM audiencia_registro")]
    for p, d, s, motivo in AS.plano_registro(reg, livros, HOJE.isoformat()):
        print('  registro: %s %s -> %s (%s)' % (br(d), p, s, motivo[:90]))
        mudancas_sync.append('%s %s: registro passa a %s (%s).' % (br(d), p, s, motivo[:110]))
        if APLICAR:
            con.execute("UPDATE audiencia_registro SET status=?, obs=TRIM(COALESCE(obs,'') || ' | ' || ?), "
                        "atualizado_por='vigia (SYNC)', atualizado_em=? WHERE processo=? AND data=?",
                        (s, motivo[:200], time.strftime('%Y-%m-%d %H:%M:%S'), p, d))
    if APLICAR:
        con.commit()

    # agenda central: o que falta entra, evento nosso acompanha a redesignacao
    print()
    agenda_central(con, auds)

    # 07/11/2026: agenda, aba de tarefas e resumo da Advogada acompanham o tribunal
    print()
    conferencia_do_tribunal(con, livros, julgamentos_sync, conferir_sync, mudancas_sync)

    # tarefa da Dra. Advogada a uma semana
    feitas = {(r['processo'], r['data']) for r in
              con.execute('SELECT processo, data FROM audiencia_providencia')}
    fora = {(r['processo'], r['data']) for r in con.execute(
        "SELECT processo, data FROM audiencia_registro WHERE status IN "
        "('cancelada','realizada','redesignada','substituida')")}
    # de 1 a 7 dias: no proprio dia a tarefa nao da tempo de reagir a nada, e so
    # vira ruido na fila (o vigia entrou no ar no dia da audiencia do Ataíde)
    janela = [a for a in auds
              if 1 <= (dt.date.fromisoformat(a['data']) - HOJE).days <= DIAS_ANTES
              and (a['processo'], a['data']) not in feitas
              and (a['processo'], a['data']) not in fora]
    print()
    print('TAREFAS DA DRA. ADVOGADA A ABRIR HOJE: %d' % len(janela))
    for a in janela:
        ja = preparacao_aberta(con, a['processo'])
        if ja:
            print('  %s %s: já existe tarefa de preparação aberta (#%d), não abro outra'
                  % (br(a['data']), a['processo'], ja))
            if APLICAR:
                con.execute('INSERT OR REPLACE INTO audiencia_providencia VALUES (?,?,?,?,?)',
                            (a['processo'], a['data'], 'ja existia: tarefa #%d do Sistema Escritório Modelo' % ja,
                             '', time.strftime('%Y-%m-%d %H:%M:%S')))
                con.commit()
            continue
        texto, alertas = checklist(con, a)
        prazo = prazo_da_tarefa(dt.date.fromisoformat(a['data']))
        no_sistema = TP.destino() == 'sistema' or not a['lawsuit_id']
        print('-' * 72)
        print(texto)
        print('alertas: %s | prazo: %s | vai para: %s' % (
            ', '.join(alertas) or 'nenhum', br(prazo.isoformat()),
            'Sistema Escritório Modelo' if no_sistema else 'ADVBOX (o espelho traz para o sistema)'))
        if not APLICAR:
            continue
        try:
            if no_sistema:
                tid = TP.criar_local(
                    TIPO_PREPARACAO, 'Preparação da audiência de %s' % br(a['data']), texto, [RESPONSAVEL_ESC],
                    processo_numero=a['processo'], cliente=a['cliente'] or None,
                    prazo_fatal=prazo.isoformat(), prazo_interno=prazo.isoformat(),
                    prioridade='urgente' if alertas else 'normal',
                    origem='robo', origem_ref='audiencia|%s|%s' % (a['processo'], a['data']),
                    criado_por='Vigia de audiências')
                resp = {'sistema_esc': tid}
            else:
                resp = AB.criar_publicacao(
                    lawsuit_id=a['lawsuit_id'], task_id=TASK_PREPARACAO,
                    guest_ids=[PAPEL_PRODUCAO], comments=texto, from_id=str(PAPEL_TITULAR),
                    date_deadline=prazo.isoformat(), urgent=bool(alertas))
        except Exception as e:
            print('  FALHOU: %s' % str(e)[:120])
            continue
        con.execute('INSERT OR REPLACE INTO audiencia_providencia VALUES (?,?,?,?,?)',
                    (a['processo'], a['data'], json.dumps(resp, ensure_ascii=False)[:500],
                     ', '.join(alertas), time.strftime('%Y-%m-%d %H:%M:%S')))
        con.commit()
        print('  CRIADA %s para a Dra. Advogada' % ('no Sistema Escritório Modelo' if no_sistema else 'no ADVBOX'))
    con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
