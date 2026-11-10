# -*- coding: utf-8 -*-
"""
AVISO AO CLIENTE: A PECA ENTROU EM PRODUCAO (Advogada, 25/09/2026).

"Quando a peca for para producao, notificar o cliente que a peca dele ja esta
em producao e que, em breve, sera distribuida; e que, depois da distribuicao,
ele vai ser atualizado e receber os dados para o acesso ao Portal do Cliente."

Primeira fase do pedido de 12/08 ("card muda, cliente sabe"), so para a etapa
Produção (18) > "Producao da Inicial". Funciona como um webhook: le a trilha
de movimentos do CRM (wa_board_hist), que registra TANTO o movimento feito na
tela quanto o feito por robo, e avisa UMA vez por cliente.

Regras:
  - so avisa movimento NOVO (na primeira rodada marca o ponto de partida e nao
    manda nada para quem ja estava la);
  - uma vez por conversa, para sempre (card que sai e volta nao recebe de novo);
  - janela 8h-20h; fora dela o aviso fica na fila e sai na abertura;
  - o card tem que CONTINUAR na etapa na hora do envio (movimento desfeito nao
    avisa ninguem).

USO:
  py SYNC/aviso_producao.py                    # simulacao, nao envia
  py SYNC/aviso_producao.py --enviar
  py SYNC/aviso_producao.py --marcar 1124      # ja avisado a mao, nao mandar
Cron sugerido: */5 7-21 * * * (com flock). Estado: aviso_producao_state.json.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    TZ = None

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

DB = os.getenv("SISTEMA_DB", "/opt/escritorio/SISTEMA/backend/sistema_esc.db")
STATE = os.path.join(_DIR, "aviso_producao_state.json")
PIPELINE = 18
ETAPA = "Producao da Inicial"
JANELA = (8, 20)

TEXTO = (
    "Oi, {nome}! Tenho uma notícia boa pra você 🙌\n\n"
    "O seu caso entrou em PRODUÇÃO aqui no escritório. A nossa equipe já está "
    "preparando a sua ação com os documentos e as informações que você mandou, "
    "e em breve ela será distribuída na Justiça ⚖️\n\n"
    "Assim que for distribuída, eu te aviso por aqui e te mando os dados para "
    "você acessar o Portal do Cliente, onde vai poder acompanhar o seu processo "
    "24 horas por dia.\n\n"
    "Qualquer dúvida, é só chamar.\nEscritório Modelo Advogados"
)


def _agora():
    return datetime.now(TZ) if TZ else datetime.now()


def _estado():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ultimo_hist": None, "avisados": {}, "fila": {}}


def _salvar(e):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(e, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def _primeiro_nome(nome):
    p = (nome or "").strip().split()
    p = p[0] if p else ""
    return p.title() if p.isalpha() else ""


def rodar(enviar=False):
    print(f"=== AVISO PRODUCAO | {_agora().isoformat()[:16]} | "
          + ("ENVIAR" if enviar else "SIMULACAO") + " ===")
    est = _estado()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    con.execute("pragma busy_timeout=8000")
    teto = con.execute("select coalesce(max(id),0) from wa_board_hist").fetchone()[0]
    if est["ultimo_hist"] is None:
        # primeira rodada: marca o ponto de partida, sem avisar o passado
        est["ultimo_hist"] = teto
        print(f"  ponto de partida marcado no movimento {teto}; nada enviado.")
        if enviar:
            _salvar(est)
        con.close()
        return
    for cid, criado in con.execute(
            "select conversa_id, max(criado_em) from wa_board_hist where id>? and "
            "para_pipeline=? and para_etapa=? and coalesce(bloqueado,0)=0 "
            "group by conversa_id", (est["ultimo_hist"], PIPELINE, ETAPA)):
        if str(cid) not in est["avisados"]:
            est["fila"].setdefault(str(cid), criado)
    est["ultimo_hist"] = teto

    dentro = JANELA[0] <= _agora().hour < JANELA[1]
    for cid in list(est["fila"]):
        r = con.execute("select contato_nome, contato_telefone, pipeline_id, etapa "
                        "from wa_conversas where id=?", (int(cid),)).fetchone()
        if not r or (r[2], r[3]) != (PIPELINE, ETAPA):
            print(f"  [conversa {cid}] saiu da etapa antes do aviso; descartado.")
            est["fila"].pop(cid)
            continue
        nome, tel = r[0], r[1]
        if not tel:
            print(f"  [{nome}] sem telefone; descartado.")
            est["fila"].pop(cid)
            continue
        if not dentro:
            print(f"  [{nome}] fora da janela {JANELA[0]}h-{JANELA[1]}h; fica na fila.")
            continue
        p = _primeiro_nome(nome)
        texto = TEXTO.replace("Oi, {nome}!", f"Oi, {p}!" if p else "Oi!")
        if not enviar:
            print(f"  [{nome}] avisaria agora ({tel}).")
            continue
        import wa_dapi
        ok, info = wa_dapi.enviar_texto(tel, texto)
        if ok:
            est["avisados"][cid] = _agora().isoformat()[:16]
            est["fila"].pop(cid)
            print(f"  [{nome}] avisado.")
        else:
            print(f"  [{nome}] falhou ({str(info)[:120]}); tenta na proxima rodada.")
    con.close()
    if enviar:
        _salvar(est)


def marcar(cids):
    est = _estado()
    for c in cids:
        est["avisados"][str(c)] = "marcado a mao " + _agora().isoformat()[:16]
        est["fila"].pop(str(c), None)
    _salvar(est)
    print("marcados como ja avisados:", ", ".join(cids))


if __name__ == "__main__":
    if "--marcar" in sys.argv:
        marcar(sys.argv[sys.argv.index("--marcar") + 1:])
    else:
        rodar("--enviar" in sys.argv)
