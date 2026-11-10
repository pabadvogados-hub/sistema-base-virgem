"""Quem o escritorio tutela neste processo — e, nesta intimacao, de quem e o prazo.

Porte fiel do que ja estava decidido em CONTROLADORIA/varredura.py e
CONTROLADORIA/classificador.py (POP da controladoria, handoff doc 01 passo 3 e
doc 04 secao 6) para dentro do Sistema Escritório Modelo. Antes disso a aba Controladoria nao
respondia a primeira pergunta do POP — "o prazo e do nosso cliente ou do outro?" —
e carimbava NOSSO em 100% das intimacoes.

Duas perguntas, duas fontes:

  1. QUEM TUTELAMOS (por processo, vem do ADVBOX)
     `customers[].origin == "PARTE CONTRARIA"` marca o adversario; todos os demais
     sao NOSSOS — no plural, porque em litisconsorcio o escritorio pode patrocinar
     mais de uma parte. Cuidado: para o cliente, `origin` guarda a ORIGEM DO LEAD
     (INDICACAO, ANUNCIO, GOOGLE...), nunca o papel processual.

  2. DE QUEM E O PRAZO (por intimacao, vem do TEXTO do despacho)
     Precedencia: prazo comum > comando dirigido a uma parte (lido contra o nosso
     polo) > ato recursal (titularidade_padrao NOSSO) > padrao NOSSO.

O polo (ativo/passivo) e a ponte entre as duas: sem ele, "manifeste-se a reclamada"
nao diz nada. Casa-se o nome do nosso cliente contra as partes que o SYNC traz na
carteira (`polo_ativo`/`polo_passivo`), que e fonte melhor que o regex sobre o
texto do DJEN usado na varredura original.
"""
from __future__ import annotations

import json
import re
import unicodedata

# Palavras que nao ajudam a identificar uma parte (razao social e ruido demais).
_RUIDO = {
    'ltda', 'sa', 's/a', 'me', 'epp', 'eireli', 'cia', 'e', 'de', 'da', 'do',
    'das', 'dos', 'em', 'recuperacao', 'judicial', 'atual', 'outro', 'outros',
    'the', 'grupo', 'comercio', 'industria', 'servicos', 'transportes',
}

# Expressoes que abrem prazo para o POLO ATIVO.
_ATIVO = [
    'manifeste-se o autor', 'manifeste-se a autora', 'manifeste-se o reclamante',
    'manifeste-se o exequente', 'manifeste-se o embargante', 'manifeste-se o agravante',
    'diga o autor', 'diga a autora', 'diga o reclamante', 'diga o exequente',
    'manifeste-se a parte autora', 'manifeste-se a parte exequente',
    'intime-se o autor', 'intime-se a parte autora', 'intime-se o reclamante',
    'intime-se a parte reclamante', 'intime-se a parte exequente',
    'intime-se a parte requerente',
    'a parte autora', 'o reclamante devera', 'o autor devera',
    # requerente aparece em tutela antecedente e em cautelar, e faltava:
    # um despacho mandando 'a requerente' regularizar cadastro abria prazo
    # nosso quando o comando era do outro lado.
    'manifeste-se a requerente', 'manifeste-se o requerente',
    'intime-se a requerente', 'intime-se o requerente',
    'deve a requerente', 'deve o requerente', 'devera a requerente',
    'deve a autora', 'deve o autor', 'deve o reclamante', 'deve a exequente',
    'a requerente devera',
    # Calibracao de 01/10/2026 (regra 3): papeis processuais que faltavam.
    # "junte o exequente a planilha" abria prazo nosso quando somos executados
    # (Banco do caso D x empresa D); "de-se ciencia a parte recorrente" e o prazo
    # de quem recorreu (caso F x empresa B).
    # (agravante/agravado, recorrente/recorrido, embargante/embargado sao papeis
    # RELATIVOS ao recurso, nao polos: ficam fora destas listas.)
    'o exequente devera', 'a exequente devera', 'junte o exequente', 'junte a exequente',
    'intime-se o exequente', 'intime-se a exequente', 'promova o exequente',
    'o exequente promova', 'a parte exequente devera', 'intime-se a parte exequente',
    'a autora devera', 'devera a autora', 'a autora complementar', 'concede a autora',
    'concedo a autora', 'concede-se a autora', 'a parte autora emende', 'emende a autora',
    'emende o autor', 'emende a parte autora', 'intime-se a parte autora para',
    'a parte requerente', 'determino que a parte autora', 'cabe a parte autora',
    'a parte autora, no prazo', 'a parte reclamante, no prazo',
]
# Expressoes que abrem prazo para o POLO PASSIVO.
_PASSIVO = [
    'manifeste-se o reu', 'manifeste-se a re', 'manifeste-se o reclamado',
    'manifeste-se a reclamada', 'manifeste-se o executado', 'manifeste-se o embargado',
    'diga o reu', 'diga a re', 'diga o reclamado', 'diga a reclamada',
    'intime-se o reu', 'intime-se a parte re', 'intime-se o executado',
    'intime-se a parte contraria', 'a parte re devera', 'o executado devera',
    'citado o reu', 'cite-se o reu', 'cite-se a reclamada',
    'manifeste-se a requerida', 'manifeste-se o requerido',
    'intime-se a requerida', 'intime-se o requerido',
    'deve a requerida', 'deve o requerido', 'devera a requerida',
    'deve a re', 'deve o reu', 'deve a reclamada', 'deve o executado',
    'a requerida devera',
    # Calibracao de 01/10/2026 (regra 3).
    'a executada devera', 'intime-se a executada', 'intime-se a parte executada',
    'a parte executada fica intimada', 'fica a parte executada intimada',
    'o executado fica intimado', 'a executada fica intimada', 'a parte executada',
    'intime-se a reclamada', 'intime-se a parte reclamada', 'intime-se o reclamado',
    'a reclamada informe', 'a reclamada devera', 'intime-se a reclamada para',
    'intime-se o reu para', 'intime-se o requerido para', 'intime-se a requerida para',
    'a requerida regularize', 'o requerido regularize', 'a parte requerida',
    'a parte re, no prazo', 'a parte reclamada, no prazo',
]

