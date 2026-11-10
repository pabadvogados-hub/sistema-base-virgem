"""Leitura automatica de uma pasta do Google Drive.

Recebe o link de uma pasta, lista os documentos (recursivo), baixa e extrai
o texto (PDF/DOCX/TXT/Google Docs) e devolve tudo concatenado como contexto
pra geracao da peca — mesma logica do intake/OPERACIONAL.
"""
import io
import os
import re
import sys
import tempfile
from pathlib import Path

PROJETO_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJETO_ROOT / 'INTEGRACOES'))

# Subpastas a ignorar na varredura
PASTAS_IGNORAR = ['PETICAO INICIAL AUTOMACAO']
EXT_SUPORTADAS = {'.pdf', '.txt', '.docx'}
MAX_CHARS_POR_DOC = 15000
MAX_CHARS_TOTAL = 120000


def extrair_folder_id(url_ou_id: str) -> str | None:
    """Extrai o ID da pasta a partir de um link do Drive (ou aceita o ID cru)."""
    if not url_ou_id:
        return None
    s = url_ou_id.strip()
    m = re.search(r'/folders/([A-Za-z0-9_-]+)', s)
    if m:
        return m.group(1)
    m = re.search(r'[?&]id=([A-Za-z0-9_-]+)', s)
    if m:
        return m.group(1)
    if re.fullmatch(r'[A-Za-z0-9_-]{20,}', s):
        return s
    return None


def _extrair_texto_pdf(conteudo: bytes) -> str:
    import pdfplumber
    partes = []
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        tmp.write(conteudo)
        tmp_path = tmp.name
    try:
        with pdfplumber.open(tmp_path) as pdf:
            for pagina in pdf.pages:
                t = pagina.extract_text()
                if t:
                    partes.append(t)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return '\n\n'.join(partes)


def _extrair_texto_docx(conteudo: bytes) -> str:
    from docx import Document as DocxDoc
    with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp:
        tmp.write(conteudo)
        tmp_path = tmp.name
    try:
        doc = DocxDoc(tmp_path)
        return '\n'.join(p.text for p in doc.paragraphs)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _listar_arquivos(drive, pasta_id: str) -> list:
    """Lista arquivos suportados na pasta e subpastas (menos as ignoradas)."""
    arquivos = []
    pendentes = [pasta_id]
    while pendentes:
        pid = pendentes.pop()
        try:
            res = drive.files().list(
                q=f"'{pid}' in parents and trashed=false",
                fields='files(id, name, mimeType)', pageSize=300,
            ).execute()
        except Exception:
            continue
        for f in res.get('files', []):
            mime = f['mimeType']
            if mime == 'application/vnd.google-apps.folder':
                if not any(ig in f['name'].upper() for ig in PASTAS_IGNORAR):
                    pendentes.append(f['id'])
            elif mime == 'application/vnd.google-apps.document':
                arquivos.append(f)
            elif os.path.splitext(f['name'])[1].lower() in EXT_SUPORTADAS:
                arquivos.append(f)
    return arquivos


def ler_pasta(drive_url: str) -> dict:
    """Le a pasta do Drive e retorna {'ok', 'texto', 'qtd', 'nomes', 'erro'}."""
    fid = extrair_folder_id(drive_url)
    if not fid:
        return {'ok': False, 'texto': '', 'qtd': 0, 'nomes': [], 'erro': 'Link da pasta do Drive invalido'}

    try:
        from google_integration import autenticar_google
        drive, _ = autenticar_google()
    except BaseException as e:  # autenticar_google pode chamar sys.exit
        return {'ok': False, 'texto': '', 'qtd': 0, 'nomes': [], 'erro': f'Falha na autenticacao Google: {e}'}

    arquivos = _listar_arquivos(drive, fid)
    if not arquivos:
        return {'ok': False, 'texto': '', 'qtd': 0, 'nomes': [], 'erro': 'Nenhum documento encontrado na pasta (verifique o compartilhamento)'}

    blocos = []
    nomes_ok = []
    total = 0
    for arq in arquivos:
        nome, mime, fid_arq = arq['name'], arq['mimeType'], arq['id']
        try:
            if mime == 'application/vnd.google-apps.document':
                content = drive.files().export(fileId=fid_arq, mimeType='text/plain').execute()
                texto = content.decode('utf-8', errors='replace')
            else:
                content = drive.files().get_media(fileId=fid_arq).execute()
                ext = os.path.splitext(nome)[1].lower()
                if ext == '.pdf':
                    texto = _extrair_texto_pdf(content)
                elif ext == '.docx':
                    texto = _extrair_texto_docx(content)
                else:  # .txt
                    texto = content.decode('utf-8', errors='replace')
        except Exception as e:
            texto = f'(erro ao ler: {e})'

        texto = (texto or '').strip()
        if not texto:
            continue
        texto = texto[:MAX_CHARS_POR_DOC]
        blocos.append(f'===== DOCUMENTO: {nome} =====\n{texto}')
        nomes_ok.append(nome)
        total += len(texto)
        if total >= MAX_CHARS_TOTAL:
            break

    if not blocos:
        return {'ok': False, 'texto': '', 'qtd': 0, 'nomes': [], 'erro': 'Documentos encontrados, mas sem texto extraivel'}

    return {'ok': True, 'texto': '\n\n'.join(blocos), 'qtd': len(nomes_ok), 'nomes': nomes_ok, 'erro': ''}
