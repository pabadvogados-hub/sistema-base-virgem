#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Lembretes de audiencia e pericia para o cliente. Template fixo, ZERO token.

POP da Advogada (03/09/2026), documento "LEMBRETE DE AUDIENCIA":
  1. quando o despacho chega, comunica a data e cadastra na agenda  -> feito na
     tela de Intimacoes, no botao Agendar;
  2. uma SEMANA antes  -> lembrete completo;
  3. no dia ANTERIOR   -> "e amanha";
  4. no PROPRIO DIA    -> antes do compromisso.

SUSTENTACAO ORAL e JULGAMENTO nao geram lembrete: o cliente nao comparece.
Esses so interessam a nos, e o cliente e avisado do RESULTADO depois.

NAO INVENTA ENDERECO. Se o evento nao tem local cadastrado, a mensagem diz que
o local sera confirmado, em vez de mandar o cliente para o lugar errado.

Idempotente por (evento, marco): rodar duas vezes no mesmo dia nao reenvia.

Uso:
    python lembretes_compromisso.py --dry-run   # mostra o que mandaria
    python lembretes_compromisso.py             # manda de verdade
"""
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import date, datetime, timedelta

sys.path.insert(0, "/opt/escritorio/SISTEMA/backend")

from app import agenda_google as AG            # noqa: E402
from app.whatsapp import zapi                  # noqa: E402

DB = "/opt/escritorio/SISTEMA/backend/sistema_esc.db"
DRY = "--dry-run" in sys.argv
DIAS_FRENTE = 30

SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
          "sexta-feira", "sábado", "domingo"]

# marcos que a Advogada definiu, em dias de distancia do compromisso
MARCOS = {7: "semana", 1: "vespera", 0: "dia"}

CNJ = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return s.upper()


def classificar(titulo):
    """audiencia | pericia | None (None = nao lembra o cliente).

    A ordem importa: 'AUDIENCIA DE PERICIA' e pericia, nao audiencia.
    """
    t = _norm(titulo)
    # "PRAZO LAUDO PERICIA" e prazo nosso, nao pericia a que o cliente vai
    if re.match(r"^\s*(PF[_ ])?PRAZO", t):
        return None
    if "SUSTENTACAO" in t or "JULGAMENTO" in t or "PAUTA" in t:
        return None
    if "PERICIA" in t or "PERICIAL" in t:
        return "pericia"
    if "AUDIENCIA" in t:
        return "audiencia"
    return None


def numero_do_processo(ev):
    """Do extendedProperties quando o evento foi criado por nos; senao do
    titulo, que sempre carrega o CNJ no padrao do escritorio."""
    p = (ev.get("processo") or "").strip()
    if CNJ.fullmatch(p):
        return p
    m = CNJ.search(str(ev.get("titulo") or ""))
    return m.group(0) if m else ""


def _con():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS agenda_lembretes (
                   evento_id TEXT NOT NULL,
                   marco TEXT NOT NULL,
                   telefone TEXT,
                   processo TEXT,
                   enviado_em TEXT,
                   PRIMARY KEY (evento_id, marco))""")
    return c


