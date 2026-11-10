"""Sobe arquivos que o lead enviou pelo WhatsApp direto para o Google Drive.

A midia do lead chega como URL do Z-API (midia_url). Aqui a gente baixa os bytes
e sobe pra pasta do lead no Drive (uma subpasta por lead dentro de uma pasta-mae
dedicada a documentos de leads). Reusa a autenticacao OAuth do INTEGRACOES.
"""
import io
import os
import re
import sys
import mimetypes
from pathlib import Path

import requests

# INTEGRACOES/ (google_integration) fica na raiz do projeto (5 niveis acima).
PROJETO_ROOT = Path(__file__).resolve().parents[4]
_p = str(PROJETO_ROOT / 'INTEGRACOES')
if _p not in sys.path:
    sys.path.insert(0, _p)

MIME_FOLDER = 'application/vnd.google-apps.folder'
# Nome da pasta-mae onde ficam os documentos que os leads mandam pelo WhatsApp.
# Fica separada de RECLAMANTE (que e so de cliente ja contratado/trabalhista).
PASTA_MAE_LEADS = os.getenv('GOOGLE_PASTA_LEADS_DOCS_NOME', 'DOCUMENTOS DE LEADS (WhatsApp)')

# Extensao por tipo de midia quando nao da pra inferir da URL/content-type.
_EXT_PADRAO = {'imagem': 'jpg', 'audio': 'ogg', 'doc': 'pdf', 'video': 'mp4'}


def _slug(texto: str) -> str:
    texto = (texto or '').strip()
    texto = re.sub(r'[\\/:*?"<>|]+', '-', texto)   # tira caracteres proibidos
    return texto[:120] or 'Lead'


def _obter_ou_criar_pasta(drive, nome: str, pasta_pai_id: str | None) -> dict:
    """Retorna {'id','url'} de uma pasta (cria se nao existir). pasta_pai_id None = raiz."""
    nome_escapado = nome.replace("'", "\\'")
    q = f"name='{nome_escapado}' and mimeType='{MIME_FOLDER}' and trashed=false"
    if pasta_pai_id:
        q += f" and '{pasta_pai_id}' in parents"
    res = drive.files().list(q=q, fields='files(id, webViewLink)', pageSize=5).execute()
    achados = res.get('files', [])
    if achados:
        return {'id': achados[0]['id'], 'url': achados[0].get('webViewLink', '')}
    body = {'name': nome, 'mimeType': MIME_FOLDER}
    if pasta_pai_id:
        body['parents'] = [pasta_pai_id]
    criada = drive.files().create(body=body, fields='id, webViewLink').execute()
    return {'id': criada['id'], 'url': criada.get('webViewLink', '')}


def _ext(midia_url: str, content_type: str, tipo: str) -> str:
    # 1) extensao explicita na URL
    m = re.search(r'\.([a-zA-Z0-9]{2,4})(?:\?|$)', (midia_url or '').split('#')[0])
    if m and len(m.group(1)) <= 4:
        return m.group(1).lower()
    # 2) do content-type
    if content_type:
        guess = mimetypes.guess_extension(content_type.split(';')[0].strip())
        if guess:
            return guess.lstrip('.').lower()
    # 3) padrao por tipo
    return _EXT_PADRAO.get(tipo, 'bin')


def _nome_arquivo(item: dict, ext: str, data_str: str) -> str:
    """Se o lead mandou um doc com nome, respeita; senao gera <data>_<tipo>_<id>.<ext>."""
    conteudo = (item.get('conteudo') or '').strip()
    if item.get('tipo') == 'doc' and conteudo and '.' in conteudo and len(conteudo) < 120:
        return _slug(conteudo)
    return f"{data_str}_{item.get('tipo','arquivo')}_{item.get('id','')}.{ext}"


def resolver_pasta_lead(drive, nome_lead: str, telefone: str) -> dict:
    """Garante a pasta-mae de leads e a subpasta deste lead. Retorna {'id','url'}."""
    mae = _obter_ou_criar_pasta(drive, PASTA_MAE_LEADS, None)
    nome_sub = f"{_slug(nome_lead) or 'Lead'} - {telefone}".strip(' -')
    return _obter_ou_criar_pasta(drive, nome_sub, mae['id'])


