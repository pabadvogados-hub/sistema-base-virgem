"""Vocabulario unico dos tipos de tarefa (fase 2, T1, 03/11/2026).

Antes havia tres vocabularios sem chave entre si: `tarefa_tipos.nome` (ADVBOX, com acento), os tipos do
catalogo do motor (catalogo_tarefas.json) e os nomes que o motor grava (sem acento, via `_norm`). Em
`tarefas.tipo` conviviam PETICAO DE MEIO e PETIÇÃO DE MEIO, REVISAO e REVISAO DE PEÇA.

Regra: o tipo gravado em tarefas.tipo e SEMPRE a forma normalizada (maiusculas, sem acento). Este modulo e
o unico lugar onde ficam
  * a funcao que normaliza (`norm_tipo`), usada pelo motor e pelo router;
  * os nomes dos tipos de etapa da cadeia (constantes), que o motor e o router importam;
  * o mapa tipo -> familia da etapa (producao, revisao, protocolo, ciencia), fundacao da decisao da Advogada de
    que, em cada etapa do fechamento, so aparecem os tipos da familia daquela etapa (secao 10, Ajuste 1;
    o filtro na tela e da fase 6);
  * a semente de `tarefa_tipos` (`semear`): so as constantes acima, nunca o catalogo.

Bateria: python -m app.tarefas_tipos
"""
import re
import unicodedata


def norm_tipo(s):
    """Tipo de tarefa sem acento, em maiusculas: 'PETIÇÃO DE MEIO' e 'PETICAO DE MEIO' viram uma so."""
    s = unicodedata.normalize('NFKD', str(s or ''))
    return ''.join(ch for ch in s if not unicodedata.combining(ch)).upper().strip()


# ---------------------------------------------------------------- tipos de etapa da cadeia
ANALISE = 'ANALISE'
PECA = 'PECA'
REVISAO = 'REVISAO'
AJUSTES = 'AJUSTES'
PROTOCOLO = 'PROTOCOLO'
PETICAO_DE_MEIO = 'PETICAO DE MEIO'
CIENCIA = 'CIENCIA'
ATUALIZAR_CLIENTE = 'ATUALIZAR CLIENTE'
ACOMPANHAMENTO = 'ACOMPANHAMENTO'
DILIGENCIA = 'DILIGENCIA'
EMITIR_GUIA = 'EMITIR GUIA'
ENVIAR_AO_CONTADOR = 'ENVIAR AO CONTADOR'
COMENTARIO = 'COMENTARIO'
REVISAO_DE_PECA = 'REVISAO DE PECA'          # nome da tarefa no ADVBOX; o motor chama a mesma coisa de REVISAO

TIPOS_CADEIA = (ANALISE, PECA, REVISAO, AJUSTES, PROTOCOLO, PETICAO_DE_MEIO, CIENCIA, ATUALIZAR_CLIENTE,
                ACOMPANHAMENTO, DILIGENCIA, EMITIR_GUIA, ENVIAR_AO_CONTADOR)
# os demais tipos que o motor grava (cada um so aparece em um ponto do motor); entram na semente
TIPOS_DO_MOTOR = ('COMUNICAR CLIENTE AUDIENCIA', 'ROTEIRO', 'ESTUDAR ROTEIRO', 'CALCULO DE HONORARIOS',
                  'LANCAMENTO DE RECEITA', 'RELATORIO DE CONSULTORIA', 'RECADO PARA PARCEIRO', 'EMITIR BOLETO',
                  'DECISAO DA REVISAO', 'DECISAO DA ESTRATEGIA', 'VALIDAR PROVIDENCIAS DA AUDIENCIA',
                  'INSCRICAO SUSTENTACAO ORAL', 'INICIAL EXECUCAO CUMPRIMENTO SENTENCA', COMENTARIO)


def eh_comentario(tipo):
    """COMENTARIO com ou sem acento: os dados antigos continuam com COMENTÁRIO."""
    return norm_tipo(tipo).startswith('COMENT')


