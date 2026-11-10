""
import io
import re
import unicodedata

PROIBIDAS = ('ATOS INTERNOS', 'INTERNO', 'ATOS INTERNO', 'CONTROLADORIA',
             'MINUTA', 'MINUTAS', 'RASCUNHO')

_NAO_MOSTRAR = re.compile(
    r'nao\s*usar|n[aã]o\s*usar|rascunho|minuta|_old|copia de|'
    r'roteiro|estrat[eé]gia|interno|controladoria|parecer de revis',
    re.I)

MAX_ENVIO = 25 * 1024 * 1024
TIPOS_ACEITOS = ('application/pdf', 'image/', 'application/msword', 'text/',
                 'application/vnd.openxmlformats', 'application/vnd.ms-')


def _norm(v):
    v = unicodedata.normalize('NFKD', v or '')
    v = ''.join(c for c in v if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', v).upper().strip()


def id_da_pasta(url_ou_id):
    ""
    v = (url_ou_id or '').strip().rstrip('/.,;')
    if re.fullmatch(r'[A-Za-z0-9_-]{20,}', v):
        return v
    if 'drive.google.com' not in v:
        return ''
    m = re.search(r'/folders/([A-Za-z0-9_-]{10,})', v) or re.search(r'[?&]id=([A-Za-z0-9_-]{10,})', v)
    return m.group(1) if m else ''


def listar(svc, pasta_id: str) -> list:
    ""
    if not pasta_id:
        return []
    try:
        r = svc.files().list(
            q="'%s' in parents and trashed=false and "
              "mimeType != 'application/vnd.google-apps.folder'" % pasta_id,
            fields='files(id,name,mimeType,modifiedTime,size)',
            pageSize=200, orderBy='name',
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    except Exception:
        return []
    saida = []
    for f in r.get('files') or []:
        if _NAO_MOSTRAR.search(f['name'] or ''):
            continue
        try:
            bytes_ = int(f.get('size') or 0)
        except (TypeError, ValueError):
            bytes_ = 0
        saida.append({
            'id': f['id'],
            'nome': re.sub(r'\.(pdf|docx?|jpe?g|png)$', '', f['name'], flags=re.I),
            'arquivo': f['name'],
            'mime': f.get('mimeType') or '',
            'quando': (f.get('modifiedTime') or '')[:10],
            'tamanho': bytes_,
            'pesado': bytes_ > 8 * 1024 * 1024,
        })
    return saida


def baixar(svc, drive_id: str):
    ""
    meta = svc.files().get(fileId=drive_id, fields='name,mimeType',
                           supportsAllDrives=True).execute()
    mime = meta.get('mimeType') or ''
    if mime.startswith('application/vnd.google-apps.'):
        return meta.get('name'), 'application/pdf', svc.files().export(
            fileId=drive_id, mimeType='application/pdf').execute()
    return meta.get('name'), mime, svc.files().get_media(fileId=drive_id).execute()


def enviar(svc, pasta_id: str, nome: str, conteudo: bytes, mime: str) -> dict:
    ""
    from googleapiclient.http import MediaIoBaseUpload

    if not pasta_id:
        return {'ok': False, 'mensagem': 'Não consegui receber agora.'}
    if len(conteudo or b'') > MAX_ENVIO:
        return {'ok': False, 'mensagem': 'Arquivo muito grande. O limite é 25 MB.'}
    if not any((mime or '').startswith(t) for t in TIPOS_ACEITOS):
        return {'ok': False,
                'mensagem': 'Tipo de arquivo não aceito. Envie PDF, foto ou documento.'}

    limpo = re.sub(r'[^\w\s.\-]', '', (nome or 'documento').split('/')[-1]).strip()
    limpo = (limpo or 'documento')[:120]

    try:
        arq = svc.files().create(
            body={'name': limpo, 'parents': [pasta_id]},
            media_body=MediaIoBaseUpload(io.BytesIO(conteudo), mimetype=mime,
                                         resumable=False),
            fields='id,name', supportsAllDrives=True).execute()
    except Exception as e:
        return {'ok': False, 'mensagem': 'Não consegui receber agora: %s' % str(e)[:70]}
    return {'ok': True, 'nome': arq.get('name'), 'id': arq.get('id')}


_JA_E_DO_CLIENTE = ('ACESSO DO CLIENTE', 'DOCUMENTOS DO CLIENTE',
                    'DOCUMENTOS CLIENTE', 'DOCS DO CLIENTE')


def achar_pasta_do_cliente(svc, pasta_url, numero='', reclamante='', cliente_nome=''):
    ""
    from . import drive_cliente

    raiz = id_da_pasta(pasta_url)
    if not raiz:
        return None, 'sem link de pasta cadastrado'
    try:
        m = svc.files().get(fileId=raiz, fields='id,name',
                            supportsAllDrives=True).execute()
    except Exception:
        return None, 'o link cadastrado nao abre'
    if _norm(m['name']) in _JA_E_DO_CLIENTE:
        return m, 'a pasta cadastrada ja e a do cliente'
    sub = drive_cliente.achar_subpasta_do_cliente(svc, raiz)
    if sub:
        return sub, 'subpasta dentro da pasta do caso'
    return None, 'a pasta do caso nao tem subpasta de cliente'
