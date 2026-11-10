"""Acha a AUDIENCIA dentro do texto da intimacao — data, hora, tipo e modalidade.

Por que existe: audiencia nao e prazo. O motor de prazos calcula D-2 e joga na
agenda como evento de dia inteiro; audiencia tem hora marcada e a pessoa precisa
ESTAR la. Entao a tela oferece um link proprio ("incluir audiencia na agenda") que
cria o evento na hora certa, e nao no fatal interno.

Conservador de proposito: sem data explicita no texto, nao devolve nada. Chutar
horario de audiencia e pior que nao oferecer o link.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

_MESES = {'janeiro': 1, 'fevereiro': 2, 'marco': 3, 'abril': 4, 'maio': 5, 'junho': 6,
          'julho': 7, 'agosto': 8, 'setembro': 9, 'outubro': 10, 'novembro': 11, 'dezembro': 12}

# Modalidade muda o que a pessoa precisa fazer (deslocar-se ou abrir o link).
_MODALIDADES = [
    ('HIBRIDA', ('hibrida',)),
    ('PRESENCIAL', ('presencial', 'comparecer pessoalmente')),
    ('VIRTUAL', ('virtual', 'telepresencial', 'videoconferencia', 'por video')),
]
# Tipo SEMPRE colado na palavra audiencia. Procurar o rotulo solto no texto pega
# ruido: "sob pena de extincao do processo sem JULGAMENTO" virava audiencia de
# julgamento (erro real, visto no caso Aurélio Bastos).
_RE_TIPO = re.compile(
    r'audiencia\s+(?:de\s+)?(inicial|una|instrucao(?:\s+e\s+julgamento)?|'
    r'conciliacao|julgamento|mediacao)')
_ROTULO_TIPO = {'inicial': 'INICIAL', 'una': 'UNA', 'instrucao': 'INSTRUCAO',
                'instrucao e julgamento': 'INSTRUCAO', 'conciliacao': 'CONCILIACAO',
                'julgamento': 'JULGAMENTO', 'mediacao': 'MEDIACAO'}


def _norm(t):
    t = re.sub(r'<[^>]+>', ' ', str(t or ''))
    t = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', t).strip()


# "designada para 30/09/2026 as 13:50", "redesignada para o dia 05/11/2026, as 09h00"
_RE_DATA_NUM = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4})')
_RE_DATA_EXT = re.compile(r'(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})')
_RE_HORA = re.compile(r'(\d{1,2})\s*(?::|h)\s*(\d{2})')

# A janela em que se procura data/hora: a partir da palavra audiencia. Sem isso o
# regex pega a data da publicacao ou do despacho, que nao e a da audiencia.
_RE_ANCORA = re.compile(
    r'audiencia[^.;]{0,200}?(?:designad|redesignad|marcad|aprazad|realizar-se-a|ocorrera|sera realizada)[^.;]{0,200}'
    r'|(?:designad|redesignad|marcad)[^.;]{0,80}?audiencia[^.;]{0,200}')


def _data_em(trecho):
    m = _RE_DATA_NUM.search(trecho)
    if m:
        d, mes, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(a, mes, d)
        except ValueError:
            return None
    m = _RE_DATA_EXT.search(trecho)
    if m and m.group(2) in _MESES:
        try:
            return date(int(m.group(3)), _MESES[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
    return None


def detectar(intimacao):
    """{'data','hora','tipo','modalidade','trecho'} ou None se nao houver audiencia
    com data legivel no texto."""
    bruto = (intimacao.get('texto') or '') + ' \n ' + (intimacao.get('resumo') or '')
    t = _norm(bruto)
    if 'audiencia' not in t:
        return None

    trecho = None
    for m in _RE_ANCORA.finditer(t):
        if _data_em(m.group(0)):
            trecho = m.group(0)
            break
    if not trecho:
        # Ultima tentativa: uma unica data no texto inteiro E a palavra audiencia.
        datas = set(_RE_DATA_NUM.findall(t))
        if len(datas) != 1:
            return None
        trecho = t

    d = _data_em(trecho)
    if not d:
        return None

    # Janela em volta da data: e ali que estao a hora, o tipo e a modalidade DESTA
    # audiencia. Ler o texto inteiro traz palavra de outra frase.
    alvo = f'{d.day:02d}/{d.month:02d}/{d.year}'
    pos = t.find(alvo)
    if pos < 0:
        pos = t.find('audiencia')
    janela = t[max(0, pos - 260):pos + 260] if pos >= 0 else trecho

    hora = None
    mh = _RE_HORA.search(janela) or _RE_HORA.search(trecho)
    if mh:
        h, mi = int(mh.group(1)), int(mh.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            hora = f'{h:02d}:{mi:02d}'

    mt = _RE_TIPO.search(janela) or _RE_TIPO.search(t)
    tipo = _ROTULO_TIPO.get(mt.group(1)) if mt else None
    modalidade = next((nome for nome, chaves in _MODALIDADES
                       if any(c in janela for c in chaves)), None)
    return {'data': d.isoformat(), 'hora': hora, 'tipo': tipo, 'modalidade': modalidade,
            'trecho': (janela if pos >= 0 else trecho)[:220]}


def titulo(aud, cliente, parte_adversa, processo):
    partes = f"{cliente or '?'} X {parte_adversa or '?'}"
    rotulo = 'AUDIENCIA' + (f' {aud["tipo"]}' if aud.get('tipo') else '')
    if aud.get('modalidade'):
        rotulo += f' ({aud["modalidade"]})'
    return f'{rotulo}_{partes}_{processo or ""}'.strip('_')


if __name__ == '__main__':
    casos = [
        ('Trata-se de intimação para comparecer à audiência UNA PRESENCIAL/HÍBRIDA '
         'designada para 30/09/2026 às 13:50. A audiência será realizada na 14a Vara '
         'do Trabalho de Advogada/ES.', '2026-09-30', '13:50', 'UNA'),
        ('O despacho intima as partes e defere o pedido de remarcação da audiência. '
         'Determina a realização da audiência de instrução presencial para o dia '
         '05/11/2026, às 09h00.', '2026-11-05', '09:00', 'INSTRUCAO'),
        ('Fica designada audiência de conciliação para 3 de dezembro de 2026 as 14:30.',
         '2026-12-03', '14:30', 'CONCILIACAO'),
        ('Ela determina que o autor compareca a audiencia inicial por videoconferencia '
         'em 21/10/2026, as 08:50, sob pena de extincao do processo sem julgamento.',
         '2026-10-21', '08:50', 'INICIAL'),
        ('Intimacao do V. Acordao publicado em 11/08/2026. Prazo de 8 dias.', None, None, None),
        ('O ato certifica o decurso de prazo. Nada sobre audiencia aqui.', None, None, None),
    ]
    ok = 0
    for texto, ed, eh, et in casos:
        r = detectar({'texto': texto})
        bom = ((r or {}).get('data') == ed and (r or {}).get('hora') == eh
               and (r or {}).get('tipo') == et)
        ok += bom
        print('%s  %s' % ('OK  ' if bom else 'FALHA', texto[:62].replace('\n', ' ')))
        print('        -> %s (esperado data=%s hora=%s tipo=%s)' % (
            {k: r[k] for k in ('data', 'hora', 'tipo', 'modalidade')} if r else None, ed, eh, et))
    print('\n%d/%d' % (ok, len(casos)))
