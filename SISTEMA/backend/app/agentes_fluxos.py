"""FLUXOS DO ESCRITORIO NA MAO DOS AGENTES (29/09/2026, pedido do Titular: "eles terao acesso
total ao backend ... se a Advogada pedir para revisar, acione o nosso fluxo; o marketing, ver, analisar,
publicar, tudo pelo Assistente ... quero tudo como se fossem assistentes mesmo").

Como o agente mexe no sistema: pela PROPRIA API do sistema, como a pessoa (token dela, gerado aqui
dentro e nunca exposto). Assim vale exatamente o que ela pode fazer na tela, com as mesmas travas
e automacoes, sem atalho no banco.

Areas (por pessoa): juridico (Advogada), comercial (Titular e vendedor),
marketing (Titular); Advogada e o admin tem todas.

ACAO EXTERNA (chega em cliente/lead ou dispara robo que chega): responder lead no WhatsApp e mover
card para coluna que aciona automacao (contratacao gera contrato + ZapSign + ADVBOX). Essas NAO
executam na hora: a ferramenta devolve uma previa e um codigo; so rodam com confirmar_acao(codigo)
DEPOIS que a pessoa respondeu (o servidor confere que houve uma mensagem nova dela depois da previa).
"""
import datetime as dt
import json
import os
import re
import sqlite3
import time

import requests

API = os.environ.get("ESC_API_INTERNA", "http://127.0.0.1:8000")
AREAS_POR_USUARIO = {3: {"juridico", "marketing", "comercial"}, 5: {"juridico"}, 6: {"juridico"},
                     7: {"comercial"}, 10: {"comercial"}, 11: {"marketing"}}
PIPE_LEADS, PIPE_CONTRATACAO = 10, 20
# mover para estas colunas do Reclamantes nao dispara robo que fala com o lead
COLUNAS_SEGURAS = {"Em Qualificação", "Lead Qualificado", "Contatado", "Lead Desqualificado", "Perdido",
                   "Perdido - Sem resposta", "Perdido - Sem interesse"}
AVISO_COLUNA = {
    "Caixa de Entrada": "o robô de leads quentes pode mandar até 4 mensagens para o lead",
    "Lead Quente": "o robô de leads quentes pode mandar mensagens para o lead",
    "Pré fechamento": "entra na régua de fechamento",
    "Fechou! Boas vindas": "o robô manda o kit de boas-vindas (8 mensagens) para o cliente",
    "Fase de Contratação": "o robô gera contrato, procuração e declaração, manda no ZapSign, cadastra no ADVBOX e manda os links para o cliente",
    "Contrato no ZapSign": "o robô passa a cobrar a assinatura do cliente todo dia",
}


def areas(ctx):
    # 01/10/2026 (Titular): "todas as tarefas que o backend tem permissao para serem feitas, elas tambem vao
    # ter permissao de fazer nos usuarios delas". Toda pessoa tem as ferramentas de todas as areas; quem
    # barra e o proprio sistema, porque o agente chama a API com o token da pessoa (403 = ela nao pode).
    # AREAS_POR_USUARIO segue so como o foco de cada um.
    return {"juridico", "marketing", "comercial"}


# ---------------------------------------------------------------- API do proprio sistema, como a pessoa
def _token(ctx):
    if "_token" not in ctx:
        from .security import criar_token
        ctx["_token"] = criar_token(ctx["eu"].email, ctx["eu"].papel or "", ctx["eu"].nome or "")
    return ctx["_token"]


# 02/10/2026 (Titular): "quero que o Assistente e o Assistente TENHAM PERMISSAO TOTAL AO BACKEND". Para elas, o que o usuario
# da pessoa nao pode (403) o agente refaz como a administracao; fica registrado em agente_acoes ("elevado").
from .juridico import equipe as _EQ
ELEVADOS = {_EQ.pessoa_do_papel("revisora").get("id", 2), _EQ.pessoa_do_papel("titular").get("id", 1)}
LUA_UID = 5
ENVIO_DE_MENSAGEM = {"mensagem_cliente", "responder_lead"}


def total(ctx):
    return bool(ctx.get("admin")) or ctx.get("uid") in ELEVADOS


def _token_admin(ctx):
    if "_token_admin" not in ctx:
        from .security import criar_token
        from . import agentes_nucleo as AG
        c = AG.con()
        try:
            u = c.execute("SELECT email, papel, nome FROM usuarios WHERE papel='admin' AND ativo=1 "
                          "ORDER BY CASE WHEN email LIKE 'titular%' THEN 0 ELSE 1 END, id LIMIT 1").fetchone()
        finally:
            c.close()
        ctx["_token_admin"] = criar_token(u["email"], u["papel"], u["nome"]) if u else None
    return ctx["_token_admin"]


def api(ctx, metodo, rota, corpo=None, params=None, timeout=90):
    r = requests.request(metodo, API + rota, json=corpo, params=params, timeout=timeout,
                         headers={"Authorization": "Bearer " + _token(ctx)})
    if r.status_code == 403 and total(ctx) and not ctx.get("admin") and _token_admin(ctx):
        r = requests.request(metodo, API + rota, json=corpo, params=params, timeout=timeout,
                             headers={"Authorization": "Bearer " + _token_admin(ctx)})
        if r.status_code < 400:
            ctx["leitura_elevada"] = True       # 09/11/2026 (D21): neste turno o Assistente nao busca na internet
        if r.status_code < 400 and metodo != "GET" and ctx.get("registrar"):
            ctx["registrar"]("elevado", rota, "Fiz como administração (o usuário de %s não tinha permissão): %s %s"
                             % (ctx.get("primeiro"), metodo, rota))
    try:
        dados = r.json()
    except ValueError:
        dados = {"texto": r.text[:300]}
    if r.status_code >= 400:
        det = dados.get("detail") if isinstance(dados, dict) else None
        raise RuntimeError("o sistema recusou (%s): %s" % (r.status_code, str(det or dados)[:200]))
    return dados


# ---------------------------------------------------------------- confirmacao de acao externa
def garantir(c):
    c.execute("""CREATE TABLE IF NOT EXISTS agente_pendentes (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER, ferramenta TEXT, args TEXT, previa TEXT,
                   ultima_msg_id INTEGER, criado_em REAL, status TEXT DEFAULT 'aguardando', resultado TEXT)""")


def _ultima_msg_dela(c, uid):
    r = c.execute("SELECT MAX(id) FROM agente_mensagens WHERE usuario_id=? AND papel='user'", (uid,)).fetchone()
    return r[0] or 0


# 29/09 (teste do Assistente no Gemini): o modelo confirmou uma tarefa no ADVBOX quando a pessoa ja estava
# fazendo OUTRO pedido. Agora o servidor so aceita se a ultima mensagem for um SIM curto e claro.
_SIM = re.compile(r"\b(sim|pode|podes|confirm[oa]|confirmado|autorizo|manda|mande|envia|envie|cria|crie|lan[cç]a|lance|"
                  r"fa[cç]a|faz|ok|okay|isso|claro|certo|beleza|blz|positivo|fechado|bora|segue|siga|move|mova)\b", re.I)
_NAO = re.compile(r"\b(n[aã]o|cancela|cancele|pare|espera|espere|nem|nunca)\b", re.I)


def _e_um_sim(c, uid):
    r = c.execute("SELECT texto FROM agente_mensagens WHERE usuario_id=? AND papel='user' ORDER BY id DESC LIMIT 1",
                  (uid,)).fetchone()
    t = (r[0] if r else "") or ""
    return len(t) <= 80 and bool(_SIM.search(t)) and not _NAO.search(t)


# 08/11/2026 (item 33): em 05/11 a Advogada pediu para mudar o texto, o Assistente mostrou o ajuste so na conversa e o "sim"
# enviou a versao antiga guardada. Na Assistente, qualquer fala depois da previa que nao seja so concordancia invalida a
# previa, e previa nova da mesma mensagem substitui a anterior.
def _falou_outra_coisa(c, uid, ultima_msg_id):
    from . import agentes_hermes as AH
    falas = c.execute("SELECT texto FROM agente_mensagens WHERE usuario_id=? AND papel='user' AND id>?",
                      (uid, ultima_msg_id)).fetchall()
    return any(not AH.e_um_sim(fala[0]) for fala in falas)


def _substituir_previa_anterior(c, uid, ferramenta, args):
    if ferramenta not in ENVIO_DE_MENSAGEM:
        return
    destino = args.get("conversa_id") or args.get("telefone")
    for p in c.execute("SELECT id, args FROM agente_pendentes WHERE usuario_id=? AND status='aguardando' AND "
                       "ferramenta IN ('mensagem_cliente', 'responder_lead')", (uid,)).fetchall():
        antes = json.loads(p["args"])
        if (antes.get("conversa_id") or antes.get("telefone")) == destino:
            c.execute("UPDATE agente_pendentes SET status='substituida' WHERE id=?", (p["id"],))


def _chave_telefone(telefone):
    """DDD e os 8 ultimos digitos: o mesmo numero com e sem o 9, com e sem o 55, e a mesma pessoa."""
    digitos = re.sub(r"\D", "", telefone or "")
    if digitos.startswith("55") and len(digitos) >= 12:
        digitos = digitos[2:]
    return digitos[:2] + digitos[-8:]


def pedir_confirmacao(c, ctx, ferramenta, args, previa):
    garantir(c)
    if ctx["uid"] == LUA_UID:
        _substituir_previa_anterior(c, ctx["uid"], ferramenta, args)
    if ctx.get("via_hermes"):
        from . import agentes_hermes as AH
        previa = AH.previa_fiel(previa, args)
    cur = c.execute("INSERT INTO agente_pendentes (usuario_id, ferramenta, args, previa, ultima_msg_id, criado_em) "
                    "VALUES (?,?,?,?,?,?)", (ctx["uid"], ferramenta, json.dumps(args, ensure_ascii=False), previa,
                                             _ultima_msg_dela(c, ctx["uid"]), time.time()))
    c.commit()
    instrucao = ("NÃO foi feito ainda. Mostre a prévia exatamente e pergunte se pode. Só chame confirmar_acao "
                 "com esse código depois que a pessoa disser que sim, numa próxima mensagem.")
    if ctx["uid"] == LUA_UID:
        instrucao += (" Se a pessoa pedir qualquer mudança, chame esta ferramenta de novo com o texto final e mostre "
                      "a prévia nova. Nunca reescreva a prévia só na conversa: o que sai é o que está guardado.")
    return {"precisa_confirmar": True, "codigo": cur.lastrowid, "previa": previa, "instrucao": instrucao}


