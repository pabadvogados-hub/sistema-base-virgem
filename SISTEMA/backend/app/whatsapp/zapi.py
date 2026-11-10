"""Cliente Z-API (WhatsApp gateway). Envio de mensagem + parse do webhook.

Cada numero tem sua propria instancia/token (multi-numero). Doc: developer.z-api.io
"""
import re
import requests

from . import dapi   # numero com zapi_instance "dapi:<sessao>" usa a D-API

BASE = "https://api.z-api.io"

# Toda mensagem de texto sai com o nome de quem manda na primeira linha, em negrito do WhatsApp.
# Pessoa da equipe = nome do cadastro; automacao = ASSINATURA_AUTOMACAO (configuravel no .env).
# remetente=None -> assinatura da automacao (padrao de todo robo e fluxo); remetente="" -> sem assinatura.
import os as _os
ASSINATURA_AUTOMACAO = (_os.getenv('ASSINATURA_AUTOMACAO') or 'Equipe Escritório Modelo').strip()


def nome_assinatura(user):
    """Nome que assina a mensagem de um usuario logado. Conta de robo (nome com "(IA)" ou
    "(automacao)") assina como a automacao."""
    nome = str(getattr(user, "nome", "") or getattr(user, "email", "") or "").strip()
    if not nome or re.search(r"\((ia|automa[cç][aã]o)\)", nome, re.I):
        return ASSINATURA_AUTOMACAO
    return nome


def assinar(mensagem, remetente=None):
    """Poe "*Nome:*" na primeira linha. Nao duplica se ja estiver assinada.
    Dois pontos depois do nome. "*Nome*" (formato da manha de 08/10) conta como assinada."""
    nome = ASSINATURA_AUTOMACAO if remetente is None else str(remetente).strip()
    texto = str(mensagem or "")
    if not nome or not texto.strip():
        return texto
    cabecalho = f"*{nome}:*"
    if texto.lstrip().startswith((cabecalho, f"*{nome}*")):
        return texto
    return f"{cabecalho}\n{texto}"


def enviar_texto(numero, telefone, mensagem, remetente=None):
    """Envia texto. Retorna (ok, info) — info = messageId no sucesso, erro no fracasso.
    remetente: quem assina a 1a linha (None = ASSINATURA_AUTOMACAO; "" = sem assinatura)."""
    mensagem = assinar(mensagem, remetente)
    if dapi.eh_dapi(numero):
        return dapi.enviar_texto(numero, telefone, mensagem)
    url = f"{BASE}/instances/{numero.zapi_instance}/token/{numero.zapi_token}/send-text"
    headers = {"Content-Type": "application/json"}
    if numero.zapi_client_token:
        headers["Client-Token"] = numero.zapi_client_token
    try:
        r = requests.post(url, json={"phone": telefone, "message": mensagem}, headers=headers, timeout=30)
        ok = r.status_code in (200, 201)
        if ok:
            try:
                j = r.json()
                return True, (j.get("messageId") or j.get("id") or "")
            except Exception:
                return True, ""
        return False, (r.text[:300] if r is not None else "")
    except Exception as e:
        return False, str(e)


def digitando(numero, telefone, ms=4000):
    """Acende o "digitando..." enquanto a IA pensa. Silencioso de proposito:
    e enfeite, e enfeite quebrado nunca pode segurar a resposta. No Z-API nao ha
    equivalente usado aqui, entao e no-op."""
    if dapi.eh_dapi(numero):
        try:
            return dapi.digitando(numero, telefone, ms)
        except Exception as e:
            return False, str(e)
    return False, "sem presenca no Z-API"


def marcar_lido(numero, telefone):
    """Idem: melhor esforco, nunca derruba o envio."""
    if dapi.eh_dapi(numero):
        try:
            return dapi.marcar_lido(numero, telefone)
        except Exception as e:
            return False, str(e)
    return False, "sem leitura no Z-API"


