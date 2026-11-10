# -*- coding: utf-8 -*-
"""O que o escritorio faz com a publicacao, depois de saber que ato ela e.

O catalogo responde QUE ato e a publicacao e qual o prazo legal. Nao responde
o que se faz com ela. Isso so existia na cabeca da Advogada e da Dra. Advogada.

A tabela abaixo saiu do historico: 715 processos e 5.382 tarefas do ADVBOX,
cruzados com 95 publicacoes do SYNC (06/08 a 04/09/2026). Para cada publicacao,
que tarefa a equipe abriu nos 10 dias seguintes naquele processo. `obs` e
quantos pares sustentam a linha; onde `obs` e baixo, a sugestao vale como
orientacao, nao como regra, e a tela diz isso.

Duas regras foram ensinadas pela Dra. Advogada em 08/09/2026 e nao vem do
historico: o POP 18 (audiencia) e a vigia do transito. Estao marcadas como
'pop' e 'regra', que e um lastro melhor do que contagem.

Nada aqui cadastra nada. Sugere, com a conta a vista.
"""
import re

from . import motor_prazos as MP
from .feriados import eh_dia_util, prazo_interno


# ato do catalogo -> (providencia, tarefa sugerida, quem, obs)
_TABELA = {
    'RECURSO ORDINARIO': (
        'Analisar a decisao e levar a Dra. Advogada: cabe ED, RO, ou nada.',
        'ANALISE DE SENTENCA', 'Dra. Advogada decide e produz', 28),
    'RECURSO DE REVISTA': (
        'Analisar o acordao e levar a Dra. Advogada: cabe ED, RR, ou nada.',
        'ANALISE DE SENTENCA', 'Dra. Advogada decide e produz', 15),
    'RECURSO ESPECIAL / EXTRAORDINARIO': (
        'Analisar o acordao e levar a Dra. Advogada: cabe ED, REsp/RE, ou nada.',
        'ANALISE DE SENTENCA', 'Dra. Advogada decide e produz', 15),
    'APELACAO': (
        'Analisar a sentenca e levar a Dra. Advogada: cabe ED, apelacao, ou nada.',
        'ANALISE DE SENTENCA', 'Dra. Advogada decide e produz', 15),
    'AGRAVO DE INSTRUMENTO': (
        'Se o recurso negado e nosso, cabe agravo; se e da parte contraria, '
        'so registro e atualizacao do cliente.',
        'ACOMPANHAMENTO', 'Dra. Advogada decide e registra', 5),
    'AGRAVO DE PETICAO': (
        'Analisar a decisao de execucao e levar a Dra. Advogada.',
        'ANALISE DE SENTENCA', 'Dra. Advogada decide e produz', 0),
    'EMBARGOS DE DECLARACAO': (
        'Decisao dos ED publicada: o prazo recursal reinicia por inteiro. '
        'Recontar RO ou RR do zero.',
        'ANALISE DE SENTENCA', 'Dra. Advogada decide e produz', 9),
    'CONTRARRAZOES DE RO': (
        'Contrarrazoes em 8 dias uteis. Peca fatal.',
        'CONTRARRAZOES', 'Dra. Advogada protocola', 6),
    'CONTRARRAZOES DE RR': (
        'Contrarrazoes em 8 dias uteis. Peca fatal.',
        'CONTRARRAZOES', 'Dra. Advogada protocola', 0),
    'CONTESTACAO': (
        'Defesa ate a audiencia. Prazo interno ancorado na data dela.',
        'CONTESTACAO NORMAL', 'Advogada', 8),
    'AUDIENCIA DESIGNADA': (
        'Abrir o POP 18 de audiencia.',
        'AVISAR CLIENTE AUDIENCIA', 'Advogada', 6),
    'PAUTA DE JULGAMENTO': (
        'Registrar a sessao na agenda e inscrever a sustentacao oral no prazo '
        'do proprio tribunal, que nao e o prazo dos autos.',
        'SUSTENTACAO ORAL TRT TRF TJ', 'Dra. Advogada sustenta e inscreve', 4),
    'MANIFESTACAO GENERICA': (
        'Peticao de meio no prazo do despacho. Conferir o polo antes: e a '
        'classe em que o comando mais costuma ser da outra parte.',
        'PETICAO DE MEIO', 'Advogada', 9),
    'PAGAMENTO VOLUNTARIO (CUMPRIMENTO)': (
        'Como exequente: acompanhar deposito e levantamento, lancar a receita '
        'e atualizar o cliente. Como executado: pagar em 15 uteis ou impugnar.',
        'ACOMPANHAMENTO', 'Advogada; financeiro lanca', 7),
    'IMPUGNACAO AO CUMPRIMENTO DE SENTENCA': (
        'Impugnacao em 15 dias uteis. Peca fatal.',
        'PETICAO SIMPLES EXECUCAO', 'Advogada', 0),
    'CUSTAS E PREPARO': (
        'Emitir guia e comprovar dentro do prazo do recurso. E trilha de meio, '
        'mas nao recolher derruba o recurso por desercao.',
        'EMITIR GUIA', 'Advogada', 1),
    'CUSTAS E DESPESAS': (
        'Emitir guia e comprovar dentro do prazo. Nao recolher gera desercao.',
        'EMITIR GUIA', 'Advogada', 0),
    'ALVARA / LEVANTAMENTO': (
        'Acompanhar a expedicao, conferir os dados bancarios e avisar o cliente '
        'quando o valor cair.',
        'ACOMPANHAR IMPLANTACAO DECISAO', 'Advogada; financeiro lanca', 3),
    'DISTRIBUICAO / SORTEIO DE RELATOR': (
        'Registro. Conferir se o numero do processo esta gravado no cadastro.',
        'ACOMPANHAMENTO', 'Advogada', 10),
    'HOMOLOGACAO DE CALCULOS': (
        'Conferir de quem sao os calculos homologados. Sendo os nossos, nao ha '
        'o que impugnar; o proximo ato e pagamento ou levantamento.',
        'ACOMPANHAMENTO', 'Dra. Advogada decide acordo', 0),
    'PAGAMENTO EM EXECUCAO': (
        'Intimacao para pagar sob pena de penhora. Avisar o cliente com o '
        'valor e a data, e tratar acordo antes que a penhora abra.',
        'ACOMPANHAMENTO', 'Advogada; financeiro provisiona', 0),
    'CIENCIA / MERO EXPEDIENTE': (
        'Registro, sem prazo. Ler o dispositivo antes de fechar: cinco de cada '
        'vinte e quatro escondiam providencia.',
        'ACOMPANHAMENTO', 'Advogada', 24),
    # Calibracao de 01/10/2026 com a Dra. Advogada (regra 7: atos que faltavam).
    'AGRAVO INTERNO': (
        'Decisao monocratica negou seguimento ao NOSSO agravo. Cabe agravo interno '
        'em 8 dias uteis (art. 1.021 CPC). Levar a Dra. Advogada hoje: o obice costuma '
        'ser formal (art. 896, 1o-A) e a chance e pequena; decidir com o cliente.',
        'ANALISE DE SENTENCA', 'Dra. Advogada decide; advogado do caso produz', 1),
    'APRESENTACAO DE CALCULOS': (
        'Liquidacao: apresentar os calculos no prazo do despacho (art. 879, 1o-B). '
        'Advogada monta a memoria de calculo; Advogada confere e protocola. Conferir '
        'antes se a decisao transitou.',
        'CONFERIR LIQUIDACAO SENTENCA', 'Dra. Advogada calcula e confere e protocola', 1),
    'CONTESTAR CALCULOS (ART. 879 2o)': (
        'Contestar os calculos da outra parte, com calculos, no prazo do despacho. '
        'Sem contestacao fundamentada eles sao homologados.',
        'PETICAO DE MEIO', 'Dra. Advogada calcula e contesta', 0),
    'MANIFESTACAO SOBRE ED': (
        'Manifestacao sobre os embargos de declaracao da outra parte, no prazo do '
        'despacho (em geral 5 dias). Com ED cruzados, "as embargadas" inclui o nosso lado.',
        'PETICAO DE MEIO', 'Dra. Advogada protocola', 1),
    'EMENDA A INICIAL': (
        'Emendar a inicial no prazo do despacho, sob pena de indeferimento. Peca '
        'substitutiva quando o juizo pede.',
        'REVISAO DE PECA', 'Dra. Advogada protocola', 1),
    'ESPECIFICACAO DE PROVAS': (
        'Especificar as provas e justificar a pertinencia no prazo do despacho '
        '(prazo comum). Decidir com a Dra. Advogada testemunhas e a posicao sobre '
        'conciliacao.',
        'REVISAO DE PECA', 'Dra. Advogada protocola', 1),
    'CREDENCIAMENTO / MIGRACAO DE SISTEMA': (
        'Credenciar os procuradores no novo sistema (eproc/SEEU) e conferir o '
        'cadastro. Sem prazo processual.',
        'ACOMPANHAMENTO', 'Advogada', 2),
    'BLOQUEIO SISBAJUD': (
        'Como exequente: aguardar o prazo de impugnacao do executado e requerer a '
        'transferencia ou o levantamento. Como executado: impugnar em 5 dias.',
        'PETICAO DE MEIO', 'Dra. Advogada acompanha', 1),
    'CUSTAS E DESPESAS': (
        'Emitir guia e comprovar dentro do prazo do despacho. Sem recolhimento o '
        'ato nao sai (ou o feito e extinto, no caso de taxa judiciaria).',
        'EMITIR GUIA', 'Advogada; financeiro paga', 1),
    'PROCESSO SIGILOSO': (
        'Teor fora do DJEN: abrir os autos no sistema do tribunal antes de '
        'classificar. Nunca fechar como ciencia.',
        'ACOMPANHAMENTO', 'Advogada abre os autos', 2),
    'DECISAO SEM TEOR NO DJEN': (
        'A publicacao traz prazo mas nao traz o teor da decisao: abrir os autos '
        '(SYNC ou tribunal) e classificar pelo dispositivo.',
        'ACOMPANHAMENTO', 'Advogada abre os autos; Advogada classifica', 1),
}

