# -*- coding: utf-8 -*-
"""TERMOMETRO DE LEADS COMO TAREFA no Sistema Escritório Modelo (pedido da Advogada em 02/10/2026).

O relatorio das 14h (relatorio_leads_aferido.py, que continua intacto e e quem le a trilha de cada lead
e reparte os leads entre as vendedoras) deixa de ir inteiro pelo WhatsApp. Agora:
  1. abre no modulo Tarefas do Sistema Escritório Modelo UMA tarefa por vendedora (lista USUARIOS abaixo), com os leads
     dela e os blocos comuns (em andamento, esfriou, mudar de coluna, resumo). O aviso de tarefa nova
     aparece na tela delas (bloco_aviso_tarefas.js);
  2. manda pelo WhatsApp so um aviso curto, para a Advogada e as duas, de que o termometro foi disparado.

Regras: so ACRESCENTA tarefa (uma por vendedora por dia; rodar de novo nao duplica), nao altera nem
cancela tarefa, nao move card. WhatsApp no teto de 3 mensagens por minuto (20s entre envios).

Uso:  python3 termometro_tarefa.py                      (simulacao: mostra as tarefas e o aviso)
      python3 termometro_tarefa.py --de-arquivo T.txt    (ensaio com um relatorio salvo, sem a IA)
      python3 termometro_tarefa.py --enviar              (abre as tarefas e manda o aviso)
"""
import os
import re
import sqlite3
import sys
import time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import relatorio_leads_aferido as RA  # noqa: E402

DB = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
ORIGEM = 'termometro-leads'
TIPO = 'ENTRAR EM CONTATO COM O LEAD'
USUARIOS = {}  # preencher: {'Nome': (id_usuario, 'Nome no sistema')}
INTERVALO = 20                         # segundos entre mensagens (teto de 3 por minuto)


def _limpo(t):
    """Tira a formatacao do WhatsApp (*negrito*, _italico_), que no sistema apareceria crua."""
    t = re.sub(r'\*([^*\n]+)\*', r'\1', t)
    return re.sub(r'(^|\s)_([^_\n]+)_(?=\s|$)', r'\1\2', t, flags=re.M)


def secoes(txt):
    """Separa o relatorio em: cabecalho, uma secao por vendedora e os blocos comuns."""
    cab, vend, comuns, atual, chave = [], {}, [], None, None
    for linha in txt.splitlines():
        m = re.match(r'👩 \*?([A-ZÇÃÉÍ]+)\*? \((\d+)\)', linha)
        if m:
            chave = next((v for v in USUARIOS if v.upper() == m.group(1)), m.group(1).title())
            vend[chave] = {'n': int(m.group(2)), 'linhas': [linha]}
            atual = vend[chave]['linhas']
            continue
        if linha.startswith(('💬', '🧊', '🗂️', '📊')):
            atual = comuns
        if atual is None:
            cab.append(linha)
        else:
            atual.append(linha)
    return cab, vend, comuns


def montar_tarefas(txt):
    hoje = date.today()
    cab, vend, comuns = secoes(txt)
    tarefas = []
    for nome, (uid, nome_sis) in USUARIOS.items():
        sec = vend.get(nome)
        n = sec['n'] if sec else 0
        corpo = cab + (sec['linhas'] if sec else [f'👩 {nome.upper()} (0)', 'Nenhum lead novo para você hoje.', '']) \
            + comuns + ['', 'Esta tarefa nasce todo dia às 14h com o termômetro de leads. Ligue nesta ordem, '
                            'atualize o card no CRM depois de cada ligação e conclua a tarefa quando terminar.']
        tarefas.append({'vend': nome, 'uid': uid, 'nome_sis': nome_sis, 'n': n,
                        'titulo': f'TERMÔMETRO DE LEADS {hoje:%d/%m} · {nome.upper()}: {n} lead(s) para trabalhar',
                        'texto': _limpo('\n'.join(corpo)).strip(),
                        'ref': f'termometro:{hoje.isoformat()}:{nome}'})
    return tarefas


def aviso(tarefas):
    partes = ' · '.join(f"{t['vend']}: {t['n']} lead(s)" for t in tarefas)
    return (f'🌡️ O termômetro de leads de hoje já foi disparado. As tarefas estão no Sistema Escritório Modelo, aba Tarefas '
            f'({partes}).')


def gravar(tarefas):
    con = sqlite3.connect(DB, timeout=20)
    con.execute('pragma busy_timeout=8000')
    hoje, abertas = date.today().isoformat(), []
    for t in tarefas:
        if con.execute('select id from tarefas where origem=? and origem_ref=?', (ORIGEM, t['ref'])).fetchone():
            print(f"tarefa de hoje de {t['vend']} ja existe; nada a fazer")
            continue
        agora = time.time()
        cur = con.execute(
            'INSERT INTO tarefas (tipo, titulo, descricao, data, prazo_interno, prioridade, status, '
            'origem, origem_ref, criado_por, criado_em, atualizado_em) '
            "VALUES (?,?,?,?,?,'normal','aberta',?,?,?,?,?)",
            (TIPO, t['titulo'], t['texto'], hoje, hoje, ORIGEM, t['ref'], 'Comercial (automação)', agora, agora))
        con.execute('INSERT INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) '
                    'VALUES (?,?,?,0,0)', (cur.lastrowid, t['uid'], t['nome_sis']))
        abertas.append((cur.lastrowid, t['vend']))
    con.commit()
    con.close()
    return abertas


if __name__ == '__main__':
    if '--de-arquivo' in sys.argv:
        txt = open(sys.argv[sys.argv.index('--de-arquivo') + 1], encoding='utf-8').read()
    else:
        txt = RA.texto(RA.montar())
    tarefas = montar_tarefas(txt)
    for t in tarefas:
        print('=' * 70)
        print(t['titulo'])
        print('-' * 70)
        print(t['texto'])
    msg = aviso(tarefas)
    print('=' * 70)
    print('AVISO NO WHATSAPP (equipe):', msg)
    if '--enviar' in sys.argv and '--de-arquivo' not in sys.argv:
        for tid, vend in gravar(tarefas):
            print(f'tarefa #{tid} aberta para {vend}')
        for i, destino in enumerate(RA.DESTINOS):
            if i:
                time.sleep(INTERVALO)
            print('aviso %s: %s' % (destino, 'OK' if RA.zapi(destino, msg) else 'FALHOU'))
    else:
        print('\n(simulacao: nada gravado nem enviado)')
