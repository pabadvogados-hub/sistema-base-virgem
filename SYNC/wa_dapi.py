# -*- coding: utf-8 -*-
"""
Saida UNICA de WhatsApp dos robos da VPS pelo numero oficial do escritorio (D-API).

Historia: o numero oficial (wa_numeros, DAPI_NUMERO_ID) esta na D-API ("dapi:<sessao>").
A Z-API desconectava sem aviso, e todo robo
que ainda chamava api.z-api.io passou a perder mensagem calado (a Z-API aceita a chamada
e devolve 200 mesmo desconectada). Este modulo e o caminho unico daqui para frente.

Dois jeitos de usar:

  1) Funcoes diretas (codigo novo):
       import wa_dapi
       ok, info = wa_dapi.enviar_texto("5511999999999", "oi")
       ok, info = wa_dapi.enviar_documento(fone, url_ou_base64, "arquivo.pdf", legenda="...")  # base64 vai como data URI

  2) Troca direta de chamada Z-API (migrar script antigo sem reescrever):
       r = wa_dapi.post_zapi("send-text", {"phone": fone, "message": texto})
       r = wa_dapi.get_zapi("status")
     r imita o requests.Response da Z-API: status_code, ok, text, json(), raise_for_status().
     Em sucesso o JSON traz messageId, zaapId e id (o mesmo id da D-API), entao checagens
     antigas como `"zaapId" in r.text` ou `r.json().get("messageId")` continuam valendo.
     Em falha, status_code NAO e 2xx (mesmo quando a D-API respondeu 200 com success:false).

Scripts fora de /opt/escritorio/SYNC:  sys.path.insert(0, "/opt/escritorio/SYNC")

Credenciais: wa_numeros (id 1) do banco do SISTEMA (leitura; precisa ser root) ou,
se nao der, DAPI_SESSION / DAPI_TOKEN do ambiente. Nunca imprime a chave.
Eco: o webhook do SISTEMA recebe o `messages.sent` e grava a mensagem na conversa
do cliente sozinho, entao quem envia por aqui NAO precisa gravar historico.
"""
import base64
import json
import os
import re
import sqlite3
import uuid

import requests

DB = "/opt/escritorio/SISTEMA/backend/sistema_esc.db"
BASE = "https://api.d-api.cloud/api/v1"
NUMERO_ID = int(os.getenv("DAPI_NUMERO_ID") or 1)
TIMEOUT = 60

# Toda mensagem de texto dos robos sai assinada na 1a linha, em negrito do WhatsApp, com ASSINATURA_AUTOMACAO
# do .env. remetente=None -> essa assinatura; remetente="" -> sem assinatura; outro nome -> esse nome.
ASSINATURA = (os.getenv("ASSINATURA_AUTOMACAO") or "Equipe Escritório Modelo").strip()


def assinar(texto, remetente=None):
    # dois pontos depois do nome, "*Nome:*". "*Nome*" tambem conta como ja assinada.
    nome = ASSINATURA if remetente is None else str(remetente).strip()
    t = str(texto or "")
    if not nome or not t.strip():
        return t
    cab = "*%s:*" % nome
    if t.lstrip().startswith((cab, "*%s*" % nome)):
        return t
    return cab + "\n" + t


_MIME = {"pdf": "application/pdf", "doc": "application/msword",
         "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "xls": "application/vnd.ms-excel",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "csv": "text/csv", "txt": "text/plain", "zip": "application/zip",
         "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
         "mp4": "video/mp4", "ogg": "audio/ogg", "mp3": "audio/mpeg", "m4a": "audio/mp4"}
_EXT_DO_MIME = {v: k for k, v in _MIME.items()}


class DapiConfigError(RuntimeError):
    """Sem credencial: falha alto, nunca envia meio configurado."""


# ------------------------------------------------------------------ credencial