_PADRAO = ('Ato sem pratica registrada no historico. Seguir a trilha do '
           'catalogo e conferir com a Advogada.', None, None, 0)


# ===========================================================================
# POP 18 - AUDIENCIA
#
# Fonte: planilha do popao, POP 18 "POP DE AUDIENCIA", revisao 3 de 17/03/2025,
# e o card do ADVBOX 271442173 (audiencia inicial do Jetro, 03/09/2026).
#
# O passo 17 do POP e a bifurcacao que importa: "identificar se trata-se de
# audiencia inicial, una ou de instrucao". Na INICIAL nao se ouve testemunha,
# e a mensagem ao cliente diz isso com todas as letras ("nenhuma testemunha e
# ouvida nesta fase, entao voce nao precisa convidar ninguem agora"). Mandar a
# Parte 2 numa inicial poe o cliente a convocar gente a toa, sete meses antes.
# ===========================================================================
_AUD_INSTRUCAO = re.compile(
    r'audiencia\s+(?:de\s+)?instrucao|instrucao\s+e\s+julgamento|oitiva\s+de\s+'
    r'testemunha|depoimento\s+pessoal|colheita\s+de\s+prova')
_AUD_UNA = re.compile(r'audiencia\s+una|audiencia\s+unica')
_AUD_INICIAL = re.compile(r'audiencia\s+inicial|audiencia\s+inaugural|'
                          r'para\s+inicial|audiencia\s+de\s+conciliacao')
