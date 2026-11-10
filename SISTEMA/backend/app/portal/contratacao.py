""
import re
import time

DDL = """CREATE TABLE IF NOT EXISTS portal_contratacao (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER, contrato TEXT,
    drive_id TEXT, nome TEXT, tipo TEXT, mime TEXT,
    ordem INTEGER, atualizado_em REAL,
    UNIQUE(cliente_id, drive_id))"""

RE_CT = re.compile(r'CT[\s.]?(\d{2,4})[\s./-](\d{4})', re.I)

TIPOS = (
    ('contrato',   re.compile(r'CONTRATO\s+DE\s+HONOR'),   'Contrato de honorários'),
    ('aditivo',    re.compile(r'ADITIVO'),                  'Aditivo'),
    ('resumo',     re.compile(r'RESUMO\s+DE\s+CONTRATA'),  'Resumo da contratação'),
    ('procuracao', re.compile(r'PROCURA'),                  'Procuração'),
    ('declaracao', re.compile(r'DECLARA'),                  'Declaração'),
)

_NAO_E_CONTRATACAO = re.compile(
    r'contrato\s+social|boleto|preposi|habilita|'
    r'para\s+revis|minuta|rascunho|modelo|template|\bcopia\b|\bcópia\b', re.I)

_ASSINADO = re.compile(r'd4sign|zapsign|assinad', re.I)


def criar_tabela(abrir):
    with abrir() as con:
        con.execute(DDL)
        con.commit()


_E_DE_UM_CASO = re.compile(r'\bX\b')


def _normalizar(nome: str) -> str:
    ""
    import unicodedata
    v = unicodedata.normalize('NFKD', nome or '')
    v = ''.join(c for c in v if not unicodedata.combining(c))
    return re.sub(r'[-_.]+', ' ', v).upper()


def classificar(nome: str):
    ""
    n = _normalizar(nome)
    if _NAO_E_CONTRATACAO.search(n):
        return None, None
    if _E_DE_UM_CASO.search(n):
        return None, None
    for chave, padrao, rotulo in TIPOS:
        if padrao.search(n):
            return chave, rotulo
    return None, None


def numero_do_contrato(nome: str) -> str:
    m = RE_CT.search(nome or '')
    return 'CT.%s.%s' % (m.group(1), m.group(2)) if m else ''


def _token_do_nome(nome: str) -> str:
    ""
    GENERICAS = {'LTDA', 'ME', 'EIRELI', 'EPP', 'SA', 'S/A', 'MEI', 'ESPOLIO',
                 'COMERCIO', 'INDUSTRIA', 'SERVICOS', 'TRANSPORTES', 'DE', 'DA', 'DO'}
    for p in (nome or '').upper().replace('.', ' ').split():
        limpo = ''.join(c for c in p if c.isalnum())
        if len(limpo) >= 4 and limpo not in GENERICAS:
            return limpo
    return ''


