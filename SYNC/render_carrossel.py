# -*- coding: utf-8 -*-
"""Transforma a pauta do estudio em imagens prontas para o Instagram.

O estudio (SYNC/estudio_conteudo.py) escreve o carrossel em mkt_pauta como JSON:
titulo, slides, legenda, hashtags. Bom para ler, inutil para postar. Aqui cada
slide vira um PNG 1080x1350 desenhado no template do escritorio.

Renderiza com o Chrome que ja existe na VPS, e nao com PIL colando texto: assim o
layout e CSS de verdade, com a Montserrat da marca (instalada em
/usr/share/fonts/truetype/montserrat/) e quebra de linha decente.

O template mora em /opt/escritorio/config/carrossel_esc.html e recebe:
    {{NUMERO}}  numero do slide       {{TOTAL}}  total de slides
    {{TITULO}}  titulo do slide       {{TEXTO}}  corpo do slide
    {{TIPO}}    capa | conteudo | fecho   (tambem vai em <body data-tipo>)

Uso:
    python render_carrossel.py --pauta 1
    python render_carrossel.py --todas          # toda pauta pronta sem imagem
    python render_carrossel.py --pauta 1 --refazer
"""
import argparse
import html
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import datetime

BASE = "/opt/escritorio/SISTEMA/backend"
DB = os.path.join(BASE, "sistema_esc.db")
MEDIA = os.getenv("ESC_MEDIA_DIR", "/opt/escritorio/media")
SAIDA = os.path.join(MEDIA, "carrossel")
PUBLICO = (os.getenv("PUBLIC_BASE_URL") or "https://sistema.escritorio.adv.br").rstrip("/") + "/media/carrossel/%s"
# Modelo HTML do carrossel com a identidade do escritorio (bordo e dourado). Ver ESC_CARROSSEL_TPL.
TEMPLATE = os.getenv("ESC_CARROSSEL_TPL", "/opt/escritorio/config/carrossel_esc.html")
CHROME = "/usr/bin/google-chrome"
LARGURA, ALTURA = 1080, 1350


def log(msg):
    print(msg, flush=True)


