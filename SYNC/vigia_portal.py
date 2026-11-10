#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""VIGIA do Portal do Cliente (16/09/2026). Roda a cada 5 min (cron).

Por que existe: em 11/09 as 11h37 o sistema reiniciou, o portal nao ligou e
ninguem ficou sabendo; o Romildo tocou 34 vezes em "pedir codigo" e recebeu erro
em todas. A sentinela geral olha a cada 30 min e nao olhava o portal.

O que faz:
  1. pergunta ao portal (GET /api/portal/me sem login). Saudavel = 401.
  2. se nao for 401, espera 20s e pergunta de novo (pode ser um deploy no meio);
  3. se continuar ruim, reinicia o esc-atendimento UMA vez (no maximo 1 a cada
     30 min, para nunca entrar em ciclo) e confere de novo;
  4. avisa no WhatsApp da Advogada (mesmo envio da sentinela) quando cai e quando volta.
Em dia bom fica calado. Nao manda nada a cliente.
USO: python vigia_portal.py [--dry]  (--dry: so diz o que faria)
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, '/opt/escritorio/SYNC')
import wa_dapi   # noqa: E402  saida unica de WhatsApp


def zapi_enviar(msg):
    """Avisa a equipe no WhatsApp de alerta (TEL_ALERTA_EQUIPE no .env; vazio = so loga)."""
    tel = os.getenv('TEL_ALERTA_EQUIPE', '').strip()
    if not tel:
        print('[vigia_portal] sem TEL_ALERTA_EQUIPE, aviso so no log:', msg[:200])
        return
    wa_dapi.enviar_texto(tel, msg)

URL = 'http://127.0.0.1:8000/api/portal/me'
ESTADO = '/opt/escritorio/SYNC/vigia_portal_state.json'
INTERVALO_REINICIO = 30 * 60
DRY = '--dry' in sys.argv


def perguntar():
    try:
        urllib.request.urlopen(URL, timeout=15)
        return 200
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def carimbo():
    return time.strftime('%d/%m %H:%M')


def main():
    try:
        st = json.load(open(ESTADO, encoding='utf-8'))
    except Exception:
        st = {}
    s = perguntar()
    if s != 401:
        time.sleep(20)
        s = perguntar()
    if s == 401:
        if st.get('fora_desde'):
            msg = (f"✅ Portal do Cliente voltou ao normal ({carimbo()}). "
                   f"Estava fora desde {st['fora_desde']}.")
            print(msg)
            if not DRY:
                zapi_enviar(msg)
                st.pop('fora_desde', None)
        print(f'[{carimbo()}] portal ok')
    else:
        desc = {0: 'nao respondeu', 404: 'rotas sumiram (404)', 503: 'em manutencao (503)'}.get(s, f'resposta {s}')
        primeira_vez = not st.get('fora_desde')
        if primeira_vez:
            st['fora_desde'] = carimbo()
        reiniciou = ''
        if time.time() - st.get('ultimo_reinicio', 0) >= INTERVALO_REINICIO:
            if DRY:
                reiniciou = ' [dry: reiniciaria o sistema]'
            else:
                subprocess.run(['systemctl', 'restart', 'esc-atendimento'], timeout=60)
                st['ultimo_reinicio'] = time.time()
                time.sleep(25)
                s2 = perguntar()
                reiniciou = (' Reiniciei o sistema e o portal VOLTOU.' if s2 == 401
                             else f' Reiniciei o sistema e o portal CONTINUA fora (resposta {s2}).')
                if s2 == 401:
                    st.pop('fora_desde', None)
        msg = f"⚠️ Portal do Cliente fora do ar ({carimbo()}): {desc}.{reiniciou}"
        print(msg)
        if not DRY and (primeira_vez or reiniciou):
            zapi_enviar(msg)
    if not DRY:
        json.dump(st, open(ESTADO, 'w', encoding='utf-8'))


if __name__ == '__main__':
    main()
