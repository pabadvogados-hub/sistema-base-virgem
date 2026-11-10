"""Salva a peca gerada na pasta do cliente no Google Drive.

Cria (ou reusa) uma subpasta dentro da pasta do cliente e sobe o .docx nela.
"""
import sys
from pathlib import Path

PROJETO_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJETO_ROOT / 'INTEGRACOES'))

from .drive_reader import extrair_folder_id

# Nome da subpasta onde as pecas do sistema sao salvas
SUBPASTA_PECAS = 'PEÇAS SISTEMA OPERACIONAL'
MIME_DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MIME_FOLDER = 'application/vnd.google-apps.folder'


def _obter_ou_criar_subpasta(drive, pasta_pai_id: str, nome: str) -> str:
    """Retorna o ID da subpasta (cria se nao existir)."""
    nome_escapado = nome.replace("'", "\\'")
    query = (
        f"'{pasta_pai_id}' in parents and name='{nome_escapado}' "
        f"and mimeType='{MIME_FOLDER}' and trashed=false"
    )
    res = drive.files().list(q=query, fields='files(id, name)', pageSize=5).execute()
    achados = res.get('files', [])
    if achados:
        return achados[0]['id']
    criada = drive.files().create(
        body={'name': nome, 'mimeType': MIME_FOLDER, 'parents': [pasta_pai_id]},
        fields='id',
    ).execute()
    return criada['id']


def salvar_peca_na_pasta(drive_url_cliente: str, arquivo_path: str, nome_arquivo: str) -> dict:
    """Sobe o .docx na subpasta de pecas dentro da pasta do cliente.

    Retorna {'ok', 'url', 'erro'}.
    """
    pasta_cliente = extrair_folder_id(drive_url_cliente)
    if not pasta_cliente:
        return {'ok': False, 'url': '', 'erro': 'Link da pasta do cliente invalido'}
    if not Path(arquivo_path).exists():
        return {'ok': False, 'url': '', 'erro': 'Arquivo da peca nao encontrado'}

    try:
        from google_integration import autenticar_google
        drive, _ = autenticar_google()
    except BaseException as e:
        return {'ok': False, 'url': '', 'erro': f'Falha na autenticacao Google: {e}'}

    try:
        from googleapiclient.http import MediaFileUpload
        subpasta_id = _obter_ou_criar_subpasta(drive, pasta_cliente, SUBPASTA_PECAS)
        media = MediaFileUpload(arquivo_path, mimetype=MIME_DOCX, resumable=False)
        arq = drive.files().create(
            body={'name': nome_arquivo, 'parents': [subpasta_id]},
            media_body=media,
            fields='id, webViewLink',
        ).execute()
        return {'ok': True, 'url': arq.get('webViewLink', ''), 'erro': ''}
    except Exception as e:
        return {'ok': False, 'url': '', 'erro': f'Erro ao subir para o Drive: {e}'}
