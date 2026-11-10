"""Agenda central do escritorio (Google Calendar da conta AGENDA_EMAIL_PRINCIPAL do .env).

Porte do INTEGRACOES/gcalendar_esc.py para dentro do Sistema Escritório Modelo, para que a
controladoria consiga fechar o ciclo do POP: publicacao -> conferida pela Advogada
-> prazo JA cadastrado na agenda que a equipe enxerga.

Mesma agenda que a esteira da inicial ja usa para os PF_RT, e mesmos convidados —
prazo de controladoria e prazo de inicial nao podem morar em lugares diferentes.

Token proprio (`config/token_esc_calendar.json`, escopo calendar) porque o
`config/token.json` do intake so tem escopo drive. Aqui NUNCA se abre navegador:
na VPS nao ha tela. Se o refresh falhar, levanta CalendarIndisponivel e quem
chamou decide o que dizer ao usuario.

Espelho no Outlook da Advogada (decisao do handoff, doc 01 passo 8) fica para
quando o Microsoft Graph estiver de pe — ver `espelhar_outlook()`.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

log = logging.getLogger('agenda')

SCOPES = ['https://www.googleapis.com/auth/calendar']
_CONFIG = Path(os.environ.get('ESC_CONFIG_DIR', '/opt/escritorio/config'))
TOKEN_PATH = _CONFIG / 'token_esc_calendar.json'
TZ = 'America/Sao_Paulo'

# Agenda central. 'primary' = a propria conta do token (AGENDA_EMAIL_PRINCIPAL).
CALENDAR_ID = os.environ.get('GCAL_ESC_ID', 'primary')

from .juridico.contatos import CFG as _CFG

# Quem precisa ver o prazo: AGENDA_CONVIDADOS do .env (vazio = so a agenda central).
CONVIDADOS_PF = _CFG.lista('AGENDA_CONVIDADOS')

# Equipe que pode ser convidada pela tela: EQUIPE_EMAILS do .env ("nome:email:papel:trilha").
# `trilha` (PECA/MEIO) faz o sistema ja marcar quem executa o ato.
EQUIPE = _CFG.equipe()
_POR_TRILHA = {p['trilha']: p['email'] for p in EQUIPE if p['trilha']}


def convidados_para(trilha=None, extras=None):
    """Convidados padrao do PF mais quem executa a trilha (POP: PECA->Advogada, MEIO->Advogada).

    Ate agora Dra. Advogada nunca entravam em evento nenhum — o prazo ficava so nas
    caixas do operacional e da Advogada, e quem ia executar nao via na propria agenda.
    """
    lista = list(CONVIDADOS_PF)
    dono = _POR_TRILHA.get((trilha or '').upper())
    if dono and dono not in lista:
        lista.append(dono)
    for e in (extras or []):
        e = (e or '').strip()
        if e and e not in lista:
            lista.append(e)
    return lista


# Convencao do POP: o prefixo [ok] no titulo marca prazo ja cumprido.
MARCA_CUMPRIDO = '[ok] '


def marcar_cumprido(evento_id, cumprido=True, calendar_id=None):
    """Poe/tira o prefixo [ok] no titulo, sem mexer em mais nada do evento."""
    svc = servico()
    cal = calendar_id or CALENDAR_ID
    ev = svc.events().get(calendarId=cal, eventId=evento_id).execute()
    titulo = (ev.get('summary') or '').strip()
    limpo = titulo[len(MARCA_CUMPRIDO):].lstrip() if titulo.lower().startswith('[ok]') else titulo
    novo = (MARCA_CUMPRIDO + limpo) if cumprido else limpo
    if novo == titulo:
        return ev
    return svc.events().patch(calendarId=cal, eventId=evento_id,
                              body={'summary': novo}, sendUpdates='none').execute()

_lock = threading.Lock()
_servico = None


class CalendarIndisponivel(RuntimeError):
    """Token ausente/expirado — exige `--auth` numa maquina com navegador."""


def servico(recriar=False):
    """Cliente autenticado, memorizado. Nunca abre navegador."""
    global _servico
    with _lock:
        if _servico is not None and not recriar:
            return _servico
        if not TOKEN_PATH.exists():
            raise CalendarIndisponivel(
                f'Sem token de agenda em {TOKEN_PATH}. Gere numa maquina com navegador '
                '(py INTEGRACOES/gcalendar_esc.py --auth) e copie para a VPS.')
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        if not creds.valid:
            if not (creds.expired and creds.refresh_token):
                raise CalendarIndisponivel('Token de agenda invalido e sem refresh_token.')
            try:
                creds.refresh(Request())
            except Exception as e:                     # refresh revogado pelo Google
                raise CalendarIndisponivel(f'Refresh da agenda falhou: {str(e)[:120]}')
            TOKEN_PATH.write_text(creds.to_json(), encoding='utf-8')
        _servico = build('calendar', 'v3', credentials=creds, cache_discovery=False)
        return _servico


def disponivel():
    """True se da para gravar na agenda agora (a tela usa isso para habilitar o botao)."""
    try:
        servico()
        return True
    except Exception as e:
        log.warning('agenda indisponivel: %s', e)
        return False


def titulo_pf(ato, cliente, parte_adversa, processo):
    """Padrao do POP: PF_<ATO>_<CLIENTE> X <PARTE ADVERSA>_<PROCESSO>.

    A tag PF no inicio e o que a equipe procura na agenda; mantida identica a da
    esteira da inicial para que uma busca por 'PF_' traga tudo.
    """
    partes = f"{cliente or '?'} X {parte_adversa or '?'}"
    return f"PF_{(ato or 'PRAZO').upper()}_{partes}_{processo or ''}".strip('_')


def criar_prazo(titulo, data_prazo, descricao='', convidados=None,
                calendar_id=None, marcador=None, local='', processo=''):
    """Cria o evento de dia inteiro do prazo fatal e convida o time.

    `marcador` vai em extendedProperties.private para dar para reencontrar o
    evento depois (idempotencia e reconciliacao) sem depender do titulo.
    Devolve o dict do evento (tem `id` e `htmlLink`).
    """
    if isinstance(data_prazo, str):
        data_prazo = datetime.fromisoformat(data_prazo[:10]).date()
    if not isinstance(data_prazo, date):
        raise ValueError('data_prazo invalida')
    svc = servico()
    corpo = {
        'summary': titulo,
        'description': descricao,
        # Dia inteiro: `end` e exclusivo, entao soma 1 dia.
        'start': {'date': data_prazo.isoformat()},
        'end': {'date': (data_prazo + timedelta(days=1)).isoformat()},
        'attendees': [{'email': e} for e in (CONVIDADOS_PF if convidados is None else convidados)],
        'guestsCanModify': False,
        'transparency': 'opaque',
        'reminders': {'useDefault': False, 'overrides': [
            {'method': 'popup', 'minutes': 3 * 24 * 60},
            {'method': 'popup', 'minutes': 24 * 60},
            {'method': 'email', 'minutes': 24 * 60},
        ]},
    }
    # location e o campo que o lembrete do cliente le para dizer o endereco
    # (ou o link da sala). Sem gravar aqui, ele chegava sempre vazio.
    if local:
        corpo['location'] = local
    priv = {}
    if marcador:
        priv['esc'] = marcador
    if processo:
        priv['processo'] = processo
    if priv:
        corpo['extendedProperties'] = {'private': priv}
    return svc.events().insert(calendarId=calendar_id or CALENDAR_ID,
                               body=corpo, sendUpdates='all').execute()


def criar_audiencia(titulo, data, hora=None, duracao_min=90, descricao='',
                    convidados=None, calendar_id=None, marcador=None,
                    local='', processo=''):
    """Audiencia com HORA marcada (nao e prazo de dia inteiro).

    Sem hora no texto, cai para dia inteiro — melhor um evento sem horario do que
    um horario inventado. Lembretes mais curtos que os de prazo: para audiencia o
    que importa e o aviso da vespera e o da hora.
    """
    if isinstance(data, str):
        data = datetime.fromisoformat(data[:10]).date()
    svc = servico()
    if hora:
        h, m = (int(x) for x in str(hora).split(':')[:2])
        ini = datetime(data.year, data.month, data.day, h, m)
        fim = ini + timedelta(minutes=duracao_min)
        quando = {'start': {'dateTime': ini.isoformat(), 'timeZone': TZ},
                  'end': {'dateTime': fim.isoformat(), 'timeZone': TZ}}
        lembretes = [{'method': 'popup', 'minutes': 24 * 60},
                     {'method': 'popup', 'minutes': 60},
                     {'method': 'email', 'minutes': 3 * 24 * 60}]
    else:
        quando = {'start': {'date': data.isoformat()},
                  'end': {'date': (data + timedelta(days=1)).isoformat()}}
        lembretes = [{'method': 'popup', 'minutes': 24 * 60},
                     {'method': 'email', 'minutes': 3 * 24 * 60}]
    corpo = {'summary': titulo, 'description': descricao,
             'attendees': [{'email': e} for e in (CONVIDADOS_PF if convidados is None else convidados)],
             'guestsCanModify': False, 'transparency': 'opaque',
             'reminders': {'useDefault': False, 'overrides': lembretes}}
    corpo.update(quando)
    # location e o campo que o lembrete do cliente le para dizer o endereco
    # (ou o link da sala). Sem gravar aqui, ele chegava sempre vazio.
    if local:
        corpo['location'] = local
    priv = {}
    if marcador:
        priv['esc'] = marcador
    if processo:
        priv['processo'] = processo
    if priv:
        corpo['extendedProperties'] = {'private': priv}
    return svc.events().insert(calendarId=calendar_id or CALENDAR_ID,
                               body=corpo, sendUpdates='all').execute()


def achar_por_marcador(marcador, calendar_id=None):
    """Evento ja criado para este marcador, se existir. Evita duplicar no duplo clique."""
    try:
        r = servico().events().list(
            calendarId=calendar_id or CALENDAR_ID,
            privateExtendedProperty=f'esc={marcador}',
            showDeleted=False, maxResults=5).execute()
        itens = [e for e in (r.get('items') or []) if e.get('status') != 'cancelled']
        return itens[0] if itens else None
    except Exception as e:
        log.warning('achar_por_marcador %s: %s', marcador, e)
        return None


def cancelar(evento_id, calendar_id=None):
    servico().events().delete(calendarId=calendar_id or CALENDAR_ID,
                              eventId=evento_id, sendUpdates='all').execute()
    return True


def listar(dias_atras=15, dias_frente=120, calendar_id=None):
    """Eventos reais da agenda central — o que a equipe ve de fato.

    Serve para a aba Agenda distinguir "prazo calculado pelo motor" de "prazo que
    JA esta cadastrado", que era justamente o que faltava.
    """
    agora = datetime.utcnow()
    r = servico().events().list(
        calendarId=calendar_id or CALENDAR_ID,
        timeMin=(agora - timedelta(days=dias_atras)).isoformat() + 'Z',
        timeMax=(agora + timedelta(days=dias_frente)).isoformat() + 'Z',
        singleEvents=True, orderBy='startTime', maxResults=500).execute()
    out = []
    for e in r.get('items') or []:
        ini = e.get('start') or {}
        quando = ini.get('date') or (ini.get('dateTime') or '')[:10]
        if not quando:
            continue
        out.append({
            'evento_id': e.get('id'),
            'titulo': e.get('summary') or '(sem titulo)',
            'data': quando,
            'hora': (ini.get('dateTime') or '')[11:16] or None,
            'link': e.get('htmlLink'),
            'dia_inteiro': bool(ini.get('date')),
            'marcador': ((e.get('extendedProperties') or {}).get('private') or {}).get('esc'),
            'processo': ((e.get('extendedProperties') or {}).get('private') or {}).get('processo'),
            'local': e.get('location') or '',
            'convidados': [a.get('email') for a in (e.get('attendees') or [])],
        })
    return out


def detalhe(evento_id, calendar_id=None):
    """Evento completo, para a tela abrir quando a pessoa clica no compromisso."""
    e = servico().events().get(calendarId=calendar_id or CALENDAR_ID, eventId=evento_id).execute()
    ini, fim = e.get('start') or {}, e.get('end') or {}
    return {
        'evento_id': e.get('id'), 'titulo': e.get('summary') or '(sem título)',
        'descricao': e.get('descricao') or e.get('description') or '',
        'data': ini.get('date') or (ini.get('dateTime') or '')[:10],
        'hora': (ini.get('dateTime') or '')[11:16] or None,
        'fim': (fim.get('dateTime') or '')[11:16] or None,
        'dia_inteiro': bool(ini.get('date')),
        'local': e.get('location'), 'link': e.get('htmlLink'),
        'cumprido': (e.get('summary') or '').lower().startswith('[ok]'),
        'marcador': ((e.get('extendedProperties') or {}).get('private') or {}).get('esc'),
        'processo': ((e.get('extendedProperties') or {}).get('private') or {}).get('processo'),
        'convidados': [{'email': a.get('email'), 'resposta': a.get('responseStatus')}
                       for a in (e.get('attendees') or [])],
        'criado_em': e.get('created'), 'atualizado_em': e.get('updated'),
        # o Google nao apaga evento: marca cancelled e o get() ainda
        # devolve 200. Quem consome precisa saber disso.
        'cancelado': e.get('status') == 'cancelled',
    }


def atualizar(evento_id, titulo=None, data=None, hora=None, duracao_min=90,
              descricao=None, convidados=None, local=None, calendar_id=None):
    """Edita o compromisso ja cadastrado. So mexe no que foi informado."""
    svc = servico()
    cal = calendar_id or CALENDAR_ID
    corpo = {}
    if titulo is not None:
        corpo['summary'] = titulo
    if descricao is not None:
        corpo['description'] = descricao
    if local is not None:
        corpo['location'] = local
    if convidados is not None:
        corpo['attendees'] = [{'email': e} for e in convidados]
    if data:
        d = datetime.fromisoformat(str(data)[:10]).date()
        if hora:
            h, m = (int(x) for x in str(hora).split(':')[:2])
            ini = datetime(d.year, d.month, d.day, h, m)
            corpo['start'] = {'dateTime': ini.isoformat(), 'timeZone': TZ}
            corpo['end'] = {'dateTime': (ini + timedelta(minutes=duracao_min)).isoformat(),
                            'timeZone': TZ}
        else:
            corpo['start'] = {'date': d.isoformat()}
            corpo['end'] = {'date': (d + timedelta(days=1)).isoformat()}
    if not corpo:
        return detalhe(evento_id, cal)
    svc.events().patch(calendarId=cal, eventId=evento_id, body=corpo,
                       sendUpdates='all').execute()
    return detalhe(evento_id, cal)


def criar_manual(titulo, data, hora=None, duracao_min=60, descricao='', convidados=None,
                 local=None, processo=None, calendar_id=None):
    """Compromisso lancado a mao pela tela da Agenda (reuniao, diligencia, pericia...).

    Nao carrega marcador de intimacao — nao veio de publicacao nenhuma. Se a pessoa
    informar o processo, ele fica no evento para a tela conseguir religar depois.
    """
    if isinstance(data, str):
        data = datetime.fromisoformat(data[:10]).date()
    svc = servico()
    if hora:
        h, m = (int(x) for x in str(hora).split(':')[:2])
        ini = datetime(data.year, data.month, data.day, h, m)
        quando = {'start': {'dateTime': ini.isoformat(), 'timeZone': TZ},
                  'end': {'dateTime': (ini + timedelta(minutes=duracao_min)).isoformat(), 'timeZone': TZ}}
        lembretes = [{'method': 'popup', 'minutes': 24 * 60}, {'method': 'popup', 'minutes': 30}]
    else:
        quando = {'start': {'date': data.isoformat()},
                  'end': {'date': (data + timedelta(days=1)).isoformat()}}
        lembretes = [{'method': 'popup', 'minutes': 24 * 60}]
    corpo = {'summary': titulo, 'description': descricao, 'location': local,
             'attendees': [{'email': e} for e in (convidados or CONVIDADOS_PF)],
             'guestsCanModify': False, 'transparency': 'opaque',
             'reminders': {'useDefault': False, 'overrides': lembretes}}
    corpo.update(quando)
    if processo:
        corpo['extendedProperties'] = {'private': {'processo': processo}}
    return svc.events().insert(calendarId=calendar_id or CALENDAR_ID, body=corpo,
                               sendUpdates='all').execute()


def espelhar_outlook(titulo, data_prazo, descricao=''):
    """Espelho no Outlook da Advogada com categoria PF (handoff, doc 01 passo 8).

    Ainda NAO implementado: exige app registration no Azure + Microsoft Graph, que
    e trabalho de infra pendente. Devolve False para o chamador registrar que o
    espelho nao aconteceu — nunca finge que gravou.
    """
    return False
