""
import json
import re

import re as _re

_SUFIXO_CADASTRO = _re.compile(r'(_[A-Z]+)+\s*$')


def nome_limpo(nome):
    ""
    if not nome:
        return nome
    limpo = _SUFIXO_CADASTRO.sub('', str(nome).strip()).strip(' -_')
    return limpo or str(nome)



CNJ = re.compile(r'^(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})$')

SEGMENTOS = {
    '1': 'Supremo Tribunal Federal',
    '3': 'Superior Tribunal de Justica',
    '4': 'Justica Federal',
    '5': 'Justica do Trabalho',
    '6': 'Justica Eleitoral',
    '8': 'Justica Estadual',
}

TJS = {
    '01': ('TJAC', 'AC'), '02': ('TJAL', 'AL'), '03': ('TJAP', 'AP'), '04': ('TJAM', 'AM'),
    '05': ('TJBA', 'BA'), '06': ('TJCE', 'CE'), '07': ('TJDFT', 'DF'), '08': ('TJES', 'ES'),
    '09': ('TJGO', 'GO'), '10': ('TJMA', 'MA'), '11': ('TJMT', 'MT'), '12': ('TJMS', 'MS'),
    '13': ('TJMG', 'MG'), '14': ('TJPA', 'PA'), '15': ('TJPB', 'PB'), '16': ('TJPR', 'PR'),
    '17': ('TJPE', 'PE'), '18': ('TJPI', 'PI'), '19': ('TJRJ', 'RJ'), '20': ('TJRN', 'RN'),
    '21': ('TJRS', 'RS'), '22': ('TJRO', 'RO'), '23': ('TJRR', 'RR'), '24': ('TJSC', 'SC'),
    '25': ('TJSE', 'SE'), '26': ('TJSP', 'SP'), '27': ('TJTO', 'TO'),
}


def _tokens(v):
    ""
    import unicodedata
    v = unicodedata.normalize('NFKD', v or '')
    v = ''.join(c for c in v if not unicodedata.combining(c))
    v = re.sub(r'[^A-Za-z0-9 ]', ' ', v).upper()
    return {t for t in v.split() if len(t) >= 4 and t not in _ENCHIMENTO}


_ENCHIMENTO = {'LTDA', 'EIRELI', 'COMERCIO', 'INDUSTRIA', 'TRANSPORTES', 'SERVICOS',
               'EMPRESA', 'SOCIEDADE', 'IMPLEMENTOS', 'EMBALAGENS', 'LOGISTICA'}


def _nosso_lado(nossos, pa_txt, pp_txt):
    ""
    if not (pa_txt or pp_txt):
        return None
    meus = set()
    for n in nossos:
        meus |= _tokens(n)
    if not meus:
        return None
    em_a = bool(meus & _tokens(pa_txt))
    em_p = bool(meus & _tokens(pp_txt))
    if em_p and not em_a:
        return 'passivo'
    if em_a and not em_p:
        return 'ativo'
    return None


def polo_do_tipo(tipo):
    ""
    import unicodedata
    t = unicodedata.normalize('NFKD', tipo or '')
    t = ''.join(c for c in t if not unicodedata.combining(c)).upper()
    for marca in ('DEFESA', 'RECLAMADA', 'REQUERIDA', ' REU', 'EXECUTAD', 'IMPETRAD'):
        if marca in t:
            return 'passivo'
    for marca in ('RECLAMANTE', 'REQUERENTE', 'AUTOR', 'EXEQUENTE', 'IMPETRANTE'):
        if marca in t:
            return 'ativo'
    return None


def partes(capa: dict, titularidade_clientes=None, titularidade_adversos=None,
           nosso_polo=None) -> dict:
    ""
    capa = capa or {}

    def _lista(valor, extra):
        ""
        nomes = []
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = []
        if isinstance(extra, list):
            nomes = [nome_limpo(str(x).strip()) for x in extra if str(x).strip()]
        if not nomes and valor:
            bruto = re.split(r';|\se\s(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ])', str(valor))
            nomes = [nome_limpo(b.strip()) for b in bruto if b.strip()]
        return nomes

    pa_txt = capa.get('polo_ativo') or ''
    pp_txt = capa.get('polo_passivo') or ''
    nossos = _lista('', titularidade_clientes)
    deles = _lista('', titularidade_adversos)

    conhecido = None
    if not nossos and not deles:
        ativo = _lista(pa_txt, None)
        passivo = _lista(pp_txt, None)
        conhecido = 'tribunal'
    else:
        conhecido = _nosso_lado(nossos, pa_txt, pp_txt) or nosso_polo
        lado = conhecido or 'ativo'
        if lado == 'passivo':
            ativo, passivo = deles, nossos
        else:
            ativo, passivo = nossos, deles
        if not ativo and pa_txt:
            ativo = _lista(pa_txt, None)
        if not passivo and pp_txt:
            passivo = _lista(pp_txt, None)

    def _resumo(nomes, rotulo):
        if not nomes:
            return {'rotulo': rotulo, 'primeiro': '', 'resto': 0, 'todos': []}
        return {'rotulo': rotulo, 'primeiro': nomes[0],
                'resto': max(0, len(nomes) - 1), 'todos': nomes}

    if (nossos or deles) and not conhecido:
        r_ativo, r_passivo = 'Do seu lado', 'Do outro lado'
    else:
        r_ativo, r_passivo = 'Parte requerente', 'Parte requerida'
    return {
        'requerente': _resumo(ativo, r_ativo),
        'requerida': _resumo(passivo, r_passivo),
    }


