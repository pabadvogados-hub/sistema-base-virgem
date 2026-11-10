"""
AGENTES DA EQUIPE - Sistema Escritório Modelo (29/09/2026, pedido do Titular).

"Cada usuario tem seu proprio agente": Assistente (Advogada), Assistente (a equipe), Assistente (Titular),
Assistente (Titular), Assistente (Advogada). A pessoa conversa com o SEU agente dentro do
proprio usuario; ninguem mais le. O admin le as conversas de todos e deixa
orientacoes (para corrigir erros), que o agente segue dali em diante.

O agente responde com o cerebro do escritorio (/opt/escritorio/cerebro/cerebro.db:
teses, POPs, regras, sistemas) e com os dados do sistema que a pessoa ja pode ver.
Modelo: Claude sonnet-5 com ferramentas, sem o modo de raciocinio (mais rapido;
ver assistente.py).

29/09 (tarde): o agente passa a AGIR a pedido da pessoa (concluir, remarcar, priorizar, criar
tarefa; marcar, mudar, cancelar compromisso na agenda) e a conversar POR VOZ (POST /voz:
Whisper ouve, ElevenLabs fala; app/agentes_voz.py). Cada acao fica em agente_acoes.
"""
import asyncio
import base64
import datetime as dt
import json
import re
import sqlite3
import time
import hmac
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import agentes_nucleo as AG
from ..deps import usuario_atual

router = APIRouter(prefix="/api/agentes", tags=["agentes"])

MODELO = "claude-sonnet-5"
CEREBRO = "/opt/escritorio/cerebro/cerebro.db"
RODADAS = 6
SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def _admin(user):
    return (getattr(user, "papel", "") or "") == "admin"


def _alvo(user, usuario_id):
    if usuario_id and usuario_id != user.id:
        if not _admin(user):
            raise HTTPException(403, "Cada pessoa so acessa o proprio agente")
        return usuario_id
    return user.id


# ---------------------------------------------------------------- ferramentas
def _cerebro():
    return sqlite3.connect("file:%s?mode=ro" % CEREBRO, uri=True, timeout=10)


def buscar_conhecimento(consulta, area=None):
    palavras = [p for p in re.findall(r"[\wÀ-ÿ]+", consulta or "") if len(p) >= 3][:12]
    if not palavras:
        return {"resultados": []}
    q = " OR ".join('"%s"' % p for p in palavras)
    try:
        c = _cerebro()
    except sqlite3.Error:
        return {"erro": "o cérebro do escritório ainda não foi carregado"}
    try:
        sql = ("SELECT titulo, secao, area, caminho, texto FROM trechos WHERE trechos MATCH ? "
               + ("AND area = ? " if area else "") + "ORDER BY bm25(trechos, 3.0, 2.0, 0, 0, 1.0) LIMIT 6")
        linhas = c.execute(sql, (q, area) if area else (q,)).fetchall()
    finally:
        c.close()
    return {"resultados": [{"titulo": t, "secao": s, "area": a, "caminho": cam, "trecho": x[:1600]}
                           for t, s, a, cam, x in linhas]}


def ler_documento(caminho):
    try:
        c = _cerebro()
    except sqlite3.Error:
        return {"erro": "o cérebro do escritório ainda não foi carregado"}
    try:
        r = c.execute("SELECT titulo, texto FROM documentos WHERE caminho=?", (caminho,)).fetchone()
    finally:
        c.close()
    if not r:
        return {"erro": "documento não encontrado"}
    return {"titulo": r[0], "texto": r[1][:15000], "cortado": len(r[1]) > 15000}


def minhas_tarefas(c, uid):
    from .assistente import montar
    d = montar(c, uid)
    g = d["tarefas"]

    def curto(lst, n):
        return [{k: v for k, v in x.items() if k in ("id", "tipo", "titulo", "cliente", "processo", "compromisso",
                                                     "prazo_fatal", "checklist") and v} for x in lst[:n]]
    return {"hoje": d["hoje"], "atrasadas": curto(g["atrasadas"], 15), "total_atrasadas": len(g["atrasadas"]),
            "para_conferir": [{"id": x["id"], "tipo": x["tipo"], "processo": x["processo"], "motivo": x["conferir"]["motivo"],
                               "provas": x["conferir"]["evidencia"][:3]} for x in g.get("conferir", [])[:12]],
            "hoje_vencem": curto(g["hoje"], 15), "amanha": curto(g["amanha"], 10), "semana": curto(g["semana"], 10),
            "sem_prazo": len(g["sem_prazo"]), "comentarios_para_ler": len(d["comentarios"]),
            "audiencias_7_dias": d["audiencias"][:8]}


def ver_tarefa(c, tarefa_id):
    from .tarefa_roteiros import garantir, progresso
    t = c.execute("SELECT * FROM tarefas WHERE id=?", (int(tarefa_id),)).fetchone()
    if not t:
        return {"erro": "tarefa não encontrada"}
    garantir(c)
    resp = [dict(r) for r in c.execute("SELECT nome, concluido_em FROM tarefa_responsaveis WHERE tarefa_id=?", (t["id"],))]
    return {"id": t["id"], "tipo": t["tipo"], "titulo": t["titulo"], "descricao": (t["descricao"] or "")[:3000],
            "processo": t["processo_numero"], "cliente": t["cliente"], "parte_adversa": t["parte_adversa"],
            "compromisso": t["prazo_interno"] or t["data"], "prazo_fatal": t["prazo_fatal"], "status": t["status"],
            "responsaveis": [r["nome"] + (" (concluiu)" if r["concluido_em"] else "") for r in resp],
            "checklist": progresso(c, t["id"], t["tipo"])}


def buscar_processo(c, termo):
    like = "%" + (termo or "").strip() + "%"
    linhas = c.execute(
        "SELECT DISTINCT p.id, p.numero, p.fase, p.etapa, p.responsavel, p.encerrado FROM jur_processos p "
        "LEFT JOIN jur_processo_partes pp ON pp.processo_id = p.id WHERE p.numero LIKE ? OR pp.nome LIKE ? LIMIT 8",
        (like, like)).fetchall()
    out = []
    for p in linhas:
        partes = c.execute("SELECT nome, papel FROM jur_processo_partes WHERE processo_id=?", (p["id"],)).fetchall()
        out.append({"numero": p["numero"], "fase": p["fase"], "grupo": p["etapa"], "responsavel": p["responsavel"],
                    "encerrado": bool(p["encerrado"]),
                    "cliente": " / ".join(x["nome"] for x in partes if not str(x["papel"] or "").upper().startswith("PARTE CONTR")),
                    "parte_contraria": " / ".join(x["nome"] for x in partes if str(x["papel"] or "").upper().startswith("PARTE CONTR"))})
    return {"processos": out}


def linha_do_tempo(c, numero):
    try:
        linhas = c.execute("SELECT criado_em, autor, responsavel, tipo, texto FROM jur_andamentos WHERE processo_numero=? "
                           "AND removido=0 ORDER BY criado_em DESC LIMIT 15", ((numero or "").strip(),)).fetchall()
    except sqlite3.OperationalError:
        return {"itens": []}
    return {"itens": [{"quando": r["criado_em"], "autor": r["autor"], "para": r["responsavel"], "tipo": r["tipo"],
                       "texto": (r["texto"] or "")[:700]} for r in linhas]}


def meus_leads(c, uid, dias=3):
    u = c.execute("SELECT email FROM usuarios WHERE id=?", (uid,)).fetchone()
    if not u:
        return {"leads": []}
    desde = (dt.datetime.utcnow() - dt.timedelta(days=max(1, min(int(dias or 3), 30)))).strftime("%Y-%m-%d %H:%M:%S")
    try:
        linhas = c.execute("SELECT id, contato_nome, etapa, status, atualizado_em, e_reclamante FROM wa_conversas "
                           "WHERE LOWER(atendente_email)=LOWER(?) AND atualizado_em >= ? ORDER BY atualizado_em DESC LIMIT 40",
                           (u["email"], desde)).fetchall()
    except sqlite3.OperationalError:
        return {"leads": []}
    return {"leads": [{"conversa": r["id"], "nome": r["contato_nome"], "etapa": r["etapa"], "status": r["status"],
                       "ultima_movimentacao_utc": str(r["atualizado_em"])[:16], "reclamante": bool(r["e_reclamante"])}
                      for r in linhas]}


# ---------------------------------------------------------------- acoes (29/09/2026, tarde)
# "Voce tem que ter o poder de gerenciar as tarefas delas tambem ... e as agendas" (Titular).
# O agente age COMO a pessoa, pelo mesmo caminho da tela (routers/tarefas.py e agenda_google),
# so nas tarefas em que ela e responsavel e nos compromissos em que ela esta convidada
# (admin pode em todos). Tudo fica em agente_acoes. Nada vai para o ADVBOX.
def _eu(c, uid):
    from types import SimpleNamespace
    u = c.execute("SELECT id, nome, email, papel FROM usuarios WHERE id=?", (uid,)).fetchone()
    return SimpleNamespace(id=u["id"], nome=u["nome"], email=u["email"], papel=u["papel"])


