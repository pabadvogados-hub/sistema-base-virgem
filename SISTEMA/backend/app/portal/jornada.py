""
from .traducao import sem_acento, traduzir_evento

TRILHAS = {
    'PRE': {
        'nome': 'Antes da ação',
        'cor': 'ambar',
        'passos': [
            ('atendimento', 'Atendimento', 'Conversamos sobre o seu caso e estamos avaliando o que dá para fazer.'),
            ('fechamento', 'Contrato', 'Contrato de honorários assinado. Seu caso foi aberto no escritório.'),
            ('documentos', 'Documentos', 'Estamos reunindo seus documentos e provas. É aqui que você mais ajuda.'),
            ('montagem', 'Montando a ação', 'Sua ação está sendo escrita, com os pedidos e as contas.'),
        ],
    },
    'CONHECIMENTO': {
        'nome': 'Processo na Justiça',
        'cor': 'azul',
        'passos': [
            ('distribuida', 'Ação distribuída', 'Sua ação foi entregue na Justiça e sorteada para uma vara.'),
            ('contestacao', 'Defesa da empresa', 'A outra parte foi chamada e apresenta a defesa dela.'),
            ('replica', 'Nossa resposta', 'Respondemos ponto a ponto o que a outra parte alegou.'),
            ('provas', 'Provas', 'É o momento de indicar testemunhas, documentos e perícia.'),
            ('audiencia', 'Audiência', 'Audiência para ouvir as partes e as testemunhas. Nem todo processo tem.'),
            ('sentenca', 'Sentença', 'O juiz decide o seu caso. Sua advogada explica o resultado para você.'),
        ],
        'opcionais': {'audiencia'},
    },
    'RECURSAL': {
        'nome': 'Recurso no tribunal',
        'cor': 'roxo',
        'passos': [
            ('recurso', 'Recurso apresentado', 'Foi pedido que o tribunal revise a decisão.'),
            ('distribuido', 'Recurso no tribunal', 'O recurso chegou ao tribunal e foi sorteado para um relator.'),
            ('contrarrazoes', 'Resposta da outra parte', 'A outra parte tem prazo para responder ao recurso.'),
            ('julgamento', 'Julgamento', 'Os desembargadores julgam o recurso.'),
        ],
    },
    'EXECUCAO': {
        'nome': 'Cobrança do valor',
        'cor': 'verde',
        'passos': [
            ('exec_distribuida', 'Execução iniciada', 'Começou a fase de cobrar o que foi decidido.'),
            ('citacao', 'Devedor citado', 'A outra parte foi chamada para pagar.'),
            ('embargos', 'Defesa na execução', 'A outra parte pode contestar a cobrança. Abre um novo prazo.'),
            ('penhora', 'Busca de bens', 'A Justiça busca bens e valores, e o cálculo é conferido.'),
            ('recebimento', 'Recebimento', 'Fase de liberar o dinheiro para você.'),
        ],
    },
}

ENCERRADO = ('encerrado', 'Encerrado', 'Seu processo foi finalizado e arquivado.')

