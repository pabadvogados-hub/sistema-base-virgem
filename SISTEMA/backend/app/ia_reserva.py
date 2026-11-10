"""IA DE RESERVA (29/09/2026): quando a Anthropic recusa (carteira da API sem credito, fora do ar),
os agentes, o "Meu dia" e a revisao da Revisora IA seguem pelo Gemini, com as MESMAS ferramentas
e as mesmas travas. Em 29/09 as 15h a carteira zerou e a Maya parou de atender a a equipe.

- anthropic_fora(): depois de um erro de credito, vai direto para o Gemini por 10 min (sem gastar
  uma chamada que ja se sabe que falha); depois tenta a Anthropic de novo sozinho.
- conversar_com_ferramentas(): o mesmo laco de ferramentas do agente, no formato do Gemini
  (functionDeclarations com parametersJsonSchema; as partes do modelo voltam inteiras, com a
  assinatura de pensamento, como o Gemini 3 exige).
"""
import json
import logging
import os
import time

import requests
from dotenv import dotenv_values

from .config import ENV_CENTRAL

log = logging.getLogger("ia_reserva")
MODELO = "gemini-3.8-flash"
MODELO_PRO = "gemini-3.1-pro-preview"
URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
_FORA = {"ate": 0.0, "motivo": ""}


def _chave():
    v = dotenv_values(str(ENV_CENTRAL)) if ENV_CENTRAL.exists() else {}
    return (v.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY") or "").strip()


def disponivel():
    return bool(_chave())


def falha_de_conta(e):
    """Erro que nao adianta repetir na Anthropic: sem credito, chave recusada, conta bloqueada, sobrecarga."""
    t = str(e).lower()
    return any(x in t for x in ("credit balance", "billing", "authentication", "invalid x-api-key", "permission",
                                "overloaded", "529", "rate_limit", "429"))


def marcar_fora(e):
    _FORA.update(ate=time.time() + 600, motivo=str(e)[:200])
    log.warning("Anthropic fora por 10 min: %s", str(e)[:200])


def anthropic_fora():
    return time.time() < _FORA["ate"]


def _post(modelo, corpo, timeout=120):
    r = requests.post(URL % modelo, json=corpo, timeout=timeout,
                      headers={"Content-Type": "application/json", "x-goog-api-key": _chave()})
    if r.status_code >= 400:
        raise RuntimeError("Gemini %s: %s" % (r.status_code, r.text[:300]))
    return r.json()


def _partes(resp):
    cands = resp.get("candidates") or []
    if not cands:
        raise RuntimeError("Gemini sem resposta: %s" % json.dumps(resp)[:300])
    return (cands[0].get("content") or {}).get("parts") or []


def _conteudos(mensagens):
    """[{'role':'user'|'assistant','content':str}] -> contents do Gemini."""
    out = []
    for m in mensagens:
        papel = "model" if m["role"] == "assistant" else "user"
        texto = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"], ensure_ascii=False, default=str)
        if out and out[-1]["role"] == papel:
            out[-1]["parts"][0]["text"] += "\n\n" + texto
        else:
            out.append({"role": papel, "parts": [{"text": texto}]})
    return out


def texto(sistema, mensagens, max_tokens=2000, modelo=None):
    """Resposta simples (sem ferramentas)."""
    corpo = {"systemInstruction": {"parts": [{"text": sistema}]}, "contents": _conteudos(mensagens),
             "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.4}}
    return "".join(p.get("text", "") for p in _partes(_post(modelo or MODELO, corpo, 300)) if not p.get("thought")).strip()


def conversar_com_ferramentas(sistema, mensagens, ferramentas, executar, rodadas=8, max_tokens=2000):
    """Laco de ferramentas no Gemini. executar(nome, args) -> dict. Devolve o texto final."""
    decl = [{"name": f["name"], "description": f.get("description", ""),
             "parametersJsonSchema": f.get("input_schema") or {"type": "object", "properties": {}}} for f in ferramentas]
    contents = _conteudos(mensagens)
    corpo = {"systemInstruction": {"parts": [{"text": sistema}]}, "contents": contents,
             "tools": [{"functionDeclarations": decl}], "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
             "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.4}}
    for _ in range(rodadas):
        partes = _partes(_post(MODELO, corpo))
        chamadas = [p["functionCall"] for p in partes if p.get("functionCall")]
        if not chamadas:
            return "".join(p.get("text", "") for p in partes if not p.get("thought")).strip()
        contents.append({"role": "model", "parts": partes})          # volta inteiro (assinatura de pensamento)
        respostas = []
        for ch in chamadas:
            out = executar(ch.get("name"), ch.get("args") or {})
            txt = json.dumps(out, ensure_ascii=False, default=str)
            respostas.append({"functionResponse": {"name": ch.get("name"),
                                                   "response": out if len(txt) < 40000 else {"resultado": txt[:40000]}}})
        contents.append({"role": "user", "parts": respostas})
    return ""
