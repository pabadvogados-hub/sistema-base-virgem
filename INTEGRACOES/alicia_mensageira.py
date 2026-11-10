"""
Alícia — mensageira do Dr. Titular (via ADONE / Atende Direito Flow API).

Envia mensagens no tom da Alícia (profissional, sem emoji, assinado "Alícia",
tratando o titular como "Dr. Titular"). Resolve o destinatário por
nome / telefone / contact_id, acha a conversa no ADONE e envia.

Uso (linha de comando):
  # texto livre, DRY-RUN (não envia, só mostra):
  py INTEGRACOES/alicia_mensageira.py "Advogada" -m "Seu texto aqui"
  # enviar de verdade:
  py INTEGRACOES/alicia_mensageira.py "Advogada" -m "Seu texto aqui" --enviar
  # a partir de um arquivo .txt:
  py INTEGRACOES/alicia_mensageira.py "Advogada" -a caminho/mensagem.txt --enviar

Uso (import):
  from INTEGRACOES.alicia_mensageira import enviar_como_alicia
  enviar_como_alicia("Advogada", "texto...", enviar=True)
"""
import os
import sys
import argparse
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except Exception:
    pass

from INTEGRACOES import atendedireito_flow as flow

log = logging.getLogger("alicia_mensageira")

# Canal padrão da Alícia = Escritório Modelo CENTRAL (mesmo número do ADONE).
CANAL_PADRAO = "01kwn2wr214ckqm6q496a3m5a6"

ASSINATURA = "Alícia, assistente do Dr. Titular"


# ============================================================
# RESOLUÇÃO DO DESTINATÁRIO -> conversa
# ============================================================

def _resolver_contato(destino):
    """Aceita contact_id, telefone (só dígitos) ou nome. Retorna o dict do contato ou None."""
    d = str(destino).strip()
    # contact_id (ULID começa com 01k... e tem 26 chars)
    if len(d) == 26 and d.lower().startswith("01"):
        c = flow.obter_contato(d)
        if c:
            return c
    # telefone
    so_digitos = "".join(ch for ch in d if ch.isdigit())
    achados = flow.buscar_contatos(d) or []
    if isinstance(achados, dict):
        achados = achados.get("data", [])
    if so_digitos and len(so_digitos) >= 8:
        for c in achados:
            if so_digitos in "".join(ch for ch in str(c.get("phone") or "") if ch.isdigit()):
                return c
    # nome (primeiro resultado)
    return achados[0] if achados else None


def _conversa_do_contato(contact_id):
    """Acha a conversa existente (pending/open/automation/closed) do contato."""
    for st in ("pending", "open", "closed"):
        cs = flow.filtrar_conversas(status=st, contact_id=contact_id) or []
        if isinstance(cs, dict):
            cs = cs.get("data", [])
        if cs:
            return cs[0]
    # sem filtro de status, última tentativa
    cs = flow.filtrar_conversas(contact_id=contact_id) or []
    if isinstance(cs, dict):
        cs = cs.get("data", [])
    return cs[0] if cs else None


# ============================================================
# ENVIO
# ============================================================

def enviar_como_alicia(destino, texto, enviar=False, canal=None):
    """
    Envia 'texto' como Alícia para 'destino' (nome/telefone/contact_id).
    enviar=False => DRY-RUN (não envia). enviar=True => envia de verdade.
    Retorna dict com o resultado.
    """
    contato = _resolver_contato(destino)
    if not contato:
        return {"ok": False, "erro": f"contato não encontrado para '{destino}'"}

    conv = _conversa_do_contato(contato.get("id"))
    if not conv:
        return {"ok": False, "erro": f"nenhuma conversa no ADONE com {contato.get('name')}",
                "contato": contato.get("id")}

    channel_id = canal or conv.get("channel_id") or CANAL_PADRAO
    resumo = {
        "contato": contato.get("name"),
        "contact_id": contato.get("id"),
        "conversation_id": conv.get("id"),
        "channel_id": channel_id,
        "enviado": bool(enviar),
    }

    if not enviar:
        print(f"[DRY-RUN] Alícia -> {contato.get('name')} (conv {conv.get('id')})")
        print("-" * 60)
        print(texto)
        print("-" * 60)
        print("Use --enviar para enviar de verdade.")
        return {"ok": True, **resumo}

    r = flow.enviar_mensagem({
        "conversation_id": conv.get("id"),
        "channel_id": channel_id,
        "message_type": "text",
        "content": texto,
    }) or {}
    resumo["msg_id"] = r.get("id")
    resumo["send_status"] = r.get("send_status")
    resumo["ok"] = bool(r.get("id"))
    return resumo


# ============================================================
# CLI
# ============================================================

def _main():
    p = argparse.ArgumentParser(description="Envia mensagem como Alícia via ADONE.")
    p.add_argument("destino", help="nome, telefone ou contact_id do destinatário")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("-m", "--mensagem", help="texto da mensagem")
    g.add_argument("-a", "--arquivo", help="caminho de um .txt com a mensagem")
    p.add_argument("--enviar", action="store_true", help="envia de verdade (sem isso é DRY-RUN)")
    p.add_argument("--canal", help="channel_id (padrão: Escritório Modelo CENTRAL / o da conversa)")
    args = p.parse_args()

    texto = args.mensagem if args.mensagem else Path(args.arquivo).read_text(encoding="utf-8")
    r = enviar_como_alicia(args.destino, texto, enviar=args.enviar, canal=args.canal)
    import json
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
