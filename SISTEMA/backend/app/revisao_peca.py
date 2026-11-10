"""REVISAO DE PECA PELA VALENTINA, disparada pelo agente (29/09/2026, pedido do Titular:
"se a Advogada pedir para revisar, acione o nosso fluxo").

Hoje a Revisora IA so roda dentro da esteira noturna da inicial (Claude Code, horas, grava no
ADVBOX). Aqui ela roda pela API, em segundo plano, sobre a peca que a pessoa aponta:
  1. acha o link da peca (na tarefa: linha "PECA A REVISAR: <link>", ou o link que a pessoa der);
  2. baixa o texto do Drive (Google Docs, DOCX ou PDF) e, se a tarefa tiver, os DOCUMENTOS DO CLIENTE;
  3. roda a skill da Revisora IA (/opt/escritorio/producao/.claude/skills/revisao-controladoria)
     como instrucao, no Opus;
  4. guarda o parecer (revisoes_peca), salva um Google Doc "PARECER VALENTINA" na mesma pasta
     da peca e avisa na conversa do agente.
Nao mexe no ADVBOX, nao manda mensagem para ninguem, nao altera a peca.
"""
import datetime as dt
import io
import logging
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("revisao_peca")
DB = str(Path(__file__).resolve().parents[1] / "sistema_esc.db")
SKILL = Path("/opt/escritorio/producao/.claude/skills/revisao-controladoria/SKILL.md")
MODELO = "claude-opus-5-5"
MAX_PECA = 160000
MAX_DOCS = 60000

EXTRA = """

COMO VOCÊ ESTÁ RODANDO AGORA (leia com atenção)
Você está rodando dentro do Sistema Escritório Modelo, chamada por um agente da equipe, SEM acesso à internet e sem outras skills.
- Não consegue abrir links de jurisprudência. Para cada julgado, súmula ou OJ citada na peça, diga se reconhece e se,
  pelo seu conhecimento, está vigente e pertinente; quando não tiver certeza, marque "CONFERIR NA FONTE" e diga onde
  conferir (TST, TRT, STJ, STF). Nunca afirme que conferiu um link.
- Recebe o texto extraído da peça (a formatação visual, como fonte, recuo e timbrado, não aparece no texto: não
  reprove por isso; se algo de formatação aparecer no texto, comente).
- Pode receber também os DOCUMENTOS DO CLIENTE (texto extraído) para conferir fatos, datas e valores.

FORMATO DA RESPOSTA
Primeira linha, exatamente: STATUS: APROVADA | APROVADA COM RESSALVAS | NÃO APROVADA (escolha um).
Depois:
## Resumo
(3 a 5 linhas: o que é a peça, para quem, e o principal a corrigir)
## Pontos a corrigir
(lista numerada, do mais grave ao menos grave; para cada um: onde está na peça, o problema e a correção proposta)
## Jurisprudência citada
(cada citação: situação e se precisa conferir na fonte)
## O que faltou para uma revisão completa
(o material que não veio e faria diferença)
Seja objetivo: no máximo 12 pontos a corrigir, cada um em até 5 linhas; junte os pequenos (redação, citação,
uniformização) num último ponto. Escreva as QUATRO seções até o fim, sem cortar nenhuma.
Português do Brasil, técnico e objetivo. Sem emoji. Não use travessão."""


def con():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    garantir(c)
    return c


def garantir(c):
    c.execute("""CREATE TABLE IF NOT EXISTS revisoes_peca (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER, agente TEXT, tarefa_id INTEGER,
                   link TEXT, arquivo TEXT, foco TEXT, status TEXT, veredito TEXT, parecer TEXT, doc_link TEXT,
                   erro TEXT, criado_em REAL, concluido_em REAL)""")


def link_da_tarefa(t):
    """(link da peca, link da pasta de documentos) a partir da descricao da tarefa."""
    d = (t["descricao"] if t else "") or ""
    m = re.search(r"PE[CÇ]A A REVISAR:\s*(https?://\S+)", d, re.I)
    docs = re.search(r"DOCUMENTOS DO CLIENTE:\s*(https?://\S+)", d, re.I)
    link = m.group(1) if m else None
    if not link:
        achados = re.findall(r"https?://(?:docs|drive)\.google\.com/(?:document|file)/d/\S+", d)
        link = achados[0] if achados else None
    return link, (docs.group(1) if docs else None)


