# -*- coding: utf-8 -*-
"""O card anda sozinho quando a ligacao aconteceu.

O vendedor liga pelo WhatsApp de dentro do sistema, a ligacao fica gravada e transcrita na
conversa, e mesmo assim o card pode continuar numa coluna de ENTRADA do quadro Reclamantes.
Trabalhado de verdade, invisivel no quadro: o painel conta errado e o lead corre o risco de
ser abordado como se fosse novo.

Esta rotina olha o FATO, nao o julgamento: se existe ligacao registrada na conversa, o card
sai da coluna de entrada e vai para "Contatado".

Tres travas:
  1. so mexe em card que esta numa coluna de ENTRADA. Card que ja avancou fica onde esta,
     e nunca volta para tras;
  2. espera 30 minutos depois da ligacao, para dar a vez a quem quiser mover a mao;
  3. registra em wa_board_hist e deixa nota na conversa, para ninguem achar que o
     card andou sozinho sem motivo.

    python3 card_apos_ligacao.py             mostra o que faria
    python3 card_apos_ligacao.py --aplicar   move de verdade
"""
import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

DB = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
BOARD = 10                                  # quadro Reclamantes
ENTRADA = ('Caixa de Entrada', 'Campanha Fast-food', 'Campanha Supermercado',
           'Campanha Acidente de Trabalho', 'Campanha Geral')
PARA = 'Contatado'
ORIGEM = 'card_apos_ligacao'
FOLGA_MIN = 30          # tempo que a equipe tem para mover a mao antes de mim

# o que conta como "a ligacao aconteceu"
MARCAS = ('%TRANSCRICAO DA LIGACAO%', '%Gravacao da ligacao pelo WhatsApp%',
          '%de conversa, desligada%')


def achar(db):
    marcas = ' or '.join("m.conteudo like '%s'" % m for m in MARCAS)
    q = ("select v.id as id, coalesce(v.contato_nome,'') as nome, v.etapa as etapa, "
         "       max(m.criado_em) as quando, count(*) as quantas "
         "  from wa_conversas v join wa_mensagens m on m.conversa_id = v.id "
         " where v.pipeline_id = ? and v.etapa in (%s) and (%s) "
         " group by v.id, v.etapa order by v.id" % (','.join('?' * len(ENTRADA)), marcas))
    return db.execute(q, (BOARD,) + ENTRADA).fetchall()


def mover(db, cid, agora, de):
    db.execute('update wa_conversas set etapa=?, atualizado_em=? where id=?',
               (PARA, agora, cid))
    db.execute('insert into wa_board_hist(conversa_id, de_pipeline, de_etapa, '
               'para_pipeline, para_etapa, quem, origem, bloqueado, criado_em) '
               'values(?,?,?,?,?,?,?,0,?)',
               (cid, BOARD, de, BOARD, PARA, 'automacao', ORIGEM, agora))
    db.execute('insert into wa_mensagens(conversa_id, origem, autor, tipo, conteudo, '
               'midia_url, criado_em, remote_id) values(?,?,?,?,?,?,?,?)',
               (cid, 'nota', 'sistema', 'texto',
                'Card movido para "%s" porque a ligação já está registrada aqui na '
                'conversa. Se não for o caso, é só arrastar de volta.' % PARA,
                '', agora, ''))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--aplicar', action='store_true')
    ap.add_argument('--folga', type=int, default=FOLGA_MIN)
    a = ap.parse_args()

    db = sqlite3.connect(DB, timeout=30)
    db.execute('PRAGMA busy_timeout=30000')
    db.row_factory = sqlite3.Row
    corte = (datetime.now(timezone.utc) - timedelta(minutes=a.folga)).strftime('%Y-%m-%d %H:%M:%S')
    agora = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    linhas = achar(db)
    print('cards nas colunas de entrada com ligação registrada: %d' % len(linhas))
    movidos = esperando = 0
    for r in linhas:
        recente = (r['quando'] or '') > corte
        marca = 'aguarda a equipe' if recente else '-> %s' % PARA
        print('   #%-6s %-24s última ligação %s  %s'
              % (r['id'], (r['nome'] or 'sem nome')[:24], (r['quando'] or '')[:16], marca))
        if recente:
            esperando += 1
            continue
        if a.aplicar:
            mover(db, r['id'], agora, r['etapa'])
        movidos += 1
    if a.aplicar:
        db.commit()
    db.close()
    print('\n%d movidos%s, %d ainda na folga de %d min'
          % (movidos, '' if a.aplicar else ' (simulação, nada gravado)', esperando, a.folga))


if __name__ == '__main__':
    main()
