"""Cliente D-API (https://d-api.cloud) — alternativa a Z-API.

Como um numero passa a usar a D-API (sem coluna nova no banco):
    wa_numeros.zapi_instance = "dapi:<sessionId>"     (ex.: "dapi:esc-principal")
    wa_numeros.zapi_token    = <API Key da D-API>     (painel app.d-api.cloud)
    wa_numeros.zapi_client_token = ""                 (nao usado)
O zapi.py detecta o prefixo "dapi:" e desvia cada chamada para ca, entao o resto
do sistema continua chamando zapi.enviar_texto(...) sem saber qual provedor e.

Webhook: a D-API manda para a MESMA URL do Z-API (/api/atendimento/webhook/{id}/{secret});
o zapi.parse_webhook reconhece o envelope {"event": ..., "sessionId": ..., "data": ...}
e chama parse_webhook daqui. Doc: https://docs.d-api.cloud
"""
import json
import re
import sqlite3
from pathlib import Path

import requests

BASE = "https://api.d-api.cloud"
PREFIXO = "dapi:"

# Enquetes espelhadas no sistema para a resposta aparecer ali. Antes, enquete e voto
# chegavam sem texto e o webhook descartava; so ficava a nota do robo. Agora a enquete aparece no chat com a
# pergunta e as opcoes (anotadas no envio pelo SYNC/wa_dapi.py, tabela wa_enquetes) e o voto aparece como
# mensagem do cliente. Enquete mandada pelo celular nao tem a pergunta: fica o aviso ENQUETE_SEM_TEXTO.
_DB = Path(__file__).resolve().parents[2] / "sistema_esc.db"
ENQUETE_SEM_TEXTO = "Enquete enviada (o WhatsApp não manda a pergunta para o sistema)"


def texto_enquete(pergunta, opcoes):
    return "Enquete: %s\n%s" % (pergunta, "\n".join("• %s" % o for o in opcoes))


def texto_voto(escolhas, pergunta=None):
    escolha = ", ".join(str(o) for o in (escolhas or []) if str(o).strip())
    if not escolha:
        return "Tirou o voto da enquete" + (' "%s"' % pergunta if pergunta else "")
    if pergunta:
        return 'Respondeu na enquete "%s": %s' % (pergunta, escolha)
    return "Respondeu na enquete: %s" % escolha