def _registrar(c, ctx, ferramenta, alvo, resumo, detalhe=None):
    c.execute("INSERT INTO agente_acoes (usuario_id, agente, ferramenta, alvo, resumo, detalhe, criado_em) "
              "VALUES (?,?,?,?,?,?,?)", (ctx["uid"], ctx["agente"], ferramenta, str(alvo), resumo,
                                         json.dumps(detalhe or {}, ensure_ascii=False, default=str), time.time()))
    c.commit()
    ctx["acoes"].append(resumo)


def _dia_ok(v):
    try:
        return dt.date.fromisoformat(str(v or "")[:10]).isoformat()
    except ValueError:
        return None


def _br(iso):
    d = _dia_ok(iso)
    return d[8:10] + "/" + d[5:7] if d else str(iso)


def _tarefa_minha(c, ctx, tid):
    try:
        tid = int(tid)
    except (TypeError, ValueError):
        return None, "informe o número da tarefa"
    t = c.execute("SELECT * FROM tarefas WHERE id=?", (tid,)).fetchone()
    if not t:
        return None, "tarefa #%s não encontrada" % tid
    if not ctx["admin"] and not c.execute("SELECT 1 FROM tarefa_responsaveis WHERE tarefa_id=? AND usuario_id=?",
                                          (tid, ctx["uid"])).fetchone():
        return None, "a tarefa #%s não é de %s; só quem é responsável (ou a administração) mexe nela" % (tid, ctx["primeiro"])
    return t, None


def _aviso_advbox(t):
    return (" Atenção: essa tarefa veio do ADVBOX; lá ela continua como estava (o sistema não mexe no ADVBOX)."
            if (t["origem"] or "") == "advbox" else "")


def _erro_http(e):
    return {"erro": str(getattr(e, "detail", e))[:200]}


def concluir_tarefa(c, ctx, tarefa_id, para_todos=False, observacao=None):
    from fastapi import HTTPException as _H
    from . import tarefas as T
    from .. import tarefas_conferir as TC
    t, erro = _tarefa_minha(c, ctx, tarefa_id)
    if erro:
        return {"erro": erro}
    if t["status"] != "aberta":
        return {"erro": "a tarefa #%s já está %s" % (t["id"], t["status"])}
    try:
        T.concluir(t["id"], todos=bool(para_todos), user=ctx["eu"])
    except _H as e:
        return _erro_http(e)
    TC.resolver(c, t["id"], True, "%s (agente de %s)" % (ctx["agente"], ctx["primeiro"]))
    c.commit()
    resumo = "Concluí a tarefa #%s (%s)%s" % (t["id"], t["tipo"], " para todos" if para_todos else "")
    _registrar(c, ctx, "concluir_tarefa", t["id"], resumo, {"para_todos": bool(para_todos), "obs": observacao})
    return {"ok": True, "feito": resumo + "." + _aviso_advbox(t)}


def reabrir_tarefa(c, ctx, tarefa_id):
    from . import tarefas as T
    t, erro = _tarefa_minha(c, ctx, tarefa_id)
    if erro:
        return {"erro": erro}
    T.reabrir(t["id"], user=ctx["eu"])
    resumo = "Reabri a tarefa #%s (%s)" % (t["id"], t["tipo"])
    _registrar(c, ctx, "reabrir_tarefa", t["id"], resumo)
    return {"ok": True, "feito": resumo + "."}


def remarcar_tarefa(c, ctx, tarefa_id, compromisso=None, hora=None, prazo_fatal=None):
    from . import tarefas as T
    t, erro = _tarefa_minha(c, ctx, tarefa_id)
    if erro:
        return {"erro": erro}
    campos = {}
    if compromisso:
        if not _dia_ok(compromisso):
            return {"erro": "data do compromisso inválida (use AAAA-MM-DD)"}
        campos["prazo_interno"] = _dia_ok(compromisso)
    if hora:
        campos["hora"] = str(hora)[:5]
    if prazo_fatal:
        if not _dia_ok(prazo_fatal):
            return {"erro": "prazo fatal inválido (use AAAA-MM-DD)"}
        campos["prazo_fatal"] = _dia_ok(prazo_fatal)
    if not campos:
        return {"erro": "diga a nova data"}
    antes = {"compromisso": t["prazo_interno"] or t["data"], "prazo_fatal": t["prazo_fatal"], "hora": t["hora"]}
    T.editar(t["id"], T.TarefaPatch(**campos), user=ctx["eu"])
    partes = []
    if "prazo_interno" in campos:
        partes.append("compromisso de %s para %s" % (_br(antes["compromisso"]), _br(campos["prazo_interno"])))
    if "prazo_fatal" in campos:
        partes.append("PRAZO FATAL de %s para %s" % (_br(antes["prazo_fatal"]), _br(campos["prazo_fatal"])))
    if "hora" in campos:
        partes.append("hora para %s" % campos["hora"])
    resumo = "Remarquei a tarefa #%s: %s" % (t["id"], ", ".join(partes))
    _registrar(c, ctx, "remarcar_tarefa", t["id"], resumo, {"antes": antes, "depois": campos})
    return {"ok": True, "feito": resumo + "." + _aviso_advbox(t)}


def priorizar_tarefa(c, ctx, tarefa_id, prioridade):
    from . import tarefas as T
    t, erro = _tarefa_minha(c, ctx, tarefa_id)
    if erro:
        return {"erro": erro}
    if prioridade not in T.PRIORIDADES:
        return {"erro": "prioridade deve ser normal, importante ou urgente"}
    T.editar(t["id"], T.TarefaPatch(prioridade=prioridade), user=ctx["eu"])
    resumo = "Mudei a prioridade da tarefa #%s de %s para %s" % (t["id"], t["prioridade"] or "normal", prioridade)
    _registrar(c, ctx, "priorizar_tarefa", t["id"], resumo)
    return {"ok": True, "feito": resumo + "."}


def _usuarios_por_nome(c, nomes, eu_id):
    ids, nao = [], []
    todos = c.execute("SELECT id, nome, email FROM usuarios WHERE ativo=1").fetchall()
    for n in nomes or []:
        alvo = str(n or "").strip().lower()
        if not alvo:
            continue
        if alvo in ("eu", "mim", "comigo"):
            ids.append(eu_id)
            continue
        achou = [u for u in todos if (u["nome"] or "").lower().startswith(alvo) or
                 (u["nome"] or "").lower().split()[0] == alvo.split()[0]]
        (ids.append(achou[0]["id"]) if len(achou) == 1 else nao.append(n))
    return list(dict.fromkeys(ids)), nao


