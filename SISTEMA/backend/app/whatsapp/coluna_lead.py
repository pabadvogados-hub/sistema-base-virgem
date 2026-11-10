# -*- coding: utf-8 -*-
"""Em qual coluna a SDR larga o lead que ela acabou de qualificar.

Regra: qualificou e viu requisitos -> Lead Qualificado ou Lead Quente conforme a temperatura;
nao identificou do que se trata -> fica na entrada para gente olhar.

Traducao para as colunas do quadro de Reclamantes:
  quentissimo, quente  -> Lead Quente
  morno, frio          -> Lead Qualificado
  sem caso / sem resposta -> nao move (fica na Caixa de Entrada para gente olhar)

A temperatura vem do termometro (`lead_termometro`) quando ele ja passou por ali.
Lead novo ainda nao tem temperatura, entao quem decide e o proprio papo: quem JA
SAIU da empresa entra como Lead Quente (prazo correndo). Quem ainda esta na empresa entra como
Lead Qualificado, EXCETO quando o assunto e rescisao indireta ou acidente/doenca do trabalho:
ai a pessoa continua na empresa e e justamente o caso urgente, entao tambem vai para Lead Quente. As expressoes sao as mesmas do fluxo
"voce ja saiu da empresa?" (SYNC/fluxo_saiu_esc.py), copiadas de proposito: o
backend nao importa nada do SYNC.
"""
import re
import unicodedata

from sqlalchemy import text

QUENTE = 'Lead Quente'
QUALIFICADO = 'Lead Qualificado'

POR_TEMPERATURA = {'quentissimo': QUENTE, 'quente': QUENTE,
                   'morno': QUALIFICADO, 'frio': QUALIFICADO,
                   'sem_caso': None, 'sem_resposta': None}

NEGOU = re.compile(r'(nao sai|ainda nao sai|nunca sai)')
SAIU = re.compile(r'(ja sai|sai de la|sai da empresa|sai dela|sai faz|sai tem|sai ha|sai em|sai no|sai na|'
                  r'sai dia|sai mes|sai sim|sim,? sai|^sai[ .!]*$|^sai ja|'
                  r'fui (demitido|mandado embora|dispensado|desligado)|me (mandaram embora|demitiram|dispensaram|'
                  r'desligaram)|pedi (demissao|as contas|pra sair|para sair)|nao trabalho mais|'
                  r'nao (to|estou) mais (la|nela|na empresa|nessa empresa|com eles)|desempregad|'
                  r'acabou (o contrato|meu contrato)|fechou a empresa|a empresa fechou)')
FICOU = re.compile(r'(ainda (to|estou|trabalho|continuo|sigo)|continuo (la|na)|to la ainda|sigo na mesma|'
                   r'mesma empresa ainda|ainda na empresa|ainda nela|ainda sim|nao sai|ainda nao sai)')


def sa(s) -> str:
    """minuscula e sem acento, igual ao fluxo do 'ja saiu?'."""
    return ''.join(c for c in unicodedata.normalize('NFD', (s or '').lower())
                   if unicodedata.category(c) != 'Mn')


def saiu_no_papo(historico) -> bool:
    """Alguma fala do TRABALHADOR diz que ele ja saiu da empresa."""
    for m in (historico or []):
        if (m.get('role') or '') != 'user':
            continue
        t = ' '.join(sa(m.get('content') or '').split())
        if not t or NEGOU.search(t) or FICOU.search(t):
            continue
        if SAIU.search(t):
            return True
    return False


URGENTE_NA_EMPRESA = re.compile(r'(rescisao indireta|salario atrasad|atraso de salario|fgts nao|nao deposit|'
                                r'assedio|humilha|acidente|me machuquei|afastad|cat\b|doenca do trabalho|'
                                r'lesao|cirurgia|inss)')


def caso_urgente_no_papo(historico) -> bool:
    """Rescisao indireta ou acidente/doenca do trabalho: urgente mesmo com a pessoa ainda na empresa."""
    for m in (historico or []):
        if (m.get('role') or '') == 'user' and URGENTE_NA_EMPRESA.search(' '.join(sa(m.get('content') or '').split())):
            return True
    return False


async def escolher(db, conv, historico=None):
    """Coluna do quadro de Reclamantes para este lead, ou None quando e melhor deixar na entrada."""
    try:
        r = (await db.execute(text('select temperatura from lead_termometro where conversa_id=:c'),
                              {'c': conv.id})).first()
    except Exception:
        r = None
    if r and r[0]:
        temp = str(r[0]).strip().lower()
        if temp in POR_TEMPERATURA:
            return POR_TEMPERATURA[temp]
    return QUENTE if (saiu_no_papo(historico) or caso_urgente_no_papo(historico)) else QUALIFICADO