def enquete_anotada(message_id):
    """{'pergunta', 'opcoes'} da enquete que o robo anotou no envio, ou None."""
    if not message_id:
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % _DB, uri=True, timeout=2)
        try:
            r = con.execute("SELECT pergunta, opcoes FROM wa_enquetes WHERE message_id=?", (message_id,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not r:
        return None
    try:
        opcoes = json.loads(r[1] or "[]")
    except ValueError:
        opcoes = []
    return {"pergunta": r[0] or "", "opcoes": opcoes}


def eh_dapi(numero) -> bool:
    return str(getattr(numero, "zapi_instance", "") or "").startswith(PREFIXO)


def _sessao(numero) -> str:
    return str(numero.zapi_instance)[len(PREFIXO):]


def _headers(numero):
    return {"Authorization": numero.zapi_token, "Content-Type": "application/json"}


def _destino(telefone) -> str:
    """D-API quer so digitos com DDI (5511999999999). @lid vai como veio."""
    t = str(telefone or "")
    if "@" in t:
        return t
    return re.sub(r"\D", "", t)


def _id_da_resposta(j) -> str:
    """O id pode vir em lugares diferentes; e ele que casa com o eco do webhook (dedup)."""
    if not isinstance(j, dict):
        return ""
    for chave in ("messageId", "id", "message_id"):
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


def _post(numero, caminho, corpo, timeout=60):
    corpo = dict(corpo, sessionId=_sessao(numero))
    try:
        r = requests.post(f"{BASE}/api/v1/{caminho}", json=corpo, headers=_headers(numero), timeout=timeout)
        if r.status_code in (200, 201):
            try:
                j = r.json()
            except Exception:
                return True, ""
            if isinstance(j, dict) and j.get("success") is False:
                return False, str(j.get("error") or j)[:300]
            return True, _id_da_resposta(j)
        return False, (r.text[:300] if r is not None else "")
    except Exception as e:
        return False, str(e)


# ---------------- envio (mesma assinatura do zapi.py) ----------------

def enviar_texto(numero, telefone, mensagem):
    return _post(numero, "messages/send/text", {"to": _destino(telefone), "text": mensagem}, timeout=30)


def editar_texto(numero, telefone, message_id, texto):
    """Edita no WhatsApp do cliente o texto de uma mensagem ja enviada.
    PUT /api/v1/messages/{id}. A D-API so repassa ao WhatsApp e responde sucesso ate para id
    inexistente; o WhatsApp so aplica ate 15 min depois do envio. Quem chama confere essas regras."""
    corpo = {"sessionId": _sessao(numero), "to": _destino(telefone), "text": texto}
    try:
        r = requests.put(f"{BASE}/api/v1/messages/{message_id}", json=corpo, headers=_headers(numero), timeout=30)
        if r.status_code in (200, 201):
            try:
                j = r.json()
            except Exception:
                return True, ""
            if isinstance(j, dict) and j.get("success") is False:
                return False, str(j.get("error") or j)[:300]
            return True, ""
        return False, r.text[:300]
    except Exception as e:
        return False, str(e)


def digitando(numero, telefone, ms=4000):
    """Acende o "digitando..." no WhatsApp do contato enquanto a resposta nao sai.
    O chatId quer o sufixo @c.us; @lid ja vem com sufixo proprio.
    Falha aqui nunca derruba o envio: e so enfeite."""
    destino = _destino(telefone)
    chat = destino if "@" in destino else destino + "@c.us"
    return _post(numero, "chats/presence",
                 {"chatId": chat, "presence": "typing", "durationMs": int(ms)}, timeout=10)


def marcar_lido(numero, telefone):
    """Tira o negrito de nao-lida do lado do contato antes de responder."""
    destino = _destino(telefone)
    chat = destino if "@" in destino else destino + "@c.us"
    return _post(numero, "chats/read", {"chatId": chat}, timeout=10)


def enviar_audio(numero, telefone, audio_url):
    return _post(numero, "messages/send/audio", {"to": _destino(telefone), "audio": audio_url, "ptt": True})


def enviar_imagem(numero, telefone, image_url, caption=""):
    return _post(numero, "messages/send/image", {"to": _destino(telefone), "image": image_url, "caption": caption or ""})


def enviar_video(numero, telefone, video_url, caption=""):
    """Video que toca na conversa. Faltava: o zapi.enviar_video ja chamava esta funcao. Mesmo corpo dos robos que ja mandam video."""
    return _post(numero, "messages/send/video", {"to": _destino(telefone), "video": video_url, "caption": caption or ""},
                 timeout=120)


_MIME = {"pdf": "application/pdf", "doc": "application/msword",
         "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "xls": "application/vnd.ms-excel",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "txt": "text/plain", "zip": "application/zip", "jpg": "image/jpeg", "jpeg": "image/jpeg",
         "png": "image/png", "mp4": "video/mp4", "ogg": "audio/ogg", "mp3": "audio/mpeg"}


def enviar_documento(numero, telefone, doc_url, filename, ext):
    ext = str(ext or "").lower().lstrip(".")
    return _post(numero, "messages/send/document", {
        "to": _destino(telefone), "document": doc_url,
        "fileName": filename or f"arquivo.{ext or 'pdf'}",
        "mimetype": _MIME.get(ext, "application/octet-stream")})


def enviar_figurinha(numero, telefone, sticker_url):
    return _post(numero, "messages/send/sticker", {"to": _destino(telefone), "sticker": sticker_url})


# ---------------- consulta ----------------

def resolver_chat(numero, identificador):
    """@lid -> (telefone, nome, foto). Na D-API o webhook ja traz o numero junto do lid,
    entao isto so e usado de reserva."""
    try:
        r = requests.get(f"{BASE}/api/v1/chats/{identificador}", headers=_headers(numero),
                         params={"sessionId": _sessao(numero)}, timeout=15)
        if r.status_code == 200:
            j = r.json()
            d = next((j[k] for k in ("chat", "data") if isinstance(j.get(k), dict)), j)
            jid = str(d.get("jid") or d.get("phone") or d.get("id") or "")
            fone = jid.split("@")[0] if jid.endswith("@s.whatsapp.net") else re.sub(r"\D", "", str(d.get("phone") or ""))
            return fone, (d.get("name") or d.get("pushName") or ""), (d.get("profilePicUrl") or d.get("picture") or "")
    except Exception:
        pass
    return "", "", ""


def status_instancia(numero):
    try:
        r = requests.get(f"{BASE}/api/v1/sessions/{_sessao(numero)}", headers=_headers(numero), timeout=15)
        if r.status_code != 200:
            return {"error": r.text[:200]}
        j = r.json()
        d = next((j[k] for k in ("session", "data") if isinstance(j.get(k), dict)), j)
        st = str(d.get("status") or d.get("state") or "").lower()
        return {"connected": st in ("connected", "open", "online", "working"), "smartphoneConnected": st == "connected",
                "provedor": "d-api", "status": st}
    except Exception as e:
        return {"error": str(e)}


# ---------------- webhook ----------------

def eh_payload_dapi(payload) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("data"), dict) and str(payload.get("event") or "").startswith("messages.")


def _numero_do(parte) -> str:
    """{'jid': '5511...@s.whatsapp.net', 'lid': '...@lid'} -> '5511...' (ou o @lid se nao houver numero)."""
    if not isinstance(parte, dict):
        return ""
    jid = str(parte.get("jid") or "")
    if jid.endswith("@s.whatsapp.net"):
        return jid.split("@")[0]
    if jid.endswith("@lid"):
        return jid
    return str(parte.get("lid") or jid)


def parse_webhook(payload: dict):
    """Converte o evento da D-API no MESMO dict que zapi.parse_webhook devolve."""
    evento = payload.get("event")
    if evento not in ("messages.received", "messages.sent"):
        return None   # status de entrega/leitura, presenca, grupos: ignorados (igual ao Z-API)
    d = payload.get("data") or {}
    if d.get("is_group"):
        return None
    fromme = bool(d.get("fromMe")) or evento == "messages.sent"
    contato = d.get("to") if fromme else d.get("from")
    telefone = _numero_do(contato)
    if not telefone or "@g.us" in telefone or "@newsletter" in telefone or "@broadcast" in telefone:
        return None
    if fromme:
        nome = (contato or {}).get("name") or ""
    else:
        nome = d.get("from_name") or (contato or {}).get("name") or ""

    t = str(d.get("type") or "text")
    texto = d.get("message") or d.get("body") or ""
    media = d.get("media_data") or {}
    midia = d.get("media_url") or ""
    tipo, conteudo = "texto", ""
    if t in ("text", "edited_message", "list_response", "template_button_reply"):
        conteudo = str(texto)
    elif t == "audio":
        tipo = "audio"
    elif t in ("image", "video"):
        # o sistema so tem 'imagem' e 'doc'; video vai como doc (abre/baixa pelo link)
        tipo = "imagem" if t == "image" else "doc"
        conteudo = media.get("caption") or ""
    elif t == "document":
        tipo = "doc"
        conteudo = media.get("caption") or ""
    elif t == "sticker":
        tipo = "figurinha"
    elif t == "contact":
        info = d.get("data") or {}
        from .zapi import _texto_contato
        conteudo = _texto_contato({"contact": {
            "displayName": info.get("display_name") or info.get("contact_name") or "",
            "phones": [info.get("contact_phone")] if info.get("contact_phone") else [],
            "vCard": info.get("vcard") or ""}})
    elif t == "location":
        info = d.get("data") or {}
        lat, lon = info.get("degrees_latitude"), info.get("degrees_longitude")
        conteudo = f"Localização: https://maps.google.com/?q={lat},{lon}" if lat is not None else str(texto)
    elif t == "reaction":
        return None   # reacao nao vira mensagem nova (Z-API tambem nao gravava)
    elif t == "poll":
        info = d.get("data") or {}
        anot = enquete_anotada(str(d.get("id") or "")) or enquete_anotada(str(d.get("message_id") or "")) or {}
        pergunta = info.get("question") or info.get("name") or anot.get("pergunta")
        opcoes = [o.get("name") if isinstance(o, dict) else str(o)
                  for o in (info.get("options") or anot.get("opcoes") or [])]
        conteudo = texto_enquete(pergunta, opcoes) if pergunta else ENQUETE_SEM_TEXTO
    elif t == "poll_update":
        info = d.get("data") or {}
        anot = enquete_anotada(str(info.get("poll_message_id") or "")) or {}
        conteudo = texto_voto(info.get("selected_options"), anot.get("pergunta"))
    else:
        conteudo = str(texto)
    if tipo == "texto" and not (conteudo or "").strip():
        return None
    foto = (contato or {}).get("profilePicUrl") or (contato or {}).get("picture") or ""
    return {"telefone": str(telefone), "nome": nome, "tipo": tipo,
            "conteudo": conteudo, "midia_url": midia,
            "direcao": "out" if fromme else "in",
            "remote_id": str(d.get("id") or ""), "foto": foto}
