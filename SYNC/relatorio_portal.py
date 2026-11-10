""
import argparse
import importlib.util
import os
import sqlite3
import sys
import time
from collections import defaultdict

DB = os.getenv("SISTEMA_DB", "/opt/escritorio/SISTEMA/backend/sistema_esc.db")

IP_INTERNOS = {x for x in {"127.0.0.1", os.getenv("IP_DA_VPS", "")} if x}
_FILTRO_IP = " AND (ip IS NULL OR ip NOT IN (%s))" % ",".join("?" * len(IP_INTERNOS))
_ARGS_IP = list(IP_INTERNOS)
DESTINOS = [d.strip() for d in os.getenv(
    "RELATORIO_PORTAL_DESTINO", "").split(",") if d.strip()]

sys.path.insert(0, "/opt/escritorio/SISTEMA/backend")


def _enviar(destino, texto):
    from app.whatsapp import zapi
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM wa_numeros WHERE ativo=1 ORDER BY id LIMIT 1").fetchone()
    con.close()
    if not r:
        return False, "nenhum numero de WhatsApp ativo"

    class N:
        pass
    wa = N()
    for k in r.keys():
        setattr(wa, k, r[k])
    return zapi.enviar_texto(wa, destino, texto)


def _con():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def _hora(t):
    return time.strftime("%d/%m %Hh%M", time.localtime(t))


def _primeiro(nome):
    return (nome or "").split()[0].title() if nome else "?"


def _ips_da_casa(con):
    ""
    ips = set(IP_INTERNOS)
    equipe = [r[0] for r in con.execute(
        "SELECT id FROM jur_clientes WHERE UPPER(nome) = 'ADVOGADA KAREN BARBOSA DOS ESCRITÓRIO MODELO'")]
    ph = ",".join("?" * len(equipe)) or "NULL"
    for (ip,) in con.execute(
            "SELECT DISTINCT ip FROM portal_log WHERE acao = 'codigo_enviado' "
            "AND ip IS NOT NULL AND (UPPER(detalhe) LIKE '%%ADVOGADA%%' "
            "OR cliente_id IN (%s))" % ph, equipe):
        ips.add(ip)
    return ips


