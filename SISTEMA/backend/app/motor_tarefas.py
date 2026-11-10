# -*- coding: utf-8 -*-
"""MOTOR DAS TAREFAS - Sistema Escritório Modelo (02/10/2026; criterio unico em 05/10/2026).

Liga a conferencia de publicacoes ao modulo Tarefas sem passar pelo ADVBOX.
A partir de 06/10/2026 a equipe cadastra e conclui tarefa AQUI; o ADVBOX fica
so de leitura (espelho das antigas).

REGRA MAE (Advogada, 01/10/2026): o motor prepara e sugere; quem conclui e
sempre uma pessoa da equipe. Este modulo nunca conclui tarefa de pessoa nem
intimacao. A unica coisa que a automacao fecha e a PROPRIA tarefa (analise e
revisao da controladoria, em nome do usuario 9, Assistente IA), ao entregar o
resultado na tarefa da pessoa seguinte.

O CRITERIO UNICO DE TODA PECA (Advogada, 05/10/2026: "ED, RO, RR, todos seguem
o mesmo criterio", igual a inicial):
    PECA       a automacao redige a primeira versao pela skill da peca, passa
               pela controladoria e entrega; a Advogada revisa (Dra. Advogada so
               nos processos da carteira dela; processo novo e da Advogada);
    REVISAO    a automacao faz a revisao final da controladoria pela skill de
               revisao: relatorio com STATUS (aprovada, aprovada com ressalvas,
               nao aprovada), peca com os ajustes marcados e sugestoes;
    AJUSTES    a Advogada aplica as ressalvas;
    PROTOCOLO  a Advogada, dona da controladoria, dos prazos e dos protocolos
               (a Advogada entra junto em inicial e em recurso). AJUSTES e
               PROTOCOLO sao a tarefa dupla que o escritorio ja usa.

NOS RECURSOS (sentenca ou acordao publicado), a Assistente vai na frente (Advogada,
05/10/2026 a noite: "a estrategia nao pode engargalar a Advogada"):
    ANALISE   a Assistente analisa a decisao inteira, pela skill de embargos de
              declaracao e pela do recurso cabivel, define a estrategia conforme
              o nosso polo e JA redige a peca;
    PECA      a Advogada recebe a revisao com a estrategia adotada e o porque. Se
              preferir outro caminho, escreve REFAZER: e a orientacao, e a
              Assistente refaz. NAO RECORRER vai para a decisao da Dra. Advogada.

DE ONDE NASCE A CADEIA:
    - da intimacao: a pessoa fecha a conferencia na tela (cadastrar);
    - de uma tarefa cadastrada: razoes finais por memoriais, cujo prazo vem da
      ata da audiencia de instrucao e nao do DJEN (varrer_tarefas_para_producao);
    - a contestacao tem esteira propria (notificacao da empresa no WhatsApp).

Datas de tras para a frente: PROTOCOLO no fatal interno (vencimento menos 2
dias uteis), AJUSTES um dia util antes, REVISAO dois, PECA tres, ESTRATEGIA
quatro; a ANALISE nasce para hoje. Nenhuma etapa nasce antes de hoje. Nos
recursos com embargos de declaracao em paralelo, a cadeia e datada pelo prazo
MAIS CURTO, que e o que nao perdoa.

Filas da automacao (um robo as consome; aqui so se enfileira e se entrega):
    producao_fila  modo 'analise' (estrategia do recurso) e 'v1' (primeira versao)
    revisao_fila   revisao final da controladoria
Tabelas: `tarefas`/`tarefa_responsaveis` (routers/tarefas.py) com as colunas
`cadeia` ('intim:<id>' ou 'tarefa:<id>') e `etapa`; `motor_pf` guarda o PF.
"""
import datetime as dt
import json
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

from . import tarefas_tipos as TT
from .controladoria_motor import feriados as FER

_DB = str(Path(__file__).resolve().parents[1] / 'sistema_esc.db')
CATALOGO = Path(__file__).resolve().parent / 'controladoria_motor' / 'catalogo_tarefas.json'

from .juridico import equipe as _EQ

# Pessoas e papeis vem de juridico/equipe.json (ids da tabela `usuarios`). Nome e o que o motor
# escreve na ficha. Producao, revisao, controladoria e protocolo: a revisora (Dra. Advogada);
# financeiro: o titular (Dr. Titular); as etapas da propria automacao: o Assistente IA.
AUTOMACAO_ID = _EQ.pessoa_do_papel('automacao').get('id', 3)
AUTOMACAO = _EQ.pessoa_do_papel('automacao').get('nome', 'Assistente IA (automacao)')
EQUIPE = _EQ.EQUIPE
REVISORA_ID = _EQ.pessoa_do_papel('revisora').get('id', 2)

# atos em que a Advogada entra junto no protocolo (POP, passo 7): inicial e
# recurso; contrarrazoes ela assina a maioria. Emenda e peca simples, nao.
_PROTOCOLO_COM_PAM = re.compile(r'(?<!EMENDA A )INICIAL|RECURSO|AGRAVO|APELACAO|RESP\b|REVISTA|CONTRARRAZ')
# skill de revisao da controladoria por familia de peca
_SKILL_REVISAO = [
    (re.compile(r'INICIAL|EMENDA'), 'revisao-inicial'),
    (re.compile(r'RECURSO|AGRAVO|APELACAO|CONTRARRAZ|CONTRAMINUTA|REVISTA'), 'revisao-recursos'),
]
# atos de meio que a Advogada resolve sem peca de tese
_MEIO_FLAVIA = re.compile(r'CUSTAS|GUIA|DEPOSITO|CREDENCIAMENTO|DILIGENCIA|CIENCIA|DISTRIBUICAO')
# skill que REDIGE a primeira versao da peca. Sem skill (RR, agravo, laudo), a
# Advogada produz a etapa 1 e o resto da cadeia e o mesmo.
_SKILL_PRODUCAO = [
    (re.compile(r'REPLICA|IMPUGNACAO A CONTESTACAO|MANIFESTACAO SOBRE (A )?(DEFESA|CONTESTACAO|DOCUMENTOS)'), 'replica'),
    (re.compile(r'EMBARGOS DE DECLARACAO'), 'embargos-declaracao'),
    (re.compile(r'RECURSO ORDINARIO|APELACAO'), 'ro-recurso-ordinario'),
    (re.compile(r'CONTRARRAZ|CONTRAMINUTA'), 'contrarrazoes-ro'),
    (re.compile(r'RAZOES FINAIS|MEMORIAIS|ALEGACOES FINAIS'), 'razoes-finais'),
    (re.compile(r'CALCULO|LIQUIDACAO|EXECUCAO CUMPRIMENTO'), 'liquidacao-previa'),
    (re.compile(r'INICIAL(?! EXECUCAO)|EMENDA'), 'peticao-inicial-reclamante'),
    (re.compile(r'CONTESTACAO|DEFESA'), 'contestacao-reclamada'),
]
# liquidacao e calculo: antes da peca, a tarefa de mandar ao contador quando os
# direitos pedem calculo mais apurado (Advogada, 05/10/2026). E condicional: a pessoa
# tira no fechamento, ou conclui dizendo que nao precisou, quando o calculo e simples.
_CALCULO = re.compile(r'CALCULO|LIQUIDACAO|CUMPRIMENTO DE SENTENCA|EXECUCAO CUMPRIMENTO|EMBARGOS A EXECUCAO')
# etapas que seguram a redacao ate uma pessoa concluir
_PORTAS = (TT.ENVIAR_AO_CONTADOR,)
# sessao de julgamento em pauta (o catalogo de atos ainda nao tem o ato; le-se do texto)
_PAUTA = re.compile(r'PAUTA DE JULGAMENTO|SESSAO DE JULGAMENTO|INCLUID[AO] (NA|EM) PAUTA|INCLUSAO (NA|EM) PAUTA|SUSTENTACAO ORAL')
# alvara expedido ou valor liberado (Advogada, 05/10/2026): a automacao acompanha o processo,
# faz o calculo previo dos honorarios pelo contrato do cliente e abre a tarefa da Titular
# de lancar a receita, validar, lancar e pagar o cliente
_ALVARA = re.compile(r'ALVARA|MANDADO DE LEVANTAMENTO|LEVANTAMENTO DE (VALOR|DEPOSITO|QUANTIA)|GUIA DE LEVANTAMENTO|'
                     r'OFICIO DE TRANSFERENCIA|TRANSFERENCIA ELETRONICA|SISCONDJ|LIBERACAO DE VALOR|'
                     r'COMPROVANTE DE (PAGAMENTO|TRANSFERENCIA|RESGATE|LEVANTAMENTO)')
# recurso contra sentenca ou acordao: analise e estrategia antes da peca
_RECURSAL = re.compile(r'RECURSO|APELACAO|AGRAVO|REVISTA|EMBARGOS DE DECLARACAO|RESP\b')
_NAO_RECURSAL = re.compile(r'CONTRARRAZ|CONTRAMINUTA|MANIFESTACAO')
# peca que nasce de tarefa cadastrada (o prazo vem da ata, nao do DJEN)
_NASCE_DE_TAREFA = re.compile(r'RAZOES FINAIS|MEMORIAIS|ALEGACOES FINAIS|^REPLICA|MANIFESTACAO SOBRE (O )?LAUDO|'
                              r'IMPUGNACAO AO LAUDO|^QUESITOS')
_LINK = re.compile(r'https?://\S+')


# PLANTAO (Advogada, 05/10/2026: "vamos pela estrategia do plantao"). Cada etapa da automacao
# tem uma pessoa de plantao. Enquanto o vigia daquela etapa nao estiver ligado, a pessoa
# entra JUNTO na tarefa, para nada ficar parado numa fila que ninguem trabalha; com ela na
# tarefa, a automacao entrega mas nao fecha (quem conclui e a pessoa). Ligar o vigia de uma
# etapa e trocar False por True aqui, depois da semana de conferencia com a Advogada.
PLANTAO = {
    TT.ANALISE: 'Advogada',
    TT.REVISAO: 'Advogada',
    'ROTEIRO': 'Advogada',
    'COMUNICAR CLIENTE AUDIENCIA': 'Advogada',
    TT.ATUALIZAR_CLIENTE: 'Advogada',
    'RECADO PARA PARCEIRO': 'Advogada',
    'CALCULO DE HONORARIOS': 'Titular',
    'RELATORIO DE CONSULTORIA': 'Advogada',
}
# 07/10/2026 (Advogada): o vigia noturno esta ligado desde 06/10 e a regra e UMA pessoa por tarefa, a Assistente
# sozinha nas dela. O plantao fica desligado (True = vigia ligado, sem pessoa junto).
VIGIA_LIGADO = {tipo: True for tipo in PLANTAO}


def _por_plantao(c, tarefa_id, tipo):
    """Poe a pessoa de plantao na tarefa da automacao quando o vigia da etapa esta desligado."""
    tipo = _norm(tipo)
    if tipo not in PLANTAO or VIGIA_LIGADO.get(tipo):
        return None
    if not c.execute('SELECT 1 FROM tarefa_responsaveis WHERE tarefa_id=? AND usuario_id=?', (tarefa_id, AUTOMACAO_ID)).fetchone():
        return None
    pessoa = quem(PLANTAO[tipo])
    if c.execute('SELECT 1 FROM tarefa_responsaveis WHERE tarefa_id=? AND usuario_id=?', (tarefa_id, pessoa['id'])).fetchone():
        return pessoa['nome']
    c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) VALUES (?,?,?,1,0)',
              (tarefa_id, pessoa['id'], pessoa['nome']))
    c.execute("UPDATE tarefas SET descricao=COALESCE(descricao,'') || ? WHERE id=?",
              ('\n\nPLANTAO: o vigia desta etapa ainda nao esta ligado. %s esta junto nesta tarefa e e quem conclui; a '
               'automacao entrega o resultado aqui quando rodar.' % pessoa['nome'], tarefa_id))
    return pessoa['nome']


def _norm(s):
    # a regra mora em tarefas_tipos.norm_tipo, a mesma do router de tarefas (fase 2, T1)
    return TT.norm_tipo(s)


def _data(v):
    if not v:
        return None
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _br(d):
    d = _data(d)
    return d.strftime('%d/%m/%Y') if d else ''


def uteis_antes(d, n, uf=''):
    """n dias uteis ANTES de d na regua D-n (n=0 devolve o proprio d), conta CRUA: calendario unico (so feriado
    nacional), sem a regra das ferias da equipe, que e aplicada sobre a cadeia inteira (FER.ajustar_cadeia_ferias).
    Para o fatal interno de um prazo use FER.prazo_interno. `uf` fica na assinatura por compatibilidade."""
    return FER.uteis_antes_cru(_data(d), n)


def uteis_depois(d, n, uf=''):
    """n dias uteis forenses depois de d (o dia de d nao conta)."""
    d = _data(d)
    while n > 0:
        d += dt.timedelta(days=1)
        if FER.eh_dia_util(d, uf):
            n -= 1
    return d


def quem(nome):
    """'Advogada' / 'Dra. Advogada' / 'Advogada' / 'Assistente' -> {'id','nome'}, ou None."""
    chave = _norm(nome).lower()
    for tok in re.split(r'[^a-z]+', chave):
        if tok in EQUIPE:
            return dict(EQUIPE[tok])
    return None


def skill_de_revisao(ato):
    a = _norm(ato)
    for rx, skill in _SKILL_REVISAO:
        if rx.search(a):
            return skill
    return 'revisao-integral'


def skill_de_producao(ato):
    a = _norm(ato)
    for rx, skill in _SKILL_PRODUCAO:
        if rx.search(a):
            return skill
    return None


def precisa_estrategia(ato):
    a = _norm(ato)
    return bool(_RECURSAL.search(a)) and not _NAO_RECURSAL.search(a)


def precisa_contador(ato):
    a = _norm(ato)
    return bool(_CALCULO.search(a)) and not precisa_estrategia(a)


# ---------------------------------------------------------------------------
# A cadeia
# ---------------------------------------------------------------------------

def _etapa(n, tipo, para, data, texto, **extra):
    e = {'etapa': n, 'tipo': tipo, 'para': para, 'data': data.isoformat() if data else None,
         'texto': texto}
    e.update(extra)
    return e


def etapas_da_peca(ato, quem_produz, venc, interno, hoje, uf='', providencia='', com_estrategia=None, nota='',
                   com_contador=None):
    """As etapas de toda peca, no criterio unico. `com_estrategia` liga as duas
    etapas dos recursos e `com_contador` a de mandar ao contador na liquidacao
    (padrao: decide pelo ato). `interno` e o fatal interno que governa as datas;
    `venc` e o prazo processual que vai escrito nas tarefas."""
    ato = _norm(ato)
    venc, interno = _data(venc), _data(interno)
    if com_estrategia is None:
        com_estrategia = precisa_estrategia(ato)
    if com_contador is None:
        com_contador = precisa_contador(ato)
    (d_peca, d_rev, d_aj, d_prot), aviso_fatal = FER.ajustar_cadeia_ferias_com_aviso(
        [uteis_antes(interno, 3, uf), uteis_antes(interno, 2, uf), uteis_antes(interno, 1, uf), interno], venc, piso=hoje)
    if aviso_fatal:
        nota = (nota + ' ' + aviso_fatal).strip()
    skill = skill_de_revisao(ato)
    producao = skill_de_producao(ato)
    nome_peca = ato.title()
    fatal = venc.isoformat() if venc else None
    prazos = 'Fatal interno %s%s.' % (_br(interno), (' (processual %s)' % _br(venc)) if venc else '')
    if nota:
        prazos += ' ' + nota
    etapas, n = [], 0
    if com_estrategia:
        # Advogada, 05/10/2026 (noite): a estrategia nao pode engargalar a Advogada. A Assistente analisa a
        # decisao inteira, define a estrategia conforme o nosso polo, JA redige a peca e entrega
        # para a Advogada revisar, dizendo qual estrategia adotou e por que. A Advogada ve como prefere seguir.
        n += 1
        etapas.append(_etapa(n, TT.ANALISE, 'Assistente', hoje,
                             'A Assistente analisa a decisao inteira, pela skill de embargos de declaracao e pela do recurso '
                             'cabivel: o que cabe alegar conforme o nosso polo, vicios que pedem ED, capitulos recorriveis, '
                             'risco, custas e deposito. Define a estrategia, JA redige a peca%s, passa pela controladoria e '
                             'entrega na tarefa seguinte, dizendo qual estrategia adotou e por que. %s'
                             % ((' pela skill ' + producao) if producao else '', prazos),
                             prazo_fatal=fatal, rotulo='ANALISE, ESTRATEGIA E PRIMEIRA VERSAO: ' + nome_peca,
                             skill_analise='embargos-declaracao + %s' % (producao or 'ro-recurso-ordinario'),
                             skill_producao=producao))
        n += 1
        etapas.append(_etapa(n, TT.PECA, quem_produz, d_peca,
                             'Revisar a estrategia e a peca que a Assistente entregou. A primeira linha da descricao diz a peca '
                             '(PECA: ...) e logo abaixo vem a estrategia adotada e o porque, com o link. Concorda: revise, '
                             'deixe o link da versao revisada e conclua. Prefere outro caminho: escreva uma linha comecando com '
                             'REFAZER: e a orientacao, e conclua; a Assistente refaz e devolve nesta mesma tarefa. %s %s'
                             % (prazos, providencia),
                             prazo_fatal=fatal, rotulo='REVISAR ESTRATEGIA E PECA: ' + nome_peca))
    else:
        quando = ' com o calculo do contador' if com_contador else ''
        if com_contador:
            n += 1
            etapas.append(_etapa(n, TT.ENVIAR_AO_CONTADOR, quem_produz, hoje,
                                 'Mandar ao contador os direitos que pedem calculo mais apurado: a decisao, os holerites, o '
                                 'ponto e o que mais ele precisar. Combine a data de retorno e anote aqui; quando o calculo '
                                 'voltar, cole o link da planilha nesta descricao e conclua: a redacao da peca entra na fila '
                                 'com ele. Calculo simples, que a automacao resolve: tire esta etapa no fechamento ou conclua '
                                 'dizendo que nao precisou. Honorarios do calculista: avisar o financeiro. %s' % prazos,
                                 prazo_fatal=fatal, condicional=True, rotulo='ENVIAR AO CONTADOR (se o calculo pedir): ' + nome_peca))
        if producao:
            texto1 = ('%s: a Assistente redige a PRIMEIRA VERSAO pela skill %s%s, passa pela controladoria e deixa o link '
                      'em ATOS INTERNOS e nesta tarefa; %s revisa e, ao concluir, deixa na descricao o link da versao '
                      'revisada: e ele que a revisao final usa. %s %s'
                      % (nome_peca, producao, quando, quem_produz, prazos, providencia))
            rotulo1 = 'V1 E REVISAO: ' + nome_peca
        else:
            texto1 = ('%s: produzir a peca%s e subir em ATOS INTERNOS (ainda nao ha skill de redacao para este ato). Ao '
                      'concluir, cole o link do Drive na descricao desta tarefa: e ele que a revisao final usa. %s %s'
                      % (nome_peca, quando, prazos, providencia))
            rotulo1 = nome_peca
        n += 1
        etapas.append(_etapa(n, TT.PECA, quem_produz, d_peca, texto1.strip(), prazo_fatal=fatal, rotulo=rotulo1,
                             skill_producao=producao))
    n += 1
    etapas.append(_etapa(n, TT.REVISAO, 'Assistente', d_rev,
                         'Revisao final da controladoria, feita pela automacao: skill %s com a '
                         'revisao-controladoria sobre o link que %s deixou. Entrega o relatorio com o STATUS '
                         '(aprovada, aprovada com ressalvas ou nao aprovada), a peca com os ajustes marcados e as '
                         'sugestoes; os links entram na tarefa seguinte. NAO APROVADA chama a Dra. Advogada.'
                         % (skill, quem_produz),
                         prazo_fatal=fatal, skill=skill, rotulo='REVISAO DA CONTROLADORIA: ' + nome_peca))
    n += 1
    etapas.append(_etapa(n, TT.AJUSTES, quem_produz, d_aj,
                         'Revisao final: aplicar as ressalvas do relatorio da controladoria e subir a versao final em '
                         'ATOS INTERNOS; ao concluir, a tarefa de protocolo da Advogada fica na vez.',
                         prazo_fatal=fatal, rotulo='REVISAO FINAL E AJUSTES DE ' + nome_peca))
    para_prot = ['Advogada']   # inicial e recurso: a Dra. Advogada protocola (POP, passo 7)
    n += 1
    etapas.append(_etapa(n, TT.PROTOCOLO, ', '.join(para_prot), d_prot,
                         'Protocolo de %s no fatal interno %s%s. Ao concluir, o PF da agenda recebe [ok] e abre '
                         'ATUALIZAR CLIENTE.' % (nome_peca.lower(), _br(interno), (' (processual %s)' % _br(venc)) if venc else ''),
                         prazo_fatal=fatal, rotulo='PROTOCOLO DE ' + nome_peca,
                         **({'aviso': aviso_fatal} if aviso_fatal else {})))
    return etapas


def etapas_do_alvara(hoje, evidencia, uf=''):
    """Alvara expedido ou valor liberado: calculo previo dos honorarios (automacao)
    e a tarefa da Titular de validar, lancar a receita e pagar o cliente."""
    dia = FER.proximo_dia_util(hoje, uf, incluir_hoje=True)
    return [
        _etapa(1, 'CALCULO DE HONORARIOS', 'Assistente', hoje,
               'Calculo previo dos honorarios pela automacao: abre o contrato do cliente na pasta do caso, le o '
               'percentual e as condicoes e calcula sobre o valor liberado (%s): honorarios contratuais, '
               'sucumbenciais, despesas a reembolsar, repasse a parceiro (imposto antes dos 50%%) e o liquido do '
               'cliente. O resultado entra na tarefa da Titular.' % evidencia,
               rotulo='CALCULO PREVIO DOS HONORARIOS'),
        _etapa(2, 'LANCAMENTO DE RECEITA', 'Titular', dia,
               'Alvara expedido ou valor liberado (%s). Validar o calculo previo da automacao (entra aqui embaixo '
               'quando ficar pronto), lancar a receita, pagar o cliente e anexar o comprovante. Antes de pagar, '
               'confira se o valor e mesmo do nosso cliente e se ja caiu na conta.' % evidencia,
               rotulo='LANCAR RECEITA E PAGAR O CLIENTE'),
    ]


# Cobranca da audiencia (Advogada, 05/10/2026): designada audiencia de cliente desta
# lista, nasce a tarefa da Titular de enviar a cobranca, com vencimento na data da
# audiencia. Reclamante nao paga deslocamento: fora da lista, nao nasce nada.
_CLI_EMP = json.loads((Path(__file__).resolve().parent / 'juridico' / 'clientes_empresa.json').read_text('utf-8'))
COBRANCA_AUDIENCIA = dict(_CLI_EMP.get('cobranca_audiencia') or {})   # juridico/clientes_empresa.json


def cobranca_da_audiencia(cliente):
    c = _norm(cliente)
    for nome, valor in COBRANCA_AUDIENCIA.items():
        if re.search(r'\b%s\b' % nome, c):
            return nome, valor
    return None, None


def _compromisso(intimacao):
    """Data, hora, especie e modalidade da audiencia, lidos do texto (compromisso.py)."""
    if isinstance(intimacao.get('compromisso'), dict):
        return intimacao['compromisso']
    try:
        from .controladoria_motor import compromisso as CP
        r = CP.extrair(intimacao.get('texto') or '', intimacao.get('orgao') or '', intimacao.get('resumo') or '')
        return r if (r or {}).get('tem') else {}
    except Exception:
        return {}


def etapas_do_roteiro(evento, dia_evento, hoje, uf='', n0=1, subtipo=None):
    """Roteiro da audiencia ou da sustentacao oral (Advogada, 05/10/2026): ao tratar a
    publicacao, a automacao gera o roteiro, entrega para a Advogada estudar com os
    pontos de atencao, e a tarefa dela nasce no sistema e na agenda juntas."""
    dia_evento = _data(dia_evento)
    piso = FER.proximo_dia_util(hoje, uf)
    estudo = FER.ajustar_cadeia_ferias([uteis_antes(dia_evento, 3, uf)], dia_evento, piso=piso)[0] if dia_evento else piso
    fatal = dia_evento.isoformat() if dia_evento else None
    quando = _br(dia_evento) if dia_evento else 'data a conferir na Agenda'
    if evento == 'sustentacao':
        oque = ('roteiro da sustentacao oral da sessao de %s: teses na ordem de forca, pontos do acordao ou da sentenca '
                'e do recurso, o que a outra parte sustenta, precedentes e a divisao do tempo' % quando)
        rot = 'ROTEIRO DA SUSTENTACAO ORAL'
    else:
        oque = ('roteiro da audiencia%s de %s: pontos controvertidos, prova de cada pedido, perguntas para o depoimento '
                'da outra parte e para cada testemunha, e a margem de acordo'
                % ((' ' + subtipo) if subtipo else '', quando))
        rot = 'ROTEIRO DA AUDIENCIA'
    return [
        _etapa(n0, 'ROTEIRO', 'Assistente', hoje,
               'A automacao gera o %s, a partir dos autos, sobe em ATOS INTERNOS e entrega na tarefa da Advogada, apontando '
               'os pontos em que ela deve ter atencao.' % oque,
               prazo_fatal=fatal, rotulo=rot, evento=evento),
        _etapa(n0 + 1, 'ESTUDAR ROTEIRO', 'Advogada', estudo,
               'Estudar o %s que a automacao gerou (o link e os pontos de atencao entram aqui embaixo) e ajustar o que '
               'for preciso antes do dia. Esta tarefa tambem esta na agenda.' % rot.lower(),
               prazo_fatal=fatal, rotulo='ESTUDAR O ' + rot),
    ]


