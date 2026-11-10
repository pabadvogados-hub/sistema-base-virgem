# -*- coding: utf-8 -*-
"""Sugestao de conclusao: o que o motor pode SUGERIR que seja concluido.

REGRA da Dra. Advogada (01/10/2026): "Voce nao vai tratar nada sozinha. Voce vai
sugerir que a tarefa seja concluida por alguem humano da equipe." Entao este
modulo nunca fecha nada: `decidir()` diz se cabe sugerir a conclusao e por que,
`sugestao_conclusao()` escreve o texto que vai para a ficha, com quem conclui.
A pessoa le e clica em "tratada". (A camada de agosto que fechava sozinha nao
volta.)

Regras do que pode ser sugerido:
  ciencia-auto            ato de ciencia pura (mero expediente, distribuicao), sem
                          prazo, com confianca minima e titularidade definida.
  ciencia-copia-contraria copia do DJEN enderecada a parte contraria num ato que
                          nao e recursal: o prazo nao e nosso.
  ciencia-copia-duplicata copia enderecada a parte contraria de ato recursal, quando
                          a NOSSA copia do mesmo ato esta na fila: o prazo nosso fica
                          na nossa copia; esta e duplicata.
  conferida-no-advbox     a equipe ja abriu tarefa de peca/protocolo nesse processo
                          depois da publicacao (espelho do ADVBOX em `tarefas`).
                          COMENTARIO e ACOMPANHAMENTO sozinhos nao contam: em 01/10
                          dois comentarios sobre responsavel do ADVBOX pareciam
                          tratamento e nao eram.

O que NUNCA recebe sugestao de conclusao: teor fora do DJEN, sigilo, titularidade
INDEFINIDO, qualquer coisa com prazo nosso vivo, texto com audiencia e data.
"""
from __future__ import annotations

import re
import unicodedata

CONFIANCA_MINIMA = 0.6
# "16.11.2026" (TRT24) e "16/11/2026": os dois jeitos de escrever a data.
_RE_DATA = re.compile(r'\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b')


