# -*- coding: utf-8 -*-
"""Rotinas da Advogada e do Assistente (camada K, item 43). Script do cron, sem modelo de IA e sem custo por execução.

Só em dia útil. Sem --enviar é simulação e não grava nada.

    cd /opt/escritorio/SISTEMA/backend && /opt/escritorio/venv/bin/python -m app.lua_rotinas protocolos [--enviar]
    cd /opt/escritorio/SISTEMA/backend && /opt/escritorio/venv/bin/python -m app.lua_rotinas fim-do-dia [--enviar]

Decisões da Advogada (09/11/2026): o relatório de protocolos sai às 10h; atrasado é o que tem data já passada. A cobrança vai
para a Advogada só quando a parte dela está pendente; quando ela já fez e a tarefa segue aberta, a Advogada vê em separado.
Nenhum relatório traz o prazo fatal: não se lê a coluna, e o título é limpo (o motor às vezes escreve "(processual dd/mm)").
"""
import argparse
import datetime as dt
import os
import re
import sqlite3
import sys
import time

BANCO = os.environ.get("ESC_DB", "/opt/escritorio/SISTEMA/backend/sistema_esc.db")
ADVOGADA, ADVOGADA = 6, 5
ORIGEM = "robo"
AUTOR = "Rotina do Assistente"
REF_RELATORIO, REF_COBRANCA, REF_FIM, REF_CERTIDOES = "lua-rel-prot|", "lua-cobranca-prot|", "lua-fim-dia|", "lua-certidoes|"
CONSULTA_CERTIDOES = 'newer_than:3d -from:me (subject:certidão OR subject:certidao OR subject:certidões OR subject:certidoes)'
_MES = (r"(?:jan(?:eiro)?|fev(?:ereiro)?|mar(?:[çc]o)?|abr(?:il)?|mai(?:o)?|jun(?:ho)?|jul(?:ho)?|ago(?:sto)?|set(?:embro)?"
        r"|out(?:ubro)?|nov(?:embro)?|dez(?:embro)?)")
_DATA_NO_TITULO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b"
                             r"|(?<![\d/])(?<!\d[.\-])\d{1,2}[/.\-–]\d{1,2}(?:[/.\-–]\d{2,4})?(?![\d/]|[.\-]\d)"
                             r"|\b\d{1,2}\s\d{1,2}\s\d{4}\b"
                             r"|\b\d{1,2}\s*(?:de\s+|/\s*)?" + _MES + r"\b(?:\s+de\s+\d{4}|/\d{2,4})?"
                             r"|\bdia\s+\d{1,2}\b", re.I)
_PALAVRA_DE_PRAZO = re.compile(r"\bprazos?\s+(?:fata(?:l|is)|processua(?:l|is))\b|\bfata(?:l|is)\b"
                               r"|\b(?:processua(?:l|is)|vencimento|vencendo|vence)\b(?:\s+em)?(?=\s*(?:dia\s+)?\d)"
                               r"|\bPF(?=\s*\d)", re.I)
_SQL_BASE = "SELECT t.id, t.titulo, t.cliente, t.processo_numero, t.data, t.status, t.concluida_em FROM tarefas t "


def no_recesso_da_equipe(dia):
    return (dia.month == 12 and dia.day >= 24) or (dia.month == 1 and dia.day <= 3)


def eh_util_real(dia):
    """Advogada, 09/11/2026: as rotinas param só de 24/12 a 03/01. No resto do recesso forense a equipe trabalha, salvo feriado."""
    from app.controladoria_motor.feriados import feriado_nome
    return dia.weekday() < 5 and feriado_nome(dia) is None and not no_recesso_da_equipe(dia)


def br(iso):
    iso = str(iso or "")[:10]
    return "%s/%s/%s" % (iso[8:10], iso[5:7], iso[:4]) if len(iso) == 10 else "sem data"