# ============================================================
# PASTA DO CLIENTE JA CONTRATADO (RECLAMANTE > NOME > DOCUMENTOS DO CLIENTE)
# ------------------------------------------------------------
# Regra: quando alguem da equipe clica em "Subir no Drive" numa
# conversa de cliente CADASTRADO, o arquivo tem que cair na pasta DELE, nao numa
# pasta solta de leads. So cai em DOCUMENTOS DE LEADS quem ainda nao e cliente.
#
# Este modulo NUNCA cria pasta de cliente. Se nao achar, devolve None e o fluxo
# antigo assume. Criar pasta no Drive continua sendo decisao humana.
# ============================================================
PASTA_RECLAMANTE = os.getenv('GOOGLE_PASTA_RECLAMANTE', '')   # ID da pasta-mae RECLAMANTE no Drive do escritorio
SUBPASTA_DOCS = 'DOCUMENTOS DO CLIENTE'
# Etiqueta que a equipe gruda no nome (na pasta ou no contato do WhatsApp) e que
# nao faz parte do nome da pessoa. ADVOGADO fica DE FORA de proposito: "Antonio
# Marcos Advogado" nao e o cliente Fausto Brandão.
_RUIDO = {'ESPOLIO', 'CLIENTE', 'TRABALHISTA',
          'NOVO', 'ANTIGO', 'LEAD', 'LEADS'}


def _normalizar(nome: str) -> list:
    """Nome virado em lista de palavras comparaveis: sem acento, maiusculo, sem
    ruido e sem conectivo (DA, DE, DOS...). "José da Silva Jr" -> ['JOSE','SILVA','JR']."""
    import unicodedata
    txt = unicodedata.normalize('NFKD', nome or '')
    txt = ''.join(c for c in txt if not unicodedata.combining(c)).upper()
    txt = re.sub(r'[^A-Z0-9 ]+', ' ', txt)
    fora = {'DA', 'DE', 'DO', 'DAS', 'DOS', 'E'} | _RUIDO
    return [p for p in txt.split() if p and p not in fora and len(p) > 1]


def _casa(partes_pessoa: list, partes_pasta: list, raros: set | None = None) -> bool:
    """Casa quando o primeiro nome bate E TODOS os nomes do contato existem na pasta.

    Por que tao duro: documento de um cliente dentro da pasta de outro e quebra de
    sigilo entre clientes, e nesta base isso acontece facil. Testado
    contra as 97 pastas reais, a regra frouxa (primeiro nome + QUALQUER sobrenome)
    mandava "Benicio Arantes Prado" pra BENICIO ARANTES DE LIMA, "Benicio Torres da
    Silva" pra BENICIO NUNES DA SILVA e "Caetano Rocha Leal" pra CAETANO ROCHA DUARTE
    QUINTELA. Todas casavam por um sobrenome comum.

    Exigindo que todo nome do contato apareca na pasta, o erro vira o contrario:
    as vezes ele NAO acha a pasta e o arquivo cai na pasta de leads, que e
    exatamente onde ele ja caia antes. Errar pra menos custa um arquivo no lugar
    de sempre; errar pra mais custa sigilo.

    O contato costuma ter MENOS nomes que a pasta ("Teodoro Lins" x "TEODORO LINS
    DE OLIVEIRA CAMPOS"), por isso e subconjunto e nao igualdade.
    """
    if len(partes_pessoa) < 2 or len(partes_pasta) < 2:
        return False
    if partes_pessoa[0] != partes_pasta[0]:
        return False
    if not set(partes_pessoa).issubset(set(partes_pasta)):
        return False
    if len(partes_pessoa) >= 3 or len(partes_pessoa) == len(partes_pasta):
        return True
    # Sobrou o caso magro: o contato deu SO DOIS nomes e a pasta tem mais.
    # "Benicio Arantes" cabe dentro de BENICIO ARANTES DE LIMA, mas nao prova nada: os
    # dois nomes sao comuns demais. Nesse caso exige que pelo menos um dos dois
    # seja UNICO entre as pastas ("Caetano Quintela": QUINTELA so existe numa).
    return bool(raros and set(partes_pessoa) & raros)