def enviar_audio(numero, telefone, audio_url):
    """Envia audio (mensagem de voz) via Z-API. audio_url = URL publica do .ogg."""
    if dapi.eh_dapi(numero):
        return dapi.enviar_audio(numero, telefone, audio_url)
    url = f"{BASE}/instances/{numero.zapi_instance}/token/{numero.zapi_token}/send-audio"
    headers = {"Content-Type": "application/json"}
    if numero.zapi_client_token:
        headers["Client-Token"] = numero.zapi_client_token
    try:
        r = requests.post(url, json={"phone": telefone, "audio": audio_url}, headers=headers, timeout=60)
        ok = r.status_code in (200, 201)
        if ok:
            try:
                j = r.json()
                return True, (j.get("messageId") or j.get("id") or "")
            except Exception:
                return True, ""
        return False, (r.text[:300] if r is not None else "")
    except Exception as e:
        return False, str(e)


def _post_zapi(numero, caminho, payload, timeout=60):
    url = f"{BASE}/instances/{numero.zapi_instance}/token/{numero.zapi_token}/{caminho}"
    headers = {"Content-Type": "application/json"}
    if numero.zapi_client_token:
        headers["Client-Token"] = numero.zapi_client_token
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        if r.status_code in (200, 201):
            try:
                j = r.json()
                return True, (j.get("messageId") or j.get("id") or "")
            except Exception:
                return True, ""
        return False, (r.text[:300] if r is not None else "")
    except Exception as e:
        return False, str(e)


def enviar_imagem(numero, telefone, image_url, caption="", remetente=None):
    if str(caption or "").strip():
        caption = assinar(caption, remetente)
    if dapi.eh_dapi(numero):
        return dapi.enviar_imagem(numero, telefone, image_url, caption)
    return _post_zapi(numero, "send-image", {"phone": telefone, "image": image_url, "caption": caption})


def enviar_documento(numero, telefone, doc_url, filename, ext):
    if dapi.eh_dapi(numero):
        return dapi.enviar_documento(numero, telefone, doc_url, filename, ext)
    return _post_zapi(numero, f"send-document/{ext}", {"phone": telefone, "document": doc_url, "fileName": filename})


def enviar_video(numero, telefone, video_url, caption="", remetente=None):
    """Video que toca na conversa. O chat chama aqui sem saber de qual provedor
    e o numero: na D-API vai pela rota de video, na Z-API pelo send-video de sempre."""
    if str(caption or "").strip():
        caption = assinar(caption, remetente)
    if dapi.eh_dapi(numero):
        return dapi.enviar_video(numero, telefone, video_url, caption)
    return _post_zapi(numero, "send-video", {"phone": telefone, "video": video_url,
                                             "caption": caption})


def enviar_figurinha(numero, telefone, sticker_url):
    """Envia figurinha (sticker). sticker_url = URL publica de um .webp (512x512 ideal)."""
    if dapi.eh_dapi(numero):
        return dapi.enviar_figurinha(numero, telefone, sticker_url)
    return _post_zapi(numero, "send-sticker", {"phone": telefone, "sticker": sticker_url})


def resolver_chat(numero, identificador):
    """Resolve um @lid (ou phone) -> (telefone_real, nome, foto) via GET /chats/{id}.
    O WhatsApp as vezes manda o contato como @lid (anonimo); isso devolve o numero real."""
    if dapi.eh_dapi(numero):
        return dapi.resolver_chat(numero, identificador)
    url = f"{BASE}/instances/{numero.zapi_instance}/token/{numero.zapi_token}/chats/{identificador}"
    headers = {}
    if numero.zapi_client_token:
        headers["Client-Token"] = numero.zapi_client_token
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            j = r.json()
            return str(j.get("phone") or ""), (j.get("name") or ""), (j.get("profileThumbnail") or "")
    except Exception:
        pass
    return "", "", ""


def status_instancia(numero):
    """Verifica se a instancia esta conectada (connected)."""
    if dapi.eh_dapi(numero):
        return dapi.status_instancia(numero)
    url = f"{BASE}/instances/{numero.zapi_instance}/token/{numero.zapi_token}/status"
    headers = {}
    if numero.zapi_client_token:
        headers["Client-Token"] = numero.zapi_client_token
    try:
        r = requests.get(url, headers=headers, timeout=15)
        return r.json() if r.status_code == 200 else {"error": r.text[:200]}
    except Exception as e:
        return {"error": str(e)}


