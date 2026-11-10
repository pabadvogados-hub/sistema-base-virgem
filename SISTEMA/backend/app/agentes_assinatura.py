"""AGENTES PELA ASSINATURA DO CLAUDE (07/10/2026). Pedido do Titular: "pode usar a nossa assinatura
claude que da conta, pode mudar".

Assistente, Assistente, Assistente, Assistente e Assistente passam a pensar pelo Claude Code da assinatura (o mesmo token dos
robos do servidor, CLAUDE_CODE_OAUTH_TOKEN em /opt/escritorio/config/.env), pelo Claude Agent SDK, e nao
mais pelo credito da API. As ferramentas sao as MESMAS do agente (executar() em routers/agentes.py),
servidas ao Claude Code como um servidor MCP dentro do proprio processo; nenhuma ferramenta do Claude
Code (Bash, arquivos, web) fica ligada aqui. Quem quer servidor continua indo pelo operador_tecnico.

Reserva: se a assinatura nao atender (limite de uso, token, CLI) ANTES de qualquer ferramenta rodar,
levanta Indisponivel e o agente segue pela API e depois pelo Gemini, como antes. Se alguma ferramenta
ja rodou, NAO repete pela API (repetiria a acao): devolve o que tiver.

Chave sem deploy: /opt/escritorio/config/agentes_cerebro com "api" volta tudo para a API; sem o arquivo ou
com "assinatura", vale a assinatura.
"""
import asyncio
import json
import threading
import time
from pathlib import Path

CASA = Path("/var/lib/esc_agentes")          # HOME e pasta de trabalho do Claude Code (fora do git)
ENV = "/opt/escritorio/config/.env"
CHAVE = "/opt/escritorio/config/agentes_cerebro"
VAGAS = threading.BoundedSemaphore(3)        # no maximo 3 Claude Code ao mesmo tempo (memoria da VPS)
ESPERA_VAGA = 25
TEMPO = {"texto": 240, "voz": 90}


class Indisponivel(Exception):
    """a assinatura nao respondeu e nenhuma ferramenta rodou: pode seguir pela API sem repetir acao"""


def ligada():
    try:
        return Path(CHAVE).read_text(encoding="utf-8").strip().lower() != "api"
    except OSError:
        return True


def _token():
    try:
        for linha in open(ENV, encoding="utf-8"):
            if linha.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
                return linha.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _prompt(msgs):
    """o Claude Code recebe uma mensagem so: o historico vai como contexto antes da pergunta nova"""
    *antes, ultima = msgs
    if not antes:
        return ultima["content"]
    linhas = ["%s: %s" % ("Pessoa" if m["role"] == "user" else "Você", m["content"]) for m in antes]
    return ("Conversa até aqui (contexto; não responda de novo ao que já foi respondido):\n\n" + "\n\n".join(linhas) +
            "\n\nMensagem nova da pessoa (responda a esta):\n" + ultima["content"])


def conversar(sis, msgs, ferramentas, processar, rodadas, modelo, via="texto"):
    """Devolve (texto, ferramentas_rodadas). Levanta Indisponivel se nada rodou e a assinatura falhou."""
    tok = _token()
    if not tok:
        raise Indisponivel("sem CLAUDE_CODE_OAUTH_TOKEN")
    if not VAGAS.acquire(timeout=ESPERA_VAGA):
        raise Indisponivel("assinatura ocupada (3 conversas ao mesmo tempo)")
    estado = {"ferramentas": 0, "textos": [], "erro": "", "stderr": [], "fonte": None, "inicio": time.time()}
    try:
        try:
            asyncio.run(asyncio.wait_for(_rodar(sis, _prompt(msgs), ferramentas, processar, rodadas, modelo, tok, estado),
                                         TEMPO.get(via, 240)))
        except asyncio.TimeoutError:
            estado["erro"] = "demorou demais"
        except Exception as e:
            estado["erro"] = "%s: %s" % (type(e).__name__, str(e)[:300])
    finally:
        VAGAS.release()
    texto = (estado.get("final") or (estado["textos"][-1] if estado["textos"] else "")).strip()
    print("[agentes-assinatura] %.1fs ferramentas=%d fonte=%s erro=%s %s" % (
        time.time() - estado["inicio"], estado["ferramentas"], estado["fonte"], estado["erro"] or "-",
        " | ".join(estado["stderr"][-2:])[:300]), flush=True)
    if estado["erro"] and not estado["ferramentas"]:
        raise Indisponivel(estado["erro"])
    if estado["fonte"] not in (None, "none") and not estado["ferramentas"]:
        # o Claude Code pegou uma chave de API em vez da assinatura: nao gasta credito por engano
        raise Indisponivel("Claude Code usou %s em vez da assinatura" % estado["fonte"])
    return texto, estado["ferramentas"]


async def _rodar(sis, prompt, ferramentas, processar, rodadas, modelo, tok, estado):
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
                                  SystemMessage, TextBlock, create_sdk_mcp_server, tool)

    def embrulhar(f):
        schema = dict(f.get("input_schema") or {})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})

        @tool(f["name"], f.get("description") or f["name"], schema)
        async def chamada(args, _nome=f["name"]):
            # roda na mesma thread do banco (a conexao sqlite do agente nao pode trocar de thread)
            estado["ferramentas"] += 1
            out = processar(_nome, args or {})
            return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)[:40000]}]}
        return chamada

    for p in ("home", "trabalho"):
        (CASA / p).mkdir(parents=True, exist_ok=True)
    servidor = create_sdk_mcp_server("esc", "1.0.0", [embrulhar(f) for f in ferramentas])
    opcoes = ClaudeAgentOptions(
        model=modelo, system_prompt=sis, tools=[], allowed_tools=["mcp__esc__" + f["name"] for f in ferramentas],
        mcp_servers={"esc": servidor}, strict_mcp_config=True, setting_sources=[], max_turns=rodadas,
        cwd=str(CASA / "trabalho"), thinking={"type": "disabled"},
        env={"HOME": str(CASA / "home"), "CLAUDE_CODE_OAUTH_TOKEN": tok, "ANTHROPIC_API_KEY": "",
             "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
        stderr=lambda s: estado["stderr"].append(s.strip()[-300:]))
    async with ClaudeSDKClient(options=opcoes) as cli:
        await cli.query(prompt)
        async for m in cli.receive_response():
            if isinstance(m, SystemMessage) and getattr(m, "subtype", "") == "init":
                estado["fonte"] = (getattr(m, "data", None) or {}).get("apiKeySource")
            elif isinstance(m, AssistantMessage):
                t = "".join(b.text for b in m.content if isinstance(b, TextBlock)).strip()
                if t:
                    estado["textos"].append(t)
            elif isinstance(m, ResultMessage):
                if m.is_error:
                    estado["erro"] = "%s: %s" % (m.subtype, str(m.result or "")[:300])
                else:
                    estado["final"] = m.result or ""
