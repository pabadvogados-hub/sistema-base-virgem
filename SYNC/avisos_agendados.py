#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Avisos agendados pela D-API (WhatsApp do escritorio): mensagem cadastrada para um dia e hora.

Fila em /opt/escritorio/config/avisos_agendados.json: lista de
    {"id": "...", "quando": "AAAA-MM-DD 09:00", "telefone": "55DDD...", "nome": "Fulana",
     "texto": "...", "enviado_em": null, "resultado": null}
O cron roda de 5 em 5 minutos (7h-20h, seg-sab); o que esta vencido e ainda nao foi enviado
sai pela D-API (SYNC/wa_dapi.py), com 20 s entre mensagens (teto de 3 por minuto do escritorio),
e e marcado com enviado_em e resultado. Nada e enviado duas vezes.

    python3 avisos_agendados.py --listar          # mostra a fila
    python3 avisos_agendados.py --simular         # diz o que enviaria agora, sem enviar
    python3 avisos_agendados.py --enviar          # envia o que esta vencido (e o que o cron chama)
    python3 avisos_agendados.py --cancelar ID     # tira um aviso da fila (fica marcado como cancelado)
"""
import datetime as dt
import json
import os
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
FILA = os.environ.get('ESC_AVISOS_FILA', '/opt/escritorio/config/avisos_agendados.json')
INTERVALO = 20      # segundos entre mensagens: no maximo 3 por minuto (para a Meta nao banir o numero)


def _agora():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)
    except Exception:
        return dt.datetime.now()


def ler():
    try:
        with open(FILA, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def gravar(fila):
    tmp = FILA + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(fila, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FILA)


def vencidos(fila, agora=None):
    agora = agora or _agora()
    out = []
    for a in fila:
        if a.get('enviado_em') or a.get('cancelado_em'):
            continue
        try:
            quando = dt.datetime.strptime(a['quando'], '%Y-%m-%d %H:%M')
        except (KeyError, ValueError):
            continue
        if quando <= agora:
            out.append(a)
    return sorted(out, key=lambda a: a['quando'])


def enviar(simular=False):
    fila = ler()
    devidos = vencidos(fila)
    if not devidos:
        print(_agora().strftime('%d/%m %H:%M'), 'nada vencido na fila (%d aviso(s))' % len(fila))
        return 0
    n = 0
    for a in devidos:
        if simular:
            print('ENVIARIA', a['id'], a['quando'], a['nome'], a['telefone'], repr(a['texto'][:60]))
            continue
        try:
            import wa_dapi
            ok, info = wa_dapi.enviar_texto(a['telefone'], a['texto'])
        except Exception as e:
            ok, info = False, str(e)[:200]
        a['tentativas'] = int(a.get('tentativas') or 0) + 1
        if ok:
            a['enviado_em'] = _agora().strftime('%Y-%m-%d %H:%M:%S')
            a['resultado'] = str(info)[:200]
            n += 1
            print(_agora().strftime('%d/%m %H:%M'), 'ENVIADO', a['id'], a['nome'], a['telefone'], str(info)[:80])
        else:
            a['resultado'] = 'falhou: ' + str(info)[:200]
            print(_agora().strftime('%d/%m %H:%M'), 'FALHOU', a['id'], a['nome'], str(info)[:120])
            if a['tentativas'] >= 6:          # meia hora tentando: desiste e deixa registrado
                a['cancelado_em'] = _agora().strftime('%Y-%m-%d %H:%M:%S')
                a['resultado'] = 'desistiu apos 6 tentativas: ' + str(info)[:160]
        gravar(fila)
        if len(devidos) > 1:
            time.sleep(INTERVALO)
    return n


def listar():
    fila = ler()
    if not fila:
        print('fila vazia (%s)' % FILA)
    for a in fila:
        estado = ('enviado ' + a['enviado_em']) if a.get('enviado_em') else ('cancelado ' + a['cancelado_em']) if a.get('cancelado_em') else 'AGENDADO'
        print('%-14s %-16s %-10s %-14s %s | %s' % (a.get('id'), a.get('quando'), a.get('nome'), a.get('telefone'), estado, repr((a.get('texto') or '')[:50])))


def cancelar(aid):
    fila = ler()
    for a in fila:
        if a.get('id') == aid and not a.get('enviado_em'):
            a['cancelado_em'] = _agora().strftime('%Y-%m-%d %H:%M:%S')
            gravar(fila)
            print('cancelado', aid)
            return 0
    print('nao achei na fila (ou ja foi enviado):', aid)
    return 1


if __name__ == '__main__':
    if '--listar' in sys.argv:
        listar()
    elif '--cancelar' in sys.argv:
        raise SystemExit(cancelar(sys.argv[sys.argv.index('--cancelar') + 1]))
    elif '--enviar' in sys.argv:
        enviar()
    elif '--simular' in sys.argv:
        enviar(simular=True)
    else:
        print(__doc__)