# Cabecalho da publicacao: quando o cadastro nao sabe o nosso polo, a propria
# publicacao diz quem e quem. PJe: "RECLAMANTE: X RECLAMADO: Y"; e-SAJ:
# "Processo N - Classe - Assunto - X - Y -" (primeiro o ativo).
_RE_CAB_PJE = re.compile(
    r'(?:reclamante|autor|autora|exequente|requerente|embargante|impetrante|requerente)\s*:\s*'
    r'(?P<a>.{3,120}?)\s+(?:reclamad[oa]|reu|re|executad[oa]|requerid[oa]|embargad[oa]|impetrad[oa])\s*:\s*'
    r'(?P<b>.{3,120}?)(?:\s+(?:destinatario|intimacao|intimad|notificacao|ato ordinatorio|advogad)|$)')
_RE_CAB_SAJ = re.compile(r'^processo [\d.\-]+ (?:\([^)]*\) )?- (?P<resto>.{10,400})')


# Palavras de classe/assunto do e-SAJ: o segmento que as tem nao e parte.
_CLASSE = re.compile(
    r'\b(cumprimento|execucao|procedimento|acao|contratos?|titulo|interdicao|curatela|'
    r'cobranca|despejo|alimentos|inventario|tutela|obrigacao|indenizacao|incidente|'
    r'desconsideracao|liminar|penhora|alugu|locacao|praticas|direito|civel|sumario|'
    r'ordinario|embargos|monitoria|consignacao|busca e apreensao|divorcio|guarda|'
    r'partilha|usucapiao|reintegracao|despesas|condominio|dano|responsabilidade|'
    r'rescisao|nulidade|revisional|cautelar|mandado|habeas|arbitramento|liquidacao)\b')