def destinatario(con, numero):
    """Cliente e telefone pelo vinculo de cadastro, nunca por nome.

    Casar por nome ja deu o telefone de um cliente para outro so pelo
    sobrenome; num lembrete de audiencia isso mandaria a pessoa errada ao
    forum.
    """
    p = con.execute("SELECT id FROM jur_processos WHERE numero=?", (numero,)).fetchone()
    if not p:
        return None
    # cliente do parceiro: quem avisa e o parceiro, nunca nos direto
    try:
        par = con.execute(
            "SELECT nome FROM processo_contato WHERE ativo=1 AND tipo='cliente_do_parceiro' "
            "AND (processo_numero=? OR cliente_id IN "
            "(SELECT cliente_id FROM jur_processo_partes WHERE processo_id=?))",
            (numero, p["id"])).fetchone()
    except sqlite3.OperationalError:
        par = None
    if par:
        return {"parceiro": par["nome"]}
    # `papel` guarda a ORIGEM do cadastro; so PARTE CONTRARIA e adversario
    sql = ("SELECT cl.nome, cl.celular, cl.telefone "
           "FROM jur_processo_partes pp JOIN jur_clientes cl ON cl.id = pp.cliente_id "
           "WHERE pp.processo_id=? AND pp.cliente_id IS NOT NULL "
           "AND UPPER(COALESCE(pp.papel,'')) NOT LIKE 'PARTE CONTR%'")
    for r in con.execute(sql, (p["id"],)):
        tel = (r["celular"] or r["telefone"] or "").strip()
        if tel:
            return {"nome": r["nome"], "telefone": tel}
    return None


def so_digitos(t):
    d = "".join(ch for ch in str(t or "") if ch.isdigit())
    if len(d) in (10, 11):
        d = "55" + d
    return d


def primeiro_nome(nome):
    partes = [p for p in str(nome or "").split() if len(p) > 2]
    return partes[0].capitalize() if partes else "tudo bem"


def adversa_do_titulo(titulo):
    """No padrao do escritorio o titulo tem CLIENTE X PARTE ADVERSA."""
    t = str(titulo or "")
    m = re.search(r"\sX\s([^_(]+)", t, re.I)
    return m.group(1).strip(" -_") if m else ""


def montar(marco, tipo, ev, alvo):
    """Mensagem do POP. Linguagem simples e sem travessao, que e a regra do
    escritorio para o que vai ao cliente."""
    d = datetime.strptime(ev["data"], "%Y-%m-%d").date()
    dia_semana = SEMANA[d.weekday()]
    rotulo = "perícia" if tipo == "pericia" else "audiência"
    adversa = adversa_do_titulo(ev.get("titulo"))
    local = (ev.get("local") or "").strip()

    if marco == "semana":
        abertura = ("Passando para lembrar que falta uma semana para a sua %s."
                    % rotulo)
    elif marco == "vespera":
        abertura = "Está chegando: a sua %s é amanhã." % rotulo
    else:
        abertura = "Hoje é o dia da sua %s." % rotulo

    linhas = ["Olá, %s. Aqui é do Escritório Modelo Advogados." % primeiro_nome(alvo["nome"]),
              "", abertura, ""]
    # nunca dizer "processo contra <o proprio cliente>": se o titulo estiver
    # invertido ou o cadastro trocado, a frase sai absurda para quem le
    if adversa and _norm(adversa)[:14] != _norm(alvo["nome"])[:14]:
        linhas.append("Processo contra %s." % adversa.title())
    if ev.get("processo_numero"):
        linhas.append("Número: %s" % ev["processo_numero"])
    linhas += ["", "Data: %s (%s)" % (d.strftime("%d/%m/%Y"), dia_semana)]
    if ev.get("hora"):
        linhas.append("Horário: %s" % ev["hora"])
    if local:
        linhas.append("Local: %s" % local)
    else:
        linhas.append("Vamos confirmar o local com você antes da data.")

    linhas += ["", "Orientações importantes:",
               "Leve um documento oficial com foto, RG ou CNH.",
               "Chegue com pelo menos 40 minutos de antecedência.",
               "Use roupa discreta ou formal.",
               "", "Qualquer dúvida, é só chamar por aqui.",
               "Operacional Escritório Modelo Advogados."]
    return "\n".join(linhas)


def enviar(con, tel, texto):
    r = con.execute("SELECT zapi_instance, zapi_token, zapi_client_token "
                    "FROM wa_numeros WHERE id=1").fetchone()
    if not r:
        return False, "WhatsApp do escritorio nao configurado"
    from types import SimpleNamespace
    numero = SimpleNamespace(zapi_instance=r[0], zapi_token=r[1], zapi_client_token=r[2])
    return zapi.enviar_texto(numero, tel, texto)


