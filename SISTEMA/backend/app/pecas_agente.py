"""PECAS ESCRITAS A PEDIDO DO AGENTE (29/09/2026, pedido do Titular: "ela pode escrever pecas
tambem"). O agente so registra o pedido aqui; quem escreve e o vigia SYNC/pecas_agente.py (root,
a cada 2 min), pelo Claude Code da assinatura (nao gasta credito da API), no Opus, com a skill
timbrado (o DOCX sai no papel timbrado). A peca vai para a ATOS INTERNOS do cliente no Drive
(quando a pasta e conhecida) e fica guardada no sistema; se pedido, passa pela Revisora IA em seguida.
"""
import datetime as dt
import sqlite3
import time
from pathlib import Path

DB = str(Path(__file__).resolve().parents[1] / "sistema_esc.db")
PASTA = Path("/var/lib/esc_pecas")


def con():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    garantir(c)
    return c


def garantir(c):
    c.execute("""CREATE TABLE IF NOT EXISTS pecas_agente (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER, agente TEXT, tipo TEXT, instrucoes TEXT,
                   tarefa_id INTEGER, processo TEXT, cliente TEXT, pasta_drive TEXT, revisar INTEGER DEFAULT 1,
                   status TEXT DEFAULT 'fila', arquivo TEXT, drive_link TEXT, resumo TEXT, revisao_id INTEGER,
                   erro TEXT, criado_em REAL, iniciado_em REAL, concluido_em REAL)""")


def pedir(uid, agente, tipo, instrucoes, tarefa_id=None, processo=None, cliente=None, pasta_drive=None, revisar=True):
    c = con()
    try:
        # o mesmo pedido de novo em menos de 1 hora (o modelo chamou duas vezes): reaproveita, nao duplica
        ja = c.execute("SELECT id FROM pecas_agente WHERE usuario_id=? AND LOWER(tipo)=LOWER(?) AND "
                       "COALESCE(tarefa_id,0)=COALESCE(?,0) AND COALESCE(processo,'')=COALESCE(?,'') AND "
                       "status IN ('fila','escrevendo') AND criado_em > ?",
                       (uid, tipo, tarefa_id, processo, time.time() - 3600)).fetchone()
        if ja:
            return ja["id"], 0
        cur = c.execute("INSERT INTO pecas_agente (usuario_id, agente, tipo, instrucoes, tarefa_id, processo, cliente, "
                        "pasta_drive, revisar, status, criado_em) VALUES (?,?,?,?,?,?,?,?,?,'fila',?)",
                        (uid, agente, tipo, instrucoes, tarefa_id, processo, cliente, pasta_drive, int(bool(revisar)),
                         time.time()))
        c.commit()
        na_frente = c.execute("SELECT COUNT(*) FROM pecas_agente WHERE status IN ('fila','escrevendo') AND id < ?",
                              (cur.lastrowid,)).fetchone()[0]
        return cur.lastrowid, na_frente
    finally:
        c.close()


def pendentes(uid):
    c = con()
    try:
        # pedido parado ha mais de 3 horas (vigia caiu no meio): nao conta mais como "trabalhando"
        return c.execute("SELECT COUNT(*) FROM pecas_agente WHERE usuario_id=? AND status IN ('fila','escrevendo') "
                         "AND criado_em > ?", (uid, time.time() - 3 * 3600)).fetchone()[0]
    finally:
        c.close()


def listar(uid, n=8):
    c = con()
    try:
        return [{"id": r["id"], "tipo": r["tipo"], "status": r["status"], "cliente": r["cliente"], "processo": r["processo"],
                 "drive": r["drive_link"],
                 "resumo": (r["resumo"] or "")[:400], "erro": r["erro"], "revisao_id": r["revisao_id"],
                 "pedida_em": dt.datetime.fromtimestamp(r["criado_em"]).strftime("%d/%m %H:%M")}
                for r in c.execute("SELECT * FROM pecas_agente WHERE usuario_id=? ORDER BY id DESC LIMIT ?", (uid, n))]
    finally:
        c.close()


def arquivo(pid, uid, admin=False):
    c = con()
    try:
        r = c.execute("SELECT * FROM pecas_agente WHERE id=?", (int(pid),)).fetchone()
        if not r or (r["usuario_id"] != uid and not admin) or not r["arquivo"]:
            return None
        p = Path(r["arquivo"])
        return p if p.exists() and PASTA in p.parents else None
    finally:
        c.close()
