""
import re

RE_CNJ = re.compile(r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}')

ROTULOS = {
    'valor_causa': (r'Valor\s+da\s+Causa[^:]{0,30}:\s*R\$\s*([\d\.]+,\d{2})',),
    'valor_pago': (r'Valor\s+do\s+Acordo[^:]{0,20}:\s*R\$\s*([\d\.]+,\d{2})',
                   r'Valor\s+da\s+Condena[^:]{0,20}:\s*R\$\s*([\d\.]+,\d{2})'),
    'proveito': (r'Proveito\s+Econ[^:]{0,20}:\s*R\$\s*([\d\.]+,\d{2})',),
}


def _num(txt):
    ""
    try:
        return float((txt or '').replace('.', '').replace(',', '.'))
    except ValueError:
        return None


def extrair(texto: str) -> dict:
    ""
    achado = {}
    for campo, padroes in ROTULOS.items():
        for p in padroes:
            m = re.search(p, texto or '', re.I)
            if m:
                achado[campo] = _num(m.group(1))
                break

    c, pg, pv = achado.get('valor_causa'), achado.get('valor_pago'), achado.get('proveito')
    if c is not None and pg is not None and pv is not None:
        if abs((c - pg) - pv) > 1.0:
            achado['inconsistente'] = True

    m = RE_CNJ.search(texto or '')
    if m:
        achado['numero_no_texto'] = m.group(0)
    return achado


def listar_pareceres(svc, limite=200) -> list:
    ""
    saida, pagina = [], None
    while True:
        r = svc.files().list(
            q="name contains 'terminativo' and trashed=false",
            fields='nextPageToken, files(id,name,mimeType,modifiedTime)',
            pageSize=100, pageToken=pagina,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        saida.extend(r.get('files') or [])
        pagina = r.get('nextPageToken')
        if not pagina or len(saida) >= limite:
            break
    return saida


DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def _texto_do_docx(bruto: bytes) -> str:
    ""
    import io
    import zipfile
    from html import unescape
    with zipfile.ZipFile(io.BytesIO(bruto)) as z:
        xml = z.read('word/document.xml').decode('utf-8', 'ignore')
    xml = re.sub(r'</w:p>', '\n', xml)
    xml = re.sub(r'<w:tab[^>]*/>', ' ', xml)
    xml = re.sub(r'<[^>]+>', '', xml)
    return unescape(xml)


def ler_texto(svc, arquivo: dict) -> str:
    ""
    mime = arquivo.get('mimeType') or ''
    if mime == 'application/vnd.google-apps.document':
        return svc.files().export(
            fileId=arquivo['id'], mimeType='text/plain').execute().decode('utf-8', 'ignore')
    if mime == DOCX:
        try:
            bruto = svc.files().get_media(fileId=arquivo['id'], supportsAllDrives=True).execute()
            return _texto_do_docx(bruto)
        except Exception:
            return ''
    return ''


def casar(arquivo: dict, texto: str, processos: list):
    ""
    nome = arquivo.get('name') or ''
    numeros = {p['numero'] for p in processos if p.get('numero')}

    m = RE_CNJ.search(nome)
    if m and m.group(0) in numeros:
        return m.group(0), 'numero no nome'

    m = RE_CNJ.search(texto or '')
    if m and m.group(0) in numeros:
        return m.group(0), 'numero no texto'

    alvo = _sem_acento(nome)
    candidatos = []
    for p in processos:
        for adv in (p.get('adversos') or []):
            primeiro = _sem_acento(str(adv)).split(' ')[0]
            if len(primeiro) >= 4 and primeiro in alvo:
                candidatos.append(p['numero'])
                break
    if len(set(candidatos)) == 1:
        return candidatos[0], 'nome da parte'
    return None, 'nao casou'


def _sem_acento(v):
    import unicodedata
    v = unicodedata.normalize('NFKD', v or '')
    return ''.join(c for c in v if not unicodedata.combining(c)).upper()
