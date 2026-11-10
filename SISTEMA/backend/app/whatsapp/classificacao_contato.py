# -*- coding: utf-8 -*-
"""A classificacao passa a morar no CONTATO, nao na conversa.

Ate agora o board e a coluna ficavam guardados na CONVERSA. Quando a conversa era
apagada no celular, ou o WhatsApp abria uma nova para a mesma pessoa, o card nascia
do zero na Caixa de Entrada e a equipe tinha que classificar de novo alguem que ja
era conhecido. Foi o que aconteceu com os contatos que "sairam" do board pessoal do
Ton e apareceram no Reclamantes.

Agora toda classificacao feita por gente (e todo avanco de esteira aceito) fica
gravada pelo TELEFONE, em `wa_contato_classificacao`. Quando nasce uma conversa
nova, o webhook pergunta aqui se aquele telefone ja tinha lugar no CRM e ja coloca
o card la, com o dono de antes.

Chave: os 8 ULTIMOS digitos do telefone, o mesmo criterio que o CRM usa para achar
a conversa, porque o numero chega ora com 55, ora sem, ora com o 9 extra.

Excecao: se o ultimo lugar da pessoa era uma coluna de PERDA
(Perdido, Sem resposta, Sem interesse, Desqualificado), o card NAO nasce enterrado
la. Quem volta a escrever deixou de ser "sem resposta": o card nasce na Caixa de
Entrada com uma nota contando de onde ele veio, para a equipe reavaliar.
"""
from datetime import datetime

from sqlalchemy import text

CRIAR = """create table if not exists wa_contato_classificacao (
  tel8 text primary key, telefone text, pipeline_id integer, etapa text,
  board_fixado integer default 0, tag text, responsavel text, atendente_email text,
  quem text, origem text, atualizado_em text)"""

# colunas de perda: a classificacao fica guardada, mas o card novo nao nasce nelas
PERDA = ('perdido', 'sem interesse', 'sem resposta', 'desqualificado')


def tel8(telefone) -> str:
    """Os 8 ultimos digitos, que e o que identifica a pessoa em qualquer formato."""
    dig = ''.join(ch for ch in str(telefone or '') if ch.isdigit())
    return dig[-8:] if len(dig) >= 8 else ''


def eh_perda(etapa) -> bool:
    e = str(etapa or '').lower()
    return any(p in e for p in PERDA)


def pode_herdar(reg) -> bool:
    return bool(reg) and bool(reg.get('pipeline_id')) and not eh_perda(reg.get('etapa'))


async def garantir_tabela(db):
    await db.execute(text(CRIAR))


async def lembrar(db, conv, quem, origem='ui'):
    """Guarda no contato onde este card esta agora. Melhor esforco: nunca derruba o move."""
    k = tel8(getattr(conv, 'contato_telefone', ''))
    if not k or not getattr(conv, 'pipeline_id', None):
        return False
    await garantir_tabela(db)
    await db.execute(text(
        'insert into wa_contato_classificacao (tel8, telefone, pipeline_id, etapa, board_fixado, tag, '
        'responsavel, atendente_email, quem, origem, atualizado_em) '
        'values (:k, :t, :p, :e, :f, :g, :r, :a, :q, :o, :d) '
        'on conflict(tel8) do update set telefone=:t, pipeline_id=:p, etapa=:e, board_fixado=:f, '
        'tag=:g, responsavel=:r, atendente_email=:a, quem=:q, origem=:o, atualizado_em=:d'),
        {'k': k, 't': str(conv.contato_telefone or '')[:30], 'p': conv.pipeline_id,
         'e': conv.etapa or '', 'f': 1 if getattr(conv, 'board_fixado', 0) else 0,
         'g': getattr(conv, 'tag', '') or '', 'r': getattr(conv, 'responsavel', '') or '',
         'a': getattr(conv, 'atendente_email', '') or '', 'q': str(quem or '')[:120],
         'o': origem, 'd': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')})
    return True


async def recuperar(db, telefone):
    """O que o CRM ja sabe sobre esse telefone, ou None."""
    k = tel8(telefone)
    if not k:
        return None
    await garantir_tabela(db)
    r = (await db.execute(text(
        'select pipeline_id, etapa, board_fixado, tag, responsavel, atendente_email, quem, atualizado_em '
        'from wa_contato_classificacao where tel8=:k'), {'k': k})).first()
    if not r:
        return None
    return {'pipeline_id': r[0], 'etapa': r[1], 'board_fixado': r[2], 'tag': r[3],
            'responsavel': r[4], 'atendente_email': r[5], 'quem': r[6], 'atualizado_em': r[7]}


def nota_de_volta(reg, nome_board, herdou) -> str:
    """Texto da nota que fica no card novo, para a equipe entender o que aconteceu."""
    onde = '%s / %s' % (nome_board or 'board %s' % reg.get('pipeline_id'), reg.get('etapa') or '')
    quando = (reg.get('atualizado_em') or '')[:10]
    if herdou:
        return ('Conversa nova do mesmo contato. Ja era conhecido do CRM, entao o card nasceu '
                'direto em %s (classificacao de %s). Nao precisa classificar de novo.' % (onde, quando))
    return ('Conversa nova do mesmo contato. Ele ja esteve em %s (em %s) e voltou a escrever, '
            'por isso o card nasceu na Caixa de Entrada para a equipe reavaliar.' % (onde, quando))
