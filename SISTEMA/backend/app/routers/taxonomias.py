"""
TAXONOMIAS - as listas fechadas que o escritorio administra. Sistema Escritório Modelo.

Grupo de acao, tipo de acao, classificacao do caso e fase deixam de ser texto
livre e passam a ser escolhidos de uma lista mantida em Configuracoes.

POR QUE FECHAR: texto livre mata metrica. "CIVEL", "Civel" e "CIVEL " viram tres
carteiras diferentes no dashboard, e ninguem descobre olhando o grafico - so
quando o numero nao fecha.

Regras de seguranca dos dados:
  RENOMEAR  -> cascateia para os casos que usam o valor antigo, senao a
               metrica se parte em duas.
  EXCLUIR   -> desativa (ativo=0). O historico mantem o valor; a lista de
               escolha deixa de oferecer. Excluir de verdade so com ?force=1
               e ainda assim os casos existentes ficam intactos.
"""
import sqlite3
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..deps import usuario_atual, requer_admin

# PERMISSAO POR ROTA (regra do escritorio, 02/09/2026):
#   ver e INCLUIR  -> qualquer usuario. E a operacao do dia a dia: apareceu um
#                     tipo de acao novo, a pessoa cadastra e segue.
#   RENOMEAR/EXCLUIR -> so admin. Essas duas mexem em dado JA GRAVADO: renomear
#                     cascateia para os casos existentes e excluir tira da
#                     escolha. Erro ali reescreve historico.
# Ao acrescentar rota nova aqui, decida explicitamente de que lado ela fica.
router = APIRouter(prefix="/api/taxonomias", tags=["taxonomias"])

_DB = str(Path(__file__).resolve().parents[2] / "sistema_esc.db")

# lista -> (coluna em `casos`, rotulo na tela)
LISTAS = {
    "grupo_acao":    ("area", "Grupo de ação (carteira)"),
    "tipo_acao":     ("tipo_acao", "Tipo de ação"),
    "classificacao": ("tipo", "Classificação do caso"),
    "fase":          ("fase", "Fase"),
    "etapa":         ("etapa", "Etapa"),
}