def confirmar_acao(c, ctx, codigo):
    garantir(c)
    p = c.execute("SELECT * FROM agente_pendentes WHERE id=? AND usuario_id=?", (int(codigo), ctx["uid"])).fetchone()
    if not p:
        return {"erro": "não achei essa ação pendente"}
    if p["status"] != "aguardando":
        return {"erro": "essa ação já foi %s" % p["status"]}
    if time.time() - p["criado_em"] > 3600:
        c.execute("UPDATE agente_pendentes SET status='expirada' WHERE id=?", (p["id"],))
        c.commit()
        return {"erro": "a prévia passou de 1 hora; faça o pedido de novo"}
    if _ultima_msg_dela(c, ctx["uid"]) <= p["ultima_msg_id"]:
        return {"erro": "a pessoa ainda não respondeu à prévia. Mostre a prévia e espere o sim."}
    if ctx["uid"] == LUA_UID and _falou_outra_coisa(c, ctx["uid"], p["ultima_msg_id"]):
        c.execute("UPDATE agente_pendentes SET status='substituida' WHERE id=? AND status='aguardando'", (p["id"],))
        c.commit()
        return {"erro": "a pessoa falou outra coisa depois dessa prévia, então o sim não vale para ela. NÃO executei. "
                        "Chame a ferramenta de novo com o texto final, mostre a prévia nova e espere o sim."}
    if not _e_um_sim(c, ctx["uid"]):
        return {"erro": "a última mensagem da pessoa não é um sim claro para essa prévia. NÃO executei. Mostre a prévia "
                        "de novo e pergunte se pode, antes de qualquer outra coisa."}
    if ctx.get("via_hermes"):
        tomou = c.execute("UPDATE agente_pendentes SET status='executando' WHERE id=? AND status='aguardando'", (p["id"],)).rowcount
        c.commit()
        if tomou != 1:
            return {"erro": "essa ação já está em andamento"}
    fn = EXTERNAS.get(p["ferramenta"])
    if not fn:
        return {"erro": "ação desconhecida"}
    try:
        res = fn(c, ctx, **json.loads(p["args"]))
    except Exception as e:
        res = {"erro": str(e)[:300]}
    c.execute("UPDATE agente_pendentes SET status=?, resultado=? WHERE id=?",
              ("feita" if not res.get("erro") else "falhou", json.dumps(res, ensure_ascii=False)[:2000], p["id"]))
    c.commit()
    return res


def cancelar_acao(c, ctx, codigo):
    garantir(c)
    c.execute("UPDATE agente_pendentes SET status='cancelada' WHERE id=? AND usuario_id=? AND status='aguardando'",
              (int(codigo), ctx["uid"]))
    c.commit()
    return {"ok": True, "feito": "Cancelei a ação pendente."}


# ================================================================ JURIDICO
def revisar_peca(c, ctx, tarefa_id=None, link=None, foco=None):
    from . import revisao_peca as RV
    rid, erro = RV.iniciar(ctx["uid"], ctx["agente"], tarefa_id, link, foco)
    if erro:
        return {"erro": erro}
    ctx["registrar"]("revisar_peca", tarefa_id or link, "Mandei a peça%s para a revisão da Revisora IA" %
                     (" da tarefa #%s" % tarefa_id if tarefa_id else ""), {"revisao": rid, "link": link})
    return {"ok": True, "revisao_id": rid,
            "feito": "A revisão começou em segundo plano (leva uns 3 a 6 minutos). O parecer aparece aqui na conversa "
                     "e fica salvo no Drive, na pasta da peça. A pessoa pode continuar conversando."}


def minhas_revisoes(c, ctx):
    from . import revisao_peca as RV
    return {"revisoes": RV.listar(ctx["uid"])}


def ver_parecer(c, ctx, revisao_id):
    from . import revisao_peca as RV
    p = RV.parecer(revisao_id, ctx["uid"], ctx["admin"])
    return p or {"erro": "revisão não encontrada"}


def marcar_checklist(c, ctx, tarefa_id, etapa, feito=True):
    r = api(ctx, "GET", "/api/roteiros/tarefa/%s" % int(tarefa_id))
    chk = r.get("checklist") or {}
    etapas = chk.get("etapas") or []
    if not etapas:
        return {"erro": "essa tarefa não tem checklist"}
    alvo = str(etapa or "").strip().lower()
    e = next((x for x in etapas if x["chave"] == alvo or alvo in (x.get("rotulo") or "").lower()), None)
    if not e:
        return {"erro": "etapa não encontrada. Etapas: " + "; ".join("%s (%s)" % (x["chave"], x["rotulo"]) for x in etapas)}
    api(ctx, "POST", "/api/roteiros/tarefa/%s/%s" % (int(tarefa_id), e["chave"]), {"feito": bool(feito)})
    resumo = "%s no checklist da tarefa #%s: %s" % ("Marquei" if feito else "Desmarquei", tarefa_id, e["rotulo"])
    ctx["registrar"]("marcar_checklist", tarefa_id, resumo)
    return {"ok": True, "feito": resumo + "."}


def intimacoes_pendentes(c, ctx, so_minhas=True, limite=25):
    d = api(ctx, "GET", "/api/controladoria/intimacoes", params={"status": "pendentes"}, timeout=120)
    nome = (ctx["eu"].nome or "").split(" ")[0].upper()
    out = []
    for it in d.get("intimacoes") or []:
        mot = it.get("motor") or {}
        resp = str(mot.get("responsavel") or "")
        if so_minhas and not ctx["admin"] and nome and resp and nome not in resp.upper():
            continue
        out.append({"id": it.get("id"), "processo": it.get("processo"), "tribunal": it.get("tribunal"),
                    "ato": mot.get("ato") or it.get("tipo_ato") or it.get("tipo_comunicacao"),
                    "prazo_fatal": mot.get("vencimento") or it.get("data_fatal"),
                    "prazo_interno": mot.get("prazo_fatal_interno"), "responsavel": resp or None,
                    "manifestacao": bool(it.get("requer_manifestacao")), "resumo": (it.get("resumo") or "")[:300]})
    out.sort(key=lambda x: str(x.get("prazo_interno") or x.get("prazo_fatal") or "9999"))
    return {"total": len(out), "intimacoes": out[:max(1, min(int(limite or 25), 60))]}


def dar_ciencia(c, ctx, ids, motivo=None):
    ids = [int(i) for i in (ids or [])][:50]
    if not ids:
        return {"erro": "diga quais intimações"}
    api(ctx, "POST", "/api/controladoria/intimacoes/ciente",
        {"ids": ids, "motivo": motivo or "ciência dada por %s a pedido de %s" % (ctx["agente"], ctx["primeiro"])})
    resumo = "Dei ciência em %d intimação(ões): %s" % (len(ids), ", ".join(map(str, ids)))
    ctx["registrar"]("dar_ciencia", ",".join(map(str, ids)), resumo)
    return {"ok": True, "feito": resumo + "."}


# ---------------------------------------------------------------- JURIDICO: peca, ADVBOX e cliente (29/09 noite)
# "Ela pode escrever pecas tambem, mexer no ADVBOX e mandar mensagem pra cliente" (Titular).
# Peca: pedido na fila (app/pecas_agente.py) -> vigia SYNC/pecas_agente.py escreve pela assinatura.
# ADVBOX e mensagem para cliente sao ACAO EXTERNA (tarefa no ADVBOX nao se apaga; mensagem nao volta):
# previa + sim da pessoa, como no comercial.
def _processo(c, termo):
    """Processo pelo numero (ou parte do nome de uma parte) -> dict com lawsuit do ADVBOX e clientes."""
    import re as _re
    termo = str(termo or "").strip()
    if not termo:
        return None
    dig = _re.sub(r"\D", "", termo)
    linhas = []
    if len(dig) >= 12:
        linhas = [r for r in c.execute("SELECT * FROM jur_processos WHERE numero IS NOT NULL") if _re.sub(r"\D", "", r["numero"] or "") == dig]
    if not linhas:
        linhas = c.execute("SELECT DISTINCT p.* FROM jur_processos p JOIN jur_processo_partes pp ON pp.processo_id=p.id "
                           "WHERE pp.nome LIKE ? AND COALESCE(p.encerrado,0)=0 LIMIT 5", ("%" + termo + "%",)).fetchall()
    if len(linhas) != 1:
        return {"ambiguo": [r["numero"] for r in linhas]} if linhas else None
    p = linhas[0]
    partes = c.execute("SELECT pp.nome, pp.papel, cl.telefone, cl.celular, cl.id cid FROM jur_processo_partes pp "
                       "LEFT JOIN jur_clientes cl ON cl.id=pp.cliente_id WHERE pp.processo_id=?", (p["id"],)).fetchall()
    clientes = [dict(x) for x in partes if not str(x["papel"] or "").upper().startswith("PARTE CONTR")]
    return {"id": p["id"], "numero": p["numero"], "lawsuit_id": p["advbox_id"], "pasta": p["pasta"], "clientes": clientes,
            "adversa": " / ".join(x["nome"] for x in partes if str(x["papel"] or "").upper().startswith("PARTE CONTR"))}


def _advbox():
    """O modulo do ADVBOX le a chave do ambiente e da sys.exit sem ela: carrega o .env antes."""
    import sys as _sys
    from dotenv import load_dotenv
    from .config import ENV_CENTRAL
    load_dotenv(str(ENV_CENTRAL))
    if "/opt/escritorio/INTEGRACOES" not in _sys.path:
        _sys.path.insert(0, "/opt/escritorio/INTEGRACOES")
    import advbox_integration as ab
    import tarefas_esc as tp
    return ab, tp


def _cnj_do_pedido(c, numero):
    """O CNJ que a producao vai usar: pedido pelo nome da parte tem de chegar la como numero."""
    from . import lua_autos as LA
    cnj = LA.cnj_no_texto(numero)
    if not cnj and numero:
        pr = _processo(c, numero)
        cnj = LA.numero_cnj(pr["numero"]) if pr and not pr.get("ambiguo") else None
    return cnj


def escrever_peca(c, ctx, tipo_peca, instrucoes, tarefa_id=None, processo=None, pasta_drive=None, revisar_depois=True):
    from . import pecas_agente as PA
    t = c.execute("SELECT * FROM tarefas WHERE id=?", (int(tarefa_id),)).fetchone() if tarefa_id else None
    if tarefa_id and not t:
        return {"erro": "tarefa #%s não encontrada" % tarefa_id}
    proc = processo or (t["processo_numero"] if t else None)
    if ctx.get("uid") == LUA_UID:
        proc = _cnj_do_pedido(c, proc) or proc
    cliente = t["cliente"] if t else None
    if proc and not cliente:
        pr = _processo(c, proc)
        if pr and not pr.get("ambiguo"):
            cliente = " / ".join(x["nome"] for x in pr["clientes"][:2]) or None
    if not (proc or t or pasta_drive):
        return {"erro": "diga o processo, a tarefa ou a pasta do Drive do caso, para eu achar os documentos"}
    pid, frente = PA.pedir(ctx["uid"], ctx["agente"], str(tipo_peca).strip()[:80], str(instrucoes or "").strip()[:4000],
                           t["id"] if t else None, proc, cliente, pasta_drive, revisar_depois)
    resumo = "Coloquei na produção: %s%s%s" % (tipo_peca, " de " + cliente if cliente else "", " (processo %s)" % proc if proc else "")
    ctx["registrar"]("escrever_peca", pid, resumo, {"tarefa": tarefa_id, "processo": proc})
    return {"ok": True, "pedido": pid, "feito": resumo + ". A peça é escrita em segundo plano (uns 20 a 40 minutos%s) "
            "e sai no timbrado; eu aviso aqui com o link%s." % (
                ", %d pedido(s) na frente" % frente if frente else "",
                " e mando para a revisão da Revisora IA em seguida" if revisar_depois else "")}