_PRESENCIAL = re.compile(r'presencial|sala\s+de\s+audiencia|comparecer\s+ao\s+forum|'
                         r'sede\s+d[ao]\s+(?:vara|forum)')
_VIRTUAL = re.compile(r'videoconferencia|modalidade\s+virtual|zoom|telepresencial')


# A designacao e a frase que traz a DATA da audiencia. Tudo o mais que fala de
# audiencia no documento e formulario: penalidade, hipotese, instrucao de Zoom.
_DATA_AUD = re.compile(r'\d{1,2}/\d{1,2}/\d{2,4}(?:\s*[,ea]{0,2}\s*(?:as|às)?\s*'
                       r'\d{1,2}[:h]\d{2})?')
_HIPOTESE = re.compile(r'em caso de[^.]{0,120}|caso (?:seja|haja|venha)[^.]{0,120}|'
                       r'a ausencia[^.]{0,160}|sob pena[^.]{0,160}')


def _janela_designacao(t):
    """Trecho em volta da data da audiencia. None quando nao ha data."""
    # tira as frases condicionais e de penalidade antes de procurar
    limpo = _HIPOTESE.sub(' ', t)
    melhor = None
    for m in _DATA_AUD.finditer(limpo):
        ini = max(0, m.start() - 260)
        janela = limpo[ini:m.end() + 90]
        if 'audiencia' in janela or 'sessao' in janela:
            melhor = janela
            break
    return melhor


def _subtipo_audiencia(t):
    """Inicial, una ou instrucao. Devolve None quando o texto nao diz.

    A ordem importa: instrucao vence una, e una vence inicial. Uma notificacao
    de audiencia una costuma citar "inicial" no cabecalho da classe, e ler o
    cabecalho em vez do dispositivo ja custou caro aqui.
    """
    alvo = _janela_designacao(t) or t
    if _AUD_INSTRUCAO.search(alvo):
        return 'instrucao'
    if _AUD_UNA.search(alvo):
        return 'una'
    if _AUD_INICIAL.search(alvo):
        return 'inicial'
    return None


def _modalidade(t):
    """Virtual ou presencial, lido na designacao.

    Janela tambem para a modalidade: toda notificacao do TRT carrega o manual
    do Zoom no rodape, entao "videoconferencia" aparece ate quando a audiencia
    e presencial. Um despacho dizia "ocorrera no formato presencial" e saia
    como virtual porque o formulario do Zoom vinha logo abaixo.
    """
    alvo = _janela_designacao(t) or t
    # o comando explicito vence qualquer mencao solta
    if re.search(r'formato presencial|modalidade presencial|sera presencial', alvo):
        return 'presencial'
    if re.search(r'modalidade virtual|por videoconferencia|formato telepresencial|'
                 r'sera (?:virtual|telepresencial)', alvo):
        return 'virtual'
    # endereco de rua na designacao so existe em audiencia presencial
    if re.search(r'\b(?:av|avenida|rua|praca|rodovia)\b[^.]{0,60}\d', alvo):
        return 'presencial'
    if _VIRTUAL.search(alvo):
        return 'virtual'
    if _PRESENCIAL.search(alvo):
        return 'presencial'
    return None