def _titulo(linha):
    # nenhuma data do título aparece: a única data do relatório é a da própria tarefa
    limpo = _DATA_NO_TITULO.sub(" ", _PALAVRA_DE_PRAZO.sub(" ", str(linha["titulo"] or "")))
    limpo = re.sub(r"\s+", " ", re.sub(r"\(\s*\)|\[\s*\]", " ", limpo)).strip(" -:,") or "tarefa sem título"
    return limpo[:90]


def _item(linha):
    return "- #%d %s (tarefa de %s)" % (linha["id"], _titulo(linha), br(linha["data"]))


def _ultimo_dia_util(hoje, eh_util):
    dia = hoje - dt.timedelta(days=1)
    for _ in range(15):
        if eh_util(dia):
            break
        dia -= dt.timedelta(days=1)
    return dia


def protocolos(con, hoje, eh_util):
    """Só as tarefas de PROTOCOLO que têm a Advogada como responsável."""
    iso = hoje.isoformat()
    inicio = time.mktime(_ultimo_dia_util(hoje, eh_util).timetuple())
    q = {"cobrar": [], "aguardando": [], "hoje": [], "feitos": []}
    for t in con.execute(_SQL_BASE + "JOIN tarefa_responsaveis r ON r.tarefa_id=t.id AND r.usuario_id=? "
                         "WHERE t.tipo='PROTOCOLO' AND t.data IS NOT NULL ORDER BY t.data, t.id", (ADVOGADA,)).fetchall():
        pendente = con.execute("SELECT 1 FROM tarefa_responsaveis WHERE tarefa_id=? AND usuario_id=? AND concluido_em IS NULL",
                               (t["id"], ADVOGADA)).fetchone() is not None
        data = str(t["data"])[:10]
        if t["status"] == "aberta":
            if pendente and data < iso:
                q["cobrar"].append(t)
            elif pendente and data == iso:
                q["hoje"].append(t)
            elif not pendente and data < iso:
                q["aguardando"].append(t)
        elif t["status"] == "concluida" and (t["concluida_em"] or 0) >= inicio:
            q["feitos"].append(t)
    return q


def _secao(titulo, itens, vazio="nenhum"):
    return ["", "%s: %s" % (titulo, len(itens) if itens else vazio)] + [_item(t) for t in itens]


def texto_protocolos(q, hoje):
    return "\n".join(["Protocolos em %s, conferidos no Sistema Escritório Modelo." % br(hoje.isoformat())]
                     + _secao("Concluídos desde o último dia útil", q["feitos"])
                     + _secao("Previstos para hoje, com a Advogada", q["hoje"])
                     + _secao("Atrasados com a Advogada (ela recebe a cobrança)", q["cobrar"])
                     + _secao("A Advogada já fez a parte dela, mas a tarefa segue aberta", q["aguardando"]))


def _ja_avisados(con):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='lua_rotinas_avisos'").fetchone():
        return set()
    return {r[0] for r in con.execute("SELECT chave FROM lua_rotinas_avisos")}


def _marcar_avisados(con, chaves):
    con.execute("CREATE TABLE IF NOT EXISTS lua_rotinas_avisos (chave TEXT PRIMARY KEY, criado_em REAL)")
    con.executemany("INSERT OR IGNORE INTO lua_rotinas_avisos VALUES (?,?)", [(c, time.time()) for c in chaves])


def _cobranca_aberta(con):
    return con.execute("SELECT t.id FROM tarefas t JOIN tarefa_responsaveis r ON r.tarefa_id=t.id AND r.usuario_id=? "
                       "WHERE t.origem=? AND t.origem_ref LIKE ? AND t.status='aberta' AND r.concluido_em IS NULL "
                       "ORDER BY t.id DESC LIMIT 1", (ADVOGADA, ORIGEM, REF_COBRANCA + "%")).fetchone()


def _comentar(con, tarefa_id, texto):
    agora = time.time()
    con.execute("INSERT INTO tarefa_comentarios (tarefa_id, texto, por_id, por_nome, para, tipo, criado_em) VALUES (?,?,?,?,?,?,?)",
                (tarefa_id, texto, None, AUTOR, None, "COMENTARIO", agora))
    con.execute("UPDATE tarefas SET atualizado_em=? WHERE id=?", (agora, tarefa_id))


