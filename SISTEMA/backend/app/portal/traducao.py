""
import re
import unicodedata

TOTAL_PASSOS = 8

JORNADA = {
    1: ('Contratacao', 'Voce fechou com o escritorio e seu caso foi aberto.'),
    2: ('Montagem do caso', 'Estamos reunindo seus documentos e provas para entrar com a acao.'),
    3: ('Acao protocolada', 'Sua acao ja foi entregue na Justica e esta com o juiz.'),
    4: ('Audiencia e provas', 'E a fase de audiencia e producao de provas do seu caso.'),
    5: ('Sentenca', 'O juiz ja decidiu o seu caso em primeira instancia.'),
    6: ('Recurso', 'O caso subiu para o tribunal, que vai revisar a decisao.'),
    7: ('Recebimento', 'Fase de cobranca do valor. E aqui que o dinheiro e buscado.'),
    8: ('Encerrado', 'Seu processo foi finalizado e arquivado.'),
}

FORA_DA_REGUA = {
    'ACORDO': ('Acordo em negociacao',
               'Existe uma proposta de acordo em conversa. Sua advogada fala com voce antes de qualquer decisao.'),
    'SUSPENSO': ('Processo suspenso',
                 'O processo esta parado por determinacao da Justica. Isso e normal e nos acompanhamos.'),
}

_ETAPA_PASSO = [
    ('ARQUIVADO', 8), ('ENCERRAD', 8), ('BAIXAD', 8), ('EXTINT', 8),
    ('CITACAO DO DEVEDOR', 7), ('PENHORA', 7), ('PAGAMENTO DE CONDENACAO', 7),
    ('ELABORACAO DE CALCULO', 7), ('JULGAMENTO DA EXECUCAO', 7), ('EXECUCAO', 7),
    ('ALVARA', 7), ('LEVANTAMENTO', 7), ('CUMPRIMENTO', 7),
    ('RECURSO', 6), ('APELACAO', 6), ('AGRAVO', 6), ('CONTRARRAZOES', 6),
    ('RESP', 6), ('REVISTA', 6), ('JULGAMENTO DE ED', 6), ('EMBARGOS DE DECLARACAO', 6),
    ('AGUARDANDO JULGAMENTO', 6),
    ('SENTENCA', 5),
    ('AUDIENCIA', 4), ('INSTRUCAO', 4), ('PERICIAL', 4), ('PERICIA', 4),
    ('CONTESTACAO', 4), ('QUESITOS', 4),
    ('ACAO PROTOCOLADA', 3), ('PROTOCOLAD', 3), ('DISTRIBUID', 3), ('CITACAO', 3),
    ('ELABORACAO', 2), ('DOCUMENTO', 2), ('ANALISE', 2), ('DEFINICAO DE INTERESSES', 2),
    ('CONTRATO', 1), ('ASSINAR', 1),
]

ETAPAS_BLOQUEIA_PORTAL = ('RENUNCIAD', 'SUBSTABELECID', 'NAO FAZ PARTE DA NOSSA CARTEIRA',
                          'DESISTIU', 'NAO FECHOU', 'ANALISADO E NAO DISTRIBUIDO')


def sem_acento(txt: str) -> str:
    txt = unicodedata.normalize('NFKD', txt or '')
    return ''.join(c for c in txt if not unicodedata.combining(c)).upper().strip()


def bloqueado_no_portal(etapa: str) -> bool:
    ""
    e = sem_acento(etapa)
    return any(k in e for k in ETAPAS_BLOQUEIA_PORTAL)


def situacao_especial(etapa: str):
    e = sem_acento(etapa)
    if 'ACORDO' in e:
        return FORA_DA_REGUA['ACORDO']
    if 'SOBRESTAD' in e or 'SUSPENS' in e:
        return FORA_DA_REGUA['SUSPENSO']
    return None


def passo_da_jornada(etapa: str, grupo: str = '', classe: str = '', baixado: bool = False) -> int:
    ""
    if baixado:
        return 8
    e = sem_acento(etapa)
    for chave, passo in _ETAPA_PASSO:
        if chave in e:
            return passo
    g = sem_acento(grupo)
    if 'ARQUIV' in g:
        return 8
    if 'EXECUCAO' in g or 'COBRANCA' in g:
        return 7
    if 'RECURSAL' in g:
        return 6
    if 'JUDICIAL' in g:
        return 3
    c = sem_acento(classe)
    if 'CUMPRIMENTO' in c or 'EXECUCAO' in c:
        return 7
    return 3