def minhas_pecas(c, ctx):
    from . import pecas_agente as PA
    return {"pecas": PA.listar(ctx["uid"])}


def advbox_criar_tarefa(c, ctx, processo, tipo, responsaveis, comentario, data=None, prazo_fatal=None, urgente=False):
    pr = _processo(c, processo)
    if not pr:
        return {"erro": "não achei o processo %s no cadastro" % processo}
    if pr.get("ambiguo"):
        return {"erro": "achei mais de um processo: %s. Diga o número." % ", ".join(pr["ambiguo"])}
    if not pr.get("lawsuit_id"):
        return {"erro": "esse processo não está ligado ao ADVBOX (sem id do ADVBOX no cadastro)"}
    try:
        ab, tp = _advbox()
        task_id = ab.buscar_tipo_tarefa(str(tipo))
    except BaseException as e:
        return {"erro": "ADVBOX indisponível agora: %s" % str(e)[:120]}
    if not task_id:
        return {"erro": "não existe tipo de tarefa \"%s\" no ADVBOX" % tipo}
    inverso = {v: k for k, v in tp.USUARIOS.items()}
    todos = c.execute("SELECT id, nome FROM usuarios WHERE ativo=1").fetchall()
    guests, nomes, falta = [], [], []
    for n in (responsaveis or []):
        alvo = str(n).strip().lower()
        u = next((x for x in todos if (x["nome"] or "").lower().split()[0] == alvo.split()[0]), None) if alvo else None
        if u and inverso.get(u["id"]):
            guests.append(inverso[u["id"]])
            nomes.append(u["nome"].split()[0])
        else:
            falta.append(str(n))
    if falta or not guests:
        return {"erro": "não achei no ADVBOX: %s" % (", ".join(falta) or "responsável")}
    texto = str(comentario or "").strip() + "\n\n(Criada por %s a pedido de %s, pelo Sistema Escritório Modelo.)" % (ctx["agente"], ctx["primeiro"])
    args = {"lawsuit_id": pr["lawsuit_id"], "task_id": task_id, "guests": guests, "comentario": texto,
            "data": data or None, "prazo_fatal": prazo_fatal or None, "urgente": bool(urgente),
            "resumo": "%s no ADVBOX para %s, processo %s" % (str(tipo).upper(), ", ".join(nomes), pr["numero"])}
    return pedir_confirmacao(c, ctx, "advbox_criar_tarefa", args,
                             "Criar tarefa NO ADVBOX (depois não dá para apagar): %s, processo %s (%s), para %s%s%s%s.\n"
                             "Texto: \"%s\"" % (str(tipo).upper(), pr["numero"],
                                                " / ".join(x["nome"] for x in pr["clientes"][:2]) or "-", ", ".join(nomes),
                                                ", data " + data if data else "", ", prazo fatal " + prazo_fatal if prazo_fatal else "",
                                                ", urgente" if urgente else "", str(comentario or "").strip()[:500]))


def _advbox_criar_tarefa(c, ctx, lawsuit_id, task_id, guests, comentario, data=None, prazo_fatal=None, urgente=False, resumo=""):
    ab, _ = _advbox()
    try:
        r = ab.criar_publicacao(lawsuit_id, task_id, guests, comentario, None, prazo_fatal, urgente, data)
    except BaseException as e:
        return {"erro": "o ADVBOX recusou: %s" % str(e)[:160]}
    if not r:
        return {"erro": "o ADVBOX não confirmou a criação"}
    ctx["registrar"]("advbox_criar_tarefa", lawsuit_id, "Criei tarefa " + resumo, {"resposta": str(r)[:300]})
    return {"ok": True, "feito": "Criei tarefa " + resumo + "."}


def advbox_andamento(c, ctx, processo, texto, data=None):
    pr = _processo(c, processo)
    if not pr or pr.get("ambiguo") or not pr.get("lawsuit_id"):
        return {"erro": "processo não encontrado ou sem ligação com o ADVBOX"}
    texto = str(texto or "").strip()
    if len(texto) < 10:
        return {"erro": "o andamento precisa de pelo menos 10 caracteres"}
    dia = dt.date.fromisoformat(data).strftime("%d/%m/%Y") if data else dt.date.today().strftime("%d/%m/%Y")
    return pedir_confirmacao(c, ctx, "advbox_andamento",
                             {"lawsuit_id": pr["lawsuit_id"], "data": dia, "texto": texto, "numero": pr["numero"]},
                             "Lançar andamento NO ADVBOX (depois não dá para apagar) no processo %s, data %s:\n\"%s\""
                             % (pr["numero"], dia, texto[:600]))


def _advbox_andamento(c, ctx, lawsuit_id, data, texto, numero=""):
    ab, _ = _advbox()
    try:
        r = ab.criar_movimentacao(lawsuit_id, data, texto)
    except BaseException as e:
        return {"erro": "o ADVBOX recusou: %s" % str(e)[:160]}
    if not r:
        return {"erro": "o ADVBOX não confirmou o andamento"}
    ctx["registrar"]("advbox_andamento", lawsuit_id, "Lancei andamento no ADVBOX (processo %s)" % numero, {"texto": texto})
    return {"ok": True, "feito": "Lancei o andamento no ADVBOX, processo %s." % numero}


def mensagem_cliente(c, ctx, texto, cliente=None, processo=None, telefone=None):
    import re as _re
    texto = str(texto or "").strip()
    if not texto:
        return {"erro": "escreva a mensagem"}
    nome, tel = None, _re.sub(r"\D", "", str(telefone or ""))
    if not tel:
        pr = _processo(c, processo or cliente)
        if pr and pr.get("ambiguo"):
            return {"erro": "achei mais de um processo: %s. Diga qual." % ", ".join(pr["ambiguo"])}
        cand = (pr or {}).get("clientes") or []
        if cliente:
            cand = [x for x in cand if str(cliente).lower().split()[0] in (x["nome"] or "").lower()] or cand
        cand = [x for x in cand if (x.get("celular") or x.get("telefone"))]
        if len(cand) != 1:
            return {"erro": "não achei um telefone único do cliente no cadastro%s. Diga o telefone." %
                            (" (%s)" % ", ".join(x["nome"] for x in cand) if cand else "")}
        nome, tel = cand[0]["nome"], _re.sub(r"\D", "", cand[0]["celular"] or cand[0]["telefone"])
    if len(tel) < 10:
        return {"erro": "telefone inválido"}
    ult8 = tel[-8:]
    conv = c.execute("SELECT id, contato_nome, ia_ativa FROM wa_conversas WHERE numero_id=1 AND contato_telefone LIKE ? "
                     "ORDER BY atualizado_em DESC LIMIT 1", ("%" + ult8,)).fetchone()
    aviso_conversa = ""
    if ctx["uid"] == LUA_UID:
        candidatas = c.execute("SELECT id, contato_nome, contato_telefone, ia_ativa FROM wa_conversas WHERE numero_id=1 "
                               "AND contato_telefone LIKE ? ORDER BY atualizado_em DESC", ("%" + ult8,)).fetchall()
        exatas = [x for x in candidatas if _chave_telefone(x["contato_telefone"]) == _chave_telefone(tel)]
        if exatas:
            conv = exatas[0]
        elif candidatas:
            return {"erro": "não há conversa com esse telefone, só com o mesmo final em outro DDD: %s. Diga o telefone completo certo." %
                            "; ".join("%s (%s)" % (x["contato_nome"], x["contato_telefone"]) for x in candidatas[:5])}
        if conv and nome and conv["contato_nome"] != nome:
            aviso_conversa = "\n(Vai cair na conversa de %s.)" % conv["contato_nome"]
    return pedir_confirmacao(c, ctx, "mensagem_cliente",
                             {"telefone": tel, "nome": nome or (conv["contato_nome"] if conv else tel), "texto": texto,
                              "conversa_id": conv["id"] if conv else None},
                             "Mandar no WhatsApp do escritório para o cliente %s (%s):\n\"%s\"%s" % (
                                 nome or (conv["contato_nome"] if conv else ""), tel, texto,
                                 aviso_conversa if conv else "\n(Ele ainda não tem conversa no Chat ao Vivo: eu abro o card no board "
                                                 "Clientes e mando.)"))


def _mensagem_cliente(c, ctx, telefone, nome, texto, conversa_id=None):
    if not conversa_id:
        d = api(ctx, "POST", "/api/atendimento/conversas", {"numero_id": 1, "contato_nome": nome, "contato_telefone": telefone,
                                                            "pipeline_id": 11, "origem": "agente " + ctx["agente"]})
        conversa_id = d.get("id") or (d.get("conversa") or {}).get("id")
        if not conversa_id:
            return {"erro": "não consegui abrir a conversa do cliente no Chat ao Vivo"}
    api(ctx, "POST", "/api/atendimento/conversas/%s/responder" % int(conversa_id), {"texto": texto})
    resumo = "Mandei mensagem no WhatsApp para o cliente %s" % nome
    ctx["registrar"]("mensagem_cliente", conversa_id, resumo, {"texto": texto, "telefone": telefone})
    return dict({"ok": True, "feito": resumo + "."}, **({"texto_enviado": texto} if ctx["uid"] == LUA_UID else {}))


def _encaminhar_email(c, ctx, email_id, para, comentario=""):
    from . import lua_email as LE
    out = LE.encaminhar(email_id, para, comentario)
    if out.get("ok") and ctx.get("registrar"):
        ctx["registrar"]("encaminhar_email", out.get("para"), "Encaminhei e-mail para %s: %s" % (out.get("para"), out.get("assunto")),
                         {"comentario": comentario, "email_id": email_id})
    return out


def _enviar_email(c, ctx, para, assunto, texto, anexos=None):
    from . import lua_email as LE
    out = LE.enviar(para, assunto, texto, anexos)
    if out.get("ok") and ctx.get("registrar"):
        ctx["registrar"]("enviar_email", out.get("para"), "Enviei e-mail para %s: %s" % (out.get("para"), out.get("assunto")),
                         {"texto": out.get("texto_enviado")})
    return out


# ================================================================ MARKETING (Estudio de Conteudo + anuncios)
def pauta_conteudo(c, ctx, estado=None, limite=20):
    d = api(ctx, "GET", "/api/estudio/pauta", params={"estado": estado or "", "limite": max(1, min(int(limite or 20), 80))})
    itens = [{k: x.get(k) for k in ("id", "tema", "formato", "estado", "titulo", "data_prevista", "publicado_em", "erro")}
             for x in d.get("itens") or []]
    return {"contagem": d.get("contagem"), "itens": itens}


def ver_conteudo(c, ctx, pauta_id):
    d = api(ctx, "GET", "/api/estudio/pauta", params={"limite": 80})
    x = next((i for i in d.get("itens") or [] if int(i.get("id") or 0) == int(pauta_id)), None)
    if not x:
        return {"erro": "peça #%s não encontrada no Estúdio" % pauta_id}
    img = x.get("imagens")
    if isinstance(img, str):
        try:
            img = json.loads(img)
        except ValueError:
            img = None
    slides = x.get("slides")
    if isinstance(slides, str):
        try:
            slides = json.loads(slides)
        except ValueError:
            pass
    return {"id": x.get("id"), "tema": x.get("tema"), "formato": x.get("formato"), "estado": x.get("estado"),
            "titulo": x.get("titulo"), "slides": slides, "legenda": x.get("legenda"), "hashtags": x.get("hashtags"),
            "imagens": (img or {}).get("slides") if isinstance(img, dict) else None,
            "zip": (img or {}).get("zip") if isinstance(img, dict) else None, "observacao": x.get("observacao"),
            "data_prevista": x.get("data_prevista")}