def _pop18(texto_norm):
    """A providencia de audiencia, montada a partir do POP 18."""
    sub = _subtipo_audiencia(texto_norm)
    mod = _modalidade(texto_norm)
    tem_testemunha = sub in ('una', 'instrucao')

    passos = [
        'Cadastrar na agenda na hora marcada, com link, sala e senha na descricao.',
        'A automacao envia ao cliente a PARTE 1 do POP 18 (instrucoes da audiencia); a Advogada '
        'fiscaliza se saiu, se saiu certo e se o cliente deu as devolutivas.',
    ]
    if tem_testemunha:
        passos += [
            'Enviar a PARTE 2: o convite pronto para o cliente repassar as '
            'testemunhas por WhatsApp. Quem convida e ele, por escolha do '
            'escritorio, e nao nos.',
            'O rol tem que dizer o que CADA testemunha prova, nao so nome e '
            'telefone.',
            'Enviar o PASSO 3: pedir os 3 prints por testemunha (mensagem '
            'enviada, resposta dela, e a tela do contato com nome e numero).',
            'Monitorar a cada dois dias ate o cliente comprovar o convite.',
            'Depois da comprovacao, peticionar nos autos informando as '
            'testemunhas e a ciencia delas.',
            'Roteiro de audiencia e perguntas para as testemunhas na pasta '
            'ATOS INTERNOS; avisar a Dra. Advogada com o link.',
            'Reuniao de orientacao com o cliente E as testemunhas antes da '
            'audiencia; tarefa de ORIENTACAO para 3 dias antes.',
            'Mudou a pauta, avisar cliente E testemunhas: ja houve audiencia '
            'remarcada por testemunha hospitalizada.',
        ]
    else:
        # Dispensada NA audiencia nao e dispensada no caso: a instrucao vem
        # depois e o rol leva meses. Por isso o card nao fecha aqui.
        passos += [
            'Nao convocar testemunha para esta data: na inicial nenhuma e '
            'ouvida, e chamar gente com meses de antecedencia so gera falta.',
            'O card NAO fecha: segue para a PARTE 2 e 3 do POP 18 depois da '
            'inicial, montando o rol para a audiencia de instrucao.',
            'Ir levantando o rol desde ja: nome, telefone e O QUE CADA UMA '
            'PROVA. Testemunha costuma demorar meses para fechar.',
        ]
    if mod == 'presencial':
        passos += [
            'Presencial: mandar o endereco e orientar a chegar 40 minutos antes.',
        ]
    elif mod == 'virtual':
        passos.append('Virtual: orientar a instalar o Zoom e permitir camera e '
                      'microfone, e a entrar 15 minutos antes.')
    passos += [
        'Quatro avisos na agenda: 7 dias uteis, 3 dias uteis, vespera e o dia.',
        'A automacao gera o roteiro da audiencia e entrega para a Advogada estudar, com os pontos de atencao.',
        'Cobranca da audiencia: so clientes empresa listados em juridico/clientes_empresa.json (tarefa do financeiro). '
        'Reclamante nao paga deslocamento.',
        'Mover a fase do processo para AGUARDANDO AUDIENCIA.',
    ]

    rotulo = {'inicial': 'audiencia INICIAL', 'una': 'audiencia UNA',
              'instrucao': 'audiencia de INSTRUCAO'}.get(sub, 'audiencia')
    cab = 'POP 18, %s%s.' % (rotulo, ' e ' + mod if mod else '')
    if sub is None:
        cab += (' O texto nao diz o tipo: conferir nos autos antes de mandar a '
                'parte das testemunhas.')
    return {
        'providencia': cab + ' ' + (
            'Tem testemunha: o cliente precisa convidar e comprovar.'
            if tem_testemunha else
            'Nenhuma testemunha e ouvida nesta fase, mas o card nao fecha: '
            'o rol segue sendo montado para a instrucao.'),
        'tarefa': 'AVISAR CLIENTE AUDIENCIA',
        'quem': 'Automacao comunica o cliente; Advogada fiscaliza',
        'obs': None,
        'forca': 'pop',
        'abre_tarefa_de_prazo': True,
        'passos': passos,
        'subtipo': sub,
        'modalidade': mod,
    }