def partes_cabecalho_lista(texto_bruto):
    """Partes do cabecalho, na ordem (ativo primeiro). Lista vazia quando nao ha."""
    t = limpar(texto_bruto)
    m = _RE_CAB_PJE.search(t)
    if m:
        return [m.group('a').strip(), m.group('b').strip()]
    m = _RE_CAB_SAJ.match(t)
    if not m:
        return []
    segs = [s.strip() for s in m.group('resto').split(' - ')]
    partes = []
    for i, s in enumerate(segs):
        if not s or s == '-':
            continue
        # comeco do despacho: verbo, "fls.", ou segmento longo
        if partes and (re.search(r'\b(vistos|fls?\.|ficam?|fica|intim|cienc|defiro|indefiro|'
                                 r'manifest|providenci|expe[cç]a|cumpra|junte|recolh|trata-se|'
                                 r'homologo|julgo|decido|determino)\b', s) or len(s) > 90):
            break
        if _CLASSE.search(s) and not partes:
            continue            # classe/assunto antes das partes
        if len(s) > 90:
            break
        partes.append(s)
    # Segredo de justica: as partes vem abreviadas ("c.f.", "s.g.f. e outro") e o
    # que veio antes delas era assunto, nao parte (ex.: "remocao").
    iniciais = [p for p in partes if eh_iniciais(p)]
    if iniciais:
        return iniciais[:6]
    return partes[:6]


def partes_do_cabecalho(texto_bruto):
    """(ativo, passivo) lidos do cabecalho da publicacao, ou (None, None)."""
    p = partes_cabecalho_lista(texto_bruto)
    if len(p) >= 2:
        return p[0], p[1]
    return None, None


_INICIAIS = re.compile(r'^(?:[a-z]\.\s*)+(?:e outros?)?$')


def eh_iniciais(nome):
    """'c.f.', 's.g.f. e outro': parte abreviada (segredo de justica) — nao serve para casar."""
    return bool(_INICIAIS.match((nome or '').strip().strip('- ').strip()))


def polo_pelo_cabecalho(texto_bruto, clientes, polo_ativo=None, polo_passivo=None, limiar=0.34):
    """('ativo'|'passivo'|None, fonte): em que polo esta o nosso cliente.

    Primeiro a capa do SYNC (polo_ativo/polo_passivo), depois o cabecalho da
    propria publicacao. Serve quando o cadastro nao sabe o polo e tambem para
    conferir o cadastro, que erra em ~22% dos casos (polo invertido na origem).
    A capa exige casamento forte (> 0.5): "Alves de Oliveira" e sobrenome comum
    e casou o cliente errado em 01/10/2026 (BB x Etabel).
    """
    cli = [c for c in (clientes or []) if c]
    if not cli:
        return None, None
    fontes = []
    if polo_ativo or polo_passivo:
        fontes.append((polo_ativo, polo_passivo, 'capa do SYNC', 0.51))
    a, b = partes_do_cabecalho(texto_bruto)
    if (a or b) and not (eh_iniciais(a) and eh_iniciais(b)):
        fontes.append((a, b, 'cabecalho da publicacao', limiar))
    for a, b, fonte, lim in fontes:
        sa = max((semelhanca(a, c) for c in cli), default=0.0) if a and not eh_iniciais(a) else 0.0
        sb = max((semelhanca(b, c) for c in cli), default=0.0) if b and not eh_iniciais(b) else 0.0
        if sa >= lim and sa > sb:
            return 'ativo', fonte
        if sb >= lim and sb > sa:
            return 'passivo', fonte
    return None, None


def parte_adversa_pelo_cabecalho(texto_bruto, nosso_polo, polo_ativo=None, polo_passivo=None):
    """Nome da outra parte, pela capa ou pelo cabecalho, dado o nosso polo."""
    if nosso_polo not in ('ativo', 'passivo'):
        return []
    nomes = []
    outro = polo_ativo if nosso_polo == 'passivo' else polo_passivo
    if outro:
        nomes.append(outro)
    a, b = partes_do_cabecalho(texto_bruto)
    outro = a if nosso_polo == 'passivo' else b
    if outro:
        nomes.append(outro)
    return nomes
# Prazo comum / ambas as partes.
# NAO incluir "as partes" nem "intimem-se as partes": e boilerplate de quase todo
# despacho e, depois de uma sentenca, significa que NOS tambem temos prazo (para
# recorrer) — nao que o prazo seja de outro. Tratar como prazo alheio foi erro real.
# "as embargadas" no plural, com ED cruzados, inclui o nosso lado (Ariosvaldo x
# Saraiva, 23/09/2026): o prazo e dos dois.
_AMBOS = [
    'ambas as partes', 'manifestem-se as partes', 'digam as partes',
    'prazo comum', 'prazo sucessivo', 'em prazo comum',
    'intimem-se as embargadas', 'intimem-se os embargados', 'as embargadas para',
    'os embargados para', 'intimem-se as partes para', 'manifestem-se os interessados',
]

