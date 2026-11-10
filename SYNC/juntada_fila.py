# -*- coding: utf-8 -*-
"""
FILA DA JUNTADA PJe - atende o botao "Montar anexos do PJe" da tarefa de PROTOCOLO (16/10/2026).

O backend (routers/juntada.py) so grava o pedido em /opt/escritorio/producao/juntada/fila/<tarefa>.json.
Este script (cron do root, a cada minuto, com flock) pega um pedido por vez e:
  1. acha na tarefa a PECA e a PASTA DOS DOCUMENTOS (links que a esteira da inicial ja poe na descricao;
     sem eles, usa a pasta do caso > DOCUMENTOS DO CLIENTE e a peca mais nova de ATOS INTERNOS);
  2. junta os documentos separados (tudo em DOCUMENTOS DO CLIENTE, menos NAO UTILIZAR, REPETIDOS e as
     DOC's PROTOCOLO antigas) e a procuracao/declaracao ASSINADAS da pasta do cliente;
  3. monta pelo SYNC/juntada_pje.py;
  4. publica em DOCUMENTOS DO CLIENTE > "DOC's PROTOCOLO - <NOME>" (criar a pasta: autorizado pelo
     Titular em 16/10/2026) com os PDFs e o "00 - SUMARIO DE JUNTADA" em Google Doc;
  5. comenta na tarefa com o link, para a Advogada conferir. Nao marca checklist nem conclui nada.
Andamento em /opt/escritorio/producao/juntada/status/<tarefa>.json (o botao mostra).
"""
import html
import json
import os
import re
import sqlite3
import sys
import time
import traceback
from datetime import datetime

sys.path.insert(0, "/opt/escritorio/SYNC")
import juntada_pje as J  # noqa: E402
from googleapiclient.http import MediaFileUpload, MediaInMemoryUpload  # noqa: E402

DB = "/opt/escritorio/SISTEMA/backend/sistema_esc.db"
FILA = os.path.join(J.BASE, "fila")
STATUS = os.path.join(J.BASE, "status")
FORA = re.compile(r"N[ÃA]O UTILIZAR|REPETID|DOC'?S PROTOCOLO|PROTOCOLO -", re.I)
PASTA_FOLDER = "application/vnd.google-apps.folder"


