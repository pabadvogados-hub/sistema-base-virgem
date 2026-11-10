""
import time


def _pi_nome(v):
    ""
    from . import processo_info
    return processo_info.nome_limpo(v)

DDL = """CREATE TABLE IF NOT EXISTS portal_painel_ficha (
    numero TEXT PRIMARY KEY,
    cliente_id INTEGER,
    valor_causa REAL,          -- sobrepoe o do sistema quando informado
    valor_pago REAL,           -- o que a EMPRESA desembolsou. So o escritorio sabe.
    parte_contraria TEXT,      -- nome curto para o grafico ("Fulano de Tal")
    situacao TEXT,             -- "Acordo quitado - arquivado"
    categoria TEXT,            -- ver CATEGORIAS: como o processo entra na carteira
    encerrado_em TEXT,         -- AAAA-MM-DD, para a curva acumulada
    observacao TEXT,
    parecer_link TEXT,         -- link do parecer terminativo no Drive
    parecer_nome TEXT,
    atualizado_em REAL,
    atualizado_por TEXT)"""

DDL_DADO = """CREATE TABLE IF NOT EXISTS portal_empresa_dado (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER, chave TEXT, pergunta TEXT, ajuda TEXT,
    valor REAL, texto TEXT, unidade TEXT,
    origem TEXT, quem TEXT, criado_em REAL)"""

DDL_CONSULT = """CREATE TABLE IF NOT EXISTS portal_painel_consultoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER, titulo TEXT, texto TEXT, ordem INTEGER,
    criado_em REAL)"""


CATEGORIAS = {
    'risco':        'Sob tutela ativa',
    'consolidando': 'Ganho em consolidação',
    'a_receber':    'Valor a receber',
}
CATEGORIA_PADRAO = 'risco'


def criar_tabelas(abrir):
    with abrir() as con:
        con.execute(DDL)
        con.execute(DDL_CONSULT)
        con.execute(DDL_DADO)
        for coluna in ('categoria TEXT', 'parecer_link TEXT', 'parecer_nome TEXT',
                       'custas_periciais REAL', 'honorarios REAL', 'outras_despesas REAL',
                       'valor_esperado REAL'):
            try:
                con.execute('ALTER TABLE portal_painel_ficha ADD COLUMN ' + coluna)
            except Exception:
                pass
        con.commit()


def dados_empresa(abrir, cliente_id) -> dict:
    ""
    with abrir() as con:
        con.execute(DDL_DADO)
        linhas = con.execute(
            'SELECT chave, pergunta, ajuda, valor, texto, unidade, origem, quem, criado_em '
            'FROM portal_empresa_dado WHERE cliente_id=? ORDER BY id', (cliente_id,)).fetchall()
    atual = {}
    for r in linhas:
        atual[r['chave']] = {
            'chave': r['chave'], 'pergunta': r['pergunta'] or '', 'ajuda': r['ajuda'] or '',
            'valor': r['valor'], 'texto': r['texto'] or '', 'unidade': r['unidade'] or '',
            'origem': r['origem'] or 'estimativa', 'quem': r['quem'] or '',
            'quando': time.strftime('%d/%m/%Y', time.localtime(r['criado_em'] or 0)) if r['criado_em'] else '',
        }
    return atual


def gravar_dado_empresa(abrir, cliente_id, chave, valor=None, texto='', quem='') -> bool:
    ""
    with abrir() as con:
        con.execute(DDL_DADO)
        base = con.execute('SELECT pergunta, ajuda, unidade FROM portal_empresa_dado '
                           'WHERE cliente_id=? AND chave=? ORDER BY id LIMIT 1',
                           (cliente_id, chave)).fetchone()
        if not base:
            return False
        con.execute('INSERT INTO portal_empresa_dado(cliente_id,chave,pergunta,ajuda,valor,texto,'
                    'unidade,origem,quem,criado_em) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (cliente_id, chave, base['pergunta'], base['ajuda'],
                     float(valor) if valor not in (None, '') else None, (texto or '')[:200],
                     base['unidade'], 'empresa', (quem or '')[:80], time.time()))
        con.commit()
    return True