def parse_webhook(payload: dict):
    """Extrai da notificacao do Z-API os campos que interessam.
    Retorna dict ou None se nao for mensagem recebida util.
    """
    if not isinstance(payload, dict):
        return None
    if dapi.eh_payload_dapi(payload):
        return dapi.parse_webhook(payload)
    # ignora GRUPO / listas de transmissao / newsletter / status — so conversa individual
    if payload.get("isGroup") or payload.get("isNewsletter") or payload.get("broadcast"):
        return None
    telefone_raw = payload.get("phone") or ""
    if "@g.us" in str(telefone_raw) or str(telefone_raw).endswith("-group"):
        return None
    telefone = payload.get("phone") or payload.get("participantPhone")
    if not telefone:
        return None
    # direcao: 'out' = enviada por nos/pelo celular ; 'in' = recebida do contato
    direcao = "out" if payload.get("fromMe") else "in"
    remote_id = str(payload.get("messageId") or payload.get("id") or "")
    # nome do CONTATO: quando a msg sai (fromMe), o senderName somos nos -> usar chatName
    if direcao == "out":
        nome = payload.get("chatName") or ""
    else:
        nome = payload.get("senderName") or payload.get("chatName") or ""
    tipo = "texto"
    conteudo = ""
    midia = ""
    if payload.get("text"):
        conteudo = (payload["text"] or {}).get("message", "") if isinstance(payload.get("text"), dict) else str(payload.get("text"))
    elif payload.get("audio"):
        tipo = "audio"; midia = (payload["audio"] or {}).get("audioUrl", "")
    elif payload.get("image"):
        tipo = "imagem"; midia = (payload["image"] or {}).get("imageUrl", ""); conteudo = (payload["image"] or {}).get("caption", "")
    elif payload.get("document"):
        tipo = "doc"; midia = (payload["document"] or {}).get("documentUrl", "")
    elif payload.get("sticker"):
        st = payload["sticker"] or {}
        tipo = "figurinha"
        midia = st.get("stickerUrl") or st.get("url") or (st if isinstance(st, str) else "")
    elif payload.get("contact") or payload.get("contacts"):
        # CONTATO COMPARTILHADO (vCard) -> texto legivel; o frontend vira cartao
        # com "Salvar contato" / "Mandar mensagem" (tema_esc.js).
        conteudo = _texto_contato(payload)
    else:
        # tipos nao tratados (localizacao, etc.)
        conteudo = payload.get("body") or ""
    # ignora callbacks sem conteudo real (ex.: status de entrega/leitura)
    if tipo == "texto" and not (conteudo or "").strip():
        return None
    foto = payload.get("photo") or payload.get("senderPhoto") or ""
    return {"telefone": str(telefone), "nome": nome, "tipo": tipo,
            "conteudo": conteudo, "midia_url": midia,
            "direcao": direcao, "remote_id": remote_id, "foto": foto}


def _texto_contato(payload: dict) -> str:
    """Monta 'Contato compartilhado: Nome' + um telefone por linha, para cada contato."""
    import re as _re
    brutos = payload.get("contacts") or [payload.get("contact")]
    if isinstance(brutos, dict):
        brutos = [brutos]
    blocos = []
    for ct in brutos or []:
        if not isinstance(ct, dict):
            continue
        nome = (ct.get("displayName") or ct.get("name") or "").strip()
        fones = []
        for f in (ct.get("phones") or []):
            d = _re.sub(r"\D", "", str(f))
            if len(d) >= 10 and d not in fones:
                fones.append(d)
        vcard = ct.get("vCard") or ct.get("vcard") or ""
        if vcard:
            if not nome:
                m = _re.search(r"^FN[^:]*:(.+)$", vcard, _re.M)
                nome = m.group(1).strip() if m else ""
            for m in _re.finditer(r"waid=(\d+)", vcard):
                if m.group(1) not in fones:
                    fones.append(m.group(1))
            if not fones:
                for m in _re.finditer(r"^TEL[^:]*:(.+)$", vcard, _re.M):
                    d = _re.sub(r"\D", "", m.group(1))
                    if len(d) >= 10 and d not in fones:
                        fones.append(d)
        linhas = ["Contato compartilhado: " + (nome or "sem nome")]
        for d in fones:
            if d.startswith("55") and len(d) in (12, 13):
                loc = d[4:]
                linhas.append("+55 %s %s-%s" % (d[2:4], loc[:-4], loc[-4:]))
            else:
                linhas.append("+" + d)
        blocos.append("\n".join(linhas))
    return "\n\n".join(blocos)
