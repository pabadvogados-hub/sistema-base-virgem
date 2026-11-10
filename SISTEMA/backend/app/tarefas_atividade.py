# -*- coding: utf-8 -*-
"""Atividade da tarefa: responder a quem mandou (Advogada, 06/10/2026).

No ADVBOX, embaixo de toda tarefa, fica a Atividade: a pessoa marca o responsavel,
escolhe o tipo (COMENTARIO ou a proxima etapa) e escreve. Faltava isso no perfil da
tarefa do Sistema Escritório Modelo: a Advogada recebeu "Distribuido: EZEQUIEL TORRES..." e nao tinha
onde responder a quem mandou.

Como fica aqui:
  * a resposta entra no FIO da tarefa original (tarefa_comentarios), com quem escreveu,
    para quem e quando;
  * e vira uma TAREFA NOVA para quem foi marcado (a sequencia logica), do tipo escolhido,
    com o texto na descricao e o link de volta para a tarefa original. Quem recebe ve a
    resposta na propria fila e responde pelo mesmo caminho, ja com o remetente marcado.
  * O ADVBOX nao tem API para comentar um post (so cria post com tipo do catalogo, e
    COMENTARIO nao esta nele). A resposta nasce e fica no Sistema Escritório Modelo; a tarefa nova
    sai com origem='resposta', que o espelho do ADVBOX ignora.

Bateria: python -m app.tarefas_atividade
"""
import json
import time

from . import motor_tarefas as _MT
from . import tarefas_tipos as TT

LINK_TAREFA = "https://sistema.escritorio.adv.br/tarefas?tarefa=%d"
TIPO_PADRAO = TT.COMENTARIO          # fase 2, T1: sem acento; quem le aceita os dois (TT.eh_comentario)

DDL = """CREATE TABLE IF NOT EXISTS tarefa_comentarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tarefa_id INTEGER NOT NULL,
    texto TEXT NOT NULL,
    por_id INTEGER,
    por_nome TEXT,
    para TEXT,
    tipo TEXT,
    tarefa_gerada INTEGER,
    criado_em REAL)"""


def garantir(c):
    c.execute(DDL)
    c.execute("CREATE INDEX IF NOT EXISTS ix_coment_tarefa ON tarefa_comentarios(tarefa_id, criado_em)")
    _MT.garantir_historico(c)


def _hist(c, tarefa_id, acao, detalhe, por):
    # fase 2, T5 (03/11/2026): mesma funcao e mesma tabela do motor
    _MT.historico(c, "tarefa", tarefa_id, acao, detalhe, por)


def _linha(r):
    d = dict(r)
    try:
        d["para"] = json.loads(d.get("para") or "[]")
    except ValueError:
        d["para"] = []
    return d


def _origem_resposta(ref):
    """'12:7' -> (12, 7): a tarefa de origem e o comentario que gerou esta."""
    try:
        a, b = str(ref or "").split(":", 1)
        return int(a), int(b)
    except (ValueError, TypeError):
        return None, None


def listar(c, tarefa_id):
    """O fio da tarefa e, se ela nasceu de uma resposta, de onde veio."""
    garantir(c)
    t = c.execute("SELECT id, titulo, origem, origem_ref FROM tarefas WHERE id=?", (tarefa_id,)).fetchone()
    if not t:
        raise LookupError("tarefa %s nao existe" % tarefa_id)
    fio = [_linha(r) for r in c.execute(
        "SELECT * FROM tarefa_comentarios WHERE tarefa_id=? ORDER BY criado_em, id", (tarefa_id,))]
    veio_de = None
    if t["origem"] == "resposta":
        tid, cid = _origem_resposta(t["origem_ref"])
        if cid:
            r = c.execute("SELECT * FROM tarefa_comentarios WHERE id=?", (cid,)).fetchone()
            o = c.execute("SELECT id, titulo FROM tarefas WHERE id=?", (tid,)).fetchone() if tid else None
            if r:
                veio_de = {"tarefa_id": tid, "titulo": (o["titulo"] if o else None),
                           "link": (LINK_TAREFA % tid) if tid else None, "comentario": _linha(r)}
    return {"comentarios": fio, "veio_de": veio_de}