# A copia: o DJEN manda uma publicacao por intimado, e o rodape diz para quem.
_RE_INTIMADOS = re.compile(r'intimado(?:\(s\))? ?/ ?citado(?:\(s\))?\s*-?\s*(.{3,200})$')
_RE_DESTINATARIO = re.compile(r'destinatario:?\s*(.{3,120}?)(?:\s+(?:intimacao|fica|de ordem|nos termos|processo|$))')


def destinatario_da_copia(texto_bruto):
    """Nomes a quem ESTA copia foi enderecada (rodape 'Intimado(s) / Citado(s) - X - Y'
    ou 'Destinatario: X'). Lista vazia quando o DJEN nao diz."""
    t = limpar(texto_bruto)
    nomes = []
    m = _RE_INTIMADOS.search(t)
    if m:
        nomes += [n.strip() for n in re.split(r'\s+-\s+', m.group(1)) if len(n.strip()) > 3]
    m = _RE_DESTINATARIO.search(t)
    if m:
        n = m.group(1).strip()
        if len(n) > 3 and not any(semelhanca(n, x) >= 0.5 for x in nomes):
            nomes.append(n)
    return nomes[:6]


def copia_de(destinatarios, clientes, adversos, limiar=0.34):
    """'cliente' | 'adverso' | None: de que lado e o destinatario desta copia.

    'cliente' quando algum nome casa com o nosso cliente. 'adverso' quando nenhum
    casa com cliente e (a) algum casa com adverso cadastrado, ou (b) conhecemos o
    cliente e o destinatario e claramente outra parte (o ADVBOX muitas vezes nao
    tem a parte contraria cadastrada: Fontanella e Transrima em 01/10/2026)."""
    if not destinatarios:
        return None
    cli = [c for c in (clientes or []) if c]
    adv = [a for a in (adversos or []) if a]
    bate_cli = any(semelhanca(d, c) >= limiar for d in destinatarios for c in cli)
    bate_adv = any(semelhanca(d, a) >= limiar for d in destinatarios for a in adv)
    if bate_cli:
        return 'cliente'
    if bate_adv:
        return 'adverso'
    return None

_MARCA_ADVERSA = 'PARTE CONTRARIA'

# Fallback fraco: o tipo do processo no ADVBOX denuncia o polo.
_TIPO_POLO = [
    ('DEFESA', 'passivo'), ('CONTESTA', 'passivo'),
    ('RECLAMACAO', 'ativo'), ('RECLAMATORIA', 'ativo'),
]

_RE_PASTA = re.compile(r'(https?://(?:drive|docs)\.google\.com/\S+)')


def sem_acento(t):
    if not t:
        return ''
    t = unicodedata.normalize('NFKD', str(t))
    return ''.join(c for c in t if not unicodedata.combining(c))


def limpar(texto):
    """Tira HTML, normaliza espacos e acentos. O DJEN vem com <br>, <a>, etc."""
    t = re.sub(r'<[^>]+>', ' ', texto or '')
    t = t.replace('&nbsp;', ' ').replace('&amp;', '&')
    t = sem_acento(t).lower()
    return re.sub(r'\s+', ' ', t).strip()


def _tokens(nome):
    n = sem_acento(nome or '').lower()
    n = re.sub(r'[^a-z0-9\s]', ' ', n)
    return {t for t in n.split() if len(t) > 2 and t not in _RUIDO}


