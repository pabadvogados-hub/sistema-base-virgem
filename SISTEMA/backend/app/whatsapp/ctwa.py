# -*- coding: utf-8 -*-
"""CTWA: de qual ANUNCIO veio o lead.

Ate agora o CRM so sabia a CAMPANHA pela frase pre-preenchida, e dois anuncios com a
mesma frase caiam no mesmo balde; a atribuicao por anuncio era feita cruzando a hora da
1a mensagem com o relatorio do Meta. O WhatsApp manda essa informacao no proprio evento
(no Z-API vem em `externalAdReply` com `sourceId` e `ctwaClid`), mas o numero do escritorio
roda na D-API desde 18/09 e os nomes dos campos dela ainda nao foram vistos aqui.

Entao este modulo faz as duas coisas ao mesmo tempo, sempre em melhor esforco (nunca
atrapalha o recebimento da mensagem):
  1. VARRE o evento inteiro procurando qualquer campo conhecido de anuncio/clique e grava
     o que achar na tabela `wa_ctwa`;
  2. Se o lead chegou pelo anuncio (frase pre-preenchida) e nada foi encontrado, guarda o
     evento CRU (ate 4 mil caracteres, no maximo 20 vezes) para eu ler os nomes reais dos
     campos da D-API e fechar a extracao.

O `ctwa_clid` e a chave que permite devolver ao Meta o evento "esse clique virou um lead
demitido" (Conversions API), que e o que ensina o Meta a procurar quem ja saiu da empresa.
"""
import json

from sqlalchemy import text

# nomes usados por Z-API, D-API, Cloud API oficial e variacoes em snake/camel case
CHAVES = {
    'ctwaclid': 'ctwa_clid', 'ctwa_clid': 'ctwa_clid', 'clickid': 'ctwa_clid', 'click_id': 'ctwa_clid',
    'sourceid': 'ad_id', 'source_id': 'ad_id', 'adid': 'ad_id', 'ad_id': 'ad_id',
    'sourcetype': 'source_type', 'source_type': 'source_type',
    'sourceurl': 'source_url', 'source_url': 'source_url',
    'conversionsource': 'source_type', 'conversion_source': 'source_type',
    'headline': 'headline', 'body': None,
    'entry_point_conversion_app': 'plataforma', 'entry_point_conversion_source': 'source_type',
    'entry_point_conversion_external_id': 'ad_id',
}
# a D-API manda os dados do anuncio em data.context_info (visto no evento cru de 28/09):
# entry_point_conversion_source='ctwa_ad', entry_point_conversion_app='instagram'
DENTRO = ('externaladreply', 'referral', 'ad', 'adreply', 'context', 'contextinfo',
          'conversionsource', 'entrypoint')
CHAVES_N = {k.replace('_', ''): v for k, v in CHAVES.items()}
CRIAR = """create table if not exists wa_ctwa (
  id integer primary key autoincrement, conversa_id integer, telefone text,
  ad_id text, ctwa_clid text, source_type text, source_url text, headline text,
  plataforma text, bruto text, criado_em text)"""


def _cavar(no, achado, dentro_de_anuncio=False, nivel=0):
    """Desce pelo evento inteiro. So aceita campo solto (sourceId, ctwaClid) quando ele esta
    dentro de um bloco de anuncio, para nao confundir com id de mensagem."""
    if nivel > 8:
        return
    if isinstance(no, dict):
        for k, v in no.items():
            kl = str(k).lower().replace('-', '').replace('_', '')
            aqui = dentro_de_anuncio or kl in DENTRO
            if isinstance(v, (dict, list)):
                _cavar(v, achado, aqui, nivel + 1)
            elif aqui and kl in CHAVES_N and CHAVES_N[kl] and v not in (None, '', {}):
                achado.setdefault(CHAVES_N[kl], str(v)[:300])
            elif kl == 'ctwaclid' and v:          # essa é inconfundível
                achado.setdefault('ctwa_clid', str(v)[:300])
    elif isinstance(no, list):
        for v in no[:20]:
            _cavar(v, achado, dentro_de_anuncio, nivel + 1)


def achar(payload) -> dict:
    achado = {}
    try:
        _cavar(payload, achado)
    except Exception:
        return {}
    return achado


async def guardar(db, conversa_id, telefone, achado, payload_bruto=None):
    """Grava o que achou; se nao achou nada e era lead de anuncio, guarda o evento cru."""
    from datetime import datetime
    await db.execute(text(CRIAR))
    if not achado and payload_bruto is not None:
        n = (await db.execute(text('select count(*) from wa_ctwa where bruto is not null'))).scalar() or 0
        if n >= 20:
            return                                    # ja tenho amostras suficientes
    if not achado and payload_bruto is None:
        return
    ja = (await db.execute(text('select id from wa_ctwa where conversa_id=:c and coalesce(ctwa_clid,"")<>"" limit 1'),
                           {'c': conversa_id})).first()
    if ja and achado.get('ctwa_clid'):
        return                                        # ja registrado para esta conversa
    await db.execute(text(
        'insert into wa_ctwa (conversa_id, telefone, ad_id, ctwa_clid, source_type, source_url, headline, '
        'plataforma, bruto, criado_em) values (:c, :t, :a, :cl, :st, :su, :h, :p, :b, :q)'),
        {'c': conversa_id, 't': str(telefone)[:30], 'a': achado.get('ad_id'), 'cl': achado.get('ctwa_clid'),
         'st': achado.get('source_type'), 'su': achado.get('source_url'), 'h': achado.get('headline'),
         'p': achado.get('plataforma'),
         'b': (json.dumps(payload_bruto, ensure_ascii=False)[:4000] if payload_bruto is not None else None),
         'q': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')})