_MAPA = [
    ('ARQUIVAD', None, 'encerrado'),
    ('ENCERRAD', None, 'encerrado'),
    ('BAIXAD', None, 'encerrado'),
    ('EXTINT', None, 'encerrado'),

    ('CITACAO DO DEVEDOR', 'EXECUCAO', 'citacao'),
    ('AGUARDANDO PENHORA', 'EXECUCAO', 'penhora'),
    ('ELABORACAO DE CALCULO', 'EXECUCAO', 'penhora'),
    ('AGUARDANDO PAGAMENTO DE CONDENACAO', 'EXECUCAO', 'recebimento'),
    ('ALVARA', 'EXECUCAO', 'recebimento'),
    ('LEVANTAMENTO', 'EXECUCAO', 'recebimento'),
    ('EMBARGOS A EXECUCAO', 'EXECUCAO', 'embargos'),
    ('IMPUGNACAO', 'EXECUCAO', 'embargos'),
    ('AGRAVO DE PETICAO', 'EXECUCAO', 'embargos'),
    ('JULGAMENTO DA EXECUCAO', 'EXECUCAO', 'penhora'),
    ('CUMPRIMENTO', 'EXECUCAO', 'exec_distribuida'),
    ('EXECUCAO', 'EXECUCAO', 'exec_distribuida'),

    ('APRESENTACAO DE CONTRARRAZOES', 'RECURSAL', 'contrarrazoes'),
    ('CONTRARRAZOES', 'RECURSAL', 'contrarrazoes'),
    ('AGUARDANDO JULGAMENTO DO RECURSO', 'RECURSAL', 'julgamento'),
    ('AGUARDANDO JULGAMENTO DE ED', 'RECURSAL', 'julgamento'),
    ('RECURSO JULGADO', 'RECURSAL', 'julgamento'),
    ('RECURSO PROTOCOLADO', 'RECURSAL', 'recurso'),
    ('RECURSO ORDINARIO', 'RECURSAL', 'recurso'),
    ('RECURSO DE REVISTA', 'RECURSAL', 'distribuido'),
    ('APELACAO', 'RECURSAL', 'recurso'),
    ('AGRAVO DE INSTRUMENTO', 'RECURSAL', 'recurso'),
    ('EMBARGOS DE DECLARACAO', 'RECURSAL', 'julgamento'),
    ('RESP', 'RECURSAL', 'distribuido'),
    ('RECURSO', 'RECURSAL', 'recurso'),

    ('SENTENCA', 'CONHECIMENTO', 'sentenca'),
    ('AGUARDANDO AUDIENCIA', 'CONHECIMENTO', 'audiencia'),
    ('FASE DE INSTRUCAO', 'CONHECIMENTO', 'provas'),
    ('FASE PERICIAL', 'CONHECIMENTO', 'provas'),
    ('PERICIA', 'CONHECIMENTO', 'provas'),
    ('QUESITOS', 'CONHECIMENTO', 'provas'),
    ('AGUARDANDO PROTOCOLO DA CONTESTACAO', 'CONHECIMENTO', 'contestacao'),
    ('CONTESTACAO', 'CONHECIMENTO', 'contestacao'),
    ('REPLICA', 'CONHECIMENTO', 'replica'),
    ('AGUARDANDO JULGAMENTO', 'CONHECIMENTO', 'sentenca'),
    ('ACAO PROTOCOLADA', 'CONHECIMENTO', 'distribuida'),
    ('PROTOCOLAD', 'CONHECIMENTO', 'distribuida'),
    ('DISTRIBUID', 'CONHECIMENTO', 'distribuida'),
    ('REQUERIMENTO PROTOCOLADO', 'CONHECIMENTO', 'distribuida'),
    ('NOTIFICACAO', 'CONHECIMENTO', 'distribuida'),

    ('DESENVOLVENDO INICIAL', 'PRE', 'montagem'),
    ('ELABORACAO DE DOCUMENTOS', 'PRE', 'documentos'),
    ('ELABORACAO DE PARECER', 'PRE', 'montagem'),
    ('CRIAR PASTA', 'PRE', 'documentos'),
    ('CONTRATO FECHADO', 'PRE', 'fechamento'),
    ('ASSINAR CONTRATO', 'PRE', 'fechamento'),
    ('ELABORACAO/REVISAO DE CONTRATO', 'PRE', 'fechamento'),
    ('FASE DE CONTRATACAO', 'PRE', 'fechamento'),
    ('ANALISE DO CASO', 'PRE', 'atendimento'),
    ('DEFINICAO DE INTERESSES', 'PRE', 'atendimento'),
    ('APRESENTACAO DE PROPOSTA', 'PRE', 'atendimento'),
    ('REUNIAO AGENDADA', 'PRE', 'atendimento'),
    ('CONTATO INICIAL', 'PRE', 'atendimento'),
    ('CONSULTA', 'PRE', 'atendimento'),
]

_POR_GRUPO = {
    'ARQUIVAMENTO': (None, 'encerrado'),
    'EXECUCAO': ('EXECUCAO', 'exec_distribuida'),
    'COBRANCA': ('EXECUCAO', 'exec_distribuida'),
    'RECURSAL': ('RECURSAL', 'recurso'),
    'JUDICIAL': ('CONHECIMENTO', 'distribuida'),
    'ADMINISTRATIVO': ('CONHECIMENTO', 'distribuida'),
    'CONSULTORIA': ('PRE', 'atendimento'),
    'NEGOCIACAO': ('PRE', 'atendimento'),
    'MARKETING': ('PRE', 'atendimento'),
}


_ORDEM_TRILHA = {'PRE': 0, 'CONHECIMENTO': 1, 'RECURSAL': 2, 'EXECUCAO': 3}


def _trilha_do_tribunal(classe: str):
    ""
    c = sem_acento(classe)
    if any(k in c for k in ('CUMPRIMENTO', 'EXECUCAO', 'LIQUIDACAO')):
        return 'EXECUCAO', 'exec_distribuida'
    if any(k in c for k in ('RECURSO', 'AGRAVO', 'APELACAO', 'EMBARGOS DE DECLARACAO')):
        return 'RECURSAL', 'distribuido'
    return None


def _pelo_advbox(etapa: str, grupo: str):
    ""
    e = sem_acento(etapa)
    for chave, trilha, passo in _MAPA:
        if chave in e:
            return trilha, passo
    g = sem_acento(grupo)
    for chave, (trilha, passo) in _POR_GRUPO.items():
        if chave in g:
            return trilha, passo
    return None


def _resolver(etapa: str, grupo: str, classe: str):
    ""
    por_advbox = _pelo_advbox(etapa, grupo)
    por_tribunal = _trilha_do_tribunal(classe)

    if por_advbox and por_advbox[0] is None:
        return por_advbox
    if not por_tribunal:
        return por_advbox or ('CONHECIMENTO', 'distribuida')
    if not por_advbox:
        return por_tribunal
    if por_advbox[0] == por_tribunal[0]:
        return por_advbox
    return max(por_advbox, por_tribunal, key=lambda t: _ORDEM_TRILHA.get(t[0], 0))


