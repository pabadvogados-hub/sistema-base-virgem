"""
CARTEIRA DE PROCESSOS - modulo Processos do Sistema Escritório Modelo

Traz para o modulo oficial Processos o que hoje so existe dentro da aba
"Processos" da Controladoria (dados do SYNC: tribunal, orgao, classe,
titularidade, intimacoes abertas, proximo prazo, pasta do caso) e cruza com
os processos ATIVOS do ADVBOX, que e a base atualizada.

Acrescenta tambem a ficha editavel (anotacoes e valores), como no ADVBOX:
    notas, valor_expectativa, valor_honorarios, pasta

Router separado de propósito: se o SYNC/Controladoria estiver fora do ar, o
modulo Processos antigo continua respondendo normalmente.

IMPORTANTE: este router precisa ser incluido ANTES do processos_router, porque
la existe GET /api/processos/{processo_id} tipado como int - registrado antes,
ele tentaria casar "carteira" como inteiro e devolver 422.
"""
import json
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..deps import usuario_atual

router = APIRouter(prefix="/api/processos", tags=["processos-carteira"])

_DB = str(Path(__file__).resolve().parents[2] / "sistema_esc.db")


def _con():
    c = sqlite3.connect(_DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


class FichaIn(BaseModel):
    notas: Optional[str] = None
    valor_expectativa: Optional[float] = None
    valor_honorarios: Optional[float] = None
    contingencia: Optional[float] = None
    pasta: Optional[str] = None


def _ativos_do_advbox(con):
    """
    numero -> dados do processo no nosso ADVBOX (jur_processos).
    So os ATIVOS: a carteira de trabalho e o que nao esta encerrado.
    """
    fora = {}
    for r in con.execute(
        "SELECT id, numero, area, tipo, fase, etapa, responsavel, encerrado, "
        "data_processo, pasta, valor_expectativa, valor_honorarios, contingencia, notas "
        "FROM jur_processos WHERE numero IS NOT NULL AND TRIM(numero) <> ''"
    ):
        fora[(r["numero"] or "").strip()] = dict(r)
    return fora


def _carteira_bruta():
    """Cards do SYNC. Isolado: se a Controladoria falhar, devolve vazio."""
    try:
        from .controladoria import _montar_carteira
        return _montar_carteira() or {}
    except Exception as e:  # noqa: BLE001
        return {"cards": [], "erro": "%s: %s" % (type(e).__name__, e)}


@router.get("/carteira")
def carteira(
    apenas_ativos: bool = Query(True, description="So processos nao encerrados no ADVBOX"),
    com_intimacao: bool = Query(False),
    com_prazo: bool = Query(False),
    fora_do_advbox: bool = Query(False),
    q: Optional[str] = None,
    limite: int = Query(500, le=2000),
    user=Depends(usuario_atual),
):
    """
    Carteira consolidada: SYNC (o que e monitorado) x ADVBOX (o que esta ativo).

    Cada card ganha o bloco `ficha` com valores e anotacoes vindos do ADVBOX,
    e o campo `encerrado`. O filtro padrao esconde o que ja foi encerrado -
    processo encerrado nao e carteira de trabalho.
    """
    bruta = _carteira_bruta()
    cards = bruta.get("cards") or []
    con = _con()
    try:
        advbox = _ativos_do_advbox(con)
    finally:
        con.close()

    saida = []
    for c in cards:
        num = (c.get("numero") or "").strip()
        p = advbox.get(num)
        card = dict(c)
        card["no_advbox"] = p is not None
        card["encerrado"] = bool(p["encerrado"]) if p else False
        card["processo_id"] = p["id"] if p else None
        card["ficha"] = {
            "notas": (p or {}).get("notas") or "",
            "valor_expectativa": (p or {}).get("valor_expectativa"),
            "valor_honorarios": (p or {}).get("valor_honorarios"),
            "contingencia": (p or {}).get("contingencia"),
            "pasta": (p or {}).get("pasta") or card.get("pasta_drive") or "",
        }
        if p:
            # o ADVBOX e a base atualizada: fase/etapa/area de la mandam
            for campo in ("area", "tipo", "fase", "etapa", "responsavel", "data_processo"):
                if p.get(campo):
                    card[campo] = p[campo]

        if apenas_ativos and card["encerrado"]:
            continue
        if com_intimacao and not (card.get("intimacoes_abertas") or 0):
            continue
        if com_prazo and not card.get("proximo_prazo"):
            continue
        if fora_do_advbox and card["no_advbox"]:
            continue
        if q:
            alvo = " ".join(str(card.get(k) or "") for k in
                            ("numero", "cliente", "parte_adversa", "classe", "orgao", "tribunal"))
            if q.lower() not in alvo.lower():
                continue
        saida.append(card)

    saida.sort(key=lambda c: ((c.get("proximo_prazo") or {}).get("data") or "9999",
                              c.get("numero") or ""))
    saida = saida[:limite]

    return {
        "cards": saida,
        "total": len(saida),
        "com_intimacao_aberta": sum(1 for c in saida if (c.get("intimacoes_abertas") or 0)),
        "com_prazo": sum(1 for c in saida if c.get("proximo_prazo")),
        "sem_cadastro_advbox": sum(1 for c in saida if not c["no_advbox"]),
        "erro_sync": bruta.get("erro"),
    }


@router.get("/ficha/{numero}")
def ler_ficha(numero: str, user=Depends(usuario_atual)):
    """Ficha do processo (anotacoes e valores) pelo NUMERO do processo."""
    con = _con()
    try:
        r = con.execute(
            "SELECT id, numero, area, tipo, fase, etapa, responsavel, encerrado, "
            "data_processo, pasta, valor_expectativa, valor_honorarios, contingencia, notas "
            "FROM jur_processos WHERE TRIM(numero)=? LIMIT 1", (numero.strip(),)).fetchone()
        if not r:
            raise HTTPException(404, "Processo nao encontrado no ADVBOX")
        d = dict(r)

        partes = [dict(x) for x in con.execute(
            "SELECT nome, papel, cliente_id FROM jur_processo_partes "
            "WHERE processo_id=? ORDER BY papel", (r["id"],))]
        d["partes"] = partes

        # ficha do cliente (o primeiro que nao e parte contraria)
        d["cliente_ficha"] = None
        for pt in partes:
            if "CONTR" in (pt.get("papel") or "").upper():
                continue
            if pt.get("cliente_id"):
                c = con.execute("SELECT * FROM jur_clientes WHERE id=?",
                                (pt["cliente_id"],)).fetchone()
                if c:
                    d["cliente_ficha"] = dict(c)
                    break
        return d
    finally:
        con.close()


@router.patch("/ficha/{numero}")
def gravar_ficha(numero: str, dados: FichaIn, user=Depends(usuario_atual)):
    """
    Grava anotacoes e valores do processo - o equivalente a ficha do ADVBOX.

    Grava no NOSSO banco (jur_processos). Nao empurra para a API do ADVBOX:
    isso seria escrita em sistema de terceiro e precisa de decisao a parte.
    """
    campos = dados.model_dump(exclude_unset=True)
    if not campos:
        raise HTTPException(400, "Nada para gravar")

    con = _con()
    try:
        r = con.execute("SELECT id FROM jur_processos WHERE TRIM(numero)=? LIMIT 1",
                        (numero.strip(),)).fetchone()
        if not r:
            raise HTTPException(404, "Processo nao encontrado no ADVBOX")
        sets = ", ".join(k + "=?" for k in campos)
        con.execute("UPDATE jur_processos SET " + sets + ", atualizado_em=datetime('now') "
                    "WHERE id=?", list(campos.values()) + [r["id"]])
        con.commit()
        novo = con.execute(
            "SELECT numero, pasta, valor_expectativa, valor_honorarios, contingencia, notas "
            "FROM jur_processos WHERE id=?", (r["id"],)).fetchone()
        return {"ok": True, "ficha": dict(novo)}
    finally:
        con.close()
