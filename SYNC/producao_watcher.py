# -*- coding: utf-8 -*-
"""
MOTOR DA PRODUCAO DA INICIAL - pipeline Produção (18), etapa
"Producao da Inicial". Plugado em 12/08/2026 (Advogada); dorme ate ter card + credito.

DESDE 28/09/2026 (Advogada): o vigia produz PELA SKILL DA ESTEIRA, do inicio ao fim,
SEM AVAL. Ele continua dono da COLETA (passo 1 abaixo); a skill
peticao-inicial-reclamante faz os Passos 1 a 7 (peca modelo MOD_RT, documentos,
liquidacao, recortes com moldura vermelha, timbrado, Revisora IA) e o
OPERACIONAL/fechamento_inicial.py faz o Passo 8 (peca em ATOS INTERNOS, tarefa
da Advogada, PF_RT na agenda, tarefa de protocolo Dra. Advogada). Ver
producao_pela_skill abaixo. Os passos 2 a 6 abaixo sao o MOTOR ANTIGO, que so
roda com ESC_PRODUCAO_PELA_SKILL=0.

Fluxo por card novo na etapa:
  1. PRE-REQUISITOS: pasta do cliente (Drive), TRANSCRICAO do fechamento
     (insumo-mestre; Drive por nome, fallback uploads) e CCT na pasta
     (DOCUMENTOS DO CLIENTE). Pela skill, CCT ausente NAO trava: o gate da CCT
     da skill busca (regra de 11/09). No motor antigo, alerta a Advogada 1x/dia.
  2. DOSSIE (Claude): transcricao + CCT -> JSON com fatos, teses com prova,
     jornada, parametros da CCT, danos morais e pendencias [CONFERIR].
  3. LIQUIDACAO (python): formulas da casa com os parametros do dossie ->
     memoria pedido a pedido + valor da causa.
  4. PECA (Claude): instanciacao do MOD_RT (padrao cheio) com o dossie e a
     liquidacao; regras permanentes da Advogada (R$, titulos justificados,
     [CONFERIR] em vermelho, sem periculosidade por tanque). Gate de
     densidade: < 8.000 palavras -> NAO entrega; alerta.
  5. DOCX timbrado (peca + memoria + sumario) em ATOS INTERNOS + EDITAVEIS.
  6. TAREFAS ADVBOX: Advogada REVISAO DE PECA (D+4) e Advogada+Advogada PROTOCOLO
     (D+8 corridos, urgente). Agenda Google: pendente token de calendar
     (403 na VPS) - vai no corpo da tarefa como lembrete.
  7. Move o card para "Revisao" e avisa a Advogada com os links.

Toda falha alerta a Advogada e deixa o card parado (nada e protocolado sem humano).
USO: py SYNC/producao_watcher.py [--executar] | --status
Cron VPS: 0 9,15 * * *. Estado: producao_state.json.
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    TZ = None

try:
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

from dotenv import load_dotenv
load_dotenv(os.getenv(
    "ESC_CONFIG_ENV",
    r"C:\AUTOMAÇÕES\AUTOMACOES_PROJETO_COMPLETO_2026-07-19\config\.env"))
if os.path.exists("/opt/escritorio/config/.env"):
    load_dotenv("/opt/escritorio/config/.env")

import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)
import varredura_provas as vp  # Drive/zapi/timbrado helpers ja blindados
import transcricao_drive as td  # onde a transcricao mora (compartilhado c/ contratacao)

STATE = os.path.join(_DIR, "producao_state.json")
WORK = os.getenv("ESC_PRODUCAO_DIR", "/opt/escritorio/producao")
DB = vp.DB
PIPELINE_PRODUCAO = 18
ETAPA_GATILHO = "Producao da Inicial"
ETAPA_DEPOIS = "Revisao"
TEL_PAM = os.getenv("TEL_ALERTA_EQUIPE", "")   # alertas da producao (vazio = nao avisa)
MODELO = "sonnet"  # alias do CLI; producao roda pela assinatura, nao pela API
MODRT = os.getenv("ESC_MODRT", "/opt/escritorio/config/MOD_RT_RECLAMANTE.docx")
MIN_PALAVRAS = 8000

PAPEL_REVISORA, PAPEL_PROTOCOLO, PAPEL_PRODUCAO, PAPEL_TITULAR = 'revisora', 'protocolo', 'producao', 'titular'   # papeis
TASK_PROTOCOLO, TASK_REVISAO_PECA = 'PROTOCOLO', 'REVISAO DE PECA'
PRAZO_PROTOCOLO_DIAS, PRAZO_REVISAO_DIAS = 8, 4
AGENDA_EMAILS = tuple(e.strip() for e in os.getenv("AGENDA_CONVIDADOS", "").split(",") if e.strip())
MEDIADOR = "http://www3.mte.gov.br/sistemas/mediador/ConsultarInstColetivo"


def _hoje():
    return (datetime.now(TZ).date() if TZ else date.today())


def _st():
    return vp._json(STATE, {"cards": {}})


def _conversas():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    con.execute("pragma busy_timeout=8000")
    rows = con.execute("select id, contato_nome, contato_telefone from wa_conversas "
                       "where pipeline_id=? and etapa=?",
                       (PIPELINE_PRODUCAO, ETAPA_GATILHO)).fetchall()
    con.close()
    # nome do card vem do WhatsApp e chega com espaco sobrando ("Benicio Arantes de
    # Souza "), o que atrapalha a busca do processo no ADVBOX
    return [(cid, (nome or "").strip(), tel) for cid, nome, tel in rows]


def _mover(cid, etapa, executar):
    if not executar:
        print(f"    (dry-run: moveria {cid} para '{etapa}')")
        return
    con = sqlite3.connect(DB, timeout=15)
    con.execute("pragma busy_timeout=8000")
    con.execute("update wa_conversas set etapa=?, atualizado_em=? where id=?",
                (etapa, datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S") if TZ else "", cid))
    con.commit()
    con.close()


def _dias_em_producao(cid):
    """Dias desde a ultima entrada do card em Producao da Inicial (trilha gravada em UTC)."""
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
        r = con.execute("select max(criado_em) from wa_board_hist where conversa_id=? and "
                        "para_etapa=?", (cid, ETAPA_GATILHO)).fetchone()
        con.close()
        return (_hoje() - (datetime.fromisoformat(r[0][:19]) - timedelta(hours=3)).date()).days
    except Exception:
        return 0


def _alerta(st, chave, texto, executar):
    if st.get("alertas", {}).get(chave) == _hoje().isoformat():
        return
    if executar:
        vp._zapi(TEL_PAM, texto)
    print("    ALERTA: " + texto.replace("\n", " ")[:180])
    st.setdefault("alertas", {})[chave] = _hoje().isoformat()


# ---------------- pre-requisitos ----------------

def achar_arquivo(pasta_id, contem):
    q = f"'{pasta_id}' in parents and trashed=false and name contains '{contem}'"
    fs = requests.get("https://www.googleapis.com/drive/v3/files", headers=vp._H(),
                      params={"q": q, "fields": "files(id,name,mimeType)", "pageSize": 10},
                      timeout=30).json().get("files", [])
    return fs[0] if fs else None


def baixar_arquivo(fid, dest, export_txt=False):
    if export_txt:
        r = requests.get(f"https://www.googleapis.com/drive/v3/files/{fid}/export",
                         headers=vp._H(), params={"mimeType": "text/plain"}, timeout=120)
    else:
        r = requests.get(f"https://www.googleapis.com/drive/v3/files/{fid}",
                         headers=vp._H(), params={"alt": "media", "supportsAllDrives": "true"},
                         timeout=180)
    r.raise_for_status()
    open(dest, "wb").write(r.content)
    return dest


def _listar(q, fields="files(id,name,mimeType,size)"):
    return requests.get("https://www.googleapis.com/drive/v3/files", headers=vp._H(),
                        params={"q": q + " and trashed=false",
                                "orderBy": "modifiedTime desc", "pageSize": 100,
                                "fields": fields},
                        timeout=30).json().get("files", [])


def _baixar_transcricao(meta, cid):
    """Traz a transcricao para a pasta de trabalho SEMPRE como texto legivel.

    Antes o .docx era baixado cru e o `_ler_texto` abria como texto puro, o que
    entrega lixo binario para o dossie. Converte igual o intake faz.
    """
    fid, nome_arq = meta["id"], (meta.get("name") or "")
    mime = meta.get("mimeType") or ""
    if mime == "application/vnd.google-apps.document":
        return baixar_arquivo(fid, os.path.join(WORK, f"{cid}_transcricao.txt"), True)
    if mime.endswith("wordprocessingml.document") or nome_arq.lower().endswith(".docx"):
        bruto = baixar_arquivo(fid, os.path.join(WORK, f"{cid}_transcricao.docx"))
        from docx import Document
        dest = os.path.join(WORK, f"{cid}_transcricao.txt")
        open(dest, "w", encoding="utf-8").write(
            "\n".join(p.text for p in Document(bruto).paragraphs))
        return dest
    ext = os.path.splitext(nome_arq)[1] or ".txt"
    return baixar_arquivo(fid, os.path.join(WORK, f"{cid}_transcricao{ext}"))


def buscar_transcricao(nome, cid, pastas=()):
    """Transcricao = insumo-mestre (Advogada 12/08). Mesma receita do intake.

    Ordem, da fonte mais confiavel para a menos (regras no `transcricao_drive`,
    compartilhado com o contratacao_watcher para o conserto valer nos dois):

      1. LINK NA FICHA do card. Combinado da Advogada em 12/08/2026: quando o lead
         fecha ainda nao existe pasta do cliente, entao a transcricao sobe
         solta no Drive e o link vai na ficha. Foi um humano que apontou aquele
         arquivo para aquele cliente, nao ha fonte melhor.
      2. LISTAGEM das pastas do cliente, RAIZ INCLUSA - o intake move a
         transcricao para a raiz da pasta, nao para uma subpasta.
      3. PASTA DE ENTRADA de transcricoes (Advogada 01/09/2026): onde o time sobe o
         arquivo enquanto o cliente ainda nao tem pasta oficial. Aqui a escolha
         e pelo NOME DO CLIENTE, porque a entrada guarda varios clientes.
      4. Nome no Drive inteiro (ultimo recurso).

    Foi a falta de (1) e (2) que travou o Benicio Arantes de Lima em 18/08/2026: a
    transcricao estava na raiz da pasta dele, com o link na ficha, chamada
    "Benicio lead mororista.txt" - e o motor alertava "nao achei" duas vezes ao dia.
    """
    os.makedirs(WORK, exist_ok=True)
    # 1) links da ficha do card
    metas = []
    for fid in td.ids_da_ficha(DB, cid):
        try:
            metas.append(requests.get(
                f"https://www.googleapis.com/drive/v3/files/{fid}", headers=vp._H(),
                params={"fields": "id,name,mimeType,size"}, timeout=30).json())
        except Exception as e:
            print(f"    (link da ficha {fid[:12]}... inacessivel: {str(e)[:80]})")
    escolhido = td.escolher(metas)
    if escolhido:
        print(f"    transcricao pela FICHA do card: {escolhido.get('name')}")
        try:
            return _baixar_transcricao(escolhido, cid)
        except Exception as e:
            print(f"    (baixar pela ficha falhou: {str(e)[:120]})")
    # 2) listagem das pastas do cliente (raiz inclusa); o filtro de nome e
    #    LOCAL de proposito, senao a armadilha do prefixo de palavra volta
    for q in td.queries_na_pasta(pastas):
        try:
            escolhido = td.escolher(_listar(q), exigir_pista=True)
        except Exception as e:
            print(f"    (listar pasta falhou: {str(e)[:120]})")
            continue
        if escolhido:
            print(f"    transcricao na pasta do cliente: {escolhido.get('name')}")
            try:
                return _baixar_transcricao(escolhido, cid)
            except Exception as e:
                print(f"    (baixar da pasta falhou: {str(e)[:120]})")
    # 3) pasta de entrada de transcricoes; escolha pelo NOME DO CLIENTE porque
    #    ali convivem transcricoes de varios clientes ao mesmo tempo
    q_entrada = td.query_na_entrada()
    if q_entrada:
        try:
            escolhido = td.escolher_na_entrada(_listar(q_entrada), nome)
        except Exception as e:
            print(f"    (entrada de transcricoes inacessivel: {str(e)[:120]})")
            escolhido = None
        if escolhido:
            print(f"    transcricao na ENTRADA de transcricoes: {escolhido.get('name')}")
            try:
                return _baixar_transcricao(escolhido, cid)
            except Exception as e:
                print(f"    (baixar da entrada falhou: {str(e)[:120]})")
    # 4) ultimo recurso: nome do cliente no Drive inteiro
    for q in td.queries_por_nome(nome):
        try:
            fs = _listar(q)
        except Exception as e:
            print(f"    (busca no Drive falhou: {str(e)[:120]})")
            return None
        if fs:
            print(f"    transcricao achada pelo nome: {fs[0].get('name')}")
            try:
                return _baixar_transcricao(fs[0], cid)
            except Exception as e:
                print(f"    (baixar transcricao falhou: {str(e)[:120]})")
                return None
    return None


# ---------------- insumos completos (ordem da Advogada, 24/09/2026) ----------------
# Caso Heitor Valença (conversa 1485): o motor lia SO a transcricao da pasta e
# a primeira CCT que o Drive devolvesse. O salario, a empresa, o CNPJ e as datas
# estavam na aba Entrevista e no TRCT/ficha da CTPS, que o motor nunca abria; a
# liquidacao caiu com "salario ausente" e a CCT pega foi a ALTERNATIVA (SETTRIM)
# em vez da da Alimentacao. Ordem obrigatoria desde entao:
#   1. transcricao (arquivo da pasta + ligacoes coladas na nota do card);
#   2. entrevista integral do Sistema Escritório Modelo (ficha_entrevista) + Ficha do Drive;
#   3. todos os documentos da pasta (lidos por visao pelo Claude, sem OCR);
#   4. CBO da CTPS + sede/CNAE da empresa;
#   5. CCT escolhida por indicacao (entrevista/LEIA-ME), nunca "a primeira".

DOC_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".webp")
DOC_PRIORIDADE = ("CONTRATO_TRABALHO", "CTPS", "TRCT", "HOLERITE", "RECIBO",
                  "EXTRATO_FGTS", "EXTRATO_BANCARIO", "CNH", "COMPROVANTE", "CTE",
                  "FOLHA_PONTO", "DIARIO", "OUTRO", "FOTO")
MAX_DOCS, MAX_FOTOS = 24, 12


def notas_do_card(cid):
    """Ligacoes do WhatsApp e anotacoes pos-ligacao coladas na nota do card."""
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
        row = con.execute("select notas from wa_conversas where id=?", (cid,)).fetchone()
        con.close()
        return (row[0] or "") if row else ""
    except Exception as e:
        print(f"    (notas do card indisponiveis: {str(e)[:80]})")
        return ""


def entrevista_do_card(cid):
    """Aba Entrevista (ficha_entrevista): so os campos preenchidos."""
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
        rows = con.execute("select campo, valor from ficha_entrevista where conversa_id=?",
                           (cid,)).fetchall()
        con.close()
    except Exception as e:
        print(f"    (entrevista indisponivel: {str(e)[:80]})")
        return {}
    return {c: (v or "").strip() for c, v in rows if (v or "").strip()}


def ficha_drive(raiz_id):
    """Ficha do Cliente na raiz da pasta. Ficha oca ({{PREENCHER...) nao conta."""
    try:
        for f in _listar(f"'{raiz_id}' in parents and name contains 'Ficha'"):
            if f.get("mimeType") == "application/vnd.google-apps.document":
                r = requests.get(f"https://www.googleapis.com/drive/v3/files/{f['id']}/export",
                                 headers=vp._H(), params={"mimeType": "text/plain"}, timeout=60)
                t = r.text
                if "{{PREENCHER" in t:
                    print("    ficha do Drive OCA (modelo nao preenchido): ignorada")
                    return ""
                return t[:30000]
    except Exception as e:
        print(f"    (ficha do Drive inacessivel: {str(e)[:80]})")
    return ""


def _id_midia(nome):
    m = re.search(r"_(\d{4,})\.[a-z]+$", nome.lower())
    return m.group(1) if m else nome


def documentos_da_pasta(docs_id, cid):
    """Baixa os documentos (sem duplicata: a varredura antiga subia 2 copias da
    mesma midia) para WORK/<cid>_docs e devolve a lista de caminhos locais."""
    # paginado: a pasta do Heitor tinha 141 arquivos e o _listar para em 100
    # SUBPASTAS (29/09/2026): cliente com acao contra mais de uma empresa (Silvano: TRANSPORTADORA A e
    # TRANSPORTADORA B) tem os documentos em DOCUMENTOS DO CLIENTE/<EMPRESA>. Antes so a
    # raiz era lida e a inicial saia sem holerite/ponto. Agora desce nas subpastas; o
    # arquivo de subpasta vai com o nome dela na frente ("TRANSPORTADORA A S.A. - HOLERITE...").
    fs, filas, pastas_vistas = [], [(docs_id, "")], set()
    while filas:
        pasta_id, sub = filas.pop(0)
        if pasta_id in pastas_vistas or len(pastas_vistas) >= 30:
            continue
        pastas_vistas.add(pasta_id)
        token = None
        while True:
            params = {"q": f"'{pasta_id}' in parents and trashed=false", "pageSize": 200,
                      "fields": "nextPageToken,files(id,name,mimeType,modifiedTime)"}
            if token:
                params["pageToken"] = token
            j = requests.get("https://www.googleapis.com/drive/v3/files", headers=vp._H(),
                             params=params, timeout=30).json()
            for f in j.get("files", []):
                if f.get("mimeType") == "application/vnd.google-apps.folder":
                    filas.append((f["id"], (sub + " - " if sub else "") + f.get("name", "").strip()))
                else:
                    f["_sub"] = sub
                    fs.append(f)
            token = j.get("nextPageToken")
            if not token:
                break
    vistos, cands = set(), []
    for f in sorted(fs, key=lambda x: x.get("modifiedTime", ""), reverse=True):
        nome = f.get("name", "")
        if not nome.lower().endswith(DOC_EXT) or nome.upper().startswith("CCT"):
            continue
        if re.match(r"\d{4}-\d{2}-\d{2}_imagem_", nome):
            continue  # midia crua, ja existe a copia classificada
        chave = _id_midia(nome)
        if chave == nome:
            chave = f["_sub"] + "/" + nome  # renomeado a mao: mesmo nome em empresas diferentes nao e duplicata
        if chave in vistos:
            continue
        vistos.add(chave)
        # nome escrito a mao ("FOLHA DE PONTO", "EXTRATO BANCARIO") conta igual ao do robo (FOLHA_PONTO)
        norm = re.sub(r"[\s\-]+", "_", nome.upper()).replace("_DE_", "_")
        pri = next((i for i, p in enumerate(DOC_PRIORIDADE) if nome.upper().startswith(p)
                    or p in nome.upper() or p in norm), len(DOC_PRIORIDADE))
        cands.append((pri, (f["_sub"] + " - " if f["_sub"] else "") + nome, f["id"]))
    cands.sort()
    docs = [c for c in cands if c[0] < DOC_PRIORIDADE.index("FOTO")][:MAX_DOCS]
    # vaga que os documentos nao usam vai para as fotos/prints (o total segue no maximo MAX_DOCS+MAX_FOTOS)
    fotos = [c for c in cands if c[0] >= DOC_PRIORIDADE.index("FOTO")][:MAX_FOTOS + MAX_DOCS - len(docs)]
    pasta = os.path.join(WORK, f"{cid}_docs")
    os.makedirs(pasta, exist_ok=True)
    locais = []
    for _, nome, fid in docs + fotos:
        dest = os.path.join(pasta, re.sub(r"[^\w.\- ]", "_", nome))
        try:
            if not os.path.exists(dest):
                baixar_arquivo(fid, dest)
            locais.append(dest)
        except Exception as e:
            print(f"    (documento {nome[:40]} nao baixou: {str(e)[:80]})")
    print(f"    documentos: {len(docs)} + fotos: {len(fotos)} (sem duplicatas)")
    return locais


def escolher_cct(docs_id, entrevista):
    """Retorna (lista_de_ccts, motivo). Nunca "a primeira que o Drive devolver".
    - uma CCT na pasta: ela;
    - varias: as indicadas pelo numero de registro na entrevista (obs_empresa)
      ou no LEIA-ME; sem indicacao, todas as PRINCIPAIS (a vigencia de cada uma
      cobre um trecho do contrato) e o dossie aplica a Sumula 374 pelo CBO e
      pelo CNAE da empresa;
    - arquivo marcado ALTERNATIVA nunca entra sozinho;
    - enquadramento pendente de decisao humana NAO trava: vira pendencia em
      vermelho na peca (regra da casa). So trava sem nenhuma CCT principal."""
    ccts = [f for f in _listar(f"'{docs_id}' in parents and name contains 'CCT'")
            if f.get("name", "").lower().endswith(".pdf")]
    if not ccts:
        return None, "sem_cct"
    principais = [f for f in ccts if "ALTERNATIVA" not in f["name"].upper()]
    if not principais:
        return None, "so_alternativa"
    if len(ccts) == 1:
        return ccts, "unica"
    indicacao = (entrevista.get("obs_empresa") or "") + " " + (entrevista.get("cct") or "")
    leia = next((f for f in _listar(f"'{docs_id}' in parents and name contains 'LEIA'")), None)
    if leia:
        try:
            indicacao += " " + requests.get(
                f"https://www.googleapis.com/drive/v3/files/{leia['id']}", headers=vp._H(),
                params={"alt": "media"}, timeout=60).text[:20000]
        except Exception:
            pass
    pendente = re.search(r"pendente de confirma|decis[aã]o da dra", indicacao, re.I)
    regs = set(re.findall(r"MG\d{6}/\d{4}|[A-Z]{2}\d{6}/\d{4}", indicacao))
    marcadas = [f for f in principais
                if any(r.replace("/", "-") in f["name"] or r in f["name"] for r in regs)]
    escolha = marcadas or principais
    if pendente:
        return escolha, "indicada_pendente" if marcadas else "principais_pendente"
    return escolha, "indicada" if marcadas else "principais"


# ---------------- estagios de IA ----------------
# Decisao da Advogada (12/08/2026): a producao roda pelo Claude Code instalado na
# VPS (assinatura Max), NUNCA pelos creditos da API Anthropic. Por isso a
# chamada e via CLI e a ANTHROPIC_API_KEY e REMOVIDA do ambiente do subprocesso
# (se ficasse, o CLI cobraria da API silenciosamente).

CLAUDE_BIN = os.getenv("ESC_CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))


def _claude_cli(prompt, timeout_s=3600):
    """Gera texto pelo Claude Code da VPS. So leitura de arquivos permitida."""
    import subprocess
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # garante assinatura, nao API
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "64000"  # peca tem 9-13k palavras
    r = subprocess.run(
        [CLAUDE_BIN, "-p", "--model", "sonnet", "--allowedTools", "Read"],
        input=prompt.encode("utf-8"), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, cwd=WORK, timeout=timeout_s)
    if r.returncode != 0:
        raise RuntimeError("claude cli rc=%d: %s"
                           % (r.returncode, r.stderr.decode("utf-8", "replace")[:300]))
    return r.stdout.decode("utf-8", "replace")


def _ler_texto(path):
    if path.endswith(".pdf"):
        try:
            import pypdf
            rd = pypdf.PdfReader(path)
            t = "\n".join((p.extract_text() or "") for p in rd.pages)
            if len(t.strip()) > 200:
                return t
        except Exception:
            pass
        return None  # pdf escaneado: mandar como document block
    return open(path, encoding="utf-8", errors="replace").read()


def gerar_dossie(transcricao_path, cct_paths, nome, tel, notas="", entrevista=None,
                 ficha="", docs=(), motivo_cct=""):
    """Dossie na ORDEM da Advogada (24/09/2026): 1 transcricao, 2 entrevista, 3
    documentos, 4 CBO + sede/CNAE, 5 CCT. Precedencia para DADO (salario,
    datas, CNPJ, CBO): documento > entrevista > transcricao; divergencia vira
    pendencia, nunca e resolvida em silencio."""
    partes = ["ORDEM DE LEITURA OBRIGATORIA: (1) transcricoes, (2) entrevista do "
              "sistema, (3) TODOS os documentos listados (abra cada um com a ferramenta "
              "Read; imagens e PDFs escaneados se leem por visao), (4) CBO da CTPS/ficha "
              "de anotacoes e sede/CNAE da empresa, (5) CCT."]
    t = _ler_texto(transcricao_path) if transcricao_path else None
    if t:
        partes.append(f"(1) TRANSCRICAO DA REUNIAO/LIGACAO DE FECHAMENTO:\n{t[:120000]}")
    elif transcricao_path:
        partes.append("(1) TRANSCRICAO: leia o PDF em "
                      f"{os.path.abspath(transcricao_path)} (use a ferramenta Read).")
    if notas.strip():
        partes.append(f"(1b) LIGACOES E NOTAS DO CARD (podem conter chamadas que nao estao "
                      f"no arquivo acima):\n{notas[:60000]}")
    if entrevista:
        partes.append("(2) ENTREVISTA DO SISTEMA Escritório Modelo (preenchida pelo time; campos vazios "
                      "omitidos):\n" + json.dumps(entrevista, ensure_ascii=False, indent=0))
    if ficha.strip():
        partes.append(f"(2b) FICHA DO CLIENTE (Drive):\n{ficha}")
    if docs:
        partes.append("(3) DOCUMENTOS DA PASTA DO CLIENTE (abra TODOS com Read antes de "
                      "responder):\n" + "\n".join(os.path.abspath(d) for d in docs))
    for cp in cct_paths or ():
        tc = _ler_texto(cp)
        rotulo = os.path.basename(cp)
        if tc:
            partes.append(f"(5) CCT {rotulo} (texto integral):\n{tc[:60000]}")
        else:
            partes.append(f"(5) CCT {rotulo}: leia o PDF em {os.path.abspath(cp)} (Read).")
    if motivo_cct.endswith("pendente"):
        partes.append("ATENCAO: o enquadramento sindical esta marcado como pendente de "
                      "confirmacao da Dra. Advogada. Aplique a Sumula 374 do TST pelo CBO e "
                      "pelo CNAE e registre em pendencias: 'confirmar enquadramento'.")
    partes.append((
        f"Cliente: {nome} (tel {tel}). Extraia o DOSSIE para a peticao inicial trabalhista "
        "de motorista (padrao Escritório Modelo). PRECEDENCIA DOS DADOS: documento > entrevista > "
        "transcricao (ex.: salario da ficha da CTPS/TRCT vence o numero dito na ligacao); "
        "toda divergencia entre fontes vai em pendencias com as duas versoes. "
        "Responda SOMENTE JSON valido com as chaves: "
        "dados_pessoais {nome_completo, cpf, rg, estado_civil, email, endereco, cidade, uf, cep}; "
        "reclamada {razao_social, nome_fantasia, cnpj, cnae_principal, sede_endereco, sede_cidade, sede_uf, vara_sugerida}; "
        "contrato {admissao, rescisao, tipo_rescisao, salario_registrado, comissoes_por_fora, funcao, cbo, veiculo}; "
        "enquadramento {cct_aplicada (numeros de registro), fundamento (CBO x CNAE x Sumula 374), confirmar (bool)}; "
        "fontes {dado: documento de onde saiu}; "
        "jornada {horas_dia, dias_semana, descricao, controles (como a empresa controlava), fraude_ponto (bool+descricao)}; "
        "cct {sindicatos, vigencia, piso_aplicavel_nome, piso_valor, adicional_he_pct, "
        "diaria_almoco, diaria_jantar, diaria_pernoite, ticket_dia, multa_convencional_regra, clausulas_citadas}; "
        "fatos_cronologicos [paragrafos completos, densos, na 3a pessoa 'a Reclamante'/'o Reclamante', "
        "sem inventar nada, citando datas e valores com R$]; "
        "teses [{chave (horas_extras|interjornada|intersemanal_35h|intrajornada|tempo_espera|"
        "adicional_noturno|diarias_cct|piso|comissoes_por_fora|descontos_indevidos|diferencas_rescisorias), "
        "fatos (paragrafos), prova, parametros_numericos}]; "
        "danos_morais [{titulo, fatos (paragrafos densos), valor_sugerido}]; "
        "danos_materiais [{titulo, fatos, valor}]; "
        "pendencias [itens que precisam conferencia humana - virarao [CONFERIR ...] em vermelho]; "
        "testemunhas_email {tem_testemunhas, email}. "
        "REGRAS: nao incluir periculosidade por tanque suplementar (tese vedada); "
        "TODO campo numerico (horas_dia, dias_semana, piso_valor, adicional_he_pct, diarias, ticket, valores) deve ser NUMERO puro (ex.: 2391.97, 7), NUNCA texto nem explicacao - detalhes vao nos campos de descricao; "
        "todo valor com R$; nao prometer resultado; basear-se APENAS no material."))
    texto = _claude_cli("\n\n".join(partes), timeout_s=1800)
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        raise ValueError("dossie: resposta sem JSON (%s...)" % texto[:200])
    return json.loads(m.group(0))


def _dinheiro(v, padrao=0.0):
    """Extrai o primeiro valor monetario/numerico de qualquer coisa que o
    modelo devolva: 2391.97, "R$ 2.391,97 (piso de maio/2025)", "50%", None.
    Caso Ezequiel (11/09/2026): piso veio com explicacao entre parenteses
    e o float() puro derrubava a liquidacao inteira."""
    if v is None:
        return padrao
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v)
    m = re.search(r"\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d+(?:\.\d+)?", t)
    if not m:
        return padrao
    n = m.group(0)
    if "," in n:
        n = n.replace(".", "").replace(",", ".")
    return float(n)


def liquidar(dossie):
    """Liquidacao generica por estimativa (formulas da casa)."""
    c = dossie.get("cct", {})
    ct = dossie.get("contrato", {})
    piso = _dinheiro(c.get("piso_valor")) or None
    sal = _dinheiro(ct.get("salario_registrado"))
    # dado faltante vira pendencia em vermelho, nao derruba a producao (regra da
    # casa; caso Heitor 24/09/2026). So para se faltarem os dois.
    pend = dossie.setdefault("pendencias", [])
    if not sal and piso:
        sal = piso
        pend.append("Salário registrado não encontrado nos documentos, na entrevista "
                    "nem na transcrição: liquidação feita sobre o piso da CCT. CONFERIR "
                    "na CTPS/holerite.")
    elif sal and not piso:
        piso = sal
        pend.append("Piso da CCT não identificado: liquidação feita sobre o salário "
                    "registrado. CONFERIR a cláusula de piso.")
    if not piso or not sal:
        raise ValueError("liquidacao: sem salario e sem piso (procurado em documentos, "
                         "entrevista, transcricao e CCT)")
    j = dossie.get("jornada", {})
    horas_dia = _dinheiro(j.get("horas_dia"), 12) or 12
    ds = j.get("dias_semana")
    # "De domingo a domingo" etc.: texto vira numero (caso Ezequiel 11/09)
    if isinstance(ds, str) and "domingo a domingo" in ds.lower():
        dias_sem = 7
    else:
        dias_sem = int(_dinheiro(ds, 6) or 6)
    dias_sem = min(max(dias_sem, 1), 7)
    try:
        adm = date.fromisoformat(str(ct.get("admissao"))[:10])
        res = date.fromisoformat(str(ct.get("rescisao"))[:10])
    except Exception:
        raise ValueError("liquidacao: datas de admissao/rescisao invalidas")
    meses = max(0.5, (res - adm).days / 30)
    dias_uteis = round(meses * (26 if dias_sem >= 6 else 22))
    com_mes = 500.0 if ct.get("comissoes_por_fora") else 0.0
    # base = o maior entre salario registrado e piso (antes era SEMPRE o piso,
    # o que subliquidava quem ganha acima dele, como o Heitor: 2.177,97 x 1.675,36)
    sal_dev = max(piso, sal) + com_mes
    vh = sal_dev / 220
    he_pct = (_dinheiro(c.get("adicional_he_pct"), 50) or 50) / 100
    v_he = vh * (1 + he_pct)
    L = []

    def add(nome, memoria, valor):
        L.append((nome, memoria, round(valor, 2)))

    chaves = {t.get("chave") for t in dossie.get("teses", [])}
    if "piso" in chaves and piso > sal:
        d = (piso - sal) * meses
        add("Diferencas de piso (CCT)", f"({piso:.2f}-{sal:.2f}) x {meses:.2f}m", d)
        add("Reflexos das diferencas de piso", "13o+ferias1/3+aviso+FGTS40%",
            d * (1/12 + 1.3333/12 + 1/12) + d * 0.08 * 1.4)
    if "comissoes_por_fora" in chaves and com_mes:
        ctot = com_mes * meses
        add("Reflexos das comissoes por fora", "DSR+13o+ferias+aviso+FGTS40%",
            ctot * (1/6) + ctot * (1/12 + 1.3333/12 + 1/12) + ctot * 0.08 * 1.4)
    if "horas_extras" in chaves:
        heh = max(0, horas_dia - 8) * dias_uteis
        hev = heh * v_he
        add("Horas extras", f"{heh:.0f}h x {v_he:.2f}", hev)
        add("Reflexos HE em DSR", "HE/6", hev / 6)
        hed = hev * (1 + 1/6)
        add("Reflexos HE+DSR demais verbas", "13o+ferias+aviso+FGTS40%",
            hed * (1/12 + 1.3333/12 + 1/12) + hed * 0.08 * 1.4)
    if "intrajornada" in chaves:
        add("Intrajornada suprimida (1h/dia +50%)", f"{dias_uteis} x {v_he:.2f}", dias_uteis * v_he)
    if "interjornada" in chaves:
        add("Interjornada violada (8x3h/mes)", f"{8*3*meses:.0f}h x {v_he:.2f}", 8 * 3 * meses * v_he)
    if "intersemanal_35h" in chaves:
        add("Descanso intersemanal 35h (estimativa)", "ADI 5322", 2000.00)
    if "adicional_noturno" in chaves:
        add("Adicional noturno (20h/mes)", f"{20*meses:.0f}h x 20% ficta",
            20 * meses * vh * 0.20 * (60/52.5))
    if "diarias_cct" in chaves:
        pern = _dinheiro(c.get("diaria_pernoite"))
        jant = _dinheiro(c.get("diaria_jantar"))
        add("Diarias CCT nao pagas", f"pernoite {pern} x {(res-adm).days}d + jantar {jant} x {dias_uteis}",
            pern * (res - adm).days + jant * dias_uteis)
    if "descontos_indevidos" in chaves:
        add("Descontos indevidos (dobra, estimativa)", "art. 462", 600.00)
    if "diferencas_rescisorias" in chaves:
        base = sal_dev + (L and next((v for n, m, v in L if n.startswith("Horas extras")), 0) * (1+1/6) / meses or 0)
        dev = base + base * 5/12 + base * 5/12 * 1.3333 + base/30
        add("Diferencas rescisorias (estimativa)", f"base {base:.2f}", max(0, dev - 3000))
    add("Multa convencional (estimativa)", c.get("multa_convencional_regra") or "10% do piso x infracoes",
        piso * 0.10 * 6)
    for dm in dossie.get("danos_morais", []):
        add(f"Dano moral - {dm.get('titulo')}", "arbitramento",
            _dinheiro(dm.get("valor_sugerido"), 10000.0))
    for dm in dossie.get("danos_materiais", []):
        add(f"Dano material - {dm.get('titulo')}", "estimativa",
            _dinheiro(dm.get("valor")))
    total = round(sum(v for _, _, v in L), 2)
    return L, total


def gerar_peca(dossie, L, total):
    """Instancia o MOD_RT via Claude: preserva os paragrafos-padrao e injeta
    apenas fatos/teses do caso. Sai em blocos marcados para montagem DOCX."""
    from docx import Document
    tpl = "\n".join(p.text for p in Document(MODRT).paragraphs if p.text.strip())
    liq = "\n".join(f"- {n}: R$ {v:,.2f} [{m}]".replace(",", "X").replace(".", ",").replace("X", ".")
                    for n, m, v in L)
    prompt = (
        "Voce e a IVA, redatora junior do Escritório Modelo Advogados. INSTANCIE a peticao inicial "
        "trabalhista abaixo a partir do TEMPLATE-MESTRE (MOD_RT), do DOSSIE e da LIQUIDACAO. "
        "METODO (obrigatorio): preservar INTEGRALMENTE os paragrafos-padrao, sumulas e "
        "ementas do template (nunca resumir, nunca trocar ementa por mencao); ativar SOMENTE "
        "as teses presentes no dossie; substituir apenas os FATOS pelos do caso; remover "
        "secoes sem prova (inclusive doenca ocupacional, rescisao indireta se dispensa "
        "imotivada, grupo economico se empregadora unica, e SEMPRE remover periculosidade "
        "por tanque). REGRAS DA CASA: 3a pessoa (a Reclamante/a Reclamada); todo valor com "
        "R$; pontos a conferir como [CONFERIR ...]; pedidos numerados com valor e topico; "
        "valor da causa = soma da liquidacao; assinatura Advogada Responsável OAB/SP "
        "000.000, Cidade. DENSIDADE: 9.000-13.000 palavras.\n"
        "FORMATO DE SAIDA (obrigatorio): uma linha por paragrafo, prefixada por "
        "##H1## (titulo), ##H2## (subtitulo), ##CIT## (citacao/ementa) ou ##P## (normal).\n\n"
        f"=== DOSSIE ===\n{json.dumps(dossie, ensure_ascii=False)[:60000]}\n\n"
        f"=== LIQUIDACAO (valor da causa R$ {total:,.2f}) ===\n{liq}\n\n"
        f"=== TEMPLATE-MESTRE ===\n{tpl}")
    texto = _claude_cli(prompt, timeout_s=3600)
    blocos = []
    for ln in texto.splitlines():
        ln = ln.strip()
        for tag, tipo in (("##H1##", "h1"), ("##H2##", "h2"), ("##CIT##", "cit"), ("##P##", "n")):
            if ln.startswith(tag):
                blocos.append((tipo, ln[len(tag):].strip()))
                break
    palavras = sum(len(t.split()) for _, t in blocos)
    return blocos, palavras


def montar_docx(blocos, dest):
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    doc = Document(vp.TIMBRADO)
    for el in list(doc.element.body):
        if not el.tag.endswith("sectPr"):
            doc.element.body.remove(el)
    doc.styles["Normal"].font.name = "Montserrat"
    doc.styles["Normal"].font.size = Pt(11)
    VERM = RGBColor(0xC0, 0x00, 0x00)
    for tipo, texto in blocos:
        p = doc.add_paragraph()
        f = p.paragraph_format
        f.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
        f.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        for parte in re.split(r"(\[[^\]]+\])", texto):
            if not parte:
                continue
            r = p.add_run(parte)
            r.font.name = "Montserrat"
            r.font.size = Pt(11)
            r.bold = tipo in ("h1", "h2")
            r.italic = tipo == "cit"
            if parte.startswith("[") and parte.endswith("]"):
                r.font.color.rgb = VERM
                r.bold = True
        if tipo == "cit":
            f.left_indent = Cm(4)
        elif tipo == "n":
            f.first_line_indent = Cm(7)
        elif tipo == "h1":
            f.space_before = Pt(12)
    doc.save(dest)


def upload_pacote(nome, atos_id, peca_path, L, total, dossie, executar):
    """Sobe peca + memoria + sumario (DOCX timbrado + EDITAVEL) em ATOS INTERNOS."""
    MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    links = {}
    corpo = open(peca_path, "rb").read()
    primeiro = nome.split()[0].upper()
    fid = vp.upload_drive(atos_id, f"RT_{primeiro}_ESBOCO_AUTOMATICO_TIMBRADO.docx", corpo, MIME)
    links["peca"] = fid
    meta = json.dumps({"name": f"RT_{primeiro}_ESBOCO (EDITAVEL)", "parents": [atos_id],
                       "mimeType": "application/vnd.google-apps.document"})
    b = "xxB"
    body = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{b}\r\nContent-Type: {MIME}\r\n\r\n").encode() + corpo + f"\r\n--{b}--".encode()
    r = requests.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id",
                      headers={**vp._H(), "Content-Type": f"multipart/related; boundary={b}"},
                      data=body, timeout=300)
    links["peca_editavel"] = r.json().get("id")
    # memoria de calculo (tabela simples em blocos)
    blocos = [("h1", "MEMÓRIA DE CÁLCULO POR ESTIMATIVA"), ("h1", nome.upper())]
    for n, m, v in L:
        blocos.append(("n", f"{n}: R$ {v:,.2f} — memória: {m}".replace(",", "X").replace(".", ",").replace("X", ".")))
    blocos.append(("h2", f"VALOR DA CAUSA: R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")))
    mem_path = os.path.join(WORK, "memoria_tmp.docx")
    montar_docx(blocos, mem_path)
    links["memoria"] = vp.upload_drive(atos_id, f"MEMORIA_CALCULO_{primeiro}_TIMBRADO.docx",
                                       open(mem_path, "rb").read(), MIME)
    # sumario de documentos: ja mantido vivo pela varredura de provas (nota)
    return links


def tarefas_advbox(nome, links_txt, executar):
    """Cria as tarefas no ADVBOX e retorna (prazo_revisao, prazo_protocolo)
    para o estado do card - o relatorio diario mostra esses prazos finais."""
    sys.path.insert(0, os.path.join(os.path.dirname(_DIR), "INTEGRACOES"))
    import advbox_integration as ab
    proc = ab.buscar_processo(nome=nome)
    lawsuit_id = None
    if proc and isinstance(proc, list):
        lawsuit_id = proc[0].get("id")
    elif isinstance(proc, dict):
        lawsuit_id = proc.get("id")
    if not lawsuit_id:
        raise ValueError(f"processo de '{nome}' nao encontrado no ADVBOX")
    hoje = _hoje()
    prazo_rev = hoje + timedelta(days=PRAZO_REVISAO_DIAS)
    prazo_prot = hoje + timedelta(days=PRAZO_PROTOCOLO_DIAS)
    if not executar:
        print(f"    (dry-run: tarefas da Advogada: revisao {prazo_rev}, protocolo {prazo_prot})")
        return prazo_rev.isoformat(), prazo_prot.isoformat()
    ab.criar_publicacao(
        lawsuit_id=lawsuit_id, task_id=TASK_REVISAO_PECA, guest_ids=[PAPEL_PRODUCAO],
        comments=(f"REVISAO DE PECA - PETICAO INICIAL (ESBOCO AUTOMATICO)\n{nome}\n\n"
                  f"A automacao produziu o esboco completo (MOD_RT, liquidado, timbrado).\n"
                  f"{links_txt}\nTudo em VERMELHO na peca exige sua conferencia.\n"
                  f"Devolutiva ate {prazo_rev:%d/%m/%Y}; protocolo previsto {prazo_prot:%d/%m/%Y}.\n"
                  "Assistente IA do escritorio."),
        from_id=str(PAPEL_TITULAR), date_deadline=prazo_rev.isoformat())
    ab.criar_publicacao(
        lawsuit_id=lawsuit_id, task_id=TASK_PROTOCOLO, guest_ids=[PAPEL_PROTOCOLO, PAPEL_REVISORA],
        comments=(f"PROTOCOLO - PETICAO INICIAL\n{nome}\n\nPrazo FINAL: {prazo_prot:%d/%m/%Y} "
                  f"({PRAZO_PROTOCOLO_DIAS} dias corridos de {hoje:%d/%m/%Y}).\n"
                  f"Aguarda a devolutiva da revisao da Advogada ({prazo_rev:%d/%m/%Y}).\n{links_txt}\n"
                  f"AGENDA: convidar {', '.join(AGENDA_EMAILS)} (token de calendar pendente na VPS - "
                  "criar o evento manualmente por enquanto)."),
        from_id=str(PAPEL_TITULAR), date_deadline=prazo_prot.isoformat(), urgent=True)
    return prazo_rev.isoformat(), prazo_prot.isoformat()


# ---------------- producao pela SKILL da esteira (Advogada, 28/09/2026) ----------------
# "A gente e para voce utilizar a skill de producao de inicial do proprio fluxo, que e
# sem o aval: pega a nossa peca modelo, verifica os documentos, usa a skill de recortes
# e depois faz as providencias finais: agenda e as tarefas no ADVBOX para a Advogada e
# para a Advogada." A skill e a peticao-inicial-reclamante (versao do projeto), instalada
# com as que ela encadeia em WORK/.claude/skills (fora do git: producao/ e ignorado).
# O Claude da VPS so enxerga skill de projeto com --setting-sources user,project,local.
# O fechamento_inicial.py e o DONO UNICO das tarefas e da agenda: neste caminho o vigia
# nao chama tarefas_advbox (senao duplicaria no ADVBOX, que nao apaga tarefa).
USAR_SKILL = os.getenv("ESC_PRODUCAO_PELA_SKILL", "1") == "1"
FECHAMENTO = os.path.join(os.path.dirname(_DIR), "OPERACIONAL", "fechamento_inicial.py")
PY_VENV = os.getenv("ESC_PYTHON", "/opt/escritorio/venv/bin/python")
MODELO_SKILL = os.getenv("ESC_MODELO_INICIAL", "sonnet")
TIMEOUT_SKILL = int(os.getenv("ESC_TIMEOUT_SKILL", str(3 * 3600)))
FERRAMENTAS_SKILL = ["Skill", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch",
                     f"Bash({PY_VENV}:*)", "Bash(ls:*)", "Bash(mkdir:*)", "Bash(cp:*)",
                     "Bash(pdftotext:*)", "Bash(pdftoppm:*)", "Bash(ffmpeg:*)"]


def _slug_caso(txt, n=60):
    t = re.sub(r"[^\w\s-]", "", (txt or "").upper()).strip()
    return re.sub(r"\s+", "_", t)[:n] or "CLIENTE"


def pasta_do_caso(cid, nome, reclamada, transcricao, notas, entrevista, ficha, docs,
                  cct_paths, pastas):
    """Monta WORK/<cid>_<data>/ com tudo o que a skill le. E dado de cliente:
    WORK (producao/) fica fora do git."""
    import shutil
    raiz_id, docs_id, atos_id = pastas
    work = os.path.join(WORK, f"{cid}_{_hoje():%Y%m%d}")
    for sub in ("insumos", "docs", "ccts", "saida"):
        os.makedirs(os.path.join(work, sub), exist_ok=True)
    ins = os.path.join(work, "insumos")
    if transcricao and os.path.exists(transcricao):
        shutil.copy2(transcricao, os.path.join(ins, "transcricao" + os.path.splitext(transcricao)[1]))
    with open(os.path.join(ins, "ligacoes_e_notas_do_card.txt"), "w", encoding="utf-8") as f:
        f.write(notas or "")
    with open(os.path.join(ins, "entrevista.json"), "w", encoding="utf-8") as f:
        json.dump(entrevista or {}, f, ensure_ascii=False, indent=1)
    if ficha:
        with open(os.path.join(ins, "ficha_drive.txt"), "w", encoding="utf-8") as f:
            f.write(ficha)
    for p in docs or []:
        destino = os.path.join(work, "docs", os.path.basename(p))
        if not os.path.exists(destino):
            shutil.copy2(p, destino)
    for i, p in enumerate(cct_paths or []):
        shutil.copy2(p, os.path.join(work, "ccts", f"CCT_{i + 1}.pdf"))
    try:
        meta = _listar(f"'{docs_id}' in parents and trashed=false", "files(id,name,mimeType)")
    except Exception:
        meta = []
    with open(os.path.join(ins, "documentos_drive.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    with open(os.path.join(work, "CASO.json"), "w", encoding="utf-8") as f:
        json.dump({"conversa": cid, "cliente": nome, "reclamada": reclamada,
                   "drive": {"raiz_do_cliente": raiz_id, "documentos_do_cliente": docs_id,
                             "atos_internos": atos_id},
                   "peca_modelo": MODRT,
                   "gerado_em": datetime.now().isoformat(timespec="minutes")},
                  f, ensure_ascii=False, indent=1)
    return work


def instrucao_skill(work, nome, reclamada):
    cli, rec = _slug_caso(nome), _slug_caso(reclamada or "RECLAMADA", 40)
    return f"""Voce e a ESTEIRA DA PETICAO INICIAL do Escritório Modelo Advogados rodando SOZINHA no vigia da
producao, sem humano na hora. Use a skill peticao-inicial-reclamante e as que ela encadeia
(liquidacao-previa, recorte-de-documentos, timbrado, revisao-controladoria)
e execute os Passos 1 a 7 DO INICIO AO FIM, SEM PARAR PARA AVAL (determinacao da Dra.
Advogada de 28/09/2026 para o fluxo do vigia): faca a analise do Passo 1 completa, registre-a
em saida/PASSO1_ANALISE.md e siga direto para a peca. NAO execute o Passo 8 (fechamento,
tarefas e agenda): isso e do vigia, logo depois de voce.

CASO: {nome} x {reclamada or "(ver entrevista)"}. Tudo o que existe esta em {work}:
- CASO.json (ids das pastas do Drive e a peca modelo);
- insumos/: transcricao (se houver), ligacoes_e_notas_do_card.txt, entrevista.json,
  ficha_drive.txt e documentos_drive.json (nome e id no Drive de cada documento, para os
  recortes);
- docs/: os documentos do cliente (abra TODOS com Read; imagem e PDF escaneado se leem por
  visao);
- ccts/: a(s) CCT(s) da pasta do cliente. Se estiver vazia, cumpra o GATE DA CCT da skill
  (busque, junte na DOCUMENTOS DO CLIENTE e arquive no acervo) antes de calcular.
Peca modelo (MOD_RT, padrao cheio): {MODRT}. Extraia o texto com python-docx pelo
{PY_VENV} e INSTANCIE a peca a partir dele, nunca do zero nem resumida. O benchmark do
Passo 6 sai de: {PY_VENV} {FECHAMENTO} --ultima-rt --cliente "{nome}".

REGRAS INEGOCIAVEIS (as da skill, em especial): valores sempre com "R$"; nunca inventar
jurisprudencia (sem precedente verificado com fonte e link, use [INSERIR PRECEDENTE]);
trava aritmetica (soma dos pedidos = valor da causa = soma da memoria); densidade de no
minimo {MIN_PALAVRAS} palavras; nunca juntar nem recortar controle de jornada da re; dado
que depende do cliente vai como marcador em vermelho, nunca inventado. Recortes: skill
recorte-de-documentos, com recortar.py --fileid <id do documentos_drive.json> e
montar_peca.py para montar o DOCX final no timbrado (sempre com {PY_VENV}).

ENTREGAS OBRIGATORIAS em {work}/saida/ (sem elas o vigia considera falha e avisa a Advogada):
1. DOSSIE_{cli}.json no schema da referencia 09 (cliente, reclamada, sintese,
   pontos_atencao, valor_causa, documentos_citados, provas_a_buscar, parecer_revisao);
2. RT_INICIAL_{cli}_x_{rec}.docx (timbrado, com os recortes);
3. PASSO1_ANALISE.md;
4. MEMORIA_CALCULO (.xlsx ou .md) com a conta de cada pedido;
5. PARECER_REVISAO.md com uma linha exatamente assim:
   STATUS: APROVADA | STATUS: APROVADA COM RESSALVAS | STATUS: NÃO APROVADA
   (com ressalvas: aplique-as na peca ANTES de entregar e registre o que mudou);
6. RESUMO.txt com 5 linhas para o aviso a Dra. Advogada.
7. SO SE O CONTRATO DUROU MENOS DE 3 MESES: PROJECAO_ACORDO.md com a projecao de liquidacao
   para fins de acordo (total liquidado e faixa minimo/provavel/maximo, com o criterio), da
   mesma memoria de calculo. O fechamento abre com ela a tarefa de acordo da Dra. Advogada.
"""


def rodar_skill(work, nome, reclamada):
    import subprocess
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)   # assinatura, nunca credito de API
    env.update({"TESSERACT_EXE": "/usr/bin/tesseract",
                "TESSDATA_DIR": "/usr/share/tesseract-ocr/5/tessdata",
                "ESC_CONFIG_DIR": os.path.join(os.path.dirname(_DIR), "config"),
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000"})
    cmd = [CLAUDE_BIN, "-p", "--model", MODELO_SKILL, "--setting-sources", "user,project,local",
           "--add-dir", work, "--allowedTools", *FERRAMENTAS_SKILL]
    log = os.path.join(work, "claude_log.txt")
    with open(log, "w", encoding="utf-8") as f:
        r = subprocess.run(cmd, input=instrucao_skill(work, nome, reclamada).encode("utf-8"),
                           stdout=f, stderr=subprocess.STDOUT, env=env, cwd=WORK,
                           timeout=TIMEOUT_SKILL)
    return r.returncode, log


def conferir_entregas(work):
    """(ok, info, motivo). NAO APROVADA da Revisora IA trava a esteira."""
    import glob
    saida = os.path.join(work, "saida")
    dossies = glob.glob(os.path.join(saida, "DOSSIE_*.json"))
    pecas = glob.glob(os.path.join(saida, "RT_INICIAL_*.docx"))
    parecer = os.path.join(saida, "PARECER_REVISAO.md")
    if not dossies or not pecas or not os.path.exists(parecer):
        return False, {}, "entregas incompletas (dossie, peca ou parecer da Revisora IA faltando)"
    try:
        dossie = json.load(open(dossies[0], encoding="utf-8"))
    except Exception as e:
        return False, {}, f"dossie invalido: {str(e)[:120]}"
    faltam = [k for k in ("cliente", "reclamada", "sintese", "valor_causa") if not dossie.get(k)]
    if faltam:
        return False, {}, f"dossie sem {', '.join(faltam)}"
    try:
        from docx import Document
        palavras = sum(len(p.text.split()) for p in Document(pecas[0]).paragraphs)
    except Exception:
        palavras = 0
    m = re.search(r"STATUS:\s*(APROVADA COM RESSALVAS|N[ÃA]O APROVADA|APROVADA)",
                  open(parecer, encoding="utf-8").read(), re.I)
    status = m.group(1).upper().replace("NAO", "NÃO") if m else ""
    info = {"dossie": dossies[0], "peca": pecas[0], "palavras": palavras, "status": status,
            "valor_causa": str(dossie.get("valor_causa") or "").replace("R$", "").strip()}
    if not status:
        return False, info, "parecer da Revisora IA sem STATUS"
    if status == "NÃO APROVADA":
        return False, info, "parecer da Revisora IA: NÃO APROVADA (a esteira trava aqui)"
    if palavras < MIN_PALAVRAS:
        return False, info, f"peca com {palavras} palavras (< {MIN_PALAVRAS})"
    return True, info, ""


def testemunhas_pendentes(tel):
    """Testemunha que o cliente ainda nao indicou (Advogada, 07/10/2026: o card anda so com
    documento; a falta vai escrita na tarefa da Advogada). Le a fila da cobranca de
    documentos pelo telefone: 'testemunhas_pendentes' gravado pela varredura de provas,
    ou o item de testemunha que ficou na lista."""
    fila = os.path.join(_DIR, "cobranca_documentos_fila.json")
    try:
        clientes = json.load(open(fila, encoding="utf-8")).get("clientes", {})
    except Exception:
        return []
    t8 = re.sub(r"\D", "", str(tel or ""))[-8:]
    for c in clientes.values():
        if t8 and re.sub(r"\D", "", str(c.get("telefone") or ""))[-8:] == t8:
            if "testemunhas_pendentes" in c:
                return list(c["testemunhas_pendentes"] or [])
            return [i for i in (c.get("checklist") or []) if re.search(r"testemunh", i, re.I)]
    return []


def preparar_dossie(dossie, cid, tel):
    """Leva para o fechamento o card e as testemunhas que faltam."""
    try:
        d = json.load(open(dossie, encoding="utf-8"))
        d["conversa_id"] = cid
        d["pendencias_cliente"] = testemunhas_pendentes(tel)
        json.dump(d, open(dossie, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return d["pendencias_cliente"]
    except Exception as e:
        print(f"    (dossie sem as pendencias do cliente: {str(e)[:100]})")
        return []


def fechamento(dossie, peca, raiz_id, executar):
    """Passo 8: OPERACIONAL/fechamento_inicial.py --fase completa (peca em ATOS INTERNOS,
    tarefas da Advogada e da Advogada no SISTEMA Escritório Modelo desde 07/10/2026, PF_RT na agenda)."""
    import subprocess
    cmd = [PY_VENV, FECHAMENTO, "--fase", "completa", "--dossie", dossie, "--peca", peca]
    if raiz_id:
        cmd += ["--pasta-cliente", raiz_id]
    if executar:
        cmd.append("--executar")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.dirname(_DIR), timeout=1800)
    saida = (r.stdout or "") + (r.stderr or "")
    with open(os.path.join(os.path.dirname(peca), "fechamento_log.txt"), "a", encoding="utf-8") as f:
        f.write(saida)
    return r.returncode, saida


# ---------------- nucleo ----------------

def rodar(executar=False):
    print(f"=== MOTOR DA PRODUCAO | {_hoje()} | " + ("EXECUTAR" if executar else "DRY-RUN") + " ===")
    if not os.path.exists(DB):
        print("Banco do Sistema Escritório Modelo indisponivel (rodar na VPS).")
        return
    est = _st()
    for cid, nome, tel in _conversas():
        st = est["cards"].setdefault(str(cid), {"status": "novo", "alertas": {}})
        if st["status"] == "manual":
            # MANUAL = a inicial sai em sessao, mas nao pode ficar esquecida (Tobias:
            # marcado em 08/10/2026 e parado ate 13/10). Um aviso por dia a Dra. Advogada.
            dias = _dias_em_producao(cid)
            if dias >= 1:
                _alerta(st, "manual_em_sessao",
                        f"🏭 {nome}: em Produção da Inicial há {dias} dia(s), marcado para "
                        "produção EM SESSÃO (o vigia não faz) e ainda sem peça. É só chamar "
                        "o Claude para produzir.", executar)
            continue
        if st["status"] in ("pronto_revisao", "concluido"):
            continue
        print(f"  [{nome}] conversa {cid} | status {st['status']}")
        raiz_id, docs_id, atos_id = vp.pastas_cliente_completo(nome or "")
        if not docs_id:
            _alerta(st, "pasta", f"🏭 Produção: pasta de {nome} não encontrada em RECLAMANTE.", executar)
            continue
        # (1) transcricao: arquivo (raiz inclusa, combinado 12/08) + ligacoes do card
        transcricao = buscar_transcricao(nome or "", cid, (atos_id, docs_id, raiz_id))
        notas = notas_do_card(cid)
        # (2) entrevista integral + ficha do Drive
        entrevista = entrevista_do_card(cid)
        ficha = ficha_drive(raiz_id) if raiz_id else ""
        print(f"    insumos: transcricao={'sim' if transcricao else 'nao'} | "
              f"ligacoes no card={'sim' if 'LIGACAO' in notas.upper() else 'nao'} | "
              f"entrevista={len(entrevista)} campos | ficha={'sim' if ficha else 'nao/oca'}")
        if not transcricao and "LIGACAO" not in notas.upper() and len(entrevista) < 10:
            _alerta(st, "transcricao",
                    f"🏭 Produção da inicial de {nome}: sem relato do caso. Não há transcrição "
                    "(procurei no link da ficha do card, na pasta do cliente e pelo nome no "
                    "Drive), nem ligação colada no card, e a aba Entrevista tem "
                    f"{len(entrevista)} campo(s). Preencha a Entrevista ou suba a transcrição "
                    "que eu retomo sozinha.", executar)
            continue
        # (5) CCT: por indicacao, nunca "a primeira que o Drive devolver"
        ccts, motivo_cct = escolher_cct(docs_id, entrevista)
        if not ccts and USAR_SKILL:
            # gate da CCT da skill (regra da Advogada de 11/09): a skill BUSCA a convencao
            # (acervo do Drive, Mediador, internet), junta na pasta e arquiva no acervo
            print(f"    CCT ausente na pasta ({motivo_cct}): o gate da CCT da skill busca")
            ccts = []
        elif not ccts:
            _alerta(st, f"cct_{motivo_cct}",
                    f"🏭 Produção da inicial de {nome}: " + (
                        "só há CCT marcada como ALTERNATIVA na pasta; falta a principal."
                        if motivo_cct == "so_alternativa" else
                        "CCT não está na DOCUMENTOS DO CLIENTE. Buscar pelo CBO da CTPS e "
                        f"pela sede da empresa (Mediador do MTE: {MEDIADOR}) e salvar lá.")
                    + " Eu retomo sozinha.", executar)
            continue
        if ccts:
            print(f"    CCT ({motivo_cct}): " + " | ".join(c["name"][:60] for c in ccts))
        if not executar:
            print("    (dry-run: pre-requisitos OK; " + (
                "rodaria a skill peticao-inicial-reclamante do Passo 1 ao 7 e o fechamento: "
                "peca em ATOS INTERNOS, tarefa da Advogada, PF_RT na agenda, protocolo Advogada+Advogada)"
                if USAR_SKILL else "producao nao roda)"))
            continue
        try:
            cct_paths = []
            for i, c in enumerate(ccts or []):
                cp = os.path.join(WORK, f"{cid}_cct_{i + 1}.pdf")
                baixar_arquivo(c["id"], cp)
                cct_paths.append(cp)
            # (3) documentos da pasta, sem duplicatas (lidos por visao no dossie)
            docs = documentos_da_pasta(docs_id, cid)
            if USAR_SKILL:
                reclamada = entrevista.get("razao_social") or ""
                work = pasta_do_caso(cid, nome, reclamada, transcricao, notas, entrevista, ficha,
                                     docs, cct_paths, (raiz_id, docs_id, atos_id))
                st["status"] = "skill"
                st["pasta_do_caso"] = work
                vp._salvar(STATE, est)
                print(f"    SKILL: esteira rodando em {work}")
                rc, arq_log = rodar_skill(work, nome, reclamada)
                ok, info, motivo_skill = conferir_entregas(work)
                if not ok:
                    raise ValueError(f"skill: {motivo_skill} (log: {arq_log})")
                print(f"    SKILL ok: {info['palavras']} palavras | Revisora IA: {info['status']}")
                sem_testemunha = preparar_dossie(info["dossie"], cid, tel)
                rc2, saida_fech = fechamento(info["dossie"], info["peca"], raiz_id, executar)
                if rc2 != 0:
                    raise ValueError("fechamento: " + saida_fech.strip()[-200:])
                try:
                    fst = json.load(open(os.path.join(os.path.dirname(info["peca"]),
                                                      "_fechamento_state.json"), encoding="utf-8"))
                except Exception:
                    fst = {}
                # a data vem do fechamento (fim de semana ja ajustado, Advogada 30/09/2026)
                st["prazo_revisao"] = (fst.get("prazo_revisao")
                                       or (_hoje() + timedelta(days=PRAZO_REVISAO_DIAS)).isoformat())
                st["prazo_protocolo"] = fst.get("prazo_protocolo")
                _mover(cid, ETAPA_DEPOIS, executar)
                st["status"] = "pronto_revisao"
                # o fechamento grava se cada compromisso entrou na agenda: o aviso
                # nao afirma agenda que falhou
                agenda = ("compromisso da revisão da Advogada e PF_RT na agenda"
                          if fst.get("agenda_revisao_ok") and fst.get("agenda_pf_ok")
                          else "⚠️ AGENDA NÃO CADASTRADA, ver fechamento_log.txt")
                vp._zapi(TEL_PAM,
                         f"🏭✅ {nome}: inicial produzida pela SKILL da esteira ({info['palavras']} "
                         f"palavras, valor da causa R$ {info['valor_causa']}, Revisora IA: "
                         f"{info['status']}). Peça com recortes em ATOS INTERNOS "
                         f"({fst.get('link_peca_final') or fst.get('link_peca_revisao') or ''}); "
                         f"tarefas da Advogada (revisão) e da Advogada (protocolo) no Sistema Escritório Modelo; "
                         f"{agenda}. Card movido para Revisão."
                         + (" Testemunha ainda não indicada pelo cliente (anotado na "
                            "tarefa da Advogada)." if sem_testemunha else ""))
                vp._salvar(STATE, est)
                continue
            st["status"] = "dossie"
            vp._salvar(STATE, est)
            dossie = gerar_dossie(transcricao, cct_paths, nome, tel, notas=notas,
                                  entrevista=entrevista, ficha=ficha, docs=docs,
                                  motivo_cct=motivo_cct)
            json.dump(dossie, open(os.path.join(WORK, f"{cid}_dossie.json"), "w",
                                   encoding="utf-8"), ensure_ascii=False, indent=1)
            L, total = liquidar(dossie)
            blocos, palavras = gerar_peca(dossie, L, total)
            print(f"    peca gerada: {palavras} palavras")
            if palavras < MIN_PALAVRAS:
                raise ValueError(f"gate de densidade: {palavras} palavras (< {MIN_PALAVRAS})")
            os.makedirs(WORK, exist_ok=True)
            peca_path = os.path.join(WORK, f"{cid}_peca.docx")
            montar_docx(blocos, peca_path)
            links = upload_pacote(nome, atos_id, peca_path, L, total, dossie, executar)
            links_txt = "\n".join(f"{k}: https://drive.google.com/file/d/{v}/view"
                                  for k, v in links.items() if v)
            st["prazo_revisao"], st["prazo_protocolo"] = tarefas_advbox(
                nome, links_txt, executar)
            _mover(cid, ETAPA_DEPOIS, executar)
            st["status"] = "pronto_revisao"
            vp._zapi(TEL_PAM,
                     f"🏭✅ {nome}: ESBOÇO da inicial produzido pela automação "
                     f"({palavras} palavras, valor da causa R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") +
                     f"). Peça, memória e pendências em ATOS INTERNOS; tarefas da Advogada "
                     f"(revisão) e Advogada+Advogada (protocolo) criadas. Card movido para Revisão.")
        except Exception as e:
            st["status"] = "falhou"
            # a chave leva o motivo: falha nova no mesmo dia tambem avisa (antes o
            # 2o aviso do dia era engolido e o motivo real sumia do log)
            motivo = re.sub(r"\W+", "_", str(e))[:40]
            print(f"    FALHA: {str(e)[:300]}")
            _alerta(st, f"falha_{motivo}",
                    f"🛑 Produção da inicial de {nome} parou: {str(e)[:220]}. Card mantido "
                    "em Produção da Inicial para ação humana.", executar)
        vp._salvar(STATE, est)
    if executar:
        vp._salvar(STATE, est)
    print("=== fim ===")


def status():
    est = _st()
    if not est["cards"]:
        print("Nenhum card acompanhado.")
        return
    for cid, st in est["cards"].items():
        print(f"  [{cid}] {st.get('status')} | alertas: {list(st.get('alertas', {}))}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--executar", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        status()
    else:
        rodar(a.executar)