def historico_dado(abrir, cliente_id, chave) -> list:
    with abrir() as con:
        con.execute(DDL_DADO)
        return [{'valor': r['valor'], 'texto': r['texto'] or '', 'origem': r['origem'] or '',
                 'quem': r['quem'] or '',
                 'quando': time.strftime('%d/%m/%Y', time.localtime(r['criado_em'] or 0)) if r['criado_em'] else ''}
                for r in con.execute('SELECT valor, texto, origem, quem, criado_em FROM portal_empresa_dado '
                                     'WHERE cliente_id=? AND chave=? ORDER BY id DESC', (cliente_id, chave))]


def custo(encerrados, ativos, fi, dados) -> dict:
    ""
    linhas = []
    for e in list(encerrados) + list(ativos):
        f = fi.get(e['numero'], {})
        desp = float(f.get('custas_periciais') or 0)
        hon = float(f.get('honorarios') or 0)
        out = float(f.get('outras_despesas') or 0)
        if not (desp or hon or out):
            continue
        pago = e.get('valor_pago')
        if pago is None:
            pago = f.get('valor_pago')
        pago = float(pago or 0)
        total = pago + desp + hon + out
        linhas.append({'numero': e['numero'], 'parte': e['parte'], 'situacao': e['situacao'],
                       'encerrado_em': e.get('encerrado_em') or '', 'causa': e['valor_causa'],
                       'pago': pago, 'despesas': desp, 'honorarios': hon,
                       'outras': out, 'total': round(total, 2),
                       'por_pedido': round(total / e['valor_causa'] * 100, 1) if e['valor_causa'] else 0})
    if not linhas:
        return None
    pedido = sum(l['causa'] for l in linhas)
    pago = sum(l['pago'] for l in linhas)
    despesas = sum(l['despesas'] for l in linhas)
    honor = sum(l['honorarios'] for l in linhas)
    outras = sum(l['outras'] for l in linhas)
    total = pago + despesas + honor + outras
    p = (lambda v: round(v / pedido * 100, 2) if pedido else 0)
    linhas.sort(key=lambda x: x['total'], reverse=True)
    bloco = {
        'acoes': len(linhas), 'pedido': round(pedido, 2), 'pago': round(pago, 2),
        'despesas': round(despesas, 2), 'honorarios': round(honor, 2), 'outras': round(outras, 2),
        'total': round(total, 2), 'medio': round(total / len(linhas), 2) if linhas else 0,
        'economia': round(pedido - total, 2),
        'por100': {'total': p(total), 'pago': p(pago), 'despesas': p(despesas),
                   'honorarios': p(honor), 'outras': p(outras)},
        'linhas': linhas,
        'provisao': round(sum(float((fi.get(a['numero']) or {}).get('valor_esperado') or 0)
                              for a in ativos), 2),
        'em_curso': round(sum(a['valor_causa'] for a in ativos if a['categoria'] == 'risco'), 2),
    }
    por_pessoa = (dados.get('passivo_por_pessoa') or {}).get('valor')
    pessoas = (dados.get('expostos_lavagem') or {}).get('valor')
    if por_pessoa and pessoas:
        d = dados['expostos_lavagem']
        bloco['insalubridade'] = {
            'passivo': round(float(por_pessoa) * float(pessoas), 2),
            'pessoas': int(float(pessoas)), 'origem': d['origem'],
            'quem': d['quem'], 'quando': d['quando'],
        }
    return bloco


