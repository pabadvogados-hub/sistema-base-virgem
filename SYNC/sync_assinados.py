# -*- coding: utf-8 -*-
"""ZAPSIGN -> DRIVE: arquiva o documento ASSINADO na pasta do cliente (Advogada, 07/10/2026).

Era uma rotina do tempo em que tudo rodava no PC ("3x ao dia pelo Agendador de Tarefas do
Windows"). Na VPS ela NUNCA entrou no cron: o contrato assinado ficava só no ZapSign. A Advogada
mandou religar em 07/10/2026.

O que mudou em relação à versão antiga, e por quê:
  1. A PASTA VEM DO QUE O SISTEMA JÁ SABE, não de busca por nome. Primeiro a linha
     "PASTA DO CASO" do processo, depois a pasta gravada no card. Só se não houver nenhuma
     das duas é que procura no Drive, e aí exige nome EXATO do cliente. Nome parecido não
     serve: escrever o contrato na pasta do cliente errado é pior do que não arquivar.
  2. JANELA. A versão antiga varria os 212 documentos assinados do histórico inteiro de uma
     vez. Agora o padrão é olhar os últimos DIAS dias (30), e o histórico só entra com --todos.
  3. MEMÓRIA NO LUGAR CERTO. O log de já sincronizados era caminho relativo: rodando de outra
     pasta ele se perdia e reenviava tudo. Agora fica ao lado do script.
  4. Antes de subir, confere se já existe arquivo com o mesmo nome na pasta: nada de duplicata.

USO:
  sync_assinados.py                 simula os últimos 30 dias (não envia nada)
  sync_assinados.py --aplicar       arquiva de verdade
  sync_assinados.py --dias 90       outra janela
  sync_assinados.py --todos         histórico inteiro (usar com --aplicar só depois de olhar)
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv(os.path.join(HERE, '..', 'config', '.env'))
load_dotenv('/opt/escritorio/config/.env', override=False)

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

DB = os.getenv('SISTEMA_DB', '/opt/escritorio/SISTEMA/backend/sistema_esc.db')
LOG = os.path.join(HERE, 'sync_assinados_log.json')          # ao lado do script, nunca relativo
TOKEN_DRIVE = os.getenv('ESC_TOKEN_GOOGLE', '/opt/escritorio/config/token.json')
RAIZ_RECLAMANTE = os.getenv('GOOGLE_PASTA_RECLAMANTE', '')
SUBPASTA = 'DOCUMENTOS DO CLIENTE'
DIAS = 30
STOP = {'DE', 'DA', 'DO', 'DOS', 'DAS', 'E', 'LTDA', 'ME', 'EIRELI'}


def norm(n):
    s = unicodedata.normalize('NFKD', str(n or '')).encode('ascii', 'ignore').decode().upper()
    return ' '.join(re.sub(r'[^A-Z0-9 ]', ' ', s).split())


def toks(n):
    return [p for p in norm(n).split() if len(p) >= 3 and p not in STOP]


def _json(p, padrao):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return padrao


def drive():
    creds = Credentials.from_authorized_user_file(
        TOKEN_DRIVE, ['https://www.googleapis.com/auth/drive'])
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_DRIVE, 'w') as f:
            f.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)


def pasta_que_o_sistema_sabe(cliente_nome):
    """A pasta do caso que já está no Sistema Escritório Modelo: no processo ou no card. Nada de adivinhar."""
    tk = toks(cliente_nome)
    if len(tk) < 2:
        return None, 'nome curto demais'
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    achados = set()
    for r in con.execute("SELECT p.notas, pp.nome FROM jur_processos p "
                         "JOIN jur_processo_partes pp ON pp.processo_id = p.id "
                         "WHERE COALESCE(p.notas,'') LIKE '%PASTA DO CASO%'"):
        if len(set(toks(r['nome'])) & set(tk)) >= 2 and toks(r['nome'])[:1] == tk[:1]:
            m = re.search(r'/folders/([A-Za-z0-9_-]{20,})', r['notas'] or '')
            if m:
                achados.add(m.group(1))
    for r in con.execute("SELECT contato_nome, drive_pasta_id FROM wa_conversas "
                         "WHERE COALESCE(drive_pasta_id,'') <> ''"):
        if len(set(toks(r['contato_nome'])) & set(tk)) >= 2 and toks(r['contato_nome'])[:1] == tk[:1]:
            achados.add(r['drive_pasta_id'])
    con.close()
    if len(achados) == 1:
        return achados.pop(), 'pasta do caso no sistema'
    if len(achados) > 1:
        return None, 'duas pastas diferentes no sistema, confira na mão'
    return None, 'sem pasta no sistema'


def pasta_no_drive(dv, cliente_nome):
    """Último recurso: procura no Drive, mas só aceita nome EXATO (normalizado)."""
    alvo = norm(cliente_nome)
    if len(toks(cliente_nome)) < 2:
        return None, 'nome curto demais'
    r = dv.files().list(
        q=("'%s' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
           % RAIZ_RECLAMANTE), fields='files(id,name)', pageSize=1000).execute()
    iguais = [f for f in r.get('files', []) if norm(f['name']) == alvo]
    if len(iguais) == 1:
        return iguais[0]['id'], 'pasta pelo nome exato'
    if len(iguais) > 1:
        return None, 'mais de uma pasta com esse nome'
    return None, 'nenhuma pasta com o nome exato'


def subpasta_documentos(dv, pasta_id):
    r = dv.files().list(q=("'%s' in parents and mimeType='application/vnd.google-apps.folder' "
                           "and name='%s' and trashed=false" % (pasta_id, SUBPASTA)),
                        fields='files(id)').execute()
    f = r.get('files', [])
    return f[0]['id'] if f else pasta_id


def ja_esta_la(dv, pasta_id, nome_arquivo):
    r = dv.files().list(q="'%s' in parents and trashed=false" % pasta_id,
                        fields='files(name)', pageSize=300).execute()
    return any(norm(f['name']) == norm(nome_arquivo) for f in r.get('files', []))


def docs_assinados(token, dias, todos):
    corte = datetime.now() - timedelta(days=dias)
    H = {'Authorization': 'Bearer ' + token}
    pag, saida = 1, []
    while pag <= 40:
        r = requests.get('https://api.zapsign.com.br/api/v1/docs/?page=%d' % pag,
                         headers=H, timeout=40)
        if r.status_code != 200:
            break
        docs = r.json().get('results', [])
        if not docs:
            break
        for d in docs:
            if d.get('status') != 'signed' or not d.get('signed_file'):
                continue
            quando = str(d.get('last_update_at') or d.get('created_at') or '')[:10]
            if not todos and quando:
                try:
                    if datetime.strptime(quando, '%Y-%m-%d') < corte:
                        continue
                except Exception:
                    pass
            saida.append(d)
        pag += 1
    return saida


def rodar(aplicar, dias, todos):
    agora = datetime.now()
    print(f"=== ZAPSIGN -> DRIVE | {agora:%d/%m/%Y %H:%M} | "
          f"{'ARQUIVANDO' if aplicar else 'SIMULACAO'} | "
          f"{'histórico inteiro' if todos else f'últimos {dias} dias'} ===")
    token = os.getenv('ZAPSIGN_API_TOKEN') or os.getenv('ZAPSIGN_TOKEN')
    if not token:
        print('sem ZAPSIGN_API_TOKEN, nada a fazer')
        return
    log = _json(LOG, {'sincronizados': []})
    feitos = set(log['sincronizados'])
    dv = drive()
    docs = docs_assinados(token, dias, todos)
    print(f'documentos assinados na janela: {len(docs)} | já arquivados antes: {len(feitos)}\n')
    ok, pulados = 0, []
    for d in docs:
        if d.get('token') in feitos:
            continue
        nome_doc = d.get('name') or 'documento'
        cliente = (d.get('folder_path') or '').strip('/').strip() or nome_doc.split(' - ')[0].strip()
        pasta, como = pasta_que_o_sistema_sabe(cliente)
        if not pasta:
            pasta, como = pasta_no_drive(dv, cliente)
        if not pasta:
            pulados.append(f'{cliente[:26]} ({como})')
            continue
        destino = subpasta_documentos(dv, pasta)
        arquivo = f'{nome_doc}.pdf' if not nome_doc.lower().endswith('.pdf') else nome_doc
        if ja_esta_la(dv, destino, arquivo):
            print(f'   [já está lá] {arquivo[:52]}')
            if aplicar:
                feitos.add(d['token'])
            continue
        print(f'   {arquivo[:52]} -> {cliente[:24]} ({como})')
        if aplicar:
            pdf = requests.get(d['signed_file'], timeout=120).content
            dv.files().create(body={'name': arquivo, 'parents': [destino]},
                              media_body=MediaInMemoryUpload(pdf, mimetype='application/pdf'),
                              fields='id').execute()
            feitos.add(d['token'])
            ok += 1
    if aplicar:
        with open(LOG, 'w', encoding='utf-8') as f:
            json.dump({'sincronizados': sorted(feitos),
                       'ultima_rodada': agora.isoformat(sep=' ', timespec='seconds')}, f, indent=1)
    print(f'\narquivados: {ok} | sem pasta segura: {len(pulados)}')
    if pulados:
        print('não arquivei (pasta não confirmada): ' + '; '.join(sorted(set(pulados))[:12]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--aplicar', action='store_true')
    ap.add_argument('--dias', type=int, default=DIAS)
    ap.add_argument('--todos', action='store_true')
    a = ap.parse_args()
    rodar(a.aplicar, a.dias, a.todos)


if __name__ == '__main__':
    main()