def montar(horas=24):
    corte = time.time() - horas * 3600
    con = _con()

    _casa = _ips_da_casa(con)
    _FILTRO_IP = " AND (ip IS NULL OR ip NOT IN (%s))" % ",".join("?" * len(_casa))
    _ARGS_IP = list(_casa)

    nomes = {r[0]: r[1] for r in con.execute("SELECT id, nome FROM jur_clientes")}

    eventos = [dict(r) for r in con.execute(
        "SELECT cpf, cliente_id, acao, detalhe, criado_em FROM portal_log "
        "WHERE criado_em > ?" + _FILTRO_IP + " ORDER BY criado_em",
        [corte] + _ARGS_IP)]
    duvidas = [dict(r) for r in con.execute(
        "SELECT cliente_id, numero, pergunta, resposta, criado_em FROM portal_duvida "
        "WHERE criado_em > ? ORDER BY criado_em", (corte,))]
    perguntas_reais = set()
    for r in con.execute(
            "SELECT cliente_id, detalhe FROM portal_log WHERE acao='duvida' "
            "AND criado_em > ?" + _FILTRO_IP, [corte] + _ARGS_IP):
        det = (r[1] or "")
        if ": " in det:
            perguntas_reais.add((r[0], det.split(": ", 1)[1].split(" [")[0].strip()[:40]))
    duvidas = [d for d in duvidas
               if (d["cliente_id"], (d["pergunta"] or "")[:40]) in perguntas_reais]
    convites = [dict(r) for r in con.execute(
        "SELECT cliente_id, celular, enviado_em FROM portal_convite WHERE ok=1")]

    por_acao = defaultdict(list)
    for e in eventos:
        por_acao[e["acao"]].append(e)

    entraram = {e["cliente_id"] for e in por_acao["login_ok"] if e["cliente_id"]}
    pediram = {e["cpf"] for e in por_acao["codigo_enviado"]}
    entraram_cpf = {e["cpf"] for e in por_acao["login_ok"]}
    travados = pediram - entraram_cpf

    ja_entrou_algum_dia = {r[0] for r in con.execute(
        "SELECT DISTINCT cliente_id FROM portal_log WHERE acao='login_ok' "
        "AND cliente_id IS NOT NULL" + _FILTRO_IP, _ARGS_IP)}
    nunca_entraram = [c for c in convites if c["cliente_id"] not in ja_entrou_algum_dia]

    try:
        nps = [dict(r) for r in con.execute(
            "SELECT cliente_id, facil, gostou, faz_sentido, comentario, criado_em "
            "FROM portal_nps WHERE criado_em > ? ORDER BY criado_em", (corte,))]
    except sqlite3.OperationalError:
        nps = []

    houve_movimento = bool(duvidas or entraram or travados or nps
                           or por_acao["codigo_negado"] or por_acao["login_falhou"])

    L = []
    L.append("*Portal do Cliente* — últimas %dh" % horas)
    L.append("")

    escalou = set()
    for e in por_acao["duvida"]:
        det = e["detalhe"] or ""
        if "[ESCALAR:" in det and ": " in det:
            escalou.add(det.split(": ", 1)[1].split(" [")[0].strip()[:40])

    if duvidas:
        L.append("*Perguntas na caixa (%d)*" % len(duvidas))
        for d in duvidas:
            quem = _primeiro(nomes.get(d["cliente_id"]))
            marca = "  ⚠️ *quer falar com você*" if (d["pergunta"] or "")[:40] in escalou else ""
            L.append("• %s, %s%s" % (quem, _hora(d["criado_em"]), marca))
            L.append("  _%s_" % (d["pergunta"] or "")[:180])
            resp = (d["resposta"] or "").replace("\n", " ")[:170]
            if resp:
                L.append("  ↳ %s" % resp)
        L.append("")

    problemas = []
    if travados:
        problemas.append("%d pediram o código e não entraram" % len(travados))
    if por_acao["codigo_negado"]:
        problemas.append("%d códigos negados" % len(por_acao["codigo_negado"]))
    if por_acao["login_falhou"]:
        problemas.append("%d códigos digitados errado" % len(por_acao["login_falhou"]))
    if escalou and not duvidas:
        problemas.append("%d perguntas pediram atendimento humano" % len(escalou))

    if problemas:
        L.append("*Precisa de olho*")
        for p in problemas:
            L.append("• " + p)
        for cpf in list(travados)[:6]:
            L.append("  documento ***%s — o código chegou?" % (cpf or "")[-4:])
        L.append("")

    if entraram:
        L.append("*Quem entrou (%d)*" % len(entraram))
        for cid in entraram:
            atos = [e for e in eventos if e["cliente_id"] == cid]
            quando = _hora(min(a["criado_em"] for a in atos))
            fez = []
            n_proc = len([a for a in atos if a["acao"] == "abriu_processo"])
            n_autos = len([a for a in atos if a["acao"] == "abriu_autos"])
            n_doc = len([a for a in atos if a["acao"] in ("leu_documento", "baixou_documento")])
            n_pasta = len([a for a in atos if a["acao"] == "pasta_liberada"])
            if n_proc:
                fez.append("%d processo%s" % (n_proc, "s" if n_proc > 1 else ""))
            if n_autos:
                fez.append("autos completos")
            if n_doc:
                fez.append("%d documento%s" % (n_doc, "s" if n_doc > 1 else ""))
            if n_pasta:
                fez.append("abriu a pasta")
            L.append("• %s, %s — %s" % (
                (nomes.get(cid) or "?")[:26], quando, ", ".join(fez) or "só entrou"))
        L.append("")

    if nps:
        L.append("*Respostas do NPS (%d)*" % len(nps))
        for r in nps:
            L.append("• %s — fácil %s/5, gostou %s/5, faz sentido %s/5" % (
                _primeiro(nomes.get(r["cliente_id"])),
                r["facil"], r["gostou"], r["faz_sentido"]))
            if r["comentario"]:
                L.append("  _%s_" % r["comentario"][:180])
        L.append("")

    if nunca_entraram:
        L.append("*Convidados que ainda não entraram (%d)*" % len(nunca_entraram))
        for c in nunca_entraram[:10]:
            L.append("• %s — convite de %s" % (
                (nomes.get(c["cliente_id"]) or "?")[:26], _hora(c["enviado_em"])))
        L.append("")

    L.append("_Detalhe completo: tela Resultado do Portal._")
    return "\n".join(L).strip(), (not houve_movimento)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--enviar", action="store_true")
    ap.add_argument("--horas", type=int, default=24)
    a = ap.parse_args()

    txt, vazio = montar(a.horas)
    print(txt)
    if vazio:
        print("\n[dia sem movimento: nao envia]")
        return
    if a.enviar:
        falhas = 0
        for destino in DESTINOS:
            ok, resp = _enviar(destino, txt)
            print("\n[envio Z-API -> %s] ok=%s %s" % (destino, ok, resp))
            falhas += 0 if ok else 1
        sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