def _id_arquivo(link):
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})", link or "") or re.search(r"[?&]id=([A-Za-z0-9_-]{20,})", link or "")
    return m.group(1) if m else None


def _drive():
    sys.path.insert(0, "/opt/escritorio/INTEGRACOES")
    from google_integration import autenticar_google
    drive, _ = autenticar_google()
    return drive


def _texto_do_arquivo(drive, fid):
    from . import drive_reader as DR
    meta = drive.files().get(fileId=fid, fields="id,name,mimeType,parents", supportsAllDrives=True).execute()
    mime = meta.get("mimeType")
    if mime == "application/vnd.google-apps.document":
        texto = drive.files().export(fileId=fid, mimeType="text/plain").execute().decode("utf-8", "replace")
    else:
        conteudo = drive.files().get_media(fileId=fid, supportsAllDrives=True).execute()
        nome = (meta.get("name") or "").lower()
        if nome.endswith(".pdf") or mime == "application/pdf":
            texto = DR._extrair_texto_pdf(conteudo)
        elif nome.endswith(".docx") or "wordprocessingml" in (mime or ""):
            texto = DR._extrair_texto_docx(conteudo)
        else:
            texto = conteudo.decode("utf-8", "replace")
    return meta, (texto or "").strip()


def _salvar_doc(drive, pasta, nome, texto):
    from googleapiclient.http import MediaIoBaseUpload
    corpo = {"name": nome, "mimeType": "application/vnd.google-apps.document"}
    if pasta:
        corpo["parents"] = [pasta]
    f = drive.files().create(body=corpo, media_body=MediaIoBaseUpload(io.BytesIO(texto.encode("utf-8")),
                                                                       mimetype="text/plain", resumable=False),
                             fields="id,webViewLink", supportsAllDrives=True).execute()
    return f.get("webViewLink")


def _avisar_na_conversa(c, uid, texto):
    try:
        conversa = c.execute("SELECT COALESCE(MAX(conversa), 1) FROM agente_mensagens WHERE usuario_id=?",
                             (uid,)).fetchone()[0]
        c.execute("INSERT INTO agente_mensagens (usuario_id, conversa, papel, texto, fontes, criado_em, acoes, via) "
                  "VALUES (?,?,?,?,?,?,?,?)", (uid, conversa, "assistant", texto, "[]", time.time(), "[]", "texto"))
        c.commit()
    except sqlite3.Error as e:
        log.warning("aviso na conversa: %s", e)


def iniciar(uid, agente, tarefa_id=None, link=None, foco=None):
    """Registra e dispara a revisao em segundo plano. Devolve (id, erro)."""
    c = con()
    try:
        t = c.execute("SELECT * FROM tarefas WHERE id=?", (int(tarefa_id),)).fetchone() if tarefa_id else None
        if tarefa_id and not t:
            return None, "tarefa #%s não encontrada" % tarefa_id
        docs = None
        if not link:
            link, docs = link_da_tarefa(t)
        if not link or not _id_arquivo(link):
            return None, ("não achei o link da peça%s. Mande o link do Google Docs ou do arquivo no Drive."
                          % (" na tarefa #%s" % tarefa_id if tarefa_id else ""))
        ja = c.execute("SELECT id FROM revisoes_peca WHERE link=? AND status='rodando' AND criado_em > ?",
                       (link, time.time() - 1800)).fetchone()
        if ja:
            return ja["id"], None
        cur = c.execute("INSERT INTO revisoes_peca (usuario_id, agente, tarefa_id, link, foco, status, criado_em) "
                        "VALUES (?,?,?,?,?,?,?)", (uid, agente, tarefa_id, link, foco, "rodando", time.time()))
        c.commit()
        rid = cur.lastrowid
    finally:
        c.close()
    threading.Thread(target=_rodar, args=(rid, docs), daemon=True, name="revisao-%s" % rid).start()
    return rid, None