def criar_conteudo(c, ctx, tema, formato="carrossel", publico=None, observacao=None, data_prevista=None):
    d = api(ctx, "POST", "/api/estudio/pauta", {"tema": tema, "formato": formato or "carrossel", "publico": publico or "",
                                                "observacao": observacao or "", "data_prevista": data_prevista or ""})
    pid = d.get("id") or (d.get("item") or {}).get("id")
    resumo = "Coloquei na pauta do Estúdio: %s (%s)%s" % (tema, formato or "carrossel", ", #%s" % pid if pid else "")
    ctx["registrar"]("criar_conteudo", pid, resumo)
    return {"ok": True, "feito": resumo + ". O Estúdio escreve em cerca de 1 a 3 minutos e as imagens saem em seguida.",
            "pauta_id": pid}


def refazer_conteudo(c, ctx, pauta_id, ajuste=None):
    if ajuste:
        c.execute("UPDATE mkt_pauta SET observacao = TRIM(COALESCE(observacao,'') || char(10) || ?) WHERE id=?",
                  ("Ajuste pedido por %s: %s" % (ctx["primeiro"], ajuste), int(pauta_id)))
        c.commit()
    api(ctx, "POST", "/api/estudio/pauta/%s/refazer" % int(pauta_id), {})
    resumo = "Mandei o Estúdio refazer a peça #%s%s" % (pauta_id, " com o ajuste: " + ajuste if ajuste else "")
    ctx["registrar"]("refazer_conteudo", pauta_id, resumo)
    return {"ok": True, "feito": resumo + "."}


def mudar_conteudo(c, ctx, pauta_id, estado=None, data_prevista=None, titulo=None, legenda=None):
    corpo = {k: v for k, v in (("estado", estado), ("data_prevista", data_prevista), ("titulo", titulo),
                                ("legenda", legenda)) if v}
    if not corpo:
        return {"erro": "diga o que mudar"}
    api(ctx, "PATCH", "/api/estudio/pauta/%s" % int(pauta_id), corpo)
    resumo = "Atualizei a peça #%s do Estúdio: %s" % (pauta_id, ", ".join("%s=%s" % (k, str(v)[:40]) for k, v in corpo.items()))
    ctx["registrar"]("mudar_conteudo", pauta_id, resumo)
    return {"ok": True, "feito": resumo + "."}


def metricas_anuncios(c, ctx, periodo="last_7d"):
    visao = api(ctx, "GET", "/api/campanhas/visao", timeout=120)
    try:
        anuncios = api(ctx, "GET", "/api/campanhas/anuncios", params={"periodo": periodo or "last_7d"}, timeout=120)
    except RuntimeError as e:
        anuncios = {"erro": str(e)}
    lista = anuncios.get("anuncios") if isinstance(anuncios, dict) else anuncios
    if isinstance(lista, list):
        lista = [{k: a.get(k) for k in ("nome", "campanha", "status", "gasto", "conversas", "custo_conversa", "ctr",
                                         "cliques", "impressoes", "alcance") if k in a} for a in lista[:12]]
    return {"visao": {k: visao.get(k) for k in ("hoje", "ontem", "mes", "projecao", "orcamento", "crm", "campanhas")
                      if k in visao} or visao, "anuncios": lista}


# ================================================================ GERAL (01/10/2026)
# O Assistente pediu o "conversa_id" do card para anotar (em vez de procurar) e o Assistente disse que nao tinha como
# desvirar documento no Drive. Agora: busca de lead por nome/telefone, a API inteira do sistema como a
# pessoa (o que ela pode na tela, o agente pode) e desvirar documentos.
_ROTA_BLOQUEADA = re.compile(r"^/api/(agentes|auth)(/|$)")
# chega no cliente/lead (WhatsApp, ligacao) ou apaga: so com previa + sim, como as outras acoes externas
_ROTA_EXTERNA = re.compile(r"/(responder|enviar|envio|midia|audio|disparar|disparo|ligar|reenviar|figurinha)(/|$)", re.I)


def _sem(s):
    import unicodedata
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower().strip()


def _cortar(d, n=12000):
    t = json.dumps(d, ensure_ascii=False, default=str)
    return d if len(t) <= n else {"parcial": t[:n], "aviso": "resposta cortada (grande demais); filtre mais"}


def buscar_lead(c, ctx, termo):
    d = api(ctx, "GET", "/api/atendimento/contatos", params={"q": str(termo or "").strip(), "limit": 15})
    pipes = {r[0]: r[1] for r in c.execute("SELECT id, nome FROM wa_pipelines")}
    return {"cards": [{"conversa_id": i.get("id"), "nome": i.get("contato_nome"), "telefone": i.get("contato_telefone"),
                       "board": pipes.get(i.get("pipeline_id"), i.get("pipeline_id")), "coluna": i.get("etapa"),
                       "responsavel": i.get("atendente_email") or None} for i in (d.get("items") or [])]}


def _achar_conversa(c, ctx, conversa_id=None, lead=None):
    """conversa_id direto, ou procura pelo nome/telefone (inclusive o nome que o lead disse ao Sebastiao)."""
    if conversa_id:
        return int(conversa_id), None
    termo = str(lead or "").strip()
    if not termo:
        return None, "diga o nome ou o telefone do lead"
    cards = buscar_lead(c, ctx, termo)["cards"]
    if len(cards) == 1:
        return cards[0]["conversa_id"], None
    exatos = [x for x in cards if _sem(x["nome"]) == _sem(termo)]
    if len(exatos) == 1:
        return exatos[0]["conversa_id"], None
    if not cards:
        return None, "não achei nenhum card com \"%s\" (nome do card, nome dito na conversa ou telefone)" % termo
    return None, "achei %d cards com \"%s\": %s. Pergunte qual é." % (
        len(cards), termo, "; ".join("%s (card %s, %s / %s)" % (x["nome"], x["conversa_id"], x["board"], x["coluna"])
                                     for x in cards[:6]))


def _quando(v):
    """criado_em do banco (UTC, com espaco ou T) -> 'dd/mm HH:MM' no horario de Brasilia."""
    try:
        d = dt.datetime.fromisoformat(str(v).replace("T", " ")[:19]) - dt.timedelta(hours=3)
        return d.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(v)[:16]


def ler_chat(c, ctx, conversa_id=None, lead=None, ultimas=80, termo=None):
    """O Chat ao Vivo / CRM do lead ou cliente (02/10, Advogada: "o Assistente nao tem acesso ao chat"). Le direto do banco,
    SO LEITURA: a rota da tela zera o 'nao lidas' e a equipe perderia o aviso de mensagem nova. Mesma trava da
    tela: board pessoal de outra pessoa nao abre."""
    conversa_id, erro = _achar_conversa(c, ctx, conversa_id, lead)
    if erro:
        return {"erro": erro}
    w = c.execute("SELECT * FROM wa_conversas WHERE id=?", (int(conversa_id),)).fetchone()
    if not w:
        return {"erro": "card não encontrado"}
    dono = c.execute("SELECT nome, dono_email FROM wa_pipelines WHERE id=?", (w["pipeline_id"],)).fetchone()
    if dono and (dono["dono_email"] or "").strip() and dono["dono_email"].lower() != (ctx["eu"].email or "").lower():
        return {"erro": "esse card está num board pessoal de outra pessoa"}
    q, args = "SELECT origem, autor, tipo, conteudo, criado_em FROM wa_mensagens WHERE conversa_id=?", [w["id"]]
    if termo:
        q += " AND conteudo LIKE ?"
        args.append("%" + str(termo) + "%")
    linhas = c.execute(q + " ORDER BY id DESC LIMIT ?", args + [max(10, min(int(ultimas or 80), 300))]).fetchall()
    # com 'termo' (ex.: TRANSCRICAO) a mensagem vem inteira: a transcricao do Silvano tem ~28 mil caracteres
    cap_msg, cap_total = (32000, 34000) if termo else (4000, 15000)
    msgs, tam = [], 0
    for m in linhas:                                   # da mais nova para a mais velha, ate caber
        texto = (m["conteudo"] or "")[:cap_msg]
        if tam + len(texto) > cap_total and msgs:
            break
        tam += len(texto)
        quem = {"contato": "lead/cliente", "atendente": "equipe", "ia": "IA de atendimento (robô)", "nota": "nota interna"}.get(
            m["origem"], m["origem"])
        msgs.append({"de": quem + (" (%s)" % m["autor"] if m["autor"] else ""), "tipo": m["tipo"], "texto": texto,
                     "quando": _quando(m["criado_em"])})
    total = c.execute("SELECT COUNT(*) FROM wa_mensagens WHERE conversa_id=?", (w["id"],)).fetchone()[0]
    try:
        entrevista = {r["campo"]: r["valor"] for r in c.execute(
            "SELECT campo, valor FROM ficha_entrevista WHERE conversa_id=? AND TRIM(COALESCE(valor,''))<>''", (w["id"],))}
    except sqlite3.Error:
        entrevista = {}
    return {"card": {"conversa_id": w["id"], "nome": w["contato_nome"], "telefone": w["contato_telefone"],
                     "board": dono["nome"] if dono else w["pipeline_id"], "coluna": w["etapa"],
                     "responsavel": w["atendente_email"] or w["responsavel"], "robo_sebastiao_ativo": bool(w["ia_ativa"])},
            "total_de_mensagens": total, "mostrando": len(msgs),
            "aviso": None if len(msgs) >= total else "só as mais recentes; use 'termo' para procurar algo mais antigo",
            "mensagens": list(reversed(msgs)), "entrevista": entrevista or None,
            "notas_da_ficha": (w["notas"] or "")[-6000:] or None}


def rotas_sistema(c, ctx, filtro=None):
    import sys as _sys
    m = _sys.modules.get("app.main_vps") or _sys.modules.get("app.main")
    out = []
    for r in getattr(getattr(m, "app", None), "routes", []):
        caminho = getattr(r, "path", "")
        if not caminho.startswith("/api/") or _ROTA_BLOQUEADA.match(caminho):
            continue
        metodos = "/".join(sorted(x for x in (getattr(r, "methods", None) or []) if x != "HEAD"))
        doc = ((getattr(getattr(r, "endpoint", None), "__doc__", "") or "").strip().split("\n") or [""])[0][:140]
        linha = "%s %s%s" % (metodos, caminho, "  # " + doc if doc else "")
        if filtro and not all(p in _sem(linha) for p in _sem(filtro).split()):
            continue
        out.append(linha)
    return {"total": len(out), "rotas": out[:150],
            "dica": "chame sistema_api com o método e a rota (troque {id} pelo número)"}