def etapas_da_sustentacao(comp, hoje, uf=''):
    """Sessao de julgamento em pauta: inscricao (Advogada, se formos sustentar),
    roteiro pela automacao e estudo da Advogada. O cliente nao comparece."""
    dia = _data((comp or {}).get('data'))
    quando = _br(dia) if dia else 'data a conferir na Agenda'
    etapas = [_etapa(1, 'INSCRICAO SUSTENTACAO ORAL', 'Advogada', hoje,
                     'Sessao de julgamento em %s. Se formos sustentar: inscrever no prazo do proprio tribunal (regimento) '
                     'e cadastrar a sessao na Agenda. O cliente nao comparece e so e avisado do resultado. Nao vamos '
                     'sustentar: tire as etapas do roteiro no fechamento.' % quando,
                     prazo_fatal=dia.isoformat() if dia else None, condicional=True,
                     rotulo='INSCRICAO PARA SUSTENTACAO ORAL')]
    etapas.extend(etapas_do_roteiro('sustentacao', dia, hoje, uf, 2))
    return etapas


def etapas_da_audiencia(cliente, conf, comp, hoje, uf=''):
    """POP 18 (Advogada, 05/10/2026): quem comunica o cliente e a AUTOMACAO; a Advogada
    ganha um acompanhamento para fiscalizar se foi feito, se foi feito certo e se
    o cliente deu as devolutivas; um vigia leva para a tarefa dela o que o cliente
    e as testemunhas responderem. Clientes empresa com cobranca por audiencia (juridico/clientes_empresa.json): tarefa do financeiro."""
    conf, comp = conf or {}, comp or {}
    sub = conf.get('subtipo') or _norm(comp.get('especie')).lower() or None
    mod = conf.get('modalidade') or comp.get('modalidade') or None
    dia_aud = _data(comp.get('data'))
    quando = ('%s%s' % (_br(dia_aud), (' as ' + str(comp.get('hora'))) if comp.get('hora') else '')) if dia_aud \
        else 'data a conferir na Agenda'
    tem_testemunha = sub in ('una', 'instrucao')
    partes = 'a PARTE 1 (instrucoes)'
    if tem_testemunha:
        partes += ', a PARTE 2 (convite para o cliente repassar as testemunhas) e o PASSO 3 (os 3 prints por testemunha)'
    fatal = dia_aud.isoformat() if dia_aud else None
    etapas = [
        _etapa(1, 'COMUNICAR CLIENTE AUDIENCIA', 'Assistente', hoje,
               'POP 18 pela automacao: envia ao cliente, pelo WhatsApp do escritorio, %s da audiencia%s de %s%s, com o '
               'texto do POP, e registra no Chat ao Vivo. Os quatro lembretes (7 dias uteis, 3 dias uteis, vespera e o '
               'dia) seguem pela rotina das 7h. Cliente de parceiro nao recebe direto: vai pelo parceiro.'
               % (partes, (' ' + sub) if sub else '', quando, (', ' + mod) if mod else ''),
               prazo_fatal=fatal, rotulo='POP 18: COMUNICAR O CLIENTE'),
        _etapa(2, TT.ACOMPANHAMENTO, 'Advogada', FER.proximo_dia_util(hoje, uf),
               'Fiscalizar a automacao no POP 18 da audiencia de %s: conferir no Chat ao Vivo se a comunicacao saiu, se '
               'saiu certa (data, hora, modalidade, link ou endereco) e se o cliente deu as devolutivas%s. O vigia das '
               'respostas escreve aqui embaixo o que o cliente e as testemunhas responderem. Concluir quando as '
               'devolutivas estiverem completas.'
               % (quando, ': confirmou presenca, mandou o rol dizendo o que cada testemunha prova e os 3 prints de cada uma'
                  if tem_testemunha else ': confirmou que recebeu e que vai comparecer'),
               prazo_fatal=fatal, rotulo='FISCALIZAR O POP 18 E AS DEVOLUTIVAS'),
    ]
    etapas.extend(etapas_do_roteiro('audiencia', dia_aud, hoje, uf, 3, sub))
    nome, valor = cobranca_da_audiencia(cliente)
    if nome:
        reais = ('%.2f' % valor).replace('.', ',')
        etapas.append(_etapa(5, 'EMITIR BOLETO', 'Titular', hoje,
                             'Cobranca da audiencia (%s): emitir e enviar ao cliente R$ %s, com vencimento em %s, a data '
                             'da audiencia.' % (nome, reais, _br(dia_aud) if dia_aud else 'data da audiencia (conferir na Agenda)'),
                             prazo_fatal=fatal, rotulo='COBRANCA DA AUDIENCIA R$ %s' % reais, valor=valor))
    return etapas


def _texto_do_item(it, nivel=0):
    """Todo texto de um item dos autos do SYNC (documento, movimento), ate 2 niveis."""
    if isinstance(it, dict) and nivel < 3:
        return ' '.join(_texto_do_item(v, nivel + 1) for v in it.values())
    if isinstance(it, (list, tuple)) and nivel < 3:
        return ' '.join(_texto_do_item(v, nivel + 1) for v in it)
    return str(it) if isinstance(it, str) else ''


def alvara_nos_autos(itens, desde=None):
    """Varre os autos (lista do SYNC) atras de alvara, mandado de levantamento,
    oficio ou comprovante de transferencia juntado a partir de `desde`. So le."""
    out = []
    for it in itens or []:
        if not isinstance(it, dict):
            continue
        texto = _texto_do_item(it)
        if not _ALVARA.search(_norm(texto)):
            continue
        data = ''
        for k in ('data', 'data_juntada', 'data_hora', 'criado_em'):
            if it.get(k):
                data = str(it[k])[:10]
                break
        if desde and data and data < str(desde)[:10]:
            continue
        out.append({'data': data, 'nome': re.sub(r'\s+', ' ', texto).strip()[:160], 'id': it.get('id')})
    return out


def cadeia_para(motor, intimacao, hoje=None, uf=''):
    """Monta as etapas que o fechamento cadastra para UMA intimacao.

    motor: a ficha do motor como o router grava no cache (ato, trilha,
           titularidade, vencimento, prazo_fatal_interno, prazos, responsavel,
           conferencia{providencia, quem, tarefas_sugeridas, vigiar_em,
           transito_em}, parceiro, cliente, parte_adversa, sigiloso...).
    Devolve {'etapas': [...], 'pf': {...}|None, 'pf_paralelo': {...}|None,
             'motivo_sem_cadeia': str|None, 'familia': str}.
    """
    hoje = hoje or dt.date.today()
    m = motor or {}
    conf = m.get('conferencia') or {}
    ato = _norm(m.get('ato'))
    trilha = _norm(m.get('trilha'))
    tit = _norm(m.get('titularidade'))
    venc = _data(m.get('vencimento'))
    interno = _data(m.get('prazo_fatal_interno')) or (FER.prazo_interno(venc, 2) if venc else None)
    processo = intimacao.get('processo') or ''
    produtor = quem(m.get('responsavel')) or quem('Advogada')
    if produtor['id'] == AUTOMACAO_ID:      # a ficha diz automacao: peca de meio vai para a revisora
        produtor = quem('Advogada')
    parceiro = m.get('parceiro') or {}
    etapas = []

    def pf_de(dia, ato_pf=None, v=None, sufixo=''):
        return {'ato': ato_pf or ato, 'data': dia.isoformat(), 'vencimento': (v or venc).isoformat() if (v or venc) else None,
                'cliente': m.get('cliente') or '', 'parte_adversa': m.get('parte_adversa') or '',
                'processo': processo, 'marcador': 'intim:%d%s' % (int(intimacao['id']), sufixo)}

    def saida(familia, pf=None, sem=None, pf_paralelo=None):
        # despacho composto (a equipe, 06/10/2026): a audiencia designada junto com outro prazo entra
        # DEPOIS das etapas do prazo, renumerada (POP 18 pela automacao, fiscalizacao, roteiro, cobranca).
        # A agenda e a preparacao D-7 da Advogada seguem com o vigia de audiencias, que tambem le esta ficha.
        aud = m.get('audiencia') or {}
        if aud.get('data') and 'AUDIENCIA' not in ato and familia not in ('sigilo', 'duplicada'):
            try:
                from .controladoria_motor import conferencia as CF
                conf_aud = CF._pop18(CF._norm(intimacao.get('texto') or ''))
            except Exception:
                conf_aud = {}
            base = len(etapas)
            for e in etapas_da_audiencia(m.get('cliente') or intimacao.get('cliente'), conf_aud, dict(aud, tem=True), hoje, uf):
                e['etapa'] = int(e.get('etapa') or 0) + base
                etapas.append(e)
            if base == 0:
                familia, sem = 'audiencia', None
        return {'etapas': etapas, 'pf': pf, 'pf_paralelo': pf_paralelo, 'familia': familia, 'motivo_sem_cadeia': sem}

    if m.get('sigiloso'):
        return saida('sigilo', sem='Processo sigiloso: o teor nao vem pelo DJEN. Conferir nos autos e cadastrar a mao.')

    # 1) parceiro no modo "encaminhar" (Advogada, 05/10/2026): quem encaminha a intimacao e
    #    acompanha a providencia com o parceiro e a AUTOMACAO, pelo WhatsApp do escritorio;
    #    a Advogada fiscaliza. O prazo e do parceiro; o PF fica na agenda quando ha data.
    if parceiro and _norm(parceiro.get('modo')) == 'ENCAMINHAR':
        nome_p = parceiro.get('nome') or 'parceiro'
        prazo_txt = (' Prazo processual %s (interno %s).' % (_br(venc), _br(interno))) if (venc and interno) else ''
        etapas.append(_etapa(1, 'RECADO PARA PARCEIRO', 'Assistente', hoje,
                             'A automacao encaminha a publicacao a %s pelo WhatsApp do escritorio, com o teor e o prazo, pede '
                             'a confirmacao da providencia e cobra de novo ate ele responder.%s %s'
                             % (nome_p, prazo_txt, conf.get('providencia') or ''),
                             prazo_fatal=venc.isoformat() if venc else None, rotulo='ENCAMINHAR AO PARCEIRO: ' + nome_p))
        etapas.append(_etapa(2, TT.ACOMPANHAMENTO, 'Advogada', FER.proximo_dia_util(hoje, uf),
                             'Fiscalizar a automacao: conferir se a publicacao foi encaminhada a %s, se foi encaminhada '
                             'certa e se ele confirmou a providencia antes do prazo.%s O que a automacao enviou e o que o '
                             'parceiro responder entram aqui embaixo. Concluir quando a providencia estiver confirmada.'
                             % (nome_p, prazo_txt),
                             prazo_fatal=venc.isoformat() if venc else None,
                             rotulo='FISCALIZAR O ENCAMINHAMENTO AO PARCEIRO: ' + nome_p))
        return saida('parceiro', pf=pf_de(interno) if (venc and interno) else None)

    # 2) prazo da outra parte com vigia do transito: ACOMPANHAMENTO na data do vigia
    if conf.get('vigiar_em') or conf.get('transito_em'):
        for i, t in enumerate(conf.get('tarefas_sugeridas') or [], 1):
            dia = _data(t.get('vencimento_interno') or t.get('vencimento')) or hoje
            etapas.append(_etapa(i, t.get('tipo') or TT.ACOMPANHAMENTO, t.get('para') or 'Advogada',
                                 max(dia, hoje), t.get('texto') or '', condicional=bool(t.get('condicional')),
                                 prazo_fatal=t.get('vencimento')))
        if not etapas:
            dia = _data(conf.get('vigiar_em') or conf.get('transito_em')) or hoje
            etapas.append(_etapa(1, TT.ACOMPANHAMENTO, 'Advogada', max(dia, hoje),
                                 'Conferir nos autos se a parte contraria peticionou; nao havendo, o transito se consolida.'))
        # a liquidacao que vem depois do transito pode pedir contador: a tarefa ja nasce, condicional
        if any(precisa_contador(e.get('tipo')) for e in etapas):
            dia = _data(conf.get('transito_em') or conf.get('vigiar_em')) or hoje
            etapas.append(_etapa(len(etapas) + 1, TT.ENVIAR_AO_CONTADOR, 'Advogada', max(dia, hoje),
                                 'Se transitar e os direitos pedirem calculo mais apurado, mandar ao contador a decisao, os '
                                 'holerites e o ponto, combinar a data de retorno e avisar o financeiro dos honorarios. '
                                 'Calculo simples: concluir dizendo que nao precisou.',
                                 condicional=True, rotulo='ENVIAR AO CONTADOR (se o calculo pedir)'))
        return saida('vigia')

    falado = _norm(' '.join(str(x or '') for x in (m.get('ato'), conf.get('providencia'), intimacao.get('resumo'),
                                                    intimacao.get('texto'))))

    # 2a) audiencia e sessao de julgamento vem ANTES da ciencia: a publicacao nao tem prazo
    #     de peca, mas tem providencia. Audiencia (POP 18): a automacao comunica o cliente, a
    #     Advogada fiscaliza, a automacao gera o roteiro para a Advogada estudar, e os clientes empresa com cobranca
    #     ganham a cobranca da Titular. Sessao em pauta: inscricao, roteiro da sustentacao e estudo.
    #     A agenda e a preparacao D-7 da Advogada seguem com o vigia de audiencias.
    if 'AUDIENCIA' in ato:
        etapas.extend(etapas_da_audiencia(m.get('cliente') or intimacao.get('cliente'), conf, _compromisso(intimacao), hoje, uf))
        return saida('audiencia')
    if ('PAUTA' in ato or 'JULGAMENTO' in ato or 'SUSTENTACAO' in ato or _PAUTA.search(falado)) and not m.get('copia_de_adverso') \
            and not venc:
        etapas.extend(etapas_da_sustentacao(_compromisso(intimacao), hoje, uf))
        return saida('sustentacao')
    if 'PERICIA' in ato:
        return saida('audiencia', sem='Pericia: cadastrar pela Agenda (os lembretes do cliente saem pela rotina das 7h).')

    # 2b) alvara expedido ou valor liberado, sem prazo nosso: calculo previo dos
    #     honorarios (automacao) e receita (Titular). A pessoa confirma no fechamento.
    if _ALVARA.search(falado) and not venc and tit != 'CONTRARIA' and not m.get('copia_de_adverso'):
        etapas.extend(etapas_do_alvara(hoje, 'publicacao de %s' % (_br(intimacao.get('data')) or 'hoje'), uf))
        return saida('alvara')

    # 3) nada a fazer: ciencia, copia da contraria, prazo so deles
    if tit == 'CONTRARIA' or trilha == 'CIENCIA' or (not venc and trilha not in ('PECA', 'MEIO')):
        sug = m.get('sugestao_conclusao') or {}
        return saida('ciencia', sem=sug.get('texto') or 'Sem prazo nosso: concluir a intimacao (ciencia).')

    # 5) conferencia ja trouxe as tarefas (pagamento em execucao e afins)
    if conf.get('tarefas_sugeridas'):
        for i, t in enumerate(conf['tarefas_sugeridas'], 1):
            dia = _data(t.get('vencimento_interno') or t.get('vencimento')) or (interno or hoje)
            etapas.append(_etapa(i, t.get('tipo') or TT.PETICAO_DE_MEIO, t.get('para') or 'Advogada',
                                 max(dia, hoje), t.get('texto') or '', prazo_fatal=(venc.isoformat() if venc else None)))
        return saida('sugeridas', pf=pf_de(interno) if (venc and interno) else None)

    # 6) sem data mas e nosso (teor fora do DJEN, decisao sem teor): conferir nos autos
    if not venc or not interno:
        etapas.append(_etapa(1, TT.DILIGENCIA, 'Advogada', hoje,
                             'Conferir o teor nos autos e cadastrar o prazo, se houver. %s' % (conf.get('providencia') or '')))
        return saida('conferir')

    # 7) MEIO com prazo: Advogada peticiona e protocola
    if trilha == 'MEIO' or _MEIO_FLAVIA.search(ato):
        d1, d_prot_meio = FER.ajustar_cadeia_ferias([uteis_antes(interno, 1, uf), interno], venc, piso=hoje)
        tipo1 = TT.EMITIR_GUIA if re.search(r'CUSTAS|GUIA|DEPOSITO', ato) else TT.PETICAO_DE_MEIO
        etapas.append(_etapa(1, tipo1, 'Advogada', d1, conf.get('providencia') or ato.title(), prazo_fatal=venc.isoformat()))
        etapas.append(_etapa(2, TT.PROTOCOLO, 'Advogada', d_prot_meio,
                             'Protocolo no fatal interno %s (processual %s).' % (_br(interno), _br(venc)),
                             prazo_fatal=venc.isoformat()))
        return saida('meio', pf=pf_de(interno))

    # 8) PECA com prazo nosso: o criterio unico. Nos recursos, a cadeia e datada
    #    pelo prazo mais curto (ED em paralelo), que e o que nao perdoa.
    quem_produz = produtor['nome'].split()[0] if produtor['id'] > 0 else 'parceiro'
    venc_c, interno_c, nota, pf_par = venc, interno, '', None
    if precisa_estrategia(ato):
        for p in (m.get('prazos') or []):
            v, i = _data(p.get('vencimento')), _data(p.get('prazo_fatal_interno'))
            if p.get('paralelo') and v and v < venc_c:
                venc_c, interno_c = v, i or FER.prazo_interno(v, 2)
        if venc_c != venc:
            nota = ('Datas pelo prazo mais curto (embargos de declaracao, processual %s); se a estrategia for o '
                    'recurso direto, o processual e %s.' % (_br(venc_c), _br(venc)))
            pf_par = pf_de(interno_c, 'EMBARGOS DE DECLARACAO', venc_c, ':ed')
    etapas.extend(etapas_da_peca(ato, quem_produz, venc_c, interno_c, hoje, uf, conf.get('providencia') or '', nota=nota))
    return saida('peca', pf=pf_de(interno), pf_paralelo=pf_par)


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------