def main():
    hoje = date.today()
    con = _con()
    try:
        eventos = AG.listar(dias_atras=0, dias_frente=DIAS_FRENTE)
    except Exception as e:
        print("ERRO ao ler a agenda:", str(e)[:160])
        return 1

    # Audiencias registradas no ADVBOX e nas intimacoes, que nunca foram postas
    # na agenda. Sem isto o robo ficou mudo por 11 dias e o Ataíde faltou.
    try:
        na_agenda = {(numero_do_processo(e), e.get("data")) for e in eventos}
        for r in con.execute(
                "SELECT * FROM audiencia_registro WHERE status='ativa' AND data BETWEEN ? AND ?",
                (hoje.isoformat(), (hoje + timedelta(days=DIAS_FRENTE)).isoformat())):
            if (r["processo"], r["data"]) in na_agenda:
                continue
            titulo = "AUDIENCIA_%s" % (r["cliente"] or "")
            if r["adversa"]:
                titulo += " X %s" % r["adversa"]
            eventos.append({"evento_id": "aud:%s:%s" % (r["processo"], r["data"]),
                            "titulo": titulo + "_" + r["processo"],
                            "data": r["data"], "hora": r["hora"] or None,
                            "local": r["local"] or "", "processo": r["processo"]})
    except sqlite3.OperationalError:
        pass   # registro ainda nao existe: segue so com a agenda

    ja = {(r["evento_id"], r["marco"]) for r in
          con.execute("SELECT evento_id, marco FROM agenda_lembretes")}

    enviados, pulados, sem_dest = 0, 0, []
    for ev in eventos:
        tipo = classificar(ev.get("titulo"))
        if not tipo:
            continue
        try:
            d = datetime.strptime(ev["data"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        faltam = (d - hoje).days
        if faltam not in MARCOS:
            continue
        marco = MARCOS[faltam]
        numero = numero_do_processo(ev)
        # a mesma audiencia pode vir da agenda e do registro: a chave e o
        # processo com a data, nao o id do evento
        chave = "aud:%s:%s" % (numero, ev["data"]) if numero else ev["evento_id"]
        if (chave, marco) in ja or (ev["evento_id"], marco) in ja:
            pulados += 1
            continue

        alvo = destinatario(con, numero) if numero else None
        if alvo and alvo.get("parceiro"):
            sem_dest.append("%s | %s | cliente do parceiro: quem avisa e %s"
                            % (ev["data"], numero, alvo["parceiro"]))
            continue
        if not alvo:
            sem_dest.append("%s | %s | %s" % (ev["data"], (ev.get("titulo") or "")[:52],
                                              numero or "sem numero de processo"))
            continue

        ev["processo_numero"] = numero
        texto = montar(marco, tipo, ev, alvo)
        tel = so_digitos(alvo["telefone"])
        if len(tel) < 12:
            sem_dest.append("%s | telefone invalido: %s" % (ev["data"], alvo["telefone"]))
            continue

        print("-" * 62)
        print("%s | %s | %s | %s" % (marco.upper(), tipo, ev["data"], alvo["nome"]))
        print(texto)
        if DRY:
            continue
        ok, info = enviar(con, tel, texto)
        if not ok:
            print("  FALHOU:", str(info)[:120])
            continue
        con.execute("INSERT OR REPLACE INTO agenda_lembretes"
                    "(evento_id, marco, telefone, processo, enviado_em) VALUES (?,?,?,?,?)",
                    (chave, marco, tel, numero,
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
        enviados += 1

    print("=" * 62)
    print("enviados: %d | ja enviados antes: %d | sem destinatario: %d"
          % (enviados, pulados, len(sem_dest)))
    for s in sem_dest:
        print("  SEM DESTINATARIO:", s)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
