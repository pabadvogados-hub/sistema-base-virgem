# -*- coding: utf-8 -*-
"""
JUNTADA PJe - monta os anexos do protocolo a partir dos documentos ja separados e da peca pronta.

Pedido do Titular (16/10/2026) para o usuario da Advogada. Hoje ela zipa as imagens que a advogada
separou, manda para o Claude no chat junto com a peca, e ele monta os PDFs. Aqui o mesmo trabalho
sai direto do Drive:

  1. LE cada arquivo separado (imagem ou PDF), sem olhar o nome: tipo, empresa, data, rota, horarios.
     Repetidos (mesmo arquivo ou mesma foto) saem antes da leitura.
  2. LE a peca e monta o PLANO de juntada no formato da controladoria (sumario do Oziel, 16/10):
     numerado, documentos pessoais primeiro, provas agrupadas por fato/periodo na ordem da peca,
     tipo no PJe, o que cada anexo sustenta, o que nao entra e o que ainda falta.
  3. MONTA os PDFs (uma imagem por pagina, desvirada e comprimida) abaixo do limite do PJe.
  4. Grava tudo em /opt/escritorio/producao/juntada/<CLIENTE>/ para conferencia. NAO escreve no Drive.

A escolha do que vai para o PJe continua sendo da advogada, e a conferencia, da Advogada.
Leitura pelo Claude Code da VPS (assinatura), como a varredura_provas: ANTHROPIC_API_KEY sai do ambiente.

USO:
  python SYNC/juntada_pje.py --cliente "NOME" --pastas <id>[,<id>] --peca <id> [--arquivos <id>,...]
                             [--esconder-nomes] [--sem-pdf]
"""
import argparse
import concurrent.futures as cf
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime

sys.path.insert(0, "/opt/escritorio/SISTEMA/backend/app")
import drive_reader  # noqa: E402,F401  (poe o caminho do google_integration)
from google_integration import autenticar_google  # noqa: E402

import fitz  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