# ---------------------------------------------------------------- familia da etapa
FAMILIAS = {'producao': 'Produção', 'revisao': 'Revisão', 'protocolo': 'Protocolo', 'ciencia': 'Ciência'}
# Decisao da Advogada (secao 10, Ajuste 1, 03/11/2026): AJUSTES fica em revisao; ATUALIZAR CLIENTE fica em ciencia,
# junto de CIENCIA e ACOMPANHAMENTO.
_FAMILIA_EXATA = {
    ANALISE: 'producao', PECA: 'producao', PETICAO_DE_MEIO: 'producao', DILIGENCIA: 'producao',
    EMITIR_GUIA: 'producao', ENVIAR_AO_CONTADOR: 'producao',
    REVISAO: 'revisao', AJUSTES: 'revisao', REVISAO_DE_PECA: 'revisao', 'DECISAO DA REVISAO': 'revisao',
    PROTOCOLO: 'protocolo',
    CIENCIA: 'ciencia', ACOMPANHAMENTO: 'ciencia', ATUALIZAR_CLIENTE: 'ciencia',
}
# tipos do ADVBOX que nao estao na lista acima, pela palavra que os define
_FAMILIA_POR_PALAVRA = (
    ('protocolo', re.compile(r'^PROTOCOLO|^CADASTRAMENTO DE PETICAO')),
    ('revisao', re.compile(r'^REVISA[OR]|^REANALISE DE PECA')),
    ('ciencia', re.compile(r'^CIENCIA|^ACOMPANH')),
    ('producao', re.compile(r'PETICAO|CONTESTACAO|REPLICA|RECURSO|EMBARGOS|AGRAVO|CONTRARRAZ|RAZOES FINAIS|'
                            r'MANIFESTACAO|IMPUGN|APELACAO|ELABORAR PECA')),
)


def familia_do_tipo(nome):
    """'producao', 'revisao', 'protocolo', 'ciencia', ou None quando o tipo nao e de etapa do fechamento."""
    n = norm_tipo(nome)
    if n in _FAMILIA_EXATA:
        return _FAMILIA_EXATA[n]
    for familia, rx in _FAMILIA_POR_PALAVRA:
        if rx.search(n):
            return familia
    return None


# ---------------------------------------------------------------- semente
def semear(c):
    """INSERT OR IGNORE em tarefa_tipos dos tipos de etapa da cadeia e dos tipos do motor, na forma normalizada.
    So as constantes deste modulo: o campo `advbox` do catalogo usa ' / ' como atalho de sufixo ("AUDIENCIA
    INICIAL / UNA / INSTRUCAO") e nao serve de lista de tipos. Um tipo que ja existe com outro acento
    (PETIÇÃO DE MEIO) nao ganha irmao sem acento: o /tipos junta os dois e a migracao normaliza o antigo.
    Idempotente. Nao faz commit. Devolve quantas linhas criou."""
    existentes = {norm_tipo(r[0]) for r in c.execute('SELECT nome FROM tarefa_tipos')}
    criadas = 0
    for nome, grupo in ([(n, 'Etapa da cadeia') for n in TIPOS_CADEIA] + [(n, 'Motor') for n in TIPOS_DO_MOTOR]):
        n = norm_tipo(nome)
        if n in existentes:
            continue
        cur = c.execute("INSERT OR IGNORE INTO tarefa_tipos (nome, grupo, ativo, origem, criado_em) "
                        "VALUES (?, ?, 1, 'sistema', datetime('now'))", (n, grupo))
        criadas += cur.rowcount or 0
        existentes.add(n)
    return criadas


if __name__ == '__main__':
    import sqlite3
    assert norm_tipo('PETIÇÃO DE MEIO') == norm_tipo('peticao de meio ') == PETICAO_DE_MEIO
    assert norm_tipo('REVISAO DE PEÇA') == REVISAO_DE_PECA and norm_tipo(None) == ''
    assert eh_comentario('COMENTÁRIO') and eh_comentario('COMENTARIO') and not eh_comentario('PROTOCOLO')
    assert familia_do_tipo('PETIÇÃO DE MEIO') == 'producao' and familia_do_tipo('REVISAO DE PEÇA') == 'revisao'
    assert familia_do_tipo('PROTOCOLO (7663169)') == 'protocolo' and familia_do_tipo('LIGAR PARA CLIENTE') is None
    b = sqlite3.connect(':memory:')
    b.execute('CREATE TABLE tarefa_tipos (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE, grupo TEXT, '
              'tmp INTEGER, advbox_id INTEGER, ativo INTEGER DEFAULT 1, origem TEXT DEFAULT \'advbox\', criado_em TEXT)')
    b.execute("INSERT INTO tarefa_tipos (nome) VALUES ('PETIÇÃO DE MEIO')")
    n1 = semear(b)
    assert n1 == len(set(TIPOS_CADEIA) | set(TIPOS_DO_MOTOR)) - 1 and semear(b) == 0   # PETICAO DE MEIO ja existia com acento
    assert b.execute("SELECT COUNT(*) FROM tarefa_tipos WHERE nome='PETICAO DE MEIO'").fetchone()[0] == 0
    print('tarefas_tipos: bateria ok (%d tipos semeados)' % n1)