def sistema_api(c, ctx, metodo, rota, corpo=None, params=None):
    metodo = str(metodo or "GET").upper()
    rota = "/" + str(rota or "").strip().lstrip("/")
    if ctx.get("via_hermes"):
        from . import agentes_hermes as AH
        if not AH.rota_limpa(rota):
            return {"erro": "rota fora do alcance do agente: %s" % rota}
    if metodo not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return {"erro": "método inválido"}
    if not rota.startswith("/api/") or _ROTA_BLOQUEADA.match(rota):
        return {"erro": "rota fora do alcance do agente: %s" % rota}
    if metodo == "GET":
        try:
            return {"resposta": _cortar(api(ctx, "GET", rota, params=params, timeout=120))}
        except RuntimeError as e:
            if "(404)" in str(e) or "(405)" in str(e):
                return {"erro": "%s. Chame rotas_sistema com uma palavra do assunto e use a rota exata." % e}
            raise
    if metodo == "DELETE" or _ROTA_EXTERNA.search(rota) or ctx.get("via_hermes"):
        return pedir_confirmacao(c, ctx, "sistema_api", {"metodo": metodo, "rota": rota, "corpo": corpo, "params": params},
                                 "%s no sistema: %s %s%s" % ("APAGAR" if metodo == "DELETE" else "Ação pedida pelo Assistente" if ctx.get("via_hermes") else "Ação que chega no cliente/lead",
                                                              metodo, rota, ("\nDados: " + json.dumps(corpo, ensure_ascii=False)[:700])
                                                              if corpo else ""))
    return _sistema_api(c, ctx, metodo, rota, corpo, params)


def _sistema_api(c, ctx, metodo, rota, corpo=None, params=None):
    try:
        d = api(ctx, metodo, rota, corpo, params, timeout=120)
    except RuntimeError as e:
        if "(404)" in str(e) or "(405)" in str(e):
            return {"erro": "%s. Essa rota não existe assim: chame rotas_sistema com uma palavra do assunto e use a "
                            "rota exata que aparecer." % e}
        raise
    resumo = "Fiz no sistema: %s %s" % (metodo, rota)
    ctx["registrar"]("sistema_api", rota, resumo, {"metodo": metodo, "corpo": corpo, "params": params})
    return {"ok": True, "feito": resumo + ".", "resposta": _cortar(d, 6000)}


def operador_tecnico(c, ctx, pedido):
    from . import operador_agente as OP
    pedido = str(pedido or "").strip()
    if len(pedido) < 15:
        return {"erro": "escreva o pedido completo (o que fazer, com nomes, links e o resultado esperado)"}
    if not total(ctx):
        return {"erro": "o operador técnico está liberado só para o Assistente, o Assistente e a administração"}
    oid, frente = OP.pedir(ctx["uid"], ctx["agente"], pedido[:6000])
    resumo = "Mandei para o operador técnico (operação #%d)" % oid
    ctx["registrar"]("operador_tecnico", oid, resumo, {"pedido": pedido[:2000]})
    return {"ok": True, "operacao": oid,
            "feito": resumo + ". Ele trabalha no servidor em segundo plano (de 1 a 20 minutos%s) e o resultado chega "
                     "aqui na conversa. A pessoa pode continuar conversando." % (
                         ", %d pedido(s) na frente" % frente if frente else "")}


def minhas_operacoes(c, ctx):
    from . import operador_agente as OP
    return {"operacoes": OP.listar(ctx["uid"])}


def girar_documentos(c, ctx, link=None, cliente=None, arquivo=None, graus=None):
    from . import girar_documentos as GD
    msg, erro = GD.iniciar(ctx["uid"], ctx["agente"], link, cliente, arquivo, graus)
    if erro:
        return {"erro": erro}
    ctx["acoes"].append(msg.split(".")[0])
    return {"ok": True, "feito": msg}


# ================================================================ COMERCIAL (CRM)
FECHADAS = ("Lead Desqualificado", "Perdido", "Perdido - Sem resposta", "Perdido - Sem interesse")


def leads_quentes(c, ctx, limite=15, so_meus=False):
    eu = (ctx["eu"].email or "").lower()
    ordem = "CASE t.temperatura WHEN 'quentissimo' THEN 0 WHEN 'quente' THEN 1 ELSE 2 END"
    linhas = c.execute(
        "SELECT w.id, w.contato_nome, w.etapa, w.atendente_email, w.atualizado_em, w.tag, t.temperatura, t.situacao, "
        "t.resumo FROM wa_conversas w JOIN lead_termometro t ON t.conversa_id = w.id "
        "WHERE w.pipeline_id=? AND t.temperatura IN ('quentissimo','quente') AND COALESCE(t.comercial,'segue')='segue' "
        "AND w.etapa NOT IN (%s) %s ORDER BY %s, w.atualizado_em DESC LIMIT ?"
        % (",".join("?" * len(FECHADAS)), "AND LOWER(COALESCE(w.atendente_email,''))=?" if so_meus else
           "AND (LOWER(COALESCE(w.atendente_email,''))=? OR COALESCE(w.atendente_email,'')='')", ordem),
        [PIPE_LEADS] + list(FECHADAS) + [eu, max(1, min(int(limite or 15), 40))]).fetchall()
    return {"leads": [{"conversa_id": r["id"], "nome": r["contato_nome"], "coluna": r["etapa"],
                       "de": "você" if (r["atendente_email"] or "").lower() == eu else ("ninguém" if not r["atendente_email"] else r["atendente_email"]),
                       "temperatura": r["temperatura"], "situacao": r["situacao"], "resumo": (r["resumo"] or "")[:240],
                       "ultima_movimentacao_utc": str(r["atualizado_em"])[:16]} for r in linhas]}


def ver_lead(c, ctx, conversa_id=None, lead=None):
    conversa_id, erro = _achar_conversa(c, ctx, conversa_id, lead)
    if erro:
        return {"erro": erro}
    w = c.execute("SELECT * FROM wa_conversas WHERE id=?", (int(conversa_id),)).fetchone()
    if not w:
        return {"erro": "lead não encontrado"}
    t = c.execute("SELECT temperatura, situacao, resumo, violacoes FROM lead_termometro WHERE conversa_id=?", (w["id"],)).fetchone()
    msgs = c.execute("SELECT origem, autor, tipo, conteudo, criado_em FROM wa_mensagens WHERE conversa_id=? ORDER BY id DESC LIMIT 20",
                     (w["id"],)).fetchall()
    pipe = c.execute("SELECT nome FROM wa_pipelines WHERE id=?", (w["pipeline_id"],)).fetchone()
    return {"conversa_id": w["id"], "nome": w["contato_nome"], "board": pipe["nome"] if pipe else w["pipeline_id"],
            "coluna": w["etapa"], "responsavel": w["atendente_email"] or w["responsavel"], "tag": w["tag"],
            "robo_sebastiao_ativo": bool(w["ia_ativa"]), "notas": (w["notas"] or "")[-1500:],
            "termometro": dict(t) if t else None,
            "ultimas_mensagens": [{"de": m["origem"] + (" (%s)" % m["autor"] if m["autor"] else ""), "tipo": m["tipo"],
                                   "texto": (m["conteudo"] or "")[:400], "quando_utc": str(m["criado_em"])[:16]}
                                  for m in reversed(msgs)]}


def anotar_lead(c, ctx, texto, conversa_id=None, lead=None):
    conversa_id, erro = _achar_conversa(c, ctx, conversa_id, lead)
    if erro:
        return {"erro": erro}
    api(ctx, "POST", "/api/atendimento/conversas/%s/nota" % int(conversa_id),
        {"texto": "%s (por %s, a pedido de %s)" % (str(texto).strip(), ctx["agente"], ctx["primeiro"])})
    resumo = "Anotei no card do lead %s" % conversa_id
    ctx["registrar"]("anotar_lead", conversa_id, resumo)
    return {"ok": True, "feito": resumo + "."}


def editar_card(c, ctx, conversa_id=None, lead=None, nome=None, tag=None, origem=None, email=None, valor=None,
                responsavel=None):
    conversa_id, erro = _achar_conversa(c, ctx, conversa_id, lead)
    if erro:
        return {"erro": erro}
    corpo = {k: v for k, v in (("contato_nome", nome), ("tag", tag), ("origem", origem), ("email", email),
                                ("valor", valor), ("responsavel", responsavel)) if v not in (None, "")}
    if not corpo:
        return {"erro": "diga o que mudar no card"}
    antes = c.execute("SELECT contato_nome FROM wa_conversas WHERE id=?", (int(conversa_id),)).fetchone()
    api(ctx, "PATCH", "/api/atendimento/conversas/%s/dados" % int(conversa_id), corpo)
    resumo = "Atualizei o card %s (%s): %s" % (conversa_id, antes[0] if antes else "", ", ".join(
        "%s=%s" % (k.replace("contato_", ""), str(v)[:60]) for k, v in corpo.items()))
    ctx["registrar"]("editar_card", conversa_id, resumo, corpo)
    return {"ok": True, "feito": resumo + "."}


def pegar_lead(c, ctx, conversa_id=None, lead=None):
    conversa_id, erro = _achar_conversa(c, ctx, conversa_id, lead)
    if erro:
        return {"erro": erro}
    api(ctx, "POST", "/api/atendimento/conversas/%s/atribuir" % int(conversa_id),
        {"atendente_email": ctx["eu"].email, "assumir_ia": False})
    resumo = "Coloquei o lead %s no nome de %s (a IA de atendimento continua como estava)" % (conversa_id, ctx["primeiro"])
    ctx["registrar"]("pegar_lead", conversa_id, resumo)
    return {"ok": True, "feito": resumo + "."}


def _colunas(c, pipeline_id):
    try:
        return [r[0] for r in c.execute("SELECT nome FROM wa_stages WHERE pipeline_id=? ORDER BY ordem", (pipeline_id,))]
    except sqlite3.Error:
        return []


def mover_lead(c, ctx, coluna, conversa_id=None, board=None, lead=None):
    conversa_id, erro = _achar_conversa(c, ctx, conversa_id, lead)
    if erro:
        return {"erro": erro}
    w = c.execute("SELECT id, contato_nome, etapa, pipeline_id FROM wa_conversas WHERE id=?", (int(conversa_id),)).fetchone()
    if not w:
        return {"erro": "lead não encontrado"}
    pid = PIPE_CONTRATACAO if str(board or "").lower().startswith("contrat") else (int(board) if str(board or "").isdigit() else w["pipeline_id"])
    cols = _colunas(c, pid)
    alvo = next((x for x in cols if x.lower() == str(coluna).lower()), None) or \
        next((x for x in cols if str(coluna).lower() in x.lower()), None)
    if cols and not alvo:
        return {"erro": "coluna não existe nesse board. Colunas: " + "; ".join(cols)}
    alvo = alvo or coluna
    args = {"conversa_id": w["id"], "coluna": alvo, "pipeline_id": pid}
    if pid == PIPE_LEADS and alvo in COLUNAS_SEGURAS and w["pipeline_id"] == PIPE_LEADS and not ctx.get("leu_de_fora"):
        return _mover(c, ctx, **args)
    aviso = next((v for k, v in AVISO_COLUNA.items() if alvo.startswith(k)), "pode acionar automações do CRM")
    return pedir_confirmacao(c, ctx, "mover_lead", args,
                             "Mover %s (%s) de \"%s\" para \"%s\"%s. Atenção: %s." % (
                                 w["contato_nome"], w["id"], w["etapa"], alvo,
                                 " no board Contratação" if pid == PIPE_CONTRATACAO else "", aviso))


