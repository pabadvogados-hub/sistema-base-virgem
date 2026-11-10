""

RE = 'requerida'

ANDAMENTOS_RE = {
    'Sua resposta foi apresentada': ('Resposta da outra parte',
                                     'A outra parte respondeu ao que foi dito na defesa.'),
    'Defesa da outra parte': ('Defesa apresentada',
                              'Foi apresentada defesa contra os pedidos do processo.'),
    'Outra parte foi chamada': ('Chamado para a defesa',
                                'A Justica fez o chamado oficial para a apresentacao da defesa.'),
    'Outra parte nao respondeu': ('Falta de defesa registrada',
                                  'O processo registrou que uma defesa nao foi apresentada no prazo. '
                                  'O escritorio confere o que isso muda.'),
    'Busca de bens da outra parte': ('Busca de bens e valores',
                                     'A Justica movimentou a busca de bens ou valores para o pagamento da cobranca.'),
    'Calculo do valor': ('Calculo do valor', 'Esta sendo apurado o valor da cobranca.'),
    'Recurso chegou ao tribunal': ('Recurso chegou ao tribunal',
                                   'O recurso foi registrado no tribunal e sorteado para um relator.'),
}

PASSOS_RE = {
    'distribuida': ('Ação distribuída', 'A ação foi entregue na Justiça e sorteada para uma vara.'),
    'contestacao': ('Sua defesa', 'O processo chega até você e o escritório apresenta a sua defesa.'),
    'replica': ('Resposta da outra parte', 'A outra parte responde ao que foi dito na sua defesa.'),
    'citacao': ('Chamado para pagar', 'A Justiça chama para o pagamento do que foi decidido. O escritório orienta cada passo.'),
    'embargos': ('Defesa na cobrança', 'É possível contestar o valor cobrado. Abre um novo prazo.'),
    'recebimento': ('Pagamento', 'Fase de pagamento do valor definido no processo.'),
}


def polo_efetivo(polo: str, nosso_polo: str = '') -> str:
    ""
    if polo:
        return polo
    n = (nosso_polo or '').strip().lower()
    if n.startswith('passiv'):
        return RE
    if n.startswith('ativ'):
        return 'requerente'
    return ''


def andamento(t: dict, polo: str) -> dict:
    ""
    if polo != RE or not isinstance(t, dict):
        return t
    troca = ANDAMENTOS_RE.get(t.get('titulo'))
    if troca:
        t = dict(t)
        t['titulo'], t['explicacao'] = troca
    return t


def tradutor(traduzir, polo: str):
    ""
    if polo != RE:
        return traduzir
    return lambda e: andamento(traduzir(e), polo)


def jornada(j: dict, polo: str) -> dict:
    if polo != RE or not isinstance(j, dict):
        return j
    j = dict(j)
    passos = []
    for p in (j.get('passos') or []):
        troca = PASSOS_RE.get(p.get('chave'))
        if troca:
            antigo = p.get('titulo')
            p = dict(p)
            p['titulo'], p['descricao'] = troca
            if p.get('estado') == 'atual' and j.get('titulo') == antigo:
                j['titulo'], j['descricao'] = troca
        passos.append(p)
    if passos:
        j['passos'] = passos
    return j


def resumo(res: dict, polo: str) -> dict:
    ""
    if polo != RE or not isinstance(res, dict):
        return res
    res = dict(res)
    for chave in ('jornada', 'trilha'):
        if isinstance(res.get(chave), dict):
            res[chave] = jornada(res[chave], polo)
    if isinstance(res.get('ultimo_andamento'), dict):
        res['ultimo_andamento'] = andamento(res['ultimo_andamento'], polo)
    for chave in ('marcos', 'linha_resumida'):
        if isinstance(res.get(chave), list):
            res[chave] = [andamento(x, polo) for x in res[chave]]
    return res