CLAUDE = os.getenv("ESC_CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))
BASE = "/opt/escritorio/producao/juntada"
LIMITE_MB = float(os.getenv("PJE_LIMITE_MB", "3"))
LOTE = 6
PARALELO = 3
EXT_IMG = (".jpg", ".jpeg", ".png", ".webp")

TIPOS = ["CT-e", "MDF-e", "DANFE", "Canhoto NF-e", "Cupom de abastecimento", "Vale-pedagio",
         "Comprovante de Pix", "Extrato bancario", "Contrato de frete", "Contrato de servico",
         "Ordem de coleta", "Ordem de transporte", "Minuta de despacho", "Manifesto de carga",
         "Romaneio de carga", "Relatorio de embarque", "Recibo de descarga", "Protocolo de entrega",
         "Controle de portaria", "Agendamento de entrega", "Packing list", "Pedido de compra",
         "Boleto", "Vale-pallet", "Conversa (print)", "Fotografia", "CNH", "RG/CPF",
         "Comprovante de residencia", "CTPS", "Holerite", "TRCT", "Extrato FGTS", "Procuracao",
         "Declaracao de hipossuficiencia", "Contrato social/ficha JUCESP", "Cartao CNPJ/QSA",
         "Convencao coletiva", "Documento processual", "Outro"]

TIPOS_PJE = ["Procuração", "Declaração de Hipossuficiência", "Documento de Identificação", "CTPS",
             "Convenção Coletiva de Trabalho (CCT)", "Contrato Social", "Documento Diverso"]


# ---------------------------------------------------------------- utilidades
def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def slug(txt):
    t = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_").upper()


def nome_arquivo(txt, limite=140):
    t = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode().upper()
    t = re.sub(r'[\\/:*?"<>|]+', "-", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= limite:
        return t
    # longo demais: corta no meio da descricao e preserva o periodo do fim (" - 05.01 A 12.02.2026")
    partes = t.rsplit(" - ", 1)
    fim = (" - " + partes[1]) if len(partes) == 2 and len(partes[1]) <= 30 else ""
    return t[:limite - len(fim)].rstrip(" ,.-") + fim


def _json_da_resposta(txt):
    """Pega o ultimo bloco JSON ({...} ou [...]) da resposta do Claude."""
    txt = re.sub(r"```(?:json)?", "", txt)
    for abre, fecha in (("[", "]"), ("{", "}")):
        i, j = txt.find(abre), txt.rfind(fecha)
        if i != -1 and j > i:
            try:
                return json.loads(txt[i:j + 1])
            except Exception:
                pass
    return None


def claude(prompt, modelo="sonnet", cwd=None, timeout=1200):
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)          # assinatura, nunca API
    r = subprocess.run([CLAUDE, "-p", "--model", modelo, "--allowedTools", "Read"],
                       input=prompt.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=env, cwd=cwd or tempfile.gettempdir(), timeout=timeout)
    if r.returncode != 0:
        log("  claude rc", r.returncode, r.stderr.decode("utf-8", "ignore")[:200])
    return _json_da_resposta(r.stdout.decode("utf-8", "ignore"))


def _salvar(path, dados):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- Drive
def listar(drive, pasta_id):
    out, tok = [], None
    while True:
        r = drive.files().list(q=f"'{pasta_id}' in parents and trashed=false", pageToken=tok,
                               fields="nextPageToken,files(id,name,mimeType,size)", pageSize=1000).execute()
        out += r["files"]
        tok = r.get("nextPageToken")
        if not tok:
            return out


def baixar(drive, f, pasta_cache):
    ext = os.path.splitext(f["name"])[1].lower() or (".pdf" if f["mimeType"].endswith("pdf") else ".jpg")
    path = os.path.join(pasta_cache, f["id"] + ext)
    if not os.path.exists(path):
        b = drive.files().get_media(fileId=f["id"]).execute()
        with open(path, "wb") as fh:
            fh.write(b)
    return path


def texto_peca(drive, peca_id):
    meta = drive.files().get(fileId=peca_id, fields="name,mimeType").execute()
    if meta["mimeType"] == "application/vnd.google-apps.document":
        return meta["name"], drive.files().export(fileId=peca_id, mimeType="text/plain").execute().decode("utf-8", "ignore")
    b = drive.files().get_media(fileId=peca_id).execute()
    if meta["name"].lower().endswith(".pdf"):
        return meta["name"], "\n".join(p.get_text() for p in fitz.open(stream=b, filetype="pdf"))
    import docx
    return meta["name"], "\n".join(p.text for p in docx.Document(io.BytesIO(b)).paragraphs)


# ---------------------------------------------------------------- repetidos
def _dhash(path, n=12):
    im = ImageOps.exif_transpose(Image.open(path)).convert("L").resize((n + 1, n))
    px = list(im.getdata())
    bits = 0
    for y in range(n):
        for x in range(n):
            bits = (bits << 1) | (px[y * (n + 1) + x] > px[y * (n + 1) + x + 1])
    return bits


def separar_repetidos(itens):
    """itens: [{id, path}] -> (unicos, repetidos[(id, id_do_original)])"""
    vistos_md5, vistos_hash, unicos, rep = {}, [], [], []
    for it in itens:
        md5 = hashlib.md5(open(it["path"], "rb").read()).hexdigest()
        it["md5"] = md5
        if md5 in vistos_md5:
            rep.append((it["id"], vistos_md5[md5]))
            continue
        vistos_md5[md5] = it["id"]
        if it["path"].lower().endswith(EXT_IMG):
            try:
                h = _dhash(it["path"])
                igual = next((oid for oh, oid in vistos_hash if bin(oh ^ h).count("1") <= 4), None)
                if igual:
                    rep.append((it["id"], igual))
                    continue
                vistos_hash.append((h, it["id"]))
            except Exception:
                pass
        unicos.append(it)
    return unicos, rep


# ---------------------------------------------------------------- 1) leitura
INSTRUCAO_LEITURA = (
    "Voce trabalha na controladoria do Escritório Modelo Advogados (acoes trabalhistas de motoristas de caminhao). "
    "Leia cada arquivo listado abaixo com a ferramenta Read e devolva APENAS um JSON array, um objeto "
    "por arquivo, nesta forma:\n"
    '{"arq": "<nome do arquivo>", "tipo": "<um da lista>", "emitente": "", "destinatario": "", '
    '"data": "DD.MM.AAAA", "numero": "", "origem": "CIDADE-UF", "destino": "CIDADE-UF", '
    '"horarios": "", "valor": "", "pessoas": "", "resumo": "", "legivel": true, '
    '"nome": "Tipo - Empresa - DD.MM.AAAA"}\n'
    "Regras: nunca invente; campo vazio quando a informacao nao aparece. 'data' e a data principal do "
    "documento (emissao, viagem, pagamento). 'horarios' so com horarios visiveis que mostram jornada "
    "(entrada/saida de portaria, inicio/fim de descarga, abastecimento). 'resumo' em uma frase: o que o "
    "documento mostra sobre o trabalho do motorista. 'nome' no padrao do escritorio, por exemplo "
    "'Canhoto NF-e - Bimbo do Brasil - 19.03.2026' ou 'Comprovante de Pix - Dinho BR Transportes ao "
    "Reclamante - 07.06.2026'. Conta de consumo (agua, luz, telefone, internet) com nome ou endereco do "
    "cliente e 'Comprovante de residencia'. No CT-e, 'emitente' e a transportadora que emitiu, nao o "
    "tomador nem o destinatario da carga. Tipos validos: " + ", ".join(TIPOS) + "."
)


def ler_lote(lote, pasta_trab):
    with tempfile.TemporaryDirectory(dir=pasta_trab) as tmp:
        nomes = {}
        for i, it in enumerate(lote, 1):
            ext = os.path.splitext(it["path"])[1].lower()
            apelido = f"a{i:02d}{ext}"          # nome neutro: a leitura nao se apoia no nome do arquivo
            shutil.copy(it["path"], os.path.join(tmp, apelido))
            nomes[apelido] = it["id"]
        prompt = INSTRUCAO_LEITURA + "\n\nArquivos:\n" + "\n".join(os.path.join(tmp, a) for a in nomes)
        res = claude(prompt, "sonnet", cwd=tmp) or []
        out = {}
        for o in res if isinstance(res, list) else []:
            fid = nomes.get(os.path.basename(str(o.get("arq", ""))))
            if fid:
                o.pop("arq", None)
                out[fid] = o
        return out


def ler_todos(unicos, leitura, pasta_trab, salvar, status=None):
    falta = [it for it in unicos if it["md5"] not in leitura]
    lotes = [falta[i:i + LOTE] for i in range(0, len(falta), LOTE)]
    log(f"leitura: {len(unicos) - len(falta)} ja lidos, {len(falta)} a ler em {len(lotes)} lotes")
    por_id = {it["id"]: it for it in unicos}
    with cf.ThreadPoolExecutor(PARALELO) as ex:
        for n, res in enumerate(ex.map(lambda l: ler_lote(l, pasta_trab), lotes), 1):
            for fid, o in res.items():
                leitura[por_id[fid]["md5"]] = o
            salvar()
            log(f"  lote {n}/{len(lotes)}: {len(res)} lidos")
            if status:
                status("lendo", f"Lendo os documentos: {min(n * LOTE, len(falta))} de {len(falta)}")
    # o que voltou sem leitura tenta mais uma vez, um por um
    for it in [it for it in unicos if it["md5"] not in leitura]:
        res = ler_lote([it], pasta_trab)
        if it["id"] in res:
            leitura[it["md5"]] = res[it["id"]]
    salvar()


# ---------------------------------------------------------------- 2) plano
INSTRUCAO_PLANO = """Voce e a controladoria do Escritório Modelo Advogados montando a juntada de documentos de uma peca trabalhista para o PJe.
Abaixo vem a PECA e a lista de DOCUMENTOS que a advogada ja separou (cada um com id e o que foi lido nele).
Monte o plano de juntada e devolva APENAS um JSON:
{"grupos": [{"n": 1, "titulo": "...", "tipo_pje": "...", "sustenta": "...", "docs": ["id", ...]}],
 "nao_entra": [{"id": "...", "motivo": "..."}],
 "falta": ["..."],
 "observacoes": "..."}

Regras:
1. Ordem: procuracao, declaracao de hipossuficiencia, documento de identificacao (CNH/RG), comprovante de residencia, CTPS; depois os demais documentos pessoais e financeiros (extrato FGTS, extratos bancarios, holerites, TRCT, comprovantes de pagamento); depois as PROVAS DOS FATOS na ordem em que a peca os narra (em geral cronologica); depois documentos das reclamadas (contrato social, ficha JUCESP, cartao CNPJ, QSA); depois convencoes coletivas; por ultimo prova emprestada.
2. Provas dos fatos: um PDF por VIAGEM ou PERIODO CONTINUO de trabalho (uma viagem de varios dias, uma semana de entregas, um contrato de servico com tudo o que veio dele). Junte no mesmo grupo todos os documentos daquele periodo (CT-e, MDF-e, notas, canhotos, cupons, vale-pedagio, portaria, recibos). Nao crie grupo com 1 ou 2 documentos se eles cabem na viagem/semana vizinha. Prints e fotos sobre o mesmo assunto (ex.: rastreador bloqueado, conversas com gerenciadoras de risco) formam um grupo so. Documentos de mesmo tipo e mesmo papel na peca (ex.: todos os comprovantes de Pix) formam um grupo so. Mire em 15 a 25 PDFs no total. Dentro do grupo, ordem cronologica.
2a. Documentos pessoais NUNCA vao para nao_entra por data: conta de consumo (agua, luz, telefone, internet) em nome do cliente ou com o endereco dele E o comprovante de residencia, mesmo sendo recente.
3. Titulo em MAIUSCULAS, sem acento, com no maximo 110 caracteres, que diga o que o anexo prova, com o periodo no fim. Exemplos do escritorio: "VIAGENS AO RIO DE JANEIRO - MDF-E, NOTAS, ABASTECIMENTOS E VALE-PEDAGIO - 05.01 A 12.02.2026"; "ENTREGAS REGIONAIS BIMBO E AMERICANAS - JORNADA DE 18.03 DAS 05H46 AS 23H20 - 16 A 21.03.2026"; "COMPROVANTES DE PIX - EDIVALDO FERREIRA E DINHO BR TRANSPORTES AO RECLAMANTE - 04.01 A 13.06.2026"; documentos pessoais: "PROCURACAO - <NOME DO CLIENTE>". Use horarios e rotas so quando estiverem na leitura do documento.
4. tipo_pje: um destes: """ + " | ".join(TIPOS_PJE) + """.
5. sustenta: o pedido ou topico da peca que o anexo prova, com a numeracao da peca quando houver (ex.: "Jornada e horas extras (III.2)").
6. Todo documento da lista entra em um grupo OU em nao_entra com o motivo (ilegivel, sem relacao com o que a peca alega, so repete outro). Nao deixe nenhum de fora.
7. falta: documentos que a peca diz juntar ou cita como prova e que nao estao na lista.
8. Nao invente fatos nem datas; use so a peca e a leitura dos documentos.
"""


def planejar(nome_cliente, peca_nome, peca_txt, docs):
    linhas = []
    for d in docs:
        l = d["leitura"]
        linhas.append(json.dumps({"id": d["id"], **{k: l.get(k, "") for k in (
            "tipo", "emitente", "destinatario", "data", "numero", "origem", "destino", "horarios",
            "valor", "pessoas", "resumo", "legivel")}}, ensure_ascii=False))
    prompt = (INSTRUCAO_PLANO + f"\nCLIENTE: {nome_cliente}\n\n=== PECA ({peca_nome}) ===\n{peca_txt[:120000]}"
              f"\n\n=== DOCUMENTOS ({len(docs)}) ===\n" + "\n".join(linhas))
    return claude(prompt, "opus", timeout=2400)


# ---------------------------------------------------------------- 3) PDFs
def _pagina_imagem(pdf, path, lado, qualidade):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((lado, lado))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=qualidade, optimize=True)
    w, h = im.size
    esc = 842.0 / max(w, h)                      # lado maior = altura de uma folha A4
    pg = pdf.new_page(width=w * esc, height=h * esc)
    pg.insert_image(pg.rect, stream=buf.getvalue())


