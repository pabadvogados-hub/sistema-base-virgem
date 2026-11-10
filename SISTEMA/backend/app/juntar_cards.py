# -*- coding: utf-8 -*-
"""Juntar dois cards do mesmo cliente, mantendo histórico e esteira.

07/11/2026: "pode juntar mantendo historico e esteira".

O QUE ESTE MÓDULO NÃO FAZ: apagar. Em 03/11/2026 três conversas foram perdidas por SQL direto,
e desde então a regra aqui é só acrescentar e marcar. O card que sai continua existindo, com o
histórico dele intacto e uma nota dizendo para onde foi; quem abrir por engano entende na hora.

COMO ESCOLHE QUEM FICA
Fica o card que tem a VIDA do cliente: mensagens dele, documentos que ele mandou e tarefas
abertas. Nos cinco casos medidos em 07/11 a diferença era gritante (176 mensagens contra 1),
mas a regra precisa valer também para o caso apertado, por isso é uma contagem e não um
palpite. Empate decide pelo card mais antigo, que é o que a esteira conhece.

O QUE SE MOVE
- **Tarefas**: sempre. É trabalho de gente esperando, e perder uma é perder um cliente.
  (No par do Dimas, o card pequeno tinha 2 tarefas.)
- **Mensagens que o principal não tem**: pelo par (hora, conteúdo). As primeiras mensagens são
  as mesmas nos dois cards, porque os dois webhooks gravaram a mesma coisa; copiar de novo
  criaria conversa repetida.
- **Nada mais.** Quadro, etapa, dono e produto do principal ficam como estão: a esteira dele
  está andando e não se mexe em esteira que anda.
"""
import sqlite3
from datetime import datetime, timezone


class NaoDaParaJuntar(ValueError):
    """Os dois cards não são o mesmo cliente, ou um deles não existe."""


def _agora():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def criar_esquema(con):
    """Registro de quem foi juntado com quem. Idempotente."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS wa_card_juntado (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          principal_id INTEGER NOT NULL,
          secundario_id INTEGER NOT NULL,
          telefone TEXT,
          tarefas_movidas INTEGER DEFAULT 0,
          mensagens_movidas INTEGER DEFAULT 0,
          quem TEXT NOT NULL,
          criado_em TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_card_juntado
          ON wa_card_juntado (principal_id, secundario_id);
    """)
    con.commit()


def _peso(con, cid):
    """Quanta vida de cliente este card tem."""
    msg_cliente = con.execute("select count(*) from wa_mensagens where conversa_id=? "
                              "and origem='contato'", (cid,)).fetchone()[0]
    docs = con.execute("select count(*) from wa_mensagens where conversa_id=? "
                       "and origem='contato' and tipo in ('imagem','doc','documento')",
                       (cid,)).fetchone()[0]
    try:
        tarefas = con.execute('select count(*) from tarefas where conversa_id=?',
                              (cid,)).fetchone()[0]
    except sqlite3.Error:
        tarefas = 0
    total = con.execute('select count(*) from wa_mensagens where conversa_id=?',
                        (cid,)).fetchone()[0]
    return {'msg_cliente': msg_cliente, 'docs': docs, 'tarefas': tarefas, 'total': total}


def escolher_principal(con, ids):
    """Qual card fica. Devolve (principal, [secundarios])."""
    if len(ids) < 2:
        raise NaoDaParaJuntar('preciso de pelo menos dois cards')
    pesos = {}
    for cid in ids:
        if not con.execute('select 1 from wa_conversas where id=?', (cid,)).fetchone():
            raise NaoDaParaJuntar('o card %r não existe' % (cid,))
        pesos[cid] = _peso(con, cid)
    # mais mensagens do cliente, depois documentos, depois tarefas, depois o mais antigo
    ordem = sorted(ids, key=lambda c: (-pesos[c]['msg_cliente'], -pesos[c]['docs'],
                                       -pesos[c]['tarefas'], -pesos[c]['total'], c))
    return ordem[0], ordem[1:]


def _mesmo_cliente(con, a, b):
    from . import busca_contato as bc
    ta = con.execute("select coalesce(contato_telefone,'') from wa_conversas where id=?",
                     (a,)).fetchone()[0]
    tb = con.execute("select coalesce(contato_telefone,'') from wa_conversas where id=?",
                     (b,)).fetchone()[0]
    va, vb = set(bc.variantes(ta)), set(bc.variantes(tb))
    return bool(va and vb and va & vb)


