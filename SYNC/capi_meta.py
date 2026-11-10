# -*- coding: utf-8 -*-
"""Devolve ao Meta o que aconteceu com cada clique no anuncio.

O anuncio traz a conversa e o CRM sabe o resto: se virou lead qualificado, se o
trabalhador JA SAIU da empresa (ou e caso urgente) e se fechou contrato. Ate agora o Meta nao ficava
sabendo de nada disso, entao ele so sabia procurar "gente que puxa conversa".
Aqui a gente conta, pela Conversions API, o que cada clique virou. E assim que se
ensina o Meta a procurar DEMITIDO em vez de qualquer clique.

O que amarra o clique ao anuncio e o `ctwa_clid`, capturado no webhook
(ver app/whatsapp/ctwa.py). Nao mandamos telefone nem nome: so o codigo do clique.

Eventos:
  Lead           -> a SDR qualificou e o card saiu da entrada
  LeadDemitido   -> o trabalhador disse que ja saiu da empresa (o que a gente quer)
  Contrato       -> o card chegou na contratacao

Uso:
  python3 capi_meta.py            -> previa, nao manda nada
  python3 capi_meta.py --enviar   -> manda de verdade
"""
import calendar
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv('/opt/escritorio/config/.env')
sys.path.insert(0, '/opt/escritorio/SISTEMA/backend')
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BANCO = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
ESTADO = '/opt/escritorio/SYNC/capi_meta_state.json'
PIXEL = os.getenv('META_PIXEL_ID', '')
TOKEN = os.getenv('META_ADS_TOKEN', '')
API = 'https://graph.facebook.com/v21.0/%s/events' % PIXEL
ENVIAR = '--enviar' in sys.argv
JANELA_DIAS = 6          # a CAPI recusa evento com mais de 7 dias; deixo folga de 1

# quem ja saiu da empresa: as mesmas expressoes do fluxo "voce ja saiu?"
NEGOU = re.compile(r'(nao sai|ainda nao sai|nunca sai)')
SAIU = re.compile(r'(ja sai|sai de la|sai da empresa|sai dela|sai faz|sai tem|sai ha|sai em|sai no|sai na|'
                  r'sai dia|sai mes|sai sim|sim,? sai|^sai[ .!]*$|^sai ja|'
                  r'fui (demitido|mandado embora|dispensado|desligado)|me (mandaram embora|demitiram|dispensaram|'
                  r'desligaram)|pedi (demissao|as contas|pra sair|para sair)|nao trabalho mais|'
                  r'nao (to|estou) mais (la|nela|na empresa|nessa empresa|com eles)|desempregad|'
                  r'acabou (o contrato|meu contrato)|fechou a empresa|a empresa fechou)')
FICOU = re.compile(r'(ainda (to|estou|trabalho|continuo|sigo)|continuo (la|na)|to la ainda|sigo na mesma|'
                   r'mesma empresa ainda|ainda na empresa|ainda nela|ainda sim|nao sai|ainda nao sai)')
BOARD_LEADS, CAIXA = 10, 'Caixa de Entrada'
BOARDS_CONTRATO = ('Contratação', 'Produção', 'Clientes')


def sa(s):
    return ''.join(c for c in unicodedata.normalize('NFD', (s or '').lower())
                   if unicodedata.category(c) != 'Mn')


def ts(txt):
    """'2026-09-28 17:18:06' -> epoch. O sistema grava em UTC e a VPS roda em Brasilia,
    entao NAO da para usar .timestamp() (jogaria o evento 3h para o futuro e o Meta recusa)."""
    for f in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            return calendar.timegm(datetime.strptime(str(txt)[:26], f).timetuple())
        except Exception:
            pass
    return calendar.timegm(datetime.utcnow().timetuple())


def carregar():
    try:
        return json.load(open(ESTADO, encoding='utf-8'))
    except Exception:
        return {}