def procurar_no_drive(svc, cliente_nome: str) -> list:
    ""
    token = _token_do_nome(cliente_nome)
    if not token:
        return []

    achados, pagina = {}, None
    while True:
        r = svc.files().list(
            q="name contains '%s' and trashed=false" % token,
            fields='nextPageToken, files(id,name,mimeType,createdTime,modifiedTime)',
            pageSize=100, pageToken=pagina,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        for f in r.get('files', []):
            achados[f['id']] = f
        pagina = r.get('nextPageToken')
        if not pagina:
            break

    porta = {}
    for f in achados.values():
        tipo, rotulo = classificar(f['name'])
        if not tipo:
            continue
        ct = numero_do_contrato(f['name'])
        if not ct:
            m_ano = re.search(r'(20\d{2})', _normalizar(f['name']))
            ct = m_ano.group(1) if m_ano else ''
        base = re.sub(r'\.(pdf|docx?)$', '', f['name'], flags=re.I).strip().upper()
        base = re.sub(r'[^A-Z0-9]+', ' ', base).strip()
        atual = porta.get((ct, tipo, base[:40]))
        if not atual or _peso(f) > _peso(atual['_f']):
            porta[(ct, tipo, base[:40])] = {
                'drive_id': f['id'], 'nome': f['name'], 'tipo': tipo,
                'rotulo': rotulo, 'mime': f['mimeType'], 'contrato': ct,
                'quando': (f.get('createdTime') or '')[:10], '_f': f}

    ordem = {c: i for i, (c, _p, _r) in enumerate(TIPOS)}
    itens = list(porta.values())
    for i in itens:
        i.pop('_f', None)
    return sorted(itens, key=lambda x: (x['contrato'], ordem.get(x['tipo'], 9)),
                  reverse=False)


def _peso(f) -> int:
    return ((2 if _ASSINADO.search(f.get('name') or '') else 0) +
            (1 if f.get('mimeType') == 'application/pdf' else 0))

def gravar(abrir, cliente_id: int, contrato: str, itens: list):
    with abrir() as con:
        con.execute(DDL)
        con.execute('DELETE FROM portal_contratacao WHERE cliente_id=?', (cliente_id,))
        for i, it in enumerate(itens):
            con.execute(
                'INSERT OR REPLACE INTO portal_contratacao'
                '(cliente_id,contrato,drive_id,nome,tipo,mime,ordem,atualizado_em)'
                ' VALUES(?,?,?,?,?,?,?,?)',
                (cliente_id, contrato, it['drive_id'], it['nome'], it['tipo'],
                 it['mime'], i, time.time()))
        con.commit()


_DATA_NO_NOME = re.compile(r'(\d{2})[.\-/](\d{2})[.\-/](20\d{2})')


def _data_do_documento(nome: str, contrato: str) -> str:
    ""
    n = _normalizar(nome)
    m = _DATA_NO_NOME.search(n)
    if m:
        return '%s-%s-%s' % (m.group(3), m.group(2), m.group(1))
    d = ''.join(c for c in (contrato or '') if c.isdigit())
    if len(d) >= 4:
        return d[-4:] + '-00-00'
    m2 = re.search(r'(20\d{2})', n)
    return (m2.group(1) + '-00-00') if m2 else ''


def do_cliente(abrir, cliente_id: int) -> dict:
    ""
    with abrir() as con:
        con.execute(DDL)
        linhas = [dict(r) for r in con.execute(
            'SELECT drive_id, nome, tipo, contrato FROM portal_contratacao '
            'WHERE cliente_id=? ORDER BY ordem', (cliente_id,))]
    rotulos = {c: r for c, _p, r in TIPOS}

    docs = []
    for l in linhas:
        data = _data_do_documento(l['nome'], l['contrato'])
        docs.append({
            'id': l['drive_id'],
            'titulo': rotulos.get(l['tipo'], 'Documento'),
            'tipo': l['tipo'],
            'arquivo': l['nome'],
            'contrato': l['contrato'] or '',
            'data': data,
            'quando': _por_extenso(data),
            'assinado': bool(_ASSINADO.search(_normalizar(l['nome']))),
        })

    contratos = [d for d in docs if d['tipo'] == 'contrato']
    ativo = None
    if contratos:
        ativo = sorted(contratos, key=lambda d: (d['assinado'], d['data']),
                       reverse=True)[0]

    companhia, anteriores = [], []
    for d in docs:
        if ativo and d['id'] == ativo['id']:
            continue
        mesmo = ativo and (
            (d['contrato'] and d['contrato'] == ativo['contrato']) or
            (d['data'] and d['data'][:4] == ativo['data'][:4]))
        (companhia if mesmo else anteriores).append(d)

    return {'ativo': ativo, 'junto': companhia, 'anteriores': anteriores}


def _por_extenso(data: str) -> str:
    ""
    if not data:
        return ''
    a, m, d = (data.split('-') + ['', ''])[:3]
    if m in ('00', ''):
        return a
    return '%s/%s/%s' % (d, m, a)