def juntar(con, principal, secundario, quem='sistema', executar=True):
    """Junta `secundario` em `principal`. Devolve o que foi feito.

    Levanta NaoDaParaJuntar se os dois não forem o mesmo telefone: juntar cliente errado é
    pior do que deixar dois cards.
    """
    if principal == secundario:
        raise NaoDaParaJuntar('o card não se junta com ele mesmo')
    if not _mesmo_cliente(con, principal, secundario):
        raise NaoDaParaJuntar('#%s e #%s não têm o mesmo telefone' % (principal, secundario))

    tarefas = [r[0] for r in con.execute('select id from tarefas where conversa_id=?',
                                         (secundario,))]
    # mensagens que o principal ainda não tem, comparadas por hora + conteúdo
    ja = {(str(r[0])[:19], (r[1] or '')[:120]) for r in con.execute(
        "select criado_em, coalesce(conteudo,'') from wa_mensagens where conversa_id=?",
        (principal,))}
    mover = [r[0] for r in con.execute(
        "select id, criado_em, coalesce(conteudo,'') from wa_mensagens where conversa_id=?",
        (secundario,)) if (str(r[1])[:19], (r[2] or '')[:120]) not in ja]

    plano = {'principal': principal, 'secundario': secundario,
             'tarefas': tarefas, 'mensagens': mover, 'executado': False}
    if not executar:
        return plano

    nome_sec = con.execute("select coalesce(contato_nome,'') from wa_conversas where id=?",
                           (secundario,)).fetchone()[0]
    tel = con.execute("select coalesce(contato_telefone,'') from wa_conversas where id=?",
                      (secundario,)).fetchone()[0]
    try:
        criar_esquema(con)
        if tarefas:
            con.execute('UPDATE tarefas SET conversa_id=? WHERE conversa_id=?',
                        (principal, secundario))
        if mover:
            con.executemany('UPDATE wa_mensagens SET conversa_id=? WHERE id=?',
                            [(principal, m) for m in mover])
        con.execute(
            "INSERT INTO wa_mensagens (conversa_id, origem, autor, tipo, conteudo, midia_url, "
            "criado_em) VALUES (?, 'nota', 'sistema', 'texto', ?, '', ?)",
            (principal,
             'Card #%s, que era do mesmo telefone, foi juntado neste. Vieram %d tarefa(s) e '
             '%d mensagem(ns) que faltavam aqui. Os dois nasceram juntos por causa de dois '
             'webhooks na mesma mensagem.' % (secundario, len(tarefas), len(mover)),
             _agora()))
        con.execute(
            "INSERT INTO wa_mensagens (conversa_id, origem, autor, tipo, conteudo, midia_url, "
            "criado_em) VALUES (?, 'nota', 'sistema', 'texto', ?, '', ?)",
            (secundario,
             'Este card era um duplicado e foi juntado no card #%s. O histórico e as tarefas '
             'do cliente estão lá. Nada foi apagado aqui.' % principal, _agora()))
        con.execute("UPDATE wa_conversas SET status='fechado', contato_nome=? WHERE id=?",
                    ('[DUPLICADO · ver #%s] %s' % (principal, nome_sec or ''), secundario))
        con.execute(
            'INSERT INTO wa_card_juntado (principal_id, secundario_id, telefone, '
            'tarefas_movidas, mensagens_movidas, quem, criado_em) VALUES (?,?,?,?,?,?,?)',
            (principal, secundario, tel, len(tarefas), len(mover), quem, _agora()))
        con.commit()
    except Exception:
        con.rollback()
        raise
    plano['executado'] = True
    return plano


def historico(con, cid):
    """As junções em que este card entrou, dos dois lados."""
    try:
        linhas = con.execute(
            'select id, principal_id, secundario_id, tarefas_movidas, mensagens_movidas, '
            'quem, criado_em from wa_card_juntado where principal_id=? or secundario_id=? '
            'order by id desc', (cid, cid)).fetchall()
    except sqlite3.Error:
        return []
    return [{'id': r[0], 'principal_id': r[1], 'secundario_id': r[2],
             'tarefas_movidas': r[3], 'mensagens_movidas': r[4], 'quem': r[5],
             'criado_em': r[6]} for r in linhas]
