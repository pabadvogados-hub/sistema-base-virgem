""
import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import requer_financeiro

router = APIRouter(prefix="/api/fin", tags=["financeiro-proprio"])

_DB = str(Path(__file__).resolve().parents[2] / "sistema_esc.db")
PROJ = Path(__file__).resolve().parents[4]
ADVBOX_API = "https://app.advbox.com.br/api/v1"


def _con():
    c = sqlite3.connect(_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


DDL = [
    """CREATE TABLE IF NOT EXISTS fin_contas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE, instituicao TEXT,
        ativo INTEGER DEFAULT 1, advbox_id INTEGER UNIQUE, criado_em REAL)""",
    """CREATE TABLE IF NOT EXISTS fin_categorias (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE,
        natureza TEXT NOT NULL,                  -- receita | despesa
        grupo TEXT DEFAULT 'operacional',        -- ver GRUPOS
        ativo INTEGER DEFAULT 1, advbox_id INTEGER UNIQUE, criado_em REAL)""",
    """CREATE TABLE IF NOT EXISTS fin_centros (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE,
        ativo INTEGER DEFAULT 1, advbox_id INTEGER UNIQUE, criado_em REAL)""",
    """CREATE TABLE IF NOT EXISTS fin_lancamentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL,                      -- receita | despesa
        descricao TEXT,
        valor_cent INTEGER NOT NULL,
        vencimento TEXT NOT NULL,                -- AAAA-MM-DD
        competencia TEXT,                        -- AAAA-MM
        pagamento TEXT,                          -- AAAA-MM-DD (baixa); NULL = em aberto
        conta_id INTEGER, categoria_id INTEGER, centro_id INTEGER,
        pessoa_nome TEXT, pessoa_doc TEXT, processo_numero TEXT, lawsuit_id INTEGER,
        responsavel TEXT, interno INTEGER DEFAULT 0, recorrente INTEGER DEFAULT 0,
        origem TEXT DEFAULT 'manual',            -- advbox | manual | comissao | asaas | c6 | cartao
        ref TEXT,                                -- chave do que gerou (ex.: comissao:2026-09:ca:123)
        advbox_id INTEGER UNIQUE, advbox_hash TEXT, advbox_mudou_em REAL,
        editado_local INTEGER DEFAULT 0, removido_advbox INTEGER DEFAULT 0,
        conciliado_em REAL, conciliado_ref TEXT,
        obs TEXT,
        excluido INTEGER DEFAULT 0, excluido_por TEXT, excluido_em REAL,
        criado_por TEXT, criado_em REAL, atualizado_por TEXT, atualizado_em REAL)""",
    "CREATE INDEX IF NOT EXISTS ix_fin_venc ON fin_lancamentos (vencimento)",
    "CREATE INDEX IF NOT EXISTS ix_fin_pag ON fin_lancamentos (pagamento)",
    "CREATE INDEX IF NOT EXISTS ix_fin_comp ON fin_lancamentos (competencia)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_fin_ref ON fin_lancamentos (ref) WHERE ref IS NOT NULL AND excluido=0",
    """CREATE TABLE IF NOT EXISTS fin_historico (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lancamento_id INTEGER, acao TEXT,
        detalhe TEXT, por TEXT, em REAL)""",
    "CREATE INDEX IF NOT EXISTS ix_fin_hist ON fin_historico (lancamento_id)",
    """CREATE TABLE IF NOT EXISTS fin_importacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, inicio REAL, fim REAL, por TEXT,
        lidos INTEGER, novos INTEGER, alterados INTEGER, protegidos INTEGER,
        removidos INTEGER, erro TEXT)""",
]

GRUPOS = ("operacional", "distribuicao", "capital", "transferencia", "repasse")
GRUPO_POR_NOME = {
    "DISTRIBUIÇÃO DE LUCROS": "distribuicao",
    "APORTE DE SÓCIO": "capital", "INTEGRALIZAÇÃO DE SÓCIO": "capital",
    "DEVOLUÇÃO APORTE DO SÓCIO": "capital", "EMPRÉSTIMO (PESSOAL)": "capital",
    "TRANSFERÊNCIA ENTRE CONTAS": "transferencia", "SALDO INICIAL": "transferencia",
    "APLICAÇÕES FINANCEIRAS": "transferencia", "INVESTIMENTO": "transferencia",
    "CAPITAL DE RESERVA": "transferencia", "PAGAMENTO DE FATURA": "transferencia",
    "PAGAMENTO DE CONDENAÇÃO OU ACORDO CLIENTE": "repasse", "REEMBOLSO DE CONDENAÇÃO/ACORDO": "repasse",
    "RESTITUIÇÃO VALOR CLIENTE": "repasse", "ALVARÁS": "repasse",
}


def _schema(c):
    for d in DDL:
        c.execute(d)


def _agora():
    return time.time()


def _nome(user):
    return getattr(user, "nome", None) or getattr(user, "email", None) or "sistema"


def _cent(v):
    ""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(round(float(v) * 100))
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return int(round(float(s) * 100))
    except ValueError:
        raise HTTPException(400, "Valor invalido: escreva so o numero, ex.: 1.250,00")


def _data(s, campo, obrig=False):
    if s in (None, ""):
        if obrig:
            raise HTTPException(400, f"{campo}: informe a data")
        return None
    try:
        return date.fromisoformat(str(s)[:10]).isoformat()
    except ValueError:
        raise HTTPException(400, f"{campo}: data invalida (AAAA-MM-DD)")


def _hist(c, lid, acao, detalhe, por):
    c.execute("INSERT INTO fin_historico (lancamento_id, acao, detalhe, por, em) VALUES (?,?,?,?,?)",
              (lid, acao, json.dumps(detalhe, ensure_ascii=False, default=str)[:4000] if detalhe else None,
               por, _agora()))


def _id_por_nome(c, tabela, nome, extra=None):
    ""
    nome = (nome or "").strip()
    if not nome:
        return None
    r = c.execute(f"SELECT id FROM {tabela} WHERE nome=?", (nome,)).fetchone()
    if r:
        return r["id"]
    cols, vals = ["nome", "criado_em"], [nome, _agora()]
    for k, v in (extra or {}).items():
        cols.append(k)
        vals.append(v)
    cur = c.execute(f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", vals)
    return cur.lastrowid


def _mapas(c):
    return ({r["id"]: dict(r) for r in c.execute("SELECT * FROM fin_contas")},
            {r["id"]: dict(r) for r in c.execute("SELECT * FROM fin_categorias")},
            {r["id"]: dict(r) for r in c.execute("SELECT * FROM fin_centros")})


def _advbox_token():
    tok = os.getenv("ADVBOX_API_TOKEN")
    if tok:
        return tok
    for env in (PROJ / "config" / ".env", Path("/opt/escritorio/config/.env")):
        if env.exists():
            for linha in env.read_text(encoding="utf-8").splitlines():
                if linha.startswith("ADVBOX_API_TOKEN="):
                    return linha.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("ADVBOX_API_TOKEN nao configurado")


def _advbox_get(rota, params=None):
    import requests
    h = {"Authorization": "Bearer " + _advbox_token(), "User-Agent": "SistemaEscritorio/1.0", "Accept": "application/json"}
    for tentativa in range(4):
        r = requests.get(ADVBOX_API + rota, headers=h, params=params or {}, timeout=90)
        if r.status_code == 429:
            time.sleep(15 * (tentativa + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("ADVBOX recusou por limite de uso (429) 4 vezes")


def _semear_cadastros(c):
    ""
    s = _advbox_get("/settings").get("financial") or {}
    for b in s.get("banks") or []:
        c.execute("INSERT OR IGNORE INTO fin_contas (nome, instituicao, advbox_id, criado_em) VALUES (?,?,?,?)",
                  (b["name"], b.get("institution"), b["id"], _agora()))
    for k in s.get("categories") or []:
        nat = "receita" if (k.get("type") or "").upper().startswith("CR") else "despesa"
        c.execute("INSERT OR IGNORE INTO fin_categorias (nome, natureza, grupo, advbox_id, criado_em) VALUES (?,?,?,?,?)",
                  (k["category"], nat, GRUPO_POR_NOME.get(k["category"], "operacional"), k["id"], _agora()))
    for k in s.get("cost_centers") or []:
        c.execute("INSERT OR IGNORE INTO fin_centros (nome, advbox_id, criado_em) VALUES (?,?,?)",
                  (k["cost_center"], k["id"], _agora()))


CAMPOS_ADVBOX = ("entry_type", "date_due", "date_payment", "competence", "amount", "description", "is_internal",
                 "is_recurrent", "responsible", "category", "lawsuit_id", "process_number", "name",
                 "identification", "debit_bank", "credit_bank", "cost_center")


def _de_advbox(c, t):
    ""
    tipo = "receita" if t.get("entry_type") == "income" else "despesa"
    comp = None
    m = re.fullmatch(r"(\d{2})/(\d{4})", str(t.get("competence") or ""))
    if m:
        comp = f"{m.group(2)}-{m.group(1)}"
    cat_nome = (t.get("category") or "").strip()
    cat = _id_por_nome(c, "fin_categorias", cat_nome,
                       {"natureza": tipo, "grupo": GRUPO_POR_NOME.get(cat_nome, "operacional")}) if cat_nome else None
    return {
        "tipo": tipo, "descricao": t.get("description"), "valor_cent": int(round(float(t.get("amount") or 0) * 100)),
        "vencimento": (t.get("date_due") or t.get("date_payment") or "")[:10] or None,
        "competencia": comp, "pagamento": (t.get("date_payment") or "")[:10] or None,
        "conta_id": _id_por_nome(c, "fin_contas", t.get("debit_bank") or t.get("credit_bank")),
        "categoria_id": cat, "centro_id": _id_por_nome(c, "fin_centros", t.get("cost_center")),
        "pessoa_nome": t.get("name"), "pessoa_doc": t.get("identification"),
        "processo_numero": t.get("process_number"), "lawsuit_id": t.get("lawsuit_id"),
        "responsavel": t.get("responsible"), "interno": 1 if t.get("is_internal") else 0,
        "recorrente": 1 if t.get("is_recurrent") else 0,
    }


def importar_advbox(por="cron"):
    ""
    c = _con()
    ini = _agora()
    lidos = novos = alterados = protegidos = removidos = 0
    try:
        _schema(c)
        if not c.execute("SELECT COUNT(*) FROM fin_categorias").fetchone()[0]:
            _semear_cadastros(c)
        vistos, off = set(), 0
        while True:
            j = _advbox_get("/transactions", {"limit": 1000, "offset": off})
            dados = j.get("data") or []
            for t in dados:
                if not t.get("id"):
                    continue
                lidos += 1
                vistos.add(t["id"])
                h = hashlib.sha1(json.dumps({k: t.get(k) for k in CAMPOS_ADVBOX}, sort_keys=True,
                                            default=str).encode()).hexdigest()
                ex = c.execute("SELECT id, advbox_hash, editado_local, removido_advbox FROM fin_lancamentos "
                               "WHERE advbox_id=?", (t["id"],)).fetchone()
                if ex and ex["advbox_hash"] == h and not ex["removido_advbox"]:
                    continue
                v = _de_advbox(c, t)
                if not v["vencimento"]:
                    continue
                if not ex:
                    cols = list(v) + ["origem", "advbox_id", "advbox_hash", "criado_por", "criado_em", "atualizado_em"]
                    vals = list(v.values()) + ["advbox", t["id"], h, "ADVBOX", _agora(), _agora()]
                    cur = c.execute(f"INSERT INTO fin_lancamentos ({', '.join(cols)}) VALUES "
                                    f"({', '.join('?' for _ in cols)})", vals)
                    novos += 1
                elif ex["editado_local"]:
                    c.execute("UPDATE fin_lancamentos SET advbox_hash=?, advbox_mudou_em=? WHERE id=?",
                              (h, _agora(), ex["id"]))
                    _hist(c, ex["id"], "advbox_mudou_ignorado", {k: t.get(k) for k in CAMPOS_ADVBOX}, "ADVBOX")
                    protegidos += 1
                else:
                    sets = ", ".join(f"{k}=?" for k in v)
                    c.execute(f"UPDATE fin_lancamentos SET {sets}, advbox_hash=?, removido_advbox=0, atualizado_em=? "
                              f"WHERE id=?", list(v.values()) + [h, _agora(), ex["id"]])
                    _hist(c, ex["id"], "copiado_do_advbox", {"pagamento": v["pagamento"], "valor_cent": v["valor_cent"],
                                                              "vencimento": v["vencimento"]}, "ADVBOX")
                    alterados += 1
            off += len(dados)
            if not dados or off >= int(j.get("totalCount") or 0):
                break
            time.sleep(1)
        if lidos and vistos:
            for r in c.execute("SELECT id, advbox_id FROM fin_lancamentos WHERE origem='advbox' AND removido_advbox=0 "
                               "AND editado_local=0 AND excluido=0").fetchall():
                if r["advbox_id"] not in vistos:
                    c.execute("UPDATE fin_lancamentos SET removido_advbox=1, atualizado_em=? WHERE id=?", (_agora(), r["id"]))
                    _hist(c, r["id"], "removido_no_advbox", None, "ADVBOX")
                    removidos += 1
        c.execute("INSERT INTO fin_importacoes (inicio, fim, por, lidos, novos, alterados, protegidos, removidos) "
                  "VALUES (?,?,?,?,?,?,?,?)", (ini, _agora(), por, lidos, novos, alterados, protegidos, removidos))
        c.commit()
        return {"lidos": lidos, "novos": novos, "alterados": alterados, "protegidos": protegidos, "removidos": removidos}
    except Exception as e:
        c.rollback()
        c.execute("INSERT INTO fin_importacoes (inicio, fim, por, erro) VALUES (?,?,?,?)", (ini, _agora(), por, str(e)[:500]))
        c.commit()
        raise
    finally:
        c.close()


BASE_VIVOS = "l.excluido=0 AND l.removido_advbox=0"


def _linha(r, contas, cats, centros):
    d = dict(r)
    d["valor"] = round((d.pop("valor_cent") or 0) / 100, 2)
    d["conta"] = (contas.get(d["conta_id"]) or {}).get("nome")
    cat = cats.get(d["categoria_id"]) or {}
    d["categoria"], d["grupo"] = cat.get("nome"), cat.get("grupo")
    d["centro"] = (centros.get(d["centro_id"]) or {}).get("nome")
    hoje = date.today().isoformat()
    d["situacao"] = "paga" if d["pagamento"] else ("vencida" if d["vencimento"] < hoje else
                                                    ("hoje" if d["vencimento"] == hoje else "a_vencer"))
    for k in ("advbox_hash",):
        d.pop(k, None)
    return d


@router.get("/cadastros")
def cadastros(user=Depends(requer_financeiro)):
    c = _con()
    try:
        _schema(c)
        ult = c.execute("SELECT * FROM fin_importacoes ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "contas": [dict(r) for r in c.execute("SELECT * FROM fin_contas ORDER BY ativo DESC, nome")],
            "categorias": [dict(r) for r in c.execute("SELECT * FROM fin_categorias ORDER BY natureza, nome")],
            "centros": [dict(r) for r in c.execute("SELECT * FROM fin_centros ORDER BY nome")],
            "grupos": GRUPOS,
            "ultima_copia": dict(ult) if ult else None,
            "total": c.execute("SELECT COUNT(*) FROM fin_lancamentos l WHERE " + BASE_VIVOS).fetchone()[0],
        }
    finally:
        c.close()


@router.get("/lancamentos")
def lancamentos(tipo: Optional[str] = None, situacao: Optional[str] = None, de: Optional[str] = None,
                ate: Optional[str] = None, por: str = "vencimento", categoria_id: Optional[int] = None,
                centro_id: Optional[int] = None, conta_id: Optional[int] = None, q: Optional[str] = None,
                origem: Optional[str] = None, limite: int = 2000, user=Depends(requer_financeiro)):
    ""
    c = _con()
    try:
        _schema(c)
        sql, args = "SELECT l.* FROM fin_lancamentos l WHERE " + BASE_VIVOS, []
        if tipo in ("receita", "despesa"):
            sql += " AND l.tipo=?"
            args.append(tipo)
        hoje = date.today().isoformat()
        if situacao == "aberta":
            sql += " AND l.pagamento IS NULL"
        elif situacao == "vencida":
            sql += " AND l.pagamento IS NULL AND l.vencimento<?"
            args.append(hoje)
        elif situacao == "a_vencer":
            sql += " AND l.pagamento IS NULL AND l.vencimento>=?"
            args.append(hoje)
        elif situacao == "paga":
            sql += " AND l.pagamento IS NOT NULL"
        campo = {"pagamento": "l.pagamento", "competencia": "l.competencia"}.get(por, "l.vencimento")
        if por == "competencia":
            if de:
                sql += " AND l.competencia>=?"
                args.append(str(de)[:7])
            if ate:
                sql += " AND l.competencia<=?"
                args.append(str(ate)[:7])
        else:
            if de:
                sql += f" AND {campo}>=?"
                args.append(_data(de, "de"))
            if ate:
                sql += f" AND {campo}<=?"
                args.append(_data(ate, "ate"))
        for col, val in (("categoria_id", categoria_id), ("centro_id", centro_id), ("conta_id", conta_id)):
            if val:
                sql += f" AND l.{col}=?"
                args.append(val)
        if origem:
            sql += " AND l.origem=?"
            args.append(origem)
        if q:
            sql += " AND (l.descricao LIKE ? OR l.pessoa_nome LIKE ? OR l.processo_numero LIKE ? OR l.pessoa_doc LIKE ?)"
            args += ["%" + q + "%"] * 4
        sql += f" ORDER BY {campo if por != 'competencia' else 'l.vencimento'} DESC, l.id DESC LIMIT ?"
        args.append(max(1, min(int(limite), 10000)))
        contas, cats, centros = _mapas(c)
        itens = [_linha(r, contas, cats, centros) for r in c.execute(sql, args)]
        soma = lambda xs: round(sum(x["valor"] for x in xs), 2)
        rec = [x for x in itens if x["tipo"] == "receita"]
        desp = [x for x in itens if x["tipo"] == "despesa"]
        return {"lancamentos": itens, "total": len(itens),
                "soma": {"receitas": soma(rec), "despesas": soma(desp), "saldo": round(soma(rec) - soma(desp), 2)}}
    finally:
        c.close()


@router.get("/resumo")
def resumo(mes: Optional[str] = None, user=Depends(requer_financeiro)):
    ""
    mes = (mes or date.today().isoformat()[:7])[:7]
    ini = mes + "-01"
    fim = (date.fromisoformat(ini).replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    hoje = date.today().isoformat()
    c = _con()
    try:
        _schema(c)
        def soma(where, args):
            r = c.execute("SELECT COUNT(*), COALESCE(SUM(l.valor_cent),0) FROM fin_lancamentos l "
                          "LEFT JOIN fin_categorias k ON k.id=l.categoria_id WHERE " + BASE_VIVOS +
                          " AND l.interno=0 AND COALESCE(k.grupo,'operacional')<>'transferencia' AND " + where, args).fetchone()
            return {"qtd": r[0], "total": round(r[1] / 100, 2)}
        return {
            "mes": mes,
            "receber_mes": soma("l.tipo='receita' AND l.vencimento BETWEEN ? AND ?", [ini, fim.isoformat()]),
            "pagar_mes": soma("l.tipo='despesa' AND l.vencimento BETWEEN ? AND ?", [ini, fim.isoformat()]),
            "recebido_mes": soma("l.tipo='receita' AND l.pagamento BETWEEN ? AND ?", [ini, fim.isoformat()]),
            "pago_mes": soma("l.tipo='despesa' AND l.pagamento BETWEEN ? AND ?", [ini, fim.isoformat()]),
            "receber_vencidas": soma("l.tipo='receita' AND l.pagamento IS NULL AND l.vencimento<?", [hoje]),
            "pagar_vencidas": soma("l.tipo='despesa' AND l.pagamento IS NULL AND l.vencimento<?", [hoje]),
            "vence_hoje": soma("l.pagamento IS NULL AND l.vencimento=?", [hoje]),
        }
    finally:
        c.close()


@router.get("/lancamentos/{lid}")
def ver(lid: int, user=Depends(requer_financeiro)):
    c = _con()
    try:
        _schema(c)
        r = c.execute("SELECT * FROM fin_lancamentos WHERE id=?", (lid,)).fetchone()
        if not r:
            raise HTTPException(404, "Lancamento nao encontrado")
        contas, cats, centros = _mapas(c)
        d = _linha(r, contas, cats, centros)
        d["historico"] = [dict(h) for h in c.execute("SELECT acao, detalhe, por, em FROM fin_historico "
                                                      "WHERE lancamento_id=? ORDER BY id DESC LIMIT 50", (lid,))]
        return d
    finally:
        c.close()


class LancIn(BaseModel):
    tipo: Optional[str] = None
    descricao: Optional[str] = None
    valor: Optional[str] = None
    vencimento: Optional[str] = None
    competencia: Optional[str] = None
    pagamento: Optional[str] = None
    conta_id: Optional[int] = None
    categoria_id: Optional[int] = None
    centro_id: Optional[int] = None
    pessoa_nome: Optional[str] = None
    pessoa_doc: Optional[str] = None
    processo_numero: Optional[str] = None
    responsavel: Optional[str] = None
    interno: Optional[bool] = None
    obs: Optional[str] = None
    parcelas: Optional[int] = None


EDITAVEIS = ("tipo", "descricao", "valor_cent", "vencimento", "competencia", "pagamento", "conta_id", "categoria_id",
             "centro_id", "pessoa_nome", "pessoa_doc", "processo_numero", "responsavel", "interno", "obs")


def _limpar(d, criando):
    out = {}
    if "tipo" in d and d["tipo"] is not None:
        if d["tipo"] not in ("receita", "despesa"):
            raise HTTPException(400, "tipo: receita ou despesa")
        out["tipo"] = d["tipo"]
    if "valor" in d and d["valor"] is not None:
        v = _cent(d["valor"])
        if v is None or v <= 0:
            raise HTTPException(400, "Valor tem que ser maior que zero")
        out["valor_cent"] = v
    if "vencimento" in d:
        out["vencimento"] = _data(d["vencimento"], "vencimento", obrig=criando)
    if "pagamento" in d:
        out["pagamento"] = _data(d["pagamento"], "pagamento")
    if "competencia" in d:
        comp = d["competencia"]
        if comp and not re.fullmatch(r"\d{4}-\d{2}", str(comp)[:7]):
            raise HTTPException(400, "competencia: AAAA-MM")
        out["competencia"] = str(comp)[:7] if comp else None
    for k in ("descricao", "pessoa_nome", "pessoa_doc", "processo_numero", "responsavel", "obs"):
        if k in d:
            out[k] = (str(d[k]).strip() or None) if d[k] is not None else None
    for k in ("conta_id", "categoria_id", "centro_id"):
        if k in d:
            out[k] = d[k] or None
    if "interno" in d and d["interno"] is not None:
        out["interno"] = 1 if d["interno"] else 0
    return out


def _somar_mes(iso, n):
    d = date.fromisoformat(iso)
    m = d.month - 1 + n
    a, m = d.year + m // 12, m % 12 + 1
    ult = (date(a, m, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return date(a, m, min(d.day, ult.day)).isoformat()


@router.post("/lancamentos")
def criar(dados: LancIn, user=Depends(requer_financeiro)):
    d = dados.model_dump(exclude_unset=True)
    v = _limpar(d, criando=True)
    for k in ("tipo", "valor_cent", "vencimento"):
        if k not in v:
            raise HTTPException(400, "Informe tipo, valor e vencimento")
    if not v.get("categoria_id"):
        raise HTTPException(400, "Escolha a categoria")
    parcelas = max(1, min(int(d.get("parcelas") or 1), 60))
    c = _con()
    try:
        _schema(c)
        ids = []
        for i in range(parcelas):
            x = dict(v)
            if i:
                x["vencimento"] = _somar_mes(v["vencimento"], i)
                x["pagamento"] = None
            x["competencia"] = x.get("competencia") or x["vencimento"][:7]
            if parcelas > 1:
                x["descricao"] = f"{v.get('descricao') or ''} ({i + 1}/{parcelas})".strip()
            x.update(origem="manual", criado_por=_nome(user), criado_em=_agora(), atualizado_em=_agora(),
                     atualizado_por=_nome(user))
            cur = c.execute(f"INSERT INTO fin_lancamentos ({', '.join(x)}) VALUES ({', '.join('?' for _ in x)})",
                            list(x.values()))
            ids.append(cur.lastrowid)
            _hist(c, cur.lastrowid, "criado", {k: x[k] for k in x if k in EDITAVEIS}, _nome(user))
        c.commit()
        return {"ok": True, "ids": ids}
    finally:
        c.close()


def _alterar(c, lid, campos, user, acao):
    r = c.execute("SELECT * FROM fin_lancamentos WHERE id=? AND excluido=0", (lid,)).fetchone()
    if not r:
        raise HTTPException(404, "Lancamento nao encontrado")
    antes = {k: r[k] for k in campos}
    campos = dict(campos)
    if r["origem"] == "advbox":
        campos["editado_local"] = 1
    campos.update(atualizado_por=_nome(user), atualizado_em=_agora())
    c.execute("UPDATE fin_lancamentos SET " + ", ".join(f"{k}=?" for k in campos) + " WHERE id=?",
              list(campos.values()) + [lid])
    _hist(c, lid, acao, {"antes": antes, "depois": {k: campos[k] for k in antes}}, _nome(user))


@router.patch("/lancamentos/{lid}")
def editar(lid: int, dados: LancIn, user=Depends(requer_financeiro)):
    v = _limpar(dados.model_dump(exclude_unset=True), criando=False)
    if not v:
        raise HTTPException(400, "Nada para mudar")
    c = _con()
    try:
        _schema(c)
        _alterar(c, lid, v, user, "editado")
        c.commit()
        return ver(lid, user)
    finally:
        c.close()


class BaixaIn(BaseModel):
    pagamento: Optional[str] = None
    conta_id: Optional[int] = None
    valor: Optional[str] = None


@router.post("/lancamentos/{lid}/baixa")
def baixar(lid: int, dados: BaixaIn, user=Depends(requer_financeiro)):
    campos = {"pagamento": _data(dados.pagamento, "pagamento") or date.today().isoformat()}
    if dados.conta_id:
        campos["conta_id"] = dados.conta_id
    if dados.valor:
        campos["valor_cent"] = _cent(dados.valor)
    c = _con()
    try:
        _schema(c)
        _alterar(c, lid, campos, user, "baixa")
        c.commit()
        return ver(lid, user)
    finally:
        c.close()


@router.post("/lancamentos/{lid}/estornar")
def estornar(lid: int, user=Depends(requer_financeiro)):
    ""
    c = _con()
    try:
        _schema(c)
        _alterar(c, lid, {"pagamento": None}, user, "estorno")
        c.commit()
        return ver(lid, user)
    finally:
        c.close()


@router.delete("/lancamentos/{lid}")
def excluir(lid: int, user=Depends(requer_financeiro)):
    ""
    c = _con()
    try:
        _schema(c)
        r = c.execute("SELECT id FROM fin_lancamentos WHERE id=? AND excluido=0", (lid,)).fetchone()
        if not r:
            raise HTTPException(404, "Lancamento nao encontrado")
        c.execute("UPDATE fin_lancamentos SET excluido=1, excluido_por=?, excluido_em=?, editado_local=1 WHERE id=?",
                  (_nome(user), _agora(), lid))
        _hist(c, lid, "excluido", None, _nome(user))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


class CadIn(BaseModel):
    nome: str
    natureza: Optional[str] = None
    grupo: Optional[str] = None
    instituicao: Optional[str] = None


@router.post("/cadastros/{tipo}")
def novo_cadastro(tipo: str, dados: CadIn, user=Depends(requer_financeiro)):
    tabela = {"conta": "fin_contas", "categoria": "fin_categorias", "centro": "fin_centros"}.get(tipo)
    if not tabela or not dados.nome.strip():
        raise HTTPException(400, "Informe o nome")
    extra = {}
    if tabela == "fin_categorias":
        if dados.natureza not in ("receita", "despesa"):
            raise HTTPException(400, "natureza: receita ou despesa")
        extra = {"natureza": dados.natureza, "grupo": dados.grupo if dados.grupo in GRUPOS else "operacional"}
    if tabela == "fin_contas" and dados.instituicao:
        extra = {"instituicao": dados.instituicao}
    c = _con()
    try:
        _schema(c)
        if c.execute(f"SELECT 1 FROM {tabela} WHERE nome=?", (dados.nome.strip().upper(),)).fetchone():
            raise HTTPException(409, "Ja existe com esse nome")
        i = _id_por_nome(c, tabela, dados.nome.strip().upper(), extra)
        c.commit()
        return {"ok": True, "id": i}
    finally:
        c.close()


class GrupoIn(BaseModel):
    grupo: str


@router.patch("/categorias/{cid}")
def grupo_categoria(cid: int, dados: GrupoIn, user=Depends(requer_financeiro)):
    if dados.grupo not in GRUPOS:
        raise HTTPException(400, "grupo invalido")
    c = _con()
    try:
        _schema(c)
        c.execute("UPDATE fin_categorias SET grupo=? WHERE id=?", (dados.grupo, cid))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.post("/sincronizar")
def sincronizar(user=Depends(requer_financeiro)):
    ""
    try:
        return importar_advbox(por=_nome(user))
    except Exception as e:
        raise HTTPException(502, f"Nao consegui ler o ADVBOX: {str(e)[:200]}")