def gravar_ficha(abrir, numero, cliente_id, **campos):
    ""
    permitidos = ('valor_causa', 'valor_pago', 'parte_contraria', 'situacao',
                  'categoria', 'encerrado_em', 'observacao', 'atualizado_por',
                  'parecer_link', 'parecer_nome',
                  'custas_periciais', 'honorarios', 'outras_despesas',
                  'valor_esperado')
    dados = {k: v for k, v in campos.items() if k in permitidos}
    with abrir() as con:
        con.execute(DDL)
        existe = con.execute('SELECT numero FROM portal_painel_ficha WHERE numero=?',
                             (numero,)).fetchone()
        if existe:
            if dados:
                sets = ', '.join('%s=?' % k for k in dados)
                con.execute('UPDATE portal_painel_ficha SET %s, atualizado_em=? '
                            'WHERE numero=?' % sets,
                            list(dados.values()) + [time.time(), numero])
        else:
            cols = ['numero', 'cliente_id', 'atualizado_em'] + list(dados)
            vals = [numero, cliente_id, time.time()] + list(dados.values())
            con.execute('INSERT INTO portal_painel_ficha(%s) VALUES(%s)'
                        % (','.join(cols), ','.join('?' * len(cols))), vals)
        con.commit()


def fichas(abrir, cliente_id):
    with abrir() as con:
        con.execute(DDL)
        return {r['numero']: dict(r) for r in con.execute(
            'SELECT * FROM portal_painel_ficha WHERE cliente_id=?', (cliente_id,))}


def e_pj_consultoria(abrir, cliente) -> bool:
    ""
    doc = ''.join(c for c in (cliente.get('documento') or cliente.get('cpf') or '')
                  if c.isdigit())
    if len(doc) != 14:
        return False
    with abrir() as con:
        n = con.execute(
            "SELECT COUNT(*) FROM jur_processos p "
            "JOIN jur_processo_partes pt ON pt.processo_id = p.id "
            "WHERE pt.cliente_id = ? AND UPPER(COALESCE(p.tipo,'') || ' ' || "
            "COALESCE(p.area,'')) LIKE '%CONSULTORIA%'",
            (cliente['id'],)).fetchone()[0]
    return n > 0


def _colunas_consultoria(con):
    ""
    con.execute(DDL_CONSULT)
    cols = {r[1] for r in con.execute('PRAGMA table_info(portal_painel_consultoria)')}
    if 'desde' not in cols:
        con.execute('ALTER TABLE portal_painel_consultoria ADD COLUMN desde TEXT')
    if 'tipo' not in cols:
        con.execute('ALTER TABLE portal_painel_consultoria ADD COLUMN tipo TEXT')


def consultoria(abrir, cliente_id):
    with abrir() as con:
        _colunas_consultoria(con)
        return [dict(r) for r in con.execute(
            "SELECT titulo, texto, COALESCE(desde, '') AS desde, COALESCE(tipo, 'frente') AS tipo "
            'FROM portal_painel_consultoria WHERE cliente_id=? ORDER BY ordem, id', (cliente_id,))]


def gravar_consultoria(abrir, cliente_id, itens):
    ""
    with abrir() as con:
        _colunas_consultoria(con)
        con.execute('DELETE FROM portal_painel_consultoria WHERE cliente_id=?',
                    (cliente_id,))
        for i, it in enumerate(itens):
            con.execute('INSERT INTO portal_painel_consultoria'
                        '(cliente_id,titulo,texto,ordem,criado_em,desde,tipo) VALUES(?,?,?,?,?,?,?)',
                        (cliente_id, it.get('titulo', ''), it.get('texto', ''),
                         i, time.time(), it.get('desde', ''), it.get('tipo') or 'frente'))
        con.commit()


def _titulo_consultoria(tipo: str) -> str:
    ""
    t = (tipo or '').strip()
    if not t:
        return 'Consultoria'
    MIUDAS = {'de', 'da', 'do', 'e', 'em', 'para', 'a', 'o'}
    ps = t.lower().split()
    return ' '.join(p if (i and p in MIUDAS) else p.capitalize()
                    for i, p in enumerate(ps))


def _limpar_nota(nota: str) -> str:
    ""
    import re as _re
    if not nota:
        return ''
    texto = _re.sub(r'https?://\S+', '', str(nota))
    linhas = []
    for l in texto.split('\n'):
        l = l.strip(' :-')
        if not l:
            continue
        if _re.match(r'^(PROCESSO CRIADO|PASTA DO CASO|EXCLUSIVO PARA)', l, _re.I):
            continue
        linhas.append(l)
    return ' '.join(linhas)[:260]