def garantir_coluna(db):
    """Guarda as imagens na propria pauta. Coluna nova, nada e alterado."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(mkt_pauta)")}
    if "imagens" not in cols:
        db.execute("ALTER TABLE mkt_pauta ADD COLUMN imagens TEXT")
        log("coluna imagens criada em mkt_pauta")
    if "imagens_em" not in cols:
        db.execute("ALTER TABLE mkt_pauta ADD COLUMN imagens_em TEXT")
    db.commit()


def tipo_do_slide(i, total):
    if i == 1:
        return "capa"
    if i == total:
        return "fecho"
    return "conteudo"


def montar_html(tpl, numero, total, slide, tipo):
    """Entrega o slide inteiro ao template, como JSON: cada tipo (gancho, lei,
    mito, prova, destaque, cta) tem campos proprios. O template monta o DOM com
    textContent, entao conteudo com & ou < nao quebra a pagina. '</' vira '<\\/'
    para ninguem fechar a tag <script> de dentro do texto."""
    s = dict(slide or {})
    s.setdefault("tipo", tipo)
    bruto = json.dumps(s, ensure_ascii=False).replace("</", "<\\/")
    return (tpl.replace("{{ESCIDE_JSON}}", bruto)
               .replace("{{NUMERO}}", str(int(numero)))
               .replace("{{TOTAL}}", str(int(total))))


def desenhar(html_txt, destino_png, trabalho):
    caminho_html = os.path.join(trabalho, "slide.html")
    with open(caminho_html, "w", encoding="utf-8") as f:
        f.write(html_txt)
    r = subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         "--force-device-scale-factor=1", "--virtual-time-budget=4000",
         "--screenshot=" + destino_png,
         "--window-size=%d,%d" % (LARGURA, ALTURA), "file://" + caminho_html],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if not os.path.exists(destino_png) or os.path.getsize(destino_png) < 2000:
        erro = (r.stderr or b"").decode("utf-8", "ignore")[-300:]
        raise RuntimeError("o Chrome nao gerou a imagem: " + erro)
    return os.path.getsize(destino_png)


def conferir(caminho):
    """A imagem saiu do tamanho certo e nao saiu de uma cor so?"""
    try:
        from PIL import Image
    except ImportError:
        return "sem Pillow para conferir"
    im = Image.open(caminho).convert("RGB")
    if im.size != (LARGURA, ALTURA):
        return "tamanho errado: %s" % (im.size,)
    cores = im.getcolors(maxcolors=200000)
    if cores and len(cores) < 12:
        return "imagem quase vazia (%d cores)" % len(cores)
    return ""


def render_pauta(db, pauta, tpl, refazer):
    pid = pauta["id"]
    slides = json.loads(pauta["slides"] or "[]")
    if not slides:
        log("  pauta %s nao tem slides" % pid)
        return None

    pasta = os.path.join(SAIDA, str(pid))
    if refazer and os.path.isdir(pasta):
        shutil.rmtree(pasta)
    os.makedirs(pasta, exist_ok=True)
    trabalho = os.path.join(pasta, "_trabalho")
    os.makedirs(trabalho, exist_ok=True)

    total = len(slides)
    urls, avisos = [], []
    for i, s in enumerate(slides, 1):
        tipo = tipo_do_slide(i, total)
        nome = "slide-%02d.png" % i
        destino = os.path.join(pasta, nome)
        corpo = montar_html(tpl, i, total, s, s.get("tipo") or tipo)
        tam = desenhar(corpo, destino, trabalho)
        aviso = conferir(destino)
        if aviso:
            avisos.append("%s: %s" % (nome, aviso))
        log("   %-13s %-9s %6d KB  %s" % (nome, tipo, tam // 1024, aviso or "ok"))
        urls.append(PUBLICO % ("%s/%s" % (pid, nome)))

    # um zip para quem quiser baixar tudo de uma vez
    caminho_zip = os.path.join(pasta, "carrossel-%s.zip" % pid)
    with zipfile.ZipFile(caminho_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(1, total + 1):
            nome = "slide-%02d.png" % i
            z.write(os.path.join(pasta, nome), nome)
        z.writestr("legenda.txt", "%s\n\n%s\n\n%s\n"
                   % (pauta["titulo"] or "", pauta["legenda"] or "", pauta["hashtags"] or ""))
    urls_zip = PUBLICO % ("%s/%s" % (pid, os.path.basename(caminho_zip)))
    shutil.rmtree(trabalho, ignore_errors=True)

    dados = {"slides": urls, "zip": urls_zip, "avisos": avisos}
    db.execute("UPDATE mkt_pauta SET imagens=?, imagens_em=? WHERE id=?",
               (json.dumps(dados, ensure_ascii=False),
                datetime.now().isoformat(sep=" ", timespec="seconds"), pid))
    db.commit()
    return dados


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pauta", type=int)
    ap.add_argument("--todas", action="store_true")
    ap.add_argument("--refazer", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(TEMPLATE):
        log("template nao encontrado em %s" % TEMPLATE)
        return 2
    tpl = open(TEMPLATE, encoding="utf-8").read()
    for marca in ("{{ESCIDE_JSON}}", "{{NUMERO}}", "{{TOTAL}}"):
        if marca not in tpl:
            log("o template nao tem o marcador %s" % marca)
            return 2

    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    garantir_coluna(db)

    if a.pauta:
        linhas = db.execute("SELECT * FROM mkt_pauta WHERE id=?", (a.pauta,)).fetchall()
    elif a.todas:
        linhas = db.execute(
            "SELECT * FROM mkt_pauta WHERE estado='pronto' "
            "AND COALESCE(imagens,'')='' ORDER BY id").fetchall()
    else:
        log("diga --pauta N ou --todas")
        return 2

    if not linhas:
        log("nada para desenhar")
        return 0

    for p in linhas:
        log("")
        log("pauta %s | %s" % (p["id"], p["titulo"] or p["tema"]))
        try:
            d = render_pauta(db, p, tpl, a.refazer)
        except Exception as e:
            log("   FALHOU: %s" % str(e)[:200])
            continue
        if d:
            log("   pronto: %d imagens + zip" % len(d["slides"]))
            log("   %s" % d["zip"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