def gravar_status(tid, etapa, msg, **extra):
    os.makedirs(STATUS, exist_ok=True)
    d = {"etapa": etapa, "mensagem": msg, "atualizado": time.time(), **extra}
    tmp = os.path.join(STATUS, f"{tid}.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(STATUS, f"{tid}.json"))


def _id_do_link(txt, tipo):
    pad = {"pasta": r"drive/folders/([A-Za-z0-9_-]{20,})",
           "arquivo": r"(?:document/d/|file/d/|open\?id=)([A-Za-z0-9_-]{20,})"}[tipo]
    m = re.search(pad, txt or "")
    return m.group(1) if m else None


def entradas_da_tarefa(drive, t):
    desc = t["descricao"] or ""
    linha_peca = next((l for l in desc.splitlines() if re.match(r"\s*Pe[cç]a", l, re.I)), "")
    linha_docs = next((l for l in desc.splitlines() if re.search(r"documentos do cliente", l, re.I)), "")
    peca = _id_do_link(linha_peca, "arquivo")
    docs = _id_do_link(linha_docs, "pasta")
    caso = _id_do_link(t["pasta_drive"] or "", "pasta")
    if not docs and caso:
        docs = next((f["id"] for f in J.listar(drive, caso)
                     if f["mimeType"] == PASTA_FOLDER and f["name"].upper().startswith("DOCUMENTOS DO CLIENTE")), None)
    if not docs:
        raise RuntimeError("nao achei a pasta DOCUMENTOS DO CLIENTE (nem na descricao, nem na pasta do caso)")
    cliente_pasta = drive.files().get(fileId=docs, fields="parents").execute().get("parents", [None])[0]
    if not peca:
        peca = peca_mais_nova(drive, cliente_pasta)
    if not peca:
        raise RuntimeError("nao achei a peca (sem link na descricao e nada em ATOS INTERNOS)")
    return docs, cliente_pasta, peca


def peca_mais_nova(drive, cliente_pasta):
    atos = next((f["id"] for f in J.listar(drive, cliente_pasta)
                 if f["mimeType"] == PASTA_FOLDER and f["name"].upper().startswith("ATOS INTERNOS")), None)
    if not atos:
        return None
    cand, pend = [], [atos]
    while pend:
        pid = pend.pop()
        for f in drive.files().list(q=f"'{pid}' in parents and trashed=false", pageSize=500,
                                    fields="files(id,name,mimeType,modifiedTime)").execute()["files"]:
            if f["mimeType"] == PASTA_FOLDER:
                pend.append(f["id"])
            elif re.search(r"^RT|INICIAL|PETI[CÇ][AÃ]O|CONTESTA|RECURSO", f["name"], re.I):
                cand.append(f)
    return max(cand, key=lambda f: f["modifiedTime"])["id"] if cand else None


def documentos_separados(drive, docs, cliente_pasta):
    out, pend = [], [docs]
    while pend:
        pid = pend.pop()
        for f in J.listar(drive, pid):
            if f["mimeType"] == PASTA_FOLDER:
                if not FORA.search(f["name"]):
                    pend.append(f["id"])
            elif not f["mimeType"].startswith("application/vnd.google-apps"):
                out.append(f)
    if cliente_pasta:                      # procuracao e declaracao assinadas ficam na raiz do cliente
        for f in J.listar(drive, cliente_pasta):
            if (re.search(r"ASSINAD", f["name"], re.I) and re.search(r"PROCURA|DECLARA", f["name"], re.I)
                    and f["name"].lower().endswith(".pdf")):
                out.append(f)
    return out


def sumario_html(cliente, r):
    plano, saidas = r["plano"], r["saidas"]
    e = html.escape
    linhas = "".join(
        f"<tr><td>{g['n']:02d}</td><td>{e(os.path.basename(a)[5:-4])}</td><td>{e(g.get('tipo_pje', ''))}</td>"
        f"<td>{e(g.get('sustenta', ''))}</td><td>{p}</td><td>{t / 1048576:.1f} MB</td></tr>"
        for g in plano["grupos"] for a, t, p in saidas.get(g["n"], []))
    nao = "".join(f"<li>{e((r['leitura_por_id'].get(x.get('id')) or {}).get('nome') or x.get('id', ''))}: "
                  f"{e(x.get('motivo', ''))}</li>" for x in plano.get("nao_entra", []))
    falta = "".join(f"<li>{e(x)}</li>" for x in plano.get("falta", [])) or "<li>nada</li>"
    obs = f"<h3>Observações</h3><p>{e(plano['observacoes'])}</p>" if plano.get("observacoes") else ""
    return (f"<html><body><h2>SUMÁRIO DE JUNTADA: {e(cliente)}</h2>"
            f"<p>Montado em {datetime.now():%d/%m/%Y %H:%M} pela montagem automática de anexos (Sistema Escritório Modelo), "
            f"a partir da peça <b>{e(r['peca'])}</b>. <b>Conferir antes de protocolar:</b> a ordem, o nexo de "
            f"cada anexo com a peça e o que ficou de fora.</p><h3>Ordem de juntada</h3>"
            "<table border='1' cellpadding='4'><tr><th>Nº</th><th>Arquivo</th><th>Tipo no PJe</th>"
            f"<th>O que sustenta na peça</th><th>Páginas</th><th>Tamanho</th></tr>{linhas}</table>"
            f"<h3>Não entra no protocolo</h3><ul>{nao or '<li>nada</li>'}</ul>"
            f"<h3>Ainda falta</h3><ul>{falta}</ul>{obs}</body></html>")


def publicar(drive, docs, cliente, r):
    base_nome = f"DOC's PROTOCOLO - {cliente.upper()}"
    existentes = {f["name"] for f in J.listar(drive, docs) if f["mimeType"] == PASTA_FOLDER}
    nome = base_nome if base_nome not in existentes else f"{base_nome} - montado {datetime.now():%d-%m %Hh%M}"
    pasta = drive.files().create(body={"name": nome, "mimeType": PASTA_FOLDER, "parents": [docs]},
                                 fields="id").execute()["id"]
    for g in r["plano"]["grupos"]:
        for arq, _, _ in r["saidas"].get(g["n"], []):
            drive.files().create(body={"name": os.path.basename(arq), "parents": [pasta]},
                                 media_body=MediaFileUpload(arq, mimetype="application/pdf", resumable=True),
                                 fields="id").execute()
    doc = subir_sumario(drive, pasta, cliente, sumario_html(cliente, r))
    return (f"https://drive.google.com/drive/folders/{pasta}",
            f"https://docs.google.com/document/d/{doc}/edit")


def subir_sumario(drive, pasta, cliente, html_txt):
    """O Drive as vezes devolve 500 na conversao HTML -> Google Doc: tenta de novo com pausa."""
    for tentativa in range(4):
        try:
            return drive.files().create(
                body={"name": f"00 - SUMARIO DE JUNTADA - {J.nome_arquivo(cliente)} (NAO PROTOCOLAR)",
                      "mimeType": "application/vnd.google-apps.document", "parents": [pasta]},
                media_body=MediaInMemoryUpload(html_txt.encode("utf-8"), mimetype="text/html", resumable=True),
                fields="id").execute()["id"]
        except Exception as e:
            J.log("  sumario: falhou no Drive, tentando de novo:", str(e)[:120])
            time.sleep(5 * (tentativa + 1))
    raise RuntimeError("o Drive nao aceitou o sumario de juntada")


def comentar(tid, texto):
    c = sqlite3.connect(DB, timeout=60)
    try:
        with c:
            c.execute("INSERT INTO tarefa_comentarios (tarefa_id, texto, por_id, por_nome, para, tipo, criado_em) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (tid, texto, None, "Montagem de anexos (automação)",
                       json.dumps([{"usuario_id": 6, "nome": "Advogada"}], ensure_ascii=False), "COMENTÁRIO", time.time()))
    finally:
        c.close()


