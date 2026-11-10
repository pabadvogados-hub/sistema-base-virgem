# -*- coding: utf-8 -*-
"""
Integracao Google Calendar - Agenda do Escritório Modelo Advogados (conta AGENDA_EMAIL_PRINCIPAL do .env).

Usada para cadastrar PRAZOS FATAIS (PF) da esteira de pecas. O evento nasce como
dia inteiro na data do prazo, com convidados e lembretes escalonados.

Por que um token separado: o config/token.json do intake tem apenas o escopo
`drive`. Acrescentar `calendar` nele obrigaria a refazer o OAuth de tudo e
arriscaria derrubar o intake. Aqui usamos o MESMO cliente OAuth
(config/oauth_credentials.json) com um token proprio.

Primeiro uso (abre o navegador uma unica vez):
    py INTEGRACOES/gcalendar_esc.py --auth

Teste:
    py INTEGRACOES/gcalendar_esc.py --listar
"""
import os
import sys
from datetime import date, datetime, timedelta

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar']

_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config')
CRED_PATH = os.getenv('GOOGLE_OAUTH_CREDENTIALS',
                      os.path.join(_CONFIG, 'oauth_credentials.json'))
TOKEN_PATH = os.path.join(_CONFIG, 'token_esc_calendar.json')

CALENDAR_ID = os.getenv('GCAL_ESC_ID', 'primary')
TZ = 'America/Sao_Paulo'

# Convidados padrao dos prazos fatais de protocolo
import escritorio_config as _CFG  # noqa: E402
CONVIDADOS_PF = _CFG.lista('AGENDA_CONVIDADOS')


class CalendarIndisponivel(RuntimeError):
    """Token de calendario ausente/invalido - exige `--auth` interativo."""


def autenticar(forcar_login=False, permitir_browser=True):
    """Autentica na agenda do Escritório Modelo. Gera/renova config/token_esc_calendar.json."""
    creds = None
    if os.path.exists(TOKEN_PATH) and not forcar_login:
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token and not forcar_login:
            creds.refresh(Request())
        else:
            if not permitir_browser:
                raise CalendarIndisponivel(
                    f'Sem token de calendario valido em {TOKEN_PATH}. '
                    'Rode uma vez: py INTEGRACOES/gcalendar_esc.py --auth'
                )
            if not os.path.exists(CRED_PATH):
                raise CalendarIndisponivel(f'Credenciais OAuth nao encontradas em {CRED_PATH}')
            print('Abrindo o navegador... faca login com %s e clique em Permitir.' % (_CFG.AGENDA_EMAIL_PRINCIPAL or 'a conta Google da agenda do escritorio'))
            flow = InstalledAppFlow.from_client_secrets_file(CRED_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, 'w') as f:
            f.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)


def criar_prazo_fatal(titulo, data_prazo, descricao='', convidados=None,
                      calendar_id=CALENDAR_ID, service=None):
    """
    Cria o evento de prazo fatal (dia inteiro) e convida o time.

    titulo      : ex. 'PF_RT JETRO CORREA DE SOUSA X RODOTRANSP TRANSPORTES'
    data_prazo  : date ou 'YYYY-MM-DD'
    convidados  : lista de e-mails (default CONVIDADOS_PF)
    Retorna o dict do evento criado (contem htmlLink).
    """
    if isinstance(data_prazo, str):
        data_prazo = datetime.fromisoformat(data_prazo).date()
    service = service or autenticar(permitir_browser=False)
    convidados = CONVIDADOS_PF if convidados is None else convidados

    corpo = {
        'summary': titulo,
        'description': descricao,
        # Evento de dia inteiro: `end` e exclusivo, entao soma 1 dia.
        'start': {'date': data_prazo.isoformat()},
        'end': {'date': (data_prazo + timedelta(days=1)).isoformat()},
        'attendees': [{'email': e} for e in convidados],
        'guestsCanModify': False,
        'transparency': 'opaque',
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'popup', 'minutes': 3 * 24 * 60},   # 3 dias antes
                {'method': 'popup', 'minutes': 24 * 60},       # 1 dia antes
                {'method': 'email', 'minutes': 24 * 60},
            ],
        },
    }

    return service.events().insert(
        calendarId=calendar_id, body=corpo, sendUpdates='all',
    ).execute()


def listar_eventos(dias=30, calendar_id=CALENDAR_ID, service=None):
    """Lista os proximos eventos (sanity check da autenticacao)."""
    service = service or autenticar(permitir_browser=False)
    inicio = datetime.combine(date.today(), datetime.min.time()).isoformat() + 'Z'
    fim = datetime.combine(date.today() + timedelta(days=dias), datetime.max.time()).isoformat() + 'Z'
    r = service.events().list(
        calendarId=calendar_id, timeMin=inicio, timeMax=fim,
        singleEvents=True, orderBy='startTime', maxResults=50,
    ).execute()
    return r.get('items', [])


if __name__ == '__main__':
    if '--auth' in sys.argv:
        autenticar(forcar_login='--forcar' in sys.argv)
        print(f'OK. Token salvo em {TOKEN_PATH}')
    elif '--listar' in sys.argv:
        for ev in listar_eventos():
            quando = ev['start'].get('date') or ev['start'].get('dateTime', '')[:16]
            print(f"{quando}  {ev.get('summary')}")
    else:
        print(__doc__)
