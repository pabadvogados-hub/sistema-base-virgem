"""DESVIRAR DOCUMENTOS NO DRIVE (01/10/2026). A Advogada pediu a Lua para "inverter os documentos que
estao virados" numa pasta de cliente (holerite de dezembro de ponta-cabeca) e a Lua nao tinha como.

Como funciona (em segundo plano; o resultado volta na conversa do agente):
  1. pega os arquivos: link de pasta (desce 1 nivel de subpasta), link de arquivo, ou o nome do cliente
     (DOCUMENTOS DO CLIENTE, casamento de nome duro do drive_leads, nunca cria pasta);
  2. cada imagem (JPG/PNG/WEBP) e cada pagina de PDF passa pelo detector de orientacao do Tesseract
     (OSD); se ele nao tiver certeza, pergunta ao Gemini olhando a imagem;
  3. so o que esta virado e girado, e o arquivo e SUBSTITUIDO no mesmo lugar, com o mesmo nome:
     o Drive guarda a versao anterior (Gerenciar versoes), da para voltar;
  4. com `graus` (90/180/270) gira direto, sem detectar.
"""
import base64
import io
import json
import logging
import os
import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path

import requests

log = logging.getLogger("girar_documentos")
DB = str(Path(__file__).resolve().parents[1] / "sistema_esc.db")
FOLDER = "application/vnd.google-apps.folder"
IMAGENS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
PDF = "application/pdf"
MAX_ARQUIVOS, MAX_PAGINAS = 60, 40


def _id(link):
    m = (re.search(r"/folders/([A-Za-z0-9_-]{15,})", link or "") or re.search(r"/d/([A-Za-z0-9_-]{15,})", link or "")
         or re.search(r"[?&]id=([A-Za-z0-9_-]{15,})", link or ""))
    if m:
        return m.group(1)
    return link if re.fullmatch(r"[A-Za-z0-9_-]{20,}", str(link or "")) else None


def _sem_acento(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def _drive():
    from .revisao_peca import _drive as d
    return d()


def _arquivos(drive, link=None, cliente=None):
    """[(id, nome, mime, pasta)] e o nome do lugar. Erro -> (None, motivo)."""
    alvo = _id(link) if link else None
    if not alvo and cliente:
        from .whatsapp.drive_leads import resolver_pasta_cliente
        achou = resolver_pasta_cliente(drive, cliente)
        if not achou:
            return None, "não achei a pasta de \"%s\" em RECLAMANTE (diga o nome completo ou mande o link)" % cliente
        alvo = achou["id"]
    if not alvo:
        return None, "mande o link da pasta (ou do arquivo) no Drive, ou o nome completo do cliente"
    meta = drive.files().get(fileId=alvo, fields="id,name,mimeType", supportsAllDrives=True).execute()
    if meta["mimeType"] != FOLDER:
        return [(meta["id"], meta["name"], meta["mimeType"], "")], meta["name"]
    out, filas = [], [(alvo, "", 0)]
    while filas and len(out) < 400:
        pid, sub, nivel = filas.pop(0)
        token = None
        while True:
            r = drive.files().list(q="'%s' in parents and trashed=false" % pid, pageSize=200, pageToken=token,
                                   fields="nextPageToken,files(id,name,mimeType)", supportsAllDrives=True,
                                   includeItemsFromAllDrives=True).execute()
            for f in r.get("files", []):
                if f["mimeType"] == FOLDER:
                    if nivel < 1:
                        filas.append((f["id"], f["name"], nivel + 1))
                elif f["mimeType"] in IMAGENS or f["mimeType"] == PDF:
                    out.append((f["id"], f["name"], f["mimeType"], sub))
            token = r.get("nextPageToken")
            if not token:
                break
    return out, meta["name"]


def _filtrar(arqs, arquivo):
    """'holerite dezembro' -> os que tem todas as palavras; senao os que tem alguma; senao todos."""
    if not arquivo:
        return arqs
    pal = [p for p in re.split(r"\W+", _sem_acento(arquivo)) if len(p) >= 3]
    if not pal:
        return arqs
    todos = [a for a in arqs if all(p in _sem_acento(a[1]) for p in pal)]
    if todos:
        return todos
    algum = [a for a in arqs if any(p in _sem_acento(a[1]) for p in pal)]
    return algum or arqs


def _gemini_rotacao(img):
    """Plano B quando o Tesseract nao tem certeza: o Gemini olha e diz quantos graus girar (horario)."""
    from . import ia_reserva as IR
    chave = IR._chave()
    if not chave:
        return None
    buf = io.BytesIO()
    im = img.convert("RGB")
    im.thumbnail((1200, 1200))
    im.save(buf, "JPEG", quality=80)
    corpo = {"contents": [{"role": "user", "parts": [
        {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}},
        {"text": "Este é um documento escaneado ou fotografado. Quantos graus no sentido HORÁRIO é preciso girar a "
                 "imagem para o texto ficar em pé, legível? Responda só um número: 0, 90, 180 ou 270."}]}],
        # o Gemini 3 pensa antes de responder: com limite de 20 tokens o pensamento comia tudo e a
        # resposta voltava vazia (01/10, pasta da Advogada). Limite folgado e pensamento curto.
        "generationConfig": {"temperature": 0, "maxOutputTokens": 2048, "thinkingConfig": {"thinkingLevel": "low"}}}
    try:
        r = requests.post(IR.URL % IR.MODELO, params={"key": chave}, json=corpo, timeout=60)
        partes = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        txt = "".join(p.get("text", "") for p in partes if not p.get("thought"))
        m = re.search(r"\b(0|90|180|270)\b", txt)
        return int(m.group(1)) if m else None
    except Exception as e:
        log.warning("gemini rotacao: %s", e)
        return None