def _con():
    c = sqlite3.connect(_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def garantir_tabelas():
    c = _con()
    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS taxonomias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lista TEXT NOT NULL,
                valor TEXT NOT NULL,
                ordem INTEGER DEFAULT 0,
                ativo INTEGER DEFAULT 1,
                criado_em REAL,
                atualizado_em REAL
            )
        """)
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_tax_valor "
                  "ON taxonomias(lista, valor)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_tax_lista ON taxonomias(lista, ativo, ordem)")
        c.commit()
    finally:
        c.close()


garantir_tabelas()


class ItemIn(BaseModel):
    lista: str
    valor: str


class ItemPatch(BaseModel):
    valor: Optional[str] = None
    ordem: Optional[int] = None
    ativo: Optional[bool] = None


def _valida_lista(lista):
    if lista not in LISTAS:
        raise HTTPException(400, "lista invalida. Use: " + ", ".join(LISTAS))


def _em_uso(c, lista, valor):
    coluna = LISTAS[lista][0]
    try:
        return c.execute("SELECT COUNT(*) FROM casos WHERE " + coluna + "=?",
                         (valor,)).fetchone()[0]
    except sqlite3.Error:
        return 0


@router.get("/listas")
def catalogo(user=Depends(usuario_atual)):
    """Quais listas existem e o rotulo de cada uma."""
    return {"listas": [{"chave": k, "rotulo": v[1], "coluna": v[0]}
                       for k, v in LISTAS.items()]}


@router.get("")
def listar(
    lista: Optional[str] = None,
    incluir_inativos: bool = False,
    user=Depends(usuario_atual),
):
    c = _con()
    try:
        onde, args = [], []
        if lista:
            _valida_lista(lista)
            onde.append("lista = ?")
            args.append(lista)
        if not incluir_inativos:
            onde.append("ativo = 1")
        sql = "SELECT * FROM taxonomias"
        if onde:
            sql += " WHERE " + " AND ".join(onde)
        sql += " ORDER BY lista, ordem, valor"

        out = {}
        for r in c.execute(sql, args):
            d = dict(r)
            d["em_uso"] = _em_uso(c, d["lista"], d["valor"])
            out.setdefault(d["lista"], []).append(d)
        return {"taxonomias": out}
    finally:
        c.close()


@router.post("")
def criar(dados: ItemIn, user=Depends(usuario_atual)):
    _valida_lista(dados.lista)
    valor = (dados.valor or "").strip()
    if not valor:
        raise HTTPException(400, "valor obrigatorio")
    c = _con()
    try:
        ja = c.execute("SELECT id, ativo FROM taxonomias WHERE lista=? AND valor=?",
                       (dados.lista, valor)).fetchone()
        if ja:
            if not ja["ativo"]:
                # reativa em vez de duplicar
                c.execute("UPDATE taxonomias SET ativo=1, atualizado_em=? WHERE id=?",
                          (time.time(), ja["id"]))
                c.commit()
                return {"ok": True, "reativado": True, "id": ja["id"]}
            raise HTTPException(409, "Esse valor ja existe nesta lista")
        agora = time.time()
        prox = c.execute("SELECT COALESCE(MAX(ordem),0)+1 FROM taxonomias WHERE lista=?",
                         (dados.lista,)).fetchone()[0]
        cur = c.execute("INSERT INTO taxonomias (lista, valor, ordem, ativo, criado_em, "
                        "atualizado_em) VALUES (?,?,?,1,?,?)",
                        (dados.lista, valor, prox, agora, agora))
        c.commit()
        return {"ok": True, "id": cur.lastrowid, "valor": valor}
    finally:
        c.close()


@router.patch("/{item_id}")
def editar(item_id: int, dados: ItemPatch, user=Depends(requer_admin)):
    """
    Renomear cascateia para os casos que usam o valor antigo. Sem isso a
    metrica se parte: metade dos casos no nome velho, metade no novo.
    """
    c = _con()
    try:
        r = c.execute("SELECT * FROM taxonomias WHERE id=?", (item_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Item nao encontrado")

        atualizados = 0
        novo_valor = (dados.valor or "").strip() if dados.valor is not None else None
        if novo_valor and novo_valor != r["valor"]:
            dup = c.execute("SELECT id FROM taxonomias WHERE lista=? AND valor=? AND id<>?",
                            (r["lista"], novo_valor, item_id)).fetchone()
            if dup:
                raise HTTPException(409, "Ja existe outro item com esse nome nesta lista")
            coluna = LISTAS[r["lista"]][0]
            try:
                atualizados = c.execute(
                    "UPDATE casos SET " + coluna + "=?, atualizado_em=? WHERE " + coluna + "=?",
                    (novo_valor, time.time(), r["valor"])).rowcount
            except sqlite3.Error:
                atualizados = 0

        campos = {}
        if novo_valor:
            campos["valor"] = novo_valor
        if dados.ordem is not None:
            campos["ordem"] = dados.ordem
        if dados.ativo is not None:
            campos["ativo"] = int(dados.ativo)
        if campos:
            sets = ", ".join(k + "=?" for k in campos)
            c.execute("UPDATE taxonomias SET " + sets + ", atualizado_em=? WHERE id=?",
                      list(campos.values()) + [time.time(), item_id])
        c.commit()
        return {"ok": True, "casos_atualizados": atualizados,
                "item": dict(c.execute("SELECT * FROM taxonomias WHERE id=?",
                                       (item_id,)).fetchone())}
    finally:
        c.close()


@router.delete("/{item_id}")
def excluir(item_id: int, force: bool = Query(False), user=Depends(requer_admin)):
    """
    Padrao: DESATIVA. O historico mantem o valor e a lista para de oferecer.
    force=1 apaga a linha - os casos que ja usam o valor continuam intactos,
    mas ele some do catalogo e some tambem do filtro do BI.
    """
    c = _con()
    try:
        r = c.execute("SELECT * FROM taxonomias WHERE id=?", (item_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Item nao encontrado")
        uso = _em_uso(c, r["lista"], r["valor"])
        if force:
            c.execute("DELETE FROM taxonomias WHERE id=?", (item_id,))
            c.commit()
            return {"ok": True, "removido": True, "casos_que_usavam": uso}
        c.execute("UPDATE taxonomias SET ativo=0, atualizado_em=? WHERE id=?",
                  (time.time(), item_id))
        c.commit()
        return {"ok": True, "desativado": True, "casos_que_usavam": uso}
    finally:
        c.close()


@router.post("/semear")
def semear(user=Depends(requer_admin)):
    """
    Primeira carga: puxa o vocabulario que JA esta em uso nos 714 processos
    importados e nos casos. Idempotente - so acrescenta o que falta, nunca
    apaga nem renomeia nada.
    """
    origem = {
        # lista           (tabela,          coluna)  -- atencao a inversao do import:
        "grupo_acao":    [("jur_processos", "area"), ("casos", "area")],
        "tipo_acao":     [("jur_processos", "tipo"), ("casos", "tipo_acao")],
        # jur_processos.etapa = o que a tela do ADVBOX chama de FASE
        "fase":          [("jur_processos", "etapa"), ("casos", "fase")],
        # jur_processos.fase = o que a tela do ADVBOX chama de ETAPA
        "etapa":         [("jur_processos", "fase"), ("casos", "etapa")],
        "classificacao": [("casos", "tipo")],
    }
    padrao_classificacao = ["CONTENCIOSO", "CONSULTORIA", "REVISAO CONTRATUAL",
                            "PARECER", "LEAD", "ACORDO EXTRAJUDICIAL", "ADMINISTRATIVO"]

    c = _con()
    try:
        agora = time.time()
        criados = {}
        for lista, fontes in origem.items():
            valores = []
            for tabela, coluna in fontes:
                try:
                    valores += [r[0] for r in c.execute(
                        "SELECT " + coluna + " FROM " + tabela + " WHERE " + coluna +
                        " IS NOT NULL AND TRIM(" + coluna + ")<>'' "
                        "GROUP BY " + coluna + " ORDER BY COUNT(*) DESC")]
                except sqlite3.Error:
                    pass
            if lista == "classificacao":
                valores += padrao_classificacao

            n = 0
            vistos = set()
            for v in valores:
                v = (v or "").strip()
                if not v or v.upper() in vistos:
                    continue
                vistos.add(v.upper())
                ja = c.execute("SELECT id FROM taxonomias WHERE lista=? AND valor=?",
                               (lista, v)).fetchone()
                if ja:
                    continue
                prox = c.execute("SELECT COALESCE(MAX(ordem),0)+1 FROM taxonomias "
                                 "WHERE lista=?", (lista,)).fetchone()[0]
                c.execute("INSERT INTO taxonomias (lista, valor, ordem, ativo, criado_em, "
                          "atualizado_em) VALUES (?,?,?,1,?,?)", (lista, v, prox, agora, agora))
                n += 1
            criados[lista] = n
        c.commit()
        return {"ok": True, "criados": criados}
    finally:
        c.close()
