# -*- coding: utf-8 -*-
"""Documento anexado pela Advogada no chat do Assistente (camada I, item 41; decisão D23: guardado por 30 dias).

O arquivo fica fora da pasta web, com nome sorteado: o nome original é só rótulo. O tipo é conferido pelo conteúdo, não pela
extensão. A leitura reaproveita os leitores da camada D. O texto é conteúdo de fora: ler_anexo está em LEITURA_DE_FORA.

    python -m app.lua_anexos limpar      (cron diário: apaga o que passou de 30 dias)
"""
import io
import json
import os
import re
import sys
import time
import uuid
import zipfile

PASTA = os.environ.get("LUA_ANEXOS_PASTA", "/opt/escritorio/dados/lua_anexos")
LUA_UID = 5
TETO_DE_BYTES = 20 * 1024 * 1024       # o nginx corta em 25 MB; passar disso daria erro sem explicação
DIAS_GUARDADOS = 30
TETO_DO_NOME = 120
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ID = re.compile(r"[0-9a-f]{32}")


class Recusado(Exception):
    pass


def _caminhos(anexo_id):
    return os.path.join(PASTA, anexo_id + ".bin"), os.path.join(PASTA, anexo_id + ".json")


def _tipo(nome, dados):
    if dados.startswith(b"%PDF-"):
        return PDF
    if dados.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if dados.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if dados.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(dados)) as z:
                return DOCX if "word/document.xml" in z.namelist() else None
        except zipfile.BadZipFile:
            return None
    if nome.lower().endswith(".txt") and b"\x00" not in dados:
        return "text/plain"
    return None


def nome_seguro(nome):
    """Só o rótulo que aparece no chat: sem pasta, sem caractere de controle e sem os colchetes da marca do anexo."""
    nome = re.split(r"[\\/]", str(nome or ""))[-1]
    nome = re.sub(r"[\x00-\x1f\x7f\[\]:]", " ", nome).strip()[:TETO_DO_NOME].strip()
    return nome or "documento"


def guardar(nome, dados, uid):
    if uid != LUA_UID:
        raise Recusado("o anexo é só para a conversa do Assistente com a Dra. Advogada.")
    if not dados:
        raise Recusado("o arquivo veio vazio.")
    if len(dados) > TETO_DE_BYTES:
        raise Recusado("arquivo grande demais: o limite é de %d MB." % (TETO_DE_BYTES // (1024 * 1024)))
    rotulo = nome_seguro(nome)
    tipo = _tipo(rotulo, dados)
    if not tipo:
        raise Recusado("não sei ler esse tipo de arquivo. Mande PDF, Word (.docx), imagem (PNG ou JPG) ou texto (.txt).")
    os.makedirs(PASTA, mode=0o700, exist_ok=True)
    anexo_id = uuid.uuid4().hex
    arquivo, meta = _caminhos(anexo_id)
    with open(os.open(arquivo, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
        f.write(dados)
    with open(os.open(meta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
        json.dump({"nome": rotulo, "tipo": tipo, "uid": uid, "criado_em": time.time(), "tamanho": len(dados)}, f, ensure_ascii=False)
    return {"id": anexo_id, "nome": rotulo}


def ler(anexo_id, parte=1):
    from . import lua_drive as LD
    anexo_id = str(anexo_id or "").strip().lower()
    if not _ID.fullmatch(anexo_id):
        return {"erro": "não reconheci esse anexo. Use o id que veio na mensagem da Advogada."}
    try:
        parte = int(parte or 1)
    except (TypeError, ValueError):
        return {"erro": "parte inválida."}
    arquivo, caminho_meta = _caminhos(anexo_id)
    try:
        with open(caminho_meta, encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("uid") != LUA_UID:
            return {"erro": "esse anexo não é da conversa com a Dra. Advogada."}
        with open(arquivo, "rb") as f:
            dados = f.read()
    except (OSError, ValueError):
        return {"erro": "esse anexo não existe mais: os documentos ficam guardados por %d dias. Peça para a Advogada anexar de novo." % DIAS_GUARDADOS}
    try:
        texto, avisos_do_arquivo = LD._leitor(meta.get("tipo"))(dados)
    except Exception:       # arquivo corrompido ou com senha
        return {"erro": "não consegui ler o conteúdo desse anexo."}
    if not (texto or "").strip():
        return {"erro": "não consegui ler nenhum texto nesse anexo."}
    partes = LD._dividir_texto(texto)
    try:
        trecho = LD._escolher(partes, parte)
    except IndexError:
        return {"erro": "esse anexo tem %d parte(s)." % len(partes)}
    avisos = [LD.AVISO_CONTEUDO_DE_FORA] + list(avisos_do_arquivo or [])
    if parte < len(partes):
        avisos.append("Há mais conteúdo: chame de novo com parte=%d (são %d partes)." % (parte + 1, len(partes)))
    return {"parte": parte, "partes": len(partes), "avisos": avisos, "arquivo": {"nome": meta.get("nome"), "id": anexo_id}, "texto": trecho}


def limpar(agora=None):
    """Apaga o que passou do prazo. Só toca em arquivo com o nome que este módulo cria."""
    if not os.path.isdir(PASTA):
        return 0
    corte = (agora or time.time()) - DIAS_GUARDADOS * 86400
    apagados = 0
    for nome in os.listdir(PASTA):
        base, ext = os.path.splitext(nome)
        if not (_ID.fullmatch(base) and ext in (".bin", ".json")):
            continue
        caminho = os.path.join(PASTA, nome)
        if os.path.getmtime(caminho) < corte:
            os.remove(caminho)
            apagados += ext == ".bin"
    return apagados


if __name__ == "__main__":
    if sys.argv[1:] == ["limpar"]:
        print("anexos apagados:", limpar())
    else:
        sys.exit(__doc__)
