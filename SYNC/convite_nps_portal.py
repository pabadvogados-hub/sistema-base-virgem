# -*- coding: utf-8 -*-
"""Chama no WhatsApp quem usou o portal, para responder as tres perguntas.

Dois dias depois, quem TEVE ACESSO
responde se achou facil, se gostou e se faz sentido.

O convite e so a CHAMADA. As perguntas ficam dentro do portal (ver
app/portal/nps.py): tres toques, sem digitar, resposta ja identificada e
estruturada. Resposta solta no WhatsApp alguem teria que ler e tabular na mao.

QUEM RECEBE: entrou no portal ao menos uma vez, ha DIAS_ESPERA ou mais, ainda
nao respondeu nem dispensou, e ainda nao foi chamado. Quem nunca entrou NAO
recebe: perguntar "o que achou" para quem nao viu nada e constrangedor e nao
gera informacao.

Roda todo dia e fica calado quando nao ha ninguem na fila, entao nao precisa de
data marcada: cada cliente e chamado no seu proprio D+2.

Uso:
    convite_nps_portal.py            mostra quem receberia, nao envia
    convite_nps_portal.py --enviar
"""
import argparse
import importlib.util
import os
import sqlite3
import sys
import time

sys.path.insert(0, '/opt/escritorio/SISTEMA/backend')
from app.portal import dados, nps                       # noqa: E402

LINK = (os.getenv('PUBLIC_BASE_URL') or 'https://sistema.escritorio.adv.br').rstrip('/') + '/portal'
# segundos entre mensagens: TETO de 3 por minuto ("para a Meta nao banir o numero",
# vale para TODO disparo).
INTERVALO = 20
DB = os.getenv('SISTEMA_DB', '/opt/escritorio/SISTEMA/backend/sistema_esc.db')

_DIR = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('wa_dapi', os.path.join(_DIR, 'wa_dapi.py'))
wa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wa)

DDL = """CREATE TABLE IF NOT EXISTS portal_nps_convite (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cliente_id INTEGER, celular TEXT,
    enviado_em REAL, ok INTEGER)"""


def com_ddd(tel):
    t = dados.so_digitos(tel or '')
    return ('55' + t) if len(t) in (10, 11) else t


# Enquanto a campanha do portal novo nao sai, o convite diario fica
# PAUSADO (arquivo abaixo existe): senao o cliente recebe duas mensagens.
PAUSA = '/opt/escritorio/SYNC/nps_convite_pausado'


def montar(primeiro_nome):
    # comunicar que MELHORAMOS e que precisamos da opiniao
    return (f'Oi, {primeiro_nome}! Aqui é do Escritório Modelo Advogados.\n\n'
            'Melhoramos o Portal do Cliente e precisamos da sua opinião para '
            'deixar ele ainda melhor. Posso te pedir 20 segundos?\n\n'
            'São três perguntinhas rápidas, dentro do próprio portal, só de '
            'tocar: se foi fácil de usar, se você gostou e se ele te ajuda a '
            'acompanhar o seu processo.\n\n'
            f'É só entrar em {LINK} que as perguntas aparecem na tela.\n\n'
            'Obrigada!')


def fila():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute(DDL)
    con.execute(nps.DDL)
    con.commit()

    # por RODADA do NPS (ver nps.RODADA_DESDE): convite e resposta
    # da rodada antiga nao impedem de chamar para avaliar o portal novo
    ja_chamados = {r[0] for r in con.execute(
        'SELECT cliente_id FROM portal_nps_convite WHERE ok=1 AND enviado_em>=?',
        (nps.RODADA_DESDE,))}
    ja_responderam = {r[0] for r in con.execute(
        'SELECT DISTINCT cliente_id FROM portal_nps WHERE criado_em>=?',
        (nps.RODADA_DESDE,))}

    # SO quem recebeu o convite do portal de verdade. Sem esta trava, entram
    # logins que nao sao de cliente: teste nosso, e o cadastro da propria
    # Advogada, cujo telefone no jur_clientes e o numero que ENVIA os codigos.
    # Perguntar "o que voce achou?" para essa gente e ruido e constrangimento.
    convidados = {r[0] for r in con.execute(
        'SELECT DISTINCT cliente_id FROM portal_convite WHERE ok=1')}

    corte = time.time() - nps.DIAS_ESPERA * 86400
    linhas = [dict(r) for r in con.execute(
        'SELECT l.cliente_id AS cid, MIN(l.criado_em) AS primeiro, '
        '       j.nome AS nome, j.celular AS celular, j.telefone AS telefone '
        'FROM portal_log l JOIN jur_clientes j ON j.id = l.cliente_id '
        "WHERE l.acao='login_ok' AND l.cliente_id IS NOT NULL "
        'GROUP BY l.cliente_id')]
    con.close()

    saida = []
    for l in linhas:
        if l['cid'] not in convidados:
            continue
        if l['cid'] in ja_chamados or l['cid'] in ja_responderam:
            continue
        if l['primeiro'] > corte:
            continue                       # ainda nao completou os 2 dias
        tel = com_ddd(l['celular'] or l['telefone'])
        if len(tel) < 12:
            continue                       # sem telefone utilizavel
        saida.append({'cliente_id': l['cid'], 'nome': l['nome'], 'celular': tel,
                      'desde': l['primeiro']})
    return saida


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--enviar', action='store_true')
    a = ap.parse_args()

    if os.path.exists(PAUSA):
        print('convite do NPS PAUSADO (campanha do portal novo em preparo): %s' % PAUSA)
        return
    f = fila()
    print('na fila do NPS: %d' % len(f))
    for x in f:
        dias = (time.time() - x['desde']) / 86400
        print('  %-32s ***%s  usa ha %.0f dia(s)' % (
            x['nome'][:32], x['celular'][-4:], dias))
    if not f:
        print('(ninguem para chamar hoje)')
        return
    if not a.enviar:
        print('\nEXEMPLO DA MENSAGEM:\n' + '-' * 60)
        print(montar((f[0]['nome'] or '').split()[0].title()))
        print('-' * 60 + '\n(nada enviado. use --enviar)')
        return

    con = sqlite3.connect(DB)
    con.execute(DDL)
    for i, x in enumerate(f, 1):
        texto = montar((x['nome'] or '').split()[0].title())
        try:
            ok, info = wa.enviar_texto(x['celular'], texto)
        except Exception as e:
            ok, info = False, str(e)[:80]
        con.execute('INSERT INTO portal_nps_convite(cliente_id,celular,enviado_em,ok)'
                    ' VALUES(?,?,?,?)', (x['cliente_id'], x['celular'], time.time(),
                                         1 if ok else 0))
        con.commit()
        print('  %d/%d %-30s %s' % (i, len(f), x['nome'][:30],
                                    'ok' if ok else 'FALHOU: %s' % str(info)[:40]))
        if i < len(f):
            time.sleep(INTERVALO)
    con.close()


if __name__ == '__main__':
    main()