def _pessoas(c, ids):
    """[{usuario_id, nome}] de quem existe e esta ativo. Id negativo e parceiro (sem login)."""
    out, vistos = [], set()
    for v in ids or []:
        try:
            uid = int(v)
        except (TypeError, ValueError):
            continue
        if uid in vistos or uid == 0:
            continue
        vistos.add(uid)
        nome = None
        try:
            if uid > 0:
                r = c.execute("SELECT nome FROM usuarios WHERE id=? AND COALESCE(ativo,1)=1", (uid,)).fetchone()
            else:
                r = c.execute("SELECT nome FROM tarefa_parceiros WHERE id=? AND COALESCE(ativo,1)=1", (-uid,)).fetchone()
            nome = r["nome"] if r else None
        except Exception:
            nome = None
        if nome:
            out.append({"usuario_id": uid, "nome": nome})
    return out


def responder(c, tarefa_id, texto, para, tipo=None, por_id=None, por_nome=None,
              prazo_fatal=None, prazo_interno=None, data=None, pfi_de=None):
    """Grava a resposta no fio e abre a tarefa nova para quem foi marcado. Nao faz commit."""
    garantir(c)
    t = c.execute("SELECT * FROM tarefas WHERE id=?", (tarefa_id,)).fetchone()
    if not t:
        raise LookupError("tarefa %s nao existe" % tarefa_id)
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("escreva a resposta")
    pessoas = _pessoas(c, para)
    if not pessoas:
        raise ValueError("marque para quem vai a resposta")
    tipo = (TT.norm_tipo(tipo) or TIPO_PADRAO)
    por_nome = por_nome or "?"
    agora = time.time()
    cur = c.execute("INSERT INTO tarefa_comentarios (tarefa_id, texto, por_id, por_nome, para, tipo, criado_em) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (tarefa_id, texto, por_id, por_nome, json.dumps(pessoas, ensure_ascii=False), tipo, agora))
    cid = cur.lastrowid

    pf = str(prazo_fatal or "")[:10] or None
    pi = str(prazo_interno or "")[:10] or None
    if pf and not pi and pfi_de:
        try:
            pi = pfi_de(pf)
        except Exception:
            pi = None
    chaves = t.keys()
    titulo = (t["titulo"] or t["tipo"] or "Tarefa").strip()
    if not titulo.startswith("Re: "):
        titulo = "Re: " + titulo
    titulo = titulo[:140]
    descricao = "%s\n\nResposta de %s à tarefa #%d (%s): %s" % (
        texto, por_nome, tarefa_id, (t["titulo"] or "")[:80], LINK_TAREFA % tarefa_id)
    cols = ["tipo", "titulo", "descricao", "processo_numero", "lawsuits_id", "cliente", "parte_adversa",
            "pasta_drive", "data", "prazo_fatal", "prazo_interno", "prioridade", "status", "origem",
            "origem_ref", "criado_por", "criado_em", "atualizado_em"]
    vals = [tipo, titulo, descricao, t["processo_numero"], t["lawsuits_id"], t["cliente"], t["parte_adversa"],
            t["pasta_drive"], str(data or "")[:10] or time.strftime("%Y-%m-%d"), pf, pi, "normal", "aberta",
            "resposta", "%d:%d" % (tarefa_id, cid), por_nome, agora, agora]
    if "caso_id" in chaves and t["caso_id"]:
        cols.append("caso_id")
        vals.append(t["caso_id"])
    nid = c.execute("INSERT INTO tarefas (%s) VALUES (%s)" % (", ".join(cols), ", ".join("?" for _ in cols)),
                    vals).lastrowid
    for p in pessoas:
        c.execute("INSERT OR IGNORE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) "
                  "VALUES (?,?,?,0,0)", (nid, p["usuario_id"], p["nome"]))
    c.execute("UPDATE tarefa_comentarios SET tarefa_gerada=? WHERE id=?", (nid, cid))
    nomes = ", ".join(p["nome"] for p in pessoas)
    _hist(c, tarefa_id, "resposta", "respondeu para %s (tarefa #%d, %s): %s" % (nomes, nid, tipo, texto[:200]), por_nome)
    _hist(c, nid, "criada", "resposta de %s à tarefa #%d" % (por_nome, tarefa_id), por_nome)
    return {"comentario": _linha(c.execute("SELECT * FROM tarefa_comentarios WHERE id=?", (cid,)).fetchone()),
            "tarefa_id": nid, "para": pessoas, "prazo_interno": pi}


# ----------------------------------------------------------------------------- bateria
def _banco_de_teste():
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE tarefas (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, titulo TEXT NOT NULL,
            descricao TEXT, processo_numero TEXT, lawsuits_id TEXT, cliente TEXT, parte_adversa TEXT, pasta_drive TEXT,
            data TEXT, hora TEXT, prazo_fatal TEXT, prazo_interno TEXT, trilha TEXT, prioridade TEXT DEFAULT 'normal',
            status TEXT DEFAULT 'aberta', origem TEXT DEFAULT 'manual', origem_ref TEXT, criado_por TEXT,
            criado_em REAL, atualizado_em REAL, concluida_em REAL, caso_id INTEGER, cadeia TEXT, etapa INTEGER);
        CREATE UNIQUE INDEX ix_tarefa_origem ON tarefas(origem, origem_ref, tipo);
        CREATE TABLE tarefa_responsaveis (id INTEGER PRIMARY KEY AUTOINCREMENT, tarefa_id INTEGER NOT NULL,
            usuario_id INTEGER, nome TEXT, concluido_em REAL, importante INTEGER DEFAULT 0, urgente INTEGER DEFAULT 0);
        CREATE UNIQUE INDEX ix_tarefa_resp ON tarefa_responsaveis(tarefa_id, usuario_id);
        CREATE TABLE usuarios (id INTEGER PRIMARY KEY, nome TEXT, ativo INTEGER DEFAULT 1);
        CREATE TABLE tarefa_parceiros (id INTEGER PRIMARY KEY, nome TEXT, telefone TEXT, ativo INTEGER DEFAULT 1);
        INSERT INTO usuarios VALUES (3, 'Advogada', 1), (5, 'Advogada', 1), (6, 'Advogada', 1), (8, 'Desligada', 0);
        INSERT INTO tarefa_parceiros VALUES (2, 'Dr. Hélio Paranhos', '11999990000', 1);
        INSERT INTO tarefas (id, tipo, titulo, descricao, processo_numero, lawsuits_id, cliente, data, origem, origem_ref,
            criado_por, criado_em, atualizado_em, caso_id)
            VALUES (584, 'COMENTÁRIO', 'Distribuído: EZEQUIEL TORRES x VILLA_Processo 1000202-91.2026.5.02.0381',
                    'Card Atualizado', '1000202-91.2026.5.02.0381', '18028610', 'EZEQUIEL TORRES', '2026-10-06',
                    'advbox', '284335022', 'ADVBOX', 1791309400, 1791309400, 77);
        INSERT INTO tarefa_responsaveis (tarefa_id, usuario_id, nome) VALUES (584, 3, 'Advogada'), (584, 5, 'Advogada');
    """)
    return c


def _bateria():
    ok = total = 0

    def caso(nome, cond):
        nonlocal ok, total
        total += 1
        ok += 1 if cond else 0
        print("  [%s] %s" % ("OK " if cond else "X  ", nome))

    c = _banco_de_teste()
    fio0 = listar(c, 584)
    caso("fio vazio no comeco", fio0["comentarios"] == [] and fio0["veio_de"] is None)

    r = responder(c, 584, "  Advogada, ciente. Pasta conferida: https://drive.google.com/x  ", [5, -2, 99, 8, 5],
                  None, 3, "Advogada")
    nova = c.execute("SELECT * FROM tarefas WHERE id=?", (r["tarefa_id"],)).fetchone()
    resp = [dict(x) for x in c.execute("SELECT * FROM tarefa_responsaveis WHERE tarefa_id=? ORDER BY usuario_id", (r["tarefa_id"],))]
    caso("so quem existe e esta ativo entra (Advogada e o parceiro; 99 e a desligada ficam de fora)",
         [p["usuario_id"] for p in r["para"]] == [5, -2])
    caso("comentario gravado no fio da tarefa original, com texto limpo",
         r["comentario"]["tarefa_id"] == 584 and r["comentario"]["texto"].startswith("Advogada, ciente.")
         and r["comentario"]["para"][0]["nome"] == "Advogada" and r["comentario"]["tarefa_gerada"] == r["tarefa_id"])
    caso("tarefa nova: Re: no titulo, tipo COMENTARIO, origem resposta com a referencia tarefa:comentario",
         nova["titulo"].startswith("Re: Distribuído: EZEQUIEL") and nova["tipo"] == "COMENTARIO"
         and nova["origem"] == "resposta" and nova["origem_ref"] == "584:%d" % r["comentario"]["id"])
    caso("tarefa nova herda processo, cliente, lawsuit e caso; descricao traz o texto e o link de volta",
         nova["processo_numero"] == "1000202-91.2026.5.02.0381" and nova["cliente"] == "EZEQUIEL TORRES"
         and nova["lawsuits_id"] == "18028610" and nova["caso_id"] == 77
         and "Advogada, ciente." in nova["descricao"] and (LINK_TAREFA % 584) in nova["descricao"])
    caso("responsaveis da tarefa nova sao quem foi marcado (parceiro com id negativo)",
         [(x["usuario_id"], x["nome"]) for x in resp] == [(-2, "Dr. Hélio Paranhos"), (5, "Advogada")])
    caso("sem prazo informado a tarefa nova fica sem prazo fatal e sem interno",
         nova["prazo_fatal"] is None and nova["prazo_interno"] is None and nova["data"] == time.strftime("%Y-%m-%d"))

    fio1 = listar(c, 584)
    caso("fio da original agora tem 1 resposta, apontando para a tarefa gerada",
         len(fio1["comentarios"]) == 1 and fio1["comentarios"][0]["tarefa_gerada"] == r["tarefa_id"])
    fio_nova = listar(c, r["tarefa_id"])
    caso("a tarefa nova sabe de onde veio (quem respondeu e o link da original)",
         fio_nova["veio_de"] and fio_nova["veio_de"]["tarefa_id"] == 584
         and fio_nova["veio_de"]["comentario"]["por_nome"] == "Advogada"
         and fio_nova["veio_de"]["link"] == LINK_TAREFA % 584)

    r2 = responder(c, r["tarefa_id"], "Perfeito, protocolo amanhã.", [3], "PROTOCOLO", 5, "Advogada",
                   prazo_fatal="2026-10-20", pfi_de=lambda pf: "2026-10-16")
    nova2 = c.execute("SELECT * FROM tarefas WHERE id=?", (r2["tarefa_id"],)).fetchone()
    caso("responder a uma resposta: nao duplica o Re:, tipo escolhido vale, prazo fatal e interno D-2",
         nova2["titulo"].startswith("Re: Distribuído") and not nova2["titulo"].startswith("Re: Re:")
         and nova2["tipo"] == "PROTOCOLO" and nova2["prazo_fatal"] == "2026-10-20" and nova2["prazo_interno"] == "2026-10-16")
    hist = [(int(x[0]), x[1]) for x in c.execute("SELECT entidade_id, acao FROM jur_historico ORDER BY id")]  # fase 2: entidade_id vira texto
    caso("historico: resposta na original e criada na nova, nas duas rodadas",
         hist == [(584, "resposta"), (r["tarefa_id"], "criada"), (r["tarefa_id"], "resposta"), (r2["tarefa_id"], "criada")])

    erros = []
    for args in ((584, "   ", [5]), (584, "oi", []), (584, "oi", [99]), (999, "oi", [5])):
        try:
            responder(c, args[0], args[1], args[2], None, 3, "Advogada")
            erros.append("passou")
        except ValueError:
            erros.append("ValueError")
        except LookupError:
            erros.append("LookupError")
    caso("texto vazio, ninguem marcado, marcado inexistente e tarefa inexistente sao recusados",
         erros == ["ValueError", "ValueError", "ValueError", "LookupError"])
    caso("a recusa nao deixa lixo: continua 1 resposta no fio da original e 3 tarefas no banco",
         len(listar(c, 584)["comentarios"]) == 1 and c.execute("SELECT COUNT(*) FROM tarefas").fetchone()[0] == 3)
    print("tarefas_atividade: %d/%d, bateria ok" % (ok, total) if ok == total else "tarefas_atividade: %d/%d FALHOU" % (ok, total))
    return ok == total


if __name__ == "__main__":
    raise SystemExit(0 if _bateria() else 1)