def _e_segundo_grau(orgao: str, classe: str) -> bool:
    ""
    if orgao == '0000':
        return True
    import unicodedata
    c = unicodedata.normalize('NFKD', classe or '')
    c = ''.join(ch for ch in c if not unicodedata.combining(ch)).upper()
    if any(k in c for k in ('CUMPRIMENTO', 'EXECUCAO', 'LIQUIDACAO')):
        return False
    return any(k in c for k in ('RECURSO', 'AGRAVO', 'APELACAO',
                                'EMBARGOS DE DECLARACAO'))


def tribunal_do_numero(numero: str, classe: str = '') -> dict:
    ""
    n = (numero or '').strip()
    m = CNJ.match(n)
    if not m:
        return {'valido': False, 'numero': n, 'sigla': '', 'link': None, 'segmento': ''}

    seq, dv, ano, seg, tr, orgao = m.groups()
    segmento = SEGMENTOS.get(seg, '')
    sigla, link = '', None
    segundo = _e_segundo_grau(orgao, classe)

    if seg == '8':
        sigla = TJS.get(tr, ('', ''))[0]
        if tr == '26' and seq.startswith('4') and int(ano) >= 2025:
            if segundo:
                link = ('https://eproc2g-consulta.tjsp.jus.br/eproc/externo_controlador.php'
                        '?acao=tjsp@consulta_unificada_publica/consultar'
                        f'&num_processo={n}&instancia=TJSP')
            else:
                link = ('https://eproc-consulta.tjsp.jus.br/consulta_1g/externo_controlador.php'
                        '?acao=tjsp@consulta_unificada_publica/consultar'
                        f'&num_processo={n}&instancia=SP')
        elif tr == '26':
            if segundo:
                link = ('https://esaj.tjsp.jus.br/cposg/search.do?conversationId='
                        '&paginaConsulta=1&cbPesquisa=NUMPROC&tipoNuProcesso=UNIFICADO'
                        f'&numeroDigitoAnoUnificado={seq}-{dv}.{ano}'
                        f'&foroNumeroUnificado={orgao}'
                        f'&dePesquisaNuUnificado={n}&dePesquisa=&uuidCaptcha=')
            else:
                link = ('https://esaj.tjsp.jus.br/cpopg/search.do?conversationId='
                        '&cbPesquisa=NUMPROC&dadosConsulta.valorConsultaNuUnificado=' + n +
                        '&dadosConsulta.tipoNuProcesso=UNIFICADO'
                        '&dadosConsulta.valorConsulta=&foroNumeroUnificado=' + orgao)
    elif seg == '5':
        regiao = str(int(tr))
        sigla = f'TRT{regiao}'
        grau = '2' if segundo else '1'
        link = f'https://pje.trt{regiao}.jus.br/consultaprocessual/detalhe-processo/{n}/{grau}'
    elif seg == '4':
        sigla = f'TRF{int(tr)}'
    elif seg in ('1', '3'):
        sigla = 'STF' if seg == '1' else 'STJ'

    return {'valido': True, 'numero': n, 'sigla': sigla,
            'segmento': segmento, 'link': link}


def polo_do_cliente(nome_cliente: str, capa: dict, clientes_titularidade=None) -> str:
    ""
    def _norm(v):
        v = re.sub(r'[^A-Za-zÀ-ÿ ]', ' ', str(v or '')).upper()
        return re.sub(r'\s+', ' ', v).strip()

    alvo = _norm(nome_cliente)
    if not alvo:
        return ''
    primeiro_sobrenome = ' '.join(alvo.split()[:2])

    ativo = _norm((capa or {}).get('polo_ativo'))
    passivo = _norm((capa or {}).get('polo_passivo'))

    if primeiro_sobrenome and primeiro_sobrenome in ativo:
        return 'requerente'
    if primeiro_sobrenome and primeiro_sobrenome in passivo:
        return 'requerida'
    return ''