def _cred():
    sessao = token = ""
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
        try:
            r = con.execute("select zapi_instance, zapi_token from wa_numeros where id=?",
                            (NUMERO_ID,)).fetchone()
        finally:
            con.close()
        if r and str(r[0] or "").startswith("dapi:"):
            sessao, token = str(r[0])[5:], str(r[1] or "")
    except Exception:
        pass
    if not (sessao and token):
        sessao = (os.getenv("DAPI_SESSION") or "").replace("dapi:", "").strip()
        token = (os.getenv("DAPI_TOKEN") or "").strip()
    if not (sessao and token):
        raise DapiConfigError(f"sem credencial D-API (wa_numeros id {NUMERO_ID} nem DAPI_SESSION/DAPI_TOKEN)")
    return sessao, token


# ------------------------------------------------------------------ destino

def destino(telefone):
    """Numero no formato da D-API: so digitos com DDI 55. Grupo vira ...@g.us; @lid vai como veio.

    Aceita o que os scripts da Z-API passavam: '11 99999-9999', '5511999999999',
    '5511999999999@c.us', '120363...-group' (grupo no formato Z-API)."""
    t = str(telefone or "").strip()
    if not t:
        return ""
    if t.endswith("-group"):
        return t[:-len("-group")] + "@g.us"
    if "@" in t:
        if t.endswith(("@c.us", "@s.whatsapp.net")):
            return re.sub(r"\D", "", t.split("@")[0])
        return t
    d = re.sub(r"\D", "", t)
    if len(d) in (10, 11):          # DDD + numero, sem DDI
        d = "55" + d
    return d


# ------------------------------------------------------------------ http

def _id_da_resposta(j):
    if not isinstance(j, dict):
        return ""
    for chave in ("messageId", "id", "message_id", "zaapId"):
        if j.get(chave):
            return str(j[chave])
    for sub in ("data", "message", "result"):
        d = j.get(sub)
        if isinstance(d, dict):
            for chave in ("messageId", "id", "message_id"):
                if d.get(chave):
                    return str(d[chave])
            k = d.get("key")
            if isinstance(k, dict) and k.get("id"):
                return str(k["id"])
    k = j.get("key")
    if isinstance(k, dict) and k.get("id"):
        return str(k["id"])
    return ""


def _post(caminho, corpo, timeout=TIMEOUT):
    """-> (ok, json_dict, http_status). ok so com 2xx e sem success:false."""
    sessao, token = _cred()
    corpo = dict(corpo, sessionId=sessao)
    try:
        r = requests.post(f"{BASE}/{caminho}", json=corpo,
                          headers={"Authorization": token, "Content-Type": "application/json"},
                          timeout=timeout)
    except requests.RequestException as e:
        return False, {"erro": f"{type(e).__name__}: {e}"}, 0
    try:
        j = r.json()
    except ValueError:
        j = {"raw": r.text[:500]}
    if not isinstance(j, dict):
        j = {"raw": j}
    ok = r.status_code in (200, 201) and j.get("success") is not False
    if ok:
        mid = _id_da_resposta(j)
        j.setdefault("messageId", mid)
        j.setdefault("zaapId", mid)
        j.setdefault("id", mid)
        if caminho == "messages/send/poll":
            _anotar_enquete(corpo, j.get("messageId"))
    return ok, j, r.status_code


# Enquetes espelhadas no sistema para a resposta aparecer ali. O WhatsApp devolve a
# enquete enviada SEM a pergunta. Aqui, no envio, pergunta e opcoes ficam em wa_enquetes (sistema_esc.db); o
# webhook do sistema (app/whatsapp/dapi.py) mostra a enquete no chat e o voto como resposta do cliente. Se o eco
# da enquete chegou antes desta anotacao, o balao provisorio e completado aqui. Falha aqui nunca derruba o envio.
ENQUETE_SEM_TEXTO = "Enquete enviada (o WhatsApp não manda a pergunta para o sistema)"
SQL_ENQUETES = ("CREATE TABLE IF NOT EXISTS wa_enquetes (message_id TEXT PRIMARY KEY, telefone TEXT, "
                "pergunta TEXT, opcoes TEXT, criado_em TEXT)")


def texto_enquete(pergunta, opcoes):
    return "Enquete: %s\n%s" % (pergunta, "\n".join("• %s" % o for o in opcoes))