def _osd(img):
    import pytesseract
    try:
        osd = pytesseract.image_to_osd(img.convert("RGB"), config="--psm 0 -c min_characters_to_try=20")
        return (int(re.search(r"Rotate:\s*(\d+)", osd).group(1)) % 360,
                float(re.search(r"Orientation confidence:\s*([\d.]+)", osd).group(1)))
    except Exception:
        return None, 0.0


def _rotacao(img, foto=True):
    """Graus no sentido horario para endireitar (0 = em pe). None = nao deu para saber.

    Teste na pasta da Advogada (01/10): em FOTO de celular o Tesseract erra muito (holerite deitado saiu
    "90 com confianca 0", o certo era 270; ponto em pe saiu "180"). Entao: pagina digital limpa ->
    Tesseract com confianca alta; foto -> Gemini, e so gira se, depois de girar, o Gemini disser que
    ficou em pe (segunda opiniao). Na duvida, nao mexe."""
    og, conf = _osd(img)
    if not foto and og is not None and conf >= 3:
        return og
    g = _gemini_rotacao(img)
    if g is None:
        return og if (og is not None and conf >= 3) else None
    if g == 0:
        return 0
    return g if _gemini_rotacao(img.rotate(-g, expand=True)) == 0 else None


class Incerto(Exception):
    """Nem o Tesseract nem o Gemini souberam dizer a orientacao: nao mexe e avisa."""


def _girar_imagem(dados, mime, graus):
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(dados)))
    g = graus if graus is not None else _rotacao(img)
    if g is None:
        raise Incerto()
    if not g:
        return None, g
    out = io.BytesIO()
    fmt = IMAGENS[mime]
    img = img.rotate(-g, expand=True)          # PIL gira anti-horario; o detector fala em horario
    if fmt == "JPEG" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(out, fmt, **({"quality": 92} if fmt == "JPEG" else {}))
    return out.getvalue(), g


def _girar_pdf(dados, graus):
    import fitz
    from PIL import Image
    doc = fitz.open(stream=dados, filetype="pdf")
    mudou, incertas = [], 0
    for i, pg in enumerate(doc):
        if i >= MAX_PAGINAS:
            break
        g = graus
        if g is None:
            pix = pg.get_pixmap(dpi=150)
            digital = len((pg.get_text() or "").strip()) > 150      # PDF gerado (texto de verdade) x escaneado
            g = _rotacao(Image.open(io.BytesIO(pix.tobytes("png"))), foto=not digital)
            if g is None:
                incertas += 1
                continue
        if g:
            pg.set_rotation((pg.rotation + g) % 360)
            mudou.append((i + 1, g))
    if not mudou:
        if incertas:
            raise Incerto()
        return None, []
    return doc.tobytes(garbage=3, deflate=True), mudou


