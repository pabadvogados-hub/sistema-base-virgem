# -*- coding: utf-8 -*-
"""Prazo fatal interno (PFI) e marcacao na agenda do responsavel.

DE ONDE VEIO ESTA REGRA. Lendo 18 meses de tarefas do ADVBOX (set/2026), o
escritorio ja fazia isso na mao, escrito em prosa dentro da tarefa:

    "Advogada, PROTOCOLO dos embargos ... Data desta tarefa = prazo fatal
     INTERNO: 24/09/2026. Prazo processual: 28/09/2026 (5 dias uteis)"

Nunca houve campo para isso la, entao o prazo de verdade vivia no texto e
nenhum relatorio conseguia ler. Pior: das 94 tarefas do historico, NENHUMA foi
concluida dentro do prazo do sistema, porque o prazo do sistema nao era o prazo
que a equipe seguia.

Aqui isso vira campo. O Titular fixou o padrao em 24/09/2026: quando a tarefa
tem prazo processual (prazo fatal), o PFI e D-2.

D-2 EM DIAS UTEIS, nao corridos: prazo fatal numa segunda cairia no sabado se
fosse corrido, e sabado ninguem protocola. Pulam sabado, domingo e feriado
nacional (Carnaval e Corpus Christi inclusos); estadual e municipal ficam fora
(Advogada, 03/11/2026). O recesso forense (20/12 a 20/01) NAO entra na conta do interno.
"""
import datetime
import os

# O calendario do D-2 mora num lugar so: controladoria_motor/feriados.prazo_interno (fase 2, T4,
# 03/11/2026). Aqui nao ha lista de feriados.
from .controladoria_motor import feriados as FER

DIAS_ANTES = 2          # o padrao D-2 que o Titular fixou
from .juridico.contatos import CFG as _CFG
AGENDA = _CFG.AGENDA_EMAIL_PRINCIPAL or "primary"
TOKEN = "/opt/escritorio/config/token_esc_calendar.json"
ESCOPO = ["https://www.googleapis.com/auth/calendar"]


def _data(v):
    """Aceita '2026-09-28' ou '2026-09-28 00:00:00'. Devolve date ou None."""
    if not v:
        return None
    if isinstance(v, datetime.date) and not isinstance(v, datetime.datetime):
        return v
    if isinstance(v, datetime.datetime):
        return v.date()
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def pfi_de(prazo_fatal, dias=DIAS_ANTES):
    """O prazo fatal interno: `dias` uteis ANTES do prazo processual.

    Devolve string 'AAAA-MM-DD' ou None quando nao ha prazo fatal.
    """
    d = FER.prazo_interno(_data(prazo_fatal), dias)
    return d.isoformat() if d else None


def completar_prazos(dados):
    """Preenche o prazo interno a partir do fatal, sem nunca sobrescrever.

    `dados` e qualquer coisa com .prazo_fatal e .prazo_interno (o modelo do
    formulario) ou um dicionario. Devolve (prazo_interno, foi_calculado).
    """
    def le(k):
        return dados.get(k) if isinstance(dados, dict) else getattr(dados, k, None)

    interno = (le("prazo_interno") or "").strip() if le("prazo_interno") else None
    if interno:
        return interno, False                 # quem escreveu manda
    calculado = pfi_de(le("prazo_fatal"))
    return calculado, bool(calculado)


# ---------------------------------------------------------------- agenda

def _servico():
    """Agenda do escritorio. Devolve None se o token nao servir - marcar na
    agenda e um extra, nunca pode derrubar a criacao da tarefa."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except Exception:
        return None
    if not os.path.exists(TOKEN):
        return None
    try:
        cred = Credentials.from_authorized_user_file(TOKEN, ESCOPO)
        if cred.expired and cred.refresh_token:
            cred.refresh(Request())
            with open(TOKEN, "w") as f:
                f.write(cred.to_json())
        return build("calendar", "v3", credentials=cred, cache_discovery=False)
    except Exception:
        return None


def _titulo(tarefa):
    partes = [str(tarefa.get("tipo") or "TAREFA").upper()]
    quem = tarefa.get("cliente") or tarefa.get("contato_nome")
    if quem:
        partes.append(str(quem))
    return "PFI: " + " - ".join(partes)


def _descricao(tarefa, prazo_fatal):
    linhas = []
    if tarefa.get("titulo"):
        linhas.append(str(tarefa["titulo"]))
    if prazo_fatal:
        linhas.append("Prazo processual (fatal): %s" % _br(prazo_fatal))
    linhas.append("Este e o prazo INTERNO, %d dias uteis antes do fatal." % DIAS_ANTES)
    if tarefa.get("processo_numero"):
        linhas.append("Processo: %s" % tarefa["processo_numero"])
    if tarefa.get("parte_adversa"):
        linhas.append("Parte adversa: %s" % tarefa["parte_adversa"])
    if tarefa.get("pasta_drive"):
        linhas.append("Pasta: %s" % tarefa["pasta_drive"])
    if tarefa.get("descricao"):
        linhas.append("")
        linhas.append(str(tarefa["descricao"])[:1200])
    linhas.append("")
    linhas.append("Tarefa #%s no Sistema Escritório Modelo: https://sistema.escritorio.adv.br/tarefas"
                  % tarefa.get("id"))
    return "\n".join(linhas)


def _br(iso):
    d = _data(iso)
    return d.strftime("%d/%m/%Y") if d else str(iso)


def marcar_na_agenda(tarefa, emails, prazo_fatal=None):
    """Cria o compromisso do PFI na agenda do escritorio com os responsaveis
    como convidados - e assim que ele aparece na agenda de cada um.

    Best-effort, igual ao aviso de parceiro que ja existe aqui: se o Google
    recusar, a tarefa continua gravada. Devolve (evento_id, erro).
    """
    dia = tarefa.get("prazo_interno")
    if not dia:
        return None, "sem prazo interno"
    s = _servico()
    if not s:
        return None, "agenda do escritorio indisponivel"
    d = _data(dia)
    corpo = {
        "summary": _titulo(tarefa),
        "description": _descricao(tarefa, prazo_fatal or tarefa.get("prazo_fatal")),
        "start": {"date": d.isoformat()},
        "end": {"date": (d + datetime.timedelta(days=1)).isoformat()},
        "transparency": "opaque",
        "reminders": {"useDefault": False, "overrides": [
            {"method": "popup", "minutes": 8 * 60},      # 8h da manha do dia
            {"method": "popup", "minutes": 24 * 60},     # e na vespera
        ]},
    }
    convidados = [{"email": e} for e in sorted(set(e for e in (emails or []) if e))]
    if convidados:
        corpo["attendees"] = convidados
    try:
        ev = s.events().insert(calendarId=AGENDA, body=corpo,
                               sendUpdates="all" if convidados else "none").execute()
        return ev.get("id"), None
    except Exception as e:
        return None, str(e)[:200]


def apagar_da_agenda(evento_id):
    """Some com o compromisso quando a tarefa e concluida ou apagada."""
    if not evento_id:
        return False
    s = _servico()
    if not s:
        return False
    try:
        s.events().delete(calendarId=AGENDA, eventId=evento_id,
                          sendUpdates="all").execute()
        return True
    except Exception as e:
        # fase 2 (03/11/2026): evento que ja nao existe no Google (404 ou 410) conta como apagado; senao o
        # evento_id fica preso na tarefa e ela nunca mais ganha compromisso novo
        return getattr(getattr(e, "resp", None), "status", None) in (404, 410)
