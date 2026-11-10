"""TAREFAS PARA CONFERIR - Sistema Escritório Modelo (29/09/2026, a Advogada avisou: "tem tarefa marcada como
atrasada que nao esta").

A causa: tarefas que NAO vieram do ADVBOX (o "motor" da Controladoria abriu 15 de uma vez
em 31/08; intimacao; manuais) nao tem quem as feche. O trabalho foi feito no ADVBOX e a
copia daqui ficou aberta, vencida. As que vem do ADVBOX o espelho ja fecha sozinho.

O vigia (SYNC/tarefas_conferir.py, 7h10 e 13h10) olha cada tarefa aberta, vencida e que
nao veio do ADVBOX, e procura no ADVBOX (so leitura) o que foi feito no mesmo processo:
  forte  -> tarefa do mesmo tipo concluida la (ex.: CONTRARRAZOES feita pela Advogada em 21/08)
  medio  -> PROTOCOLO concluido la depois que a tarefa nasceu
  fraco  -> nada la; venceu ha dias e ninguem mexeu aqui (so para as abertas por robo:
            motor ou intimacao; tarefa criada a mao sem prova continua atrasada)
Nada e fechado sozinho: a tarefa sai de "Atrasadas" e vai para "Conferir", com a prova.
Quem e responsavel (ou o agente, a pedido) responde "ja foi feita" (conclui) ou "ainda falta"
(volta a contar como atrasada e o vigia nao pergunta de novo).
"""
import datetime as dt
import json
import re
import time
import unicodedata

FAMILIAS = [
    ("CONTRARRAZOES", ("CONTRARRAZ",)),
    ("RECURSO ORDINARIO", ("RECURSO ORDINARIO", " RO ")),
    ("RECURSO DE REVISTA", ("RECURSO DE REVISTA", " RR ")),
    ("AGRAVO", ("AGRAVO",)),
    ("EMBARGOS", ("EMBARGOS",)),
    ("CONTESTACAO", ("CONTESTACAO",)),
    ("REPLICA", ("REPLICA", "IMPUGNACAO")),
    ("MANIFESTACAO", ("MANIFESTACAO",)),
    ("CUSTAS", ("CUSTAS", "PREPARO", "GUIA")),
    ("PAGAMENTO", ("PAGAMENTO", "DEPOSITO")),
    ("PROTOCOLO", ("PROTOCOLO",)),
]
PECAS = {"CONTRARRAZOES", "RECURSO ORDINARIO", "RECURSO DE REVISTA", "AGRAVO", "EMBARGOS", "CONTESTACAO",
         "REPLICA", "MANIFESTACAO"}
# dia em que a equipe parou de cadastrar tarefa no ADVBOX (Advogada, 01/10/2026)
CORTE_ADVBOX = dt.date(2026, 10, 6)


def _sem_acento(s):
    return "".join(ch for ch in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(ch) != "Mn").upper()


def familia(tipo):
    t = " " + _sem_acento(tipo) + " "
    for nome, chaves in FAMILIAS:
        if any(k in t for k in chaves):
            return nome
    return None


def digitos(s):
    return re.sub(r"\D", "", str(s or ""))


def garantir(c):
    c.execute("""CREATE TABLE IF NOT EXISTS tarefa_conferir (
                   tarefa_id INTEGER PRIMARY KEY, nivel TEXT, motivo TEXT, evidencia TEXT,
                   status TEXT DEFAULT 'suspeita', detectado_em REAL, resolvido_por TEXT, resolvido_em REAL)""")


def _dia(v):
    try:
        return dt.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def _alvo(t):
    """A data que manda: o prazo fatal; sem ele, o compromisso."""
    return _dia(t["prazo_fatal"]) or _dia(t["prazo_interno"] or t["data"])