def guardar(d):
    json.dump(d, open(ESTADO, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def eventos_do_lead(c, cid):
    """O que este clique virou, com a hora de cada coisa. Devolve [(evento, epoch)]."""
    conv = c.execute('select etapa, pipeline_id, criado_em from wa_conversas where id=?', (cid,)).fetchone()
    if not conv:
        return []
    etapa, pipe, nascida = conv
    fora = []
    # 1. qualificado: o card saiu da Caixa de Entrada
    mv = c.execute("""select para_etapa, criado_em from wa_board_hist
                      where conversa_id=? and bloqueado=0 and para_etapa<>? order by id limit 1""",
                   (cid, CAIXA)).fetchone()
    if mv:
        fora.append(('Lead', ts(mv[1])))
    elif not (pipe == BOARD_LEADS and (etapa or '') == CAIXA):
        fora.append(('Lead', ts(nascida)))
    # 2. demitido: a mensagem em que ele conta que saiu
    for conteudo, quando in c.execute(
            "select conteudo, criado_em from wa_mensagens where conversa_id=? and origem='contato' order by id", (cid,)):
        t = ' '.join(sa(conteudo).split())
        if not t or NEGOU.search(t) or FICOU.search(t):
            continue
        if SAIU.search(t):
            fora.append(('LeadDemitido', ts(quando)))
            break
    # 3. contrato: o card chegou na esteira de contratacao
    nome_board = c.execute('select nome from wa_pipelines where id=?', (pipe,)).fetchone()
    if (nome_board and nome_board[0] in BOARDS_CONTRATO) or 'fechou' in sa(etapa):
        mvc = c.execute("""select criado_em from wa_board_hist where conversa_id=? and bloqueado=0
                           order by id desc limit 1""", (cid,)).fetchone()
        fora.append(('Contrato', ts(mvc[0]) if mvc else ts(nascida)))
    return fora


# O Meta so aceita alguns nomes de evento quando a fonte e business_messaging
# (Lead, Contact, Schedule e nome proprio sao recusados).
# Os que passam: LeadSubmitted, InitiateCheckout, AddToCart, ViewContent, Purchase.
# Entao o nosso funil vira:
MAPA = {'Lead': 'LeadSubmitted',          # a SDR qualificou
        'LeadDemitido': 'InitiateCheckout',  # JA SAIU da empresa: e este que a gente quer
        'Contrato': 'Purchase'}           # fechou
PAGINA = os.getenv('META_PAGE_ID', '')   # pagina que roda os anuncios
VALOR_CONTRATO = float(os.getenv('META_VALOR_CONTRATO', '3500'))


def manda(evento, quando, clid, valor=None):
    ev = {'event_name': MAPA.get(evento, evento), 'event_time': quando,
          'action_source': 'business_messaging', 'messaging_channel': 'whatsapp',
          'user_data': {'ctwa_clid': clid, 'page_id': PAGINA}}
    if evento == 'Contrato':
        ev['custom_data'] = {'value': valor or VALOR_CONTRATO, 'currency': 'BRL'}
    r = requests.post(API, json={'data': [ev], 'access_token': TOKEN}, timeout=45)
    return r.status_code, r.text[:300]


def main():
    if not TOKEN:
        print('sem META_ADS_TOKEN no .env'); return
    c = sqlite3.connect(BANCO)
    estado = carregar()
    limite = calendar.timegm((datetime.utcnow() - timedelta(days=JANELA_DIAS)).timetuple())
    novos = velhos = enviados = falhas = 0
    for cid, clid in c.execute("select conversa_id, ctwa_clid from wa_ctwa "
                              "where coalesce(ctwa_clid,'')<>'' order by conversa_id"):
        ja = estado.setdefault(clid, {})
        for evento, quando in eventos_do_lead(c, cid):
            if evento in ja:
                continue
            if quando < limite:
                velhos += 1
                ja[evento] = 'fora da janela'
                continue
            novos += 1
            if not ENVIAR:
                print('  [previa] conv %-5s %-13s %s (hora de Brasilia)' % (cid, evento, datetime.fromtimestamp(quando)))
                ja.pop(evento, None)
                continue
            valor = c.execute('select valor from wa_conversas where id=?', (cid,)).fetchone()
            cod, txt = manda(evento, quando, clid, (valor or [None])[0])
            if cod == 200:
                enviados += 1
                ja[evento] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                print('  enviado conv %-5s %-13s ok' % (cid, evento))
            else:
                falhas += 1
                print('  FALHOU  conv %-5s %-13s %s %s' % (cid, evento, cod, txt))
    if ENVIAR:
        guardar(estado)
    print('novos=%d enviados=%d falhas=%d fora_da_janela=%d%s'
          % (novos, enviados, falhas, velhos, '' if ENVIAR else '  (previa: rode com --enviar)'))


if __name__ == '__main__':
    main()