# NOME COMPLETO PELO TELEFONE. O nome do card vem cortado pelo CRM
# ("Jesse", "Francisco Roberio Do Nasc") e o casamento de pasta, que e duro de proposito,
# falhava: o documento do cliente ia para a pasta de leads. O cadastro tem o nome inteiro.
DB_SISTEMA = os.getenv('SISTEMA_DB', '/opt/escritorio/SISTEMA/backend/sistema_esc.db')


def nome_do_cadastro(telefone: str) -> str:
    """Nome completo do cliente no cadastro (jur_clientes), achado pelos 8 ultimos digitos do
    telefone. Devolve '' quando nao acha, quando o telefone e curto ou quando ha mais de um
    cliente com aquele final (ai nao se chuta: o nome do card segue valendo)."""
    import sqlite3
    d = ''.join(ch for ch in str(telefone or '') if ch.isdigit())[-8:]
    if len(d) < 8:
        return ''
    try:
        con = sqlite3.connect('file:%s?mode=ro' % DB_SISTEMA, uri=True, timeout=15)
        rows = con.execute(
            "select distinct upper(trim(nome)) from jur_clientes "
            " where replace(replace(replace(replace(coalesce(telefone,''),' ',''),'-',''),"
            "'(',''),')','') like ? "
            "    or replace(replace(replace(replace(coalesce(celular,''),' ',''),'-',''),"
            "'(',''),')','') like ?",
            ('%' + d, '%' + d)).fetchall()
        con.close()
    except Exception:
        return ''
    nomes = [r[0] for r in rows if (r[0] or '').strip()]
    return nomes[0] if len(nomes) == 1 else ''


def resolver_pasta_cliente(drive, nome: str) -> dict | None:
    """Acha a pasta do cliente em RECLAMANTE. None se nao achar ou se ficar ambiguo.

    Devolve {'id','url','pasta','subpasta'} apontando para DOCUMENTOS DO CLIENTE
    quando ela existe, senao para a raiz da pasta do cliente (sem criar nada).
    """
    if not PASTA_RECLAMANTE:
        return None                      # pasta-mae nao configurada no .env (GOOGLE_PASTA_RECLAMANTE)
    partes = _normalizar(nome)
    if len(partes) < 2:
        return None                      # um nome sozinho nao da pra afirmar de quem e
    try:
        pastas, token = [], None
        while True:
            r = drive.files().list(
                q=f"'{PASTA_RECLAMANTE}' in parents and mimeType='{MIME_FOLDER}' and trashed=false",
                fields='nextPageToken, files(id,name,webViewLink)',
                pageSize=1000, pageToken=token).execute()
            pastas += r.get('files', [])
            token = r.get('nextPageToken')
            if not token:
                break
    except Exception:
        return None                      # Drive fora do ar nao pode quebrar o upload

    pastas = [p for p in pastas if not p['name'].startswith('00 ')]
    # nome que aparece em UMA unica pasta: e ele que sustenta um casamento de 2 nomes
    from collections import Counter
    freq = Counter()
    for p in pastas:
        freq.update(set(_normalizar(p['name'])))
    raros = {n for n, c in freq.items() if c == 1}

    achados = [p for p in pastas if _casa(partes, _normalizar(p['name']), raros)]
    if len(achados) != 1:
        return None                      # zero: nao e cliente. mais de um: nao chuto.

    cliente = achados[0]
    try:
        subs = drive.files().list(
            q=f"'{cliente['id']}' in parents and mimeType='{MIME_FOLDER}' and trashed=false",
            fields='files(id,name,webViewLink)', pageSize=50).execute().get('files', [])
    except Exception:
        subs = []
    docs = next((s for s in subs if s['name'].strip().upper() == SUBPASTA_DOCS), None)
    if docs:
        return {'id': docs['id'], 'url': docs.get('webViewLink', ''),
                'pasta': cliente['name'], 'subpasta': SUBPASTA_DOCS}
    # pasta do cliente existe mas sem a subpasta padrao: sobe na raiz dela.
    # NAO cria a subpasta: criar pasta no Drive e decisao humana.
    return {'id': cliente['id'], 'url': cliente.get('webViewLink', ''),
            'pasta': cliente['name'], 'subpasta': ''}


