"""VOZ DOS AGENTES: chat por voz; se pedirem para executar uma tarefa por audio, executa e responde por audio.

  ouvir  -> Groq Whisper (o mesmo STT dos audios do WhatsApp, app/whatsapp/transcricao)
  falar  -> ElevenLabs, modelo eleven_flash_v2_5 (rapido e custa metade do credito).
            Se o ElevenLabs falhar (cota do mes acabou, fora do ar), fala pelo Gemini TTS,
            para a conversa nao quebrar.

O credito do ElevenLabs e por caracteres/mes: resposta falada tem de ser curta. A voz de cada agente e escolhida pelo admin (agentes.voz_id);
sem escolha, vale a PADRAO abaixo (vozes publicas da biblioteca: adicionar na conta do escritorio).
"""
import base64
import json
import logging
import os
import struct
import urllib.error
import urllib.request

import requests
from dotenv import dotenv_values

from .config import ENV_CENTRAL, settings

log = logging.getLogger("agentes_voz")
MODELO_TTS = "eleven_flash_v2_5"
GEMINI_TTS = "gemini-2.5-flash-preview-tts"

# simbolo do agente -> voz padrao (ElevenLabs, ja na biblioteca da conta)
PADRAO = {
    "lua": ("RGymW84CSmfVugnA5tvA", "Roberta (suave e confiante)", "Kore"),
    "maya": ("GDzHdQOi6jjf8zaXhCYD", "Raquel (expressiva e animada)", "Aoede"),
    "agatha": ("cyD08lEy76q03ER1jZ7y", "Scheila (séria e direta)", "Leda"),
    "neo": ("2CECaLAGTS5NRGxgbcxr", "Davi Andrei (jovem, natural)", "Puck"),
    "renatinho": ("CstacWqMhJQlnfLPxRG4", "Will (grave e acolhedora)", "Charon"),
    "estrela": ("czvzJwIVS2asEKnthV40", "Daniel (conversa, neutro)", "Iapetus"),
}
# opcoes que o admin pode escolher na tela (biblioteca publica do ElevenLabs, pt-BR)
OPCOES = {
    "a": [
        ("RGymW84CSmfVugnA5tvA", "Roberta: jovem, suave e confiante"),
        ("GDzHdQOi6jjf8zaXhCYD", "Raquel: expressiva e animada"),
        ("cyD08lEy76q03ER1jZ7y", "Scheila: madura, séria e direta"),
    ],
    "o": [
        ("2CECaLAGTS5NRGxgbcxr", "Davi Andrei: jovem, natural"),
        ("CstacWqMhJQlnfLPxRG4", "Will: grave e acolhedora"),
        ("czvzJwIVS2asEKnthV40", "Daniel: conversa, neutro"),
    ],
}


