"""Agentes da equipe: um assistente de IA por pessoa, com nome proprio.

Nascem dois agentes padrao (Dr. Titular e Dra. Advogada, ids de juridico/equipe.json) com o nome
"Assistente"; o admin batiza e ajusta papel/foco pela tela (PUT /api/agentes/{id}). Quem ainda nao
tem agente fica com o generico.

Cada agente conhece o escritorio pelo "cerebro" (cerebro.db: teses, POPs, regras, sistemas) e os
dados do sistema que a pessoa ja ve (as tarefas dela, os processos, a linha do tempo, os leads
dela). So a pessoa conversa com o proprio agente; o admin ve as conversas e deixa ORIENTACOES, que
o agente passa a seguir.
"""
import sqlite3
import time
from pathlib import Path

DB = str(Path(__file__).resolve().parents[1] / 'sistema_esc.db')

from .juridico import equipe as _EQ

PADRAO = {
    _EQ.pessoa_do_papel('titular').get('id', 1): dict(
        nome='Assistente', genero='o', cor='petroleo', simbolo='estrela',
        papel='Dr. Titular, titular do escritório: empresarial, assessoria corporativa e defesa do empregador',
        foco='gestão do escritório, metas e comercial, carteira empresarial, contestações na defesa do empregador, '
             'tráfego e funil de captação'),
    _EQ.pessoa_do_papel('revisora').get('id', 2): dict(
        nome='Assistente', genero='o', cor='azul', simbolo='estrela',
        papel='Dra. Advogada (OAB/UF 000.000): contencioso trabalhista do reclamante, revisora e dona das tarefas',
        foco='revisão de peças pelo DNA do escritório, prazos e publicações, audiências, tarefas da equipe, '
             'teses do reclamante no TRT-2'),
}
GENERICO = dict(nome='Assistente', genero='o', cor='petroleo', simbolo='estrela', papel='', foco='rotina e dúvidas do escritório')


def con(db=None):
    c = sqlite3.connect(db or DB, timeout=30)
    c.row_factory = sqlite3.Row
    garantir(c)
    return c


def garantir(c):
    c.execute("""CREATE TABLE IF NOT EXISTS agentes (
                   usuario_id INTEGER PRIMARY KEY, nome TEXT, genero TEXT, cor TEXT, simbolo TEXT,
                   papel TEXT, foco TEXT, ativo INTEGER DEFAULT 1, atualizado_por TEXT, atualizado_em REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS agente_mensagens (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL, conversa INTEGER DEFAULT 1,
                   papel TEXT NOT NULL, texto TEXT, fontes TEXT, autor_id INTEGER, criado_em REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_agmsg ON agente_mensagens (usuario_id, conversa, id)")
    c.execute("""CREATE TABLE IF NOT EXISTS agente_orientacoes (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL, texto TEXT,
                   por TEXT, criado_em REAL, ativo INTEGER DEFAULT 1)""")
    # 29/09/2026 (tarde): o agente passa a AGIR (tarefas e agenda) a pedido da pessoa.
    # Tudo o que ele faz fica aqui: quem pediu, o que mudou, antes e depois.
    c.execute("""CREATE TABLE IF NOT EXISTS agente_acoes (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL, agente TEXT,
                   ferramenta TEXT, alvo TEXT, resumo TEXT, detalhe TEXT, criado_em REAL)""")
    for tabela, coluna, tipo in (("agentes", "voz_id", "TEXT"), ("agente_mensagens", "acoes", "TEXT"),
                                 ("agente_mensagens", "via", "TEXT")):
        cols = [r[1] for r in c.execute("PRAGMA table_info(%s)" % tabela)]
        if coluna not in cols:
            try:
                c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tabela, coluna, tipo))
            except sqlite3.OperationalError as e:      # duas telas abrindo juntas: a outra ja criou
                if "duplicate column" not in str(e):
                    raise
    if not c.execute("SELECT COUNT(*) FROM agentes").fetchone()[0]:
        for uid, a in PADRAO.items():
            c.execute("INSERT OR IGNORE INTO agentes (usuario_id, nome, genero, cor, simbolo, papel, foco, atualizado_por, "
                      "atualizado_em) VALUES (?,?,?,?,?,?,?,?,?)",
                      (uid, a['nome'], a['genero'], a['cor'], a['simbolo'], a['papel'], a['foco'], 'padrão', time.time()))
        c.commit()


def agente_de(c, uid):
    """Config do agente da pessoa (o generico se ela ainda nao tem um batizado)."""
    r = c.execute("SELECT * FROM agentes WHERE usuario_id=? AND ativo=1", (uid,)).fetchone()
    if r:
        return dict(r)
    u = c.execute("SELECT nome FROM usuarios WHERE id=?", (uid,)).fetchone()
    return dict(GENERICO, usuario_id=uid, papel=(u['nome'] if u else ''), padrao=True)


def apresentacao(a):
    """'o Assistente', 'o Assistente'."""
    return ('a ' if a.get('genero') == 'a' else 'o ') + (a.get('nome') or 'Assistente')