def semelhanca(a, b):
    """Jaccard sobre tokens significativos. 0..1"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def separar_partes(proc):
    """Tres baldes, a partir do `origin` do ADVBOX:

    - `adversos`     — `origin == 'PARTE CONTRARIA'`. Marcacao explicita, vale mais
                       que qualquer semelhanca de nome.
    - `confirmados`  — `origin` preenchido com QUALQUER outra coisa. Para o cliente,
                       `origin` guarda a origem do lead (INDICACAO, BNI, ANUNCIO,
                       GOOGLE, ESCRITORIO...), entao ter origin e prova de que a
                       parte foi cadastrada COMO CLIENTE.
    - `indefinidos`  — `origin` vazio. Pode ser cliente cadastrado com pressa ou
                       adversario que ninguem marcou. Nao decide polo sozinho.

    Distinguir confirmado de indefinido e o que impede o sistema de apontar o
    proprio cliente como parte contraria (erro real: Sebastiao Caldeira e Rosemar).
    """
    confirmados, indefinidos, adversos = [], [], []
    for c in (proc or {}).get('customers') or []:
        if isinstance(c, dict):
            nome, origem = c.get('name'), sem_acento(c.get('origin') or '').upper().strip()
        else:
            nome, origem = str(c), ''
        if not nome:
            continue
        if _MARCA_ADVERSA in origem:
            adversos.append(nome)
        elif origem:
            confirmados.append(nome)
        else:
            indefinidos.append(nome)
    return confirmados, indefinidos, adversos


def separar_clientes(proc):
    """(nossos, adversos) — compatibilidade com a varredura original."""
    conf, indef, adv = separar_partes(proc)
    return conf + indef, adv


def _melhor_casamento(nomes, polo_ativo, polo_passivo):
    melhor = (None, None, 0.0)
    for nome in nomes:
        for polo, parte in (('ativo', polo_ativo), ('passivo', polo_passivo)):
            if not parte:
                continue
            s = semelhanca(nome, parte)
            if s > melhor[2]:
                melhor = (polo, nome, round(s, 2))
    return melhor


def detectar_polo(proc, polo_ativo, polo_passivo, limiar=0.34):
    """Em que polo o escritorio esta neste processo.

    `polo_ativo`/`polo_passivo` vem da carteira do SYNC. Devolve dict com
    polo ('ativo'|'passivo'|None), cliente casado, score e como descobriu.
    """
    confirmados, indefinidos, adversos = separar_partes(proc)
    nossos = confirmados + indefinidos
    # Quando o polo nao sai do casamento de nomes, o cliente exibido e o confirmado.
    padrao = confirmados[0] if confirmados else (nossos[0] if nossos else None)
    base = {'clientes': nossos, 'adversos': adversos,
            'confirmados': confirmados, 'indefinidos': indefinidos}

    # 1) Cliente CONFIRMADO (com origin) casando com uma das partes. Tem precedencia
    #    sobre quem esta sem origin, mesmo que o outro case melhor de nome — senao um
    #    adversario nao marcado, de nome identico, rouba o polo do cliente.
    melhor = _melhor_casamento(confirmados, polo_ativo, polo_passivo)
    if melhor[2] >= limiar:
        return {**base, 'polo': melhor[0], 'cliente': melhor[1], 'score': melhor[2],
                'como': 'cliente confirmado no ADVBOX casou com a parte do processo'}

    # 1b) Sem cliente confirmado: aceita o casamento de quem esta sem origin.
    melhor = _melhor_casamento(indefinidos, polo_ativo, polo_passivo)
    if melhor[2] >= limiar:
        return {**base, 'polo': melhor[0], 'cliente': melhor[1], 'score': melhor[2],
                'como': 'nome casou com a parte do processo (cadastro sem origin)'}

    # 2) A PARTE ADVERSA casa: se o adverso e o reclamado, nos somos o reclamante.
    for nome in adversos:
        for polo, parte in (('ativo', polo_ativo), ('passivo', polo_passivo)):
            if not parte:
                continue
            s = semelhanca(nome, parte)
            if s >= limiar:
                return {**base, 'polo': 'passivo' if polo == 'ativo' else 'ativo',
                        'cliente': padrao, 'score': round(s, 2),
                        'como': 'parte adversa casou (polo invertido)'}

    # 3) Fallback fraco: o tipo do processo no ADVBOX.
    tipo = sem_acento((proc or {}).get('type') or '').upper()
    for marca, polo in _TIPO_POLO:
        if marca in tipo:
            return {**base, 'polo': polo, 'cliente': padrao,
                    'score': 0.0, 'como': f"inferido do tipo do processo ({tipo.title()})"}

    return {**base, 'polo': None, 'cliente': padrao,
            'score': melhor[2], 'como': 'indefinido'}


def _ocorrencias(texto, expressoes):
    """Expressoes presentes no texto, casadas por palavra inteira.

    Substring pura confunde prefixo: "deve a re" casa dentro de "deve a
    requerente" e o mesmo despacho passa a apontar para os dois polos.
    """
    achadas = []
    for e in expressoes:
        if re.search(re.escape(e) + r'(?![a-z0-9])', texto):
            achadas.append(e)
    return achadas


def avaliar_titularidade(texto_limpo, nosso_polo=None):
    """De quem e o prazo desta intimacao.

    Devolve (veredito, confianca, evidencias) — veredito em
    NOSSO | CONTRARIA | AMBOS | INDEFINIDO.
    """
    ev = []
    hits_ativo = _ocorrencias(texto_limpo, _ATIVO)
    hits_passivo = _ocorrencias(texto_limpo, _PASSIVO)
    hits_ambos = _ocorrencias(texto_limpo, _AMBOS)

    if hits_ambos:
        ev.append(f'prazo comum: {hits_ambos[0]!r}')
        return 'AMBOS', 0.85, ev

    # As expressoes so viram "nosso" ou "deles" depois de saber o nosso polo.
    # Sem polo, um comando dirigido a um papel ("junte o exequente", "a parte
    # requerida") nao se resolve: fica INDEFINIDO para a tela pedir conferencia,
    # em vez de afirmar NOSSO ou CONTRARIA no chute (BB x Etabel, 01/10/2026).
    if not nosso_polo and (hits_ativo or hits_passivo):
        ev.append('comando dirigido a um papel (%r) e o cadastro nao diz o nosso polo'
                  % (hits_ativo or hits_passivo)[0])
        return 'INDEFINIDO', 0.4, ev
    if nosso_polo == 'passivo':
        hits_nosso, hits_deles = hits_passivo, hits_ativo
    else:
        hits_nosso, hits_deles = hits_ativo, hits_passivo

    if hits_nosso and not hits_deles:
        ev.append(f'abre prazo para nos: {hits_nosso[0]!r}')
        return 'NOSSO', (0.9 if nosso_polo else 0.7), ev
    if hits_deles and not hits_nosso:
        ev.append(f'abre prazo para a contraria: {hits_deles[0]!r}')
        return 'CONTRARIA', (0.85 if nosso_polo else 0.6), ev
    if hits_nosso and hits_deles:
        ev.append(f'cita os dois polos ({hits_nosso[0]!r} e {hits_deles[0]!r})')
        return 'INDEFINIDO', 0.35, ev

    # PADRAO: nosso. A comunicacao so chegou porque a NOSSA OAB e destinataria —
    # fomos intimados. A assimetria justifica: tratar como nosso um prazo alheio
    # custa uma tarefa a mais; tratar como alheio um prazo nosso perde prazo.
    ev.append('fomos os intimados e nada no texto passa o prazo para a outra parte')
    return 'NOSSO', (0.75 if nosso_polo else 0.65), ev


def extrair_pasta_drive(proc):
    """O link da pasta do caso mora em `notes` ('PASTA DO CASO: ...')."""
    m = _RE_PASTA.search((proc or {}).get('notes') or '')
    return m.group(1).rstrip('.,;') if m else None


def resolver_processo(proc_advbox, processo_sync, limiar=0.34):
    """Tudo o que se sabe do PROCESSO (nao muda de intimacao pra intimacao)."""
    p = processo_sync or {}
    ativo, passivo = p.get('polo_ativo'), p.get('polo_passivo')
    d = detectar_polo(proc_advbox, ativo, passivo, limiar)
    d['pasta_drive'] = extrair_pasta_drive(proc_advbox)
    d['polo_ativo'] = ativo
    d['polo_passivo'] = passivo
    d['avisos'] = []

    # Rede de seguranca do cadastro: quem esta do LADO DE LA nao e nosso, mesmo que
    # o ADVBOX tenha esquecido de marcar origin='PARTE CONTRARIA' (caso real: Souza
    # Cruz entrou como cliente porque o origin estava vazio).
    # So vale para quem esta SEM origin. Cliente confirmado nunca e rebaixado: se ele
    # casa com o polo oposto, o errado e o polo, nao o cadastro — e ai o caso vai pra
    # conferencia humana em vez de virar acusacao contra o proprio cliente.
    oposto = passivo if d['polo'] == 'ativo' else ativo if d['polo'] == 'passivo' else None
    if oposto:
        legitimos = []
        for nome in d['clientes']:
            suspeito = (nome in d.get('indefinidos', [])
                        and nome != d.get('cliente')
                        and semelhanca(nome, oposto) >= limiar)
            if suspeito:
                d['adversos'].append(nome)
                d['avisos'].append(
                    f"cadastro: '{nome}' esta sem origin no ADVBOX e casa com o polo "
                    f"oposto — marcar como PARTE CONTRARIA")
            else:
                legitimos.append(nome)
        d['clientes'] = legitimos
        for nome in d.get('confirmados', []):
            if nome != d.get('cliente') and semelhanca(nome, oposto) >= limiar:
                d['avisos'].append(
                    f"conferir: '{nome}' e cliente no ADVBOX mas casa com o polo "
                    f"oposto ao que identifiquei — polo pode estar errado")

    # A parte adversa que interessa e a que ocupa o polo oposto ao nosso.
    d['parte_adversa'] = None
    if d['adversos']:
        if oposto:
            d['parte_adversa'] = max(d['adversos'], key=lambda n: semelhanca(n, oposto))
        else:
            d['parte_adversa'] = d['adversos'][0]
    elif oposto:
        d['parte_adversa'] = oposto

    d['tutelamos'] = bool(d['clientes'])
    if not d['tutelamos']:
        d['avisos'].append('nenhuma parte tutelada identificada no ADVBOX — conferir cadastro')
    return d


def resolver_intimacao(base, intimacao):
    """Junta o que se sabe do processo com o que o TEXTO desta intimacao diz."""
    b = dict(base or {})
    texto = limpar(intimacao.get('texto') or intimacao.get('resumo') or '')
    veredito, conf, ev = avaliar_titularidade(texto, b.get('polo'))
    ev.extend(b.get('avisos') or [])
    b['titularidade'] = veredito
    b['titularidade_confianca'] = round(conf, 2)
    b['titularidade_evidencias'] = ev
    b['nosso_polo'] = b.get('polo')
    return b


def como_texto(d):
    """Uma linha legivel pra tela e pro relatorio."""
    if not d:
        return ''
    cli = ' + '.join(d.get('clientes') or []) or '?'
    adv = d.get('parte_adversa') or '?'
    polo = d.get('polo') or 'polo indefinido'
    return f'{cli} × {adv} ({polo})'


if __name__ == '__main__':   # bateria rapida: python -m app.controladoria_motor.titularidade
    casos = [
        ('reclamante nosso, prazo do autor', 'ativo',
         'Manifeste-se o reclamante, no prazo de 8 dias, sobre a defesa.', 'NOSSO'),
        ('reclamante nosso, prazo da re', 'ativo',
         'Manifeste-se a reclamada, no prazo de 5 dias, sobre os calculos.', 'CONTRARIA'),
        ('defendemos a re, prazo da re = nosso', 'passivo',
         'Manifeste-se a reclamada, no prazo de 5 dias, sobre os calculos.', 'NOSSO'),
        ('defendemos a re, prazo do autor', 'passivo',
         'Manifeste-se o reclamante em 8 dias.', 'CONTRARIA'),
        ('prazo comum', 'ativo', 'Manifestem-se as partes, em prazo comum de 5 dias.', 'AMBOS'),
        ('boilerplate nao e prazo alheio', 'ativo',
         'Intimem-se as partes da sentenca proferida nos autos.', 'NOSSO'),
        ('sem polo conhecido cai no padrao', None,
         'Tomem ciencia da decisao proferida.', 'NOSSO'),
    ]
    ok = 0
    for nome, polo, texto, esperado in casos:
        got, conf, ev = avaliar_titularidade(limpar(texto), polo)
        bom = got == esperado
        ok += bom
        print('%s  %-38s polo=%-8s -> %-10s (esperado %s) conf=%.2f' % (
            'OK  ' if bom else 'FALHA', nome, polo, got, esperado, conf))
        print('        %s' % (ev[0] if ev else ''))
    print('\n%d/%d' % (ok, len(casos)))