def _chaves():
    v = dotenv_values(str(ENV_CENTRAL)) if ENV_CENTRAL.exists() else {}
    return ((v.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEY") or "").strip(),
            (v.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY") or "").strip())


def voz_do_agente(ag):
    padrao = PADRAO.get((ag or {}).get("simbolo") or "estrela", PADRAO["estrela"])
    return (ag or {}).get("voz_id") or padrao[0], padrao[2]


# o modelo maior do Whisper erra menos em fala curta ("amanha" virava "mangar" no turbo);
# a dica ajuda com os nomes e termos do escritorio
MODELO_STT = "whisper-large-v3"
DICA_STT = ("Conversa em português do Brasil com a assistente do escritório Escritório Modelo Advogados sobre tarefas, prazos, "
            "prazo fatal, audiência, agenda, amanhã, hoje, processo, cliente, petição, protocolo, contrarrazões. "
            "Nomes: " + (os.getenv("STT_NOMES_EQUIPE") or "Titular, Advogada") + ".")


def transcrever(audio, mime="audio/webm"):
    """Audio do navegador (webm/ogg/mp4) -> texto em portugues. '' se nao entendeu."""
    key = settings.STT_API_KEY
    if not key or not audio:
        return ""
    ext = "mp4" if "mp4" in mime or "m4a" in mime else ("ogg" if "ogg" in mime else ("mp3" if "mpeg" in mime else "webm"))
    try:
        r = requests.post(settings.STT_BASE_URL.rstrip("/") + "/audio/transcriptions",
                          headers={"Authorization": "Bearer " + key},
                          files={"file": ("fala." + ext, audio, mime.split(";")[0] or "audio/webm")},
                          data={"model": MODELO_STT, "language": "pt", "response_format": "text",
                                "prompt": DICA_STT, "temperature": "0"},
                          timeout=60)
        if r.status_code == 200:
            return r.text.strip()
        log.warning("STT %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("STT falhou: %s", e)
    return ""


def _eleven(texto, voz_id, chave):
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/text-to-speech/%s?output_format=mp3_44100_64" % voz_id,
        data=json.dumps({"text": texto, "model_id": MODELO_TTS, "language_code": "pt",
                         "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "speed": 1.05}}).encode(),
        headers={"xi-api-key": chave, "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=60).read()


def _wav(pcm, taxa=24000):
    cab = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, taxa, taxa * 2, 2, 16)
    return cab + b"data" + struct.pack("<I", len(pcm)) + pcm


def _gemini(texto, voz, chave):
    corpo = {"contents": [{"parts": [{"text": "Fale em português do Brasil, em tom natural de conversa: " + texto}]}],
             "generationConfig": {"responseModalities": ["AUDIO"],
                                  "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voz}}}}}
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % GEMINI_TTS,
        data=json.dumps(corpo).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": chave})
    r = json.loads(urllib.request.urlopen(req, timeout=90).read())
    for p in r["candidates"][0]["content"]["parts"]:
        d = p.get("inlineData") or p.get("inline_data")
        if d:
            return _wav(base64.b64decode(d["data"]))
    raise RuntimeError("Gemini nao devolveu audio")


def falar(texto, ag):
    """Texto -> (bytes, mime, motor). Tenta ElevenLabs; se falhar, Gemini. (None, None, erro) se nenhum."""
    texto = (texto or "").strip()[:1500]
    if not texto:
        return None, None, "sem texto"
    eleven, gemini = _chaves()
    voz_id, voz_gemini = voz_do_agente(ag)
    if eleven:
        try:
            return _eleven(texto, voz_id, eleven), "audio/mpeg", "elevenlabs"
        except urllib.error.HTTPError as e:
            log.warning("ElevenLabs %s: %s", e.code, e.read()[:200])
        except Exception as e:
            log.warning("ElevenLabs falhou: %s", e)
    if gemini:
        try:
            return _gemini(texto, voz_gemini, gemini), "audio/wav", "gemini"
        except Exception as e:
            log.warning("Gemini TTS falhou: %s", e)
            return None, None, "voz indisponível agora"
    return None, None, "sem chave de voz"


_COTA = {"em": 0, "valor": None}


def cota():
    """Quanto sobra no ElevenLabs este mes (para o admin ver). Guarda 10 min para a tela nao esperar."""
    import time
    if _COTA["valor"] and time.time() - _COTA["em"] < 600:
        return _COTA["valor"]
    eleven, _ = _chaves()
    if not eleven:
        return None
    try:
        req = urllib.request.Request("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": eleven})
        s = json.loads(urllib.request.urlopen(req, timeout=6).read())
        _COTA.update(em=time.time(), valor={"plano": s.get("tier"), "usados": s.get("character_count"),
                                            "limite": s.get("character_limit"),
                                            "renova_em": s.get("next_character_count_reset_unix")})
        return _COTA["valor"]
    except Exception:
        return _COTA["valor"]


def para_falar(texto, limite=450):
    """Tira do texto o que nao se le em voz alta (markdown, [#123] -> 'tarefa 123') e corta no fim
    de uma frase perto do limite: a fala fica curta (e gasta pouco); o texto inteiro fica no chat."""
    import re
    t = re.sub(r"\[#(\d+)\]", r"tarefa \1", texto or "")
    t = re.sub(r"\*\*|__|`|^#+\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.M)
    t = re.sub(r"\s*\n+\s*", " ", t).strip()
    if len(t) <= limite:
        return t
    frases = re.split(r"(?<=[.!?])\s+", t)
    out = ""
    for f in frases:
        if out and len(out) + len(f) + 1 > limite:
            break
        out = (out + " " + f).strip()
    return (out or t[:limite]) + " O resto está escrito aqui no chat."