def _con(db=None):
    c = sqlite3.connect(db or _DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def garantir(c):
    cols = {r[1] for r in c.execute('PRAGMA table_info(tarefas)')}
    for nome, tipo in (('cadeia', 'TEXT'), ('etapa', 'INTEGER')):
        if nome not in cols:
            c.execute('ALTER TABLE tarefas ADD COLUMN %s %s' % (nome, tipo))
    c.execute('CREATE INDEX IF NOT EXISTS ix_tarefa_cadeia ON tarefas(cadeia, etapa)')
    c.execute('''CREATE TABLE IF NOT EXISTS revisao_fila (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, tarefa_id INTEGER NOT NULL, tarefa_peca_id INTEGER,
                   intimacao_id INTEGER, processo_numero TEXT, skill TEXT, links TEXT,
                   status TEXT DEFAULT 'aguardando', resultado TEXT, criado_em REAL, iniciado_em REAL,
                   terminado_em REAL)''')
    # a ficha da intimacao por cadeia: a intimacao some do cache dias depois de tratada e o
    # vigia da Assistente precisa de ato, prazos, partes e o texto da publicacao (06/10/2026)
    c.execute('''CREATE TABLE IF NOT EXISTS motor_fichas (
                   cadeia TEXT PRIMARY KEY, ficha TEXT, criado_em REAL)''')
    # modo 'analise' = estrategia do recurso; 'v1' = primeira versao da peca
    c.execute('''CREATE TABLE IF NOT EXISTS producao_fila (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, tarefa_id INTEGER NOT NULL, intimacao_id INTEGER,
                   processo_numero TEXT, skill TEXT, status TEXT DEFAULT 'aguardando', resultado TEXT,
                   criado_em REAL, iniciado_em REAL, terminado_em REAL, modo TEXT DEFAULT 'v1')''')
    if 'modo' not in {r[1] for r in c.execute('PRAGMA table_info(producao_fila)')}:
        c.execute("ALTER TABLE producao_fila ADD COLUMN modo TEXT DEFAULT 'v1'")
    # PF da cadeia, para marcar [ok] no protocolo (vale para 'intim:' e 'tarefa:')
    c.execute('''CREATE TABLE IF NOT EXISTS motor_pf (
                   cadeia TEXT PRIMARY KEY, evento_id TEXT, link TEXT, data TEXT, criado_em REAL)''')
    garantir_historico(c)
    c.execute('''CREATE TABLE IF NOT EXISTS sync_intim_agenda (
                   id INTEGER PRIMARY KEY, evento_id TEXT, link TEXT, data TEXT,
                   titulo TEXT, por TEXT, espelho_outlook INTEGER, criado_em REAL)''')


def garantir_historico(c):
    """Unica definicao de jur_historico (fase 2, T5, 03/11/2026). IF NOT EXISTS: banco que ja tem a tabela
    (com entidade_id INTEGER) segue como esta, sem migrar; o SQLite guarda texto de CNJ do mesmo jeito."""
    c.execute('''CREATE TABLE IF NOT EXISTS jur_historico (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, entidade TEXT, entidade_id TEXT,
                   acao TEXT, detalhe TEXT, por TEXT, em REAL)''')


def historico(c, entidade, entidade_id, acao, detalhe, por):
    garantir_historico(c)
    c.execute('INSERT INTO jur_historico (entidade, entidade_id, acao, detalhe, por, em) VALUES (?,?,?,?,?,?)',
              (entidade, str(entidade_id), acao, (detalhe or '')[:2000], por, time.time()))


def ja_cadastrado(c, intimacao_id, processo=None):
    """O que ja existe para esta intimacao: tarefas do motor/da tela e o evento do PF."""
    garantir(c)
    ref = str(int(intimacao_id))
    tarefas = [dict(r) for r in c.execute(
        "SELECT id, tipo, titulo, status, data, prazo_interno, etapa, origem FROM tarefas "
        "WHERE origem IN ('motor','intimacao') AND (origem_ref=? OR origem_ref LIKE ?) ORDER BY etapa, id", (ref, ref + ':e%'))]
    ev = c.execute('SELECT evento_id, link, data FROM sync_intim_agenda WHERE id=?', (int(intimacao_id),)).fetchone()
    return {'tarefas': tarefas, 'pf': dict(ev) if ev and ev['evento_id'] else None}


def _responsaveis_de(texto):
    out = []
    for parte in re.split(r'[,;/]| e ', str(texto or '')):
        q = quem(parte)
        if q and q['id'] not in [x['id'] for x in out]:
            out.append(q)
    return out


def _responsaveis_por_id(c, ids):
    """Advogada, 07/11/2026: na conferencia a pessoa ESCOLHE quem faz na lista da equipe (antes digitava, e um
    nome fora da EQUIPE, como a equipe, caia na Advogada). Id > 0 = usuarios; id < 0 = parceiro (-id)."""
    out = []
    for v in ids or []:
        try:
            i = int(v)
        except (TypeError, ValueError):
            continue
        if not i or i in [x['id'] for x in out]:
            continue
        try:
            if i > 0:
                r = c.execute('SELECT id, nome FROM usuarios WHERE id=?', (i,)).fetchone()
                if r:
                    out.append({'id': r[0], 'nome': r[1]})
            else:
                r = c.execute('SELECT nome FROM tarefa_parceiros WHERE id=?', (-i,)).fetchone()
                if r:
                    out.append({'id': i, 'nome': r[0]})
        except Exception:
            continue
    return out


def _na_fila(c, tarefa_id, modo):
    return c.execute('SELECT 1 FROM producao_fila WHERE tarefa_id=? AND modo=?', (tarefa_id, modo)).fetchone()


def cadastrar(c, intimacao, motor, etapas, por, criar_pf=True, agenda=None, uf='', observacao=''):
    """Grava as etapas escolhidas na tela como tarefas, o PF e os compromissos.

    `etapas`: lista no formato de cadeia_para (a tela pode ter tirado ou
    trocado responsavel/data). `agenda`: modulo agenda_google ja importado, ou
    None para nao tocar na agenda (testes). Devolve o relatorio do que fez.
    Nao marca a intimacao como tratada: isso e o router, depois de gravar.
    """
    garantir(c)
    iid = int(intimacao['id'])
    ref = str(iid)
    cadeia = 'intim:%s' % ref
    processo = intimacao.get('processo') or ''
    m = motor or {}
    agora = time.time()
    feitas, puladas, avisos = [], [], []
    cliente = (m.get('cliente') or intimacao.get('cliente') or '').strip()
    adversa = (m.get('parte_adversa') or '').strip()
    partes = ('%s x %s' % (cliente, adversa)).strip(' x') if (cliente or adversa) else ''
    c.execute('INSERT OR REPLACE INTO motor_fichas (cadeia, ficha, criado_em) VALUES (?,?,?)', (cadeia, json.dumps({
        'processo': processo, 'tribunal': intimacao.get('tribunal'), 'cliente': cliente, 'parte_adversa': adversa,
        'ato': m.get('ato'), 'titularidade': m.get('titularidade'), 'vencimento': m.get('vencimento'),
        'prazo_fatal_interno': m.get('prazo_fatal_interno'), 'prazos': m.get('prazos'),
        'providencia': (m.get('conferencia') or {}).get('providencia'),
        'publicacao': str(intimacao.get('texto') or intimacao.get('resumo') or '')[:6000]}, ensure_ascii=False), agora))
    etapas = sorted(etapas, key=lambda x: int(x.get('etapa') or 0))
    # ha etapa antes da redacao: porta de pessoa (contador) ou a ANALISE da Assistente, que ja redige
    tem_estrategia = any(_norm(x.get('tipo')) in _PORTAS + (TT.ANALISE,) for x in etapas)
    primeira = int(etapas[0].get('etapa') or 0) if etapas else 0
    # Advogada, 07/11/2026: a conferencia pode ter o mesmo tipo duas vezes (duas peticoes, por exemplo);
    # tipo repetido na lista e conferido por tipo E etapa, para a segunda nao ser "pulada"
    _vezes = {}
    for _x in etapas:
        _t = _norm(_x.get('tipo'))
        _vezes[_t] = _vezes.get(_t, 0) + 1
    _vistos = set()

    for e in etapas:
        tipo = _norm(e.get('tipo'))
        if e.get('aviso') and e['aviso'] not in avisos:
            avisos.append(e['aviso'])
        rotulo = e.get('rotulo') or tipo
        titulo = ('%s - %s' % (rotulo, partes)) if partes else rotulo
        if processo:
            titulo += ' - ' + processo
        # o indice unico e origem+origem_ref+tipo: a 1a vez do tipo usa a intimacao; as seguintes, '<intimacao>:e<etapa>'
        # (a mesma marca que ja_cadastrado procura)
        ref_t = ('%s:e%s' % (ref, int(e.get('etapa') or 0))) if (_vezes.get(tipo, 0) > 1 and tipo in _vistos) else ref
        _vistos.add(tipo)
        existe = c.execute("SELECT id FROM tarefas WHERE origem='motor' AND origem_ref=? AND tipo=?",
                           (ref_t, tipo)).fetchone()
        if existe:
            puladas.append({'tipo': tipo, 'id': existe['id'], 'motivo': 'ja existia'})
            continue
        responsaveis = _responsaveis_por_id(c, e.get('para_ids')) or _responsaveis_de(e.get('para'))
        if not responsaveis:
            avisos.append('etapa %s sem responsavel reconhecido (%s): ficou com a Advogada' % (e.get('etapa'), e.get('para')))
            responsaveis = [quem('Advogada')]
        data = _data(e.get('data')) or dt.date.today()
        descricao = (e.get('texto') or '').strip()
        descricao += '\n\nEtapa %s da cadeia %s (intimacao %s). Processo: %s' % (
            e.get('etapa'), cadeia, ref, processo or 'sem numero')
        # o que a pessoa escreveu na conferencia vai para quem executa, com o nome dela
        if str(e.get('obs') or '').strip():
            descricao += '\n\nOBSERVACAO DA CONFERENCIA (%s): %s' % (por, str(e['obs']).strip()[:1500])
        if str(observacao or '').strip():
            descricao += '\n\nOBSERVACAO GERAL DA CONFERENCIA (%s): %s' % (por, str(observacao).strip()[:1500])
        if e.get('skill'):
            descricao += '\nSkill da revisao: %s' % e['skill']
        if e.get('skill_producao'):
            descricao += '\nSkill da primeira versao: %s' % e['skill_producao']
        if e.get('skill_analise'):
            descricao += '\nSkills da analise: %s' % e['skill_analise']
        prioridade = 'importante' if int(e.get('etapa') or 0) == primeira else 'normal'
        cur = c.execute(
            "INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, cliente, parte_adversa, data, "
            "prazo_fatal, prazo_interno, trilha, prioridade, status, origem, origem_ref, criado_por, "
            "criado_em, atualizado_em, cadeia, etapa) VALUES (?,?,?,?,?,?,?,?,?,?,?,'aberta','motor',?,?,?,?,?,?)",
            (tipo, titulo, descricao, processo or None, cliente or None, adversa or None, data.isoformat(),
             e.get('prazo_fatal') or m.get('vencimento'), data.isoformat(), m.get('trilha'), prioridade, ref_t,
             por, agora, agora, cadeia, int(e.get('etapa') or 0)))
        tid = cur.lastrowid
        for r in responsaveis:
            c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) '
                      'VALUES (?,?,?,?,0)', (tid, r['id'], r['nome'], 1 if prioridade == 'importante' else 0))
        _por_plantao(c, tid, tipo)
        historico(c, 'tarefa', tid, 'criada', 'fechamento da intimacao %s, etapa %s (%s) para %s'
                  % (ref, e.get('etapa'), tipo, ', '.join(r['nome'] for r in responsaveis)), por)
        # filas da automacao: a analise entra ja; a v1 entra ja quando nao ha
        # estrategia a validar antes (nos recursos, so depois da ESTRATEGIA)
        na_fila = None
        if tipo == TT.ANALISE:
            c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, criado_em, modo) "
                      "VALUES (?,?,?,?,?,?,'analise')", (tid, iid, processo or None, e.get('skill_analise'), 'aguardando', agora))
            na_fila = 'analise'
        elif tipo == 'CALCULO DE HONORARIOS':
            c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, criado_em, modo) "
                      "VALUES (?,?,?,?,?,?,'honorarios')", (tid, iid, processo or None, 'calculo-honorarios', 'aguardando', agora))
            na_fila = 'honorarios'
        elif tipo == 'COMUNICAR CLIENTE AUDIENCIA':
            c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, criado_em, modo) "
                      "VALUES (?,?,?,?,?,?,'pop18')", (tid, iid, processo or None, 'pop-18-audiencia', 'aguardando', agora))
            na_fila = 'pop18'
        elif tipo == 'RECADO PARA PARCEIRO' and any(r['id'] == AUTOMACAO_ID for r in responsaveis):
            c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, criado_em, modo) "
                      "VALUES (?,?,?,?,?,?,'parceiro')", (tid, iid, processo or None, 'encaminhar-ao-parceiro', 'aguardando', agora))
            na_fila = 'parceiro'
        elif tipo == 'ROTEIRO':
            c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, criado_em, modo) "
                      "VALUES (?,?,?,?,?,?,'roteiro')", (tid, iid, processo or None,
                                                         'roteiro-%s' % (e.get('evento') or 'audiencia'), 'aguardando', agora))
            na_fila = 'roteiro'
        elif tipo == TT.PECA and e.get('skill_producao') and not tem_estrategia:
            c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, criado_em, modo) "
                      "VALUES (?,?,?,?,?,?,'v1')", (tid, iid, processo or None, e['skill_producao'], 'aguardando', agora))
            na_fila = 'v1'
        feitas.append({'id': tid, 'tipo': tipo, 'etapa': e.get('etapa'), 'data': data.isoformat(),
                       'responsaveis': [r['nome'] for r in responsaveis], 'titulo': titulo,
                       'parceiros': [r for r in responsaveis if r['id'] < 0], 'fila': na_fila})
    c.commit()

    # PF na agenda (marcador intim:<id>) e, nos recursos, o PF do prazo mais curto
    pf_info = None
    if criar_pf and agenda is not None and m.get('prazo_fatal_interno') and m.get('vencimento'):
        def marcar(marcador, ato_pf, dia, venc_pf):
            ja = agenda.achar_por_marcador(marcador)
            if ja and (ja.get('extendedProperties') or {}).get('private', {}).get('esc', marcador) == marcador:
                return {'evento_id': ja.get('id'), 'link': ja.get('htmlLink'), 'ja_existia': True}
            titulo_pf = agenda.titulo_pf(ato_pf or 'PRAZO', cliente or 'CLIENTE', adversa or 'PARTE ADVERSA', processo)
            desc = ('PRAZO FATAL INTERNO (data deste evento): %s\nPrazo processual: %s\n%s\nProcesso: %s\n'
                    'Tarefas no Sistema Escritório Modelo: https://sistema.escritorio.adv.br/tarefas'
                    % (_br(dia), _br(venc_pf), (m.get('conferencia') or {}).get('providencia') or '', processo))
            ev = agenda.criar_prazo(titulo_pf, str(dia)[:10], desc, agenda.convidados_para(m.get('trilha')), None,
                                    marcador, '', processo)
            return {'evento_id': ev.get('id'), 'link': ev.get('htmlLink'), 'ja_existia': False}
        try:
            pf_info = marcar(cadeia, m.get('ato'), m['prazo_fatal_interno'], m['vencimento'])
            c.execute('INSERT OR REPLACE INTO sync_intim_agenda (id, evento_id, link, data, titulo, por, '
                      'espelho_outlook, criado_em) VALUES (?,?,?,?,?,?,0,?)',
                      (iid, pf_info['evento_id'], pf_info.get('link'), str(m['prazo_fatal_interno'])[:10],
                       'PF ' + (m.get('ato') or ''), por, agora))
            c.execute('INSERT OR REPLACE INTO motor_pf (cadeia, evento_id, link, data, criado_em) VALUES (?,?,?,?,?)',
                      (cadeia, pf_info['evento_id'], pf_info.get('link'), str(m['prazo_fatal_interno'])[:10], agora))
            par = cadeia_para(m, intimacao, uf=uf).get('pf_paralelo')
            if par:
                p2 = marcar(par['marcador'], par['ato'], par['data'], par['vencimento'])
                pf_info['paralelo'] = p2
            c.commit()
        except Exception as e:  # a agenda e um extra: a tarefa ja esta gravada
            avisos.append('PF nao foi para a agenda: %s' % str(e)[:160])
    # compromisso de cada etapa na agenda de quem executa (regra da Advogada, 28/09:
    # toda tarefa aberta tem o seu compromisso). O PROTOCOLO usa o proprio PF e
    # as etapas da automacao nao ocupam a agenda de ninguem.
    if agenda is not None:
        try:
            from . import tarefas_prazo as TP
            for f in feitas:
                if _norm(f['tipo']) == TT.PROTOCOLO:
                    continue
                emails = [r['email'] for r in c.execute(
                    'SELECT u.email FROM tarefa_responsaveis r JOIN usuarios u ON u.id=r.usuario_id '
                    'WHERE r.tarefa_id=? AND r.usuario_id>0 AND r.usuario_id != ?', (f['id'], AUTOMACAO_ID))]
                if not emails:
                    continue
                t = dict(c.execute('SELECT * FROM tarefas WHERE id=?', (f['id'],)).fetchone())
                ev, erro = TP.marcar_na_agenda(t, emails)
                if ev:
                    c.execute('UPDATE tarefas SET evento_id=? WHERE id=?', (ev, f['id']))
                elif erro and erro != 'sem prazo interno':
                    avisos.append('compromisso da etapa %s: %s' % (f['etapa'], erro))
            c.commit()
        except Exception as e:
            avisos.append('compromissos das etapas: %s' % str(e)[:160])
    return {'tarefas': feitas, 'puladas': puladas, 'pf': pf_info, 'avisos': avisos, 'cadeia': cadeia}


# ---------------------------------------------------------------------------
# Passagem de etapa: chamado quando uma tarefa da cadeia e concluida
# ---------------------------------------------------------------------------

def _peca_escolhida(descricao):
    """A linha 'PECA: ...' da tarefa de estrategia, normalizada; None se nao houver."""
    m = re.search(r'^\s*PE[CÇ]A\s*:\s*(.+)$', str(descricao or ''), flags=re.M | re.I)
    return _norm(m.group(1)) if m else None


def ao_concluir(c, tarefa_id, por, agenda=None):
    """Depois que a tarefa foi concluida: deixa a etapa seguinte na vez; valida a
    estrategia e enfileira a redacao; enfileira a revisao final quando a pessoa
    termina a revisao da v1; marca [ok] no PF e abre ATUALIZAR CLIENTE quando o
    PROTOCOLO termina. Nunca conclui nada. Devolve o que fez."""
    garantir(c)
    t = c.execute('SELECT * FROM tarefas WHERE id=?', (tarefa_id,)).fetchone()
    if not t or not t['cadeia'] or t['status'] != 'concluida':
        return None
    t = dict(t)
    tipo_c = _norm(t['tipo'])
    de_intim = t['cadeia'].startswith('intim:')
    iid = t['cadeia'].split(':')[-1]
    ref = iid if de_intim else t['cadeia']           # o origem_ref das tarefas desta cadeia
    feito = {'cadeia': t['cadeia'], 'etapa': t['etapa'], 'proxima': None, 'producao_fila': None,
             'revisao_fila': None, 'pf_ok': None, 'atualizar_cliente': None, 'decisao_advogada': None}
    prox = c.execute("SELECT * FROM tarefas WHERE cadeia=? AND etapa>? AND status='aberta' "
                     "ORDER BY etapa LIMIT 1", (t['cadeia'], int(t['etapa'] or 0))).fetchone()
    agora = time.time()
    if prox:
        prox = dict(prox)
        c.execute("UPDATE tarefas SET prioridade='importante', atualizado_em=? WHERE id=?", (agora, prox['id']))
        c.execute('UPDATE tarefa_responsaveis SET importante=1 WHERE tarefa_id=?', (prox['id'],))
        nomes = ', '.join(r[0] for r in c.execute(
            'SELECT nome FROM tarefa_responsaveis WHERE tarefa_id=?', (prox['id'],)))
        historico(c, 'tarefa', prox['id'], 'na-vez',
                  'etapa %s (%s) concluida por %s; etapa %s (%s) na vez de %s'
                  % (t['etapa'], t['tipo'], por, prox['etapa'], prox['tipo'], nomes), por)
        feito['proxima'] = {'id': prox['id'], 'tipo': prox['tipo'], 'etapa': prox['etapa'], 'responsaveis': nomes}

    # A Advogada concluiu a revisao da estrategia e da peca (recursos). Tres caminhos:
    #   REFAZER: <orientacao>  -> a Assistente refaz; a tarefa da Advogada volta a ficar aberta, nada segue adiante
    #   PECA: NAO RECORRER      -> nada e redigido nem revisado; a decisao vai para a Dra. Advogada
    #   (nenhum dos dois)       -> segue para a revisao final da controladoria
    refazer = nao_recorrer = False
    veio_de_analise = tipo_c == TT.PECA and c.execute(
        "SELECT 1 FROM tarefas WHERE cadeia=? AND tipo=?", (t['cadeia'], TT.ANALISE)).fetchone()
    if veio_de_analise:
        desc = str(t.get('descricao') or '')
        mref = re.search(r'^\s*REFAZER\b\s*[:\-]?\s*(.*)$', desc, flags=re.M | re.I)
        escolha = _peca_escolhida(desc)
        if mref:
            refazer = True
            orientacao = mref.group(1).strip()[:1500] or 'refazer conforme a orientacao da tarefa'
            ms = re.search(r'Skill da primeira versao: ([\w-]+)', ' '.join(
                r[0] or '' for r in c.execute("SELECT descricao FROM tarefas WHERE cadeia=? AND tipo=?", (t['cadeia'], TT.ANALISE))))
            skill = skill_de_producao(escolha) or (ms.group(1) if ms else None)
            c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, resultado, criado_em, modo) "
                      "VALUES (?,?,?,?,?,?,?,'v1')", (t['id'], int(iid) if (de_intim and iid.isdigit()) else None,
                                                    t.get('processo_numero'), skill or 'sem-skill', 'aguardando',
                                                    json.dumps({'refazer': orientacao, 'pedido_por': por}, ensure_ascii=False), agora))
            # a etapa nao terminou: a tarefa volta a ficar aberta, esperando a nova versao
            c.execute("UPDATE tarefas SET status='aberta', concluida_em=NULL, atualizado_em=?, descricao=? WHERE id=?",
                      (agora, re.sub(r'^\s*REFAZER\b.*$', 'REFAZER PEDIDO por %s em %s: %s (aguardando a nova versao da Assistente)'
                                     % (por, dt.datetime.now().strftime('%d/%m %H:%M'), orientacao), desc, count=1, flags=re.M | re.I),
                       t['id']))
            c.execute('UPDATE tarefa_responsaveis SET concluido_em=NULL WHERE tarefa_id=? AND usuario_id != ?', (t['id'], AUTOMACAO_ID))
            if prox:
                c.execute("UPDATE tarefas SET prioridade='normal' WHERE id=?", (prox['id'],))
            historico(c, 'tarefa', t['id'], 'refazer', '%s pediu para refazer: %s' % (por, orientacao), por)
            feito['refazer'] = orientacao
            feito['proxima'] = None
            feito['producao_fila'] = {'tarefa': t['id'], 'skill': skill, 'status': 'aguardando', 'refazer': orientacao}
        elif escolha and 'NAO RECORRER' in escolha:
            nao_recorrer = True
            if not c.execute("SELECT 1 FROM tarefas WHERE origem='motor' AND origem_ref=? AND tipo='DECISAO DA ESTRATEGIA'",
                             (ref,)).fetchone():
                cur = c.execute(
                    "INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, cliente, parte_adversa, data, prazo_fatal, "
                    "prazo_interno, trilha, prioridade, status, origem, origem_ref, criado_por, criado_em, atualizado_em, cadeia, etapa) "
                    "VALUES ('DECISAO DA ESTRATEGIA', ?, ?, ?, ?, ?, ?, ?, ?, 'PECA', 'urgente', 'aberta', 'motor', ?, ?, ?, ?, ?, ?)",
                    ('DECISAO: nao recorrer - ' + (t.get('titulo') or '').split(' - ', 1)[-1],
                     'A estrategia ficou em NAO RECORRER (revisada por %s). Confirmar a decisao; confirmada, cancelar as etapas '
                     'seguintes da cadeia %s. Nada foi cancelado nem enfileirado.\n\n%s' % (por, t['cadeia'], desc[:3000]),
                     t.get('processo_numero'), t.get('cliente'), t.get('parte_adversa'), dt.date.today().isoformat(),
                     t.get('prazo_fatal'), dt.date.today().isoformat(), ref, por, agora, agora, t['cadeia'],
                     int(t['etapa'] or 0)))
                pam = quem('Advogada')
                c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) '
                          'VALUES (?,?,?,1,1)', (cur.lastrowid, pam['id'], pam['nome']))
                feito['decisao_advogada'] = cur.lastrowid

    # a Advogada validou o que a audiencia gerou: cada providencia que ficou vira tarefa
    if tipo_c == 'VALIDAR PROVIDENCIAS DA AUDIENCIA':
        feito['providencias'] = _abrir_providencias_validadas(c, t, por)

    # o calculo voltou do contador (ou a pessoa disse que nao precisou): a redacao entra na fila
    if tipo_c == TT.ENVIAR_AO_CONTADOR and prox and _norm(prox['tipo']) == TT.PECA:
        links = [u for u in _LINK.findall(t.get('descricao') or '') if 'sistema.escritorio.adv.br' not in u]
        if links:
            c.execute("UPDATE tarefas SET descricao=COALESCE(descricao,'') || ? WHERE id=?",
                      ('\n\nCalculo do contador: ' + '\n'.join(links), prox['id']))
        ms = re.search(r'Skill da primeira versao: ([\w-]+)', prox.get('descricao') or '')
        skill = ms.group(1) if ms else None
        if skill and not _na_fila(c, prox['id'], 'v1'):
            c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, criado_em, modo) "
                      "VALUES (?,?,?,?,?,?,'v1')", (prox['id'], int(iid) if (de_intim and iid.isdigit()) else None,
                                                    t.get('processo_numero'), skill, 'aguardando', agora))
        feito['producao_fila'] = {'tarefa': prox['id'], 'skill': skill, 'calculo_do_contador': links,
                                  'status': 'aguardando' if skill else 'sem-skill: a pessoa produz'}

    # a pessoa terminou a revisao da v1: a revisao final da controladoria entra na fila.
    # Vale pelo que vem DEPOIS (a etapa REVISAO), nao pelo tipo da tarefa concluida:
    # nas razoes finais a etapa 1 e a propria tarefa cadastrada (tipo RAZOES FINAIS).
    if prox and _norm(prox['tipo']) == TT.REVISAO and tipo_c not in (TT.ANALISE,) + _PORTAS and not refazer and not nao_recorrer:
        todos = _LINK.findall(t.get('descricao') or '')
        links = [u for u in todos if 'sistema.escritorio.adv.br' not in u]
        if links:
            c.execute("UPDATE tarefas SET descricao=COALESCE(descricao,'') || ? WHERE id=?",
                      ('\n\nPeca para revisar: ' + '\n'.join(links), prox['id']))
        ms = re.search(r'Skill da revisao: (\S+)', prox.get('descricao') or '')
        skill = ms.group(1) if ms else skill_de_revisao(t['tipo'])
        c.execute('INSERT INTO revisao_fila (tarefa_id, tarefa_peca_id, intimacao_id, processo_numero, skill, '
                  'links, status, criado_em) VALUES (?,?,?,?,?,?,?,?)',
                  (prox['id'], t['id'], int(iid) if (de_intim and iid.isdigit()) else None, t.get('processo_numero'),
                   skill, json.dumps(links), 'aguardando' if links else 'sem-link', agora))
        feito['revisao_fila'] = {'skill': skill, 'links': links, 'status': 'aguardando' if links else 'sem-link'}

    if tipo_c == TT.PROTOCOLO:
        ev = c.execute('SELECT evento_id FROM motor_pf WHERE cadeia=?', (t['cadeia'],)).fetchone()
        if not ev and de_intim and iid.isdigit():
            ev = c.execute('SELECT evento_id FROM sync_intim_agenda WHERE id=?', (int(iid),)).fetchone()
        if ev and ev['evento_id'] and agenda is not None:
            try:
                agenda.marcar_cumprido(ev['evento_id'], True)
                feito['pf_ok'] = ev['evento_id']
                historico(c, 'cadeia', t['cadeia'], 'pf-ok', 'PF marcado [ok] na agenda apos o protocolo por %s' % por, por)
            except Exception as e:
                feito['pf_ok'] = 'falhou: %s' % str(e)[:120]
        # depois do protocolo: a ATUALIZACAO DO CLIENTE e feita pela automacao (Advogada, 05/10/2026)
        # e a Advogada ganha o acompanhamento para fiscalizar se saiu e se saiu certa
        atual = abrir_atualizacao_do_cliente(
            c, t['cadeia'], ref, 'o protocolo de %s' % (t.get('titulo') or 'peca').split(' - ')[0].lower(),
            t.get('processo_numero'), t.get('cliente'), t.get('parte_adversa'), int(t['etapa'] or 0) + 1, por)
        if atual:
            feito['atualizar_cliente'] = atual['atualizar']
            feito['fiscalizar_atualizacao'] = atual['fiscalizar']
    c.commit()
    return feito


# ---------------------------------------------------------------------------
# Peca que nasce de TAREFA cadastrada, e nao de intimacao (razoes finais)
#
# Advogada, 05/10/2026: o prazo de razoes finais por memoriais e deferido na ata da
# audiencia de instrucao. A tarefa e cadastrada depois da ata, com o prazo; a
# automacao varre as tarefas, acha a peca a fazer e monta em volta dela a
# MESMA cadeia de toda peca.
# ---------------------------------------------------------------------------