def processar(drive, arqs, graus=None):
    from googleapiclient.http import MediaIoBaseUpload
    girados, em_pe, falhas = [], 0, []
    for fid, nome, mime, sub in arqs[:MAX_ARQUIVOS]:
        rotulo = (sub + "/" if sub else "") + nome
        try:
            dados = drive.files().get_media(fileId=fid, supportsAllDrives=True).execute()
            if mime == PDF:
                novo, paginas = _girar_pdf(dados, graus)
                detalhe = ", ".join("pág. %d: %d°" % p for p in paginas)
            else:
                novo, g = _girar_imagem(dados, mime, graus)
                detalhe = "%d°" % g if g else ""
            if not novo:
                em_pe += 1
                continue
            if os.environ.get("ESC_GIRAR_SIMULAR"):      # teste: detecta, nao grava no Drive
                girados.append("%s (%s) [simulado]" % (rotulo, detalhe))
                continue
            drive.files().update(fileId=fid, media_body=MediaIoBaseUpload(io.BytesIO(novo), mimetype=mime, resumable=False),
                                 supportsAllDrives=True).execute()
            girados.append("%s (%s)" % (rotulo, detalhe))
        except Incerto:
            falhas.append("%s: não consegui ver a orientação (diga quantos graus girar que eu giro)" % rotulo)
        except Exception as e:
            falhas.append("%s: %s" % (rotulo, str(e)[:80]))
    return girados, em_pe, falhas


def iniciar(uid, agente, link=None, cliente=None, arquivo=None, graus=None):
    """Confere o pedido e dispara em segundo plano. Devolve (mensagem, erro)."""
    if graus is not None:
        graus = int(graus) % 360
        if graus not in (90, 180, 270):
            return None, "graus tem de ser 90, 180 ou 270 (sentido horário)"
    drive = _drive()
    arqs, onde = _arquivos(drive, link, cliente)
    if arqs is None:
        return None, onde
    arqs = _filtrar(arqs, arquivo)
    if not arqs:
        return None, "não há imagem nem PDF em \"%s\"" % onde
    threading.Thread(target=_rodar, args=(uid, agente, drive, arqs, onde, graus), daemon=True).start()
    return ("Estou conferindo %d arquivo(s) em \"%s\"%s. Eu giro só o que estiver virado e aviso aqui quando terminar "
            "(uns %d segundos)." % (len(arqs[:MAX_ARQUIVOS]), onde, " (girando %d°)" % graus if graus else "",
                                    max(10, 4 * len(arqs[:MAX_ARQUIVOS])))), None


def _rodar(uid, agente, drive, arqs, onde, graus):
    from .revisao_peca import _avisar_na_conversa
    try:
        girados, em_pe, falhas = processar(drive, arqs, graus)
        if girados:
            txt = "Pronto, desvirei %d arquivo(s) em \"%s\":\n%s" % (len(girados), onde, "\n".join("- " + g for g in girados))
        else:
            txt = "Conferi \"%s\": nenhum documento estava virado." % onde
        if em_pe and girados:
            txt += "\nOs outros %d já estavam em pé." % em_pe
        if falhas:
            txt += "\nNão consegui mexer em: " + "; ".join(falhas)
        if girados:
            txt += "\nO arquivo foi trocado no mesmo lugar; a versão anterior fica no Drive (Gerenciar versões)."
    except Exception as e:
        log.exception("girar documentos")
        txt = "Não consegui terminar de desvirar os documentos de \"%s\": %s" % (onde, str(e)[:200])
    c = sqlite3.connect(DB, timeout=30)
    try:
        _avisar_na_conversa(c, uid, txt)
        c.execute("INSERT INTO agente_acoes (usuario_id, agente, ferramenta, alvo, resumo, detalhe, criado_em) "
                  "VALUES (?,?,?,?,?,?,?)", (uid, agente, "girar_documentos", onde, txt.split("\n")[0][:300],
                                             json.dumps({"arquivos": len(arqs)}), time.time()))
        c.commit()
    except sqlite3.Error as e:
        log.warning("registro girar: %s", e)
    finally:
        c.close()
