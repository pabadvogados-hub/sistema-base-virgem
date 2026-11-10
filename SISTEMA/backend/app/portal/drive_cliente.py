""
import re
import unicodedata

NOMES_ACEITOS = (
    'ACESSO DO CLIENTE',
    'DOCUMENTOS DO CLIENTE', 'DOCUMENTOS CLIENTE', 'DOCS DO CLIENTE',
    'DOCUMENTOS DO CASO', 'DOCUMENTOS DO PROCESSO', 'DOCUMENTOS',
    'DOCS DO CASO', 'DOCUMENTOS DA ACAO',
)
PAPEL = 'reader'


def _norm(v: str) -> str:
    v = unicodedata.normalize('NFKD', v or '')
    v = ''.join(c for c in v if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', v).upper().strip()


def achar_subpasta_do_cliente(svc, pasta_raiz_id: str):
    ""
    try:
        r = svc.files().list(
            q=(f"'{pasta_raiz_id}' in parents and trashed=false and "
               "mimeType='application/vnd.google-apps.folder'"),
            fields='files(id,name)', pageSize=50,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    except Exception:
        return None
    achadas = {_norm(f['name']): f for f in (r.get('files') or [])}
    for nome in NOMES_ACEITOS:
        if nome in achadas:
            return achadas[nome]
    return None


def liberar_leitura(svc, pasta_raiz_id: str, email: str, avisar: bool = True) -> dict:
    ""
    sub = achar_subpasta_do_cliente(svc, pasta_raiz_id)
    if not sub:
        return {'ok': False, 'motivo': 'sem_subpasta',
                'mensagem': 'Não consegui liberar agora.'}

    email = (email or '').strip().lower()
    if '@' not in email:
        return {'ok': False, 'motivo': 'sem_email',
                'mensagem': 'Informe um e-mail para receber o acesso a pasta.'}

    ja = svc.permissions().list(
        fileId=sub['id'], fields='permissions(id,emailAddress,role)',
        supportsAllDrives=True).execute()
    for p in (ja.get('permissions') or []):
        if (p.get('emailAddress') or '').lower() == email:
            if p.get('role') != PAPEL and p.get('role') != 'owner':
                svc.permissions().update(fileId=sub['id'], permissionId=p['id'],
                                         body={'role': PAPEL},
                                         supportsAllDrives=True).execute()
            return {'ok': True, 'ja_tinha': True, 'pasta_id': sub['id'],
                    'pasta_nome': sub['name'],
                    'link': f"https://drive.google.com/drive/folders/{sub['id']}"}

    svc.permissions().create(
        fileId=sub['id'],
        body={'type': 'user', 'role': PAPEL, 'emailAddress': email},
        sendNotificationEmail=avisar,
        emailMessage='O Escritório Modelo Advogados liberou o acesso de leitura aos documentos '
                     'do seu processo.',
        supportsAllDrives=True).execute()
    return {'ok': True, 'ja_tinha': False, 'pasta_id': sub['id'],
            'pasta_nome': sub['name'],
            'link': f"https://drive.google.com/drive/folders/{sub['id']}"}


def listar(svc, pasta_id: str) -> list:
    ""
    try:
        r = svc.files().list(
            q=f"'{pasta_id}' in parents and trashed=false",
            fields='files(id,name,mimeType,modifiedTime,webViewLink)',
            pageSize=100, orderBy='folder,name',
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        return r.get('files') or []
    except Exception:
        return []