def jornada(etapa: str, grupo: str = '', classe: str = '', baixado: bool = False) -> dict:
    passo = passo_da_jornada(etapa, grupo, classe, baixado)
    titulo, descricao = JORNADA[passo]
    esp = situacao_especial(etapa)
    return {
        'passo': passo,
        'total': TOTAL_PASSOS,
        'titulo': titulo,
        'descricao': descricao,
        'aviso': ({'titulo': esp[0], 'texto': esp[1]} if esp else None),
        'etapa_interna': etapa or '',
    }


_REGRAS = [
    (r'^remetid[oa]s? ao (dje|di[áa]rio)|teor do ato:|^certid[ãa]o de publica[çc][ãa]o',
     'Publicacao oficial',
     'Um ato do processo foi publicado no diario oficial.', 'rotina'),
    (r'^proferido despacho de mero expediente(?!.*(designo audi|audi[êe]ncia))',
     'Andamento interno', 'Movimentacao interna de rotina do cartorio.', 'rotina'),

    (r'^(acolhid[oa]s? em parte os embargos de declara|embargos de declara[çc][ãa]o acolhid[oa]s? em parte)',
     'Pedido de esclarecimento aceito em parte',
     'O juiz analisou o pedido de esclarecimento e mudou parte da decisao.', 'marco'),
    (r'^(n[ãa]o acolhid[oa]s? os embargos de declara|embargos de declara[çc][ãa]o n[ãa]o acolhid|'
     r'rejeitad[oa]s? os embargos de declara)',
     'Pedido de esclarecimento negado',
     'O juiz analisou o pedido de esclarecimento e manteve a decisao como estava.', 'normal'),
    (r'^(acolhid[oa]s? os embargos de declara|embargos de declara[çc][ãa]o acolhid)',
     'Pedido de esclarecimento aceito',
     'O juiz analisou o pedido de esclarecimento e ajustou a decisao.', 'marco'),

    (r'^(n[ãa]o admitid[oa] o recurso|negad[oa] seguimento)',
     'Recurso barrado',
     'O recurso nao foi aceito para seguir adiante. Por esse recurso, o caso nao sobe.', 'marco'),
    (r'^admitid[oa] o recurso',
     'Recurso aceito para seguir',
     'O recurso foi aceito e sera enviado para o tribunal superior julgar.', 'marco'),

    (r'^n[ãa]o[\s-]conhecid[oa] o recurso',
     'Recurso nao analisado',
     'O tribunal nao analisou o recurso porque faltou um requisito para ele ser julgado.', 'marco'),
    (r'^conhecid[oa] (em parte )?o recurso.{0,250}n[ãa]o[\s-]provid|^negad[oa] provimento|^n[ãa]o[\s-]provid',
     'Recurso julgado e negado',
     'O tribunal julgou o recurso e manteve a decisao.', 'marco'),
    (r'^conhecid[oa] (em parte )?o recurso.{0,250}provid[oa] em parte|^dado provimento em parte|'
     r'^provid[oa] em parte',
     'Recurso julgado e aceito em parte',
     'O tribunal julgou o recurso e mudou parte da decisao.', 'marco'),
    (r'^conhecid[oa] (em parte )?o recurso.{0,250}provid|^dado provimento|^provid[oa]\b',
     'Recurso julgado e aceito',
     'O tribunal julgou o recurso e mudou a decisao.', 'marco'),
    (r'^conhecid[oa] (em parte )?o recurso',
     'Recurso julgado',
     'O tribunal julgou o recurso. O resultado esta na decisao dos desembargadores.', 'marco'),
    (r'^julgado virtualmente',
     'Julgamento no tribunal',
     'O caso foi julgado em sessao virtual do tribunal. O resultado esta na decisao.', 'marco'),

    (r'^homologad[oa] a transa[çc][ãa]o|^homologad[oa] o acordo|homologa[çc][ãa]o de acordo|'
     r'acordo homologado|homologo o acordo',
     'Acordo homologado',
     'A Justica aprovou o acordo. A partir de agora ele vale como decisao.', 'marco'),

    (r'^acolhid[oa] em parte a impugna',
     'Contestacao da cobranca aceita em parte',
     'O juiz aceitou parte da contestacao feita contra o calculo da cobranca.', 'marco'),
    (r'^(rejeitad[oa]|n[ãa]o acolhid[oa]) a impugna',
     'Contestacao da cobranca negada',
     'O juiz nao aceitou a contestacao feita contra o calculo da cobranca.', 'marco'),
    (r'^acolhid[oa] a impugna',
     'Contestacao da cobranca aceita',
     'O juiz aceitou a contestacao feita contra o calculo da cobranca.', 'marco'),

    (r'^tr[âa]nsitad[oa] em julgado',
     'Decisao definitiva',
     'A decisao virou definitiva e nao cabe mais recurso.', 'marco'),

    (r'r[ée]plica|manifesta[çc][ãa]o sobre a contesta[çc][ãa]o|vista [àa] (parte )?autor',
     'Sua resposta foi apresentada',
     'Seu advogado respondeu ao que a outra parte alegou no processo.', 'normal'),

    (r'conclus[oa]s?.{0,45}(julgamento|senten[çc]a|despacho|decis[ãa]o)|proferir senten[çc]a|'
     r'para (julgamento|senten[çc]a)',
     'Processo com o juiz',
     'O processo foi para a mesa do juiz. A decisao ainda nao saiu.', 'normal'),

    (r'extin(t[ao]|[çc][ãa]o).{0,45}(execu[çc][ãa]o|cumprimento)|cumprimento integral do acordo|'
     r'quita[çc][ãa]o integral',
     'Cobranca encerrada',
     'A cobranca terminou porque o valor devido foi pago.', 'marco'),

    (r'cumprimento de senten[çc]a|execu[çc][ãa]o de senten[çc]a|liquida[çc][ãa]o de senten[çc]a',
     'Fase de cobranca',
     'O processo entrou na fase de cobrar o que foi decidido.', 'marco'),

    (r'contrarraz[õo]es|contraminuta',
     'Resposta ao recurso',
     'Foi apresentada a resposta ao recurso da outra parte.', 'normal'),

    (r'\bac[óo]rd[ãa]o\b',
     'Tribunal julgou o recurso',
     'Os desembargadores decidiram sobre o recurso do seu caso.', 'marco'),

    (r'\bsenten[çc]a\b|\bjulgo\b|julgad[oa] (parcialmente )?(procedente|improcedente)|'
     r'\b(procedente|improcedente)\b',
     'Sentenca proferida',
     'O juiz decidiu o seu caso. Sua advogada vai te explicar o resultado.', 'marco'),

    (r'homologa[çc][ãa]o de acordo|acordo homologado|homologo o acordo',
     'Acordo homologado',
     'A Justica aprovou o acordo. A partir de agora ele vale como decisao.', 'marco'),

    (r'\bliminar\b|tutela de urg[êe]ncia|antecipa[çc][ãa]o de tutela|tutela provis[óo]ria',
     'Decisao sobre pedido urgente',
     'O juiz analisou um pedido urgente feito no seu processo.', 'marco'),

    (r'audi[êe]ncia.{0,40}(designad|marcad|redesignad)|(designad|redesignad).{0,25}audi[êe]ncia',
     'Audiencia marcada',
     'Sua audiencia foi marcada. O escritorio avisa a data e te orienta antes.', 'marco'),
    (r'audi[êe]ncia.{0,30}(cancelad|adiad)',
     'Audiencia adiada', 'A audiencia foi desmarcada e sera remarcada.', 'marco'),
    (r'audi[êe]ncia.{0,30}(realizad|encerrad)|ata de audi[êe]ncia',
     'Audiencia realizada', 'A audiencia do seu caso aconteceu.', 'marco'),
    (r'\baudi[êe]ncia\b',
     'Movimentacao de audiencia',
     'Houve uma movimentacao ligada a audiencia do seu caso.', 'normal'),

    (r'(juntada|interpos|apresenta).{0,30}recurso ordin[áa]rio|recurso ordin[áa]rio.{0,20}interpost',
     'Recurso apresentado',
     'Foi apresentado recurso para o tribunal revisar a decisao.', 'marco'),
    (r'recurso de revista|recurso especial|recurso extraordin[áa]rio',
     'Recurso para tribunal superior',
     'Foi apresentado recurso para um tribunal superior. Antes de subir, ele passa por uma analise.', 'marco'),
    (r'(juntada|interpos|apresenta).{0,30}apela[çc][ãa]o|apela[çc][ãa]o.{0,20}interpost',
     'Recurso apresentado',
     'Foi apresentado recurso para o tribunal revisar a decisao.', 'marco'),
    (r'embargos de declara[çc][ãa]o',
     'Pedido de esclarecimento da decisao',
     'Foi pedido que o juiz esclareca pontos da decisao.', 'normal'),
    (r'agravo',
     'Agravo apresentado',
     'Foi apresentado recurso contra uma decisao tomada no processo.', 'normal'),

    (r'\balvar[áa]\b|levantamento de valor|expedi[çc][ãa]o de alvar',
     'Liberacao de valores',
     'Foi movimentada a liberacao de valores do processo.', 'marco'),
    (r'penhora|bloqueio de valor|sisbajud|bacenjud|renajud',
     'Busca de bens da outra parte',
     'A Justica esta buscando bens ou valores para pagar o seu credito.', 'marco'),
    (r'dep[óo]sito judicial|guia de dep[óo]sito|pagamento volunt[áa]rio|comprovante de pagamento',
     'Deposito no processo',
     'Foi feito um deposito de valores no processo.', 'marco'),
    (r'c[áa]lculo|liquida[çc][ãa]o',
     'Calculo do valor',
     'Esta sendo apurado quanto voce tem a receber.', 'normal'),
    (r'\bcustas\b|\bpreparo\b',
     'Custas do processo', 'Movimentacao sobre as custas do processo.', 'rotina'),

    (r'laudo pericial|\blaudo\b',
     'Laudo do perito', 'O perito entregou o laudo sobre o seu caso.', 'marco'),
    (r'per[íi]cia|\bperito\b|quesitos',
     'Pericia', 'Movimentacao sobre a pericia do seu caso.', 'normal'),

    (r'distribu[íi]d|distribui[çc][ãa]o por sorteio',
     'Processo distribuido',
     'Seu processo foi registrado e sorteado para uma vara.', 'marco'),
    (r'(juntada|apresenta[çc][ãa]o|protocolo).{0,30}contesta[çc][ãa]o|contesta[çc][ãa]o juntada',
     'Defesa da outra parte',
     'A outra parte apresentou a defesa dela no processo.', 'marco'),
    (r'\bcita[çc][ãa]o\b|\bcitad[oa]\b|\bmandado de cita',
     'Outra parte foi chamada',
     'A Justica chamou a outra parte para responder ao processo.', 'normal'),
    (r'revelia',
     'Outra parte nao respondeu',
     'A outra parte nao apresentou defesa no prazo.', 'marco'),

    (r'tr[âa]nsito em julgado',
     'Decisao definitiva',
     'A decisao virou definitiva e nao cabe mais recurso.', 'marco'),
    (r'arquivad|baixa definitiva|\bbaixa\b',
     'Processo arquivado', 'O processo foi encerrado e arquivado.', 'marco'),
    (r'extin[çc][ãa]o|extint',
     'Processo extinto', 'O processo foi encerrado pelo juiz.', 'marco'),

    (r'conclus[oa]s? (os autos|para)|encerrada a conclus',
     'Processo com o juiz',
     'O processo foi para a mesa do juiz para uma decisao.', 'normal'),
    (r'despacho|mero expediente|ato ordinat[óo]rio',
     'Andamento interno', 'Movimentacao interna de rotina do cartorio.', 'rotina'),
    (r'decis[ãa]o',
     'Decisao sobre o andamento',
     'O juiz decidiu algo sobre o andamento do processo, sem julgar o caso ainda.', 'normal'),
    (r'certid[ãa]o de publica[çc][ãa]o|disponibilizad|publicad|di[áa]rio (da justi[çc]a|eletr[ôo]nico)|\bdje\b|\bdj\b',
     'Publicacao oficial',
     'Um ato do processo foi publicado no diario oficial.', 'rotina'),
    (r'intima[çc][ãa]o|notifica[çc][ãa]o',
     'Aviso enviado pela Justica',
     'A Justica enviou um aviso as partes do processo.', 'rotina'),
    (r'decorrido o prazo|prazo decorrido',
     'Prazo encerrado', 'Um prazo do processo terminou.', 'rotina'),
    (r'remetid|recebidos? os autos|\bremessa\b|redistribu',
     'Processo mudou de setor',
     'O processo foi enviado para outro setor da Justica.', 'rotina'),
    (r'habilita[çc][ãa]o|substabelec|procura[çc][ãa]o',
     'Advogado no processo',
     'Houve movimentacao sobre os advogados cadastrados no processo.', 'rotina'),
    (r'\bjuntada\b|\bpeti[çc][ãa]o\b|\bmanifesta[çc][ãa]o\b',
     'Documento juntado',
     'Um documento foi anexado ao processo.', 'rotina'),
    (r'certid[ãa]o',
     'Certidao', 'Foi emitida uma certidao no processo.', 'rotina'),
]