def criar_tarefa(c, ctx, tipo, titulo, descricao=None, compromisso=None, hora=None, prazo_fatal=None,
                 processo_numero=None, cliente=None, responsaveis=None, prioridade="normal"):
    from fastapi import HTTPException as _H
    from . import tarefas as T
    ids, nao = _usuarios_por_nome(c, responsaveis, ctx["uid"]) if responsaveis else ([ctx["uid"]], [])
    if nao:
        return {"erro": "não achei na equipe: %s. Diga o primeiro nome como está no sistema." % ", ".join(nao)}
    if not ids:
        ids = [ctx["uid"]]
    if prioridade not in T.PRIORIDADES:
        prioridade = "normal"
    dados = T.TarefaIn(tipo=str(tipo or "TAREFA").strip().upper()[:60], titulo=str(titulo or "").strip()[:200] or str(tipo),
                       descricao=(str(descricao).strip() + "\n\n" if descricao else "") +
                       "Criada por %s a pedido de %s." % (ctx["agente"], ctx["primeiro"]),
                       prazo_interno=_dia_ok(compromisso), data=_dia_ok(compromisso), hora=(str(hora)[:5] if hora else None),
                       prazo_fatal=_dia_ok(prazo_fatal), processo_numero=processo_numero or None, cliente=cliente or None,
                       prioridade=prioridade, responsaveis=[T.ResponsavelIn(usuario_id=i) for i in ids])
    try:
        feita = T.criar(dados, user=ctx["eu"])
    except _H as e:
        return _erro_http(e)
    nomes = [r["nome"].split()[0] for r in c.execute(
        "SELECT nome FROM usuarios WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)]
    resumo = "Criei a tarefa #%s (%s) para %s%s" % (feita["id"], dados.tipo, ", ".join(nomes),
                                                    ", compromisso " + _br(dados.prazo_interno) if dados.prazo_interno else "")
    _registrar(c, ctx, "criar_tarefa", feita["id"], resumo, {"dados": dados.model_dump()})
    return {"ok": True, "feito": resumo + ".", "tarefa_id": feita["id"],
            "agenda": "o compromisso foi para a agenda de quem é responsável" if feita.get("evento_id") else None}


def conferir_tarefa(c, ctx, tarefa_id, ja_feita):
    from fastapi import HTTPException as _H
    from .assistente import resolver_conferir
    t, erro = _tarefa_minha(c, ctx, tarefa_id)
    if erro:
        return {"erro": erro}
    try:
        resolver_conferir(t["id"], bool(ja_feita), ctx["eu"], "%s (agente de %s)" % (ctx["agente"], ctx["primeiro"]))
    except _H as e:
        return _erro_http(e)
    resumo = ("Marquei a tarefa #%s como já feita e concluí" if ja_feita else
              "Marquei a tarefa #%s como ainda pendente (volta a contar como atrasada)") % t["id"]
    _registrar(c, ctx, "conferir_tarefa", t["id"], resumo)
    return {"ok": True, "feito": resumo + "." + (_aviso_advbox(t) if ja_feita else "")}


# ---- agenda (Google Calendar do escritorio, o mesmo da tela Agenda)
def _evento_meu(ctx, evento_id):
    from .. import agenda_google as AGG
    try:
        d = AGG.detalhe(evento_id)
    except Exception as e:
        return None, "compromisso não encontrado (%s)" % str(e)[:80]
    if d.get("cancelado"):
        return None, "esse compromisso já foi cancelado"
    emails = [str(x.get("email") or "").lower() for x in d.get("convidados") or []]
    if not ctx["admin"] and (ctx["eu"].email or "").lower() not in emails:
        return None, "%s não está nesse compromisso; só quem está convidado (ou a administração) mexe nele" % ctx["primeiro"]
    return d, None


def minha_agenda(c, ctx, dias=7, de_todos=False):
    from .. import agenda_google as AGG
    if not AGG.disponivel():
        return {"erro": "a agenda do Google está indisponível agora (token)"}
    dias = max(1, min(int(dias or 7), 60))
    ev = AGG.listar(dias_atras=1, dias_frente=dias)
    eu = (ctx["eu"].email or "").lower()
    hoje = dt.date.today().isoformat()
    out = []
    for e in ev:
        if e["data"] < hoje:
            continue
        convidado = eu in [str(x or "").lower() for x in e.get("convidados") or []]
        if not de_todos and not convidado:
            continue
        out.append({"evento_id": e["evento_id"], "titulo": e["titulo"], "data": e["data"], "hora": e.get("hora"),
                    "local": e.get("local") or None, "processo": e.get("processo"), "voce_convidado": convidado,
                    "cumprido": str(e["titulo"]).lower().startswith("[ok]")})
    return {"hoje": hoje, "compromissos": out[:60], "total": len(out)}


def criar_compromisso(c, ctx, titulo, data, hora=None, duracao_min=60, descricao=None, convidados=None,
                      local=None, processo=None):
    from .. import agenda_google as AGG
    if not _dia_ok(data):
        return {"erro": "data inválida (use AAAA-MM-DD)"}
    ids, nao = _usuarios_por_nome(c, convidados, ctx["uid"]) if convidados else ([ctx["uid"]], [])
    if nao:
        return {"erro": "não achei na equipe: %s" % ", ".join(nao)}
    if ctx["uid"] not in ids:
        ids.append(ctx["uid"])
    emails = [r["email"] for r in c.execute("SELECT email FROM usuarios WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
              if r["email"]]
    desc = ((descricao or "").strip() + "\n\n" if descricao else "") + "Marcado por %s a pedido de %s." % (ctx["agente"], ctx["primeiro"])
    ev = AGG.criar_manual(str(titulo).strip()[:150], _dia_ok(data), (str(hora)[:5] if hora else None),
                          int(duracao_min or 60), desc, emails, local or None, processo or None)
    resumo = "Marquei na agenda: %s em %s%s" % (titulo, _br(data), " às " + str(hora)[:5] if hora else "")
    _registrar(c, ctx, "criar_compromisso", ev.get("id"), resumo, {"convidados": emails})
    return {"ok": True, "feito": resumo + ".", "evento_id": ev.get("id"), "convidados": emails}


def mudar_compromisso(c, ctx, evento_id, data=None, hora=None, titulo=None, descricao=None, local=None):
    from .. import agenda_google as AGG
    d, erro = _evento_meu(ctx, evento_id)
    if erro:
        return {"erro": erro}
    nova_data = _dia_ok(data) if data else None
    if data and not nova_data:
        return {"erro": "data inválida (use AAAA-MM-DD)"}
    duracao = 60
    if d.get("hora") and d.get("fim"):
        h1, m1 = (int(x) for x in d["hora"].split(":"))
        h2, m2 = (int(x) for x in d["fim"].split(":"))
        duracao = max(15, (h2 * 60 + m2) - (h1 * 60 + m1))
    # mudar so o dia nao pode transformar compromisso com hora em "dia inteiro"
    h = str(hora)[:5] if hora else (d.get("hora") if (nova_data and not d.get("dia_inteiro")) else None)
    dia = nova_data or (d["data"] if hora else None)
    AGG.atualizar(evento_id, titulo, dia, h, duracao, descricao, None, local)
    resumo = "Mudei o compromisso \"%s\"%s%s" % (d["titulo"], " para " + _br(dia) if dia else "", " às " + h if (h and hora) else "")
    _registrar(c, ctx, "mudar_compromisso", evento_id, resumo, {"antes": {"data": d["data"], "hora": d.get("hora")}})
    return {"ok": True, "feito": resumo + "."}


def cancelar_compromisso(c, ctx, evento_id):
    from .. import agenda_google as AGG
    d, erro = _evento_meu(ctx, evento_id)
    if erro:
        return {"erro": erro}
    AGG.cancelar(evento_id)
    resumo = "Cancelei o compromisso \"%s\" de %s" % (d["titulo"], _br(d["data"]))
    _registrar(c, ctx, "cancelar_compromisso", evento_id, resumo, {"evento": d})
    return {"ok": True, "feito": resumo + "."}


def compromisso_cumprido(c, ctx, evento_id, cumprido=True):
    from .. import agenda_google as AGG
    d, erro = _evento_meu(ctx, evento_id)
    if erro:
        return {"erro": erro}
    AGG.marcar_cumprido(evento_id, bool(cumprido))
    resumo = "Marquei \"%s\" como %s" % (d["titulo"], "cumprido" if cumprido else "não cumprido")
    _registrar(c, ctx, "compromisso_cumprido", evento_id, resumo)
    return {"ok": True, "feito": resumo + "."}


FERRAMENTAS = [
    {"name": "buscar_conhecimento",
     "description": "Busca no cérebro do escritório (teses do banco de teses, POPs, regras de peças e documentos, "
                    "como funcionam os sistemas, procedimentos do comercial e do operacional, marketing). Use sempre que "
                    "a pergunta for sobre como o escritório faz algo, qual tese usa ou qual é a regra.",
     "input_schema": {"type": "object", "properties": {
         "consulta": {"type": "string", "description": "palavras-chave da busca"},
         "area": {"type": "string", "enum": ["teses", "juridico", "pecas", "operacional", "comercial", "marketing",
                                            "sistemas", "regras", "mapas"],
                  "description": "opcional: limita a uma área"}}, "required": ["consulta"]}},
    {"name": "ler_documento",
     "description": "Lê um documento inteiro do cérebro (use o 'caminho' que veio na busca) quando o trecho não bastar.",
     "input_schema": {"type": "object", "properties": {"caminho": {"type": "string"}}, "required": ["caminho"]}},
    {"name": "minhas_tarefas",
     "description": "As tarefas abertas da pessoa com quem você fala: atrasadas, de hoje, de amanhã e da semana, com o "
                    "ponto do checklist, e as audiências dos próximos 7 dias.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "ver_tarefa", "description": "Detalhe de uma tarefa pelo número (#id): descrição, prazos, responsáveis, checklist.",
     "input_schema": {"type": "object", "properties": {"tarefa_id": {"type": "integer"}}, "required": ["tarefa_id"]}},
    {"name": "buscar_processo", "description": "Procura processo pelo número ou pelo nome de uma parte.",
     "input_schema": {"type": "object", "properties": {"termo": {"type": "string"}}, "required": ["termo"]}},
    {"name": "linha_do_tempo", "description": "Os últimos andamentos, comentários e tarefas de um processo (pelo número CNJ).",
     "input_schema": {"type": "object", "properties": {"numero": {"type": "string"}}, "required": ["numero"]}},
    {"name": "meus_leads",
     "description": "Os leads (conversas do WhatsApp) atendidos pela pessoa nos últimos dias, com a etapa de cada um.",
     "input_schema": {"type": "object", "properties": {"dias": {"type": "integer", "description": "padrão 3"}}}},
    # ---- acoes em tarefas (so as da pessoa; admin em todas)
    {"name": "concluir_tarefa",
     "description": "Conclui uma tarefa para a pessoa (a parte dela). para_todos=true fecha para todos os responsáveis: "
                    "só quando ela pedir isso claramente.",
     "input_schema": {"type": "object", "properties": {
         "tarefa_id": {"type": "integer"}, "para_todos": {"type": "boolean"},
         "observacao": {"type": "string", "description": "o que a pessoa disse sobre a conclusão, se disse"}},
         "required": ["tarefa_id"]}},
    {"name": "reabrir_tarefa", "description": "Reabre uma tarefa concluída (para desfazer uma conclusão).",
     "input_schema": {"type": "object", "properties": {"tarefa_id": {"type": "integer"}}, "required": ["tarefa_id"]}},
    {"name": "remarcar_tarefa",
     "description": "Muda a data do compromisso (prazo interno) e/ou a hora de uma tarefa. prazo_fatal é o prazo do "
                    "PROCESSO: só mude se a pessoa pedir explicitamente e disser a nova data.",
     "input_schema": {"type": "object", "properties": {
         "tarefa_id": {"type": "integer"}, "compromisso": {"type": "string", "description": "AAAA-MM-DD"},
         "hora": {"type": "string", "description": "HH:MM"}, "prazo_fatal": {"type": "string", "description": "AAAA-MM-DD"}},
         "required": ["tarefa_id"]}},
    {"name": "priorizar_tarefa", "description": "Muda a prioridade de uma tarefa.",
     "input_schema": {"type": "object", "properties": {
         "tarefa_id": {"type": "integer"}, "prioridade": {"type": "string", "enum": ["normal", "importante", "urgente"]}},
         "required": ["tarefa_id", "prioridade"]}},
    {"name": "criar_tarefa",
     "description": "Cria uma tarefa no sistema. Sem responsáveis, fica para a própria pessoa. Responsáveis pelo "
                    "primeiro nome (ex.: [\"Advogada\"]). O compromisso vai para a agenda de quem é responsável.",
     "input_schema": {"type": "object", "properties": {
         "tipo": {"type": "string", "description": "ex.: PROTOCOLO, REVISAO DE PEÇA, ACOMPANHAMENTO, LIGAR PARA CLIENTE"},
         "titulo": {"type": "string"}, "descricao": {"type": "string"},
         "compromisso": {"type": "string", "description": "AAAA-MM-DD"}, "hora": {"type": "string"},
         "prazo_fatal": {"type": "string", "description": "AAAA-MM-DD, só se a pessoa disser"},
         "processo_numero": {"type": "string"}, "cliente": {"type": "string"},
         "responsaveis": {"type": "array", "items": {"type": "string"}},
         "prioridade": {"type": "string", "enum": ["normal", "importante", "urgente"]}},
         "required": ["tipo", "titulo"]}},
    {"name": "conferir_tarefa",
     "description": "Responde a uma tarefa da caixa 'Conferir' (parece já feita no ADVBOX): ja_feita=true conclui; "
                    "false diz que ainda falta (volta a contar como atrasada).",
     "input_schema": {"type": "object", "properties": {"tarefa_id": {"type": "integer"}, "ja_feita": {"type": "boolean"}},
                      "required": ["tarefa_id", "ja_feita"]}},
    # ---- agenda (Google Calendar do escritório)
    {"name": "minha_agenda",
     "description": "Compromissos da agenda do escritório nos próximos dias em que a pessoa está convidada "
                    "(de_todos=true mostra a agenda inteira do escritório).",
     "input_schema": {"type": "object", "properties": {"dias": {"type": "integer", "description": "padrão 7"},
                                                       "de_todos": {"type": "boolean"}}}},
    {"name": "criar_compromisso",
     "description": "Marca um compromisso na agenda do escritório com a pessoa convidada (e quem mais ela pedir, "
                    "pelo primeiro nome). Sem hora = dia inteiro.",
     "input_schema": {"type": "object", "properties": {
         "titulo": {"type": "string"}, "data": {"type": "string", "description": "AAAA-MM-DD"},
         "hora": {"type": "string", "description": "HH:MM"}, "duracao_min": {"type": "integer"},
         "descricao": {"type": "string"}, "convidados": {"type": "array", "items": {"type": "string"}},
         "local": {"type": "string"}, "processo": {"type": "string"}}, "required": ["titulo", "data"]}},
    {"name": "mudar_compromisso", "description": "Muda data, hora, título, descrição ou local de um compromisso (pelo evento_id).",
     "input_schema": {"type": "object", "properties": {
         "evento_id": {"type": "string"}, "data": {"type": "string"}, "hora": {"type": "string"},
         "titulo": {"type": "string"}, "descricao": {"type": "string"}, "local": {"type": "string"}},
         "required": ["evento_id"]}},
    {"name": "cancelar_compromisso", "description": "Cancela um compromisso da agenda. Só com pedido explícito.",
     "input_schema": {"type": "object", "properties": {"evento_id": {"type": "string"}}, "required": ["evento_id"]}},
    {"name": "compromisso_cumprido", "description": "Marca um compromisso/prazo da agenda como cumprido (ou desmarca).",
     "input_schema": {"type": "object", "properties": {"evento_id": {"type": "string"}, "cumprido": {"type": "boolean"}},
                      "required": ["evento_id"]}},
]

ACOES = {"concluir_tarefa": concluir_tarefa, "reabrir_tarefa": reabrir_tarefa, "remarcar_tarefa": remarcar_tarefa,
         "priorizar_tarefa": priorizar_tarefa, "criar_tarefa": criar_tarefa, "conferir_tarefa": conferir_tarefa,
         "minha_agenda": minha_agenda, "criar_compromisso": criar_compromisso, "mudar_compromisso": mudar_compromisso,
         "cancelar_compromisso": cancelar_compromisso, "compromisso_cumprido": compromisso_cumprido}

# 06/11/2026 (Advogada): a Amel.IA, assistente dela no WhatsApp (Hermes), pergunta o Assistente pela rota /amelia.
# Nesse canal o Assistente ouve e responde, mas NAO age no sistema: so as ferramentas desta lista fechada.
SO_LEITURA = {"buscar_conhecimento", "ler_documento", "minhas_tarefas", "ver_tarefa", "buscar_processo",
              "linha_do_tempo", "meus_leads", "minha_agenda", "buscar_lead", "ler_chat", "rotas_sistema",
              "minhas_revisoes", "ver_parecer", "intimacoes_pendentes", "minhas_pecas", "pauta_conteudo",
              "ver_conteudo", "metricas_anuncios", "leads_quentes", "ver_lead"}
AVISO_AMELIA = ("\n\nCANAL DA AMEL.IA: quem pergunta agora e a Amel.IA, assistente pessoal da Advogada no WhatsApp, "
                "em nome dela. Aqui voce esta em modo SO LEITURA: consulta e responde, mas nao conclui, cria, "
                "remarca, move, envia nem altera nada. Se pedirem uma acao, diga o que seria feito e que a "
                "Advogada precisa autorizar pelo sistema. Responda direto, com numeros, nomes e datas.")


def executar(c, ctx, nome, entrada):
    from .. import agentes_fluxos as AF
    entrada = entrada or {}
    uid = ctx["uid"]
    if nome in ACOES:
        return ACOES[nome](c, ctx, **entrada)
    if nome in AF.ACOES:
        if not AF.permitida(ctx, nome):
            return {"erro": "essa ferramenta não é da área de %s" % ctx["primeiro"]}
        return AF.ACOES[nome](c, ctx, **entrada)
    if nome == "buscar_conhecimento":
        return buscar_conhecimento(entrada.get("consulta"), entrada.get("area"))
    if nome == "ler_documento":
        return ler_documento(entrada.get("caminho"))
    if nome == "minhas_tarefas":
        return minhas_tarefas(c, uid)
    if nome == "ver_tarefa":
        return ver_tarefa(c, entrada.get("tarefa_id"))
    if nome == "buscar_processo":
        return buscar_processo(c, entrada.get("termo"))
    if nome == "linha_do_tempo":
        return linha_do_tempo(c, entrada.get("numero"))
    if nome == "meus_leads":
        return meus_leads(c, uid, entrada.get("dias") or 3)
    return {"erro": "ferramenta desconhecida"}


def sistema(c, ag, pessoa, via="texto", ctx=None):
    agora = dt.datetime.now()
    orient = [r["texto"] for r in c.execute("SELECT texto FROM agente_orientacoes WHERE usuario_id=? AND ativo=1 "
                                            "ORDER BY id", (ag["usuario_id"],))]
    txt = ("Você é %s, assistente de IA de %s no Escritório Modelo Advogados (escritório trabalhista em Cidade; a "
           "maioria dos clientes é trabalhador reclamante; titular: Dra. Advogada Responsável, OAB/UF 000.000).\n"
           "Seu foco com essa pessoa: %s.\n"
           "Hoje é %s, %s, %s.\n\n"
           "Como trabalhar:\n"
           "- Português do Brasil, tom de colega de equipe: direto, gentil e prático. Frases curtas. Sem emoji. Não use travessão.\n"
           "- Para dúvidas de procedimento, tese, regra, POP ou como o escritório faz algo: CONSULTE o cérebro "
           "(buscar_conhecimento, e ler_documento se precisar) antes de responder, e diga de qual documento tirou "
           "(ex.: \"pelo banco de teses, tema intervalo intrajornada\"). Cite o nome do documento, nunca o caminho "
           "do arquivo (a tela já mostra as fontes consultadas).\n"
           "- Para tarefas, prazos, processos e leads, use as ferramentas do sistema. Cite tarefa como [#123].\n"
           "- Dúvida jurídica ou de processo (ex.: ação anulatória, prazos, recursos, o que fazer num caso): consulte o "
           "cérebro e o processo no sistema; se o material do escritório não cobrir, RESPONDA com o seu conhecimento "
           "jurídico, dizendo que é orientação geral e não do material do escritório. Não invente número de processo, "
           "súmula, julgado ou valor que você não tem.\n"
           "- Não prometa resultado de processo. Em conteúdo e abordagem de cliente, respeite o Provimento 205 da OAB.\n"
           "- Dado pessoal de cliente só quando for necessário para a tarefa.\n"
           "\nVocê também AGE, quando a pessoa pede: concluir, reabrir, remarcar, priorizar e criar tarefas; responder à "
           "caixa \"Conferir\"; ver e mexer na agenda (marcar, mudar, cancelar compromisso, marcar cumprido).\n"
           "- Pedido claro (\"conclui a 182\", \"passa a 255 para sexta\", \"marca reunião amanhã às 10h\"): faça, sem "
           "perguntar de novo. Pedido ambíguo (qual tarefa? que dia?): pergunte antes.\n"
           "- Tarefa citada pelo número: chame a ferramenta direto (ou ver_tarefa). É a ferramenta que confere se a "
           "tarefa existe e se é da pessoa. Nunca diga que não achou uma tarefa sem ter chamado a ferramenta.\n"
           "- Se a ferramenta recusar porque a tarefa é de outra pessoa, explique que só quem é responsável (ou a "
           "administração) mexe nela. Não peça confirmação para mexer em tarefa de outra pessoa.\n"
           "- Concluir para todos, cancelar compromisso e mudar PRAZO FATAL: só com pedido explícito. Prazo fatal é o "
           "prazo do processo; na dúvida, não mexa e diga isso.\n"
           "- Para organizar o dia ou dizer o que é mais urgente: use minhas_tarefas e a agenda, ordene por prazo fatal, "
           "prioridade e checklist, e proponha a ordem. Só mude datas ou prioridades se a pessoa pedir ou concordar.\n"
           "- Tarefas da caixa \"para_conferir\" parecem já feitas no ADVBOX: não são atrasadas. Pergunte se já foram "
           "feitas e use conferir_tarefa com a resposta.\n"
           "- Depois de agir, diga exatamente o que fez (qual tarefa, de quê para quê). Se a ferramenta devolver erro, "
           "diga o erro com clareza; nunca diga que fez o que não fez.\n"
           "- ADVBOX, só pelas ferramentas advbox_*. Mensagem para lead ou cliente, mover card que dispara robô e apagar "
           "algo: sempre com a prévia confirmada pela pessoa.\n"
           "\nPOSTURA (regra do Titular, 01/10/2026): você é a assistente dessa pessoa, não fiscal dela.\n"
           "- Tudo o que o usuário dela pode fazer no sistema, você faz por ela, sem questionar. Não recuse, não dê "
           "sermão, não discuta se o serviço é do escopo do escritório, se o preço está certo ou se ela devia "
           "consultar a Dra. Advogada. Só opine se ela pedir a sua opinião.\n"
           "- Nunca peça número de card, conversa_id, id de tarefa ou link que você mesma pode achar: procure "
           "(buscar_lead, buscar_processo, minhas_tarefas, rotas_sistema + sistema_api).\n"
           "- Se não existe ferramenta específica, use sistema_api. Só diga que não conseguiu DEPOIS de tentar, e "
           "diga o erro exato que voltou.\n"
           "- Se a pessoa colar uma transcrição ou um texto e pedir para registrar, registre (anotar_lead no card "
           "certo) e confirme em uma frase. Um resumo curto do que ficou combinado basta.\n"
           "- Datas nas ferramentas sempre AAAA-MM-DD. \"Amanhã\", \"sexta\", \"semana que vem\" contam a partir de hoje.\n"
           % (AG.apresentacao(ag).split(" ", 1)[1], pessoa, ag.get("foco") or "rotina do escritório",
              SEMANA[agora.weekday()], agora.strftime("%d/%m/%Y"), agora.strftime("%H:%M")))
    if via == "voz":
        txt += ("\nAGORA A CONVERSA É POR VOZ: a pessoa falou e vai OUVIR a sua resposta. Responda em no máximo 3 "
                "frases curtas (uns 300 caracteres), faladas. Se houver muita coisa, diga só o principal e ofereça "
                "detalhar. Sem listas, sem markdown, sem colchetes. Diga \"tarefa 182\" em vez de [#182]. "
                "Não leia número de processo: diga o nome do cliente. Se a transcrição parecer sem sentido, peça para "
                "repetir.\n")
    if ctx:
        from .. import agentes_fluxos as AF
        txt += AF.prompt(ctx)
        AF.garantir(c)
        pend = c.execute("SELECT id, previa FROM agente_pendentes WHERE usuario_id=? AND status='aguardando' AND criado_em > ? "
                         "ORDER BY id DESC LIMIT 5", (ctx["uid"], time.time() - 3600)).fetchall()
        if pend:
            txt += ("\nAções esperando o SIM da pessoa (se a última mensagem dela for um sim para uma delas, chame "
                    "confirmar_acao com o código; se for não, cancelar_acao):\n" +
                    "\n".join("- código %s: %s" % (p["id"], (p["previa"] or "")[:400]) for p in pend) + "\n")
    if orient:
        txt += "\nOrientações da administração (siga sempre):\n" + "\n".join("- " + o for o in orient)
    return txt


def _responder_pela_api(c, msg_id, sis, msgs, ferramentas, processar, teto):
    """Credito da API Anthropic e, se ela recusar, o Gemini. Era o caminho principal ate 07/10/2026;
    agora e a reserva da assinatura (app/agentes_assinatura.py)."""
    import anthropic
    from ..config import settings
    from .. import ia_reserva as IR
    base = [dict(m) for m in msgs]
    resposta = ""
    try:
        if IR.anthropic_fora() and IR.disponivel():
            raise RuntimeError("anthropic fora (reserva ativa)")
        cli = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        for _ in range(RODADAS + 2):
            r = cli.messages.create(model=MODELO, max_tokens=teto, system=sis, tools=ferramentas,
                                    messages=msgs, thinking={"type": "disabled"})
            if r.stop_reason == "tool_use":
                msgs.append({"role": "assistant", "content": r.content})
                res = [{"type": "tool_result", "tool_use_id": b.id,
                        "content": json.dumps(processar(b.name, b.input), ensure_ascii=False, default=str)[:40000]}
                       for b in r.content if getattr(b, "type", "") == "tool_use"]
                msgs.append({"role": "user", "content": res})
                continue
            resposta = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text").strip()
            break
        else:
            # acabaram as rodadas de ferramenta (01/10, Assistente chutando rota): em vez de "nao consegui fechar",
            # uma ultima chamada SEM ferramenta para dizer o que fez e o que deu errado
            r = cli.messages.create(model=MODELO, max_tokens=teto, system=sis, tools=ferramentas,
                                    tool_choice={"type": "none"}, thinking={"type": "disabled"},
                                    messages=msgs + [{"role": "user", "content": "(sistema: acabaram as tentativas. "
                                                      "Diga em poucas frases o que você fez, o que não deu certo e "
                                                      "qual foi o erro.)"}]
                                    if msgs[-1]["role"] == "assistant" else msgs)
            resposta = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        # 29/09/2026: a carteira da Anthropic zerou e o Assistente parou. Agora segue pelo Gemini,
        # com as mesmas ferramentas e travas; so desiste se os dois falharem.
        if not IR.disponivel() or not (IR.anthropic_fora() or IR.falha_de_conta(e)):
            c.execute("DELETE FROM agente_mensagens WHERE id=?", (msg_id,))
            c.commit()
            raise
        if not IR.anthropic_fora():
            IR.marcar_fora(e)
        try:
            resposta = IR.conversar_com_ferramentas(sis, base, ferramentas, processar, RODADAS + 2, teto + 1000)
        except Exception as e2:
            c.execute("DELETE FROM agente_mensagens WHERE id=?", (msg_id,))
            c.commit()
            raise RuntimeError("a IA está indisponível agora (Anthropic sem crédito e a reserva do Google "
                               "também falhou: %s). Avise o Titular." % str(e2)[:120])
    return resposta


def conversar(uid, texto, autor_id, via="texto", somente_leitura=False, lua_reserva=None):
    if not (texto or "").strip():
        raise ValueError("mensagem vazia")
    # 07/11/2026 (Advogada, Assistente no Hermes, fatia 1): so o Assistente e so com o interruptor em "hermes".
    # Os outros agentes nao mudam. lua_reserva vem preenchido quando o Hermes caiu (reserva).
    if not somente_leitura and lua_reserva is None:
        from .. import agentes_hermes as AH
        if AH.motor_da_lua(uid) == "hermes":
            with AH.TRAVA_DO_TURNO:      # um turno por vez: a reserva abaixo nao passa por aqui
                return _conversar_hermes(uid, texto, autor_id, via)
    c = AG.con()
    try:
        ag = AG.agente_de(c, uid)
        u = c.execute("SELECT nome FROM usuarios WHERE id=?", (uid,)).fetchone()
        pessoa = (u["nome"] if u else "") + (" (%s)" % ag["papel"] if ag.get("papel") else "")
        eu = _eu(c, uid)
        ctx = {"uid": uid, "eu": eu, "admin": (eu.papel or "") == "admin", "agente": ag.get("nome") or "Assistente",
               "primeiro": (eu.nome or "").split(" ")[0].capitalize(), "acoes": []}
        ctx["registrar"] = lambda ferramenta, alvo, resumo, detalhe=None: _registrar(c, ctx, ferramenta, alvo, resumo, detalhe)
        from .. import agentes_fluxos as AF
        ferramentas = FERRAMENTAS + AF.ferramentas(ctx)
        if somente_leitura:
            ferramentas = [f for f in ferramentas if f["name"] in SO_LEITURA]
        # a conversa da Amel.IA corre separada da conversa da propria Advogada com o Assistente (mesmo usuario)
        filtro_via = " AND via='amelia'" if somente_leitura else " AND COALESCE(via,'')!='amelia'"
        conversa = c.execute("SELECT COALESCE(MAX(conversa), 1) FROM agente_mensagens WHERE usuario_id=?", (uid,)).fetchone()[0]
        hist = c.execute("SELECT papel, texto FROM agente_mensagens WHERE usuario_id=? AND conversa=?" + filtro_via + " ORDER BY id DESC LIMIT 16",
                         (uid, conversa)).fetchall()
        msgs = []
        for h in list(reversed(hist)) + [{"papel": "user", "texto": texto}]:
            if not h["texto"] or h["papel"] not in ("user", "assistant"):
                continue
            papel = "user" if h["papel"] == "user" else "assistant"
            if msgs and msgs[-1]["role"] == papel:
                # pergunta que ficou sem resposta (a IA falhou) e a pessoa mandou de novo: vale so a
                # ultima. Juntar tudo fazia o agente responder repetido e misturar ruido de voz (29/09).
                if papel == "user":
                    msgs[-1]["content"] = h["texto"]
                else:
                    msgs[-1]["content"] += "\n\n" + h["texto"]
            else:
                msgs.append({"role": papel, "content": h["texto"]})
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        cur = c.execute("INSERT INTO agente_mensagens (usuario_id, conversa, papel, texto, autor_id, criado_em, via) "
                        "VALUES (?,?,?,?,?,?,?)", (uid, conversa, "user", texto, autor_id, time.time(), via))
        c.commit()
        msg_id = cur.lastrowid
        fontes, resposta = [], ""

        def processar(nome, entrada):
            if somente_leitura and nome not in SO_LEITURA:
                return {"erro": "canal da Amel.IA e so leitura: o Assistente nao age no sistema por aqui"}
            try:
                out = executar(c, ctx, nome, entrada)
            except TypeError as e:
                out = {"erro": "parâmetros errados para %s: %s" % (nome, str(e)[:120])}
            except Exception as e:
                out = {"erro": str(e)[:200]}
            for x in (out.get("resultados") or []):
                if x.get("titulo") and x["titulo"] not in fontes:
                    fontes.append(x["titulo"])
            if nome == "ler_documento" and out.get("titulo") and out["titulo"] not in fontes:
                fontes.append(out["titulo"])
            return out

        sis = sistema(c, ag, pessoa, via, ctx)
        if lua_reserva and lua_reserva.get("memoria"):
            # o mesmo arquivo de licoes do Hermes: a reserva nao volta esquecida (fonte unica)
            sis += ("\n\nMEMORIA DA LUA (o que a Advogada ja ensinou; siga sempre):\n" +
                    lua_reserva["memoria"][:6000])
        if somente_leitura:
            sis += AVISO_AMELIA
        teto = 700 if via == "voz" else 2000
        # 07/10/2026 (Titular: "pode usar a nossa assinatura claude"): pensa pela assinatura do Claude;
        # API e Gemini ficam de reserva, so quando a assinatura falha antes de qualquer ferramenta rodar
        from .. import agentes_assinatura as AA
        pela_api = True
        if AA.ligada():
            try:
                resposta, _rodou = AA.conversar(sis, [dict(m) for m in msgs], ferramentas, processar, RODADAS + 2,
                                                MODELO, via)
                pela_api = False
            except AA.Indisponivel:
                pass
        if pela_api:
            resposta = _responder_pela_api(c, msg_id, sis, msgs, ferramentas, processar, teto)
        if not resposta and ctx["acoes"]:
            resposta = "Fiz isto antes de acabar as tentativas:\n" + "\n".join("- %s" % a for a in ctx["acoes"][:12])
        if not resposta:
            resposta = "Não consegui fechar a resposta agora. Pode perguntar de novo, de um jeito mais direto?"
        resposta = re.sub(r"\s+[—–]\s+", ", ", resposta).replace("—", ", ")   # regra do escritorio: sem travessao
        if lua_reserva and lua_reserva.get("pos"):
            resposta = lua_reserva["pos"](resposta)      # trava do processual na reserva do Assistente
        c.execute("INSERT INTO agente_mensagens (usuario_id, conversa, papel, texto, fontes, criado_em, acoes, via) "
                  "VALUES (?,?,?,?,?,?,?,?)",
                  (uid, conversa, "assistant", resposta, json.dumps(fontes[:8], ensure_ascii=False), time.time(),
                   json.dumps(ctx["acoes"], ensure_ascii=False), via))
        c.commit()
        return {"resposta": resposta, "fontes": fontes[:8], "acoes": ctx["acoes"], "agente": ag}
    finally:
        c.close()


# ---------------------------------------------------------------- rotas
def _publico(a):
    from .. import agentes_voz as V
    out = {k: a.get(k) for k in ("usuario_id", "nome", "genero", "cor", "simbolo", "papel", "foco")}
    out["padrao"] = bool(a.get("padrao"))
    out["voz_id"] = V.voz_do_agente(a)[0]
    return out


@router.get("/meu")
def meu(usuario_id: Optional[int] = None, user=Depends(usuario_atual)):
    uid = _alvo(user, usuario_id)
    c = AG.con()
    try:
        return {"agente": _publico(AG.agente_de(c, uid)), "eu": user.id, "admin": _admin(user),
                "somente_leitura": uid != user.id}
    finally:
        c.close()


@router.get("/equipe")
def equipe(user=Depends(usuario_atual)):
    if not _admin(user):
        raise HTTPException(403, "So administrador")
    c = AG.con()
    try:
        out = []
        for u in c.execute("SELECT id, nome FROM usuarios WHERE ativo=1 ORDER BY nome"):
            if re.search(r"IA\)|automa", u["nome"] or "", re.I):
                continue
            a = AG.agente_de(c, u["id"])
            n = c.execute("SELECT COUNT(*) FROM agente_mensagens WHERE usuario_id=? AND papel='user'", (u["id"],)).fetchone()[0]
            out.append({"usuario_id": u["id"], "pessoa": u["nome"], "agente": _publico(a), "perguntas": n})
        return {"equipe": out}
    finally:
        c.close()


@router.get("/conversa")
def ver_conversa(usuario_id: Optional[int] = None, conversa: Optional[int] = None, user=Depends(usuario_atual)):
    uid = _alvo(user, usuario_id)
    c = AG.con()
    try:
        ultima = c.execute("SELECT COALESCE(MAX(conversa), 1) FROM agente_mensagens WHERE usuario_id=?", (uid,)).fetchone()[0]
        conversa = min(max(int(conversa or ultima), 1), ultima)
        msgs = [{"id": r["id"], "papel": r["papel"], "texto": r["texto"], "fontes": json.loads(r["fontes"] or "[]"),
                 "acoes": json.loads(r["acoes"] or "[]"), "via": r["via"] or "texto",
                 "em": r["criado_em"]} for r in c.execute(
            "SELECT * FROM agente_mensagens WHERE usuario_id=? AND conversa=? AND papel IN ('user','assistant') "
            "ORDER BY id DESC LIMIT 80", (uid, conversa))]
        out = {"mensagens": list(reversed(msgs)), "conversa": conversa, "total_conversas": ultima}
        try:
            from .. import revisao_peca as RV, pecas_agente as PA, operador_agente as OP
            # revisao, peca ou operacao em segundo plano: a tela fica atualizando ate o resultado chegar
            out["trabalhando"] = RV.rodando(uid) + PA.pendentes(uid) + OP.pendentes(uid)
        except Exception:
            out["trabalhando"] = 0
        if _admin(user):
            out["orientacoes"] = [dict(r) for r in c.execute(
                "SELECT id, texto, por, criado_em FROM agente_orientacoes WHERE usuario_id=? AND ativo=1 ORDER BY id", (uid,))]
        return out
    finally:
        c.close()


class MsgIn(BaseModel):
    texto: str


PAM_ID = 3   # Advogada (admin): o Assistente responde a Amel.IA com o que a Advogada ve


def _chave_amelia():
    # o Settings do sistema nao exporta o .env para o ambiente: le direto do backend/.env
    from dotenv import dotenv_values
    from ..config import ENV_LOCAL
    return (dotenv_values(str(ENV_LOCAL)).get("AMELIA_AGATHA_CHAVE") or os.getenv("AMELIA_AGATHA_CHAVE") or "").strip()


@router.post("/amelia")
async def amelia_pergunta(dados: MsgIn, x_amelia_chave: Optional[str] = Header(None)):
    """Amel.IA (Hermes) -> Assistente, so leitura. Autentica pela chave AMELIA_AGATHA_CHAVE do backend/.env, nao por login."""
    chave = _chave_amelia()
    if len(chave) < 24 or not hmac.compare_digest(chave, x_amelia_chave or ""):
        raise HTTPException(403, "chave invalida")
    texto = (dados.texto or "").strip()
    if not texto:
        raise HTTPException(400, "escreva a pergunta")
    if len(texto) > 6000:
        raise HTTPException(400, "pergunta grande demais")
    try:
        r = await asyncio.to_thread(conversar, PAM_ID, texto, PAM_ID, "amelia", True)
        return {"resposta": r["resposta"], "fontes": r["fontes"]}
    except Exception as e:
        raise HTTPException(502, "O Assistente nao respondeu agora: %s" % str(e)[:160])


@router.post("/conversa")
async def falar(dados: MsgIn, user=Depends(usuario_atual)):
    texto = (dados.texto or "").strip()
    if not texto:
        raise HTTPException(400, "escreva a pergunta")
    if len(texto) > 6000:
        raise HTTPException(400, "pergunta grande demais")
    try:
        r = await asyncio.to_thread(conversar, user.id, texto, user.id)
        r.pop("agente", None)
        return r
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, "O agente não respondeu agora: %s" % str(e)[:160])


@router.post("/anexo")
async def anexar_documento(arquivo: UploadFile = File(...), user=Depends(usuario_atual)):
    """09/11/2026 (item 41): a Advogada anexa um documento no chat do Assistente. So guarda; quem le e a ferramenta ler_anexo."""
    from .. import lua_anexos as LX
    if user.id != LX.LUA_UID:
        raise HTTPException(403, "o anexo e so para a conversa do Assistente com a Dra. Advogada")
    dados = await arquivo.read(LX.TETO_DE_BYTES + 1)
    try:
        return await asyncio.to_thread(LX.guardar, arquivo.filename, dados, user.id)
    except LX.Recusado as e:
        raise HTTPException(400, str(e))


@router.post("/voz")
async def falar_por_voz(audio: UploadFile = File(...), user=Depends(usuario_atual)):
    """Conversa por voz: a pessoa fala, o agente entende, age se for o caso e responde falando."""
    from .. import agentes_voz as V
    dados = await audio.read()
    if not dados:
        raise HTTPException(400, "áudio vazio")
    if len(dados) > 12 * 1024 * 1024:
        raise HTTPException(400, "áudio grande demais (fale em partes)")
    texto = await asyncio.to_thread(V.transcrever, dados, audio.content_type or "audio/webm")

    def _ag():
        c = AG.con()
        try:
            return AG.agente_de(c, user.id)
        finally:
            c.close()
    if not texto or len(texto.strip()) < 2:
        ag = await asyncio.to_thread(_ag)
        frase = "Não consegui entender. Pode falar de novo?"
        b, mime, motor = await asyncio.to_thread(V.falar, frase, ag)
        return {"transcricao": "", "resposta": frase, "acoes": [], "fontes": [],
                "audio": base64.b64encode(b).decode() if b else None, "mime": mime, "motor": motor}
    try:
        r = await asyncio.to_thread(conversar, user.id, texto, user.id, "voz")
    except Exception as e:
        raise HTTPException(502, "O agente não respondeu agora: %s" % str(e)[:160])
    ag = r.pop("agente", None)
    b, mime, motor = await asyncio.to_thread(V.falar, V.para_falar(r["resposta"]), ag)
    r.update({"transcricao": texto, "audio": base64.b64encode(b).decode() if b else None, "mime": mime,
              "motor": motor})
    return r


@router.post("/ouvir/{mensagem_id}")
async def ouvir(mensagem_id: int, user=Depends(usuario_atual)):
    """Lê em voz alta uma resposta do agente (da propria pessoa; admin ouve qualquer uma)."""
    from .. import agentes_voz as V

    def _pegar():
        c = AG.con()
        try:
            m = c.execute("SELECT * FROM agente_mensagens WHERE id=? AND papel='assistant'", (mensagem_id,)).fetchone()
            if not m:
                raise HTTPException(404, "mensagem não encontrada")
            _alvo(user, m["usuario_id"])
            return m["texto"], AG.agente_de(c, m["usuario_id"])
        finally:
            c.close()
    texto, ag = await asyncio.to_thread(_pegar)
    b, mime, motor = await asyncio.to_thread(V.falar, V.para_falar(texto, 900), ag)
    if not b:
        raise HTTPException(502, "Não consegui gerar a voz agora (%s)" % motor)
    return {"audio": base64.b64encode(b).decode(), "mime": mime, "motor": motor}


@router.get("/vozes")
async def vozes(user=Depends(usuario_atual)):
    """Opcoes de voz (admin escolhe a de cada agente) e quanto sobra do ElevenLabs no mes."""
    from .. import agentes_voz as V
    if not _admin(user):
        raise HTTPException(403, "So administrador")
    cota = await asyncio.to_thread(V.cota)
    return {"opcoes": {g: [{"voz_id": i, "nome": n} for i, n in lst] for g, lst in V.OPCOES.items()},
            "cota": cota}


@router.post("/{uid}/testar-voz")
async def testar_voz(uid: int, dados: dict, user=Depends(usuario_atual)):
    """Admin ouve como uma voz fica no agente, antes de escolher (frase curta: gasta pouco)."""
    from .. import agentes_voz as V
    if not _admin(user):
        raise HTTPException(403, "So administrador")

    def _ag():
        c = AG.con()
        try:
            return AG.agente_de(c, uid)
        finally:
            c.close()
    ag = dict(await asyncio.to_thread(_ag))
    if dados.get("voz_id"):
        ag["voz_id"] = str(dados["voz_id"])
    frase = "Oi, aqui é %s. É assim que eu vou falar com você." % AG.apresentacao(ag)
    b, mime, motor = await asyncio.to_thread(V.falar, frase, ag)
    if not b:
        raise HTTPException(502, "Não consegui gerar a voz agora (%s)" % motor)
    return {"audio": base64.b64encode(b).decode(), "mime": mime, "motor": motor}


@router.post("/conversa/nova")
def nova(user=Depends(usuario_atual)):
    c = AG.con()
    try:
        atual = c.execute("SELECT COALESCE(MAX(conversa), 1) FROM agente_mensagens WHERE usuario_id=?", (user.id,)).fetchone()[0]
        tem = c.execute("SELECT 1 FROM agente_mensagens WHERE usuario_id=? AND conversa=?", (user.id, atual)).fetchone()
        # a conversa antiga fica guardada (o admin ainda le); so comeca outra
        if tem:
            c.execute("INSERT INTO agente_mensagens (usuario_id, conversa, papel, texto, criado_em) VALUES (?,?,?,?,?)",
                      (user.id, atual + 1, "sistema", "", time.time()))
            c.commit()
        return {"ok": True, "conversa": atual + 1 if tem else atual}
    finally:
        c.close()


class OrientIn(BaseModel):
    texto: str


@router.post("/{uid}/orientacoes")
def orientar(uid: int, dados: OrientIn, user=Depends(usuario_atual)):
    if not _admin(user):
        raise HTTPException(403, "So administrador orienta o agente")
    texto = (dados.texto or "").strip()
    if not texto:
        raise HTTPException(400, "escreva a orientação")
    c = AG.con()
    try:
        c.execute("INSERT INTO agente_orientacoes (usuario_id, texto, por, criado_em) VALUES (?,?,?,?)",
                  (uid, texto[:2000], getattr(user, "nome", "") or "admin", time.time()))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.delete("/orientacoes/{oid}")
def tirar_orientacao(oid: int, user=Depends(usuario_atual)):
    if not _admin(user):
        raise HTTPException(403, "So administrador")
    c = AG.con()
    try:
        c.execute("UPDATE agente_orientacoes SET ativo=0 WHERE id=?", (oid,))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


class AgenteIn(BaseModel):
    nome: Optional[str] = None
    genero: Optional[str] = None
    cor: Optional[str] = None
    simbolo: Optional[str] = None
    foco: Optional[str] = None
    voz_id: Optional[str] = None


@router.put("/{uid}")
def batizar(uid: int, dados: AgenteIn, user=Depends(usuario_atual)):
    """Da nome (ou muda) o agente de alguem. So admin."""
    if not _admin(user):
        raise HTTPException(403, "So administrador")
    c = AG.con()
    try:
        a = AG.agente_de(c, uid)
        novo = {k: v for k, v in dados.model_dump(exclude_unset=True).items() if v is not None}
        a.update(novo)
        c.execute("INSERT OR REPLACE INTO agentes (usuario_id, nome, genero, cor, simbolo, papel, foco, ativo, atualizado_por, "
                  "atualizado_em, voz_id) VALUES (?,?,?,?,?,?,?,1,?,?,?)",
                  (uid, a["nome"], a.get("genero") or "o", a.get("cor"), a.get("simbolo"), a.get("papel"), a.get("foco"),
                   getattr(user, "nome", "") or "admin", time.time(), a.get("voz_id")))
        c.commit()
        return {"ok": True, "agente": _publico(AG.agente_de(c, uid))}
    finally:
        c.close()


# ================================================================ Assistente no Hermes (fatia 1, 07/11/2026)
# Spec: LUA_HERMES/SPEC_lua_hermes.md. O Hermes (perfil lua, 127.0.0.1:8644) e o cerebro; as maos sao as
# ferramentas de hoje, pela rota /hermes/ferramenta, sempre como a Advogada e com as mesmas travas.
# Volta na hora: echo atual > /opt/escritorio/config/lua_cerebro
def _env_backend(nome):
    # o Settings do sistema nao exporta o .env para o ambiente: le direto do backend/.env (molde da Amel.IA)
    from dotenv import dotenv_values
    from ..config import ENV_LOCAL
    return (dotenv_values(str(ENV_LOCAL)).get(nome) or os.getenv(nome) or "").strip()


def _chave_lua():
    chave = _env_backend("LUA_HERMES_CHAVE")
    return chave if len(chave) >= 24 else ""


def _ip(request):
    return request.client.host if request.client else ""


def _ctx_lua(c):
    from .. import agentes_hermes as AH
    eu = _eu(c, AH.LUA_UID)
    ctx = AH.ctx_da_rota({})
    ctx.update({"eu": eu, "admin": (eu.papel or "") == "admin",
                "primeiro": (eu.nome or "").split(" ")[0].capitalize()})
    ctx["registrar"] = lambda ferramenta, alvo, resumo, detalhe=None: _registrar(c, ctx, ferramenta, alvo, resumo,
                                                                                 detalhe)
    return ctx


def _processuais_do_banco(c, AH, pfi):
    """Rede de seguranca da trava: o Hermes lembra de conversas passadas, entao a data processual pode voltar
    sem nenhuma ferramenta neste turno. So as tarefas da Advogada, de 60 dias atras a 240 a frente: com o escritorio
    inteiro, 36 de 51 datas eram o interno de outra tarefa e a trava mostrava data errada (08/11/2026)."""
    hoje = dt.date.today()
    de, ate = hoje - dt.timedelta(days=60), hoje + dt.timedelta(days=240)
    linhas = []
    for r in c.execute("SELECT t.prazo_fatal, t.prazo_interno FROM tarefas t JOIN tarefa_responsaveis r ON r.tarefa_id=t.id "
                       "WHERE r.usuario_id=? AND t.prazo_fatal IS NOT NULL AND t.prazo_fatal != ''", (AH.LUA_UID,)):
        d = AH.data_de(r["prazo_fatal"])
        if d and de <= d <= ate:
            linhas.append({"prazo_fatal": r["prazo_fatal"], "prazo_interno": r["prazo_interno"]})
    return AH.datas_processuais(linhas, pfi)


def _datas_comuns_do_banco(c, AH):
    """As datas das tarefas da Advogada que nao sao processuais: nelas, a trava so troca se a frase disser que e processual."""
    achadas = set()
    for r in c.execute("SELECT t.data, t.prazo_interno FROM tarefas t JOIN tarefa_responsaveis r ON r.tarefa_id=t.id "
                       "WHERE r.usuario_id=?", (AH.LUA_UID,)):
        achadas.update(filter(None, (AH.data_de(r["data"]), AH.data_de(r["prazo_interno"]))))
    return achadas


def _conversar_hermes(uid, texto, autor_id, via):
    from .. import agentes_hermes as AH
    from .. import agentes_fluxos as AF
    from ..tarefas_prazo import pfi_de
    c = AG.con()
    try:
        ag = AG.agente_de(c, uid)
        aviso_ctx = {"uid": uid, "agente": AH.AGENTE, "acoes": []}
        conversa = c.execute("SELECT COALESCE(MAX(conversa), 1) FROM agente_mensagens WHERE usuario_id=?",
                             (uid,)).fetchone()[0]
        orient = [r["texto"] for r in c.execute("SELECT texto FROM agente_orientacoes WHERE usuario_id=? AND ativo=1 "
                                                "ORDER BY id", (uid,))]
        AF.garantir(c)
        pend = [dict(p) for p in c.execute(
            "SELECT id, previa FROM agente_pendentes WHERE usuario_id=? AND status='aguardando' AND criado_em > ? "
            "ORDER BY id DESC LIMIT 5", (uid, time.time() - 3600))]
        # a pergunta vai para o banco ANTES do Hermes: o confirmar_acao so aceita o "sim" que ja esta gravado
        msg_id = AH.gravar_pergunta(c, uid, conversa, texto, autor_id)
        AH.abrir_turno(uid)
        turno = {"processuais": {}, "acoes": [], "previas": [], "comuns": set()}
        gravou_na_reserva = []

        def processuais():
            turno.update(AH.fechar_turno(uid))
            mapa = _processuais_do_banco(c, AH, pfi_de)
            mapa.update(turno["processuais"])      # o que a ferramenta trouxe neste turno e o dado exato da tarefa
            turno["mapa"] = mapa
            turno["comuns"] = set(turno["comuns"]) | _datas_comuns_do_banco(c, AH)
            return mapa

        def motor_atual(t, memoria):
            # o conversar de hoje grava a propria pergunta: tira a que ja foi gravada, para nao ficar em dobro
            c.execute("DELETE FROM agente_mensagens WHERE id=?", (msg_id,))
            c.commit()
            gravou_na_reserva.append(True)
            pos = lambda r: AH.sem_travessao(AH.trava_processual(r, _processuais_do_banco(c, AH, pfi_de),
                                                                 _datas_comuns_do_banco(c, AH))[0])
            return conversar(uid, t, autor_id, via, lua_reserva={"memoria": memoria, "pos": pos})["resposta"]

        def avisar(motivo):
            _registrar(c, aviso_ctx, "hermes_fora", "lua",
                       "O Assistente respondeu pelo motor de hoje: o Hermes nao respondeu (%s)" % str(motivo)[:200])

        url = _env_backend("LUA_HERMES_URL") or AH.HERMES_URL
        chave_hermes = _env_backend("LUA_HERMES_API_KEY")
        resposta, trocas, _pela_reserva = AH.turno_hermes(
            texto, via, dt.datetime.now(), orient, pend,
            lambda m: AH.perguntar(m, chave_hermes, url=url, sessao="%s-%s" % (AH.SESSAO, conversa), timeout=300),
            AH.reserva_sem_repetir(uid, motor_atual), AH.ler_memoria, avisar, processuais,
            previas=lambda: turno["previas"], comuns=lambda: turno["comuns"])
        if not resposta:
            resposta = "Nao consegui fechar a resposta agora. Pode perguntar de novo, de um jeito mais direto?"
        acoes = AH.acoes_sem_processual(turno["acoes"], turno.get("mapa") or {}, turno["comuns"])
        if not gravou_na_reserva:
            AH.gravar_resposta(c, uid, conversa, resposta, acoes)
        if trocas:
            _registrar(c, aviso_ctx, "trava_processual", "lua",
                       "Troquei %d data(s) processual(is) pelo fatal interno na resposta a Advogada" % trocas)
        return {"resposta": resposta, "fontes": [], "acoes": acoes, "agente": ag}
    finally:
        AH.fechar_turno(uid)
        c.close()


@router.get("/hermes/ferramentas")
def lua_hermes_ferramentas(request: Request, x_lua_chave: Optional[str] = Header(None)):
    """Lista das ferramentas do Assistente para o MCP (app/lua_mcp.py). So de dentro do servidor, com a chave."""
    from .. import agentes_hermes as AH
    from .. import agentes_fluxos as AF
    if not AH.chave_ok(x_lua_chave, _chave_lua(), _ip(request), dict(request.headers)):
        raise HTTPException(403, "chave invalida")
    c = AG.con()
    try:
        return {"tools": AH.ferramentas_para_mcp(FERRAMENTAS + AF.ferramentas(_ctx_lua(c)))}
    finally:
        c.close()


@router.post("/hermes/ferramenta")
def lua_hermes_ferramenta(dados: dict, request: Request, x_lua_chave: Optional[str] = Header(None)):
    """As maos do Assistente no Hermes: executa uma ferramenta como a Advogada (uid 5), com as travas de hoje."""
    from .. import agentes_hermes as AH
    from .. import agentes_fluxos as AF
    from ..tarefas_prazo import pfi_de
    c = AG.con()
    try:
        status, out = AH.atender_rota(
            x_lua_chave, _chave_lua(), _ip(request), dict(request.headers), dados, lambda: _ctx_lua(c),
            lambda ctx, nome, entrada: executar(c, ctx, nome, entrada),
            lambda ctx, nome, entrada, previa: AF.pedir_confirmacao(c, ctx, nome, entrada, previa)["codigo"],
            pfi_de,
            ultima_msg=lambda: (c.execute("SELECT texto FROM agente_mensagens WHERE usuario_id=? AND papel='user' "
                                          "ORDER BY id DESC LIMIT 1", (AH.LUA_UID,)).fetchone() or [""])[0],
            ultima_previa=lambda: c.execute("SELECT MAX(id) FROM agente_pendentes WHERE usuario_id=? AND "
                                            "status='aguardando'", (AH.LUA_UID,)).fetchone()[0],
            falta_ler_autos=lambda nome, entrada: AF.falta_ler_autos(c, nome, entrada))
    finally:
        c.close()
    if status != 200:
        raise HTTPException(status, out.get("erro"))
    return out


# Vindas do Hermes, estas viram previa; o confirmar_acao so roda o que esta em EXTERNAS. O motor de hoje nao muda.
def _externas_da_lua():
    from .. import agentes_fluxos as AF
    from .. import agentes_hermes as AH
    AF.EXTERNAS.update({"concluir_tarefa": concluir_tarefa, "remarcar_tarefa": remarcar_tarefa,
                        "cancelar_compromisso": cancelar_compromisso, "dar_ciencia": AF.dar_ciencia})
    for nome in AH.PREVIA_SE_LEU_DE_FORA:       # D26: toda acao interna que vira previa tem como ser confirmada
        funcao = ACOES.get(nome) or AF.ACOES.get(nome)
        if funcao:
            AF.EXTERNAS.setdefault(nome, funcao)


_externas_da_lua()