def _rodar(rid, pasta_docs=None):
    import anthropic
    from .config import settings
    c = con()
    try:
        r = c.execute("SELECT * FROM revisoes_peca WHERE id=?", (rid,)).fetchone()
        t = c.execute("SELECT * FROM tarefas WHERE id=?", (r["tarefa_id"],)).fetchone() if r["tarefa_id"] else None
        drive = _drive()
        meta, texto = _texto_do_arquivo(drive, _id_arquivo(r["link"]))
        if len(texto) < 500:
            raise RuntimeError("a peça veio vazia ou curta demais (%d caracteres); confira o compartilhamento" % len(texto))
        c.execute("UPDATE revisoes_peca SET arquivo=? WHERE id=?", (meta.get("name"), rid))
        c.commit()
        docs_txt = ""
        if pasta_docs:
            try:
                from . import drive_reader as DR
                lido = DR.ler_pasta(pasta_docs)
                if lido.get("ok"):
                    docs_txt = (lido.get("texto") or "")[:MAX_DOCS]
            except Exception as e:
                log.warning("docs do cliente: %s", e)
        contexto = []
        if t:
            contexto.append("TAREFA #%s: %s | cliente: %s | parte adversa: %s | processo: %s | prazo fatal: %s"
                            % (t["id"], t["tipo"], t["cliente"] or "-", t["parte_adversa"] or "-",
                               t["processo_numero"] or "-", t["prazo_fatal"] or "-"))
            contexto.append("ORIENTAÇÕES DA TAREFA:\n" + re.sub(r"https?://\S+", "[link]", (t["descricao"] or ""))[:4000])
        if r["foco"]:
            contexto.append("PEDIDO DE QUEM SOLICITOU A REVISÃO: " + r["foco"])
        contexto.append("PEÇA A REVISAR (%s):\n\n%s" % (meta.get("name"), texto[:MAX_PECA]))
        if docs_txt:
            contexto.append("DOCUMENTOS DO CLIENTE (texto extraído, pode estar incompleto):\n\n" + docs_txt)
        sistema = (SKILL.read_text(encoding="utf-8") if SKILL.exists() else "Você é Revisora IA, controladoria jurídica do Escritório Modelo.") + EXTRA
        from . import ia_reserva as IR
        from . import claude_plano  # plano Max no lugar da API (07/10/2026)
        # O Opus 5.5 so aceita raciocinio "adaptive". Na primeira versao (sem limite de esforco e 16 mil
        # tokens) ele gastou tudo pensando e o parecer saiu vazio: agora esforco medio, 64 mil de folga,
        # e se ainda vier sem texto tenta de novo com esforco baixo.
        parecer, motivo, cortado = "", "", False
        try:
            if IR.anthropic_fora() and IR.disponivel():
                raise RuntimeError("anthropic fora (reserva ativa)")
            cli = claude_plano.Anthropic(origem='revisao-peca')
            for esforco, teto in (("medium", 64000), ("low", 32000)):
                with cli.messages.stream(model=MODELO, max_tokens=teto, system=sistema, thinking={"type": "adaptive"},
                                         extra_body={"output_config": {"effort": esforco}},
                                         messages=[{"role": "user", "content": "\n\n".join(contexto)}]) as s:
                    final = s.get_final_message()
                parecer = "".join(getattr(b, "text", "") for b in final.content if getattr(b, "type", "") == "text").strip()
                motivo = "%s, %s tokens de saída" % (final.stop_reason, getattr(final.usage, "output_tokens", "?"))
                log.info("revisao %s: esforco %s -> %s", rid, esforco, motivo)
                cortado = final.stop_reason == "max_tokens"
                if len(parecer) > 200:
                    break
        except Exception as e:
            # carteira da Anthropic sem credito (29/09): a Revisora IA roda no Gemini Pro
            if not IR.disponivel() or not (IR.anthropic_fora() or IR.falha_de_conta(e)):
                raise
            if not IR.anthropic_fora():
                IR.marcar_fora(e)
            parecer = IR.texto(sistema, [{"role": "user", "content": "\n\n".join(contexto)}], 30000, IR.MODELO_PRO)
            motivo, cortado = "gemini", False
            log.info("revisao %s: feita no Gemini (%d caracteres)", rid, len(parecer))
        if len(parecer) < 200:
            raise RuntimeError("a Revisora IA não devolveu o parecer (%s)" % motivo)
        if cortado:
            parecer += "\n\n(Atenção: o parecer passou do tamanho máximo e foi cortado aqui. Peça a revisão de novo com um foco.)"
        parecer = re.sub(r"\s+[—–]\s+", ", ", parecer).replace("—", ", ")
        m = re.search(r"STATUS:\s*(APROVADA COM RESSALVAS|N[ÃA]O APROVADA|APROVADA)", parecer, re.I)
        veredito = m.group(1).upper().replace("NAO", "NÃO") if m else "SEM STATUS"
        doc_link = None
        try:
            pasta = (meta.get("parents") or [None])[0]
            doc_link = _salvar_doc(drive, pasta, "PARECER VALENTINA - %s - %s" % (
                re.sub(r"\.(docx|pdf)$", "", meta.get("name") or "peca", flags=re.I), dt.date.today().strftime("%d-%m-%Y")),
                parecer)
        except Exception as e:
            log.warning("salvar parecer no Drive: %s", e)
        c.execute("UPDATE revisoes_peca SET status='pronta', veredito=?, parecer=?, doc_link=?, concluido_em=? WHERE id=?",
                  (veredito, parecer, doc_link, time.time(), rid))
        c.commit()
        resumo = parecer
        ms = re.search(r"##\s*Resumo\s*\n(.+?)(?=\n##|\Z)", parecer, re.S)
        pts = re.search(r"##\s*Pontos a corrigir\s*\n(.+?)(?=\n##|\Z)", parecer, re.S)
        if ms:
            resumo = ms.group(1).strip() + ("\n\n**Pontos a corrigir:**\n" + pts.group(1).strip()[:2500] if pts else "")
        aviso = ("Terminei a revisão da peça **%s**%s pela Revisora IA: **%s**.\n\n%s\n\n%s" % (
            meta.get("name"), " (tarefa [#%s])" % r["tarefa_id"] if r["tarefa_id"] else "", veredito, resumo[:4000],
            "O parecer completo ficou no Drive, na mesma pasta da peça: %s" % doc_link if doc_link
            else "Não consegui salvar o parecer no Drive; peça que eu mostro o texto completo aqui."))
        _avisar_na_conversa(c, r["usuario_id"], aviso)
    except Exception as e:
        log.exception("revisao %s", rid)
        c.execute("UPDATE revisoes_peca SET status='erro', erro=?, concluido_em=? WHERE id=?", (str(e)[:500], time.time(), rid))
        c.commit()
        r = c.execute("SELECT usuario_id FROM revisoes_peca WHERE id=?", (rid,)).fetchone()
        _avisar_na_conversa(c, r["usuario_id"], "Não consegui terminar a revisão da peça: %s. Pode me mandar o link de novo "
                                                 "ou conferir se o arquivo está compartilhado com o escritório?" % str(e)[:200])
    finally:
        c.close()


