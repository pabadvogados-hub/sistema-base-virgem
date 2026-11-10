# -*- coding: utf-8 -*-
"""CHECKLIST DE DOCUMENTOS PODADO PELA TRANSCRICAO DA REUNIAO.

Problema que este modulo resolve (Advogada, 25/08/2026). O `CHECKLIST_PADRAO` do
`cobranca_documentos.py` e uma lista FIXA no codigo, aplicada a todo cliente
novo: o `cobranca_assinatura.py` chama o enroll sem passar checklist quando o
cliente assina. Resultado, duas vezes:

  - um cliente (08/2026): cobrado por B.O. de roubo e por TRCT, com o contrato
    de trabalho AINDA EM VIGOR. Corrigido a mao pela Advogada.
  - outro cliente (08/2026): cobrado por "Boletim de Ocorrencia do
    roubo/sequestro" durante CINCO toques, quando na reuniao de fechamento ele
    afirmou expressamente que NUNCA foi assaltado nem sequestrado.

Cobrar documento impossivel queima a confianca do cliente e gasta toque de
cobranca. A transcricao da reuniao ja tem a resposta; o checklist e que nao
olhava para ela.

REGRA DE SEGURANCA, deliberadamente conservadora:
  - so REMOVE item que a transcricao CONTRADIZ EXPRESSAMENTE;
  - na duvida, ou se a transcricao for silente, MANTEM (e melhor pedir de novo
    do que deixar de juntar prova);
  - NUNCA acrescenta item que nao esteja no checklist recebido;
  - qualquer falha (sem transcricao, CLI fora, JSON quebrado) devolve None e o
    chamador segue com o checklist padrao. O pior caso e o comportamento de
    hoje, nunca pior que ele.

Roda pelo Claude Code da VPS (assinatura), nunca pelos creditos da API: a
ANTHROPIC_API_KEY e removida do ambiente do subprocesso.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

DB = os.getenv("SISTEMA_DB", "/opt/escritorio/SISTEMA/backend/sistema_esc.db")
CLAUDE_BIN = os.getenv("ESC_CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))
LOG = os.getenv("ESC_LOG_CHECKLIST", "/var/log/escritorio-checklist-documentos.log")


def _log(txt):
    print(txt)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(txt.rstrip() + "\n")
    except Exception:
        pass


def conversa_por_telefone(telefone, nome=""):
    """Acha o card do cliente. Casa pelos ULTIMOS 8 digitos, que e a convencao
    de dedup da casa (o WhatsApp varia o nono digito e o codigo do pais)."""
    tel = re.sub(r"\D", "", telefone or "")
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
        if len(tel) >= 8:
            r = con.execute(
                "select id from wa_conversas where replace(replace(replace("
                "contato_telefone,'-',''),' ',''),'+','') like ? "
                "order by atualizado_em desc limit 1", ("%" + tel[-8:],)).fetchone()
            if r:
                con.close()
                return r[0]
        if nome:
            r = con.execute("select id from wa_conversas where contato_nome like ? "
                            "order by atualizado_em desc limit 1",
                            ("%" + nome.strip().split()[0] + "%",)).fetchone()
            con.close()
            return r[0] if r else None
        con.close()
    except Exception as e:
        _log(f"[checklist] nao achei o card ({str(e)[:100]})")
    return None


def _transcricao(nome, cid):
    """Texto da transcricao, pela mesma receita dos outros motores."""
    try:
        import requests
        import transcricao_drive as td
        import varredura_provas as vp
    except Exception as e:
        _log(f"[checklist] dependencia ausente: {str(e)[:100]}")
        return None

    def _meta(fid):
        return requests.get(f"https://www.googleapis.com/drive/v3/files/{fid}",
                            headers=vp._H(),
                            params={"fields": "id,name,mimeType,size"}, timeout=30).json()

    def _baixar(meta):
        fid, mime = meta["id"], (meta.get("mimeType") or "")
        if mime == "application/vnd.google-apps.document":
            r = requests.get(f"https://www.googleapis.com/drive/v3/files/{fid}/export",
                             headers=vp._H(), params={"mimeType": "text/plain"}, timeout=120)
            return r.content.decode("utf-8", "replace")
        r = requests.get(f"https://www.googleapis.com/drive/v3/files/{fid}",
                         headers=vp._H(), params={"alt": "media"}, timeout=180)
        if mime.endswith("wordprocessingml.document") or \
                (meta.get("name") or "").lower().endswith(".docx"):
            import io
            from docx import Document
            return "\n".join(p.text for p in Document(io.BytesIO(r.content)).paragraphs)
        return r.content.decode("utf-8", "replace")

    metas = []
    for fid in td.ids_da_ficha(DB, cid):
        try:
            metas.append(_meta(fid))
        except Exception:
            pass
    escolhido = td.escolher(metas)
    if not escolhido:
        try:
            raiz, docs, atos = vp.pastas_cliente_completo(nome or "")
            import requests as rq
            for pid in (p for p in (raiz, atos, docs) if p):
                fs = rq.get("https://www.googleapis.com/drive/v3/files", headers=vp._H(),
                            params={"q": f"'{pid}' in parents and trashed=false",
                                    "fields": "files(id,name,mimeType,size)",
                                    "pageSize": 100}, timeout=30).json().get("files", [])
                escolhido = td.escolher(fs, exigir_pista=True)
                if escolhido:
                    break
        except Exception as e:
            _log(f"[checklist] busca na pasta falhou: {str(e)[:100]}")
    if not escolhido:
        return None
    try:
        _log(f"[checklist] transcricao usada: {escolhido.get('name')}")
        return _baixar(escolhido)
    except Exception as e:
        _log(f"[checklist] baixar transcricao falhou: {str(e)[:100]}")
        return None


def _claude(prompt, timeout_s=600):
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)   # assinatura, nunca API
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "8000"
    r = subprocess.run(
        [CLAUDE_BIN, "-p", "--model", "sonnet", "--output-format", "stream-json",
         "--verbose", "--tools", "", "--strict-mcp-config",
         "--mcp-config", '{"mcpServers":{}}'],
        input=prompt.encode("utf-8"), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, timeout=timeout_s)
    if r.returncode != 0:
        raise RuntimeError("claude cli rc=%d: %s"
                           % (r.returncode, r.stderr.decode("utf-8", "replace")[:200]))
    # junta TODAS as mensagens do assistente (o modo -p imprime so a ultima)
    partes = []
    for linha in r.stdout.decode("utf-8", "replace").splitlines():
        try:
            ev = json.loads(linha)
        except Exception:
            continue
        if ev.get("type") == "assistant":
            for b in (ev.get("message", {}).get("content") or []):
                if b.get("type") == "text":
                    partes.append(b.get("text", ""))
    return "".join(partes)


def _baixar_meta(meta):
    """Baixa um arquivo do Drive como texto (gdoc exporta, docx extrai)."""
    import requests
    import varredura_provas as vp
    fid, mime = meta["id"], (meta.get("mimeType") or "")
    if mime == "application/vnd.google-apps.document":
        r = requests.get(f"https://www.googleapis.com/drive/v3/files/{fid}/export",
                         headers=vp._H(), params={"mimeType": "text/plain"}, timeout=120)
        return r.content.decode("utf-8", "replace")
    r = requests.get(f"https://www.googleapis.com/drive/v3/files/{fid}",
                     headers=vp._H(), params={"alt": "media"}, timeout=180)
    if mime.endswith("wordprocessingml.document") or \
            (meta.get("name") or "").lower().endswith(".docx"):
        import io as _io
        from docx import Document
        return "\n".join(p.text for p in Document(_io.BytesIO(r.content)).paragraphs)
    return r.content.decode("utf-8", "replace")


def _meta_drive(fid):
    import requests
    import varredura_provas as vp
    return requests.get(f"https://www.googleapis.com/drive/v3/files/{fid}",
                        headers=vp._H(),
                        params={"fields": "id,name,mimeType,size"}, timeout=30).json()


def _entrevista_rows(cid):
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
        rows = con.execute(
            "select campo, valor from ficha_entrevista where conversa_id=? "
            "order by campo", (cid,)).fetchall()
        con.close()
        return [(c, (v or "").strip()) for c, v in rows]
    except Exception:
        return []


def _link_transcricao_entrevista(cid):
    """A aba Entrevista tem botao de anexar transcricao; o link fica no campo
    _transcricao_link. E mais uma porta de entrada da transcricao."""
    for campo, valor in _entrevista_rows(cid):
        if campo == "_transcricao_link" and valor:
            m = re.search(r"(?:/file/d/|/document/d/|/spreadsheets/d/|[?&]id=)"
                          r"([A-Za-z0-9_-]{20,})", valor)
            if m:
                return m.group(1)
    return None


def _entrevista_texto(cid):
    """Insumo da aba ENTREVISTA do card (tabela ficha_entrevista, no ar desde
    02/09/2026). A Advogada integrou a entrevista na ficha do cliente do Sistema
    Escritório Modelo: quando o time preenche por la, o caso esta descrito sem depender de
    transcricao (11/09/2026)."""
    rows = [(c, v) for c, v in _entrevista_rows(cid)
            if v and not c.startswith("_")]
    if len(rows) < 10:
        return None
    return ("FICHA DE ENTREVISTA DO CLIENTE (preenchida pelo time no Sistema "
            "Escritório Modelo):\n" + "\n".join(f"{c}: {v}" for c, v in rows))


def _pasta_do_card(cid):
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
        r = con.execute("select drive_pasta_id from wa_conversas where id=?",
                        (cid,)).fetchone()
        con.close()
        return (r[0] or "").strip() if r else ""
    except Exception:
        return ""


def _ficha_texto(nome, cid):
    """Fallback de insumo: a FICHA DO CLIENTE gerada no intake (tem o resumo
    do caso). Vale quando a transcricao nunca subiu - caso a equipe,
    11/09/2026, que levou a lista comum num caso de gravidez."""
    try:
        import requests as rq
        import varredura_provas as vp
        pastas = []
        raiz_card = _pasta_do_card(cid) if cid else ""
        if raiz_card:
            pastas.append(raiz_card)
        try:
            raiz, docs, atos = vp.pastas_cliente_completo(nome or "")
            pastas += [p for p in (raiz, atos) if p and p not in pastas]
        except Exception:
            pass
        for pid in pastas:
            fs = rq.get("https://www.googleapis.com/drive/v3/files",
                        headers=vp._H(),
                        params={"q": f"'{pid}' in parents and trashed=false",
                                "fields": "files(id,name,mimeType,size)",
                                "pageSize": 100}, timeout=30).json().get("files", [])
            fichas = [f for f in fs if "ficha" in (f.get("name") or "").lower()
                      and "folder" not in (f.get("mimeType") or "")]
            if fichas:
                t = _baixar_meta(fichas[0])
                # ficha OCA (template cru do intake sem transcricao): nao e
                # relato de caso nenhum - caso a equipe, 11/09/2026
                if t and ("{{PREENCHER" in t or "PREENCHER OS FATOS" in t):
                    _log(f"[checklist] ficha {fichas[0].get('name')} esta OCA "
                         "(template sem fatos); nao serve de insumo")
                    return None
                _log(f"[checklist] ficha usada como insumo: {fichas[0].get('name')}")
                return t
    except Exception as e:
        _log(f"[checklist] busca da ficha falhou: {str(e)[:100]}")
    return None


def montar(nome, telefone, checklist, cid=None):
    """Monta a lista de documentos DO CASO (Advogada, 11/09/2026).

    Insumo: transcricao da reuniao; na falta, a Ficha do Cliente do intake.
    Caso comum: poda a lista base (so contradicao EXPRESSA remove).
    Caso especial: a lista base nao serve; o modelo escreve uma
    lista propria do caso (a a equipe, gestante, recebeu cobranca de B.O. de
    itens que nao se aplicam ao caso - nunca mais).

    Retorna (itens, origem) quando ha lista confiavel;
    (None, motivo) quando NAO ha - e o chamador decide segurar a cobranca.
    Motivos: sem_card | sem_insumo | cli_indisponivel | resposta_invalida.
    """
    if not checklist:
        return None, "sem_lista_base"
    cid = cid or conversa_por_telefone(telefone, nome)
    if not cid:
        _log(f"[checklist] {nome}: sem card no CRM")
        return None, "sem_card"
    fonte = "transcricao"
    texto = _transcricao(nome, cid)
    if not texto or len(texto.strip()) < 400:
        fid = _link_transcricao_entrevista(cid)
        if fid:
            try:
                texto = _baixar_meta(_meta_drive(fid))
                fonte = "transcricao (link da entrevista)"
                _log(f"[checklist] transcricao pelo link da entrevista ({fid[:10]}...)")
            except Exception as e:
                _log(f"[checklist] link da entrevista falhou: {str(e)[:80]}")
    if not texto or len(texto.strip()) < 400:
        fonte = "entrevista"
        texto = _entrevista_texto(cid)
        if texto:
            _log(f"[checklist] entrevista do card usada como insumo ({len(texto)} chars)")
    if not texto or len(texto.strip()) < 400:
        fonte = "ficha"
        texto = _ficha_texto(nome, cid)
    if not texto or len(texto.strip()) < 300:
        _log(f"[checklist] {nome}: sem insumo (nem transcricao nem ficha)")
        return None, "sem_insumo"

    itens = list(checklist)
    prompt = (
        "Voce prepara a LISTA DE DOCUMENTOS que sera cobrada por WhatsApp de "
        "um cliente do Escritório Modelo Advogados (escritorio trabalhista).\n\n"
        "Abaixo estao o RELATO DO CASO (transcricao da reuniao de fechamento "
        "ou ficha do cliente) e a LISTA BASE, desenhada para o CASO TRABALHISTA "
        "COMUM (verbas rescisorias, jornada, horas extras, intervalo, vinculo).\n\n"
        "PASSO 1: decida pelo relato se o caso e trabalhista comum ou ESPECIAL (gestante, acidente de trabalho, doenca ocupacional, assedio, rescisao indireta por fato especifico).\n\n"
        "PASSO 2a, SE FOR COMUM: confira a lista base e aponte APENAS os "
        "itens que o relato CONTRADIZ EXPRESSAMENTE (o cliente disse que nao "
        "existe, nao aconteceu ou nao tem como fornecer). REGRAS RIGIDAS: "
        "silencio NAO e contradicao; duvida MANTEM o item; nunca invente item "
        "novo; para cada remocao cite o TRECHO literal que justifica.\n\n"
        "PASSO 2b, SE FOR ESPECIAL: a lista base NAO SERVE para este "
        "cliente. Monte uma lista NOVA com 4 a 7 itens de documentos e provas "
        "adequados a ESTE caso concreto: so peca o que a pessoa plausivelmente "
        "tem (carteira de trabalho, exames e atestados, conversas com o "
        "empregador, holerites, comprovantes); linguagem simples de WhatsApp, "
        "sem juridiques, um item por linha, emoji discreto quando couber; "
        "nada de promessa de resultado; respeite o que o relato ja diz que "
        "nao existe; inclua o item do e-mail se estiver na lista base; NUNCA inclua extrato bancario (regra do escritorio: extrato e pedido pessoalmente pela equipe em fase posterior porque cliente teme golpe quando a automacao pede, nunca pela automacao).\n\n"
        "Responda SOMENTE JSON valido, sem cercas de codigo:\n"
        '{"caso_motorista": true, "remover": [{"item": "...", "motivo": "...", '
        '"trecho": "..."}]}\n'
        "ou\n"
        '{"caso_motorista": false, "lista_nova": ["...", "..."]}\n\n'
        "=== LISTA BASE (caso comum) ===\n"
        + "\n".join(f"- {i}" for i in itens)
        + "\n\n=== RELATO DO CASO ===\n" + texto[:100000])

    try:
        resp = _claude(prompt)
    except Exception as e:
        _log(f"[checklist] {nome}: CLI indisponivel ({str(e)[:120]})")
        return None, "cli_indisponivel"
    m = re.search(r"\{.*\}", resp, re.S)
    if not m:
        _log(f"[checklist] {nome}: resposta sem JSON")
        return None, "resposta_invalida"
    try:
        dados = json.loads(m.group(0))
    except Exception:
        try:
            dados = json.loads(m.group(0), strict=False)
        except Exception as e:
            _log(f"[checklist] {nome}: JSON invalido ({str(e)[:80]})")
            return None, "resposta_invalida"

    def norm(t):
        return re.sub(r"[^a-z0-9]+", "", (t or "").lower())

    if dados.get("caso_motorista") is False:
        lista = dados.get("lista_nova") or []
        lista = [str(i).strip() for i in lista
                 if isinstance(i, str) and 8 <= len(str(i).strip()) <= 140]
        if not 3 <= len(lista) <= 8:
            _log(f"[checklist] {nome}: lista gerada invalida ({len(lista)} itens)")
            return None, "resposta_invalida"
        _log(f"[checklist] {nome}: caso especial; lista propria "
             f"com {len(lista)} itens (fonte: {fonte})")
        for i in lista:
            _log(f"    ITEM: {i}")
        return lista, f"gerado para o caso ({fonte})"

    remover = dados.get("remover") or []
    if not isinstance(remover, list) or not remover:
        _log(f"[checklist] {nome}: caso comum, nada a remover ({len(itens)} itens, "
             f"fonte: {fonte})")
        return itens, f"confirmado pelo caso ({fonte})"
    fora = {norm(r.get("item")) for r in remover if isinstance(r, dict)}
    podado = [i for i in itens if norm(i) not in fora]
    if not podado or len(podado) < max(1, len(itens) // 2):
        _log(f"[checklist] {nome}: poda suspeita ({len(itens)} -> {len(podado)}), "
             "mantendo a lista base")
        return itens, f"poda suspeita ignorada ({fonte})"
    _log(f"[checklist] {nome}: {len(itens)} -> {len(podado)} itens (fonte: {fonte})")
    for r in remover:
        if isinstance(r, dict) and norm(r.get("item")) in {norm(i) for i in itens}:
            _log(f"    REMOVIDO: {r.get('item')}"
                 f"\n      motivo: {(r.get('motivo') or '')[:160]}"
                 f"\n      trecho: {(r.get('trecho') or '')[:220]}")
    return podado, f"podado pelo caso ({fonte})"


if __name__ == "__main__":
    import argparse
    from cobranca_documentos import CHECKLIST_PADRAO
    ap = argparse.ArgumentParser(description="Poda o checklist pela transcricao")
    ap.add_argument("--nome", required=True)
    ap.add_argument("--telefone", default="")
    ap.add_argument("--cid", type=int, default=None)
    a = ap.parse_args()
    r, origem = montar(a.nome, a.telefone, list(CHECKLIST_PADRAO), cid=a.cid)
    print()
    print("RESULTADO:", "checklist padrao (sem poda)" if r is None else "")
    for i in (r or CHECKLIST_PADRAO):
        print("  -", i)