def varrer_tarefas_para_producao(c, agenda=None, hoje=None, uf=''):
    """Acha tarefa aberta de razoes finais ainda sem cadeia, monta as etapas
    seguintes em volta dela, poe a v1 na fila e marca o PF. So prepara: nao
    produz e nao conclui. Chamado pelo robo das filas a cada rodada."""
    garantir(c)
    hoje = hoje or dt.date.today()
    agora = time.time()
    novas = []
    # so o que nasceu AQUI: tarefa do espelho do ADVBOX ja esta em andamento por la e nao ganha cadeia
    linhas = c.execute("SELECT * FROM tarefas WHERE status='aberta' AND (cadeia IS NULL OR cadeia='') "
                       "AND COALESCE(origem,'') != 'advbox'").fetchall()
    for linha in linhas:
        t = dict(linha)
        tipo = _norm(t['tipo'])
        if not _NASCE_DE_TAREFA.search(tipo) or _na_fila(c, t['id'], 'v1'):
            continue
        cadeia = 'tarefa:%d' % t['id']
        venc = _data(t.get('prazo_fatal')) or _data(t.get('data'))
        interno = _data(t.get('prazo_interno')) if _data(t.get('prazo_fatal')) else None
        if venc and (not interno or interno > venc):
            interno = FER.prazo_interno(venc, 2)
        if not venc:
            interno = hoje
        resp = [r['nome'] for r in c.execute(
            'SELECT nome FROM tarefa_responsaveis WHERE tarefa_id=? AND usuario_id != ?', (t['id'], AUTOMACAO_ID))]
        dono = quem(resp[0]) if resp else None
        if not dono or dono['id'] == AUTOMACAO_ID:
            dono = quem('Advogada')
            c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) '
                      'VALUES (?,?,?,1,0)', (t['id'], dono['id'], dono['nome']))
        quem_produz = dono['nome'].split()[0] if dono['id'] > 0 else 'parceiro'
        etapas = etapas_da_peca(tipo, quem_produz, venc, interno, hoje, uf, com_estrategia=False)
        e1 = etapas[0]
        skill = e1.get('skill_producao')        # sem skill de redacao (quesitos, laudo): a pessoa produz a etapa 1
        c.execute("UPDATE tarefas SET cadeia=?, etapa=1, descricao=COALESCE(descricao,'') || ?, atualizado_em=? WHERE id=?",
                  (cadeia, '\n\n' + e1['texto'] + (('\nSkill da primeira versao: %s' % skill) if skill else ''), agora, t['id']))
        criadas = []
        partes = (t.get('titulo') or '').split(' - ', 1)[-1] if ' - ' in (t.get('titulo') or '') else (t.get('cliente') or '')
        for e in etapas[1:]:
            tipo_e = _norm(e['tipo'])
            if c.execute("SELECT 1 FROM tarefas WHERE origem='motor' AND origem_ref=? AND tipo=?", (cadeia, tipo_e)).fetchone():
                continue
            desc = e['texto'] + '\n\nEtapa %s da cadeia %s. Processo: %s' % (
                e['etapa'], cadeia, t.get('processo_numero') or 'sem numero')
            if e.get('skill'):
                desc += '\nSkill da revisao: %s' % e['skill']
            cur = c.execute(
                "INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, cliente, parte_adversa, data, "
                "prazo_fatal, prazo_interno, trilha, prioridade, status, origem, origem_ref, criado_por, "
                "criado_em, atualizado_em, cadeia, etapa) VALUES (?,?,?,?,?,?,?,?,?,'PECA','normal','aberta','motor',?,?,?,?,?,?)",
                (tipo_e, ('%s - %s' % (e.get('rotulo') or tipo_e, partes)).strip(' -'), desc, t.get('processo_numero'),
                 t.get('cliente'), t.get('parte_adversa'), e['data'], venc.isoformat() if venc else None, e['data'],
                 cadeia, 'motor das tarefas', agora, agora, cadeia, int(e['etapa'])))
            for r in _responsaveis_de(e['para']) or [quem('Advogada')]:
                c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) '
                          'VALUES (?,?,?,0,0)', (cur.lastrowid, r['id'], r['nome']))
            _por_plantao(c, cur.lastrowid, tipo_e)
            criadas.append({'id': cur.lastrowid, 'tipo': tipo_e, 'etapa': e['etapa'], 'data': e['data'], 'para': e['para']})
        c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, criado_em, modo) "
                  "VALUES (?,?,?,?,?,?,'v1')", (t['id'], None, t.get('processo_numero'), skill or 'sem-skill',
                                                'aguardando' if skill else 'sem-skill', agora))
        historico(c, 'tarefa', t['id'], 'cadeia-montada',
                  'varredura: %s virou a etapa 1 da cadeia %s; v1 na fila (skill %s); etapas %s'
                  % (t['tipo'], cadeia, skill, ', '.join('#%s %s' % (x['id'], x['tipo']) for x in criadas)), 'motor das tarefas')
        pf = None
        if agenda is not None and venc:
            try:
                ev = agenda.achar_por_marcador(cadeia)
                if not ev:
                    titulo_pf = agenda.titulo_pf(tipo, t.get('cliente') or 'CLIENTE',
                                                 t.get('parte_adversa') or 'PARTE ADVERSA', t.get('processo_numero') or '')
                    desc = ('PRAZO FATAL INTERNO (data deste evento): %s\nPrazo processual: %s\nProcesso: %s\n'
                            'Tarefas no Sistema Escritório Modelo: https://sistema.escritorio.adv.br/tarefas'
                            % (_br(interno), _br(venc), t.get('processo_numero') or ''))
                    ev = agenda.criar_prazo(titulo_pf, interno.isoformat(), desc, agenda.convidados_para('PECA'), None,
                                            cadeia, '', t.get('processo_numero') or '')
                c.execute('INSERT OR REPLACE INTO motor_pf (cadeia, evento_id, link, data, criado_em) VALUES (?,?,?,?,?)',
                          (cadeia, ev.get('id'), ev.get('htmlLink'), interno.isoformat(), agora))
                pf = ev.get('id')
            except Exception as e:
                pf = 'falhou: %s' % str(e)[:120]
        novas.append({'id': t['id'], 'tipo': t['tipo'], 'processo': t.get('processo_numero'), 'skill': skill,
                      'cadeia': cadeia, 'etapas': criadas, 'pf': pf, 'sem_prazo': not venc})
    c.commit()
    return novas


# ---------------------------------------------------------------------------
# O que o robo das filas chama quando termina cada trabalho
# ---------------------------------------------------------------------------

def _bloco_links(links):
    rotulos = [('analise', 'Analise da decisao'), ('peca', 'Peca (DOCX timbrado)'), ('peca_editavel', 'Peca (EDITAVEL)'),
               ('parecer', 'Parecer da controladoria sobre a v1'),
               ('peca_com_ajustes', 'Peca com os ajustes marcados (DOCX)'),
               ('peca_com_ajustes_editavel', 'Peca com os ajustes marcados (EDITAVEL)'),
               ('relatorio', 'Relatorio da controladoria (DOCX)'),
               ('relatorio_editavel', 'Relatorio da controladoria (EDITAVEL)')]
    conhecidos = dict(rotulos)
    linhas = ['%s: %s' % (rot, links[k]) for k, rot in rotulos if (links or {}).get(k)]
    linhas += ['%s: %s' % (k, v) for k, v in (links or {}).items() if k not in conhecidos and v]
    return '\n'.join(linhas) or 'sem links'


def _fechar_a_propria(c, tarefa_id, por, motivo, agenda=None):
    """A automacao fecha a PROPRIA tarefa (so quando nao ha pessoa pendente nela)
    e passa a vez. Devolve a passagem, ou None se a tarefa tem gente e fica aberta."""
    agora = time.time()
    de_gente = c.execute('SELECT COUNT(*) FROM tarefa_responsaveis WHERE tarefa_id=? AND usuario_id != ? '
                         'AND concluido_em IS NULL', (tarefa_id, AUTOMACAO_ID)).fetchone()[0]
    if de_gente:
        # a parte da automacao fica marcada (quem olha a tarefa ve "a Assistente ja fez a dela");
        # a tarefa segue aberta ate a pessoa de plantao concluir a dela (06/10/2026)
        c.execute('UPDATE tarefa_responsaveis SET concluido_em=? WHERE tarefa_id=? AND usuario_id=? AND concluido_em IS NULL',
                  (agora, tarefa_id, AUTOMACAO_ID))
        historico(c, 'tarefa', tarefa_id, 'entregue', motivo + '; a tarefa tem responsavel humano e fica aberta', por)
        c.commit()
        return None
    c.execute('UPDATE tarefa_responsaveis SET concluido_em=? WHERE tarefa_id=? AND concluido_em IS NULL',
              (agora, tarefa_id))
    c.execute("UPDATE tarefas SET status='concluida', concluida_em=?, atualizado_em=? WHERE id=?",
              (agora, agora, tarefa_id))
    historico(c, 'tarefa', tarefa_id, 'concluida-pela-automacao', motivo + '; a automacao fechou a propria tarefa', por)
    c.commit()
    return ao_concluir(c, tarefa_id, por, agenda) or {}


def entregar_analise(c, tarefa_analise_id, estrategia, peca_sugerida, links=None, por=AUTOMACAO, agenda=None):
    """A Assistente terminou a analise, definiu a estrategia e redigiu a peca: tudo entra na
    tarefa de revisao da Advogada, com a linha PECA no topo, a estrategia adotada e o porque, e
    os links. A Assistente fecha a propria tarefa. Quem decide como seguir e a Advogada."""
    garantir(c)
    agora = time.time()
    t = c.execute('SELECT * FROM tarefas WHERE id=?', (tarefa_analise_id,)).fetchone()
    if not t or _norm(t['tipo']) != TT.ANALISE or not t['cadeia']:
        return None
    seg = c.execute("SELECT * FROM tarefas WHERE cadeia=? AND etapa>? ORDER BY etapa LIMIT 1",
                    (t['cadeia'], int(t['etapa'] or 0))).fetchone()
    topo = ('PECA: %s\n(troque a linha acima se preferir outra peca; para a Assistente refazer, escreva uma linha REFAZER: e a '
            'orientacao)\n\nESTRATEGIA ADOTADA PELA CLAUDIA E POR QUE:\n%s\n\n%s\n\n'
            % (_norm(peca_sugerida) or 'A DEFINIR', (estrategia or '').strip(),
               ('PRIMEIRA VERSAO E ANALISE:\n' + _bloco_links(links)) if links else 'Sem peca anexada.'))
    nova = None
    if seg:
        c.execute("UPDATE tarefas SET descricao=? || COALESCE(descricao,''), prioridade='importante', atualizado_em=? WHERE id=?",
                  (topo, agora, seg['id']))
        c.execute('UPDATE tarefa_responsaveis SET importante=1 WHERE tarefa_id=?', (seg['id'],))
    else:
        # passo a passo (07/10/2026): a revisao da Advogada nasce agora, com a sintese antes dos links
        lista = [v for k, v in (links or {}).items() if v and 'pasta' not in k] if isinstance(links, dict) else list(links or [])
        nova = nascer_proxima(c, dict(t), por, lista, bloco=topo.strip(), bloco_primeiro=True)
        if nova:
            seg = c.execute('SELECT * FROM tarefas WHERE id=?', (nova['id'],)).fetchone()
    c.execute("UPDATE producao_fila SET status='entregue', terminado_em=?, resultado=? WHERE tarefa_id=? AND modo='analise'",
              (agora, json.dumps({'peca_sugerida': peca_sugerida, 'links': links}, ensure_ascii=False), tarefa_analise_id))
    passagem = _fechar_a_propria(c, tarefa_analise_id, por, 'analise, estrategia e primeira versao entregues (peca: %s)'
                                 % (peca_sugerida or 'a definir'), agenda)
    return {'revisao_da_estrategia_em': seg['id'] if seg else None, 'passagem': passagem, 'fechou_a_propria': passagem is not None}


def abrir_tarefas_de_alvara(c, processo, evidencia, cliente='', adversa='', por='motor das tarefas', hoje=None, uf=''):
    """O vigia dos autos achou alvara ou valor liberado: abre o calculo previo dos
    honorarios (automacao) e a tarefa da Titular. Uma vez por processo e data."""
    garantir(c)
    hoje = hoje or dt.date.today()
    agora = time.time()
    cadeia = 'alvara:%s:%s' % (processo, (evidencia or {}).get('data') or 'sd')
    if c.execute('SELECT 1 FROM tarefas WHERE cadeia=?', (cadeia,)).fetchone():
        return None
    onde = '%s, juntado em %s' % ((evidencia or {}).get('nome') or 'alvara', _br((evidencia or {}).get('data')) or 'data nao informada')
    partes = ('%s x %s' % (cliente or '', adversa or '')).strip(' x')
    criadas = []
    for e in etapas_do_alvara(hoje, onde, uf):
        tipo = _norm(e['tipo'])
        titulo = ' - '.join(x for x in (e['rotulo'], partes, processo) if x)
        cur = c.execute(
            "INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, cliente, parte_adversa, data, prazo_interno, "
            "trilha, prioridade, status, origem, origem_ref, criado_por, criado_em, atualizado_em, cadeia, etapa) "
            "VALUES (?,?,?,?,?,?,?,?,'MEIO',?,'aberta','motor',?,?,?,?,?,?)",
            (tipo, titulo, e['texto'] + '\n\nEtapa %s da cadeia %s. Processo: %s' % (e['etapa'], cadeia, processo),
             processo, cliente or None, adversa or None, e['data'], e['data'],
             'importante' if e['etapa'] == 1 else 'normal', cadeia, por, agora, agora, cadeia, e['etapa']))
        r = quem(e['para'])
        c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) '
                  'VALUES (?,?,?,0,0)', (cur.lastrowid, r['id'], r['nome']))
        if tipo == 'CALCULO DE HONORARIOS':
            c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, criado_em, modo) "
                      "VALUES (?,?,?,?,?,?,'honorarios')", (cur.lastrowid, None, processo, 'calculo-honorarios', 'aguardando', agora))
        _por_plantao(c, cur.lastrowid, tipo)
        criadas.append({'id': cur.lastrowid, 'tipo': tipo, 'para': r['nome'], 'data': e['data']})
    historico(c, 'processo', processo, 'alvara', 'vigia dos autos: %s; tarefas %s'
              % (onde, ', '.join('#%s %s' % (x['id'], x['tipo']) for x in criadas)), por)
    c.commit()
    return {'cadeia': cadeia, 'tarefas': criadas}


def entregar_calculo_honorarios(c, tarefa_calculo_id, calculo, links=None, por=AUTOMACAO):
    """O calculo previo dos honorarios ficou pronto: entra na tarefa da Titular, que
    valida, lanca e paga o cliente. A automacao fecha a propria tarefa de calculo."""
    garantir(c)
    agora = time.time()
    t = c.execute('SELECT * FROM tarefas WHERE id=?', (tarefa_calculo_id,)).fetchone()
    if not t or _norm(t['tipo']) != 'CALCULO DE HONORARIOS' or not t['cadeia']:
        return None
    seg = c.execute("SELECT * FROM tarefas WHERE cadeia=? AND etapa>? ORDER BY etapa LIMIT 1",
                    (t['cadeia'], int(t['etapa'] or 0))).fetchone()
    if seg:
        c.execute("UPDATE tarefas SET descricao=COALESCE(descricao,'') || ?, atualizado_em=? WHERE id=?",
                  ('\n\nCALCULO PREVIO DOS HONORARIOS (automacao; validar antes de lancar):\n%s%s'
                   % ((calculo or '').strip(), ('\n' + _bloco_links(links)) if links else ''), agora, seg['id']))
    c.execute("UPDATE producao_fila SET status='entregue', terminado_em=?, resultado=? WHERE tarefa_id=? AND modo='honorarios'",
              (agora, json.dumps({'calculo': calculo, 'links': links}, ensure_ascii=False), tarefa_calculo_id))
    passagem = _fechar_a_propria(c, tarefa_calculo_id, por, 'calculo previo dos honorarios entregue')
    return {'receita_em': seg['id'] if seg else None, 'passagem': passagem, 'fechou_a_propria': passagem is not None}


def entregar_pop18(c, tarefa_id, relato, por=AUTOMACAO):
    """A automacao comunicou o cliente pelo POP 18: o relato (o que foi, para quem e
    quando) entra na tarefa de fiscalizacao da Advogada e a automacao fecha a propria."""
    garantir(c)
    agora = time.time()
    t = c.execute('SELECT * FROM tarefas WHERE id=?', (tarefa_id,)).fetchone()
    if not t or _norm(t['tipo']) != 'COMUNICAR CLIENTE AUDIENCIA' or not t['cadeia']:
        return None
    acomp = c.execute("SELECT id FROM tarefas WHERE cadeia=? AND tipo=? ORDER BY etapa LIMIT 1",
                      (t['cadeia'], TT.ACOMPANHAMENTO)).fetchone()
    if acomp:
        c.execute("UPDATE tarefas SET descricao=COALESCE(descricao,'') || ?, atualizado_em=? WHERE id=?",
                  ('\n\nO QUE A AUTOMACAO ENVIOU (POP 18):\n' + (relato or '').strip(), agora, acomp['id']))
    c.execute("UPDATE producao_fila SET status='entregue', terminado_em=?, resultado=? WHERE tarefa_id=? AND modo='pop18'",
              (agora, json.dumps({'relato': relato}, ensure_ascii=False), tarefa_id))
    passagem = _fechar_a_propria(c, tarefa_id, por, 'POP 18 comunicado ao cliente')
    return {'fiscalizacao_em': acomp['id'] if acomp else None, 'passagem': passagem, 'fechou_a_propria': passagem is not None}


def entregar_roteiro(c, tarefa_id, pontos_de_atencao, links=None, por=AUTOMACAO):
    """O roteiro da audiencia ou da sustentacao ficou pronto: o link e os pontos de
    atencao entram na tarefa de estudo da Advogada e a automacao fecha a propria."""
    garantir(c)
    agora = time.time()
    t = c.execute('SELECT * FROM tarefas WHERE id=?', (tarefa_id,)).fetchone()
    if not t or _norm(t['tipo']) != 'ROTEIRO' or not t['cadeia']:
        return None
    estudo = c.execute("SELECT id FROM tarefas WHERE cadeia=? AND tipo='ESTUDAR ROTEIRO' ORDER BY etapa LIMIT 1",
                       (t['cadeia'],)).fetchone()
    if estudo:
        c.execute("UPDATE tarefas SET descricao=COALESCE(descricao,'') || ?, prioridade='importante', atualizado_em=? WHERE id=?",
                  ('\n\nROTEIRO PRONTO (automacao):\n%s\n\nPONTOS DE ATENCAO:\n%s'
                   % (_bloco_links(links), (pontos_de_atencao or '').strip()), agora, estudo['id']))
        c.execute('UPDATE tarefa_responsaveis SET importante=1 WHERE tarefa_id=?', (estudo['id'],))
    c.execute("UPDATE producao_fila SET status='entregue', terminado_em=?, resultado=? WHERE tarefa_id=? AND modo='roteiro'",
              (agora, json.dumps({'links': links, 'pontos': pontos_de_atencao}, ensure_ascii=False), tarefa_id))
    passagem = _fechar_a_propria(c, tarefa_id, por, 'roteiro entregue para estudo')
    return {'estudo_em': estudo['id'] if estudo else None, 'passagem': passagem, 'fechou_a_propria': passagem is not None}


def entregar_recado_parceiro(c, tarefa_id, relato, por=AUTOMACAO):
    """A automacao encaminhou a intimacao ao parceiro: o relato entra na tarefa de
    fiscalizacao da Advogada e a automacao fecha a propria. A confirmacao do parceiro
    chega depois, pelo vigia das respostas (registrar_resposta)."""
    garantir(c)
    agora = time.time()
    t = c.execute('SELECT * FROM tarefas WHERE id=?', (tarefa_id,)).fetchone()
    if not t or _norm(t['tipo']) != 'RECADO PARA PARCEIRO' or not t['cadeia']:
        return None
    acomp = c.execute("SELECT id FROM tarefas WHERE cadeia=? AND tipo=? ORDER BY etapa LIMIT 1",
                      (t['cadeia'], TT.ACOMPANHAMENTO)).fetchone()
    if acomp:
        c.execute("UPDATE tarefas SET descricao=COALESCE(descricao,'') || ?, atualizado_em=? WHERE id=?",
                  ('\n\nO QUE A AUTOMACAO ENVIOU AO PARCEIRO:\n' + (relato or '').strip(), agora, acomp['id']))
    c.execute("UPDATE producao_fila SET status='entregue', terminado_em=?, resultado=? WHERE tarefa_id=? AND modo='parceiro'",
              (agora, json.dumps({'relato': relato}, ensure_ascii=False), tarefa_id))
    passagem = _fechar_a_propria(c, tarefa_id, por, 'intimacao encaminhada ao parceiro')
    return {'fiscalizacao_em': acomp['id'] if acomp else None, 'passagem': passagem, 'fechou_a_propria': passagem is not None}


def registrar_resposta(c, cadeia, autor, texto, quando=None, por='vigia das respostas'):
    """Resposta de cliente, testemunha ou parceiro: vai para a tarefa de fiscalizacao."""
    return registrar_resposta_audiencia(c, cadeia, autor, texto, quando, por)


def registrar_resposta_audiencia(c, cadeia, autor, texto, quando=None, por='vigia das respostas'):
    """O vigia das respostas achou devolutiva do cliente ou da testemunha: escreve na
    tarefa de fiscalizacao da Advogada e a deixa importante. So registra; quem conclui e ela."""
    garantir(c)
    agora = time.time()
    acomp = c.execute("SELECT id, status FROM tarefas WHERE cadeia=? AND tipo=? ORDER BY etapa LIMIT 1",
                      (cadeia, TT.ACOMPANHAMENTO)).fetchone()
    if not acomp:
        return None
    c.execute("UPDATE tarefas SET descricao=COALESCE(descricao,'') || ?, prioridade='importante', atualizado_em=? WHERE id=?",
              ('\n\nRESPOSTA (%s, %s): %s' % (autor or 'cliente', quando or dt.datetime.now().strftime('%d/%m %H:%M'),
                                              (texto or '').strip()[:1500]), agora, acomp['id']))
    c.execute('UPDATE tarefa_responsaveis SET importante=1 WHERE tarefa_id=?', (acomp['id'],))
    historico(c, 'tarefa', acomp['id'], 'resposta-audiencia', 'resposta de %s registrada' % (autor or 'cliente'), por)
    c.commit()
    return {'tarefa': acomp['id'], 'aberta': acomp['status'] == 'aberta'}


def entregar_v1(c, tarefa_id, links, por=AUTOMACAO):
    """A primeira versao ficou pronta (ja passada pela controladoria): os links
    entram na tarefa de quem revisa e a tarefa fica importante. Nao conclui nada."""
    garantir(c)
    agora = time.time()
    row = c.execute('SELECT * FROM tarefas WHERE id=?', (tarefa_id,)).fetchone()
    if not row:
        return None
    if _so_automacao(c, tarefa_id) and row['cadeia']:
        # passo a passo (07/10/2026): a tarefa e so da Assistente; a entrega abre a tarefa de quem revisa
        t = dict(row)
        volta = None
        try:
            r = c.execute("SELECT resultado FROM producao_fila WHERE tarefa_id=? AND modo='v1' ORDER BY id DESC", (tarefa_id,)).fetchone()
            volta = (json.loads(r['resultado']) if r and r['resultado'] else {}).get('volta_para')
        except Exception:
            volta = None
        lista = [links.get('peca_editavel') or links.get('peca')] if isinstance(links, dict) else list(links or [])
        lista = [u for u in lista if u]
        bloco = 'PRIMEIRA VERSAO PRONTA (Assistente, %s):\n%s' % (dt.datetime.now().strftime('%d/%m %H:%M'), _bloco_links(links))
        c.execute("UPDATE producao_fila SET status='entregue', terminado_em=?, resultado=? WHERE tarefa_id=? AND modo='v1'",
                  (agora, json.dumps({'links': links, 'volta_para': volta}, ensure_ascii=False), tarefa_id))
        nova = nascer_proxima(c, t, por, lista, bloco=bloco, tpl=volta, bloco_primeiro=True)
        historico(c, 'tarefa', tarefa_id, 'v1-entregue', 'primeira versao entregue pela automacao; tarefa seguinte #%s' % (nova or {}).get('id'), por)
        c.commit()
        passagem = _fechar_a_propria(c, tarefa_id, por, 'primeira versao entregue')
        return {'tarefa': tarefa_id, 'links': links, 'nova': nova, 'passagem': passagem}
    c.execute("UPDATE tarefas SET descricao=COALESCE(descricao,'') || ?, prioridade='importante', atualizado_em=? WHERE id=?",
              ('\n\nPRIMEIRA VERSAO PRONTA (automacao):\n' + _bloco_links(links), agora, tarefa_id))
    c.execute('UPDATE tarefa_responsaveis SET importante=1 WHERE tarefa_id=?', (tarefa_id,))
    c.execute("UPDATE producao_fila SET status='entregue', terminado_em=?, resultado=? WHERE tarefa_id=? AND modo='v1'",
              (agora, json.dumps({'links': links}, ensure_ascii=False), tarefa_id))
    historico(c, 'tarefa', tarefa_id, 'v1-entregue', 'primeira versao entregue pela automacao: ' + _bloco_links(links)[:400], por)
    c.commit()
    return {'tarefa': tarefa_id, 'links': links}