# ===========================================================================
# VIGIA DO TRANSITO
#
# Regra da Dra. Advogada, 08/09/2026, a partir do caso caso C x empresa C: o TRT
# DENEGOU seguimento ao recurso de revista da reclamada, por desercao. Nao ha
# nada para nos fazer. Mas tambem nao ha transito ainda: a reclamada ainda pode
# opor ED (5 uteis) ou agravo de instrumento (8 uteis).
#
# Entao conta-se o prazo DELES e deixa-se tarefa para o primeiro dia util
# DEPOIS do maior deles. Se nada foi peticionado, segue para a liquidacao.
#
# Sem essa vigia a decisao favoravel simplesmente some da fila: e uma ciencia,
# ninguem volta nela, e a liquidacao comeca meses depois.
# ===========================================================================
_DENEGA = re.compile(
    r'denego\s+seguimento|nego\s+seguimento|negar\s+seguimento|'
    r'nao\s+admito\s+o\s+recurso|recurso\s+nao\s+admitido|'
    r'inadmito\s+o\s+recurso|deixo\s+de\s+admitir\s+o\s+recurso')
_RECURSO_DE = re.compile(r'recurso\s+de\s*:\s*([a-z0-9 .,/&-]{4,90})')
# Quando nao ha "RECURSO DE:", o cabecalho diz quem recorreu: "AGRAVANTE: X",
# "RECORRENTE: X", "EMBARGANTE: X" (caso E x empresa E no TST, 23/09/2026).
_RECORRENTE = re.compile(r'(?:agravante|recorrente|embargante|apelante)\s*:\s*([a-z0-9 .,/&-]{4,90}?)'
                         r'(?:\s+(?:agravad|recorrid|embargad|apelad|advogad)|$)')

# Prazos que a parte vencida ainda tem na Justica do Trabalho.
_PRAZOS_DELES = ((5, 'embargos de declaracao', 'art. 897-A CLT'),
                 (8, 'agravo de instrumento', 'art. 897, b, CLT'))


# Prazo que o escritorio se da para a liquidacao depois do transito. Nao e
# prazo legal: e compromisso interno, definido pela Dra. Advogada em 08/09/2026.
_DIAS_LIQUIDACAO = 8


def _somar_uteis(iso, n):
    import datetime
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
    except Exception:
        return None
    while n:
        d += datetime.timedelta(days=1)
        if eh_dia_util(d):
            n -= 1
    return d.isoformat()


def _menos_uteis(iso, n):
    """n dias uteis antes: o prazo interno da liquidacao. Calendario unico (feriados.prazo_interno)."""
    d = prazo_interno(str(iso)[:10], n) if iso else None
    return d.isoformat() if d else None


def _dia_util_seguinte(iso):
    """Primeiro dia util DEPOIS da data. E quando se olha os autos.

    No proprio dia do vencimento a peticao ainda pode entrar ate a meia-noite:
    conferir nesse dia daria transito que nao houve.
    """
    import datetime
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
    except Exception:
        return None
    d += datetime.timedelta(days=1)
    for _ in range(30):
        if eh_dia_util(d):
            return d.isoformat()
        d += datetime.timedelta(days=1)
    return d.isoformat()


def _mesma_parte(a, b):
    """Duas grafias do mesmo nome. Compara as tres primeiras palavras uteis."""
    def toks(s):
        s = re.sub(r'[^a-z0-9 ]', ' ', str(s or '').lower())
        fora = {'s', 'a', 'sa', 'ltda', 'me', 'epp', 'eireli', 'do', 'da', 'de',
                'e', 'dos', 'das', 'em', 'no', 'na'}
        return [w for w in s.split() if w not in fora and len(w) > 1][:3]
    ta, tb = toks(a), toks(b)
    return bool(ta) and bool(tb) and len(set(ta) & set(tb)) >= 2


def _vigia_transito(texto_norm, intimacao, clientes, adversos):
    """Decisao que so depende do prazo da outra parte. None quando nao e o caso.

    `clientes` e `adversos` sao LISTAS. Litisconsorcio e o caso comum: no
    processo que ensinou esta regra havia dois reclamados, e o recurso denegado
    era o do segundo.
    """
    if not _DENEGA.search(texto_norm):
        return None
    m = _RECURSO_DE.search(texto_norm) or _RECORRENTE.search(texto_norm)
    de_quem = (m.group(1).strip() if m else '')
    # Se o recurso denegado e NOSSO, a leitura se inverte: o prazo de agravo
    # passa a ser nosso, e fatal. Sugerir vigia aqui seria sugerir o oposto.
    if de_quem and any(_mesma_parte(de_quem, c) for c in clientes if c):
        return {'nosso_recurso_negado': True, 'de_quem': de_quem}
    conhecido = any(_mesma_parte(de_quem, a) for a in adversos if a)
    if de_quem and not conhecido and not any(c for c in clientes if c):
        # Nome que nao casa com ninguem e nem sabemos quem e o nosso cliente.
        # Nao da para afirmar de que lado a decisao cai; perguntar e melhor do
        # que chutar.
        return {'incerto': True, 'de_quem': de_quem}
    # Se sabemos quem e o nosso cliente e o recorrente nao e ele, o recurso
    # negado e da outra parte, mesmo que o cadastro nao tenha a parte adversa
    # (caso F x empresa B, 17/09/2026: adversos vazios no ADVBOX).
    if not de_quem and not adversos:
        return None

    disp = (intimacao or {}).get('data')
    trib = (intimacao or {}).get('tribunal') or ''
    orgao = (intimacao or {}).get('orgao') or ''
    prazos = []
    for dias, nome, base in _PRAZOS_DELES:
        try:
            p = MP.calcular_prazo(disp, dias, 'uteis', tribunal=trib, orgao=orgao)
        except Exception:
            continue
        if p.get('vencimento'):
            prazos.append({'ato': nome, 'dias': dias, 'base_legal': base,
                           'vencimento': p['vencimento']})
    if not prazos:
        return None
    ultimo = max(p['vencimento'] for p in prazos)
    return {'de_quem': de_quem or (adversos[0] if adversos else ''),
            'prazos_deles': prazos,
            'ultimo': ultimo, 'vigiar_em': _dia_util_seguinte(ultimo)}