def _mover(c, ctx, conversa_id, coluna, pipeline_id):
    antes = c.execute("SELECT etapa FROM wa_conversas WHERE id=?", (int(conversa_id),)).fetchone()
    api(ctx, "POST", "/api/atendimento/conversas/%s/etapa" % int(conversa_id), {"etapa": coluna, "pipeline_id": pipeline_id})
    resumo = "Movi o lead %s de \"%s\" para \"%s\"" % (conversa_id, antes["etapa"] if antes else "?", coluna)
    ctx["registrar"]("mover_lead", conversa_id, resumo, {"pipeline_id": pipeline_id})
    return {"ok": True, "feito": resumo + "."}


def responder_lead(c, ctx, texto, conversa_id=None, lead=None):
    conversa_id, erro = _achar_conversa(c, ctx, conversa_id, lead)
    if erro:
        return {"erro": erro}
    w = c.execute("SELECT id, contato_nome, contato_telefone, ia_ativa FROM wa_conversas WHERE id=?", (int(conversa_id),)).fetchone()
    if not w:
        return {"erro": "lead não encontrado"}
    texto = str(texto or "").strip()
    if not texto:
        return {"erro": "escreva a mensagem"}
    return pedir_confirmacao(c, ctx, "responder_lead", {"conversa_id": w["id"], "texto": texto},
                             "Mandar no WhatsApp para %s:\n\"%s\"%s" % (
                                 ("%s (%s)" % (w["contato_nome"], w["contato_telefone"])
                                  if ctx["uid"] == LUA_UID and w["contato_nome"]
                                  else w["contato_nome"] or w["contato_telefone"]), texto,
                                 "\n(Ao responder, a IA de atendimento sai desta conversa.)" if w["ia_ativa"] else ""))


def _responder(c, ctx, conversa_id, texto):
    api(ctx, "POST", "/api/atendimento/conversas/%s/responder" % int(conversa_id), {"texto": texto})
    resumo = "Mandei a mensagem no WhatsApp do lead %s" % conversa_id
    ctx["registrar"]("responder_lead", conversa_id, resumo, {"texto": texto})
    return dict({"ok": True, "feito": resumo + "."}, **({"texto_enviado": texto} if ctx["uid"] == LUA_UID else {}))


EXTERNAS = {"mover_lead": _mover, "responder_lead": _responder, "advbox_criar_tarefa": _advbox_criar_tarefa,
            "advbox_andamento": _advbox_andamento, "mensagem_cliente": _mensagem_cliente, "sistema_api": _sistema_api,
            # 07/11/2026 (Assistente no Hermes): vindo do Hermes, o operador tecnico vira previa e so roda com o sim
            "operador_tecnico": operador_tecnico,
            # 09/11/2026 (item 42): e-mail da Advogada, so depois da previa e do sim
            "enviar_email": _enviar_email, "encaminhar_email": _encaminhar_email}

# 09/11/2026 (reuniao de 06/11): registros dos vigias, so leitura e em lista fechada.
def ler_log_vigia(c, ctx, nome=None, filtro=None, linhas=120):
    from . import lua_logs as LL
    return LL.ler(nome, filtro, linhas)


# 09/11/2026 (reuniao de 06/11): processos pelo nome no SYNC, para o dossie do GR.
def buscar_por_nome(c, ctx, nome, cpf=None):
    from . import lua_busca_nome as LB
    out = LB.buscar(nome, cpf)
    if not out.get("erro") and ctx.get("registrar"):
        ctx["registrar"]("buscar_por_nome", "sync", "Busquei processos pelo nome: %s" % str(nome)[:80])
    return out


# 09/11/2026 (item 41): documento que a Advogada anexou no chat, so leitura.
def ler_anexo(c, ctx, anexo_id, parte=1):
    from . import lua_anexos as LX
    return LX.ler(anexo_id, parte)


# 09/11/2026 (item 42): caixa de e-mail da Advogada, so leitura. O envio esta em EXTERNAS.
def ler_email(c, ctx, consulta=None, email_id=None, parte=1):
    from . import lua_email as LE
    return LE.ler(consulta, email_id, parte)


# 09/11/2026 (item 40): busca na internet (D21), so para o Assistente no Hermes. Nao abre pagina.
def buscar_internet(c, ctx, consulta):
    from . import lua_internet as LI
    out = LI.buscar(consulta)
    if not out.get("erro") and ctx.get("registrar"):
        ctx["registrar"]("buscar_internet", "internet", "Buscou na internet: %s" % str(consulta)[:120])
    return out


# 08/11/2026 (item 37): arquivos do Drive em lista fechada (D22), so para o Assistente no Hermes.
def ler_drive(c, ctx, link, parte=1):
    from . import lua_drive as LD
    return LD.ler(link, parte)


# 08/11/2026 (item 36): jurisprudencia real e verificada, so para o Assistente no Hermes.
def buscar_jurisprudencia(c, ctx, termo, tribunais=None):
    from . import lua_jurisprudencia as LJ
    return LJ.buscar(termo, tribunais)


# 08/11/2026 (item 34): o Assistente le os autos pelo SYNC. So o Assistente no Hermes: os outros agentes nao mudam nesta camada.
def ler_autos(c, ctx, numero, filtro=None, documento_id=None, intimacao_id=None, parte=1):
    from . import lua_autos as LA
    cnj = LA.cnj_no_texto(numero)
    if not cnj:
        pr = _processo(c, numero)
        if not pr:
            return {"erro": "não achei esse processo no cadastro. Diga o número CNJ."}
        if pr.get("ambiguo"):
            return {"erro": "achei mais de um processo: %s. Diga o número." % ", ".join(filter(None, pr["ambiguo"]))}
        cnj = LA.numero_cnj(pr["numero"])
        if not cnj:
            return {"erro": "esse caso não tem número CNJ (o GR usa o número da notificação), então não existe no SYNC."}
    return LA.ler(cnj, filtro, documento_id, intimacao_id, parte)


# Regra da Advogada (D25, 08/11/2026): sobre um processo, o Assistente nao confere e nao faz peca sem consultar os autos pelo SYNC.
EXIGE_AUTOS = {"escrever_peca", "revisar_peca", "conferir_tarefa"}


def _processos_do_pedido(c, entrada):
    """(numero, origem) de tudo o que o pedido aponta: ler um processo nao libera outro."""
    from . import lua_autos as LA
    achados = [(str(entrada["processo"]), "processo")] if entrada.get("processo") else []
    try:
        tarefa_id = int(entrada.get("tarefa_id") or 0)
        t = c.execute("SELECT processo_numero FROM tarefas WHERE id=?", (tarefa_id,)).fetchone() if tarefa_id else None
    except (TypeError, ValueError, OverflowError):
        t = None
    if t and t[0]:
        achados.append((t[0], "tarefa"))
    return achados


def falta_ler_autos(c, nome, entrada):
    """O motivo para recusar, ou None. Inicial nao distribuida e GR nao tem autos para consultar."""
    if nome not in EXIGE_AUTOS:
        return None
    from . import lua_autos as LA
    por_origem = {}
    achados = _processos_do_pedido(c, entrada)
    citado = LA.cnj_no_texto(entrada.get("instrucoes"))
    if citado and not achados:
        return ("As instruções citam o processo %s, mas o pedido não diz de qual processo é a peça. Se for nele, chame de novo "
                "com processo=%s. Se for inicial ou outro caso e o número é só referência, tire o número das instruções. "
                "NÃO fiz." % (citado, citado))
    for numero, origem in achados:
        cnj = LA.cnj_no_texto(numero)
        if not cnj:
            pr = _processo(c, numero)
            if pr and pr.get("ambiguo"):
                return "achei mais de um processo com esse nome. Descubra qual é e consulte os autos com ler_autos. NÃO fiz."
            cadastrado = c.execute("SELECT 1 FROM jur_processos WHERE numero=?", (numero,)).fetchone()    # GR: numero da notificacao
            if not pr and not cadastrado and origem == "processo":
                return "não identifiquei esse processo. Diga o número CNJ e consulte os autos com ler_autos. NÃO fiz."
            cnj = LA.numero_cnj(pr["numero"]) if pr else None
        por_origem[origem] = cnj
        if cnj and not LA.autos_lidos(cnj):
            return ("Regra obrigatória: consulte os autos do processo %s com ler_autos, sem filtro, antes disso. NÃO fiz. Se o "
                    "SYNC não trouxer os autos, avise a Advogada e pare." % cnj)
    if por_origem.get("processo") and por_origem.get("tarefa") and por_origem["processo"] != por_origem["tarefa"]:
        return "o processo pedido é diferente do processo da tarefa. Confirme com a Advogada qual é o certo. NÃO fiz."
    return None