def entregar_revisao(c, tarefa_revisao_id, links, status_revisao, por=AUTOMACAO, agenda=None, hoje=None):
    """A revisao final da controladoria ficou pronta. Os links e o STATUS entram
    na tarefa seguinte (a da Advogada); a automacao fecha a PROPRIA tarefa de
    revisao e a etapa da Advogada fica na vez. NAO APROVADA abre a decisao da
    Dra. Advogada. Tarefa de pessoa nenhuma e concluida aqui."""
    garantir(c)
    hoje = hoje or dt.date.today()
    agora = time.time()
    t = c.execute('SELECT * FROM tarefas WHERE id=?', (tarefa_revisao_id,)).fetchone()
    if not t or _norm(t['tipo']) != TT.REVISAO or not t['cadeia']:
        return None
    t = dict(t)
    status = _norm(status_revisao) or 'SEM STATUS'
    seg = c.execute("SELECT * FROM tarefas WHERE cadeia=? AND etapa>? ORDER BY etapa LIMIT 1",
                    (t['cadeia'], int(t['etapa'] or 0))).fetchone()
    bloco = ('\n\nREVISAO DA CONTROLADORIA: %s.\nVermelho tachado = sai; vermelho = entra; colchetes = marcador. '
             'Nada em vermelho vai ao protocolo.\n%s' % (status, _bloco_links(links)))
    nova = None
    if seg:
        c.execute("UPDATE tarefas SET descricao=COALESCE(descricao,'') || ?, atualizado_em=? WHERE id=?",
                  (bloco, agora, seg['id']))
    else:
        # passo a passo (07/10/2026): a tarefa de ajustes nasce agora, com o parecer e os links
        lista = [v for k, v in (links or {}).items() if v and 'pasta' not in k] if isinstance(links, dict) else list(links or [])
        nova = nascer_proxima(c, t, por, lista, bloco=bloco.strip(), hoje=hoje)
        if nova:
            seg = c.execute('SELECT * FROM tarefas WHERE id=?', (nova['id'],)).fetchone()
    c.execute("UPDATE revisao_fila SET status='entregue', terminado_em=?, resultado=? WHERE tarefa_id=?",
              (agora, json.dumps({'links': links, 'status_revisao': status}, ensure_ascii=False), tarefa_revisao_id))
    decisao = None
    if 'NAO APROVADA' in status and not c.execute(
            "SELECT 1 FROM tarefas WHERE origem='motor' AND origem_ref=? AND tipo='DECISAO DA REVISAO'",
            (t['origem_ref'],)).fetchone():
        cur = c.execute(
            "INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, cliente, parte_adversa, data, prazo_fatal, "
            "prazo_interno, trilha, prioridade, status, origem, origem_ref, criado_por, criado_em, atualizado_em, cadeia, etapa) "
            "VALUES ('DECISAO DA REVISAO', ?, ?, ?, ?, ?, ?, ?, ?, 'PECA', 'urgente', 'aberta', 'motor', ?, ?, ?, ?, ?, ?)",
            ('DECISAO: revisao NAO APROVADA - ' + (t.get('titulo') or '').split(' - ', 1)[-1],
             'A controladoria nao aprovou a peca. Decidir a estrategia antes dos ajustes.' + bloco,
             t.get('processo_numero'), t.get('cliente'), t.get('parte_adversa'), hoje.isoformat(), t.get('prazo_fatal'),
             hoje.isoformat(), t['origem_ref'], por, agora, agora, t['cadeia'], int(t['etapa'] or 0)))
        pam = quem('Advogada')
        c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) '
                  'VALUES (?,?,?,1,1)', (cur.lastrowid, pam['id'], pam['nome']))
        decisao = cur.lastrowid
    c.commit()
    passagem = _fechar_a_propria(c, tarefa_revisao_id, por, 'revisao da controladoria entregue (%s)' % status, agenda)
    return {'status_revisao': status, 'seguinte': seg['id'] if seg else None, 'decisao_advogada': decisao,
            'passagem': passagem, 'fechou_a_propria': passagem is not None, 'nova': nova}


def abrir_atualizacao_do_cliente(c, cadeia, ref, motivo, processo, cliente, adversa, etapa, por='motor das tarefas',
                                 hoje=None, uf=''):
    """Atualizacao do cliente (POP 16) no modelo novo: quem atualiza e a AUTOMACAO,
    pelo WhatsApp do escritorio e com o texto aprovado; a Advogada ganha um
    acompanhamento para fiscalizar. O cliente ve o movimento inteiro no Portal;
    a mensagem avisa e manda para la. Uma vez por cadeia."""
    garantir(c)
    if c.execute("SELECT 1 FROM tarefas WHERE cadeia=? AND tipo=?", (cadeia, TT.ATUALIZAR_CLIENTE)).fetchone():
        return None
    hoje = hoje or dt.date.today()
    agora = time.time()
    dia = FER.proximo_dia_util(hoje, uf, incluir_hoje=True)
    partes = ('%s x %s' % (cliente or '', adversa or '')).strip(' x')
    sufixo = ' - '.join(x for x in (partes, processo) if x)
    linhas = (
        (TT.ATUALIZAR_CLIENTE, 'ATUALIZAR O CLIENTE (automacao)', 'Assistente', dia, etapa,
         'Atualizacao do cliente pela automacao sobre %s (POP 16): mensagem pelo WhatsApp do escritorio, com o texto '
         'aprovado, sem travessao e sem promessa de resultado, avisando que ha novidade e pedindo que ele abra o Portal '
         'do Cliente, onde esta o movimento inteiro. Cliente de parceiro nao recebe direto. O que foi enviado entra na '
         'tarefa de fiscalizacao da Advogada.' % motivo),
        (TT.ACOMPANHAMENTO, 'FISCALIZAR A ATUALIZACAO DO CLIENTE', 'Advogada', FER.proximo_dia_util(dia, uf), etapa + 1,
         'Fiscalizar a automacao: conferir no Chat ao Vivo se a atualizacao sobre %s saiu, se saiu certa e para a pessoa '
         'certa, e se o cliente respondeu. O que a automacao enviou entra aqui embaixo.' % motivo),
    )
    ids = {}
    for tipo, rotulo, para, quando, n, texto in linhas:
        if c.execute("SELECT 1 FROM tarefas WHERE origem='motor' AND origem_ref=? AND tipo=?", (ref, tipo)).fetchone():
            continue                      # a cadeia ja tem tarefa desse tipo (ex.: acompanhamento do POP 18)
        cur = c.execute(
            "INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, cliente, parte_adversa, data, prazo_interno, "
            "prioridade, status, origem, origem_ref, criado_por, criado_em, atualizado_em, cadeia, etapa) "
            "VALUES (?,?,?,?,?,?,?,?,'normal','aberta','motor',?,?,?,?,?,?)",
            (tipo, (rotulo + ' - ' + sufixo).strip(' -'), texto, processo, cliente, adversa, quando.isoformat(),
             quando.isoformat(), ref, 'motor das tarefas', agora, agora, cadeia, n))
        r = quem(para)
        c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) '
                  'VALUES (?,?,?,0,0)', (cur.lastrowid, r['id'], r['nome']))
        _por_plantao(c, cur.lastrowid, tipo)
        ids[tipo] = cur.lastrowid
        if tipo == TT.ATUALIZAR_CLIENTE:
            c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, criado_em, modo) "
                      "VALUES (?,?,?,?,?,?,'atualizacao')", (cur.lastrowid, int(ref) if str(ref).isdigit() else None,
                                                             processo, 'pop-16-atualizacao', 'aguardando', agora))
    if not ids:
        return None
    historico(c, 'cadeia', cadeia, 'atualizacao-do-cliente', 'atualizacao sobre %s: %s' % (
        motivo, ', '.join('#%s %s' % (v, k) for k, v in ids.items())), por)
    return {'atualizar': ids.get(TT.ATUALIZAR_CLIENTE), 'fiscalizar': ids.get(TT.ACOMPANHAMENTO)}


def entregar_atualizacao(c, tarefa_id, relato, por=AUTOMACAO):
    """A automacao atualizou o cliente: o relato (texto, para quem, quando) entra na
    tarefa de fiscalizacao da Advogada e a automacao fecha a propria tarefa."""
    garantir(c)
    agora = time.time()
    t = c.execute('SELECT * FROM tarefas WHERE id=?', (tarefa_id,)).fetchone()
    if not t or _norm(t['tipo']) != TT.ATUALIZAR_CLIENTE or not t['cadeia']:
        return None
    fisc = c.execute("SELECT id FROM tarefas WHERE cadeia=? AND tipo=? AND etapa>? ORDER BY etapa LIMIT 1",
                     (t['cadeia'], TT.ACOMPANHAMENTO, int(t['etapa'] or 0))).fetchone()
    if fisc:
        c.execute("UPDATE tarefas SET descricao=COALESCE(descricao,'') || ?, atualizado_em=? WHERE id=?",
                  ('\n\nO QUE A AUTOMACAO ENVIOU AO CLIENTE:\n' + (relato or '').strip(), agora, fisc['id']))
    c.execute("UPDATE producao_fila SET status='entregue', terminado_em=?, resultado=? WHERE tarefa_id=? AND modo='atualizacao'",
              (agora, json.dumps({'relato': relato}, ensure_ascii=False), tarefa_id))
    passagem = _fechar_a_propria(c, tarefa_id, por, 'cliente atualizado')
    return {'fiscalizacao_em': fisc['id'] if fisc else None, 'passagem': passagem, 'fechou_a_propria': passagem is not None}


# ---------------------------------------------------------------------------
# Consultoria de cliente empresa pelo canal central (Advogada, 05/10/2026, item 27)
#
# O cliente empresa manda a duvida pelo WhatsApp do escritorio; um agente que
# so conhece a empresa dele colhe o problema, acalma, entende a urgencia e troca a
# etapa do CRM para "solicitacao de consultoria". A partir dai: a automacao entende
# o caso, ativa as instrucoes da IVA e a skill do cliente, faz o relatorio, e nasce
# a tarefa da Advogada e da Advogada para validarem a devolutiva, com a Advogada
# responsavel por da-la ao cliente. Junto nasce o caso de consultoria no Sistema
# Escritório Modelo, para constar no Portal do Cliente.
# ---------------------------------------------------------------------------
SKILL_CONSULTORIA = dict(_CLI_EMP.get('consultoria') or {})   # juridico/clientes_empresa.json


def _tem_tabela(c, nome):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nome,)).fetchone())


def abrir_consultoria(c, cliente, duvida, urgencia='', conversa_id=None, por='agente do canal central', hoje=None):
    """A etapa do CRM virou "solicitacao de consultoria": abre a tarefa da automacao
    de entender o caso e fazer o relatorio. Uma por conversa e dia."""
    garantir(c)
    hoje = hoje or dt.date.today()
    agora = time.time()
    cadeia = 'consultoria:%s:%s' % (conversa_id or 'sem-conversa', hoje.isoformat())
    if c.execute('SELECT 1 FROM tarefas WHERE cadeia=?', (cadeia,)).fetchone():
        return None
    skill = next((sk for nome, sk in SKILL_CONSULTORIA.items() if re.search(r'\b%s\b' % nome, _norm(cliente))), 'consultoria-pj')
    urgente = bool(re.search(r'URGENT|HOJE|AGORA|IMEDIAT', _norm(urgencia)))
    texto = ('Consultoria pedida por %s pelo canal central. O que o cliente relatou ao agente: %s\nUrgencia no retorno: %s\n\n'
             'A automacao entende o caso, ativa as instrucoes da IVA e a skill %s (com a norma coletiva do cliente) e faz o '
             'relatorio com a devolutiva proposta. Ao entregar, nasce o caso de consultoria, que aparece no Portal do '
             'Cliente, e a tarefa de validacao da Advogada e da Advogada.'
             % (cliente, (duvida or '').strip()[:1500], urgencia or 'nao informada', skill))
    cur = c.execute(
        "INSERT INTO tarefas (tipo, titulo, descricao, cliente, data, prazo_interno, trilha, prioridade, status, origem, "
        "origem_ref, criado_por, criado_em, atualizado_em, cadeia, etapa, conversa_id) "
        "VALUES ('RELATORIO DE CONSULTORIA', ?, ?, ?, ?, ?, 'MEIO', ?, 'aberta', 'motor', ?, ?, ?, ?, ?, 1, ?)",
        ('RELATORIO DE CONSULTORIA - %s' % cliente, texto, cliente, hoje.isoformat(), hoje.isoformat(),
         'urgente' if urgente else 'importante', cadeia, por, agora, agora, cadeia, conversa_id))
    a = quem('Assistente')
    c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) '
              'VALUES (?,?,?,1,?)', (cur.lastrowid, a['id'], a['nome'], 1 if urgente else 0))
    c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, criado_em, modo) "
              "VALUES (?,?,?,?,?,?,'consultoria')", (cur.lastrowid, None, None, skill, 'aguardando', agora))
    _por_plantao(c, cur.lastrowid, 'RELATORIO DE CONSULTORIA')
    historico(c, 'tarefa', cur.lastrowid, 'criada', 'consultoria de %s aberta pelo canal central (skill %s)' % (cliente, skill), por)
    c.commit()
    return {'cadeia': cadeia, 'tarefa': cur.lastrowid, 'skill': skill, 'urgente': urgente}


def entregar_consultoria(c, tarefa_relatorio_id, titulo, relatorio, links=None, portal_cliente_id=None, por=AUTOMACAO,
                         hoje=None, uf=''):
    """O relatorio da consultoria ficou pronto: nasce o caso de consultoria no
    Sistema Escritório Modelo (e a linha dele no Portal do Cliente, so com o status) e a tarefa
    da Advogada e da Advogada para validarem a devolutiva; a Advogada e quem responde ao
    cliente. A automacao fecha a propria tarefa. A devolutiva so vai ao cliente
    pela mao da Advogada, depois de validada."""
    garantir(c)
    hoje = hoje or dt.date.today()
    agora = time.time()
    t = c.execute('SELECT * FROM tarefas WHERE id=?', (tarefa_relatorio_id,)).fetchone()
    if not t or _norm(t['tipo']) != 'RELATORIO DE CONSULTORIA' or not t['cadeia']:
        return None
    t = dict(t)
    avisos, caso_id = [], None
    if c.execute("SELECT 1 FROM tarefas WHERE cadeia=? AND tipo='CONSULTORIA'", (t['cadeia'],)).fetchone():
        return None
    fl = quem('Advogada')
    if _tem_tabela(c, 'casos'):
        cur = c.execute(
            "INSERT INTO casos (titulo, tipo, area, cliente_nome, responsavel, responsavel_id, status, notas, criado_por, "
            "criado_em, atualizado_em, conversa_id, origem) VALUES (?, 'consultoria', 'Trabalhista', ?, ?, ?, 'ativo', ?, ?, ?, ?, ?, ?)",
            (titulo, t.get('cliente'), fl['nome'], fl['id'], (t.get('descricao') or '')[:3000], por, agora, agora,
             t.get('conversa_id'), 'canal central'))
        caso_id = cur.lastrowid
    else:
        avisos.append('tabela de casos nao encontrada: o caso de consultoria nao foi criado')
    portal = None
    if portal_cliente_id and _tem_tabela(c, 'portal_painel_consultoria'):
        cur = c.execute(
            "INSERT INTO portal_painel_consultoria (cliente_id, titulo, texto, ordem, criado_em, desde, tipo) "
            "VALUES (?, ?, ?, 0, ?, ?, 'caso')",
            (int(portal_cliente_id), titulo, 'Consulta recebida em %s. A equipe esta analisando; a devolutiva chega pelo '
             'canal do escritorio.' % _br(hoje), agora, hoje.isoformat()))
        portal = cur.lastrowid
    elif not portal_cliente_id:
        avisos.append('cliente do Portal nao informado: o caso nao entrou no Portal')
    corpo = ('Validar a devolutiva da consultoria de %s: "%s".\nresponsável valida o conteudo juridico; Advogada valida e e a responsavel '
             'por dar a devolutiva ao cliente (e-mail e WhatsApp do escritorio), de acordo com o relatorio abaixo. Cada uma '
             'conclui a sua parte.\n\nRELATORIO DA AUTOMACAO:\n%s%s'
             % (t.get('cliente') or 'cliente', titulo, (relatorio or '').strip(), ('\n' + _bloco_links(links)) if links else ''))
    dia = FER.proximo_dia_util(hoje, uf, incluir_hoje=True)
    cols = {r[1] for r in c.execute('PRAGMA table_info(tarefas)')}
    cur = c.execute(
        "INSERT INTO tarefas (tipo, titulo, descricao, cliente, data, prazo_interno, trilha, prioridade, status, origem, "
        "origem_ref, criado_por, criado_em, atualizado_em, cadeia, etapa, conversa_id) "
        "VALUES ('CONSULTORIA', ?, ?, ?, ?, ?, 'MEIO', ?, 'aberta', 'motor', ?, ?, ?, ?, ?, 2, ?)",
        ('CONSULTORIA: VALIDAR E DAR A DEVOLUTIVA - %s - %s' % (t.get('cliente') or '', titulo), corpo, t.get('cliente'),
         dia.isoformat(), dia.isoformat(), t.get('prioridade') or 'importante', t['cadeia'], por, agora, agora, t['cadeia'],
         t.get('conversa_id')))
    tid = cur.lastrowid
    if caso_id and 'caso_id' in cols:
        c.execute('UPDATE tarefas SET caso_id=? WHERE id IN (?, ?)', (caso_id, tid, tarefa_relatorio_id))
    for r in (quem('Advogada'), fl):
        c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) '
                  'VALUES (?,?,?,1,0)', (tid, r['id'], r['nome']))
    c.execute("UPDATE producao_fila SET status='entregue', terminado_em=?, resultado=? WHERE tarefa_id=? AND modo='consultoria'",
              (agora, json.dumps({'titulo': titulo, 'caso': caso_id, 'links': links}, ensure_ascii=False), tarefa_relatorio_id))
    historico(c, 'tarefa', tid, 'criada', 'consultoria "%s": caso #%s%s; validacao da Advogada e da Advogada, devolutiva pela Advogada'
              % (titulo, caso_id, (', Portal #%s' % portal) if portal else ''), por)
    c.commit()
    passagem = _fechar_a_propria(c, tarefa_relatorio_id, por, 'relatorio da consultoria entregue')
    return {'caso': caso_id, 'portal': portal, 'validacao': tid, 'avisos': avisos, 'passagem': passagem,
            'fechou_a_propria': passagem is not None}


# ---------------------------------------------------------------------------
# Vigia pos-audiencia (Advogada, 05/10/2026): "sempre apos a audiencia o vigia vai fazer
# uma varredura no processo completo, vai entender quais foram as providencias
# geradas (impugnacao de pericia, designacao de pericia, prazo de razoes finais ou
# replica) e vai cadastrar uma tarefa para a Advogada validar se e isso".
# O robo le a ata nos autos e chama providencias_da_ata + abrir_validacao_pos_audiencia.
# Quando a Advogada conclui a validacao, cada linha que ela deixou vira tarefa; as de
# peca caem na varredura e ganham a cadeia inteira.
# ---------------------------------------------------------------------------
_EXTENSO_DIAS = {'CINCO': 5, 'OITO': 8, 'DEZ': 10, 'QUINZE': 15, 'VINTE': 20, 'TRINTA': 30, 'TRES': 3, 'DOIS': 2,
                 'QUARENTA E OITO HORAS': 2}
_ATA = [
    ('RAZOES FINAIS', re.compile(r'RAZOES FINAIS|MEMORIAIS|ALEGACOES FINAIS')),
    ('REPLICA', re.compile(r'\bREPLICA\b|MANIFESTA\w+ SOBRE (A )?(DEFESA|CONTESTACAO)|IMPUGNA\w+ (A|DA) (DEFESA|CONTESTACAO)|'
                           r'VISTA (A|AO) (PARTE )?(RECLAMANTE|AUTORA?) DA (DEFESA|CONTESTACAO)')),
    ('MANIFESTACAO SOBRE LAUDO', re.compile(r'(MANIFESTA\w+|IMPUGNA\w+) (SOBRE|AO|DO|ACERCA DO) (O )?LAUDO|VISTA (AS PARTES )?DO LAUDO')),
    ('QUESITOS', re.compile(r'QUESITOS')),
    ('PERICIA', re.compile(r'(DESIGN|DETERMIN|NOMEI|DEFIR)\w* .{0,80}(PERICIA|PERITO)|(PERICIA|PERITO).{0,60}(DESIGNAD|NOMEAD|DETERMINAD)')),
    ('NOVA AUDIENCIA', re.compile(r'REDESIGN\w+|ADI\w+ A AUDIENCIA|DESIGN\w+ (NOVA )?AUDIENCIA|AUDIENCIA DE (PROSSEGUIMENTO|ENCERRAMENTO)|'
                                  r'AUDIENCIA .{0,40}(PARA O DIA|EM PROSSEGUIMENTO)')),
    ('JUNTAR DOCUMENTOS', re.compile(r'JUNT(AR|ADA|E) .{0,40}DOCUMENTOS?|APRESENT\w+ .{0,30}DOCUMENTOS?')),
    ('ACORDO', re.compile(r'HOMOLOG\w+ .{0,30}ACORDO|ACORDO .{0,40}HOMOLOGAD|CONCILIACAO ACEITA|AS PARTES SE CONCILIARAM')),
    ('AGUARDAR SENTENCA', re.compile(r'CONCLUSOS? PARA (SENTENCA|JULGAMENTO)|JULGAMENTO (DESIGNADO|SINE DIE)|SENTENCA SERA (PROFERIDA|PUBLICADA)')),
]
# providencia validada -> (tipo da tarefa, quem, e peca?)
_PROVIDENCIA_TAREFA = [
    (re.compile(r'RAZOES FINAIS|MEMORIAIS'), 'RAZOES FINAIS', 'Advogada', True),
    (re.compile(r'REPLICA|DEFESA|CONTESTACAO'), 'REPLICA', 'Advogada', True),
    (re.compile(r'LAUDO'), 'MANIFESTACAO SOBRE LAUDO', 'Advogada', True),
    (re.compile(r'QUESITO'), 'QUESITOS', 'Advogada', True),
    (re.compile(r'PERICIA|PERITO'), 'PERICIA DESIGNADA', 'Advogada', False),
    (re.compile(r'AUDIENCIA'), 'AUDIENCIA', 'Advogada', False),
    (re.compile(r'DOCUMENTO'), TT.PETICAO_DE_MEIO, 'Advogada', False),
    (re.compile(r'ACORDO'), 'ACOMPANHAR IMPLANTACAO DECISAO', 'Advogada', False),
    (re.compile(r'SENTENCA|JULGAMENTO'), TT.ACOMPANHAMENTO, 'Advogada', False),
]
_OQUE = {
    'PERICIA DESIGNADA': 'Pericia designada na audiencia: cadastrar na Agenda (data, local, perito), conferir assistente tecnico e avisar o cliente.',
    'AUDIENCIA': 'Nova audiencia marcada na ata: cadastrar na Agenda; o POP 18 e o roteiro saem pela automacao.',
    TT.PETICAO_DE_MEIO: 'Juntada de documentos determinada na audiencia: reunir e peticionar no prazo.',
    'ACOMPANHAR IMPLANTACAO DECISAO': 'Acordo homologado em audiencia: controlar as parcelas e preparar o parecer terminativo.',
    TT.ACOMPANHAMENTO: 'Processo concluso para sentenca: acompanhar a publicacao.',
}


def _dias_perto(texto, pos, raio=220):
    janela = texto[max(0, pos - 60): pos + raio]
    m = re.search(r'(\d{1,2}) ?(?:\([A-Z ]+\) ?)?DIAS', janela)
    if m:
        return int(m.group(1))
    for palavra, n in _EXTENSO_DIAS.items():
        if re.search(r'\b%s\b ?(?:\(\d+\) ?)?(DIAS|$)' % palavra, janela):
            return n
    return None


def providencias_da_ata(texto, data_audiencia=None, uf=''):
    """Le a ata e devolve as providencias que ela gerou, com o prazo quando da para
    calcular (dias uteis contados da audiencia, em que as partes saem intimadas).
    So le e sugere: quem valida e a Advogada."""
    t = _norm(texto)
    dia = _data(data_audiencia)
    achadas, vistos = [], set()
    for tipo, rx in _ATA:
        m = rx.search(t)
        if not m or tipo in vistos:
            continue
        vistos.add(tipo)
        dias = _dias_perto(t, m.start()) if tipo not in ('ACORDO', 'AGUARDAR SENTENCA', 'NOVA AUDIENCIA', 'PERICIA') else None
        prazo = uteis_depois(dia, dias, uf).isoformat() if (dia and dias) else None
        trecho = re.sub(r'\s+', ' ', t[max(0, m.start() - 80): m.end() + 160]).strip()
        achadas.append({'tipo': tipo, 'dias': dias, 'prazo': prazo, 'trecho': trecho})
    return achadas


def abrir_validacao_pos_audiencia(c, processo, data_audiencia, providencias, cliente='', adversa='', link_ata=None,
                                  observacao='', por='vigia pos-audiencia', hoje=None, uf=''):
    """Abre a tarefa da Advogada de validar o que a audiencia gerou. Uma por processo e
    audiencia. Mesmo sem nada achado a tarefa nasce, para ninguem ficar sem conferir."""
    garantir(c)
    hoje = hoje or dt.date.today()
    agora = time.time()
    cadeia = 'audiencia:%s:%s' % (processo, str(data_audiencia)[:10])
    if c.execute('SELECT 1 FROM tarefas WHERE cadeia=?', (cadeia,)).fetchone():
        return None
    linhas = ['PROVIDENCIA: %s | PRAZO: %s' % (p['tipo'], p.get('prazo') or '-') for p in (providencias or [])]
    topo = ('\n'.join(linhas) if linhas else 'PROVIDENCIA: (a automacao nao achou nenhuma na ata)') + \
        '\n(uma providencia por linha; apague a que nao procede, corrija o tipo ou a data e acrescente o que faltar no mesmo formato)'
    partes = ('%s x %s' % (cliente or '', adversa or '')).strip(' x')
    corpo = ('%s\n\nValidar o que a audiencia de %s gerou. A automacao leu o processo inteiro e a ata e levantou as '
             'providencias acima. Ao concluir, cada linha que ficar vira tarefa: as de peca (razoes finais, replica, laudo, '
             'quesitos) ja nascem com a cadeia inteira e a primeira versao em fila; pericia e nova audiencia vao para a '
             'Advogada cadastrar na Agenda.%s%s\n\nO QUE A ATA DIZ:\n%s'
             % (topo, _br(data_audiencia), ('\nAta: %s' % link_ata) if link_ata else '',
                ('\n' + observacao) if observacao else '',
                '\n'.join('- %s%s: "%s"' % (p['tipo'], (' (%s dias)' % p['dias']) if p.get('dias') else '', p['trecho'][:300])
                          for p in (providencias or [])) or '- nada identificado; conferir a ata nos autos'))
    curto = min([_data(p['prazo']) for p in (providencias or []) if p.get('prazo')] or [None], key=lambda d: d or dt.date.max)
    dia = FER.proximo_dia_util(hoje, uf, incluir_hoje=True)
    cur = c.execute(
        "INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, cliente, parte_adversa, data, prazo_fatal, prazo_interno, "
        "trilha, prioridade, status, origem, origem_ref, criado_por, criado_em, atualizado_em, cadeia, etapa) "
        "VALUES ('VALIDAR PROVIDENCIAS DA AUDIENCIA', ?, ?, ?, ?, ?, ?, ?, ?, 'PECA', 'importante', 'aberta', 'motor', ?, ?, ?, ?, ?, 1)",
        (' - '.join(x for x in ('VALIDAR AS PROVIDENCIAS DA AUDIENCIA DE %s' % _br(data_audiencia), partes, processo) if x), corpo,
         processo, cliente or None, adversa or None, dia.isoformat(), curto.isoformat() if curto else None, dia.isoformat(),
         cadeia, por, agora, agora, cadeia))
    b = quem('Advogada')
    c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) VALUES (?,?,?,1,0)',
              (cur.lastrowid, b['id'], b['nome']))
    historico(c, 'processo', processo, 'pos-audiencia', 'audiencia de %s: %s; validacao da Advogada #%s'
              % (_br(data_audiencia), ', '.join(p['tipo'] for p in (providencias or [])) or 'nada identificado', cur.lastrowid), por)
    c.commit()
    return {'cadeia': cadeia, 'tarefa': cur.lastrowid, 'providencias': [p['tipo'] for p in (providencias or [])]}


