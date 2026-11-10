"""Transcricao de audio (speech-to-text) do WhatsApp.

Baixa o audio do link do Z-API e transcreve via endpoint compativel com OpenAI.
Funciona com Groq (recomendado, ~gratis) ou OpenAI — so muda BASE/MODEL/KEY no .env.
"""
import requests
from ..config import settings


def baixar(url: str) -> bytes:
    """Baixa o binario do audio (ou b'' se falhar)."""
    try:
        r = requests.get(url, timeout=60)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    return b""


def transcrever_bytes(audio_bytes: bytes) -> str:
    """Transcreve o binario do audio via STT (Groq/OpenAI)."""
    key = settings.STT_API_KEY
    if not key or not audio_bytes:
        return ""
    try:
        files = {"file": ("audio.ogg", audio_bytes, "audio/ogg")}
        data = {"model": settings.STT_MODEL, "language": "pt", "response_format": "text"}
        r = requests.post(
            f"{settings.STT_BASE_URL.rstrip('/')}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files=files, data=data, timeout=120,
        )
        if r.status_code == 200:
            return r.text.strip()
    except Exception:
        pass
    return ""


def transcrever(audio_url: str) -> str:
    """Retorna o texto do audio, ou '' se falhar/sem chave."""
    return transcrever_bytes(baixar(audio_url))
