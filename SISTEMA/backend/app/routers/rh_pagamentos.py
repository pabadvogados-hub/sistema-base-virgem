import os
""
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import usuario_atual

router = APIRouter(prefix="/api/rh", tags=["rh"])

_DB = str(Path(__file__).resolve().parents[2] / "sistema_esc.db")

PAGAMENTO_DIVIDIDO = {int(x) for x in os.getenv("RH_PAGAMENTO_DIVIDIDO_IDS", "").split(",") if x.strip().isdigit()}
AVISO_DIVIDIDO = ("Esta tela mostra a parte paga pelo Escritório Modelo Advogados. A outra parte da sua "
                  "remuneração é paga por outra empresa e não entra neste demonstrativo.")


def _con():
    c = sqlite3.connect(_DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _admin(user):
    return (getattr(user, "papel", "") or "") == "admin"


def _colaborador(c, user, colaborador_id: Optional[int]):
    ""
    if colaborador_id and _admin(user):
        r = c.execute("SELECT id, nome, tipo_contrato FROM rh_colaboradores WHERE id=?",
                      (colaborador_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Colaborador nao encontrado")
        return r
    r = c.execute("SELECT id, nome, tipo_contrato FROM rh_colaboradores WHERE usuario_id=?",
                  (user.id,)).fetchone()
    if not r and getattr(user, "email", None):
        r = c.execute("SELECT id, nome, tipo_contrato FROM rh_colaboradores WHERE lower(email)=?",
                      (user.email.lower(),)).fetchone()
    if not r:
        raise HTTPException(404, "Este login ainda nao tem ficha de RH")
    if colaborador_id and int(colaborador_id) != int(r["id"]):
        raise HTTPException(403, "Voce so pode ver os seus proprios pagamentos")
    return r


@router.get("/pagamentos")
def pagamentos(colaborador_id: Optional[int] = Query(None), user=Depends(usuario_atual)):
    c = _con()
    try:
        ficha = _colaborador(c, user, colaborador_id)
        existe = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rh_pagamentos'").fetchone()
        if existe and "fonte" not in [r[1] for r in c.execute("PRAGMA table_info(rh_pagamentos)")]:
            c.execute("ALTER TABLE rh_pagamentos ADD COLUMN fonte TEXT")
            c.commit()
        if not existe:
            return {"colaborador": {"id": ficha["id"], "nome": ficha["nome"]},
                    "pagamentos": [], "anos": [], "total": 0, "sincronizado_em": None,
                    "aviso": "A sincronia com o financeiro ainda nao rodou."}
        linhas = c.execute(
            "SELECT data_pagamento, competencia, rotulo, descricao, valor, banco, fonte, "
            "sincronizado_em FROM rh_pagamentos WHERE colaborador_id=? "
            "ORDER BY data_pagamento DESC, id DESC",
            (ficha["id"],)).fetchall()
        itens = [dict(r) for r in linhas]
        anos = {}
        for i in itens:
            ano = (i["data_pagamento"] or "")[:4] or "sem data"
            a = anos.setdefault(ano, {"ano": ano, "total": 0.0, "qtd": 0})
            a["total"] += i["valor"] or 0
            a["qtd"] += 1
        return {
            "colaborador": {"id": ficha["id"], "nome": ficha["nome"],
                            "tipo_contrato": ficha["tipo_contrato"]},
            "pagamentos": itens,
            "anos": sorted(anos.values(), key=lambda x: x["ano"], reverse=True),
            "total": round(sum((i["valor"] or 0) for i in itens), 2),
            "observacao": AVISO_DIVIDIDO if ficha["id"] in PAGAMENTO_DIVIDIDO else None,
            "sincronizado_em": max([i["sincronizado_em"] for i in itens if i["sincronizado_em"]] or [None]),
        }
    finally:
        c.close()