def _abrir_providencias_validadas(c, t, por):
    """A Advogada concluiu a validacao: cada linha PROVIDENCIA que ficou vira tarefa."""
    agora = time.time()
    hoje = dt.date.today()
    criadas = []
    partes = ('%s x %s' % (t.get('cliente') or '', t.get('parte_adversa') or '')).strip(' x')
    for m in re.finditer(r'^\s*PROVID[EÊ]NCIA\s*:\s*([^|\n]+?)\s*(?:\|\s*PRAZO\s*:\s*([^\n|]*))?\s*$',
                         str(t.get('descricao') or ''), flags=re.M | re.I):
        dito = _norm(m.group(1))
        if not dito or dito.startswith('('):
            continue
        bruto = (m.group(2) or '').strip()
        br = re.match(r'(\d{2})/(\d{2})/(\d{4})', bruto)
        prazo = _data('%s-%s-%s' % (br.group(3), br.group(2), br.group(1))) if br else _data(bruto)
        tipo, para, peca = next(((tp, pa, pc) for rx, tp, pa, pc in _PROVIDENCIA_TAREFA if rx.search(dito)), (dito[:60], 'Advogada', False))
        if c.execute("SELECT 1 FROM tarefas WHERE origem='motor' AND origem_ref=? AND tipo=?", (t['cadeia'], tipo)).fetchone():
            continue
        dia = prazo or FER.proximo_dia_util(hoje, incluir_hoje=True)
        texto = _OQUE.get(tipo) or ('%s: prazo fixado na audiencia%s. A automacao monta a cadeia e, havendo skill, a primeira versao.'
                                    % (tipo.title(), (', ate %s' % _br(prazo)) if prazo else ''))
        cur = c.execute(
            "INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, cliente, parte_adversa, data, prazo_fatal, prazo_interno, "
            "trilha, prioridade, status, origem, origem_ref, criado_por, criado_em, atualizado_em, cadeia, etapa) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,'importante','aberta','motor',?,?,?,?,?,?)",
            (tipo, ' - '.join(x for x in (tipo, partes, t.get('processo_numero')) if x),
             texto + '\n\nValidado por %s na tarefa #%s (%s).' % (por, t['id'], t['cadeia']), t.get('processo_numero'),
             t.get('cliente'), t.get('parte_adversa'), dia.isoformat(), prazo.isoformat() if prazo else None,
             (FER.prazo_interno(prazo, 2) if prazo else dia).isoformat(), 'PECA' if peca else 'MEIO', t['cadeia'], por, agora, agora,
             None if peca else t['cadeia'], None if peca else 2))
        r = quem(para)
        c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) VALUES (?,?,?,1,0)',
                  (cur.lastrowid, r['id'], r['nome']))
        criadas.append({'id': cur.lastrowid, 'tipo': tipo, 'para': r['nome'], 'prazo': prazo.isoformat() if prazo else None, 'peca': peca})
    historico(c, 'tarefa', t['id'], 'providencias-validadas', 'validadas por %s: %s'
              % (por, ', '.join('#%s %s' % (x['id'], x['tipo']) for x in criadas) or 'nenhuma'), por)
    return criadas


# ---------------------------------------------------------------------------
# Fluxo passo a passo (Advogada, 07/10/2026): a cadeia nao nasce inteira. Cada tarefa traz a
# providencia a executar e a PROXIMA ETAPA SUGERIDA; quem executa clica em "Concluir e
# encaminhar" e a tarefa seguinte nasce para o responsavel. Quando a seguinte e da Assistente,
# ela entra na fila, faz o intake e, ao entregar, abre ela mesma a tarefa seguinte.
# O gabarito das etapas fica na ficha da cadeia (motor_fichas.ficha['etapas']).
# Uma pessoa por tarefa; a Assistente sozinha nas dela; a Dra. Advogada recebe relatorio.
# ---------------------------------------------------------------------------

def gabarito_da_cadeia(c, cadeia):
    """(ficha, etapas) da cadeia; etapas e o gabarito das proximas tarefas."""
    r = c.execute('SELECT ficha FROM motor_fichas WHERE cadeia=?', (cadeia,)).fetchone()
    try:
        f = json.loads(r['ficha']) if r and r['ficha'] else {}
    except (ValueError, TypeError):
        f = {}
    return f, list(f.get('etapas') or [])


def proxima_da_cadeia(c, cadeia, etapa_atual):
    """O gabarito da etapa seguinte a `etapa_atual`, ou None no fim da cadeia."""
    _, etapas = gabarito_da_cadeia(c, cadeia)
    cand = [e for e in etapas if int(e.get('etapa') or 0) > int(etapa_atual or 0)]
    return dict(min(cand, key=lambda e: int(e.get('etapa') or 0))) if cand else None


def etapa_do_gabarito(c, cadeia, etapa):
    _, etapas = gabarito_da_cadeia(c, cadeia)
    for e in etapas:
        if int(e.get('etapa') or 0) == int(etapa or 0):
            return dict(e)
    return None


def resumo_proxima(c, t):
    """O que a tela mostra no perfil: a proxima etapa sugerida de uma tarefa aberta da cadeia."""
    if not t or not t.get('cadeia') or t.get('status') != 'aberta':
        return None
    tpl = proxima_da_cadeia(c, t['cadeia'], t.get('etapa'))
    if not tpl:
        return None
    q = quem(tpl.get('para'))
    return {'etapa': tpl.get('etapa'), 'tipo': _norm(tpl.get('tipo')), 'rotulo': tpl.get('rotulo') or _norm(tpl.get('tipo')),
            'para': (q or {}).get('nome') or tpl.get('para'), 'automacao': bool(q and q['id'] == AUTOMACAO_ID),
            'data': tpl.get('data'), 'pede_link': _norm(tpl.get('tipo')) in (TT.REVISAO, TT.AJUSTES, TT.PROTOCOLO)}


def _links_da_descricao(desc):
    out = []
    for u in _LINK.findall(str(desc or '')):
        u = u.rstrip('.,;:)')
        if 'sistema.escritorio.adv.br' in u or u in out:
            continue
        out.append(u)
    return out


def link_editavel(desc):
    """O primeiro link de Google Docs da descricao (a versao editavel, que e onde a pessoa revisa)."""
    for u in _links_da_descricao(desc):
        if 'docs.google.com/document' in u:
            return u
    return None


_REFAZER = re.compile(r'^\s*REFAZER\b\s*[:\-]?\s*(.*)$', flags=re.M | re.I)


def texto_da_etapa(tpl, bloco='', proxima=None, bloco_primeiro=False):
    """O texto da tarefa que nasce: a providencia (gabarito), a entrega da etapa anterior e a proxima sugerida."""
    corpo = (tpl.get('texto') or '').strip()
    partes = [bloco.strip(), corpo] if bloco_primeiro else [corpo, bloco.strip()]
    if proxima:
        q = quem(proxima.get('para'))
        nome = (q or {}).get('nome') or str(proxima.get('para') or '')
        pede = _norm(proxima.get('tipo')) in (TT.REVISAO, TT.AJUSTES, TT.PROTOCOLO)
        partes.append('PRÓXIMA ETAPA SUGERIDA: %s (%s%s). Com a providência feita, clique em "Concluir e encaminhar para %s"%s.'
                      % (proxima.get('rotulo') or _norm(proxima.get('tipo')), nome,
                         (', ' + _br(proxima.get('data'))) if proxima.get('data') else '', nome.split()[0],
                         ' e informe o link da entrega' if pede else ''))
    else:
        partes.append('ÚLTIMA ETAPA DA CADEIA: ao concluir, a cadeia termina.')
    return '\n\n'.join(p for p in partes if p)


def nascer_proxima(c, atual, por, links=None, bloco='', agenda=None, tpl=None, fila_extra=None, hoje=None, bloco_primeiro=False):
    """Cria a tarefa da proxima etapa (gabarito) a partir da tarefa `atual` (dict). `links`: as URLs
    da entrega (lista); `bloco`: texto pronto da entrega (vale no lugar dos links); `tpl`: gabarito
    explicito (usado no REFAZER e no retorno da Assistente); `fila_extra`: JSON gravado em
    producao_fila.resultado (orientacao do refazer e para onde a entrega volta). Devolve a nova."""
    garantir(c)
    cadeia = atual.get('cadeia')
    if not cadeia:
        return None
    ficha, _ = gabarito_da_cadeia(c, cadeia)
    tpl = tpl or proxima_da_cadeia(c, cadeia, atual.get('etapa'))
    if not tpl:
        return None
    etapa = int(tpl.get('etapa') or 0)
    ja = c.execute("SELECT id FROM tarefas WHERE cadeia=? AND etapa=? AND status='aberta' AND id != ?",
                   (cadeia, etapa, int(atual.get('id') or 0))).fetchone()
    if ja:
        return {'id': ja['id'], 'ja_existia': True, 'etapa': etapa}
    q = quem(tpl.get('para')) or quem('Advogada')
    hoje = hoje or dt.date.today()
    data = _data(tpl.get('data')) or hoje
    if data < hoje:
        data = hoje
    tipo = _norm(tpl.get('tipo'))
    rotulo = tpl.get('rotulo') or tipo
    proxima2 = proxima_da_cadeia(c, cadeia, etapa)
    entrega = bloco or ('ENTREGA DA ETAPA ANTERIOR (%s, %s):\n%s' % (por, dt.datetime.now().strftime('%d/%m %H:%M'),
                                                                     '\n'.join(links)) if links else '')
    desc = texto_da_etapa(tpl, entrega, proxima2, bloco_primeiro)
    if tpl.get('skill'):
        desc += '\nSkill da revisao: %s' % tpl['skill']
    if tpl.get('skill_producao'):
        desc += '\nSkill da primeira versao: %s' % tpl['skill_producao']
    iid_txt = cadeia.split(':')[-1] if cadeia.startswith('intim:') else ''
    ref = iid_txt or str(atual.get('origem_ref') or cadeia)
    # as cadeias antigas ja tiveram tarefas desses tipos para a mesma intimacao (UNIQUE origem+ref+tipo):
    # a que nasce do encaminhar ganha o sufixo :e<etapa> quando o tipo ja existe
    ref_novo = ref
    if c.execute("SELECT 1 FROM tarefas WHERE origem='motor' AND origem_ref=? AND tipo=?", (ref, _norm(tpl.get('tipo')))).fetchone():
        ref_novo = '%s:e%s' % (ref, etapa)
    processo = atual.get('processo_numero') or ficha.get('processo') or ''
    cliente = (atual.get('cliente') or ficha.get('cliente') or '').strip()
    adversa = (atual.get('parte_adversa') or ficha.get('parte_adversa') or '').strip()
    desc += '\n\nEtapa %s da cadeia %s (intimacao %s). Processo: %s' % (etapa, cadeia, ref, processo or 'sem numero')
    partes = ('%s x %s' % (cliente, adversa)).strip(' x') if (cliente or adversa) else ''
    titulo = ' - '.join(x for x in (rotulo, partes, processo) if x)
    agora = time.time()
    cur = c.execute(
        "INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, cliente, parte_adversa, data, prazo_fatal, prazo_interno, "
        "trilha, prioridade, status, origem, origem_ref, criado_por, criado_em, atualizado_em, cadeia, etapa) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,'importante','aberta','motor',?,?,?,?,?,?)",
        (tipo, titulo, desc, processo or None, cliente or None, adversa or None, data.isoformat(),
         tpl.get('prazo_fatal') or atual.get('prazo_fatal') or ficha.get('vencimento'),
         tpl.get('prazo_interno') or data.isoformat(), atual.get('trilha') or ficha.get('trilha'), ref_novo, por, agora, agora, cadeia, etapa))
    tid = cur.lastrowid
    c.execute('INSERT OR REPLACE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) VALUES (?,?,?,1,0)',
              (tid, q['id'], q['nome']))
    historico(c, 'tarefa', tid, 'criada', 'nasceu do encaminhar da tarefa #%s: etapa %s (%s) para %s'
              % (atual.get('id'), etapa, tipo, q['nome']), por)
    fila = None
    if q['id'] == AUTOMACAO_ID:
        iid = int(iid_txt) if iid_txt.isdigit() else None
        extra = json.dumps(fila_extra, ensure_ascii=False) if fila_extra else None
        if tipo == TT.REVISAO:
            c.execute('INSERT INTO revisao_fila (tarefa_id, tarefa_peca_id, intimacao_id, processo_numero, skill, links, status, criado_em) '
                      'VALUES (?,?,?,?,?,?,?,?)',
                      (tid, atual.get('id'), iid, processo or None, tpl.get('skill') or skill_de_revisao(ficha.get('ato')),
                       json.dumps(links or []), 'aguardando' if links else 'sem-link', agora))
            fila = 'revisao'
        elif tipo in (TT.PECA, TT.ANALISE):
            modo = 'analise' if tipo == TT.ANALISE else 'v1'
            skill = tpl.get('skill_analise') if modo == 'analise' else (tpl.get('skill_producao') or skill_de_producao(ficha.get('ato')) or 'sem-skill')
            c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, resultado, criado_em, modo) "
                      "VALUES (?,?,?,?,?,?,?,?)", (tid, iid, processo or None, skill, 'aguardando', extra, agora, modo))
            fila = modo
        else:
            modos = {'CALCULO DE HONORARIOS': ('honorarios', 'calculo-honorarios'), 'COMUNICAR CLIENTE AUDIENCIA': ('pop18', 'pop-18-audiencia'),
                     'RECADO PARA PARCEIRO': ('parceiro', 'encaminhar-ao-parceiro'), 'ROTEIRO': ('roteiro', 'roteiro-%s' % (tpl.get('evento') or 'audiencia'))}
            if tipo in modos:
                c.execute("INSERT INTO producao_fila (tarefa_id, intimacao_id, processo_numero, skill, status, criado_em, modo) "
                          "VALUES (?,?,?,?,?,?,?)", (tid, iid, processo or None, modos[tipo][1], 'aguardando', agora, modos[tipo][0]))
                fila = modos[tipo][0]
    c.commit()
    return {'id': tid, 'tipo': tipo, 'etapa': etapa, 'para': q['nome'], 'automacao': q['id'] == AUTOMACAO_ID,
            'data': data.isoformat(), 'fila': fila, 'titulo': titulo}


def encaminhar(c, tarefa_id, por, link=None, obs=None, agenda=None, hoje=None):
    """O botao "Concluir e encaminhar": conclui a tarefa e faz nascer a proxima do gabarito, com o link
    da entrega. Uma linha REFAZER: na descricao manda a peca de volta para a Assistente (tarefa dela),
    que ao entregar reabre esta etapa para a mesma pessoa."""
    garantir(c)
    row = c.execute('SELECT * FROM tarefas WHERE id=?', (tarefa_id,)).fetchone()
    if not row:
        raise LookupError('tarefa %s nao existe' % tarefa_id)
    t = dict(row)
    if t['status'] != 'aberta':
        raise ValueError('a tarefa ja esta concluida')
    if not t.get('cadeia'):
        raise ValueError('a tarefa nao faz parte de uma cadeia')
    desc = str(t.get('descricao') or '')
    mref = _REFAZER.search(desc)
    links = [link.strip()] if (link or '').strip() else []
    if not links:
        ed = link_editavel(desc)
        links = [ed] if ed else _links_da_descricao(desc)[:1]
    agora = time.time()
    c.execute('UPDATE tarefa_responsaveis SET concluido_em=? WHERE tarefa_id=? AND concluido_em IS NULL', (agora, tarefa_id))
    c.execute("UPDATE tarefas SET status='concluida', concluida_em=?, atualizado_em=?, descricao=COALESCE(descricao,'') || ? WHERE id=?",
              (agora, agora, '\n\nENCAMINHADA por %s em %s%s' % (por, dt.datetime.now().strftime('%d/%m/%Y %H:%M'),
                                                                 (': ' + str(obs).strip()[:800]) if (obs or '').strip() else ''), tarefa_id))
    c.commit()
    orientacao = None
    nova = None
    if mref:
        # refazer: a Assistente ganha a tarefa dela; a entrega volta para esta etapa (mesma pessoa)
        orientacao = mref.group(1).strip()[:1500] or 'refazer conforme a orientacao da tarefa'
        ficha, _ = gabarito_da_cadeia(c, t['cadeia'])
        volta = etapa_do_gabarito(c, t['cadeia'], t.get('etapa')) or {
            'etapa': t.get('etapa'), 'tipo': t.get('tipo'), 'para': 'Advogada', 'rotulo': (t.get('titulo') or '').split(' - ')[0], 'texto': ''}
        volta['texto'] = (volta.get('texto') or '').strip() or 'Revisar a nova versao da Assistente.'
        tpl_ref = {'etapa': int(t.get('etapa') or 0), 'tipo': TT.PECA, 'para': 'Assistente', 'rotulo': 'REFAZER A PEÇA (Assistente)',
                   'skill_producao': volta.get('skill_producao') or skill_de_producao(ficha.get('ato')),
                   'texto': 'Assistente: refazer a peça conforme a orientação de %s: %s\nAo entregar, a tarefa de revisão volta para %s.'
                            % (por, orientacao, por)}
        historico(c, 'tarefa', tarefa_id, 'refazer', '%s pediu para refazer: %s' % (por, orientacao), por)
        nova = nascer_proxima(c, t, por, links, agenda=agenda, tpl=tpl_ref, hoje=hoje,
                              fila_extra={'refazer': orientacao, 'pedido_por': por, 'volta_para': volta})
    else:
        try:
            ao_concluir(c, tarefa_id, por, agenda)          # protocolo: [ok] no PF e atualizar cliente
        except Exception:
            pass
        nova = nascer_proxima(c, t, por, links, agenda=agenda, hoje=hoje)
    historico(c, 'tarefa', tarefa_id, 'encaminhada', 'encaminhada para #%s (%s, %s)' % (
        (nova or {}).get('id'), (nova or {}).get('tipo'), (nova or {}).get('para')) if nova else 'encaminhada: fim da cadeia', por)
    c.commit()
    return {'concluida': tarefa_id, 'nova': nova, 'refazer': orientacao, 'links': links}


def apos_concluir(c, tarefa_id, por, agenda=None, hoje=None):
    """Quem usa o botao Concluir comum numa tarefa da cadeia nao trava o fluxo: se o gabarito tem
    proxima etapa e nao ha tarefa aberta dela, ela nasce aqui (sem link informado, vai o editavel)."""
    row = c.execute('SELECT * FROM tarefas WHERE id=?', (tarefa_id,)).fetchone()
    if not row or row['status'] != 'concluida' or not row['cadeia']:
        return None
    t = dict(row)
    if c.execute("SELECT 1 FROM tarefas WHERE cadeia=? AND etapa>? AND status='aberta'", (t['cadeia'], int(t.get('etapa') or 0))).fetchone():
        return None
    if not proxima_da_cadeia(c, t['cadeia'], t.get('etapa')):
        return None
    ed = link_editavel(t.get('descricao'))
    return nascer_proxima(c, t, por, [ed] if ed else _links_da_descricao(t.get('descricao'))[:1], agenda=agenda, hoje=hoje)


def _so_automacao(c, tarefa_id):
    return c.execute('SELECT COUNT(*) FROM tarefa_responsaveis WHERE tarefa_id=? AND usuario_id != ?',
                     (tarefa_id, AUTOMACAO_ID)).fetchone()[0] == 0


def cadeia_da_tarefa(c, tarefa_id):
    """As irmas da mesma cadeia, para a tela mostrar 'etapa 2 de 4'."""
    garantir(c)
    t = c.execute('SELECT cadeia FROM tarefas WHERE id=?', (tarefa_id,)).fetchone()
    if not t or not t['cadeia']:
        return []
    out = []
    for r in c.execute('SELECT id, etapa, tipo, titulo, status, data FROM tarefas WHERE cadeia=? ORDER BY etapa, id',
                       (t['cadeia'],)):
        d = dict(r)
        d['responsaveis'] = [x[0] for x in c.execute(
            'SELECT nome FROM tarefa_responsaveis WHERE tarefa_id=?', (r['id'],))]
        out.append(d)
    return out