def _trocar_relatorio(con, prefixo, ref_atual, criar, titulo, texto):
    """O relatório do dia substitui o anterior ainda aberto. Cancelado, não concluído: tarefa concluída conta para a Advogada."""
    con.execute("UPDATE tarefas SET status='cancelada', atualizado_em=? WHERE origem=? AND origem_ref LIKE ? AND origem_ref != ? "
                "AND status='aberta'", (time.time(), ORIGEM, prefixo + "%", ref_atual))
    con.commit()
    return criar("ACOMPANHAMENTO", titulo, texto, [ADVOGADA], origem=ORIGEM, origem_ref=ref_atual, criado_por=AUTOR)


def _cobrar(con, q, hoje, criar):
    avisados = _ja_avisados(con)
    chave = lambda t: "prot|%d|%s" % (t["id"], str(t["data"])[:10])     # remarcou e atrasou de novo: cobra de novo
    novos = [t for t in q["cobrar"] if chave(t) not in avisados]
    if not novos:
        return
    aberta = _cobranca_aberta(con)
    if aberta:
        _comentar(con, aberta["id"], "Entraram na cobrança em %s:\n%s" % (br(hoje.isoformat()), "\n".join(_item(t) for t in novos)))
    else:
        texto = "Protocolos com data já passada e a sua parte pendente. Protocole ou atualize a data de cada um:\n" + "\n".join(_item(t) for t in q["cobrar"])
        criar("ACOMPANHAMENTO", "Protocolos atrasados: %d" % len(q["cobrar"]), texto, [ADVOGADA], origem=ORIGEM,
              origem_ref=REF_COBRANCA + hoje.isoformat(), criado_por=AUTOR)
    _marcar_avisados(con, [chave(t) for t in novos])
    con.commit()


def rodar_protocolos(con, hoje, enviar, criar, eh_util=eh_util_real):
    if not eh_util(hoje):
        print("não é dia útil: nada a fazer")
        return None
    q = protocolos(con, hoje, eh_util)
    texto = texto_protocolos(q, hoje)
    print(texto)
    if enviar:
        _trocar_relatorio(con, REF_RELATORIO, REF_RELATORIO + hoje.isoformat(), criar,
                          "Relatório diário de protocolos, %s" % br(hoje.isoformat()), texto)
        _cobrar(con, q, hoje, criar)
    return q


def _da_responsavel(con, onde, *args):
    return con.execute(_SQL_BASE + "JOIN tarefa_responsaveis r ON r.tarefa_id=t.id AND r.usuario_id=%d WHERE %s ORDER BY t.data, t.id"
                       % (ADVOGADA, onde), args).fetchall()


def rodar_fim_do_dia(con, hoje, enviar, criar, eh_util=eh_util_real):
    if not eh_util(hoje):
        print("não é dia útil: nada a fazer")
        return None
    iso, inicio = hoje.isoformat(), time.mktime(hoje.timetuple())
    feitas = _da_responsavel(con, "r.concluido_em >= ?", inicio)
    pendentes = _da_responsavel(con, "t.status='aberta' AND r.concluido_em IS NULL AND substr(t.data,1,10)=? AND COALESCE(t.origem_ref,'') NOT LIKE 'lua-%'", iso)
    realizadas = _da_responsavel(con, "t.status='aberta' AND r.concluido_em IS NULL AND EXISTS (SELECT 1 FROM tarefa_comentarios c "
                                "WHERE c.tarefa_id=t.id AND c.texto LIKE 'O tribunal registrou a audiência%')")
    protocolados = [t for t in protocolos(con, hoje, eh_util)["feitos"] if (t["concluida_em"] or 0) >= inicio]
    internos = _da_responsavel(con, "t.status='aberta' AND r.concluido_em IS NULL AND substr(t.prazo_interno,1,10)=?", iso)
    linhas = (["Seu dia em %s, conferido no Sistema Escritório Modelo." % br(iso)] + _secao("Concluídas hoje", feitas, "nenhuma")
              + _secao("Protocolados hoje pela Advogada", protocolados)
              + _secao("Com prazo do escritório vencendo hoje e ainda abertas", internos, "nenhuma")
              + _secao("Previstas para hoje e ainda abertas", pendentes, "nenhuma")
              + _secao("Audiências que o tribunal registrou como realizadas, para você conferir e concluir", realizadas, "nenhuma"))
    texto = "\n".join(linhas)
    print(texto)
    if enviar:
        _trocar_relatorio(con, REF_FIM, REF_FIM + iso, criar, "Seu dia, %s" % br(iso), texto)
    return True