def _montar(paths, lado, qualidade):
    pdf = fitz.open()
    for p in paths:
        if p.lower().endswith(".pdf"):
            pdf.insert_pdf(fitz.open(p))
        else:
            _pagina_imagem(pdf, p, lado, qualidade)
    return pdf.tobytes(garbage=4, deflate=True)


def gerar_pdf(paths, destino_sem_ext):
    """Grava o PDF abaixo do limite; se nao couber nem comprimido, divide em partes."""
    limite = LIMITE_MB * 1024 * 1024
    for lado, q in ((1800, 78), (1400, 65), (1100, 55)):
        b = _montar(paths, lado, q)
        if len(b) <= limite:
            with open(destino_sem_ext + ".pdf", "wb") as f:
                f.write(b)
            return [(destino_sem_ext + ".pdf", len(b))]
    if len(paths) == 1:
        with open(destino_sem_ext + ".pdf", "wb") as f:
            f.write(b)
        return [(destino_sem_ext + ".pdf", len(b))]
    meio = len(paths) // 2
    partes = []
    for i, pedaco in enumerate((paths[:meio], paths[meio:]), 1):
        partes += gerar_pdf(pedaco, f"{destino_sem_ext} (PARTE {i})")
    return partes


# ---------------------------------------------------------------- sumario
def sumario_txt(nome_cliente, peca_nome, plano, saidas, leitura_por_id, repetidos):
    L = [f"SUMARIO DE JUNTADA: {nome_cliente}",
         f"Montado em {datetime.now():%d/%m/%Y %H:%M} pela automacao de juntada (Sistema Escritório Modelo), a partir da peca "
         f"'{peca_nome}'. CONFERIR antes de protocolar.", "", "ORDEM DE JUNTADA",
         "No | Arquivo | Tipo no PJe | O que sustenta na peca | Paginas | Tamanho"]
    for g in plano.get("grupos", []):
        for arq, tam, pags in saidas.get(g["n"], []):
            L.append(f"{g['n']:02d} | {os.path.basename(arq)[:-4]} | {g.get('tipo_pje', '')} | "
                     f"{g.get('sustenta', '')} | {pags} | {tam / 1048576:.1f} MB")
    L += ["", "NAO ENTRA NO PROTOCOLO"]
    for x in plano.get("nao_entra", []):
        lt = leitura_por_id.get(x.get("id"), {})
        L.append(f"* {lt.get('nome') or x.get('id')}: {x.get('motivo', '')}")
    if repetidos:
        L.append(f"* {len(repetidos)} arquivo(s) repetido(s) tirados antes da leitura (lista em repetidos.csv)")
    L += ["", "AINDA FALTA"] + ([f"* {f}" for f in plano.get("falta", [])] or ["* nada"])
    if plano.get("observacoes"):
        L += ["", "OBSERVACOES", plano["observacoes"]]
    return "\n".join(L)