def _nome_do_caso(notas: str, pasta: str) -> str:
    ""
    import re as _re
    nome = ''
    p = str(pasta or '')
    if _re.match(r'^\s*CONSULTORIA\s+\S', p, _re.I) and not _re.search(r'https?:', p, _re.I):
        nome = _re.sub(r'^\s*CONSULTORIA\s*', '', p, flags=_re.I)
    if not nome and _re.search(r'\bCASO\s', str(notas or ''), _re.I):
        m = _re.search(r'\bCASO\s+(.+?)\s*(?:\(|\s-\s|\d{2}/\d{2}/\d{4}|\.\s|$)', str(notas or ''), _re.I)
        nome = m.group(1) if m else ''
    nome = nome.strip(' -.')
    if not nome:
        return ''
    MIUDAS = {'de', 'da', 'do', 'das', 'dos', 'e'}
    return 'Caso ' + ' '.join(w.lower() if (i and w.lower() in MIUDAS) else w.capitalize()
                              for i, w in enumerate(nome.split()))


def consultoria_do_advbox(abrir, cliente_id: int) -> list:
    ""
    import re as _re
    saida = []
    try:
        with abrir() as con:
            linhas = con.execute(
                'SELECT p.tipo, p.data_processo, p.notas, p.encerrado, p.pasta '
                'FROM jur_processos p '
                'JOIN jur_processo_partes pt ON pt.processo_id = p.id '
                "WHERE pt.cliente_id = ? AND UPPER(p.area) = 'CONSULTORIA' "
                'ORDER BY p.data_processo DESC', (cliente_id,)).fetchall()
    except Exception:
        return saida
    for r in linhas:
        d = (r[1] or '')[:10]
        quando = '%s/%s/%s' % (d[8:10], d[5:7], d[:4]) if len(d) == 10 else ''
        if not quando:
            m = _re.search(r'\b(\d{2}/\d{2}/\d{4})\b', str(r[2] or ''))
            quando = m.group(1) if m else ''
        caso = _nome_do_caso(r[2], r[4])
        if caso:
            saida.append({'titulo': caso, 'texto': '', 'desde': quando,
                          'tipo': 'caso', 'fonte': 'advbox'})
            continue
        if r[3]:
            continue
        saida.append({'titulo': _titulo_consultoria(r[0]),
                      'texto': _limpar_nota(r[2]),
                      'desde': quando,
                      'tipo': 'frente',
                      'fonte': 'advbox'})
    return saida


def _palavras_fortes(texto: str) -> set:
    ""
    import re as _re
    import unicodedata
    v = unicodedata.normalize('NFKD', texto or '')
    v = ''.join(c for c in v if not unicodedata.combining(c)).upper()
    COMUNS = {'CONSULTORIA', 'CONTRATO', 'ANALISE', 'REVISAO', 'EMPRESA',
              'DOCUMENTO', 'JURIDICA', 'PROCESSO', 'CLIENTE'}
    return {p for p in _re.findall(r'[A-Z]{5,}', v) if p not in COMUNS}


def _consultoria_junta(abrir, cliente_id: int) -> list:
    ""
    do_advbox = consultoria_do_advbox(abrir, cliente_id)
    itens = [i for i in do_advbox if i.get('texto')]
    assuntos = set()
    for i in itens:
        assuntos |= _palavras_fortes(i['titulo'] + ' ' + i.get('texto', ''))

    for c in consultoria(abrir, cliente_id):
        palavras = _palavras_fortes((c.get('titulo') or '') + ' ' + (c.get('texto') or ''))
        if len(palavras & assuntos) >= 2:
            continue
        c = dict(c)
        c.setdefault('desde', '')
        c['fonte'] = 'ficha'
        assuntos |= palavras
        itens.append(c)
    return itens