def _anotar_enquete(corpo, mid):
    if not mid:
        return
    try:
        import datetime as _dt
        pergunta = str(corpo.get("question") or "")
        opcoes = [str(o.get("name") if isinstance(o, dict) else o) for o in (corpo.get("options") or [])]
        con = sqlite3.connect(DB, timeout=30)
        try:
            con.execute(SQL_ENQUETES)
            con.execute("INSERT OR REPLACE INTO wa_enquetes (message_id, telefone, pergunta, opcoes, criado_em) "
                        "VALUES (?,?,?,?,?)", (str(mid), str(corpo.get("to") or ""), pergunta,
                                               json.dumps(opcoes, ensure_ascii=False),
                                               _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
            if pergunta:
                con.execute("UPDATE wa_mensagens SET conteudo=? WHERE remote_id=? AND conteudo=?",
                            (texto_enquete(pergunta, opcoes), str(mid), ENQUETE_SEM_TEXTO))
            con.commit()
        finally:
            con.close()
    except Exception as e:
        print("[wa_dapi] enquete %s nao anotada: %s" % (mid, e))


def _falha(info, **extra):
    info = dict(info or {})
    info.update(extra)
    return False, info


# ------------------------------------------------------------------ midia

def _como_url(conteudo, nome="arquivo", ext=""):
    """URL passa direto. Arquivo em base64 vai como data URI (a D-API aceita, testado em - nunca vira arquivo publico: os PDFs do kit sao privados por regra."""
    s = str(conteudo or "").strip()
    if s.startswith(("http://", "https://", "data:")):
        return s
    try:
        base64.b64decode(s[:64] + "=" * (-len(s[:64]) % 4), validate=False)
    except Exception:
        raise ValueError("conteudo nao e URL nem base64")
    ext = (ext or (nome.rsplit(".", 1)[-1] if "." in nome else "")).lower().lstrip(".")
    return f"data:{_MIME.get(ext, 'application/octet-stream')};base64,{s}"


# ------------------------------------------------------------------ envio

def enviar_texto(telefone, texto, remetente=None):
    if not str(texto or "").strip():
        return False, {"erro": "texto vazio"}
    texto = assinar(texto, remetente)
    d = destino(telefone)
    if not d:
        return False, {"erro": "telefone vazio ou invalido"}
    ok, j, st = _post("messages/send/text", {"to": d, "text": str(texto)})
    return (ok, j) if ok else _falha(j, http_status=st)


def enviar_imagem(telefone, imagem, legenda="", remetente=None):
    if str(legenda or "").strip():
        legenda = assinar(legenda, remetente)
    d = destino(telefone)
    if not d:
        return False, {"erro": "telefone vazio ou invalido"}
    try:
        url = _como_url(imagem, "imagem", "")
    except ValueError as e:
        return False, {"erro": str(e)}
    ok, j, st = _post("messages/send/image", {"to": d, "image": url, "caption": legenda or ""})
    return (ok, j) if ok else _falha(j, http_status=st)


def enviar_documento(telefone, documento, nome_arquivo="arquivo.pdf", legenda="", ext="", remetente=None):
    """Documento por URL ou base64. A legenda vai numa mensagem de texto logo depois
    (o envio de documento da D-API nao tem legenda garantida)."""
    d = destino(telefone)
    if not d:
        return False, {"erro": "telefone vazio ou invalido"}
    ext = (ext or (nome_arquivo.rsplit(".", 1)[-1] if "." in (nome_arquivo or "") else "pdf")).lower().lstrip(".")
    try:
        url = _como_url(documento, nome_arquivo or "arquivo", ext)
    except ValueError as e:
        return False, {"erro": str(e)}
    ok, j, st = _post("messages/send/document", {
        "to": d, "document": url, "fileName": nome_arquivo or f"arquivo.{ext}",
        "mimetype": _MIME.get(ext, "application/octet-stream")})
    if not ok:
        return _falha(j, http_status=st)
    if str(legenda or "").strip():
        enviar_texto(d, legenda, remetente)
    return ok, j


def enviar_audio(telefone, audio, ptt=True):
    d = destino(telefone)
    if not d:
        return False, {"erro": "telefone vazio ou invalido"}
    try:
        url = _como_url(audio, "audio", "ogg")
    except ValueError as e:
        return False, {"erro": str(e)}
    ok, j, st = _post("messages/send/audio", {"to": d, "audio": url, "ptt": bool(ptt)})
    return (ok, j) if ok else _falha(j, http_status=st)


def enviar_video(telefone, video, legenda="", remetente=None):
    """Video que TOCA na conversa, pela rota messages/send/video da D-API.

    Ate isso ia como documento mp4 e o cliente recebia arquivo para baixar. A rota de
    video existe: sondada com corpo vazio, devolve 400 pedindo sessionId, to e video, enquanto
    messages/send/media e messages/send/file devolvem 404. O sessionId quem poe e o _post.

    Se a rota recusar, cai no documento mp4, que e o que a casa fazia antes.
    """
    d = destino(telefone)
    if not d:
        return False, {"erro": "telefone vazio ou invalido"}
    try:
        url = _como_url(video, "video", "mp4")
    except ValueError as e:
        return False, {"erro": str(e)}
    corpo = {"to": d, "video": url}
    if str(legenda or "").strip():
        corpo["caption"] = assinar(legenda, remetente)
    ok, j, st = _post("messages/send/video", corpo)
    if ok:
        return ok, j
    print("[wa_dapi] video pela rota falhou (%s), mando como documento mp4" % str(j)[:120])
    return enviar_documento(telefone, video, "video.mp4", legenda=legenda, ext="mp4",
                            remetente=remetente)


def digitando(telefone, ms=3000):
    d = destino(telefone)
    chat = d if "@" in d else d + "@c.us"
    ok, _, _ = _post("chats/presence", {"chatId": chat, "presence": "typing", "durationMs": int(ms)}, timeout=10)
    return ok


# ------------------------------------------------------------------ consulta

def status():
    """Formato da Z-API: {'connected', 'smartphoneConnected', 'status', 'provedor'} ou {'error'}."""
    try:
        sessao, token = _cred()
        r = requests.get(f"{BASE}/sessions/{sessao}", headers={"Authorization": token}, timeout=15)
        if r.status_code != 200:
            return {"connected": False, "smartphoneConnected": False, "error": r.text[:200]}
        j = r.json()
        d = next((j[k] for k in ("session", "data") if isinstance(j.get(k), dict)), j)
        st = str(d.get("status") or d.get("state") or "").lower()
        conectado = st in ("connected", "open", "online", "working")
        return {"connected": conectado, "smartphoneConnected": st == "connected",
                "status": st, "provedor": "d-api", "error": "" if conectado else f"sessao {st or '?'}"}
    except Exception as e:
        return {"connected": False, "smartphoneConnected": False, "error": f"{type(e).__name__}: {e}"}


def conectado():
    return bool(status().get("connected"))


def foto_perfil(telefone):
    """Link da foto de perfil ('' se nao tiver)."""
    try:
        sessao, token = _cred()
        r = requests.get(f"{BASE}/chats/{destino(telefone)}", headers={"Authorization": token},
                         params={"sessionId": sessao}, timeout=15)
        if r.status_code == 200:
            j = r.json()
            d = next((j[k] for k in ("chat", "data") if isinstance(j.get(k), dict)), j)
            return str(d.get("profilePicUrl") or d.get("picture") or d.get("imgUrl") or "")
    except Exception:
        pass
    return ""


# ------------------------------------------------------------------ compat Z-API

class Resposta:
    """Imita o requests.Response que os scripts recebiam da Z-API."""

    def __init__(self, ok, corpo, status_code):
        self.ok = bool(ok)
        if self.ok:
            self.status_code = status_code if status_code in (200, 201) else 200
        else:
            self.status_code = status_code if (status_code and status_code >= 400) else 502
        self._corpo = corpo if isinstance(corpo, (dict, list)) else {"raw": corpo}
        self.text = json.dumps(self._corpo, ensure_ascii=False)
        self.content = self.text.encode("utf-8")
        self.headers = {"Content-Type": "application/json"}
        self.reason = "OK" if self.ok else "D-API recusou"
        self.url = "d-api"

    def json(self):
        return self._corpo

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"D-API {self.status_code}: {self.text[:300]}", response=self)


def _texto_de_opcoes(p):
    """Botoes/listas da Z-API viram texto numerado (WhatsApp comum nao mostra botao)."""
    linhas = [str(p.get("message") or p.get("title") or "").strip()]
    ops = p.get("buttonList", {}).get("buttons") if isinstance(p.get("buttonList"), dict) else None
    ops = ops or p.get("buttonActions") or (p.get("optionList") or {}).get("options") or []
    for i, o in enumerate(ops, 1):
        rot = o.get("label") or o.get("title") or o.get("description") or "" if isinstance(o, dict) else str(o)
        if rot:
            linhas.append(f"{i}. {rot}")
    return "\n".join(l for l in linhas if l)


def post_zapi(endpoint, payload=None, timeout=TIMEOUT):
    """POST no formato da Z-API ('send-text', 'send-image', 'send-document/pdf', ...) pela D-API."""
    p = dict(payload or {})
    ep = str(endpoint or "").strip("/").split("?")[0]
    fone = p.get("phone") or p.get("to") or ""
    try:
        if ep == "send-text":
            ok, j = enviar_texto(fone, p.get("message") or p.get("text") or "")
        elif ep == "send-link":
            ok, j = enviar_texto(fone, p.get("message") or p.get("linkUrl") or "")
        elif ep == "send-image":
            ok, j = enviar_imagem(fone, p.get("image") or "", p.get("caption") or "")
        elif ep.startswith("send-document"):
            ext = ep.split("/", 1)[1] if "/" in ep else ""
            ok, j = enviar_documento(fone, p.get("document") or "", p.get("fileName") or f"arquivo.{ext or 'pdf'}",
                                     legenda=p.get("caption") or "", ext=ext)
        elif ep in ("send-audio", "send-ptv"):
            ok, j = enviar_audio(fone, p.get("audio") or "")
        elif ep == "send-video":
            ok, j = enviar_video(fone, p.get("video") or "", p.get("caption") or "")
        elif ep in ("send-button-list", "send-button-actions", "send-option-list", "send-buttons"):
            ok, j = enviar_texto(fone, _texto_de_opcoes(p))
        elif ep in ("read-message", "modify-chat", "send-chat-state"):
            ok, j = True, {"ignorado": ep}
        else:
            return Resposta(False, {"erro": f"endpoint Z-API '{ep}' sem traducao para a D-API em wa_dapi.py"}, 501)
    except DapiConfigError as e:
        return Resposta(False, {"erro": str(e)}, 500)
    return Resposta(ok, j, 200 if ok else (j.get("http_status") or 502))


def get_zapi(endpoint, params=None, timeout=15):
    """GET no formato da Z-API ('status', 'profile-picture?phone=', 'phone-exists/<fone>')."""
    ep = str(endpoint or "").strip("/")
    base, _, qs = ep.partition("?")
    params = dict(params or {})
    for par in filter(None, qs.split("&")):
        k, _, v = par.partition("=")
        params.setdefault(k, v)
    if base == "status":
        s = status()
        return Resposta(True, s, 200)
    if base == "profile-picture":
        return Resposta(True, {"link": foto_perfil(params.get("phone") or "")}, 200)
    if base.startswith("phone-exists"):
        # a D-API nao tem essa consulta aqui: responde "existe" para nao bloquear envio;
        # numero invalido falha no proprio envio (ok=False)
        fone = base.split("/", 1)[1] if "/" in base else params.get("phone", "")
        return Resposta(True, {"exists": True, "phone": destino(fone), "verificado": False}, 200)
    return Resposta(False, {"erro": f"endpoint Z-API '{base}' sem traducao para a D-API em wa_dapi.py"}, 501)


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False))