# ---------------------------------------------------------------- principal
def montar(cliente, arquivos, peca_id, drive=None, sem_pdf=False, status=None):
    """arquivos: [{id,name,mimeType}] ja escolhidos. Devolve dict com raiz, saida, plano, saidas, peca."""
    status = status or (lambda etapa, msg: None)
    raiz = os.path.join(BASE, slug(cliente))
    cache, saida = os.path.join(raiz, "cache"), os.path.join(raiz, "saida")
    os.makedirs(cache, exist_ok=True)
    estado_path = os.path.join(raiz, "leitura.json")
    leitura = json.load(open(estado_path, encoding="utf-8")) if os.path.exists(estado_path) else {}
    drive = drive or autenticar_google()[0]
    log(f"{len(arquivos)} arquivos separados")
    status("baixando", f"Baixando {len(arquivos)} documentos do Drive")
    itens = []
    for f in arquivos:
        ext = os.path.splitext(f["name"])[1].lower()
        if ext not in EXT_IMG + (".pdf",):
            log("  ignorado (tipo):", f["name"])
            continue
        itens.append({"id": f["id"], "nome_drive": f["name"], "path": baixar(drive, f, cache)})
    unicos, repetidos = separar_repetidos(itens)
    log(f"{len(unicos)} unicos, {len(repetidos)} repetidos")

    ler_todos(unicos, leitura, raiz, lambda: _salvar(estado_path, leitura), status)
    docs = [dict(it, leitura=leitura.get(it["md5"], {"resumo": "(nao lido)"})) for it in unicos]
    leitura_por_id = {d["id"]: d["leitura"] for d in docs}
    with open(os.path.join(raiz, "nomes_sugeridos.csv"), "w", encoding="utf-8") as f:
        f.write("id;nome_no_drive;nome_sugerido;tipo;data;resumo\n")
        for d in docs:
            l = d["leitura"]
            f.write(";".join(str(x).replace(";", ",").replace("\n", " ") for x in (
                d["id"], d["nome_drive"], l.get("nome", ""), l.get("tipo", ""), l.get("data", ""), l.get("resumo", ""))) + "\n")
    with open(os.path.join(raiz, "repetidos.csv"), "w", encoding="utf-8") as f:
        f.write("id;igual_a\n" + "".join(f"{x};{y}\n" for x, y in repetidos))

    peca_nome, peca_txt = texto_peca(drive, peca_id)
    log(f"peca: {peca_nome} ({len(peca_txt)} caracteres); planejando...")
    status("planejando", f"Lendo a peca ({peca_nome}) e montando o plano de juntada")
    plano = planejar(cliente, peca_nome, peca_txt, docs)
    if not plano or not plano.get("grupos"):
        raise RuntimeError("o plano de juntada nao voltou do Claude")
    _salvar(os.path.join(raiz, "plano.json"), plano)
    usados = {i for g in plano["grupos"] for i in g.get("docs", [])} | {x.get("id") for x in plano.get("nao_entra", [])}
    sobra = [d["id"] for d in docs if d["id"] not in usados]
    if sobra:
        log(f"  atencao: {len(sobra)} documento(s) sem destino no plano")
        plano.setdefault("nao_entra", []).extend({"id": i, "motivo": "o plano nao usou (conferir)"} for i in sobra)

    saidas = {}
    if not sem_pdf:
        status("gerando", "Gerando os PDFs")
        if os.path.isdir(saida):
            shutil.rmtree(saida)
        os.makedirs(saida)
        por_id = {d["id"]: d for d in docs}
        for g in plano["grupos"]:
            paths = [por_id[i]["path"] for i in g.get("docs", []) if i in por_id]
            if not paths:
                continue
            base = os.path.join(saida, f"{g['n']:02d} - {nome_arquivo(g['titulo'])}")
            saidas[g["n"]] = [(p, t, fitz.open(p).page_count) for p, t in gerar_pdf(paths, base)]
            log(f"  {os.path.basename(base)}: {len(paths)} doc(s), {sum(t for _, t, _ in saidas[g['n']]) / 1048576:.1f} MB")
        txt = sumario_txt(cliente, peca_nome, plano, saidas, leitura_por_id, repetidos)
        with open(os.path.join(saida, f"00 - SUMARIO DE JUNTADA - {nome_arquivo(cliente)} (NAO PROTOCOLAR).txt"),
                  "w", encoding="utf-8") as f:
            f.write(txt)
    log(f"pronto: {raiz}")
    return {"raiz": raiz, "saida": saida, "plano": plano, "saidas": saidas, "peca": peca_nome,
            "leitura_por_id": leitura_por_id, "repetidos": repetidos}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cliente", required=True)
    ap.add_argument("--pastas", default="", help="ids de pastas do Drive com os documentos separados")
    ap.add_argument("--arquivos", default="", help="ids de arquivos avulsos (documentos pessoais em PDF)")
    ap.add_argument("--peca", required=True, help="id da peca no Drive (PDF, DOCX ou Google Doc)")
    ap.add_argument("--sem-pdf", action="store_true", help="so le e planeja, nao monta os PDFs")
    a = ap.parse_args()
    drive, _ = autenticar_google()
    arquivos = []
    for pid in filter(None, a.pastas.split(",")):
        arquivos += [f for f in listar(drive, pid) if not f["mimeType"].endswith("folder")
                     and not f["mimeType"].startswith("application/vnd.google-apps")]
    for fid in filter(None, a.arquivos.split(",")):
        arquivos.append(drive.files().get(fileId=fid, fields="id,name,mimeType,size").execute())
    montar(a.cliente, arquivos, a.peca, drive=drive, sem_pdf=a.sem_pdf)


if __name__ == "__main__":
    main()