def _mes(iso):
    ""
    MESES = ('jan', 'fev', 'mar', 'abr', 'mai', 'jun',
             'jul', 'ago', 'set', 'out', 'nov', 'dez')
    try:
        a, m, _d = (iso or '')[:10].split('-')
        return '%s/%s' % (MESES[int(m) - 1], a[2:])
    except Exception:
        return ''



def _sem_acento(v):
    import unicodedata
    v = unicodedata.normalize('NFKD', v or '')
    return ''.join(c for c in v if not unicodedata.combining(c)).upper().strip()


_NAO_E_PARTE = ('TRIBUNAL', 'CADEIRA', 'MINISTERIO PUBLICO', 'JUIZO', 'VARA DO',
                'DESEMBARGADOR', 'RELATOR', 'CAMARA', 'TURMA')


def _titulo(nome: str) -> str:
    ""
    miudas = {'de', 'da', 'do', 'das', 'dos', 'e'}
    partes = (nome or '').strip().split()
    saida = []
    for i, p in enumerate(partes):
        b = p.lower()
        saida.append(b if (i and b in miudas) else b.capitalize())
    return ' '.join(saida)


def outra_parte(processo: dict, cliente_nome: str, ficha: dict = None) -> str:
    ""
    f = ficha or {}
    if f.get('parte_contraria'):
        return f['parte_contraria']

    eu = _sem_acento(cliente_nome)[:18]

    def serve(v):
        n = _sem_acento(v)
        if not n or len(n) < 4:
            return False
        if any(p in n for p in _NAO_E_PARTE):
            return False
        return not (eu and (eu in n or n[:18] == eu))

    for v in (processo.get('adversos') or []):
        if serve(v):
            return _titulo(str(v))
    for campo in ('polo_ativo', 'polo_passivo'):
        v = processo.get(campo)
        if serve(v):
            return _titulo(str(v))
    return ''