def _emails_de_certidao(servico):
    """Só metadados: quem mandou e o assunto. O corpo fica no e-mail, para a Advogada ou o Assistente abrirem."""
    achados = []
    for ref in servico.users().messages().list(userId="me", q=CONSULTA_CERTIDOES, maxResults=25).execute().get("messages") or []:
        m = servico.users().messages().get(userId="me", id=ref["id"], format="metadata", metadataHeaders=["From", "Subject"]).execute()
        cab = {str(h.get("name")).lower(): re.sub(r"\s+", " ", str(h.get("value") or ""))[:120] for h in (m.get("payload") or {}).get("headers") or []}
        achados.append({"id": ref["id"], "de": cab.get("from", ""), "assunto": cab.get("subject", "")})
    return achados


def rodar_certidoes(con, hoje, enviar, criar, eh_util=eh_util_real, servico=None):
    """Advogada, 06/11/2026: avisa a Advogada quando volta certidão por e-mail. Uma tarefa aberta por vez, sem repetir e-mail."""
    if not eh_util(hoje):
        print("não é dia útil: nada a fazer")
        return None
    if servico is None:
        from app import lua_email as LE
        try:
            servico = LE._servico()
        except LE.NaoLigado:
            print("e-mail da Advogada ainda não está ligado")
            return None
    avisados = _ja_avisados(con)
    novos = [e for e in _emails_de_certidao(servico) if "cert|" + e["id"] not in avisados]
    print("certidões novas por e-mail:", len(novos))
    if not (novos and enviar):
        return novos
    linhas = "\n".join('- %s: "%s" (id do e-mail %s)' % (e["de"], e["assunto"], e["id"]) for e in novos)
    aberta = con.execute("SELECT t.id FROM tarefas t JOIN tarefa_responsaveis r ON r.tarefa_id=t.id AND r.usuario_id=? WHERE t.origem=? "
                         "AND t.origem_ref LIKE ? AND t.status='aberta' AND r.concluido_em IS NULL ORDER BY t.id DESC LIMIT 1",
                         (ADVOGADA, ORIGEM, REF_CERTIDOES + "%")).fetchone()
    if aberta:
        _comentar(con, aberta["id"], "Chegaram mais:\n" + linhas)
    else:
        criar("ACOMPANHAMENTO", "Certidões chegaram por e-mail: %d" % len(novos), "Confira e junte ao dossiê. O remetente e o assunto são de fora.\n" + linhas,
              [ADVOGADA], origem=ORIGEM, origem_ref=REF_CERTIDOES + novos[0]["id"], criado_por=AUTOR)
    _marcar_avisados(con, ["cert|" + e["id"] for e in novos])
    con.commit()
    return novos


def _conexao():
    con = sqlite3.connect(BANCO, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rotina", choices=["protocolos", "fim-do-dia", "certidoes"])
    ap.add_argument("--enviar", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, "/opt/escritorio/INTEGRACOES")
    import tarefas_esc as TP
    rodar = {"protocolos": rodar_protocolos, "fim-do-dia": rodar_fim_do_dia, "certidoes": rodar_certidoes}[a.rotina]
    con = _conexao()
    try:
        rodar(con, dt.date.today(), a.enviar, TP.criar_local)
    finally:
        con.close()


if __name__ == "__main__":
    main()