_PELO_ANDAMENTO = {
    'Sentenca proferida': ('CONHECIMENTO', 'sentenca'),
    'Recurso apresentado': ('RECURSAL', 'recurso'),
    'Tribunal julgou o recurso': ('RECURSAL', 'julgamento'),
    'Recurso julgado': ('RECURSAL', 'julgamento'),
    'Recurso julgado e negado': ('RECURSAL', 'julgamento'),
    'Recurso julgado e aceito': ('RECURSAL', 'julgamento'),
    'Recurso julgado e aceito em parte': ('RECURSAL', 'julgamento'),
    'Recurso nao analisado': ('RECURSAL', 'julgamento'),
    'Recurso barrado': ('RECURSAL', 'julgamento'),
    'Recurso aceito para seguir': ('RECURSAL', 'julgamento'),
    'Julgamento no tribunal': ('RECURSAL', 'julgamento'),
    'Contestacao da cobranca aceita': ('EXECUCAO', 'embargos'),
    'Contestacao da cobranca aceita em parte': ('EXECUCAO', 'embargos'),
    'Contestacao da cobranca negada': ('EXECUCAO', 'embargos'),
}
_ESCLARECIMENTO_JULGADO = ('Pedido de esclarecimento aceito',
                           'Pedido de esclarecimento aceito em parte',
                           'Pedido de esclarecimento negado')


def _posicao(trilha_passo):
    ""
    trilha, passo = trilha_passo
    chaves = [p[0] for p in TRILHAS.get(trilha, {}).get('passos', [])]
    return (_ORDEM_TRILHA.get(trilha, 0), chaves.index(passo) if passo in chaves else 0)


def _pelos_andamentos(eventos):
    ""
    melhor = None
    for e in eventos or []:
        if (e.get('descricao') or '').strip().lower().startswith('proferid'):
            continue
        titulo = traduzir_evento(e).get('titulo')
        if titulo in _ESCLARECIMENTO_JULGADO:
            achado = (('RECURSAL', 'julgamento') if (e.get('grau') or '').upper() == 'G2'
                      else ('CONHECIMENTO', 'sentenca'))
        else:
            achado = _PELO_ANDAMENTO.get(titulo)
        if achado and (melhor is None or _posicao(achado) > _posicao(melhor)):
            melhor = achado
    return melhor


def jornada(etapa: str, grupo: str = '', classe: str = '', baixado: bool = False,
            eventos: list = None) -> dict:
    ""
    trilha, passo_chave = (None, 'encerrado') if baixado else _resolver(etapa, grupo, classe)
    if trilha is not None and eventos:
        pelo_tribunal = _pelos_andamentos(eventos)
        if pelo_tribunal and _posicao(pelo_tribunal) > _posicao((trilha, passo_chave)):
            trilha, passo_chave = pelo_tribunal

    if trilha is None:
        return {
            'trilha': 'ENCERRADO',
            'trilha_nome': 'Processo encerrado',
            'cor': 'cinza',
            'passo': 1, 'total': 1,
            'titulo': ENCERRADO[1],
            'descricao': ENCERRADO[2],
            'passos': [{'n': 1, 'chave': 'encerrado', 'titulo': ENCERRADO[1],
                        'descricao': ENCERRADO[2], 'estado': 'atual', 'opcional': False}],
            'aviso': None,
            'etapa_interna': etapa or '',
        }

    conf = TRILHAS[trilha]
    chaves = [p[0] for p in conf['passos']]
    idx = chaves.index(passo_chave) if passo_chave in chaves else 0
    opcionais = conf.get('opcionais', set())

    passos = []
    for i, (chave, titulo, desc) in enumerate(conf['passos']):
        estado = 'feito' if i < idx else ('atual' if i == idx else 'futuro')
        passos.append({'n': i + 1, 'chave': chave, 'titulo': titulo,
                       'descricao': desc, 'estado': estado,
                       'opcional': chave in opcionais})

    return {
        'trilha': trilha,
        'trilha_nome': conf['nome'],
        'cor': conf['cor'],
        'passo': idx + 1,
        'total': len(conf['passos']),
        'titulo': conf['passos'][idx][1],
        'descricao': conf['passos'][idx][2],
        'passos': passos,
        'aviso': _aviso(etapa),
        'etapa_interna': etapa or '',
    }


def _aviso(etapa: str):
    ""
    e = sem_acento(etapa)
    if 'ACORDO' in e:
        return {'titulo': 'Acordo em negociação',
                'texto': 'Existe uma proposta de acordo em conversa. '
                         'Sua advogada fala com você antes de qualquer decisão.'}
    if 'SOBRESTAD' in e or 'SUSPENS' in e:
        return {'titulo': 'Processo suspenso',
                'texto': 'O processo está parado por determinação da Justiça. '
                         'Isso acontece e nós seguimos acompanhando.'}
    if 'ENTRE EM CONTATO' in e:
        return {'titulo': 'Fale com o escritório',
                'texto': 'Há uma novidade importante no seu processo que merece '
                         'uma conversa. Chame a gente no WhatsApp.'}
    return None