# ================================================================ esquemas para o Claude
_I = {"type": "integer"}
_S = {"type": "string"}
FERRAMENTAS = {
    "comum": [
        {"name": "confirmar_acao", "description": "Executa uma ação que pediu confirmação (mensagem para lead, mover card "
                                                  "que dispara robô). Só depois que a pessoa disse sim à prévia.",
         "input_schema": {"type": "object", "properties": {"codigo": _I}, "required": ["codigo"]}},
        {"name": "cancelar_acao", "description": "Cancela uma ação que estava esperando confirmação.",
         "input_schema": {"type": "object", "properties": {"codigo": _I}, "required": ["codigo"]}},
        {"name": "buscar_lead",
         "description": "Acha o card (conversa do WhatsApp) de um lead ou cliente pelo nome, pelo nome que ele disse na "
                        "conversa ou pelo telefone. Use SEMPRE que a pessoa citar alguém sem dar o número do card.",
         "input_schema": {"type": "object", "properties": {"termo": _S}, "required": ["termo"]}},
        {"name": "ler_chat",
         "description": "Lê o CHAT AO VIVO (a conversa do WhatsApp no CRM) de qualquer lead ou cliente: mensagens dele, "
                        "da equipe e da IA de atendimento, notas internas, TRANSCRIÇÕES das ligações e a aba Entrevista. Pelo "
                        "conversa_id ou pelo nome/telefone em 'lead'. 'termo' procura uma palavra na conversa inteira "
                        "e traz a mensagem completa: para a TRANSCRIÇÃO inteira de uma ligação use termo='TRANSCRICAO'. "
                        "Só leitura: não marca nada como lido.",
         "input_schema": {"type": "object", "properties": {"conversa_id": _I, "lead": _S, "termo": _S,
                                                           "ultimas": {"type": "integer", "description": "padrão 80"}}}},
        {"name": "girar_documentos",
         "description": "Desvira documentos no Google Drive (foto ou PDF de ponta-cabeça ou deitado): confere cada "
                        "imagem e página, gira só o que estiver virado e troca o arquivo no mesmo lugar (o Drive guarda "
                        "a versão anterior). Passe o link da pasta ou do arquivo, OU o nome completo do cliente (pasta "
                        "DOCUMENTOS DO CLIENTE). 'arquivo' filtra pelo nome (ex.: 'holerite'). 'graus' (90, 180, 270, "
                        "sentido horário) gira direto sem detectar. Roda em segundo plano e avisa aqui.",
         "input_schema": {"type": "object", "properties": {
             "link": _S, "cliente": _S, "arquivo": _S, "graus": {"type": "integer", "enum": [90, 180, 270]}}}},
        {"name": "rotas_sistema",
         "description": "Lista as funções do Sistema Escritório Modelo (rotas da API) que podem ser usadas com sistema_api. Filtre por "
                        "palavra (ex.: 'nota', 'processos', 'financeiro', 'clientes', 'tarefas', 'crm', 'portal').",
         "input_schema": {"type": "object", "properties": {"filtro": _S}}},
        {"name": "sistema_api",
         "description": "Faz no Sistema Escritório Modelo, COMO A PESSOA, qualquer coisa que ela pode fazer na tela, quando não houver "
                        "ferramenta específica: consultar (GET) e cadastrar/alterar (POST, PUT, PATCH). O sistema confere "
                        "a permissão dela. Apagar e o que chega no cliente (mensagem, ligação) volta com prévia para o sim.",
         "input_schema": {"type": "object", "properties": {
             "metodo": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
             "rota": {"type": "string", "description": "ex.: /api/processos?busca=joao"},
             "corpo": {"type": "object", "description": "JSON enviado (POST/PUT/PATCH)"},
             "params": {"type": "object", "description": "parâmetros da URL"}}, "required": ["metodo", "rota"]}},
    ],
    "juridico": [
        {"name": "revisar_peca",
         "description": "Aciona o fluxo de revisão da CONTROLADORIA (Revisora IA) sobre uma peça: pela tarefa de revisão "
                        "(pega o link 'PECA A REVISAR' da tarefa) ou por um link do Google Docs/Drive. Roda em segundo "
                        "plano (3 a 6 min); o parecer (APROVADA / COM RESSALVAS / NÃO APROVADA) volta na conversa e fica "
                        "salvo no Drive ao lado da peça.",
         "input_schema": {"type": "object", "properties": {
             "tarefa_id": _I, "link": _S, "foco": {"type": "string", "description": "o que a pessoa quer que olhe com atenção"}}}},
        {"name": "minhas_revisoes", "description": "As revisões pedidas e o resultado de cada uma.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "ver_parecer", "description": "O texto completo do parecer de uma revisão.",
         "input_schema": {"type": "object", "properties": {"revisao_id": _I}, "required": ["revisao_id"]}},
        {"name": "marcar_checklist", "description": "Marca (ou desmarca) uma etapa do checklist de uma tarefa (ex.: revisão: "
                                                    "recebido, analise, duvidas, entrevista, finalizou, protocolo).",
         "input_schema": {"type": "object", "properties": {"tarefa_id": _I, "etapa": _S, "feito": {"type": "boolean"}},
                          "required": ["tarefa_id", "etapa"]}},
        {"name": "intimacoes_pendentes", "description": "Intimações pendentes da Controladoria (as da pessoa, pela trilha).",
         "input_schema": {"type": "object", "properties": {"so_minhas": {"type": "boolean"}, "limite": _I}}},
        {"name": "dar_ciencia", "description": "Dá ciência (marca como tratada) em intimações, pelo id. Só com pedido claro.",
         "input_schema": {"type": "object", "properties": {"ids": {"type": "array", "items": _I}, "motivo": _S},
                          "required": ["ids"]}},
        {"name": "escrever_peca",
         "description": "Manda ESCREVER uma peça (contestação, réplica, recurso ordinário, embargos, manifestação, "
                        "petição simples, contrarrazões...). Roda na produção em segundo plano (20 a 40 min), no Opus, no "
                        "timbrado do Escritório Modelo, lendo os documentos do caso; o link volta aqui e, se pedido, a peça passa pela "
                        "Revisora IA. Passe o que a pessoa quer (teses, pedidos, prazo) em instrucoes.",
         "input_schema": {"type": "object", "properties": {
             "tipo_peca": _S, "instrucoes": _S, "tarefa_id": _I, "processo": _S,
             "pasta_drive": {"type": "string", "description": "link da pasta do cliente, se a pessoa der"},
             "revisar_depois": {"type": "boolean", "description": "padrão true: manda para a Revisora IA quando ficar pronta"}},
             "required": ["tipo_peca", "instrucoes"]}},
        {"name": "minhas_pecas", "description": "As peças pedidas para a produção e a situação de cada uma.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "advbox_criar_tarefa",
         "description": "Cria tarefa NO ADVBOX num processo (não dá para apagar depois). Sempre volta com prévia para "
                        "confirmar. Responsáveis pelo primeiro nome.",
         "input_schema": {"type": "object", "properties": {
             "processo": {"type": "string", "description": "número CNJ ou nome do cliente"}, "tipo": _S,
             "responsaveis": {"type": "array", "items": _S}, "comentario": _S,
             "data": {"type": "string", "description": "AAAA-MM-DD"}, "prazo_fatal": {"type": "string", "description": "AAAA-MM-DD"},
             "urgente": {"type": "boolean"}}, "required": ["processo", "tipo", "responsaveis", "comentario"]}},
        {"name": "advbox_andamento", "description": "Lança um andamento (movimentação manual) no processo NO ADVBOX. "
                                                    "Sempre volta com prévia para confirmar.",
         "input_schema": {"type": "object", "properties": {"processo": _S, "texto": _S,
                                                           "data": {"type": "string", "description": "AAAA-MM-DD"}},
                          "required": ["processo", "texto"]}},
        {"name": "mensagem_cliente",
         "description": "Manda mensagem no WhatsApp do escritório para um CLIENTE (acha o telefone pelo processo ou pelo "
                        "nome no cadastro). Sempre volta com prévia para confirmar. Linguagem simples, sem prometer resultado.",
         "input_schema": {"type": "object", "properties": {"texto": _S, "cliente": _S, "processo": _S, "telefone": _S},
                          "required": ["texto"]}},
    ],
    "marketing": [
        {"name": "pauta_conteudo", "description": "A pauta do Estúdio de Conteúdo (Instagram do escritório): peças na fila, "
                                                  "gerando, prontas, agendadas e publicadas.",
         "input_schema": {"type": "object", "properties": {"estado": _S, "limite": _I}}},
        {"name": "ver_conteudo", "description": "Uma peça do Estúdio completa: slides, legenda, hashtags e links das imagens.",
         "input_schema": {"type": "object", "properties": {"pauta_id": _I}, "required": ["pauta_id"]}},
        {"name": "criar_conteudo", "description": "Coloca um tema na pauta do Estúdio; ele escreve (voz JusMotô, regras da OAB) "
                                                  "e gera as imagens sozinho.",
         "input_schema": {"type": "object", "properties": {
             "tema": _S, "formato": {"type": "string", "enum": ["carrossel", "reels", "story", "legenda"]},
             "publico": _S, "observacao": _S, "data_prevista": {"type": "string", "description": "AAAA-MM-DD"}},
             "required": ["tema"]}},
        {"name": "refazer_conteudo", "description": "Manda o Estúdio reescrever uma peça, com o ajuste pedido.",
         "input_schema": {"type": "object", "properties": {"pauta_id": _I, "ajuste": _S}, "required": ["pauta_id"]}},
        {"name": "mudar_conteudo", "description": "Muda estado (fila, pronto, agendado, publicado, arquivado), data, título "
                                                  "ou legenda de uma peça. Marcar publicado NÃO publica: só registra.",
         "input_schema": {"type": "object", "properties": {"pauta_id": _I, "estado": _S, "data_prevista": _S,
                                                           "titulo": _S, "legenda": _S}, "required": ["pauta_id"]}},
        {"name": "metricas_anuncios", "description": "Números dos anúncios da Meta (gasto, conversas, custo por conversa, "
                                                     "projeção do mês e ranking dos anúncios).",
         "input_schema": {"type": "object", "properties": {"periodo": {"type": "string", "enum": [
             "today", "yesterday", "last_7d", "last_14d", "last_30d"]}}}},
    ],
    "comercial": [
        {"name": "leads_quentes", "description": "Leads quentes e quentíssimos do Reclamantes (termômetro), da pessoa e sem dono.",
         "input_schema": {"type": "object", "properties": {"limite": _I, "so_meus": {"type": "boolean"}}}},
        {"name": "ver_lead", "description": "Card do lead: coluna, dono, termômetro, notas e as últimas mensagens. "
                                            "Pelo conversa_id ou pelo nome/telefone em 'lead'.",
         "input_schema": {"type": "object", "properties": {"conversa_id": _I, "lead": _S}}},
        {"name": "anotar_lead", "description": "Escreve uma nota interna no card (resumo de ligação, combinado, qualquer "
                                               "anotação). Pelo conversa_id ou pelo nome/telefone em 'lead'.",
         "input_schema": {"type": "object", "properties": {"conversa_id": _I, "lead": _S, "texto": _S},
                          "required": ["texto"]}},
        {"name": "editar_card", "description": "Muda dados do card/ficha do lead ou cliente: nome (corrigir o nome do "
                                               "card), tag, origem, e-mail, valor, responsável. Para nota, use anotar_lead.",
         "input_schema": {"type": "object", "properties": {"conversa_id": _I, "lead": _S, "nome": _S, "tag": _S,
                                                           "origem": _S, "email": _S, "valor": {"type": "number"},
                                                           "responsavel": _S}}},
        {"name": "pegar_lead", "description": "Coloca o lead no nome da pessoa.",
         "input_schema": {"type": "object", "properties": {"conversa_id": _I, "lead": _S}}},
        {"name": "mover_lead", "description": "Move o card para outra coluna (ou para o board Contratação com "
                                              "board='contratacao'). Colunas que disparam robô pedem confirmação.",
         "input_schema": {"type": "object", "properties": {"conversa_id": _I, "lead": _S, "coluna": _S, "board": _S},
                          "required": ["coluna"]}},
        {"name": "responder_lead", "description": "Manda mensagem no WhatsApp do lead. SEMPRE pede confirmação com a prévia.",
         "input_schema": {"type": "object", "properties": {"conversa_id": _I, "lead": _S, "texto": _S},
                          "required": ["texto"]}},
    ],
}
ACOES = {
    "confirmar_acao": confirmar_acao, "cancelar_acao": cancelar_acao,
    "revisar_peca": revisar_peca, "minhas_revisoes": minhas_revisoes, "ver_parecer": ver_parecer,
    "marcar_checklist": marcar_checklist, "intimacoes_pendentes": intimacoes_pendentes, "dar_ciencia": dar_ciencia,
    "escrever_peca": escrever_peca, "minhas_pecas": minhas_pecas, "advbox_criar_tarefa": advbox_criar_tarefa,
    "advbox_andamento": advbox_andamento, "mensagem_cliente": mensagem_cliente,
    "pauta_conteudo": pauta_conteudo, "ver_conteudo": ver_conteudo, "criar_conteudo": criar_conteudo,
    "refazer_conteudo": refazer_conteudo, "mudar_conteudo": mudar_conteudo, "metricas_anuncios": metricas_anuncios,
    "leads_quentes": leads_quentes, "ver_lead": ver_lead, "anotar_lead": anotar_lead, "pegar_lead": pegar_lead,
    "mover_lead": mover_lead, "responder_lead": responder_lead,
    "buscar_lead": buscar_lead, "girar_documentos": girar_documentos, "rotas_sistema": rotas_sistema,
    "editar_card": editar_card, "ler_chat": ler_chat,
    "operador_tecnico": operador_tecnico, "minhas_operacoes": minhas_operacoes,
    "sistema_api": sistema_api, "ler_autos": ler_autos, "buscar_jurisprudencia": buscar_jurisprudencia,
    "ler_drive": ler_drive, "buscar_internet": buscar_internet, "ler_email": ler_email, "ler_anexo": ler_anexo, "ler_log_vigia": ler_log_vigia, "buscar_por_nome": buscar_por_nome,
}


