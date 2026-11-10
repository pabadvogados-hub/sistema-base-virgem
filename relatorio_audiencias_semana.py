#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Relatorio de sexta para a controladoria: as audiencias da semana seguinte.

Pedido da Dra. Advogada em 14/09/2026, depois de duas faltas de reclamante em
uma semana (Severino em 08/09, Ataíde em 14/09). Toda sexta a Advogada recebe no
WhatsApp a rotina de audiencias da proxima semana, com o que falta em cada uma.

De onde vem: `audiencia_registro`, que o audiencias_vigia.py atualiza as 6h50
juntando ADVBOX, intimacoes e agendas. Os alertas de cada audiencia sao os
mesmos que vao na tarefa da Dra. Advogada (checklist do vigia).

SEMANA VAZIA TAMBEM MANDA. Relatorio que some quando nao ha audiencia fica
igual ao robo de lembretes que ficou mudo 11 dias: silencio parece "esta tudo
certo". A mensagem vazia diz o que fazer se a Advogada souber de uma audiencia
que nao aparece.

Uso:
    python3 relatorio_audiencias_semana.py                     # mostra
    python3 relatorio_audiencias_semana.py --semana 2026-09-28 # outra semana
    python3 relatorio_audiencias_semana.py --enviar            # manda
"""
import datetime as dt
import importlib.util
import sqlite3
import os
import sys
import time
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('vigia', '/opt/escritorio/audiencias_vigia.py')
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

from app.whatsapp import zapi  # noqa: E402  (o vigia ja pos o backend no path)

ADVOGADA = os.getenv('TEL_REVISORA', '')   # WhatsApp da Dra. Advogada (vazio = so imprime)
ENVIAR = '--enviar' in sys.argv
DIA = ['SEG', 'TER', 'QUA', 'QUI', 'SEX', 'SÁB', 'DOM']
SUBTIPO = {'inicial': 'inicial', 'una': 'UNA', 'instrucao': 'de instrução'}
MODAL = {'virtual': 'virtual', 'presencial': 'presencial', 'hibrida': 'híbrida'}
ALERTA = {
    'peça pronta sem protocolo': 'peça pronta e ainda não protocolada',
    'sem roteiro': 'roteiro não está na pasta do caso',
    'cliente sem WhatsApp': 'telefone do cliente não existe no WhatsApp',
    'sem cliente no cadastro': 'processo sem cliente no cadastro',
    'sem registro de testemunha': 'nenhum registro de testemunha na linha do tempo do processo',
    'pedido de modalidade sem despacho': 'pedido de audiência virtual ou híbrida SEM despacho',
    'histórico não lido': 'ADVBOX não respondeu; conferir o card direto',
}


def semana_alvo():
    if '--semana' in sys.argv:
        seg = dt.date.fromisoformat(sys.argv[sys.argv.index('--semana') + 1])
        return seg - dt.timedelta(days=seg.weekday())
    hoje = dt.date.today()
    return hoje + dt.timedelta(days=7 - hoje.weekday())


def main():
    seg = semana_alvo()
    dom = seg + dt.timedelta(days=6)
    con = V._con()
    con.execute("""CREATE TABLE IF NOT EXISTS relatorio_audiencias_envio (
                     semana TEXT PRIMARY KEY, enviado_em TEXT, audiencias INTEGER)""")
    if ENVIAR and con.execute('SELECT 1 FROM relatorio_audiencias_envio WHERE semana=?',
                              (seg.isoformat(),)).fetchone():
        print('relatorio da semana %s ja enviado' % seg.isoformat())
        return 0

    auds = [dict(r) for r in con.execute(
        "SELECT * FROM audiencia_registro WHERE status='ativa' AND data BETWEEN ? AND ? "
        "ORDER BY data, hora", (seg.isoformat(), dom.isoformat()))]
    providencias = {(r['processo'], r['data']): r['criado_em'] for r in
                    con.execute('SELECT processo, data, criado_em FROM audiencia_providencia')}

    periodo = '%s a %s' % (seg.strftime('%d/%m'), dom.strftime('%d/%m'))
    L = ['*Audiências da próxima semana* (%s)' % periodo, '']

    if not auds:
        L += ['Advogada, não há nenhuma audiência registrada para essa semana.', '',
              'Se você souber de alguma que não está aqui, lance hoje no Sistema Escritório Modelo, em '
              'Agenda > Audiências (botão Nova audiência). Sem esse cadastro o cliente '
              'não recebe lembrete.', '']
    else:
        L += ['Advogada, segue a rotina de audiências para a controladoria conferir. '
              'Cada uma traz o que o sistema encontrou pendente.', '']
        for a in auds:
            d = dt.date.fromisoformat(a['data'])
            cab = '*%s %s' % (DIA[d.weekday()], d.strftime('%d/%m'))
            if a['hora']:
                cab += ' · %s' % a['hora']
            cab += ' · %s' % ('audiência ' + SUBTIPO.get(a['subtipo'], '')).strip()
            if a['modalidade']:
                cab += ' %s' % MODAL.get(a['modalidade'], a['modalidade'])
            L.append(cab + '*')
            nome = (a['cliente'] or 'cliente não identificado').title()
            L.append('%s x %s' % (nome, (a['adversa'] or '').title()) if a['adversa'] else nome)
            L.append(a['processo'])

            _, alertas = V.checklist(con, a)
            cli = V.cliente_do_processo(con, a['processo'])
            marcos = [r[0] for r in con.execute(
                'SELECT marco FROM agenda_lembretes WHERE processo=? ORDER BY enviado_em',
                (a['processo'],))]
            if cli.get('parceiro'):
                L.append('Cliente do parceiro: quem avisa é %s.' % cli['parceiro'])
            else:
                L.append('Lembretes ao cliente: %s' % (', '.join(marcos) or 'nenhum enviado ainda'))
            L.append('Agenda central: %s' % ('ok' if a.get('evento_id')
                                             else 'FORA da agenda (conferir em Agenda > Audiências)'))
            aberta = providencias.get((a['processo'], a['data']))
            L.append('Providência da Dra. Advogada: %s' % (
                'aberta em %s' % V.br(aberta) if aberta
                else 'abre em %s' % (d - dt.timedelta(days=7)).strftime('%d/%m')))
            pend = [ALERTA.get(x, x) for x in alertas if x != 'datas divergentes']
            if a['divergencia']:
                pend.insert(0, a['divergencia'].rstrip('.'))
            if pend:
                L.append('Atenção: ' + '; '.join(pend) + '.')
            L.append('')

    try:
        _, _, sem_data = V.de_advbox()
    except Exception as e:
        sem_data = []
        L.append('(Nao consegui conferir os cards de audiencia sem data no ADVBOX agora: '
                 '%s. O resto do relatorio esta completo.)' % str(e)[:120])
        L.append('')
    if sem_data:
        L.append('*Cards de audiência sem data no ADVBOX* (corrigir o card, ou lançar a data em '
                 'Agenda > Audiências no sistema):')
        L += ['%s%s' % (num, ' · ' + nota if nota else '') for num, nota in sem_data]
        L.append('')
    L.append('Controladoria Escritório Modelo Advogados')
    texto = '\n'.join(L).strip()

    print(texto)
    if not ENVIAR:
        print('\n(simulacao) use --enviar para mandar a Advogada')
        return 0
    r = con.execute('SELECT zapi_instance, zapi_token, zapi_client_token '
                    'FROM wa_numeros WHERE id=1').fetchone()
    ok, info = zapi.enviar_texto(SimpleNamespace(zapi_instance=r[0], zapi_token=r[1],
                                                 zapi_client_token=r[2]), ADVOGADA, texto)
    if not ok:
        print('FALHOU: %s' % str(info)[:160])
        return 1
    con.execute('INSERT OR REPLACE INTO relatorio_audiencias_envio VALUES (?,?,?)',
                (seg.isoformat(), time.strftime('%Y-%m-%d %H:%M:%S'), len(auds)))
    con.commit()
    print('\nenviado para a Advogada (%s)' % info)
    return 0


if __name__ == '__main__':
    sys.exit(main())