# Ordem que so vale depois que outra coisa acontecer. O prazo esta escrito no
# despacho mas nao comecou a correr.
_CONDICIONADA = re.compile(
    r'ap[oó]s\s+(?:o|a|os|as)?\s*[a-z]{3,}[^.]{0,160}?intime-se[^.]{0,160}?'
    r'(?:prazo de\s*(\d{1,3})\s*dias|no prazo)|'
    r'ap[oó]s[^.]{0,120}?comprovar[^.]{0,200}?intime-se')


def _ordem_condicionada(t):
    """Trecho da ordem condicionada, quando existe."""
    m = _CONDICIONADA.search(t)
    return m.group(0)[:300] if m else None


def _forca(obs):
    """Quanto a linha se sustenta. O criterio e o mesmo da skill."""
    if obs >= 15:
        return 'forte'
    if obs >= 6:
        return 'media'
    if obs >= 1:
        return 'fraca'
    return 'sem evidencia'


def _norm(s):
    import unicodedata
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', s).lower().strip()


def sugerir(classificacao, nosso_polo=None, intimacao=None,
            cliente=None, parte_adversa=None, clientes=None, adversos=None):
    """A providencia sugerida para uma publicacao ja classificada.

    Devolve sempre um dicionario; nunca None, para a tela nao ter que testar.
    """
    r = classificacao or {}
    ato = r.get('ato') or ''
    tit = r.get('titularidade') or 'INDEFINIDO'
    texto = _norm((intimacao or {}).get('texto') or (intimacao or {}).get('resumo') or '')
    cliente = cliente or r.get('cliente')
    parte_adversa = parte_adversa or r.get('parte_adversa')
    clientes = [x for x in (clientes or [cliente]) if x]
    adversos = [x for x in (adversos or [parte_adversa]) if x]
    responsavel = r.get('responsavel')

    # 0. Processo conduzido por parceiro (regra da Advogada, 01/10/2026): a
    #    providencia e fixa, a Advogada encaminha e confirma. Nao ha peca nossa.
    parc = r.get('parceiro') or {}
    if parc.get('modo') == 'encaminhar':
        nome = parc.get('nome') or 'o parceiro'
        return {
            'providencia': ('Processo conduzido por %s. A Advogada encaminha a intimacao a ele, '
                            'confirma a providencia tomada e registra, como nas intimacoes dos '
                            'demais parceiros.' % nome),
            'tarefa': 'ACOMPANHAMENTO',
            'quem': 'Advogada encaminha a %s e confere' % nome,
            'obs': None, 'forca': 'regra', 'abre_tarefa_de_prazo': True,
            'parceiro': nome,
        }

    # 0b. Reclamada intimada a pagar sob pena de execucao: a cadeia que o
    #     escritorio usou num caso real (28/09 a 02/10/2026) vira a
    #     providencia padrao. Regra 6 da calibracao.
    if ato == 'PAGAMENTO EM EXECUCAO' and nosso_polo == 'passivo' and not _ordem_condicionada(texto):
        prazos = [p for p in (r.get('prazos') or []) if not p.get('paralelo')]
        venc = (prazos[0].get('vencimento') if prazos else None)
        interno = (prazos[0].get('prazo_fatal_interno') if prazos else None)
        return {
            'providencia': (
                'Reclamada intimada a pagar sob pena de execucao. Cadeia do escritorio: '
                '1) atualizar o cliente no mesmo dia com valor e prazo; 2) tentativa de '
                'acordo com a parte contraria; 3) plano B de parcelamento judicial com as '
                'guias; 4) protocolo no fatal interno%s.'
                % (' (%s; processual %s)' % (_br(interno), _br(venc)) if venc else '')),
            'tarefa': 'TENTATIVA DE ACORDO',
            'quem': 'Dra. Advogada negocia e atualiza o cliente, emite as guias e protocola',
            'obs': None, 'forca': 'regra', 'abre_tarefa_de_prazo': True,
            'tarefas_sugeridas': [
                {'para': 'Advogada', 'tipo': 'ATUALIZAR CLIENTE', 'agenda': False,
                 'texto': 'Atualizar o cliente: intimado a pagar; valor, prazo e as opcoes (pagar, acordo, parcelamento).'},
                {'para': 'Advogada', 'tipo': 'TENTATIVA DE ACORDO', 'agenda': False,
                 'texto': 'Tentativa de acordo com a parte contraria antes do fatal interno.'},
                {'para': 'Advogada', 'tipo': 'PETICAO DE MEIO', 'vencimento': interno, 'agenda': True,
                 'texto': 'Plano B: parcelamento judicial com as guias, se nao houver acordo.'},
                {'para': 'Advogada', 'tipo': 'PROTOCOLO', 'vencimento': interno, 'agenda': True,
                 'texto': 'Protocolo do pagamento, do acordo ou do parcelamento no fatal interno.'},
            ],
        }

    # 1. Decisao favoravel que so espera o prazo da outra parte. Vem antes de
    #    tudo porque a classificacao dela e "ciencia", e ciencia nao volta.
    if texto:
        vg = _vigia_transito(texto, intimacao, clientes, adversos)
        if vg and vg.get('nosso_recurso_negado'):
            peca = 'agravo interno' if ato == 'AGRAVO INTERNO' else 'agravo de instrumento'
            quem_produz = 'Dra. Advogada'
            return {
                'providencia': 'Seguimento NEGADO ao NOSSO recurso. O prazo de '
                               '%s e nosso e e fatal: levar a Dra. Advogada hoje.' % peca,
                'tarefa': 'ANALISE DE SENTENCA',
                'quem': 'Dra. Advogada decide; %s produz' % quem_produz,
                'obs': None, 'forca': 'regra', 'abre_tarefa_de_prazo': True,
            }
        if vg and vg.get('incerto'):
            return {
                'providencia': 'Foi negado seguimento a um recurso de "%s", nome '
                               'que nao esta no cadastro do processo. Conferir '
                               'nos autos de quem e o recurso antes de agir: se '
                               'for nosso, o prazo de agravo e fatal.'
                               % vg['de_quem'][:60],
                'tarefa': 'ACOMPANHAMENTO',
                'quem': 'Advogada confere nos autos',
                'obs': None, 'forca': 'regra', 'abre_tarefa_de_prazo': True,
            }
        if vg:
            linhas = '; '.join('%s ate %s' % (p['ato'], _br(p['vencimento']))
                               for p in vg['prazos_deles'])
            transito = vg['vigiar_em']
            liq = _somar_uteis(transito, _DIAS_LIQUIDACAO)
            liq_int = _menos_uteis(liq, 2)
            return {
                'providencia': (
                    'Decisao favoravel: negado seguimento ao recurso da parte '
                    'contraria. Nada a peticionar. Falta so o prazo deles (%s), '
                    'e o transito se consolida em %s. Se nada for peticionado, '
                    'segue para a liquidacao de sentenca, prazo da Advogada ate %s '
                    '(interno %s).'
                    % (linhas, _br(transito), _br(liq), _br(liq_int))),
                'tarefa': 'ACOMPANHAMENTO',
                'quem': 'Dra. Advogada acompanha e liquida',
                'obs': None,
                'forca': 'regra',
                'abre_tarefa_de_prazo': True,
                'vigiar_em': transito,
                'transito_em': transito,
                'prazos_da_contraria': vg['prazos_deles'],
                'prazo_liquidacao': liq,
                'prazo_liquidacao_interno': liq_int,
                # As duas tarefas ja escritas, para a Advogada so conferir e
                # mandar. O texto e o que a Dra. Advogada escreveu no card.
                'tarefas_sugeridas': [
                    {'para': 'Advogada', 'tipo': 'ACOMPANHAMENTO',
                     'vencimento': transito, 'agenda': True,
                     'texto': ('Advogada, segue para acompanhar o transito em '
                               'julgado, que se consolidara em %s. Conferir nos '
                               'autos se houve peticao da parte contraria. Nao '
                               'havendo, segue para liquidacao de sentenca.'
                               % _br(transito))},
                    {'para': 'Advogada', 'tipo': 'INICIAL EXECUCAO CUMPRIMENTO SENTENCA',
                     'vencimento': liq, 'vencimento_interno': liq_int,
                     'agenda': True, 'condicional': True,
                     'texto': ('Liquidacao de sentenca, se nao houver recurso. '
                               'Prazo de %d dias uteis contados do transito em '
                               '%s: vence em %s, interno em %s.'
                               % (_DIAS_LIQUIDACAO, _br(transito), _br(liq),
                                  _br(liq_int)))},
                ],
                'passos': [
                    'Comunicar o cliente da decisao favoravel.',
                    'Cadastrar na agenda a conferencia do transito em %s.'
                    % _br(transito),
                    'Cadastrar na agenda o prazo da liquidacao: vence %s, '
                    'interno %s.' % (_br(liq), _br(liq_int)),
                    'Em %s, conferir nos autos se a parte contraria peticionou.'
                    % _br(transito),
                    'Sem peticao: certidao de transito e a liquidacao segue '
                    'para a Advogada.',
                    'Com peticao: nova ficha, o prazo volta a correr e a '
                    'liquidacao cai.',
                ],
            }

    # 2. Prazo da outra parte encerra a conferencia antes da tabela. Foi o erro
    #    que a Dra. Advogada pediu para checar: "verificar se a providencia e da
    #    nossa parte (Reclamada), ou do Reclamante".
    if tit == 'CONTRARIA' and ato == 'BLOQUEIO SISBAJUD':
        return {
            'providencia': 'Bloqueio SISBAJUD com prazo de impugnacao do executado (5 dias). '
                           'Como exequente: depois do prazo, requerer a transferencia ou o '
                           'levantamento do valor bloqueado.',
            'tarefa': 'PETICAO DE MEIO',
            'quem': 'Dra. Advogada requer e acompanha',
            'obs': None, 'forca': 'regra', 'abre_tarefa_de_prazo': True,
        }
    if tit == 'CONTRARIA':
        return {
            'providencia': 'O prazo corre para a parte contraria. Acompanhar o '
                           'andamento e nao abrir tarefa de prazo nossa.',
            'tarefa': 'ACOMPANHAMENTO',
            'quem': 'Advogada',
            'obs': None,
            'forca': 'regra',
            'abre_tarefa_de_prazo': False,
        }

    # 3. Ordem de pagamento condicionada: o prazo existe no papel e nao comecou.
    if ato in ('PAGAMENTO EM EXECUCAO', 'HOMOLOGACAO DE CALCULOS') and texto:
        cond = _ordem_condicionada(texto)
        if cond:
            return {
                'providencia': (
                    'A ordem de pagamento e CONDICIONADA: o prazo so comeca '
                    'depois do ato que o despacho exige antes. Hoje nao corre '
                    'prazo nosso. Vigiar a publicacao que abrir o prazo e '
                    'avisar o cliente para provisionar desde ja, porque nao '
                    'pagar abre penhora.'),
                'tarefa': 'ACOMPANHAMENTO',
                'quem': 'Advogada vigia a publicacao; Dra. Advogada decide acordo',
                'obs': None,
                'forca': 'regra',
                'abre_tarefa_de_prazo': True,
                'condicao': cond,
                'passos': [
                    'Nao lancar prazo fatal hoje: ele ainda nao nasceu.',
                    'Avisar o cliente do valor homologado e pedir provisao.',
                    'Vigiar o processo ate sair a intimacao para pagamento.',
                    'Saindo a intimacao, nova ficha: ai o prazo corre e e '
                    'fatal, sob pena de penhora.',
                    'Enquanto nao sai, e a ultima janela para acordo com '
                    'parcelamento.',
                ],
            }

    # 4. Audiencia tem POP proprio, com bifurcacao por tipo.
    if ato == 'AUDIENCIA DESIGNADA' and texto:
        return _pop18(texto)

    prov, tarefa, quem, obs = _TABELA.get(ato, _PADRAO)
    trilha = r.get('trilha') or 'CIENCIA'
    if parc.get('modo') == 'conjunta':
        prov = prov + (' Cliente de parceria com %s: a peca e nossa, mas o contato com o '
                       'cliente passa pelo parceiro.' % parc.get('nome'))
    d = {
        'providencia': prov,
        'tarefa': tarefa,
        'quem': quem or responsavel,
        'obs': obs or None,
        'forca': _forca(obs),
        'abre_tarefa_de_prazo': trilha in ('PECA', 'MEIO') and tit != 'CONTRARIA',
    }
    if r.get('copia_de_adverso') and tit != 'CONTRARIA':
        d['providencia'] = ('Copia enderecada a parte contraria; o prazo nosso esta na nossa '
                            'copia desta decisao. ' + d['providencia'])
    if tit == 'AMBOS':
        d['providencia'] = 'Prazo comum: corre para os dois lados. ' + d['providencia']
    if tit == 'INDEFINIDO':
        d['providencia'] = ('Titularidade a conferir antes de agir. '
                            + d['providencia'])
    # A cadeia depois da peca e sempre a mesma, e quem confere precisa ver o
    # caminho inteiro, nao so o primeiro passo.
    if trilha == 'PECA':
        d['cadeia'] = ['REVISAO DE PECA', 'CADASTRAMENTO DE PETICAO NO TRIBUNAL',
                       'PROTOCOLO', 'ATUALIZAR CLIENTE']
    return d


def _br(d):
    t = str(d or '')[:10].split('-')
    return '%s/%s/%s' % (t[2], t[1], t[0]) if len(t) == 3 else str(d)
