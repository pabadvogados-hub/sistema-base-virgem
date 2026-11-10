""
import re
import unicodedata

NOME_ENVIO = 'ENVIO DO CLIENTE'
PAPEL_ENVIO = 'writer'

NOMES_ENVIO = ('ENVIO DO CLIENTE', 'ENVIO DE DOCUMENTOS', 'DOCUMENTOS ENVIADOS',
               'ENVIAR DOCUMENTOS', 'UPLOAD DO CLIENTE')

PROIBIDAS = ('ATOS INTERNOS', 'INTERNO', 'ATOS INTERNO', 'CONTROLADORIA',
             'MINUTA', 'MINUTAS', 'RASCUNHO')


def _norm(v):
    v = unicodedata.normalize('NFKD', v or '')
    v = ''.join(c for c in v if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', v).upper().strip()


def _id_da_pasta(url_ou_id):
    v = (url_ou_id or '').rstrip('/')
    return v.split('/')[-1].split('?')[0]


def achar_subpasta(svc, raiz_id, nomes):
    try:
        r = svc.files().list(
            q=("'%s' in parents and trashed=false and "
               "mimeType='application/vnd.google-apps.folder'" % raiz_id),
            fields='files(id,name)', pageSize=60,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    except Exception:
        return None
    for f in r.get('files') or []:
        n = _norm(f['name'])
        if n in PROIBIDAS:
            continue
        if n in nomes:
            return f
    return None


def criar_pasta_envio(svc, raiz_id):
    ""
    try:
        return svc.files().create(
            body={'name': NOME_ENVIO, 'mimeType': 'application/vnd.google-apps.folder',
                  'parents': [raiz_id]},
            fields='id,name', supportsAllDrives=True).execute()
    except Exception:
        return None


def liberar_envio(svc, pasta_raiz, email, avisar=True) -> dict:
    ""
    raiz = _id_da_pasta(pasta_raiz)
    email = (email or '').strip().lower()
    if '@' not in email:
        return {'ok': False, 'motivo': 'sem_email',
                'mensagem': 'Informe um e-mail para receber o acesso.'}
    if not raiz:
        return {'ok': False, 'motivo': 'sem_pasta',
                'mensagem': 'Não consegui liberar agora.'}

    sub = achar_subpasta(svc, raiz, NOMES_ENVIO) or criar_pasta_envio(svc, raiz)
    if not sub:
        return {'ok': False, 'motivo': 'sem_subpasta',
                'mensagem': 'Não consegui liberar agora.'}

    if _norm(sub['name']) in PROIBIDAS:
        return {'ok': False, 'motivo': 'pasta_proibida',
                'mensagem': 'Não consegui liberar agora.'}

    ja = svc.permissions().list(
        fileId=sub['id'], fields='permissions(id,emailAddress,role)',
        supportsAllDrives=True).execute()
    for p in (ja.get('permissions') or []):
        if (p.get('emailAddress') or '').lower() == email:
            if p.get('role') not in (PAPEL_ENVIO, 'owner'):
                svc.permissions().update(
                    fileId=sub['id'], permissionId=p['id'],
                    body={'role': PAPEL_ENVIO}, supportsAllDrives=True).execute()
            return {'ok': True, 'ja_tinha': True, 'pasta_id': sub['id'],
                    'pasta_nome': sub['name'],
                    'link': 'https://drive.google.com/drive/folders/%s' % sub['id']}

    svc.permissions().create(
        fileId=sub['id'],
        body={'type': 'user', 'role': PAPEL_ENVIO, 'emailAddress': email},
        sendNotificationEmail=avisar,
        emailMessage='O Escritório Modelo Advogados liberou uma pasta para você enviar '
                     'documentos do seu processo.',
        supportsAllDrives=True).execute()
    return {'ok': True, 'ja_tinha': False, 'pasta_id': sub['id'],
            'pasta_nome': sub['name'],
            'link': 'https://drive.google.com/drive/folders/%s' % sub['id']}