_REGRAS_COMPILADAS = [(re.compile(rx, re.I), t, e, r) for rx, t, e, r in _REGRAS]

_LIMPEZA = [
    (re.compile(r'Nome da parte ocultada nos termos da Res\.?\s*121/2010 do CNJ', re.I), 'uma das partes'),
    (re.compile(r'\(;\d+:[^)]*\)'), ''),
    (re.compile(r'\s+'), ' '),
]


def limpar(descricao: str) -> str:
    txt = (descricao or '').replace('\n', ' ').strip()
    for rx, sub in _LIMPEZA:
        txt = rx.sub(sub, txt)
    return txt.strip(' .;:-')


def traduzir_movimento(descricao: str) -> dict:
    ""
    original = limpar(descricao)
    for rx, titulo, explicacao, relevancia in _REGRAS_COMPILADAS:
        if rx.search(original):
            return {'titulo': titulo, 'explicacao': explicacao,
                    'relevancia': relevancia, 'original': original}
    return {'titulo': 'Andamento do processo', 'explicacao': original or 'Movimentacao registrada pela Justica.',
            'relevancia': 'rotina', 'original': original}


def traduzir_documento(nome: str, tipo_documento: str = '') -> dict:
    ""
    base = (tipo_documento or nome or 'Documento').strip()
    t = sem_acento(base)
    if 'SENTENCA' in t:
        titulo, rel, exp = 'Sentenca', 'marco', 'A decisao do juiz sobre o seu caso.'
    elif 'ACORDAO' in t:
        titulo, rel, exp = 'Decisao do tribunal', 'marco', 'A decisao do tribunal sobre o recurso.'
    elif 'ATA' in t and 'AUDIENCIA' in t:
        titulo, rel, exp = 'Registro da audiencia', 'marco', 'O registro do que aconteceu na audiencia.'
    elif 'LAUDO' in t or 'LTCAT' in t or 'PPP' in t:
        titulo, rel, exp = 'Laudo tecnico', 'marco', 'Um laudo tecnico juntado ao seu processo.'
    elif 'CONTESTACAO' in t or 'DEFESA' in t:
        titulo, rel, exp = 'Defesa da outra parte', 'marco', 'A defesa apresentada pela outra parte.'
    elif 'PERIC' in t:
        titulo, rel, exp = 'Documento da pericia', 'normal', 'Um documento ligado a pericia do seu caso.'
    elif 'RECURSO' in t or 'APELACAO' in t or 'AGRAVO' in t:
        titulo, rel, exp = 'Recurso', 'marco', 'Uma peca de recurso juntada ao processo.'
    elif 'DESPACHO' in t or 'DECISAO' in t:
        titulo, rel, exp = ('Decisao sobre o andamento', 'normal',
                            'O juiz decidiu algo sobre o andamento, sem julgar o caso ainda.')
    elif 'INICIAL' in t:
        titulo, rel, exp = 'Peticao inicial', 'marco', 'A peca que abriu o seu processo.'
    elif 'PROCURACAO' in t or 'SUBSTABELEC' in t or 'HABILITACAO' in t:
        titulo, rel, exp = 'Documento do advogado', 'rotina', 'Documento sobre quem atua no processo.'
    elif 'PETICAO' in t or 'MANIFEST' in t:
        titulo, rel, exp = 'Peca apresentada', 'rotina', 'Uma peca foi apresentada no processo.'
    elif 'INTIMACAO' in t or 'CERTIDAO' in t or 'COMPROVANTE' in t or 'GUIA' in t:
        titulo, rel, exp = 'Documento de rotina', 'rotina', 'Documento de rotina do processo.'
    else:
        titulo, rel, exp = 'Documento anexado', 'rotina', 'Um documento foi anexado ao processo.'
    return {'titulo': titulo, 'explicacao': exp, 'relevancia': rel, 'original': nome or base}