FERRAMENTAS_TOTAL = [
    {"name": "operador_tecnico",
     "description": "Faz no SERVIDOR qualquer trabalho que as outras ferramentas não cobrem, como o Claude Code do "
                    "Titular: separar, renomear, mover e organizar arquivos e pastas no Drive; ler documentos do Drive "
                    "e tirar informação; consultar, cruzar e corrigir dados do banco em lote; relatórios, listas e "
                    "planilhas; conferências (ex.: quais clientes estão sem procuração assinada); consultas ao ADVBOX. "
                    "Roda em segundo plano (1 a 20 min) e o resultado chega aqui. Escreva o pedido COMPLETO e "
                    "autossuficiente: nomes completos, links, números de card/processo, e o que a pessoa quer no final.",
     "input_schema": {"type": "object", "properties": {"pedido": _S}, "required": ["pedido"]}},
    {"name": "minhas_operacoes", "description": "As operações mandadas para o operador técnico e o resultado de cada uma.",
     "input_schema": {"type": "object", "properties": {}}},
]


FERRAMENTAS_LUA = [
    {"name": "ler_autos",
     "description": "OBRIGATÓRIA antes de responder, conferir ou fazer peça sobre um processo. Lê os AUTOS pelo SYNC: capa e "
                    "linha do tempo (intimações, documentos, movimentos e audiências), do mais recente ao mais antigo. "
                    "'numero' aceita o CNJ ou o nome da parte. 'filtro' procura uma palavra nos itens. Com 'documento_id' ou "
                    "'intimacao_id' (da linha do tempo) traz o teor. Saída longa vem em partes: chame de novo com 'parte'. "
                    "Só leitura.",
     "input_schema": {"type": "object", "properties": {"numero": _S, "filtro": _S, "documento_id": _I, "intimacao_id": _I,
                                                       "parte": _I}, "required": ["numero"]}},
    {"name": "buscar_jurisprudencia",
     "description": "Busca jurisprudência REAL e verificada. Use SEMPRE que for citar julgado: só cite o que esta ferramenta "
                    "devolver, com tribunal, número, data e link. 'tribunais' é a lista, como tst, trt2, trt6, stj (padrão: tst). "
                    "A base não cobre os TRT 11, 12, 15 e 21. Cada tribunal gasta uma consulta de um limite diário pequeno (máximo de 3 por vez). Se a consulta falhar, diga isso: não é o mesmo que não haver julgado.",
     "input_schema": {"type": "object", "properties": {"termo": _S, "tribunais": {"type": "array", "items": _S}},
                      "required": ["termo"]}},
    {"name": "ler_drive",
     "description": "Lê um arquivo ou lista uma pasta do Google Drive, pelo link ou pelo id. Lê PDF (também digitalizado), Word, "
                    "texto, imagem e arquivos do Google. Pasta devolve a lista: chame de novo com o id do item. Saída longa vem em "
                    "partes: use 'parte'. Só alcança as pastas permitidas; o resto é recusado. Só leitura.",
     "input_schema": {"type": "object", "properties": {"link": _S, "parte": _I}, "required": ["link"]}},
    {"name": "buscar_internet",
     "description": "Busca na internet e devolve título, link e trecho de até 5 resultados. Não abre página. Busque só o tema: "
                    "número de processo, CPF, CNPJ, telefone e e-mail são recusados. Também é recusada depois de ler processo em "
                    "segredo de justiça ou dado que exigiu permissão de administração. Para julgado, use buscar_jurisprudencia.",
     "input_schema": {"type": "object", "properties": {"consulta": _S}, "required": ["consulta"]}},
    {"name": "ler_email",
     "description": "Lê a caixa de e-mail da Dra. Advogada. Sem nada, lista a última semana. 'consulta' usa a busca do Gmail, como "
                    "from:fulano ou subject:notificacao. Com 'email_id' (da lista) abre o e-mail. Texto longo vem em partes. Só leitura.",
     "input_schema": {"type": "object", "properties": {"consulta": _S, "email_id": _S, "parte": _I}}},
    {"name": "enviar_email",
     "description": "Envia e-mail pela conta da Dra. Advogada. Devolve uma prévia: mostre destinatário, assunto e texto integral e só "
                    "chame confirmar_acao depois do sim dela. 'para' aceita até 5 endereços separados por vírgula. Em 'anexos', "
                    "a lista com o id do anexo do chat ou o link do Drive (só das pastas permitidas).",
     "input_schema": {"type": "object", "properties": {"para": _S, "assunto": _S, "texto": _S, "anexos": {"type": "array", "items": _S}}, "required": ["para", "assunto", "texto"]}},
    {"name": "encaminhar_email",
     "description": "Encaminha um e-mail recebido pela Dra. Advogada, com os anexos dele. 'email_id' vem de ler_email. Devolve uma prévia: só chame "
                    "confirmar_acao depois do sim dela.",
     "input_schema": {"type": "object", "properties": {"email_id": _S, "para": _S, "comentario": _S}, "required": ["email_id", "para"]}},
    {"name": "ler_anexo",
     "description": "Lê um documento que a Dra. Advogada anexou no chat. Quando a mensagem dela trouxer [Anexo <id>: nome], chame com esse "
                    "id. Lê PDF (também digitalizado), Word, imagem e texto. Saída longa vem em partes: use 'parte'. Só leitura.",
     "input_schema": {"type": "object", "properties": {"anexo_id": _S, "parte": _I}, "required": ["anexo_id"]}},
    {"name": "ler_log_vigia",
     "description": "Lê o registro de um vigia, para investigar uma falha antes de corrigir na mão. Sem 'nome', lista os registros permitidos. "
                    "'filtro' procura uma palavra. 'linhas' vai até 300. Só leitura: a correção passa pela Advogada ou pelo Ton.",
     "input_schema": {"type": "object", "properties": {"nome": _S, "filtro": _S, "linhas": _I}}},
    {"name": "buscar_por_nome",
     "description": "Busca processos no SYNC pelo nome completo da pessoa, em todos os tribunais, para o dossiê do GR. Informe o 'cpf' sempre que tiver, "
                    "para separar os homônimos. A busca é incompleta: nunca diga nada consta a partir dela. Só leitura.",
     "input_schema": {"type": "object", "properties": {"nome": _S, "cpf": _S}, "required": ["nome"]}},
]


def ferramentas(ctx):
    out = list(FERRAMENTAS["comum"])
    for a in ("juridico", "marketing", "comercial"):
        if a in areas(ctx):
            out += FERRAMENTAS[a]
    if total(ctx):
        out += FERRAMENTAS_TOTAL
    if ctx.get("uid") == LUA_UID and ctx.get("via_hermes"):
        out += FERRAMENTAS_LUA
    return out


def permitida(ctx, nome):
    return any(f["name"] == nome for f in ferramentas(ctx))


PROMPT_AREAS = {
    "juridico": "Jurídico: você aciona a REVISÃO DA CONTROLADORIA (Revisora IA) com revisar_peca quando a pessoa pedir para "
                "revisar/conferir uma peça (pela tarefa de revisão ou pelo link); acompanha com minhas_revisoes; marca o "
                "checklist; vê intimações pendentes e dá ciência quando pedirem; manda ESCREVER peças (escrever_peca: "
                "contestação, réplica, recurso, manifestação...) e acompanha com minhas_pecas; cria tarefa e lança "
                "andamento NO ADVBOX; e manda mensagem para CLIENTE no WhatsApp. ADVBOX e mensagem para cliente passam "
                "SEMPRE pela prévia e pelo sim da pessoa.",
    "marketing": "Marketing: você cuida do Estúdio de Conteúdo (Instagram do escritório): lista a pauta, mostra uma peça, cria "
                 "tema novo, manda refazer com ajuste, muda estado e data; e analisa os anúncios da Meta. PUBLICAR no "
                 "Instagram ainda não é automático para o Instagram do escritório (a conta não está conectada ao sistema): "
                 "entregue o link das imagens e da legenda para a pessoa postar e, depois, marque como publicado.",
    "comercial": "Comercial: você mostra leads quentes, abre o card do lead, anota, coloca o lead no nome da pessoa, move "
                 "card e responde o lead no WhatsApp. Responder lead e mover para coluna que dispara robô (contratação, "
                 "boas-vindas, leads quentes) SEMPRE passam pela prévia e confirmação.",
}


def prompt(ctx):
    a = areas(ctx)
    if not a:
        return ""
    txt = "\nFluxos do escritório que você aciona para essa pessoa:\n" + "\n".join("- " + PROMPT_AREAS[x] for x in
                                                                                   ("juridico", "marketing", "comercial") if x in a)
    txt += ("\n- Geral: buscar_lead acha qualquer card pelo nome (inclusive o nome que o lead disse à IA de atendimento) ou "
            "telefone; ler_chat abre o CHAT AO VIVO de qualquer lead ou cliente (conversa, transcrição das ligações, "
            "entrevista): o Chat ao Vivo e o CRM SÃO o sistema que você acessa, nunca diga que é outro sistema; "
            "girar_documentos desvira fotos e PDFs no Drive; e para qualquer outra coisa que a pessoa faz na "
            "tela do sistema (notas, CRM, clientes, processos, tarefas, agenda, portal, financeiro que ela enxerga...) "
            "chame PRIMEIRO rotas_sistema com uma palavra do assunto e depois sistema_api com a rota EXATA que veio "
            "(nunca invente rota). Se o sistema recusar (403), aí sim diga que o usuário dela não tem essa permissão.")
    if total(ctx):
        txt += ("\n- PERMISSÃO TOTAL (Titular, 02/10/2026): para essa pessoa você pode TUDO no sistema. O que o usuário "
                "dela não pode, o sistema refaz como administração sozinho. E para o que nenhuma ferramenta faz (Drive, "
                "banco em lote, relatórios, planilhas, conferências, ADVBOX) use operador_tecnico, que trabalha no "
                "servidor como o Claude Code do Titular. NUNCA responda \"não tenho acesso\" ou \"não consigo\" sem "
                "antes tentar sistema_api ou operador_tecnico. Mensagem para cliente ou lead continua com prévia e sim.")
    txt += ("\n- Ação externa (lead, cliente, ADVBOX, card que dispara robô): chame a ferramenta DIRETO; ela devolve a "
            "prévia oficial com \"precisa_confirmar\". NÃO faça prévia por conta própria antes. Mostre a prévia do jeito "
            "que veio, pergunte, e só chame confirmar_acao(codigo) se a PRÓXIMA mensagem da pessoa for um sim para ela. "
            "Se a pessoa fizer outro pedido em vez de responder, a prévia fica esperando: não confirme. Se ela mudar o "
            "texto, gere uma prévia nova.\n- Pedido já feito nesta conversa (peça na produção, tarefa criada): não faça de "
            "novo; diga que já está feito.\n")
    return txt
