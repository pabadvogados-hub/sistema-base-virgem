# -*- coding: utf-8 -*-
"""
DONO DO LEAD NO CRM - cada vendedor com o seu lead.

O dono e o wa_conversas.atendente_email: o mesmo campo que o filtro "meus" do Chat, o comercial.py,
o painel comercial e a contratacao ja usam. Nada de campo novo.

Equipe: VENDEDORES no .env (ver roteamento_vendedoras.py). Sem vendedor com e-mail cadastrado,
o robo nao faz nada.

Regra:
  1. Quem MANDA MENSAGEM para o lead e o dono (o primeiro vendedor que escreveu).
  2. Lead novo que nenhum vendedor tocou: rodizio entre os vendedores em horario; fora do horario
     de todos, o primeiro da lista. O rodizio daqui tem estado PROPRIO (crm_dona_state.json) para
     nao mexer na vez do alarme e do fluxo "voce ja saiu?".
  3. Os que ja passaram sem contato: divididos igualmente, por quadro (--estoque).
  4. Dono dado por este robo e PROVISORIO: se outro vendedor mandar a primeira mensagem, o lead
     passa para ele (regra 1). Dono marcado por pessoa ou pelo sistema fica como esta.

Modos:
  (sem flag)            simula tudo, nao grava nada (relatorio em PRODUCAO_DIR/crm_dona/)
  --estoque --aplicar   uma vez: divide igualmente os que ja passaram sem dono
  --novos               cron a cada minuto: dono para lead novo + passa para quem escreveu primeiro
Quadros: Reclamantes (10) e Empresas (30). Etapas encerradas ficam de fora.
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta

try:
    import fcntl
except ImportError:
    fcntl = None

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import roteamento_vendedoras as ROTA  # noqa: E402

DB = os.getenv("ESC_DB", "/opt/escritorio/SISTEMA/backend/sistema_esc.db")
SAIDA = os.path.join(os.getenv("PRODUCAO_DIR", "/opt/escritorio/producao"), "crm_dona")
ESTADO = os.path.join(HERE, "crm_dona_state.json")
QUADROS = (10, 30)
VENDEDORAS = dict(ROTA.EMAILS)                    # email -> nome
EMAIL = {v: k for k, v in VENDEDORAS.items()}     # nome -> email
ORDEM = [n for n in ROTA.VENDEDORAS if n in EMAIL]
ENCERRADAS = ("perdido", "desqualificado", "fechou")
JANELA_NOVOS_H = 48          # --novos olha leads que chegaram nas ultimas 48 h


def agora_br():
    return datetime.utcnow() - timedelta(hours=3)


def br(utc_txt):
    return datetime.fromisoformat(str(utc_txt)[:19].replace("T", " ")) - timedelta(hours=3)


def log(*a):
    print(agora_br().strftime("%d/%m %H:%M:%S"), *a, flush=True)


def regra_da_vez(quando_br, est):
    """Rodizio entre os vendedores em horario; fora do horario de todos, o primeiro da lista."""
    if not ORDEM:
        return None
    ativos = [n for n in ORDEM if ROTA._em_horario(ROTA.VENDEDORAS[n], quando_br)]
    if not ativos:
        return ORDEM[0]
    ult = est.get("ultimo")
    i = (ativos.index(ult) + 1) % len(ativos) if ult in ativos else 0
    est["ultimo"] = ativos[i]
    return ativos[i]


def primeira_vendedora(c, cid):
    if not VENDEDORAS:
        return None
    r = c.execute("select lower(autor) from wa_mensagens where conversa_id=? and origem='atendente' "
                  "and lower(autor) in (%s) order by id limit 1" % ",".join("?" * len(VENDEDORAS)),
                  (cid, *VENDEDORAS)).fetchone()
    return VENDEDORAS[r[0]] if r else None


def aberto(etapa):
    return not any(e in (etapa or "").lower() for e in ENCERRADAS)


def carregar_estado():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            est = json.load(f)
    except Exception:
        est = {}
    est.setdefault("auto", {})          # conversa_id -> dono dado pelo robo (provisorio)
    return est


def salvar_estado(est):
    tmp = ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(est, f, ensure_ascii=False)
    os.replace(tmp, ESTADO)


def marcar(c, cid, dona, so_se_vazio=True):
    cond = " and coalesce(atendente_email,'')=''" if so_se_vazio else ""
    return c.execute("update wa_conversas set atendente_email=? where id=?" + cond, (EMAIL[dona], cid)).rowcount


def simular_ou_estoque(aplicar, estoque):
    if not ORDEM:
        print("nenhum vendedor com e-mail em VENDEDORES: nada a fazer")
        return
    c = sqlite3.connect(DB, timeout=60) if aplicar else sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    est = carregar_estado()
    leads = c.execute(
        "select id, coalesce(contato_nome,''), contato_telefone, pipeline_id, coalesce(etapa,''), "
        "coalesce(atendente_email,''), criado_em from wa_conversas where pipeline_id in (%s) and status='aberto' "
        "order by criado_em" % ",".join(map(str, QUADROS))).fetchall()
    vez, linhas, motivo_ct, dona_ct, sim = {}, [], Counter(), Counter(), {}
    for cid, nome, tel, pip, etapa, atual, criado in leads:
        if not aberto(etapa):
            continue
        if atual:
            dona, motivo = VENDEDORAS.get(atual.lower(), atual), "ja tinha dono"
        else:
            p = primeira_vendedora(c, cid)
            if p:
                dona, motivo = p, "mandou mensagem primeiro"
            elif estoque:
                vez[pip] = (vez.get(pip, -1) + 1) % len(ORDEM)
                dona, motivo = ORDEM[vez[pip]], "estoque sem contato: dividido igualmente"
            else:
                dona, motivo = regra_da_vez(br(criado), sim), "lead sem contato: rodizio"
        motivo_ct[motivo] += 1
        dona_ct[(pip, dona)] += 1
        linhas.append((cid, nome, tel, pip, etapa, atual, dona, motivo, br(criado).strftime("%d/%m %H:%M")))
    os.makedirs(SAIDA, exist_ok=True)
    arq = os.path.join(SAIDA, f"{'aplicado' if aplicar else 'simulacao'}_{datetime.now():%Y%m%d_%H%M}.csv")
    with open(arq, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["conversa", "nome", "telefone", "quadro", "etapa", "dono_antes", "dono", "motivo", "chegou"])
        w.writerows(linhas)
    print(f"leads abertos analisados: {len(linhas)}  (relatorio: {arq})")
    for m, n in motivo_ct.most_common():
        print(f"  {m}: {n}")
    for pip in QUADROS:
        print(f"  quadro {pip}: " + ", ".join(f"{d} {n}" for (p, d), n in sorted(dona_ct.items()) if p == pip))
    if not aplicar:
        print("SIMULACAO: nada foi gravado.")
        return
    mudou = 0
    with c:
        for cid, _, _, _, _, atual, dona, motivo, _ in linhas:
            if not atual and dona in EMAIL and marcar(c, cid, dona):
                mudou += 1
                if not motivo.startswith("mandou"):
                    est["auto"][str(cid)] = dona       # provisorio: passa para quem escrever primeiro
    salvar_estado(est)
    print(f"APLICADO: {mudou} leads ganharam dono")


def novos():
    """Cron: dono para lead novo e troca o dono provisorio para quem mandou a primeira mensagem."""
    if not ORDEM:
        return
    trava = open(ESTADO + ".lock", "w")
    try:
        if fcntl:
            fcntl.flock(trava, fcntl.LOCK_EX)
        est = carregar_estado()
        c = sqlite3.connect(DB, timeout=60)
        feitos = []
        with c:
            desde = (datetime.utcnow() - timedelta(hours=JANELA_NOVOS_H)).strftime("%Y-%m-%d %H:%M:%S")
            for cid, pip, etapa in c.execute(
                    "select id, pipeline_id, coalesce(etapa,'') from wa_conversas where pipeline_id in (%s) "
                    "and status='aberto' and coalesce(atendente_email,'')='' and criado_em>=?"
                    % ",".join(map(str, QUADROS)), (desde,)).fetchall():
                if not aberto(etapa):
                    continue
                p = primeira_vendedora(c, cid)
                dona = p or regra_da_vez(agora_br(), est)
                if dona and marcar(c, cid, dona):
                    if not p:
                        est["auto"][str(cid)] = dona
                    feitos.append(f"#{cid}->{dona}{'' if p else ' (rodizio)'}")
            for scid, dona in list(est["auto"].items()):
                row = c.execute("select coalesce(atendente_email,''), pipeline_id from wa_conversas where id=?",
                                (int(scid),)).fetchone()
                if not row or row[0].lower() != EMAIL.get(dona) or row[1] not in QUADROS:
                    est["auto"].pop(scid, None)        # alguem mudou na mao ou saiu do quadro: nao mexo mais
                    continue
                p = primeira_vendedora(c, int(scid))
                if p:
                    if p != dona:
                        marcar(c, int(scid), p, so_se_vazio=False)
                        feitos.append(f"#{scid} {dona}->{p} (escreveu primeiro)")
                    est["auto"].pop(scid, None)
        salvar_estado(est)
        if feitos:
            log("; ".join(feitos))
    finally:
        if fcntl:
            fcntl.flock(trava, fcntl.LOCK_UN)
        trava.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="grava (com --estoque)")
    ap.add_argument("--estoque", action="store_true", help="os que ja passaram sem contato: dividir igualmente")
    ap.add_argument("--novos", action="store_true", help="cron: dono para lead novo")
    a = ap.parse_args()
    if a.novos:
        novos()
    else:
        simular_ou_estoque(a.aplicar, a.estoque)


if __name__ == "__main__":
    main()