if __name__ == '__main__':
    COBRANCA_AUDIENCIA.update({'ALFA': 500.00, 'BETA': 500.00})          # empresas ficticias do teste
    SKILL_CONSULTORIA.update({'ALFA': 'consultoria-alfa', 'BETA': 'consultoria-beta'})
    hoje = dt.date(2026, 10, 2)

    def tipos(r):
        return [e['tipo'] for e in r['etapas']]

    def datas(r):
        return [e['data'] for e in r['etapas']]

    # --- cadeias (sem banco) ---
    m = {'ato': 'EMENDA A INICIAL', 'trilha': 'PECA', 'titularidade': 'NOSSO', 'vencimento': '2026-10-09',
         'prazo_fatal_interno': '2026-10-07', 'responsavel': 'Advogada', 'cliente': 'FULANA', 'parte_adversa': 'RESTAURANTE',
         'conferencia': {'providencia': 'Emendar a inicial.'}}
    r = cadeia_para(m, {'id': 78821, 'processo': '9552774-18.2026.5.02.1821'}, hoje=hoje)
    assert tipos(r) == ['PECA', 'REVISAO', 'AJUSTES', 'PROTOCOLO'], r
    assert datas(r) == ['2026-10-02', '2026-10-05', '2026-10-06', '2026-10-07'], datas(r)
    assert [e['para'] for e in r['etapas']] == ['Advogada', 'Assistente', 'Advogada', 'Advogada'], r     # emenda: sem a Advogada
    assert r['etapas'][0]['skill_producao'] == 'peticao-inicial-reclamante' and r['etapas'][1]['skill'] == 'revisao-inicial'
    assert r['pf']['marcador'] == 'intim:78821' and r['pf']['data'] == '2026-10-07' and not r['pf_paralelo']

    m2 = dict(m, ato='RECURSO ORDINARIO', vencimento='2026-10-14', prazo_fatal_interno='2026-10-09', responsavel='Advogada')
    r2 = cadeia_para(m2, {'id': 1, 'processo': 'x'}, hoje=hoje)
    assert tipos(r2) == ['ANALISE', 'PECA', 'REVISAO', 'AJUSTES', 'PROTOCOLO'], r2      # sem a etapa de estrategia da Advogada
    assert datas(r2) == ['2026-10-02', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09'], datas(r2)
    assert [e['para'] for e in r2['etapas']] == ['Assistente', 'Advogada', 'Assistente', 'Advogada', 'Advogada']
    assert r2['etapas'][0]['skill_producao'] == 'ro-recurso-ordinario' and r2['etapas'][2]['skill'] == 'revisao-recursos'
    assert 'REFAZER' in r2['etapas'][1]['texto'] and r2['etapas'][1]['rotulo'].startswith('REVISAR ESTRATEGIA E PECA')

    # sentenca com ED em paralelo: a cadeia e datada pelo prazo mais curto e ha dois PFs
    m2b = dict(m2, responsavel='Advogada', prazos=[
        {'vencimento': '2026-10-14', 'prazo_fatal_interno': '2026-10-09'},
        {'paralelo': True, 'vencimento': '2026-10-09', 'prazo_fatal_interno': '2026-10-07', 'ato': 'EMBARGOS DE DECLARACAO'}])
    r2b = cadeia_para(m2b, {'id': 500, 'processo': '0002'}, hoje=hoje)
    assert datas(r2b) == ['2026-10-02', '2026-10-02', '2026-10-05', '2026-10-06', '2026-10-07'], datas(r2b)
    assert r2b['pf']['data'] == '2026-10-09' and r2b['pf_paralelo']['data'] == '2026-10-07'
    assert r2b['pf_paralelo']['marcador'] == 'intim:500:ed' and 'prazo mais curto' in r2b['etapas'][0]['texto']

    m3 = dict(m, ato='MANIFESTACAO SOBRE ED', vencimento='2026-10-03', prazo_fatal_interno='2026-10-01')
    r3 = cadeia_para(m3, {'id': 2, 'processo': 'x'}, hoje=hoje)
    assert tipos(r3) == ['PECA', 'REVISAO', 'AJUSTES', 'PROTOCOLO'] and all(d == '2026-10-02' for d in datas(r3)), r3
    assert r3['etapas'][3]['para'] == 'Advogada' and r3['etapas'][0]['skill_producao'] is None
    r4 = cadeia_para(dict(m, ato='CUSTAS E DESPESAS', trilha='MEIO', responsavel='Advogada'), {'id': 3, 'processo': 'x'}, hoje=hoje)
    assert tipos(r4) == ['EMITIR GUIA', 'PROTOCOLO'], r4
    r5 = cadeia_para({'ato': 'CIENCIA', 'trilha': 'CIENCIA', 'titularidade': 'NOSSO',
                      'sugestao_conclusao': {'texto': 'SUGESTAO: concluir'}}, {'id': 4, 'processo': 'x'}, hoje=hoje)
    assert not r5['etapas'] and r5['motivo_sem_cadeia'].startswith('SUGESTAO')
    # despacho composto (a equipe 86458, 06/10/2026): juntada em 5 dias + audiencia inicial designada
    m6 = dict(m, ato='JUNTADA DE DOCUMENTOS', trilha='MEIO', responsavel='Advogada', vencimento='2026-10-15',
              prazo_fatal_interno='2026-10-13', conferencia={'providencia': 'Juntar CTPS digital e 3 holerites.'},
              audiencia={'data': '2026-12-01', 'hora': '10:05', 'modalidade': 'hibrida', 'especie': 'inicial'})
    r6 = cadeia_para(m6, {'id': 86458, 'processo': '9431272-85.2026.5.02.6259',
                          'texto': 'Designo a audiencia Inicial: 01/12/2026 10:05 na modalidade HIBRIDA'}, hoje=hoje)
    assert tipos(r6)[:2] == ['PETICAO DE MEIO', 'PROTOCOLO'] and 'COMUNICAR CLIENTE AUDIENCIA' in tipos(r6), tipos(r6)
    assert [e['etapa'] for e in r6['etapas']] == list(range(1, len(r6['etapas']) + 1)), [e['etapa'] for e in r6['etapas']]
    assert r6['familia'] == 'meio' and r6['pf']['data'] == '2026-10-13', r6
    aud_et = [e for e in r6['etapas'] if e['tipo'] == 'COMUNICAR CLIENTE AUDIENCIA'][0]
    assert aud_et['prazo_fatal'] == '2026-12-01' and '01/12/2026' in aud_et['texto'] and 'inicial' in aud_et['texto'], aud_et
    # ciencia pura + audiencia designada: a cadeia vira so a da audiencia
    r7 = cadeia_para({'ato': 'CIENCIA', 'trilha': 'CIENCIA', 'titularidade': 'NOSSO', 'cliente': 'FULANA',
                      'audiencia': {'data': '2026-12-01', 'hora': '', 'modalidade': '', 'especie': ''}}, {'id': 5, 'processo': 'x'}, hoje=hoje)
    assert r7['familia'] == 'audiencia' and r7['etapas'] and not r7['motivo_sem_cadeia'], r7
    r6 = cadeia_para(dict(m, parceiro={'nome': 'Dr. Parceiro Exemplo', 'modo': 'encaminhar'}), {'id': 5, 'processo': 'x'}, hoje=hoje)
    assert r6['etapas'][0]['tipo'] == 'RECADO PARA PARCEIRO' and r6['pf']
    assert tipos(r6) == ['RECADO PARA PARCEIRO', 'ACOMPANHAMENTO'] and [e['para'] for e in r6['etapas']] == ['Assistente', 'Advogada']
    m7 = dict(m, titularidade='CONTRARIA', conferencia={'vigiar_em': '2026-10-20', 'transito_em': '2026-10-20', 'tarefas_sugeridas': [
        {'para': 'Advogada', 'tipo': 'ACOMPANHAMENTO', 'vencimento': '2026-10-20', 'texto': 'vigiar'},
        {'para': 'Advogada', 'tipo': 'INICIAL EXECUCAO CUMPRIMENTO SENTENCA', 'vencimento': '2026-11-03',
         'vencimento_interno': '2026-10-30', 'condicional': True, 'texto': 'liquidar'}]})
    r7 = cadeia_para(m7, {'id': 6, 'processo': 'x'}, hoje=hoje)
    assert datas(r7) == ['2026-10-20', '2026-10-30', '2026-10-20'] and r7['etapas'][1]['condicional']
    r8 = cadeia_para(dict(m, ato='REPLICA'), {'id': 7, 'processo': 'x'}, hoje=hoje)
    assert r8['etapas'][0]['skill_producao'] == 'replica' and r8['etapas'][0]['rotulo'].startswith('V1 E REVISAO')
    assert r8['etapas'][1]['skill'] == 'revisao-integral' and len(r8['etapas']) == 4
    r9 = cadeia_para(dict(m, ato='RECURSO DE REVISTA', vencimento='2026-10-14', prazo_fatal_interno='2026-10-09'),
                     {'id': 9, 'processo': 'x'}, hoje=hoje)
    assert [e['para'] for e in r9['etapas']] == ['Assistente', 'Advogada', 'Assistente', 'Advogada', 'Advogada'], r9
    assert r9['etapas'][0]['skill_producao'] is None and r9['etapas'][2]['skill'] == 'revisao-recursos'   # RR ainda sem skill de redacao
    r10 = cadeia_para(dict(m, ato='CONTRARRAZOES DE RR', vencimento='2026-10-14', prazo_fatal_interno='2026-10-09'),
                      {'id': 10, 'processo': 'x'}, hoje=hoje)
    assert tipos(r10) == ['PECA', 'REVISAO', 'AJUSTES', 'PROTOCOLO'] and r10['etapas'][0]['skill_producao'] == 'contrarrazoes-ro'
    assert precisa_estrategia('EMBARGOS DE DECLARACAO') and precisa_estrategia('AGRAVO INTERNO')
    assert not precisa_estrategia('MANIFESTACAO SOBRE ED') and not precisa_estrategia('EMBARGOS A EXECUCAO')
    assert quem('Dra. Advogada')['id'] == REVISORA_ID and quem('Assistente')['id'] == AUTOMACAO_ID
    assert _responsaveis_de('Advogada, Titular') == [quem('Advogada'), quem('Titular')]

    # --- fluxo inteiro num banco em memoria ---
    VIGIA_LIGADO.update({k: True for k in VIGIA_LIGADO})      # os fluxos abaixo testam a automacao ligada
    def banco():
        b = sqlite3.connect(':memory:')
        b.row_factory = sqlite3.Row
        b.execute('''CREATE TABLE tarefas (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT, titulo TEXT, descricao TEXT,
            processo_numero TEXT, lawsuits_id TEXT, cliente TEXT, parte_adversa TEXT, pasta_drive TEXT, data TEXT, hora TEXT,
            prazo_fatal TEXT, prazo_interno TEXT, trilha TEXT, prioridade TEXT, status TEXT DEFAULT 'aberta', origem TEXT,
            origem_ref TEXT, criado_por TEXT, criado_em REAL, atualizado_em REAL, concluida_em REAL, evento_id TEXT)''')
        b.execute('CREATE UNIQUE INDEX ix ON tarefas(origem, origem_ref, tipo)')
        b.execute('''CREATE TABLE tarefa_responsaveis (id INTEGER PRIMARY KEY AUTOINCREMENT, tarefa_id INTEGER,
            usuario_id INTEGER, nome TEXT, concluido_em REAL, importante INTEGER DEFAULT 0, urgente INTEGER DEFAULT 0)''')
        b.execute('CREATE UNIQUE INDEX ixr ON tarefa_responsaveis(tarefa_id, usuario_id)')
        return b

    def gente_conclui(b, tid):
        b.execute('UPDATE tarefa_responsaveis SET concluido_em=1 WHERE tarefa_id=?', (tid,))
        b.execute("UPDATE tarefas SET status='concluida' WHERE id=?", (tid,))
        return ao_concluir(b, tid, 'pessoa (teste)')

    def tid_de(b, cadeia, tipo):
        return b.execute('SELECT id FROM tarefas WHERE cadeia=? AND tipo=?', (cadeia, tipo)).fetchone()[0]

    # 1) recurso: a Assistente analisa, decide e redige -> Advogada revisa estrategia e peca -> revisao final -> ajustes -> protocolo
    b = banco()
    it = {'id': 500, 'processo': '0002'}
    rel = cadastrar(b, it, m2b, r2b['etapas'], 'Advogada (teste)', agenda=None)
    assert len(rel['tarefas']) == 5 and [x['fila'] for x in rel['tarefas']] == ['analise', None, None, None, None], rel
    assert cadastrar(b, it, m2b, r2b['etapas'], 'x', agenda=None)['tarefas'] == [], 'fechamento duplicou'
    ea = entregar_analise(b, tid_de(b, 'intim:500', 'ANALISE'), 'Ha omissao no capitulo das horas extras; embargos antes do recurso.',
                          'EMBARGOS DE DECLARACAO', {'peca': 'https://drive.google.com/v1', 'analise': 'https://drive.google.com/analise'})
    assert ea['fechou_a_propria'] and ea['passagem']['proxima']['tipo'] == 'PECA', ea
    peca = tid_de(b, 'intim:500', 'PECA')
    dp = b.execute('SELECT descricao, prioridade FROM tarefas WHERE id=?', (peca,)).fetchone()
    assert dp['descricao'].startswith('PECA: EMBARGOS DE DECLARACAO') and 'ESTRATEGIA ADOTADA PELA CLAUDIA E POR QUE' in dp['descricao']
    assert 'Ha omissao no capitulo' in dp['descricao'] and 'https://drive.google.com/v1' in dp['descricao'] and dp['prioridade'] == 'importante'
    assert not b.execute("SELECT 1 FROM producao_fila WHERE modo='v1'").fetchone()        # a redacao foi no trabalho de analise
    pp = gente_conclui(b, peca)                                                           # a Advogada concordou e revisou
    assert pp['revisao_fila']['status'] == 'aguardando' and len(pp['revisao_fila']['links']) == 2 and pp['proxima']['tipo'] == 'REVISAO', pp
    er = entregar_revisao(b, tid_de(b, 'intim:500', 'REVISAO'), {'relatorio': 'https://d/rel', 'peca_com_ajustes': 'https://d/marc'},
                          'APROVADA COM RESSALVAS', hoje=hoje)
    assert er['fechou_a_propria'] and er['passagem']['proxima']['tipo'] == 'AJUSTES' and not er['decisao_advogada'], er
    aj = b.execute("SELECT id, descricao FROM tarefas WHERE cadeia='intim:500' AND tipo='AJUSTES'").fetchone()
    assert 'APROVADA COM RESSALVAS' in aj['descricao'] and 'https://d/rel' in aj['descricao']
    assert gente_conclui(b, aj['id'])['proxima']['tipo'] == 'PROTOCOLO'
    fim = gente_conclui(b, tid_de(b, 'intim:500', 'PROTOCOLO'))
    assert fim['atualizar_cliente'] and b.execute("SELECT origem_ref FROM tarefas WHERE tipo='ATUALIZAR CLIENTE'").fetchone()[0] == '500'
    assert b.execute("SELECT r.usuario_id FROM tarefa_responsaveis r JOIN tarefas t ON t.id=r.tarefa_id "
                     "WHERE t.tipo='ATUALIZAR CLIENTE'").fetchone()[0] == AUTOMACAO_ID            # quem atualiza e a automacao
    assert fim['fiscalizar_atualizacao'] and b.execute("SELECT 1 FROM producao_fila WHERE modo='atualizacao'").fetchone()
    eat = entregar_atualizacao(b, fim['atualizar_cliente'], 'Mensagem enviada ao 11 98888-0000 em 08/10 09h, com o link do Portal.')
    fz = b.execute('SELECT descricao, status FROM tarefas WHERE id=?', (fim['fiscalizar_atualizacao'],)).fetchone()
    assert eat['fechou_a_propria'] and 'O QUE A AUTOMACAO ENVIOU AO CLIENTE' in fz['descricao'] and fz['status'] == 'aberta'

    # 1b) a Advogada prefere outro caminho: REFAZER devolve para a Assistente e nada segue adiante
    b = banco()
    cadastrar(b, {'id': 502, 'processo': '0004'}, m2b, cadeia_para(m2b, {'id': 502, 'processo': '0004'}, hoje=hoje)['etapas'], 'x')
    entregar_analise(b, tid_de(b, 'intim:502', 'ANALISE'), 'Embargos primeiro.', 'EMBARGOS DE DECLARACAO', {'peca': 'https://drive.google.com/v1'})
    peca = tid_de(b, 'intim:502', 'PECA')
    b.execute("UPDATE tarefas SET descricao = descricao || ? WHERE id=?", ('\nREFAZER: recorrer direto, sem embargos', peca))
    pr = gente_conclui(b, peca)
    assert pr['refazer'] == 'recorrer direto, sem embargos' and pr['proxima'] is None and not pr['revisao_fila'], pr
    lp = b.execute('SELECT status, descricao FROM tarefas WHERE id=?', (peca,)).fetchone()
    assert lp['status'] == 'aberta' and 'REFAZER PEDIDO por pessoa (teste)' in lp['descricao'] and 'aguardando a nova versao da Assistente' in lp['descricao']
    fila = b.execute("SELECT tarefa_id, modo, status, resultado FROM producao_fila WHERE modo='v1'").fetchone()
    assert fila['tarefa_id'] == peca and fila['status'] == 'aguardando' and 'recorrer direto' in fila['resultado']
    assert b.execute('SELECT concluido_em FROM tarefa_responsaveis WHERE tarefa_id=?', (peca,)).fetchone()[0] is None
    assert not b.execute('SELECT 1 FROM revisao_fila').fetchone()

    # 2) a Assistente conclui por NAO RECORRER e a Advogada concorda: nada segue, a Advogada decide
    b = banco()
    cadastrar(b, {'id': 501, 'processo': '0003'}, m2b, cadeia_para(m2b, {'id': 501, 'processo': '0003'}, hoje=hoje)['etapas'], 'x')
    entregar_analise(b, tid_de(b, 'intim:501', 'ANALISE'), 'Sentenca favoravel; risco baixo.', 'NAO RECORRER')
    pn = gente_conclui(b, tid_de(b, 'intim:501', 'PECA'))
    assert pn['decisao_advogada'] and not pn['revisao_fila'] and not b.execute('SELECT 1 FROM revisao_fila').fetchone(), pn

    # 3) razoes finais: tarefa cadastrada depois da ata -> varredura monta a MESMA cadeia
    b = banco()
    b.execute("INSERT INTO tarefas (tipo, titulo, processo_numero, cliente, parte_adversa, prazo_fatal, status, origem) "
              "VALUES ('RAZÕES FINAIS', 'RAZÕES FINAIS - DIOSME x USIMINAS', '0001', 'DIOSME', 'USIMINAS', '2026-10-16', 'aberta', 'manual')")
    b.execute("INSERT INTO tarefas (tipo, titulo, status, origem) VALUES ('PROTOCOLO', 'outra coisa', 'aberta', 'manual')")
    v = varrer_tarefas_para_producao(b, hoje=hoje)
    assert len(v) == 1 and v[0]['skill'] == 'razoes-finais', v
    assert [(x['tipo'], x['para'], x['data']) for x in v[0]['etapas']] == [
        ('REVISAO', 'Assistente', '2026-10-09'), ('AJUSTES', 'Advogada', '2026-10-13'), ('PROTOCOLO', 'Advogada', '2026-10-14')], v   # 12/10 e feriado
    assert varrer_tarefas_para_producao(b, hoje=hoje) == [], 'varredura repetiu'
    assert tuple(b.execute('SELECT modo, status FROM producao_fila').fetchone()) == ('v1', 'aguardando')
    assert b.execute('SELECT nome FROM tarefa_responsaveis WHERE tarefa_id=1').fetchone()[0] == 'Advogada'
    entregar_v1(b, 1, {'peca': 'https://drive.google.com/rf'})
    p1 = gente_conclui(b, 1)
    assert p1['proxima']['tipo'] == 'REVISAO' and p1['revisao_fila']['status'] == 'aguardando', p1
    er = entregar_revisao(b, p1['proxima']['id'], {'relatorio': 'https://d/r'}, 'APROVADA', hoje=hoje)
    assert er['passagem']['proxima']['tipo'] == 'AJUSTES'
    gente_conclui(b, tid_de(b, 'tarefa:1', 'AJUSTES'))
    fim = gente_conclui(b, tid_de(b, 'tarefa:1', 'PROTOCOLO'))
    assert fim['atualizar_cliente'] and b.execute("SELECT origem_ref FROM tarefas WHERE tipo='ATUALIZAR CLIENTE'").fetchone()[0] == 'tarefa:1'

    # 3b) liquidacao: mandar ao contador segura a redacao; quando o calculo volta, a peca entra na fila
    mc = dict(m, ato='APRESENTACAO DE CALCULOS', vencimento='2026-10-14', prazo_fatal_interno='2026-10-09')
    rc = cadeia_para(mc, {'id': 600, 'processo': '0004'}, hoje=hoje)
    assert tipos(rc) == ['ENVIAR AO CONTADOR', 'PECA', 'REVISAO', 'AJUSTES', 'PROTOCOLO'], rc
    assert rc['etapas'][0]['condicional'] and rc['etapas'][0]['para'] == 'Advogada' and rc['etapas'][0]['data'] == '2026-10-02'
    assert rc['etapas'][1]['skill_producao'] == 'liquidacao-previa'
    assert precisa_contador('CONTESTAR CALCULOS (ART. 879 2o)') and precisa_contador('INICIAL EXECUCAO CUMPRIMENTO SENTENCA')
    assert not precisa_contador('RECURSO ORDINARIO') and not precisa_contador('REPLICA')
    b = banco()
    relc = cadastrar(b, {'id': 600, 'processo': '0004'}, mc, rc['etapas'], 'Advogada (teste)')
    assert [x['fila'] for x in relc['tarefas']] == [None] * 5, 'redigiu antes do contador'
    cont = tid_de(b, 'intim:600', 'ENVIAR AO CONTADOR')
    b.execute("UPDATE tarefas SET descricao = descricao || ' planilha: https://drive.google.com/planilha' WHERE id=?", (cont,))
    pc = gente_conclui(b, cont)
    assert pc['producao_fila']['skill'] == 'liquidacao-previa' and pc['producao_fila']['calculo_do_contador'], pc
    assert 'Calculo do contador' in b.execute("SELECT descricao FROM tarefas WHERE cadeia='intim:600' AND tipo='PECA'").fetchone()[0]
    # sem a etapa do contador (a pessoa tirou no fechamento): a v1 entra na fila na hora
    b = banco()
    rels = cadastrar(b, {'id': 601, 'processo': '0005'}, mc, rc['etapas'][1:], 'Advogada (teste)')
    assert rels['tarefas'][0]['fila'] == 'v1', rels
    # vigia do transito com liquidacao depois: a tarefa do contador nasce condicional
    assert tipos(r7) == ['ACOMPANHAMENTO', 'INICIAL EXECUCAO CUMPRIMENTO SENTENCA', 'ENVIAR AO CONTADOR'] and r7['etapas'][2]['condicional']

    # 3c) alvara: pela publicacao (fechamento) e pelo vigia dos autos
    ma = {'ato': 'CIENCIA / MERO EXPEDIENTE', 'trilha': 'CIENCIA', 'titularidade': 'NOSSO', 'cliente': 'JOSE', 'parte_adversa': 'TRANSP'}
    ia = {'id': 700, 'processo': '0006', 'data': '2026-10-02', 'resumo': 'Expedido alvará de levantamento em favor do reclamante.'}
    ra = cadeia_para(ma, ia, hoje=hoje)
    assert ra['familia'] == 'alvara' and tipos(ra) == ['CALCULO DE HONORARIOS', 'LANCAMENTO DE RECEITA'], ra
    assert [e['para'] for e in ra['etapas']] == ['Assistente', 'Titular']
    assert cadeia_para(dict(ma, titularidade='CONTRARIA'), ia, hoje=hoje)['familia'] == 'ciencia'      # alvara da outra parte
    assert cadeia_para(ma, dict(ia, resumo='Ciencia do despacho.'), hoje=hoje)['familia'] == 'ciencia'
    b = banco()
    rela = cadastrar(b, ia, ma, ra['etapas'], 'Advogada (teste)')
    assert [x['fila'] for x in rela['tarefas']] == ['honorarios', None] and rela['tarefas'][1]['responsaveis'] == ['Titular']
    eh = entregar_calculo_honorarios(b, tid_de(b, 'intim:700', 'CALCULO DE HONORARIOS'),
                                     'Bruto 10.000,00; honorarios 30% = 3.000,00; liquido do cliente 7.000,00.')
    assert eh['fechou_a_propria'] and eh['passagem']['proxima']['tipo'] == 'LANCAMENTO DE RECEITA', eh
    assert 'liquido do cliente 7.000,00' in b.execute("SELECT descricao FROM tarefas WHERE tipo='LANCAMENTO DE RECEITA'").fetchone()[0]
    autos = [{'id': 1, 'data': '2026-09-10', 'documento': {'nome': 'Sentença', 'tipo_documento': 'Sentença'}},
             {'id': 2, 'data': '2026-10-01', 'documento': {'nome': 'Alvará de levantamento - reclamante', 'tipo_documento': 'Alvará'}},
             {'id': 3, 'data': '2026-08-01', 'movimento': 'Expedido mandado de levantamento'}]
    ev = alvara_nos_autos(autos, desde='2026-09-01')
    assert [x['id'] for x in ev] == [2], ev
    ab = abrir_tarefas_de_alvara(b, '0007', ev[0], 'MARIA', 'LOG', hoje=hoje)
    assert [x['tipo'] for x in ab['tarefas']] == ['CALCULO DE HONORARIOS', 'LANCAMENTO DE RECEITA'] and ab['cadeia'] == 'alvara:0007:2026-10-01'
    assert abrir_tarefas_de_alvara(b, '0007', ev[0], 'MARIA', 'LOG', hoje=hoje) is None, 'alvara duplicou'

    # 3d) audiencia (POP 18): automacao comunica, Advogada fiscaliza, roteiro para a Advogada, clientes empresa pagam
    mau = {'ato': 'AUDIENCIA DESIGNADA', 'trilha': 'CIENCIA', 'titularidade': 'NOSSO', 'cliente': 'JOSE DA SILVA',
           'conferencia': {'subtipo': 'una', 'modalidade': 'virtual'}}
    iau = {'id': 800, 'processo': '0008', 'compromisso': {'tem': True, 'data': '2026-11-26', 'hora': '14:30', 'especie': 'UNA'}}
    rau = cadeia_para(mau, iau, hoje=hoje)
    assert rau['familia'] == 'audiencia', rau          # mesmo com trilha de ciencia, audiencia tem providencia
    assert tipos(rau) == ['COMUNICAR CLIENTE AUDIENCIA', 'ACOMPANHAMENTO', 'ROTEIRO', 'ESTUDAR ROTEIRO'], rau   # reclamante nao paga
    assert [e['para'] for e in rau['etapas']] == ['Assistente', 'Advogada', 'Assistente', 'Advogada'] and 'PARTE 2' in rau['etapas'][0]['texto']
    assert rau['etapas'][1]['data'] == '2026-10-05' and rau['etapas'][1]['prazo_fatal'] == '2026-11-26'
    assert rau['etapas'][3]['data'] == '2026-11-23' and 'testemunha' in rau['etapas'][2]['texto']      # estudo 3 uteis antes
    for cli in ('ALFA COMERCIO EXEMPLO LTDA', 'BETA SERVICOS EXEMPLO LTDA'):
        rr = cadeia_para(dict(mau, cliente=cli, conferencia={'subtipo': 'inicial'}), iau, hoje=hoje)
        assert tipos(rr) == ['COMUNICAR CLIENTE AUDIENCIA', 'ACOMPANHAMENTO', 'ROTEIRO', 'ESTUDAR ROTEIRO', 'EMITIR BOLETO'], rr
        assert rr['etapas'][4]['para'] == 'Titular' and '500,00' in rr['etapas'][4]['texto'] and '26/11/2026' in rr['etapas'][4]['texto']
        assert rr['etapas'][4]['prazo_fatal'] == '2026-11-26' and 'PARTE 2' not in rr['etapas'][0]['texto']
    assert cobranca_da_audiencia('BETAX TRANSPORTES')[0] is None and cobranca_da_audiencia('Alfa')[1] == 500.0
    b = banco()
    mrx = dict(mau, cliente='ALFA COMERCIO')
    relau = cadastrar(b, iau, mrx, cadeia_para(mrx, iau, hoje=hoje)['etapas'], 'x')
    assert [x['fila'] for x in relau['tarefas']] == ['pop18', None, 'roteiro', None, None], relau
    assert relau['tarefas'][4]['responsaveis'] == ['Titular'] and relau['tarefas'][3]['responsaveis'] == ['Advogada']
    ep = entregar_pop18(b, tid_de(b, 'intim:800', 'COMUNICAR CLIENTE AUDIENCIA'), 'Parte 1 enviada ao 11 99999-0000 em 02/10 10h.')
    assert ep['fechou_a_propria'] and ep['fiscalizacao_em'], ep
    rg = registrar_resposta_audiencia(b, 'intim:800', 'cliente', 'Confirmo presenca.', '02/10 11:00')
    fisc = b.execute("SELECT descricao, prioridade, status FROM tarefas WHERE cadeia='intim:800' AND tipo='ACOMPANHAMENTO'").fetchone()
    assert rg['aberta'] and 'O QUE A AUTOMACAO ENVIOU' in fisc['descricao'] and 'Confirmo presenca' in fisc['descricao']
    assert fisc['prioridade'] == 'importante' and fisc['status'] == 'aberta'            # quem conclui e a Advogada
    ero = entregar_roteiro(b, tid_de(b, 'intim:800', 'ROTEIRO'), 'Atencao ao cartao de ponto de marco.', {'roteiro': 'https://d/rot'})
    est = b.execute("SELECT descricao, status FROM tarefas WHERE cadeia='intim:800' AND tipo='ESTUDAR ROTEIRO'").fetchone()
    assert ero['fechou_a_propria'] and 'PONTOS DE ATENCAO' in est['descricao'] and 'https://d/rot' in est['descricao'] and est['status'] == 'aberta'

    # 3e) sessao de julgamento em pauta: inscricao (Advogada), roteiro da sustentacao (automacao), estudo (Advogada)
    msu = {'ato': 'CIENCIA / MERO EXPEDIENTE', 'trilha': 'CIENCIA', 'titularidade': 'NOSSO', 'cliente': 'JOSE'}
    isu = {'id': 900, 'processo': '0009', 'resumo': 'Processo incluído na pauta de julgamento da sessão de 20/10/2026.',
           'compromisso': {'tem': True, 'tipo': 'julgamento', 'data': '2026-10-20'}}
    rsu = cadeia_para(msu, isu, hoje=hoje)
    assert rsu['familia'] == 'sustentacao' and tipos(rsu) == ['INSCRICAO SUSTENTACAO ORAL', 'ROTEIRO', 'ESTUDAR ROTEIRO'], rsu
    assert [e['para'] for e in rsu['etapas']] == ['Advogada', 'Assistente', 'Advogada'] and rsu['etapas'][0]['condicional']
    assert rsu['etapas'][2]['data'] == '2026-10-15' and 'sustentacao oral' in rsu['etapas'][1]['texto']
    assert cadeia_para(dict(msu, copia_de_adverso=True), isu, hoje=hoje)['familia'] == 'ciencia'
    assert cadeia_para(dict(mau, ato='PERICIA DESIGNADA'), iau, hoje=hoje)['etapas'] == []

    # 3e2) parceiro: a automacao encaminha e acompanha; a Advogada fiscaliza e e quem conclui
    b = banco()
    mp = dict(m, parceiro={'nome': 'Dr. Parceiro Exemplo', 'modo': 'encaminhar'})
    relp = cadastrar(b, {'id': 950, 'processo': '0010'}, mp, r6['etapas'], 'x')
    assert [x['fila'] for x in relp['tarefas']] == ['parceiro', None], relp
    erp = entregar_recado_parceiro(b, tid_de(b, 'intim:950', 'RECADO PARA PARCEIRO'), 'Encaminhado ao Dr. Hélio em 02/10 10h.')
    registrar_resposta(b, 'intim:950', 'Dr. Hélio', 'Ciente, protocolo ate quarta.')
    fp = b.execute("SELECT descricao, status FROM tarefas WHERE cadeia='intim:950' AND tipo='ACOMPANHAMENTO'").fetchone()
    assert erp['fechou_a_propria'] and 'ENVIOU AO PARCEIRO' in fp['descricao'] and 'protocolo ate quarta' in fp['descricao'] and fp['status'] == 'aberta'

    # 3f) consultoria pelo canal central: relatorio da automacao, caso no sistema e no Portal, validacao Dra. Advogada
    b = banco()
    b.execute("ALTER TABLE tarefas ADD COLUMN conversa_id INTEGER")
    b.execute("ALTER TABLE tarefas ADD COLUMN caso_id INTEGER")
    b.execute("""CREATE TABLE casos (id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT, tipo TEXT, area TEXT, cliente_nome TEXT,
        responsavel TEXT, responsavel_id INTEGER, status TEXT, notas TEXT, criado_por TEXT, criado_em REAL, atualizado_em REAL,
        conversa_id INTEGER, origem TEXT)""")
    b.execute("""CREATE TABLE portal_painel_consultoria (id INTEGER PRIMARY KEY AUTOINCREMENT, cliente_id INTEGER, titulo TEXT,
        texto TEXT, ordem INTEGER, criado_em REAL, desde TEXT, tipo TEXT)""")
    ac = abrir_consultoria(b, 'ALFA COMERCIO EXEMPLO', 'Funcionario faltou tres dias sem atestado; posso advertir?',
                           'precisa de resposta hoje', conversa_id=321, hoje=hoje)
    assert ac['skill'] == 'consultoria-alfa' and ac['urgente'] and ac['cadeia'] == 'consultoria:321:2026-10-02', ac
    assert abrir_consultoria(b, 'ALFA COMERCIO EXEMPLO', 'de novo', '', conversa_id=321, hoje=hoje) is None
    assert b.execute("SELECT modo FROM producao_fila").fetchone()[0] == 'consultoria'
    ec = entregar_consultoria(b, ac['tarefa'], 'Faltas injustificadas e advertencia', 'Cabe advertencia escrita; ver art. 482.',
                              {'relatorio': 'https://d/cons'}, portal_cliente_id=556, hoje=hoje)
    assert ec['caso'] and ec['portal'] and ec['fechou_a_propria'] and not ec['avisos'], ec
    val = b.execute("SELECT id, descricao, caso_id, status FROM tarefas WHERE tipo='CONSULTORIA'").fetchone()
    assert 'RELATORIO DA AUTOMACAO' in val['descricao'] and val['caso_id'] == ec['caso'] and val['status'] == 'aberta'
    assert sorted(r[0] for r in b.execute('SELECT nome FROM tarefa_responsaveis WHERE tarefa_id=?', (val['id'],))) == ['Advogada']
    caso = b.execute('SELECT tipo, cliente_nome, responsavel, conversa_id FROM casos').fetchone()
    assert tuple(caso) == ('consultoria', 'ALFA COMERCIO EXEMPLO', 'Advogada', 321), tuple(caso)
    pt = b.execute('SELECT cliente_id, tipo, texto FROM portal_painel_consultoria').fetchone()
    assert pt['cliente_id'] == 556 and pt['tipo'] == 'caso' and 'art. 482' not in pt['texto']      # o Portal so mostra o status
    assert abrir_consultoria(b, 'BETA SERVICOS', 'duvida', '', conversa_id=9, hoje=hoje)['skill'] == 'consultoria-beta'

    # 3g) pos-audiencia: a ata vira providencias, a Advogada valida, as pecas ganham a cadeia
    ata = ('Encerrada a instrução processual. Razões finais por memoriais no prazo comum de 5 dias. Defiro a produção de prova '
           'pericial e nomeio perito o Dr. Fulano. As partes poderão apresentar quesitos em cinco dias. Nada mais.')
    pv = providencias_da_ata(ata, '2026-10-06')
    assert [x['tipo'] for x in pv] == ['RAZOES FINAIS', 'QUESITOS', 'PERICIA'], pv
    assert pv[0]['dias'] == 5 and pv[0]['prazo'] == '2026-10-14' and pv[1]['prazo'] == '2026-10-14' and pv[2]['prazo'] is None   # 12/10 feriado
    assert [x['tipo'] for x in providencias_da_ata('Vista ao reclamante da defesa e documentos por 10 dias.', '2026-10-06')] == ['REPLICA']
    assert providencias_da_ata('Audiencia realizada. Nada a deliberar.', '2026-10-06') == []
    b = banco()
    va = abrir_validacao_pos_audiencia(b, '0011', '2026-10-06', pv, 'DIOSME', 'USIMINAS', 'https://d/ata', hoje=hoje)
    assert va['providencias'] == ['RAZOES FINAIS', 'QUESITOS', 'PERICIA'] and va['cadeia'] == 'audiencia:0011:2026-10-06'
    assert abrir_validacao_pos_audiencia(b, '0011', '2026-10-06', pv, hoje=hoje) is None, 'validacao duplicou'
    dv = b.execute('SELECT descricao, prazo_fatal FROM tarefas WHERE id=?', (va['tarefa'],)).fetchone()
    assert dv['descricao'].startswith('PROVIDENCIA: RAZOES FINAIS | PRAZO: 2026-10-14') and dv['prazo_fatal'] == '2026-10-14'
    assert b.execute('SELECT nome FROM tarefa_responsaveis WHERE tarefa_id=?', (va['tarefa'],)).fetchone()[0] == 'Advogada'
    # a Advogada tira a linha dos quesitos, corrige a data das razoes finais e conclui
    nova = dv['descricao'].replace('PROVIDENCIA: QUESITOS | PRAZO: 2026-10-14\n', '').replace(
        'PROVIDENCIA: RAZOES FINAIS | PRAZO: 2026-10-14', 'PROVIDÊNCIA: RAZÕES FINAIS | PRAZO: 16/10/2026')
    b.execute('UPDATE tarefas SET descricao=? WHERE id=?', (nova, va['tarefa']))
    pvz = gente_conclui(b, va['tarefa'])
    assert [(x['tipo'], x['para'], x['prazo']) for x in pvz['providencias']] == [
        ('RAZOES FINAIS', 'Advogada', '2026-10-16'), ('PERICIA DESIGNADA', 'Advogada', None)], pvz
    vv = varrer_tarefas_para_producao(b, hoje=hoje)                   # a peca validada ganha a cadeia e a v1
    assert len(vv) == 1 and vv[0]['skill'] == 'razoes-finais' and [x['tipo'] for x in vv[0]['etapas']] == ['REVISAO', 'AJUSTES', 'PROTOCOLO'], vv
    # tarefa do espelho do ADVBOX nao e varrida; quesitos (sem skill) ganha cadeia sem fila de redacao
    b.execute("INSERT INTO tarefas (tipo, titulo, prazo_fatal, status, origem, origem_ref) VALUES ('RAZÕES FINAIS', 'do advbox', '2026-10-20', 'aberta', 'advbox', '999')")
    b.execute("INSERT INTO tarefas (tipo, titulo, prazo_fatal, status, origem) VALUES ('QUESITOS', 'quesitos', '2026-10-20', 'aberta', 'manual')")
    vq = varrer_tarefas_para_producao(b, hoje=hoje)
    assert [x['tipo'] for x in vq] == ['QUESITOS'] and vq[0]['skill'] is None, vq
    assert b.execute("SELECT status FROM producao_fila ORDER BY id DESC LIMIT 1").fetchone()[0] == 'sem-skill'
    assert b.execute("SELECT cadeia FROM tarefas WHERE origem='advbox'").fetchone()[0] is None

    # 3h) PLANTAO: com o vigia desligado, a pessoa entra junto e a automacao nao fecha a tarefa
    VIGIA_LIGADO.update({k: False for k in VIGIA_LIGADO})
    b = banco()
    relpl = cadastrar(b, {'id': 500, 'processo': '0002'}, m2b, r2b['etapas'], 'Advogada (teste)')

    def donos(tipo):
        return sorted(r[0] for r in b.execute('SELECT r.nome FROM tarefa_responsaveis r JOIN tarefas t ON t.id=r.tarefa_id '
                                              "WHERE t.cadeia='intim:500' AND t.tipo=?", (tipo,)))
    assert donos('ANALISE') == [AUTOMACAO, 'Advogada'] and donos('REVISAO') == [AUTOMACAO, 'Advogada'], (donos('ANALISE'), donos('REVISAO'))
    assert donos('PECA') == ['Advogada'] and donos('PROTOCOLO') == ['Advogada']
    assert 'PLANTAO' in b.execute("SELECT descricao FROM tarefas WHERE cadeia='intim:500' AND tipo='REVISAO'").fetchone()[0]
    epl = entregar_analise(b, tid_de(b, 'intim:500', 'ANALISE'), 'Estrategia proposta.', 'RECURSO ORDINARIO')
    assert not epl['fechou_a_propria'], epl                       # tem gente na tarefa: quem conclui e a Advogada
    assert b.execute("SELECT status FROM tarefas WHERE cadeia='intim:500' AND tipo='ANALISE'").fetchone()[0] == 'aberta'
    assert b.execute("SELECT descricao FROM tarefas WHERE cadeia='intim:500' AND tipo='PECA'").fetchone()[0].startswith('PECA: RECURSO ORDINARIO')
    VIGIA_LIGADO.update({k: True for k in VIGIA_LIGADO})

    # 3i) observacoes da conferencia vao para a tarefa de quem executa; manifestacao sobre ED e peca simples
    med = dict(m, ato='MANIFESTACAO SOBRE ED', vencimento='2026-10-13', prazo_fatal_interno='2026-10-08')
    red = cadeia_para(med, {'id': 79801, 'processo': '0012740'}, hoje=dt.date(2026, 10, 5))
    assert tipos(red) == ['PECA', 'REVISAO', 'AJUSTES', 'PROTOCOLO'] and red['etapas'][3]['para'] == 'Advogada', red
    assert datas(red) == ['2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08'] and red['pf']['data'] == '2026-10-08'
    b = banco()
    eto = [dict(e) for e in red['etapas']]
    eto[0]['obs'] = 'Responder so o ponto dos pedagios.'
    cadastrar(b, {'id': 79801, 'processo': '0012740'}, med, eto, 'Advogada', observacao='Embargos da MJL, mov. 97.')
    d1 = b.execute("SELECT descricao FROM tarefas WHERE cadeia='intim:79801' AND tipo='PECA'").fetchone()[0]
    d4 = b.execute("SELECT descricao FROM tarefas WHERE cadeia='intim:79801' AND tipo='PROTOCOLO'").fetchone()[0]
    assert 'OBSERVACAO DA CONFERENCIA (Advogada): Responder so o ponto dos pedagios.' in d1 and 'OBSERVACAO DA CONFERENCIA' not in d4.replace('OBSERVACAO GERAL', '')
    assert 'OBSERVACAO GERAL DA CONFERENCIA (Advogada): Embargos da MJL, mov. 97.' in d1 and 'Embargos da MJL, mov. 97.' in d4

    # 4) NAO APROVADA chama a Advogada; tarefa de revisao com gente nao e fechada pela automacao
    b = banco()
    garantir(b)
    b.execute("INSERT INTO tarefas (tipo, titulo, status, origem, origem_ref, cadeia, etapa) "
              "VALUES ('REVISAO', 'REVISAO - X', 'aberta', 'motor', '77', 'intim:77', 2)")
    r77 = tid_de(b, 'intim:77', 'REVISAO')
    b.execute("INSERT INTO tarefa_responsaveis (tarefa_id, usuario_id, nome) VALUES (?, 2, 'Advogada')", (r77,))
    er2 = entregar_revisao(b, r77, {}, 'NÃO APROVADA', hoje=hoje)
    assert er2['decisao_advogada'] and not er2['fechou_a_propria'], er2
    assert b.execute('SELECT status FROM tarefas WHERE id=?', (r77,)).fetchone()[0] == 'aberta'
    # 5) passo a passo (07/10/2026): gabarito na ficha, encaminhar, Assistente sozinha, refazer volta para a Advogada
    b = banco()
    garantir(b)
    gab = [{'etapa': 1, 'tipo': 'REVISAO DE PEÇA', 'para': 'Advogada', 'data': '2026-10-07', 'rotulo': 'REVISAR A PEÇA DA CLÁUDIA'},
           {'etapa': 2, 'tipo': 'REVISAO', 'para': 'Assistente', 'data': '2026-10-07', 'rotulo': 'REVISÃO DA CONTROLADORIA', 'skill': 'revisao-recursos', 'texto': 'Assistente: revisar.'},
           {'etapa': 3, 'tipo': 'AJUSTES', 'para': 'Advogada', 'data': '2026-10-08', 'rotulo': 'AJUSTES FINAIS', 'texto': 'Advogada, aplicar as ressalvas.'},
           {'etapa': 4, 'tipo': 'PROTOCOLO', 'para': 'Advogada', 'data': '2026-10-09', 'prazo_fatal': '2026-10-14', 'rotulo': 'PROTOCOLO', 'texto': 'Advogada, protocolar.'}]
    b.execute("INSERT INTO motor_fichas (cadeia, ficha, criado_em) VALUES ('intim:900', ?, 0)",
              (json.dumps({'processo': '0009', 'cliente': 'CLEBER', 'parte_adversa': 'SARAIVA', 'ato': 'EMBARGOS DE DECLARACAO', 'etapas': gab}),))
    b.execute("INSERT INTO tarefas (tipo, titulo, descricao, status, origem, origem_ref, cadeia, etapa, processo_numero, cliente, parte_adversa, prazo_fatal) "
              "VALUES ('REVISAO DE PEÇA', 'REVISAR - CLEBER x SARAIVA - 0009', 'Peça: https://docs.google.com/document/d/EDIT1/edit\nDOCX: https://drive.google.com/file/d/D1/view', "
              "'aberta', 'motor', '900', 'intim:900', 1, '0009', 'CLEBER', 'SARAIVA', '2026-10-14')")
    t1 = tid_de(b, 'intim:900', 'REVISAO DE PEÇA')
    b.execute("INSERT INTO tarefa_responsaveis (tarefa_id, usuario_id, nome) VALUES (?, 5, 'Advogada')", (t1,))
    rp = resumo_proxima(b, dict(b.execute('SELECT * FROM tarefas WHERE id=?', (t1,)).fetchone()))
    assert rp and rp['tipo'] == 'REVISAO' and rp['automacao'] and rp['para'].startswith('Assistente') and rp['pede_link'], rp
    e1 = encaminhar(b, t1, 'Advogada', hoje=hoje)
    assert b.execute('SELECT status FROM tarefas WHERE id=?', (t1,)).fetchone()[0] == 'concluida'
    assert e1['nova']['tipo'] == 'REVISAO' and e1['nova']['automacao'] and e1['nova']['fila'] == 'revisao', e1
    assert e1['links'] == ['https://docs.google.com/document/d/EDIT1/edit'], e1['links']       # sem link informado, vai o editavel
    rv = b.execute('SELECT links, skill, status FROM revisao_fila WHERE tarefa_id=?', (e1['nova']['id'],)).fetchone()
    assert json.loads(rv['links']) == e1['links'] and rv['skill'] == 'revisao-recursos' and rv['status'] == 'aguardando', tuple(rv)
    resp = [r[0] for r in b.execute('SELECT usuario_id FROM tarefa_responsaveis WHERE tarefa_id=?', (e1['nova']['id'],))]
    assert resp == [AUTOMACAO_ID], resp                                                          # a Assistente sozinha, sem plantao
    d2 = b.execute('SELECT descricao FROM tarefas WHERE id=?', (e1['nova']['id'],)).fetchone()[0]
    assert 'ENTREGA DA ETAPA ANTERIOR (Advogada' in d2 and 'PRÓXIMA ETAPA SUGERIDA: AJUSTES FINAIS (Advogada, 08/10/2026)' in d2, d2
    assert encaminhar.__doc__ and apos_concluir(b, t1, 'Advogada', hoje=hoje) is None                # ja tem a proxima aberta: nao duplica
    # a Assistente entrega a revisao: a tarefa de ajustes da Advogada nasce com o parecer; a dela fecha
    er5 = entregar_revisao(b, e1['nova']['id'], {'relatorio_editavel': 'https://docs.google.com/document/d/REL/edit', 'pasta': 'https://drive.google.com/drive/folders/P'},
                           'APROVADA COM RESSALVAS', hoje=hoje)
    assert er5['nova'] and er5['nova']['tipo'] == 'AJUSTES' and er5['nova']['para'] == 'Advogada' and er5['fechou_a_propria'], er5
    d3 = b.execute('SELECT descricao FROM tarefas WHERE id=?', (er5['nova']['id'],)).fetchone()[0]
    assert d3.startswith('Advogada, aplicar as ressalvas.') and 'REVISAO DA CONTROLADORIA: APROVADA COM RESSALVAS' in d3 and 'REL/edit' in d3, d3
    assert 'PRÓXIMA ETAPA SUGERIDA: PROTOCOLO (Advogada, 09/10/2026)' in d3 and 'informe o link da entrega' in d3, d3
    # a Advogada encaminha os ajustes com o link final; a Advogada recebe o protocolo; no fim, a cadeia termina
    e3 = encaminhar(b, er5['nova']['id'], 'Advogada', link='https://docs.google.com/document/d/FINAL/edit', hoje=hoje)
    assert e3['nova']['tipo'] == 'PROTOCOLO' and e3['nova']['para'] == 'Advogada' and e3['nova']['data'] == '2026-10-09', e3
    d4 = b.execute('SELECT descricao, prazo_fatal FROM tarefas WHERE id=?', (e3['nova']['id'],)).fetchone()
    assert 'FINAL/edit' in d4[0] and 'ÚLTIMA ETAPA DA CADEIA' in d4[0] and d4[1] == '2026-10-14', tuple(d4)
    e4 = encaminhar(b, e3['nova']['id'], 'Advogada', hoje=hoje)
    assert e4['nova'] is None, e4
    # a cadeia antiga ja tinha um REVISAO para a mesma intimacao (UNIQUE): a nova ganha o sufixo :e<etapa>
    # (o REVISAO de origem_ref '900' ja existe: e o que nasceu do e1 acima)
    b.execute("INSERT INTO tarefas (tipo, titulo, descricao, status, origem, origem_ref, cadeia, etapa) "
              "VALUES ('REVISAO DE PEÇA', 'de novo', 'https://docs.google.com/document/d/E2/edit', 'aberta', 'motor', '900:e1', 'intim:900', 1)")
    t1b = b.execute("SELECT id FROM tarefas WHERE origem_ref='900:e1'").fetchone()[0]
    b.execute("INSERT INTO tarefa_responsaveis (tarefa_id, usuario_id, nome) VALUES (?, 5, 'Advogada')", (t1b,))
    e1b = encaminhar(b, t1b, 'Advogada', hoje=hoje)
    assert e1b['nova'] and e1b['nova']['tipo'] == 'REVISAO', e1b
    rr = b.execute('SELECT origem_ref FROM tarefas WHERE id=?', (e1b['nova']['id'],)).fetchone()[0]
    assert rr == '900:e2', rr
    assert b.execute('SELECT intimacao_id FROM revisao_fila WHERE tarefa_id=?', (e1b['nova']['id'],)).fetchone()[0] == 900
    assert any(x['id'] == e1b['nova']['id'] for x in ja_cadastrado(b, 900)['tarefas'])
    # 6) REFAZER: a Assistente ganha a tarefa dela e, ao entregar, a revisao volta para a Advogada
    b = banco()
    garantir(b)
    b.execute("INSERT INTO motor_fichas (cadeia, ficha, criado_em) VALUES ('intim:901', ?, 0)",
              (json.dumps({'processo': '0010', 'ato': 'EMBARGOS DE DECLARACAO', 'etapas': gab}),))
    b.execute("INSERT INTO tarefas (tipo, titulo, descricao, status, origem, origem_ref, cadeia, etapa, processo_numero) "
              "VALUES ('REVISAO DE PEÇA', 'REVISAR - 0010', 'REFAZER: recorrer direto, sem embargos', 'aberta', 'motor', '901', 'intim:901', 1, '0010')")
    t9 = tid_de(b, 'intim:901', 'REVISAO DE PEÇA')
    b.execute("INSERT INTO tarefa_responsaveis (tarefa_id, usuario_id, nome) VALUES (?, 5, 'Advogada')", (t9,))
    e9 = encaminhar(b, t9, 'Advogada', hoje=hoje)
    assert e9['refazer'] == 'recorrer direto, sem embargos' and e9['nova']['tipo'] == 'PECA' and e9['nova']['automacao'] and e9['nova']['fila'] == 'v1', e9
    pf9 = b.execute("SELECT skill, resultado FROM producao_fila WHERE tarefa_id=?", (e9['nova']['id'],)).fetchone()
    assert pf9['skill'] == 'embargos-declaracao' and json.loads(pf9['resultado'])['volta_para']['etapa'] == 1, tuple(pf9)
    ev9 = entregar_v1(b, e9['nova']['id'], {'peca': 'https://drive.google.com/file/d/N/view', 'peca_editavel': 'https://docs.google.com/document/d/N/edit'})
    assert ev9['nova'] and ev9['nova']['tipo'] == 'REVISAO DE PECA' and ev9['nova']['para'] == 'Advogada' and ev9['nova']['etapa'] == 1, ev9   # o tipo sai normalizado, como no cadastrar
    assert b.execute('SELECT status FROM tarefas WHERE id=?', (e9['nova']['id'],)).fetchone()[0] == 'concluida'
    d9 = b.execute('SELECT descricao FROM tarefas WHERE id=?', (ev9['nova']['id'],)).fetchone()[0]
    assert d9.startswith('PRIMEIRA VERSAO PRONTA (Assistente') and 'N/edit' in d9 and 'PRÓXIMA ETAPA SUGERIDA: REVISÃO DA CONTROLADORIA' in d9, d9
    print('motor_tarefas: bateria ok (peca no criterio unico; recurso com analise e estrategia; liquidacao com contador; razoes finais por tarefa; alvara; audiencia e sustentacao com roteiro; entregas da automacao; passo a passo com encaminhar e refazer)')