def varrer(c, feitas_advbox, pendentes_advbox, hoje=None, folga_dias=1):
    """Marca como 'suspeita' as tarefas abertas, vencidas e de fora do ADVBOX.

    feitas_advbox / pendentes_advbox: listas de tarefas do ADVBOX (GET /posts).
    Devolve (novas, atualizadas). Nao conclui nada."""
    garantir(c)
    hoje = hoje or dt.date.today()
    por_proc = {}
    for estado, lista in (("feita", feitas_advbox), ("pendente", pendentes_advbox)):
        for x in lista:
            num = digitos((x.get("lawsuit") or {}).get("process_number"))
            if num:
                por_proc.setdefault(num, []).append((estado, x))
    novas = atualizadas = 0
    linhas = c.execute(
        "SELECT t.* FROM tarefas t WHERE t.status='aberta' AND COALESCE(t.origem,'') != 'advbox' AND EXISTS "
        "(SELECT 1 FROM tarefa_responsaveis r WHERE r.tarefa_id=t.id AND r.concluido_em IS NULL)").fetchall()
    for t in linhas:
        alvo = _alvo(t)
        if not alvo or alvo >= hoje - dt.timedelta(days=folga_dias):
            continue
        ja = c.execute("SELECT status FROM tarefa_conferir WHERE tarefa_id=?", (t["id"],)).fetchone()
        if ja and ja["status"] != "suspeita":
            continue                       # alguem ja respondeu: nao pergunta de novo
        fam = familia(t["tipo"])
        nasceu = dt.date.fromtimestamp(float(t["criado_em"])) if str(t["criado_em"] or "").replace(".", "").isdigit() \
            else (_dia(t["criado_em"]) or alvo)
        desde = nasceu - dt.timedelta(days=20)
        provas, nivel = [], "fraco"
        for estado, x in por_proc.get(digitos(t["processo_numero"]), []):
            if estado != "feita":
                continue
            fx = familia(x.get("task"))
            quem = [u for u in (x.get("users") or []) if u.get("completed")]
            quando = max((_dia(u.get("completed")) for u in quem if _dia(u.get("completed"))), default=None)
            if not quando or quando < desde:
                continue
            txt = "%s concluída no ADVBOX por %s em %s" % (
                x.get("task") or "tarefa", ", ".join(str(u.get("name") or "").split(" ")[0].title() for u in quem) or "alguém",
                quando.strftime("%d/%m"))
            if fam and fx == fam:
                provas.insert(0, txt)
                nivel = "forte"
            elif fx == "PROTOCOLO" and fam in PECAS:
                provas.append(txt)
                if nivel == "fraco":
                    nivel = "medio"
        # sem prova, so desconfia das abertas por robo: tarefa criada a mao por alguem
        # e real, e atrasada continua atrasada. Desde 06/10/2026 o trabalho nao passa
        # mais pelo ADVBOX: tarefa nascida aqui a partir do corte nao tem como ter
        # prova la, entao "nada no ADVBOX" deixa de ser suspeita para ela.
        if nivel == "fraco" and (t["origem"] not in ("motor", "intimacao") or nasceu >= CORTE_ADVBOX):
            continue
        dias = (hoje - alvo).days
        motivo = {"forte": "Parece já feita: o ADVBOX tem a mesma peça concluída neste processo.",
                  "medio": "Parece já feita: houve protocolo no ADVBOX neste processo depois que a tarefa nasceu.",
                  "fraco": "Venceu há %d dias e ninguém mexeu nela aqui. Confira se ainda falta." % dias}[nivel]
        if t["origem"] == "motor":
            motivo += " (Tarefa aberta pelo motor da Controladoria, não pelo ADVBOX.)"
        ev = json.dumps(provas[:4], ensure_ascii=False)
        if ja:
            c.execute("UPDATE tarefa_conferir SET nivel=?, motivo=?, evidencia=? WHERE tarefa_id=?",
                      (nivel, motivo, ev, t["id"]))
            atualizadas += 1
        else:
            c.execute("INSERT INTO tarefa_conferir (tarefa_id, nivel, motivo, evidencia, status, detectado_em) "
                      "VALUES (?,?,?,?,'suspeita',?)", (t["id"], nivel, motivo, ev, time.time()))
            novas += 1
    # quem foi concluida/cancelada por outro caminho sai da lista
    c.execute("UPDATE tarefa_conferir SET status='resolvida_fora', resolvido_em=? WHERE status='suspeita' AND "
              "tarefa_id IN (SELECT id FROM tarefas WHERE status != 'aberta')", (time.time(),))
    c.commit()
    return novas, atualizadas


def suspeitas(c):
    """{tarefa_id: {nivel, motivo, evidencia[]}} das que estao esperando resposta."""
    try:
        garantir(c)
        return {r["tarefa_id"]: {"nivel": r["nivel"], "motivo": r["motivo"],
                                 "evidencia": json.loads(r["evidencia"] or "[]")}
                for r in c.execute("SELECT * FROM tarefa_conferir WHERE status='suspeita'")}
    except Exception:
        return {}


def resolver(c, tarefa_id, feita, por):
    """feita=True: conclui a parte de quem respondeu (o chamador faz isso) e tira da lista.
    feita=False: volta a contar como atrasada e o vigia nao pergunta mais."""
    garantir(c)
    c.execute("INSERT INTO tarefa_conferir (tarefa_id, status, resolvido_por, resolvido_em, detectado_em) "
              "VALUES (?,?,?,?,?) ON CONFLICT(tarefa_id) DO UPDATE SET status=excluded.status, "
              "resolvido_por=excluded.resolvido_por, resolvido_em=excluded.resolvido_em",
              (int(tarefa_id), "feita" if feita else "pendente_confirmada", por, time.time(), time.time()))