_PROXIMO = {
    1: ['Reunir os seus documentos e provas.',
        'Montar a acao com todos os pedidos que o seu caso permite.'],
    2: ['Terminar a montagem da acao.',
        'Protocolar o processo na Justica.'],
    3: ['A Justica vai chamar a outra parte para se defender.',
        'Depois disso costuma ser marcada a audiencia.'],
    4: ['Realizar a audiencia e apresentar as provas.',
        'Depois da instrucao, o juiz profere a sentenca.'],
    5: ['Analisar a decisao com voce.',
        'Se couber recurso, apresentar dentro do prazo.'],
    6: ['Aguardar o julgamento pelo tribunal.',
        'Assim que sair a decisao, sua advogada te explica o resultado.'],
    7: ['Buscar o pagamento do valor devido.',
        'Quando o dinheiro entrar, voce e avisado para receber.'],
    8: ['Seu processo esta encerrado.',
        'Guarde os documentos. Se precisar de alguma certidao, e so pedir.'],
}


def proximos_passos(passo: int) -> list:
    return list(_PROXIMO.get(passo, _PROXIMO[3]))


def traduzir_evento(item: dict) -> dict:
    ""
    if item.get('tipo') == 'documento':
        t = traduzir_documento(item.get('nome') or '', item.get('tipo_documento') or '')
        t['tecnico'] = (item.get('tipo_documento') or item.get('nome') or '').strip()
    else:
        t = traduzir_movimento(item.get('descricao') or '')
        t['tecnico'] = limpar(item.get('descricao') or '')
    if t['tecnico'].strip().lower() == (t['titulo'] or '').strip().lower():
        t['tecnico'] = ''

    if (item.get('grau') or '').upper() == 'G2' and t['titulo'] == 'Processo distribuido':
        t['titulo'] = 'Recurso chegou ao tribunal'
        t['explicacao'] = 'Seu recurso foi registrado no tribunal e sorteado para um relator.'

    t['data'] = item.get('data')
    t['tipo'] = item.get('tipo')
    t['documento_id'] = item.get('documento_id')
    t['tem_arquivo'] = bool(item.get('tem_arquivo'))
    t['tem_texto'] = bool(item.get('tem_texto'))
    return t


def montar_resumo(capa: dict, eventos_brutos: list, etapa: str, grupo: str = '') -> dict:
    ""
    capa = capa or {}
    from .jornada import jornada as _jornada
    j = _jornada(etapa, grupo, capa.get('classe') or '',
                 bool(capa.get('baixado')) or bool(capa.get('arquivado')),
                 eventos=eventos_brutos)

    eventos = [traduzir_evento(it) for it in (eventos_brutos or [])]
    marcos = [e for e in eventos if e['relevancia'] == 'marco']
    resumida = [e for e in eventos if e['relevancia'] in ('marco', 'normal')]
    ultimo = next((e for e in eventos if e['relevancia'] in ('marco', 'normal')), None)

    return {
        'jornada': j,
        'ultimo_andamento': ultimo,
        'marcos': marcos[:12],
        'linha_resumida': resumida[:40],
        'proximos_passos': proximos_passos(j['passo']),
        'total_eventos': len(eventos),
    }