def _norm_txt(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', s).lower()

_ATOS_CIENCIA = {'CIENCIA / MERO EXPEDIENTE', 'DISTRIBUICAO / SORTEIO DE RELATOR'}

# Tipos de tarefa do ADVBOX que provam conferencia da publicacao.
TIPOS_QUE_CONFEREM = {
    'REVISAO DE PEÇA', 'PROTOCOLO', 'PETIÇÃO DE MEIO', 'CONTRARRAZÕES', 'EMITIR GUIA',
    'PETIÇÃO SIMPLES', 'PETIÇÃO INICIAL SIMPLES', 'PETIÇÃO INICIAL NORMAL',
    'MANIFESTAÇÃO DE LAUDO TECNICO (PERICIA)', 'TENTATIVA DE ACORDO',
    'CONFERIR LIQUIDAÇÃO SENTENÇA', 'EMBARGOS DE DECLARAÇÃO', 'ANALISE DE SENTENCA',
    'PREPARAÇÃO PARA AUDIENCIA', 'ACOMPANHAR IMPLANTAÇÃO DECISÃO', 'ATUALIZAR CLIENTE',
    'CONTESTAÇÃO NORMAL', 'PETIÇÃO SIMPLES EXECUCAO', 'INICIAL EXECUÇÃO CUMPRIMENTO SENTENÇA',
    'AVISAR CLIENTE AUDIÊNCIA', 'COMUNICAR CLIENTE SOBRE AUDIENCIA - POP 18',
    'SUSTENTAÇÃO ORAL TRT TRF TJ', 'MINUTA DE ACORDO', 'AUDIÊNCIA INICIAL', 'AUDIENCIA UNA',
    'AUDIENCIA DE INSTRUÇÃO', 'AUDIENCIA DE CONCILIAÇÃO', 'SOLICITAR E MONITORAR O ENVIO DE DOCUMENTOS',
}


def _norm_tipo(t):
    # 03/11/2026 (fase 1, ajuste 5): sem acento, como o motor e o router de tarefas gravam. Antes so
    # passava para maiusculas e "PETICAO DE MEIO" (motor) nunca casava com "PETIÇÃO DE MEIO" (ADVBOX).
    s = unicodedata.normalize('NFKD', str(t or ''))
    return ''.join(ch for ch in s if not unicodedata.combining(ch)).strip().upper()


# a lista acima fica legivel com acento; a comparacao usa a forma sem acento
TIPOS_QUE_CONFEREM = {_norm_tipo(t) for t in TIPOS_QUE_CONFEREM}


def _prova_desta_intimacao(t, intimacao):
    """Tarefa da tela ou da cadeia do motor (origem intimacao/motor) so prova a conferencia da PROPRIA
    intimacao: a cadeia de uma intimacao irma do mesmo processo nao fecha esta (revisao de 03/11/2026,
    que achou a sugestao falsa de "ja conferida" com prazo nosso vivo). ADVBOX e manual seguem pelo
    processo e pela data, como antes."""
    if (t.get('origem') or 'advbox') not in ('motor', 'intimacao'):
        return True
    return str(t.get('origem_ref') or '') == str((intimacao or {}).get('id') or '')


_QUEM = {
    'conferida-no-advbox': 'Advogada conclui (a providencia ja esta no ADVBOX)',
    'conferida-no-sistema': 'Advogada conclui (a tarefa ja esta aberta no Sistema Escritório Modelo)',
    'ciencia-copia-contraria': 'Advogada conclui',
    'ciencia-copia-duplicata': 'Advogada conclui',
    'ciencia-auto': 'Advogada conclui',
}
_ROTULO = {
    'conferida-no-advbox': 'ja conferida no ADVBOX',
    'conferida-no-sistema': 'ja conferida: tarefa aberta no Sistema Escritório Modelo',
    'ciencia-copia-contraria': 'copia da parte contraria',
    'ciencia-copia-duplicata': 'copia duplicada de ato recursal',
    'ciencia-auto': 'ciencia pura, sem prazo',
}


def sugestao_conclusao(decisao, tarefas_advbox=None):
    """O texto que vai para a ficha. Quem conclui e sempre uma pessoa."""
    regra, motivo = decisao
    quem = _QUEM.get(regra, 'Advogada conclui')
    donos = sorted({str(t.get('responsavel') or '').split()[0] for t in (tarefas_advbox or [])
                    if t.get('responsavel')})
    if regra == 'conferida-no-advbox' and donos:
        quem = '%s ja esta com a tarefa no ADVBOX; Advogada conclui aqui' % ', '.join(donos)
    elif regra == 'conferida-no-sistema' and donos:
        quem = '%s ja esta com a tarefa no Sistema Escritório Modelo; Advogada conclui' % ', '.join(donos)
    return {
        'regra': regra,
        'rotulo': _ROTULO.get(regra, regra),
        'motivo': motivo,
        'quem': quem,
        'texto': 'SUGESTAO DO MOTOR: concluir esta intimacao (%s). %s Quem conclui: %s.'
                 % (_ROTULO.get(regra, regra), motivo, quem),
    }


def decidir(intimacao: dict, motor: dict | None, tarefas_advbox: list[dict] | None,
            fila: list[dict] | None = None, confianca_minima: float = CONFIANCA_MINIMA):
    """(regra, motivo) quando cabe SUGERIR a conclusao; None quando nao. Nunca executa.

    intimacao: item do cache (id, processo, data, texto...)
    motor: saida do classificador para esta intimacao (ou None)
    tarefas_advbox: tarefas do espelho (origem advbox) do MESMO processo criadas
                    a partir da data da publicacao: dicts com id, tipo, origem_ref, data
    fila: as demais intimacoes pendentes (para achar a nossa copia de um ato recursal)
    """
    if not intimacao or intimacao.get('tratada'):
        return None
    m = motor or {}
    if m.get('sigiloso') or m.get('teor_fora_do_djen'):
        return None
    # Audiencia com data nunca fecha sozinha, seja qual for o rotulo: a
    # notificacao da audiencia inicial do Jetro (20692) chegou a cair em
    # "ciencia" numa regressao de 01/10/2026.
    texto = _norm_txt(intimacao.get('texto') or intimacao.get('resumo') or '')
    if 'audiencia' in texto and _RE_DATA.search(texto):
        return None
    tit = m.get('titularidade') or ''
    ato = m.get('ato') or ''
    conf = float(m.get('confianca') or 0)
    prazos = m.get('prazos') or []
    venc = m.get('vencimento')

    # 1) Equipe ja agiu depois da publicacao: tarefa no ADVBOX (ate 03/10/2026) ou,
    #    a partir de 06/10/2026, no proprio Sistema Escritório Modelo (motor, tela ou manual).
    provas = [t for t in (tarefas_advbox or []) if _norm_tipo(t.get('tipo')) in TIPOS_QUE_CONFEREM
              and _prova_desta_intimacao(t, intimacao)]
    if provas:
        ids = ', '.join('%s %s' % (t.get('origem_ref') or t.get('id'), _norm_tipo(t.get('tipo'))[:24])
                        for t in provas[:3])
        if all((t.get('origem') or 'advbox') == 'advbox' for t in provas):
            return ('conferida-no-advbox',
                    'Tarefa aberta no ADVBOX depois da publicacao: %s' % ids)
        return ('conferida-no-sistema',
                'Tarefa aberta no Sistema Escritório Modelo depois da publicacao: %s' % ids)

    # 2) Copia enderecada a parte contraria (em ato com prazo; ciencia pura cai na regra 3).
    if m.get('copia_de_adverso') and m.get('trilha') != 'CIENCIA':
        dest = '; '.join(m.get('destinatarios') or [])[:80]
        if tit == 'CONTRARIA':
            return ('ciencia-copia-contraria',
                    'Copia do DJEN enderecada a parte contraria (%s); o prazo nao e nosso.' % dest)
        # ato recursal: so fecha se a nossa copia do mesmo ato esta na fila
        for outra in (fila or []):
            if (outra is not intimacao and outra.get('id') != intimacao.get('id')
                    and outra.get('processo') == intimacao.get('processo')
                    and str(outra.get('data'))[:10] == str(intimacao.get('data'))[:10]
                    and not ((outra.get('motor') or {}).get('copia_de_adverso'))):
                return ('ciencia-copia-duplicata',
                        'Copia enderecada a parte contraria (%s) de ato recursal; o prazo nosso '
                        'esta na copia id %s.' % (dest, outra.get('id')))
        return None

    # 3) Ciencia pura.
    if (m.get('trilha') == 'CIENCIA' and ato in _ATOS_CIENCIA and not prazos and not venc
            and conf >= confianca_minima and tit in ('NOSSO', 'CONTRARIA', 'AMBOS')):
        return ('ciencia-auto', 'Ato de ciencia sem prazo (%s), confianca %.2f.' % (ato, conf))

    return None


if __name__ == '__main__':
    base = {'id': 1, 'processo': 'X', 'data': '2026-10-01'}
    casos = [
        ('ciencia pura', base, {'trilha': 'CIENCIA', 'ato': 'DISTRIBUICAO / SORTEIO DE RELATOR', 'confianca': 0.65, 'titularidade': 'NOSSO'}, [], 'ciencia-auto'),
        ('ciencia com prazo nao fecha', base, {'trilha': 'CIENCIA', 'ato': 'CIENCIA / MERO EXPEDIENTE', 'confianca': 0.9, 'titularidade': 'NOSSO', 'vencimento': '2026-10-09'}, [], None),
        ('teor fora nao fecha', base, {'trilha': 'MEIO', 'ato': 'DECISAO SEM TEOR NO DJEN', 'teor_fora_do_djen': True, 'confianca': 0.9, 'titularidade': 'NOSSO'}, [], None),
        ('copia contraria', base, {'trilha': 'MEIO', 'ato': 'MANIFESTACAO GENERICA', 'copia_de_adverso': True, 'titularidade': 'CONTRARIA', 'destinatarios': ['fontanella']}, [], 'ciencia-copia-contraria'),
        ('comentario nao e conferencia', base, {'trilha': 'PECA', 'ato': 'EMENDA A INICIAL', 'titularidade': 'NOSSO', 'confianca': 0.8}, [{'tipo': 'COMENTÁRIO', 'origem_ref': 1}], None),
        ('protocolo e conferencia', base, {'trilha': 'PECA', 'ato': 'EMENDA A INICIAL', 'titularidade': 'NOSSO', 'confianca': 0.8}, [{'tipo': 'PROTOCOLO', 'origem_ref': 282601466}], 'conferida-no-advbox'),
        # 03/11/2026: o router e o motor gravam o tipo sem acento; tem de contar igual ao do ADVBOX
        ('peticao de meio sem acento (tela)', base, {'trilha': 'MEIO', 'ato': 'JUNTADA DE DOCUMENTOS', 'titularidade': 'NOSSO', 'confianca': 0.8}, [{'tipo': 'PETICAO DE MEIO', 'origem_ref': 1, 'origem': 'intimacao'}], 'conferida-no-sistema'),
        ('revisao de peca sem acento (motor)', base, {'trilha': 'PECA', 'ato': 'EMENDA A INICIAL', 'titularidade': 'NOSSO', 'confianca': 0.8}, [{'tipo': 'REVISAO DE PECA', 'origem_ref': 1, 'origem': 'motor'}], 'conferida-no-sistema'),
        ('cadeia da intimacao irma nao fecha esta', base, {'trilha': 'MEIO', 'ato': 'JUNTADA DE DOCUMENTOS', 'titularidade': 'NOSSO', 'confianca': 0.8}, [{'tipo': 'PETICAO DE MEIO', 'origem_ref': 777, 'origem': 'motor'}], None),
        ('com acento do advbox segue valendo', base, {'trilha': 'MEIO', 'ato': 'JUNTADA DE DOCUMENTOS', 'titularidade': 'NOSSO', 'confianca': 0.8}, [{'tipo': 'PETIÇÃO DE MEIO', 'origem_ref': 9}], 'conferida-no-advbox'),
    ]
    ok = 0
    for nome, it, mo, tar, esperado in casos:
        got = decidir(it, mo, tar)
        regra = got[0] if got else None
        bom = regra == esperado
        ok += bom
        print('%s  %-30s -> %s (esperado %s)' % ('OK  ' if bom else 'FALHA', nome, regra, esperado))
    print('\n%d/%d' % (ok, len(casos)))