def montar(abrir, cliente, processos, jornada_fn, espelho_fn=None):
    ""
    fi = fichas(abrir, cliente['id'])

    with abrir() as con:
        causas = {r[0]: r[1] for r in con.execute(
            'SELECT numero, valor_expectativa FROM jur_processos WHERE numero IS NOT NULL')}

    encerrados, ativos, pendentes = [], [], []
    for p in processos:
        f = fi.get(p['numero'], {})
        j = jornada_fn(p['etapa'], p['grupo'], p['classe'], p['baixado'])
        causa = f.get('valor_causa') or causas.get(p['numero']) or 0
        item = {
            'numero': p['numero'],
            'parte': outra_parte(p, cliente['nome'], f),
            'valor_causa': float(causa or 0),
            'situacao': f.get('situacao') or j['titulo'],
            'fase': j['titulo'],
            'trilha': j['trilha'],
            'encerrado_em': f.get('encerrado_em') or '',
            'observacao': f.get('observacao') or '',
            'categoria': f.get('categoria') or CATEGORIA_PADRAO,
        }
        if j['trilha'] == 'ENCERRADO':
            pago = f.get('valor_pago')
            if pago is None:
                pendentes.append(item)
                continue
            item['valor_pago'] = float(pago)
            item['economia'] = item['valor_causa'] - item['valor_pago']
            item['reducao'] = (item['economia'] / item['valor_causa'] * 100
                               if item['valor_causa'] else 0)
            encerrados.append(item)
        else:
            ativos.append(item)

    encerrados.sort(key=lambda x: x['valor_causa'], reverse=True)
    ativos.sort(key=lambda x: x['valor_causa'], reverse=True)

    reclamado = sum(e['valor_causa'] for e in encerrados)
    pago = sum(e['valor_pago'] for e in encerrados)
    economia = reclamado - pago
    reducao = (economia / reclamado * 100) if reclamado else 0

    ativo = sum(a['valor_causa'] for a in ativos if a['categoria'] == 'risco')
    consolidando = sum(a['valor_causa'] for a in ativos if a['categoria'] == 'consolidando')
    a_receber = sum(a['valor_causa'] for a in ativos if a['categoria'] == 'a_receber')

    carteira = [
        {'rotulo': 'Encerrado com êxito', 'valor': round(reclamado, 2), 'cor': 'aqua',
         'nota': '%d processo(s) concluído(s)' % len(encerrados)},
        {'rotulo': CATEGORIAS['consolidando'], 'valor': round(consolidando, 2),
         'cor': 'gold', 'nota': 'sentença favorável, em liquidação'},
        {'rotulo': CATEGORIAS['risco'], 'valor': round(ativo, 2), 'cor': 'blue',
         'nota': '%d processo(s) em curso' % sum(1 for a in ativos if a['categoria'] == 'risco')},
    ]
    carteira = [c for c in carteira if c['valor'] > 0]

    com_data = sorted([e for e in encerrados if e['encerrado_em']],
                      key=lambda x: x['encerrado_em'])
    acumulado, soma = [], 0.0
    for e in com_data:
        soma += e['economia']
        acumulado.append({'mes': _mes(e['encerrado_em']), 'valor': round(soma, 2),
                          'marco': e['parte'] or e['numero']})

    todos = []
    _so_empresa = len(''.join(c for c in str(cliente.get('cpf')
                                             or cliente.get('documento') or '')
                              if c.isdigit())) == 14
    for p in processos:
        f = fi.get(p['numero'], {})
        if _so_empresa and p['baixado'] and p['numero'] not in fi:
            continue
        j = jornada_fn(p['etapa'], p['grupo'], p['classe'], p['baixado'])
        from . import processo_info as _pi
        j_limpa = {k: v for k, v in j.items() if k != 'etapa_interna'}
        info = _pi.tribunal_do_numero(p['numero'], p.get('classe') or '')
        todos.append({
            'numero': p['numero'],
            'parte': outra_parte(p, cliente['nome'], f),
            'fase': j['titulo'],
            'trilha': j['trilha'],
            'ativo': j['trilha'] != 'ENCERRADO',
            'tem_pasta': bool(p.get('pasta_drive')),
            'jornada': j_limpa,
            'partes': _pi.partes({'polo_ativo': p.get('polo_ativo'),
                                  'polo_passivo': p.get('polo_passivo')},
                                 p.get('clientes'), p.get('adversos'),
                                 p.get('nosso_polo')),
            'link_tribunal': info['link'],
            'segredo': bool(p.get('segredo')),
        })
    todos.sort(key=lambda x: (not x['ativo'], x['parte'] or x['numero']))

    processual = {
        'total': len(todos),
        'ativos': sum(1 for t in todos if t['ativo']),
        'encerrados': sum(1 for t in todos if not t['ativo']),
    }

    compromissos = []

    _dados_emp = dados_empresa(abrir, cliente['id'])
    _custo_bloco = custo(encerrados, ativos, fi, _dados_emp)

    return {
        'empresa': _pi_nome(cliente['nome']),
        'processual': processual,
        'processos': todos,
        'agenda': compromissos,
        'resumo': {
            'economia': round(economia, 2),
            'reclamado': round(reclamado, 2),
            'pago': round(pago, 2),
            'reducao': round(reducao, 1),
            'encerrados': len(encerrados),
            'sem_desembolso': sum(1 for e in encerrados if e['valor_pago'] == 0),
            'ativo': round(ativo, 2),
            'consolidando': round(consolidando, 2),
            'a_receber': round(a_receber, 2),
            'em_curso': sum(1 for a in ativos if a['categoria'] == 'risco'),
        },
        'carteira': carteira,
        'categorias': CATEGORIAS,
        'custo': _custo_bloco,
        'confirmar': [d for d in _dados_emp.values() if d['pergunta']],
        'acumulado': acumulado,
        'encerrados': encerrados,
        'ativos': ativos,
        'consultoria': consultoria(abrir, cliente['id']),
        'pendentes_internos': [p['numero'] for p in pendentes],
        'gerado_em': time.strftime('%d/%m/%Y'),
    }
