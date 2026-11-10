# -*- coding: utf-8 -*-
"""As mãos do Assistente no Hermes: servidor MCP por stdio com as ferramentas do Sistema Escritório Modelo (fatia 1, item 32).

O Hermes (perfil lua) lança este programa e conversa com ele pela entrada e saída padrão (MCP, JSON-RPC 2.0,
uma mensagem por linha). Cada ferramenta vira uma chamada à rota interna do backend,
POST http://127.0.0.1:8000/api/agentes/hermes/ferramenta, com a chave LUA_HERMES_CHAVE no cabeçalho
x-lua-chave. Quem decide o que pode é o backend: sempre como a Advogada (uid 5), com as travas de hoje.

POR QUE ASSIM
- Não é terminal: o modelo só escolhe ferramenta e argumentos; não roda comando.
- A chave vem do .env do backend, lido aqui. Nunca do config.yaml do Hermes, que é versionado.
- Só biblioteca padrão do Python: nada novo para instalar no venv.
- Chama o backend direto em 127.0.0.1:8000, sem passar pelo nginx (que bloqueia a rota de fora, D12).

A saída padrão é só do protocolo. Qualquer recado vai para a saída de erro.
"""
import json
import os
import sys
import urllib.error
import urllib.request

ENV_BACKEND = os.environ.get("LUA_BACKEND_ENV", "/opt/escritorio/SISTEMA/backend/.env")
API = os.environ.get("ESC_API_INTERNA", "http://127.0.0.1:8000")
ROTA = "/api/agentes/hermes/ferramenta"
ROTA_LISTA = "/api/agentes/hermes/ferramentas"
VERSAO_PADRAO = "2025-06-18"
TETO_RESPOSTA = 40000          # o mesmo corte do motor de hoje para a saída de ferramenta

INSTRUCOES = ("Ferramentas do Sistema Escritório Modelo. Você age sempre como a Dra. Advogada, com as permissões dela. Ação que "
              "pede confirmação devolve uma prévia: mostre a prévia e só chame confirmar_acao depois do sim dela.")


def ler_chave(arquivo=ENV_BACKEND):
    """LUA_HERMES_CHAVE do .env do backend. Sem arquivo ou sem a linha: vazio (e a rota recusa)."""
    try:
        with open(arquivo, encoding="utf-8") as f:
            linhas = f.read().splitlines()
    except OSError:
        return ""
    for linha in linhas:
        linha = linha.strip()
        if linha.startswith("#") or "=" not in linha:
            continue
        nome, valor = linha.split("=", 1)
        if nome.strip() == "LUA_HERMES_CHAVE":
            return valor.strip().strip('"').strip("'")
    return ""


def _http(metodo, rota, corpo=None, chave=None, timeout=300):
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(API + rota, data=dados, method=metodo,
                                 headers={"Content-Type": "application/json", "x-lua-chave": chave or ler_chave()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("o Sistema Escritório Modelo recusou (%s)" % e.code) from e


def _resultado(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _erro(id_, codigo, mensagem):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": codigo, "message": mensagem}}


def _texto(conteudo, erro):
    return {"content": [{"type": "text", "text": conteudo}], "isError": erro}


def tratar(msg, listar, chamar):
    """Uma mensagem do Hermes, uma resposta. Notificação (sem id) não tem resposta.

    listar() devolve as ferramentas no formato MCP; chamar(nome, entrada) devolve a saída da rota.
    """
    if not isinstance(msg, dict):
        return _erro(None, -32600, "mensagem inválida")
    id_, metodo, params = msg.get("id"), msg.get("method"), msg.get("params")
    params = params if isinstance(params, dict) else {}
    if id_ is None:
        return None
    if metodo == "initialize":
        return _resultado(id_, {"protocolVersion": params.get("protocolVersion") or VERSAO_PADRAO,
                                "capabilities": {"tools": {"listChanged": False}},
                                "serverInfo": {"name": "esc", "version": "1.0"}, "instructions": INSTRUCOES})
    if metodo == "ping":
        return _resultado(id_, {})
    if metodo == "tools/list":
        try:
            return _resultado(id_, {"tools": listar()})
        except Exception as e:
            return _erro(id_, -32603, "não consegui listar as ferramentas: %s" % str(e)[:200])
    if metodo == "tools/call":
        nome, argumentos = params.get("name"), params.get("arguments") or {}
        if not isinstance(nome, str) or not nome or not isinstance(argumentos, dict):
            return _resultado(id_, _texto("chamada inválida: nome da ferramenta e argumentos como objeto. Nada foi feito.", True))
        try:
            out = chamar(nome, argumentos)
        except Exception as e:
            return _resultado(id_, _texto("o Sistema Escritório Modelo não respondeu agora (%s). Não sei se a ação foi feita: confira antes de repetir." % str(e)[:200], True))
        erro = isinstance(out, dict) and bool(out.get("erro"))
        return _resultado(id_, _texto(json.dumps(out, ensure_ascii=False, default=str)[:TETO_RESPOSTA], erro))
    return _erro(id_, -32601, "método desconhecido: %s" % metodo)


def processar_linha(linha, listar, chamar):
    """Uma linha da entrada, uma resposta. Nada que chegue aqui pode derrubar o servidor (A9)."""
    try:
        msg = json.loads(linha)
    except (ValueError, RecursionError):
        return _erro(None, -32700, "JSON inválido")
    try:
        return tratar(msg, listar, chamar)
    except Exception as e:
        return _erro(msg.get("id") if isinstance(msg, dict) else None, -32603, "erro interno: %s" % str(e)[:200])


def main():
    # O MCP fala UTF-8. Sem isto, o idioma da máquina decide (no ensaio do Windows os acentos quebraram a
    # conversa; um serviço do systemd sem LANG pode cair no mesmo erro).
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    chave = ler_chave()
    if not chave:
        print("lua_mcp: LUA_HERMES_CHAVE ausente em %s; as chamadas serão recusadas" % ENV_BACKEND, file=sys.stderr)

    def listar():
        return _http("GET", ROTA_LISTA, chave=chave)["tools"]

    def chamar(nome, entrada):
        return _http("POST", ROTA, {"nome": nome, "entrada": entrada}, chave=chave)

    for linha in sys.stdin:
        linha = linha.strip()
        if not linha:
            continue
        resposta = processar_linha(linha, listar, chamar)
        if resposta is not None:
            sys.stdout.write(json.dumps(resposta, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