def subir_arquivos(nome_lead: str, telefone: str, itens: list, pasta_id: str = '',
                   data_str: str = '') -> dict:
    """Baixa cada midia (Z-API) e sobe pro Drive na pasta do lead.

    itens: [{'id','tipo','midia_url','conteudo'}]
    Retorna {'ok','pasta_id','pasta_url','resultados':[{'id','ok','url','erro'}], 'erro'}.
    """
    try:
        from google_integration import autenticar_google
        drive, _ = autenticar_google()
    except BaseException as e:       # autenticar_google pode chamar sys.exit
        return {'ok': False, 'pasta_id': '', 'pasta_url': '', 'resultados': [],
                'erro': f'Falha na autenticacao Google: {e}'}

    from googleapiclient.http import MediaIoBaseUpload

    # ONDE VAI CAIR (nesta ordem):
    #   1. pasta do CLIENTE em RECLAMANTE, quando o nome casa com uma so pasta;
    #   2. a pasta que ja foi usada antes nesta conversa (cache);
    #   3. a pasta de leads (quem ainda nao e cliente).
    # O cliente vem ANTES do cache de proposito: as conversas que subiram arquivo
    # antes de 22/09 tem o cache apontando pra pasta solta de leads, e sem essa
    # ordem elas continuariam caindo no lugar errado pra sempre.
    pasta_url, destino = '', 'leads'
    try:
        # 1o o nome COMPLETO do cadastro (achado pelo telefone), depois o nome do card: o do
        # card vem cortado pelo CRM e por isso o documento do cliente ia para a pasta de leads
        cliente = None
        for _nome_tentativa in [n for n in (nome_do_cadastro(telefone), nome_lead) if n]:
            cliente = resolver_pasta_cliente(drive, _nome_tentativa)
            if cliente:
                break
        if cliente:
            pasta_id, pasta_url = cliente['id'], cliente['url']
            destino = 'cliente'
            caminho = 'RECLAMANTE > ' + cliente['pasta'] + (
                ' > ' + cliente['subpasta'] if cliente['subpasta'] else '')
        elif pasta_id:
            meta = drive.files().get(fileId=pasta_id, fields='id, webViewLink',
                                     supportsAllDrives=True).execute()
            pasta_url = meta.get('webViewLink', '')
            caminho = PASTA_MAE_LEADS
        else:
            pasta = resolver_pasta_lead(drive, nome_lead, telefone)
            pasta_id, pasta_url = pasta['id'], pasta['url']
            caminho = PASTA_MAE_LEADS
    except Exception as e:
        return {'ok': False, 'pasta_id': '', 'pasta_url': '', 'resultados': [],
                'erro': f'Erro ao resolver pasta de destino: {e}'}

    resultados = []
    for item in itens:
        url = item.get('midia_url') or ''
        if not url:
            resultados.append({'id': item.get('id'), 'ok': False, 'url': '', 'erro': 'sem midia'})
            continue
        try:
            r = requests.get(url, timeout=45)
            r.raise_for_status()
            dados = r.content
            ct = r.headers.get('Content-Type', '')
            ext = _ext(url, ct, item.get('tipo', ''))
            nome_arq = _nome_arquivo(item, ext, data_str)
            mime = ct.split(';')[0].strip() or mimetypes.guess_type(nome_arq)[0] or 'application/octet-stream'
            media = MediaIoBaseUpload(io.BytesIO(dados), mimetype=mime, resumable=False)
            arq = drive.files().create(
                body={'name': nome_arq, 'parents': [pasta_id]},
                media_body=media, fields='id, webViewLink',
            ).execute()
            resultados.append({'id': item.get('id'), 'ok': True,
                               'url': arq.get('webViewLink', ''), 'erro': ''})
        except Exception as e:
            resultados.append({'id': item.get('id'), 'ok': False, 'url': '', 'erro': str(e)[:200]})

    algum = any(x['ok'] for x in resultados)
    return {'ok': algum, 'pasta_id': pasta_id, 'pasta_url': pasta_url,
            'destino': destino, 'caminho': caminho,
            'resultados': resultados, 'erro': '' if algum else 'Nenhum arquivo subiu'}