def atender(pedido_path):
    ped = json.load(open(pedido_path, encoding="utf-8"))
    tid = int(ped["tarefa_id"])
    os.remove(pedido_path)
    st = lambda etapa, msg: gravar_status(tid, etapa, msg, pedido_por=ped.get("pedido_por"))
    try:
        st("iniciando", "Procurando a peca e os documentos da tarefa")
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        t = dict(c.execute("SELECT * FROM tarefas WHERE id=?", (tid,)).fetchone())
        c.close()
        cliente = (t.get("cliente") or "").strip() or f"TAREFA {tid}"
        drive = J.autenticar_google()[0]
        docs, cliente_pasta, peca = entradas_da_tarefa(drive, t)
        arquivos = documentos_separados(drive, docs, cliente_pasta)
        if not arquivos:
            raise RuntimeError("a pasta DOCUMENTOS DO CLIENTE esta vazia (fora NAO UTILIZAR e REPETIDOS)")
        r = J.montar(cliente, arquivos, peca, drive=drive, status=st)
        st("publicando", "Subindo os PDFs para o Drive do cliente")
        pasta, sumario = publicar(drive, docs, cliente, r)
        n = sum(len(v) for v in r["saidas"].values())
        falta = r["plano"].get("falta", [])
        comentar(tid, "ANEXOS DO PJe MONTADOS (automação)\n\n"
                 f"Pedido por {ped.get('pedido_por')}. São {n} PDFs numerados, na ordem do protocolo.\n"
                 f"Pasta: {pasta}\nSumário de juntada (não vai ao processo): {sumario}\n\n"
                 "CONFERIR antes de protocolar: a ordem, se cada anexo tem nexo com a peça e o que ficou fora."
                 + ("\n\nAinda falta:\n" + "\n".join(f"- {x}" for x in falta) if falta else ""))
        gravar_status(tid, "pronto", f"Pronto: {n} PDFs na pasta do cliente", pasta=pasta, sumario=sumario,
                      falta=falta, pedido_por=ped.get("pedido_por"))
    except Exception as e:
        traceback.print_exc()
        gravar_status(tid, "erro", f"Nao consegui montar: {e}", pedido_por=ped.get("pedido_por"))


def main():
    if not os.path.isdir(FILA):
        return
    pedidos = sorted((os.path.join(FILA, f) for f in os.listdir(FILA) if f.endswith(".json")), key=os.path.getmtime)
    for p in pedidos:
        J.log("pedido", os.path.basename(p))
        atender(p)


if __name__ == "__main__":
    main()