def _orfas(c):
    """Revisao 'rodando' ha mais de 25 min morreu (o sistema reiniciou no meio): avisa e libera."""
    for r in c.execute("SELECT id, usuario_id FROM revisoes_peca WHERE status='rodando' AND criado_em < ?",
                       (time.time() - 1500,)).fetchall():
        c.execute("UPDATE revisoes_peca SET status='erro', erro=?, concluido_em=? WHERE id=?",
                  ("interrompida (o sistema reiniciou no meio)", time.time(), r["id"]))
        _avisar_na_conversa(c, r["usuario_id"], "A revisão da peça foi interrompida porque o sistema reiniciou no meio. "
                                                 "Pode pedir de novo que eu refaço.")
    c.commit()


def rodando(uid):
    c = con()
    try:
        _orfas(c)
        return c.execute("SELECT COUNT(*) FROM revisoes_peca WHERE usuario_id=? AND status='rodando'", (uid,)).fetchone()[0]
    finally:
        c.close()


def listar(uid, n=8):
    c = con()
    try:
        return [{"id": r["id"], "tarefa_id": r["tarefa_id"], "arquivo": r["arquivo"], "status": r["status"],
                 "veredito": r["veredito"], "parecer_no_drive": r["doc_link"], "erro": r["erro"],
                 "pedida_em": dt.datetime.fromtimestamp(r["criado_em"]).strftime("%d/%m %H:%M")}
                for r in c.execute("SELECT * FROM revisoes_peca WHERE usuario_id=? ORDER BY id DESC LIMIT ?", (uid, n))]
    finally:
        c.close()


def parecer(rid, uid, admin=False):
    c = con()
    try:
        r = c.execute("SELECT * FROM revisoes_peca WHERE id=?", (int(rid),)).fetchone()
        if not r or (r["usuario_id"] != uid and not admin):
            return None
        return {"id": r["id"], "arquivo": r["arquivo"], "veredito": r["veredito"], "parecer": (r["parecer"] or "")[:20000],
                "drive": r["doc_link"], "status": r["status"], "erro": r["erro"]}
    finally:
        c.close()
