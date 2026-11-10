"""OPERADOR TECNICO DOS AGENTES (02/10/2026). Pedido do Titular: "quero que o Assistente e o Assistente TENHAM
PERMISSAO TOTAL AO BACKEND para realizar tudo que realizamos aqui" (aqui = o Claude Code dele).

O agente so registra o pedido aqui; quem executa e o vigia SYNC/operador_agente.py (cron de 1 em 1
minuto), que roda o Claude Code da ASSINATURA na VPS (sem credito de API) com Bash, Python, o banco,
o Drive, o ADVBOX e as integracoes. Ele roda como o usuario do sistema (www-data): mexe nos DADOS como
o sistema mexe, mas nao reinstala servidor, nao mexe nos robos agendados e nao troca codigo do root.
Antes de cada operacao o banco ganha uma copia (/var/backups/esc_operador). O resultado volta na
conversa do agente.
"""
import datetime as dt
import sqlite3
import time
from pathlib import Path

DB = str(Path(__file__).resolve().parents[1] / "sistema_esc.db")
PASTA = Path("/opt/escritorio/operador/trabalho")


def con():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    garantir(c)
    return c


def garantir(c):
    c.execute("""CREATE TABLE IF NOT EXISTS operacoes_agente (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER, agente TEXT, pedido TEXT,
                   status TEXT DEFAULT 'fila', resposta TEXT, backup TEXT, erro TEXT,
                   criado_em REAL, iniciado_em REAL, concluido_em REAL)""")


def pedir(uid, agente, pedido):
    c = con()
    try:
        # o modelo chamou duas vezes o mesmo pedido: reaproveita
        ja = c.execute("SELECT id FROM operacoes_agente WHERE usuario_id=? AND pedido=? AND status IN ('fila','rodando') "
                       "AND criado_em > ?", (uid, pedido, time.time() - 1800)).fetchone()
        if ja:
            return ja["id"], 0
        cur = c.execute("INSERT INTO operacoes_agente (usuario_id, agente, pedido, status, criado_em) VALUES (?,?,?,'fila',?)",
                        (uid, agente, pedido, time.time()))
        c.commit()
        frente = c.execute("SELECT COUNT(*) FROM operacoes_agente WHERE status IN ('fila','rodando') AND id < ?",
                           (cur.lastrowid,)).fetchone()[0]
        return cur.lastrowid, frente
    finally:
        c.close()


def pendentes(uid):
    try:
        c = con()
    except sqlite3.Error:
        return 0
    try:
        return c.execute("SELECT COUNT(*) FROM operacoes_agente WHERE usuario_id=? AND status IN ('fila','rodando') "
                         "AND criado_em > ?", (uid, time.time() - 2 * 3600)).fetchone()[0]
    finally:
        c.close()


def listar(uid, n=8):
    c = con()
    try:
        return [{"id": r["id"], "status": r["status"], "pedido": (r["pedido"] or "")[:300],
                 "resposta": (r["resposta"] or "")[:600], "erro": r["erro"],
                 "pedida_em": dt.datetime.fromtimestamp(r["criado_em"]).strftime("%d/%m %H:%M")}
                for r in c.execute("SELECT * FROM operacoes_agente WHERE usuario_id=? ORDER BY id DESC LIMIT ?", (uid, n))]
    finally:
        c.close()
