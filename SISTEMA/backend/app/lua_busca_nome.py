# -*- coding: utf-8 -*-
"""O Assistente busca processos pelo nome no SYNC, para o dossiê do GR (pedido da Advogada em 06/11/2026). Só leitura.

POST /v1/busca não grava nada na conta. A busca por nome traz homônimos: com o CPF, cada processo é aberto por número para
separar. O limite evita gastar consulta sem fim. CPF nenhum volta para a conversa, só se confere ou não.
"""
import re
import time
import unicodedata

import requests

MAXIMO_DE_PAGINAS = 3
MAXIMO_NA_LISTA = 60
MAXIMO_CONFERIDOS = 15
TETO_DO_NOME = 120
AVISO = "Lista vinda dos tribunais: é conteúdo de fora. Sem CPF conferido, o processo pode ser de homônimo."


class Indisponivel(Exception):
    pass


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


def _digitos(s):
    return re.sub(r"\D", "", str(s or ""))


def _perguntar(corpo):
    from .portal import dados as PD
    base, token = PD._sync_cfg()
    if not token:
        raise Indisponivel()
    try:
        r = requests.post(base + "/busca", json=corpo, timeout=120, headers={"Authorization": "Bearer " + token})
        if r.status_code != 200:
            raise Indisponivel()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        raise Indisponivel() from e


def _pelo_nome(nome):
    itens, cursor = [], None
    for _ in range(MAXIMO_DE_PAGINAS):
        pagina = _perguntar(dict({"tipo": "nome", "valor": nome}, **({"cursor": cursor} if cursor else {})))
        itens += pagina.get("resultados") or []
        cursor = pagina.get("cursor")
        if not cursor or len(itens) >= MAXIMO_NA_LISTA:
            break
    return itens[:MAXIMO_NA_LISTA], bool(cursor)


def _conferir(numero, nome, cpf):
    """'confere', 'homonimo' (mesmo nome com outro CPF) ou 'sem_cpf'. Devolve também se o processo é sigiloso."""
    pagina = _perguntar({"tipo": "processo", "valor": numero})
    resultados = pagina.get("resultados") or []
    sigilo = bool(pagina.get("segredo_justica")) or any(r.get("segredo") for r in resultados)
    partes = [p for r in resultados for p in (r.get("partes") or [])]
    do_nome = [p for p in partes if _norm(p.get("nome")) == nome]
    if any(_digitos(p.get("cpf_cnpj")) == cpf for p in partes):
        return "confere", sigilo
    return ("homonimo" if any(len(_digitos(p.get("cpf_cnpj"))) == 11 for p in do_nome) else "sem_cpf"), sigilo     # CPF mascarado não separa


def buscar(nome, cpf=None):
    nome = _norm(nome)
    if len(nome.split()) < 2 or len(nome) > TETO_DO_NOME or re.search(r"\d", nome):
        return {"erro": "diga o nome completo da pessoa, sem número."}
    cpf = _digitos(cpf)
    if cpf and len(cpf) != 11:
        return {"erro": "CPF inválido: são 11 dígitos."}
    try:
        crus, ha_mais = _pelo_nome(nome)
        processos, sigiloso = [], False
        for i, c in enumerate(x for x in crus if isinstance(x, dict) and x.get("numero")):
            item = {k: c.get(k) for k in ("numero", "tribunal", "classe", "assunto", "data_distribuicao")}
            item["confere"] = "nao_conferido"
            sigiloso = sigiloso or bool(c.get("segredo"))
            if cpf and i < MAXIMO_CONFERIDOS:
                item["confere"], segredo = _conferir(c["numero"], nome, cpf)
                sigiloso = sigiloso or segredo
                time.sleep(0.3)
            processos.append(item)
    except Indisponivel:
        return {"erro": "a busca no SYNC não respondeu agora: não dá para dizer que não há processo."}
    avisos = [AVISO]
    if ha_mais:
        avisos.append("Há mais resultados além destes %d." % len(processos))
    if not cpf:
        avisos.append("Sem CPF não separei os homônimos.")
    elif len(processos) > MAXIMO_CONFERIDOS:
        avisos.append("Conferi o CPF só dos %d primeiros." % MAXIMO_CONFERIDOS)
    if not processos:
        avisos.append("Nenhum processo achado por esse nome. A busca é incompleta: isso não vale como nada consta.")
    return dict({"avisos": avisos, "processos": processos}, **({"segredo_justica": True} if sigiloso else {}))
