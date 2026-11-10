"""
CLIENTES E PROCESSOS - cadastro proprio (28/09/2026, pedido do Titular: "migrar logo do ADVBOX").

O Sistema Escritório Modelo passa a ser o dono de clientes (jur_clientes) e processos
(jur_processos + jur_processo_partes). Antes o ADVBOX mandava: a contratacao
criava la e o confronto das 5h copiava para ca.

  - Toda a equipe cria, edita e move processo de fase (decisao do Titular).
    Tudo vai para jur_historico (quem, quando, de onde para onde).
  - Mexeu aqui -> editado_local=1: o confronto das 5h NAO sobrescreve mais.
  - Fases e etapas: catalogo do ADVBOX (96 fases em 9 etapas) copiado para
    jur_fases na primeira abertura; da para criar fase nova aqui.
  - NOMES (herdados do ADVBOX, cuidado): em jur_processos, `etapa` e o GRUPO
    (JUDICIAL, NEGOCIACAO...) e `fase` e a coluna do quadro (AGUARDANDO AUDIENCIA...).
  - jur_processo_partes.papel: 'PARTE CONTRÁRIA' marca o outro lado; o resto
    (origem do cliente, vazio) e cliente nosso.
Nada aqui escreve no ADVBOX.
"""
import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import usuario_atual

router = APIRouter(prefix="/api/cadastro", tags=["cadastro"])

_DB = str(Path(__file__).resolve().parents[2] / "sistema_esc.db")
CONTRARIA = "PARTE CONTRÁRIA"
ENCERRA = ("ARQUIV", "ENCERR", "BAIX", "EXTINT", "TRANSITO EM JULGADO/PROCESSO FINALIZADO")


# As tabelas jur_* vieram do modelo SQLAlchemy com quase tudo NOT NULL e sem
# default: texto vazio vai como "" e valor vazio como 0 (nunca NULL).
CLI_TXT = ("nome", "documento", "email", "telefone", "celular", "origem", "nascimento", "estado_civil",
           "profissao", "logradouro", "cidade", "estado", "cep", "notas")
PROC_TXT = ("numero", "protocolo", "area", "tipo", "fase", "etapa", "responsavel", "status_encerramento",
            "data_processo", "pasta", "notas")
PROC_NUM = ("valor_expectativa", "valor_honorarios", "contingencia")


def _sem_nulo(d, txt, num=()):
    for k in list(d):
        if d[k] is None and k in txt:
            d[k] = ""
        elif d[k] is None and k in num:
            d[k] = 0.0
    return d


def _con():
    c = sqlite3.connect(_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _agora_txt():
    # hora local da VPS (Sao Paulo), no mesmo formato do confronto das 5h
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _nome(user):
    return getattr(user, "nome", None) or getattr(user, "email", None) or "sistema"


DDL = [
    """CREATE TABLE IF NOT EXISTS jur_fases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, etapa TEXT NOT NULL, fase TEXT NOT NULL,
        ordem INTEGER DEFAULT 0, ativo INTEGER DEFAULT 1, advbox_id INTEGER,
        UNIQUE (etapa, fase))""",
    """CREATE TABLE IF NOT EXISTS jur_tipos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE, advbox_id INTEGER)""",
    """CREATE TABLE IF NOT EXISTS jur_origens (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE)""",
    """CREATE TABLE IF NOT EXISTS jur_historico (
        id INTEGER PRIMARY KEY AUTOINCREMENT, entidade TEXT NOT NULL, entidade_id INTEGER NOT NULL,
        acao TEXT, detalhe TEXT, por TEXT, em TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_jur_hist ON jur_historico (entidade, entidade_id)",
]
COLS_EXTRA = {
    "jur_processos": {"editado_local": "INTEGER DEFAULT 0", "fase_desde": "TEXT", "criado_por": "TEXT"},
    "jur_clientes": {"editado_local": "INTEGER DEFAULT 0", "rg": "TEXT", "bairro": "TEXT", "criado_por": "TEXT"},
}


def _schema(c):
    for d in DDL:
        c.execute(d)
    for tab, cols in COLS_EXTRA.items():
        tem = {r[1] for r in c.execute(f"PRAGMA table_info({tab})")}
        for col, tipo in cols.items():
            if col not in tem:
                c.execute(f"ALTER TABLE {tab} ADD COLUMN {col} {tipo}")
    if not c.execute("SELECT COUNT(*) FROM jur_fases").fetchone()[0]:
        _semear(c)
        c.commit()


def _semear(c):
    """Catalogo do ADVBOX (so leitura) + o que ja existe nos processos e nao esta nele."""
    try:
        from .fin import _advbox_get
        s = _advbox_get("/settings")
        for i, x in enumerate(s.get("stages") or []):
            c.execute("INSERT OR IGNORE INTO jur_fases (etapa, fase, ordem, advbox_id) VALUES (?,?,?,?)",
                      ((x.get("step") or "").strip().upper(), (x.get("stage") or "").strip().upper(), i, x.get("id")))
        for t in s.get("lawsuit_types") or []:
            nome = (t.get("lawsuit") or t.get("type") or "").strip().upper()
            if nome:
                c.execute("INSERT OR IGNORE INTO jur_tipos (nome, advbox_id) VALUES (?,?)", (nome, t.get("id")))
        for o in s.get("origins") or []:
            nome = (o.get("origin") or "").strip().upper()
            if nome:
                c.execute("INSERT OR IGNORE INTO jur_origens (nome) VALUES (?)", (nome,))
    except Exception:
        pass                                   # sem ADVBOX: semeia so do que ja existe
    n = c.execute("SELECT COALESCE(MAX(ordem),0) FROM jur_fases").fetchone()[0]
    for r in c.execute("SELECT DISTINCT UPPER(TRIM(etapa)), UPPER(TRIM(fase)) FROM jur_processos "
                       "WHERE COALESCE(TRIM(fase),'')<>''").fetchall():
        n += 1
        c.execute("INSERT OR IGNORE INTO jur_fases (etapa, fase, ordem) VALUES (?,?,?)", (r[0] or "SEM ETAPA", r[1], n))
    for r in c.execute("SELECT DISTINCT UPPER(TRIM(tipo)) FROM jur_processos WHERE COALESCE(TRIM(tipo),'')<>''").fetchall():
        c.execute("INSERT OR IGNORE INTO jur_tipos (nome) VALUES (?)", (r[0],))


def _hist(c, ent, eid, acao, detalhe, user):
    c.execute("INSERT INTO jur_historico (entidade, entidade_id, acao, detalhe, por, em) VALUES (?,?,?,?,?,?)",
              (ent, eid, acao, json.dumps(detalhe, ensure_ascii=False, default=str)[:4000] if detalhe else None,
               _nome(user), _agora_txt()))


def _dias(desde):
    if not desde:
        return None
    try:
        return max(0, (datetime.now() - datetime.fromisoformat(str(desde)[:19])).days)
    except ValueError:
        return None


def _encerra(etapa, fase):
    t = f"{etapa or ''} {fase or ''}".upper()
    return any(x in t for x in ENCERRA)


# ------------------------------------------------------------------ catalogo
@router.get("/catalogo")
def catalogo(user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        etapas, vistas = [], {}
        for r in c.execute("SELECT * FROM jur_fases WHERE ativo=1 ORDER BY ordem, id"):
            if r["etapa"] not in vistas:
                vistas[r["etapa"]] = {"etapa": r["etapa"], "fases": []}
                etapas.append(vistas[r["etapa"]])
            vistas[r["etapa"]]["fases"].append({"id": r["id"], "fase": r["fase"]})
        cont = {}
        for r in c.execute("SELECT UPPER(TRIM(etapa)) e, COUNT(*) n FROM jur_processos WHERE COALESCE(encerrado,0)=0 GROUP BY 1"):
            cont[r["e"]] = r["n"]
        for e in etapas:
            e["ativos"] = cont.get(e["etapa"], 0)
        resp = sorted({(r[0] or "").strip() for r in c.execute("SELECT DISTINCT responsavel FROM jur_processos") if (r[0] or "").strip()}
                      | {(r[0] or "").strip().upper() for r in c.execute("SELECT nome FROM usuarios WHERE ativo=1 AND email NOT LIKE 'alicia@%' "
                                                                          "AND email NOT LIKE 'claudia@%'")})
        return {"etapas": etapas,
                "tipos": [r[0] for r in c.execute("SELECT nome FROM jur_tipos ORDER BY nome")],
                "origens": [r[0] for r in c.execute("SELECT nome FROM jur_origens ORDER BY nome")],
                "areas": [r[0] for r in c.execute("SELECT DISTINCT area FROM jur_processos WHERE COALESCE(area,'')<>'' ORDER BY 1")],
                "responsaveis": resp}
    finally:
        c.close()


class FaseNovaIn(BaseModel):
    etapa: str
    fase: str


@router.post("/catalogo/fase")
def fase_nova(dados: FaseNovaIn, user=Depends(usuario_atual)):
    etapa, fase = dados.etapa.strip().upper(), dados.fase.strip().upper()
    if not etapa or not fase:
        raise HTTPException(400, "Informe a etapa e o nome da fase")
    c = _con()
    try:
        _schema(c)
        ordem = (c.execute("SELECT MAX(ordem) FROM jur_fases WHERE etapa=?", (etapa,)).fetchone()[0] or 0) + 1
        c.execute("INSERT OR IGNORE INTO jur_fases (etapa, fase, ordem) VALUES (?,?,?)", (etapa, fase, ordem))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


# ------------------------------------------------------------------ processos
SQL_PROC = """
SELECT p.*,
  (SELECT group_concat(pp.nome, ' / ') FROM jur_processo_partes pp WHERE pp.processo_id=p.id
      AND COALESCE(pp.papel,'')<>'PARTE CONTRÁRIA') AS clientes,
  (SELECT group_concat(pp.nome, ' / ') FROM jur_processo_partes pp WHERE pp.processo_id=p.id
      AND pp.papel='PARTE CONTRÁRIA') AS adversos,
  (SELECT pp.cliente_id FROM jur_processo_partes pp WHERE pp.processo_id=p.id
      AND COALESCE(pp.papel,'')<>'PARTE CONTRÁRIA' AND pp.cliente_id IS NOT NULL ORDER BY pp.id LIMIT 1) AS cliente_id
FROM jur_processos p
"""


def _proc(r):
    d = dict(r)
    d["dias_na_fase"] = _dias(d.get("fase_desde") or d.get("atualizado_em") or d.get("criado_em"))
    d["pasta_link"] = None
    m = re.search(r"PASTA DO CASO:\s*(https?://\S+)", d.get("notas") or "")
    if m:
        d["pasta_link"] = m.group(1)
    return d


@router.get("/processos")
def processos(q: Optional[str] = None, etapa: Optional[str] = None, fase: Optional[str] = None,
              responsavel: Optional[str] = None, situacao: str = "ativos", area: Optional[str] = None,
              limite: int = 3000, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        sql, args = SQL_PROC + " WHERE 1=1", []
        if situacao == "ativos":
            sql += " AND COALESCE(p.encerrado,0)=0"
        elif situacao == "encerrados":
            sql += " AND COALESCE(p.encerrado,0)=1"
        for col, val in (("etapa", etapa), ("fase", fase), ("responsavel", responsavel), ("area", area)):
            if val:
                sql += f" AND UPPER(TRIM(p.{col}))=UPPER(TRIM(?))"
                args.append(val)
        if q:
            sql += (" AND (p.numero LIKE ? OR p.tipo LIKE ? OR p.notas LIKE ? OR p.pasta LIKE ? OR EXISTS (SELECT 1 FROM "
                    "jur_processo_partes pp WHERE pp.processo_id=p.id AND (pp.nome LIKE ? OR pp.documento LIKE ?)))")
            args += ["%" + q + "%"] * 6
        sql += " ORDER BY COALESCE(p.fase_desde, p.atualizado_em, p.criado_em) DESC LIMIT ?"
        args.append(max(1, min(int(limite), 5000)))
        itens = [_proc(r) for r in c.execute(sql, args)]
        return {"processos": itens, "total": len(itens)}
    finally:
        c.close()


@router.get("/processos/{pid}")
def processo(pid: int, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        r = c.execute(SQL_PROC + " WHERE p.id=?", (pid,)).fetchone()
        if not r:
            raise HTTPException(404, "Processo nao encontrado")
        d = _proc(r)
        d["partes"] = [dict(x) for x in c.execute(
            "SELECT pp.*, cl.telefone, cl.celular, cl.email FROM jur_processo_partes pp LEFT JOIN jur_clientes cl "
            "ON cl.id=pp.cliente_id WHERE pp.processo_id=? ORDER BY pp.papel='PARTE CONTRÁRIA', pp.id", (pid,))]
        d["historico"] = [dict(x) for x in c.execute(
            "SELECT acao, detalhe, por, em FROM jur_historico WHERE entidade='processo' AND entidade_id=? "
            "ORDER BY id DESC LIMIT 60", (pid,))]
        return d
    finally:
        c.close()


class ProcIn(BaseModel):
    numero: Optional[str] = None
    protocolo: Optional[str] = None
    tipo: Optional[str] = None
    area: Optional[str] = None
    etapa: Optional[str] = None
    fase: Optional[str] = None
    responsavel: Optional[str] = None
    encerrado: Optional[bool] = None
    pasta: Optional[str] = None
    notas: Optional[str] = None
    valor_expectativa: Optional[float] = None
    valor_honorarios: Optional[float] = None
    contingencia: Optional[float] = None
    data_processo: Optional[str] = None
    # so na criacao
    cliente_id: Optional[int] = None
    cliente_nome: Optional[str] = None
    cliente_documento: Optional[str] = None
    parte_contraria: Optional[str] = None


EDIT_PROC = ("numero", "protocolo", "tipo", "area", "etapa", "fase", "responsavel", "encerrado", "pasta", "notas",
             "valor_expectativa", "valor_honorarios", "contingencia", "data_processo")


def _etapa_da_fase(c, fase):
    r = c.execute("SELECT etapa FROM jur_fases WHERE fase=? AND ativo=1 ORDER BY ordem LIMIT 1", (fase,)).fetchone()
    return r["etapa"] if r else None


def _limpar_proc(c, campos):
    out = {}
    for k, v in campos.items():
        if k not in EDIT_PROC:
            continue
        if isinstance(v, str):
            v = v.strip() or None
            if v and k in ("tipo", "area", "etapa", "fase", "responsavel"):
                v = v.upper()
        out[k] = v
    if out.get("fase") and not out.get("etapa"):
        out["etapa"] = _etapa_da_fase(c, out["fase"])
    if "encerrado" in out and out["encerrado"] is not None:
        out["encerrado"] = 1 if out["encerrado"] else 0
    elif out.get("fase") or out.get("etapa"):
        out["encerrado"] = 1 if _encerra(out.get("etapa"), out.get("fase")) else 0
    return out


def _cliente_de(c, cid=None, nome=None, doc=None, user=None):
    """Cliente existente (id ou CPF) ou novo. Devolve a linha."""
    if cid:
        r = c.execute("SELECT * FROM jur_clientes WHERE id=?", (cid,)).fetchone()
        if not r:
            raise HTTPException(404, "Cliente nao encontrado")
        return r
    doc_d = re.sub(r"\D", "", doc or "")
    if doc_d:
        for r in c.execute("SELECT * FROM jur_clientes WHERE documento IS NOT NULL"):
            if re.sub(r"\D", "", r["documento"] or "") == doc_d:
                return r
    if not (nome or "").strip():
        raise HTTPException(400, "Informe o cliente (escolha um ou digite o nome)")
    agora = _agora_txt()
    novo = {k: "" for k in CLI_TXT}
    novo.update(nome=nome.strip().upper(), documento=_fmt_doc(doc) or "", criado_em=agora, atualizado_em=agora,
                editado_local=1, criado_por=_nome(user))
    cur = c.execute(f"INSERT INTO jur_clientes ({', '.join(novo)}) VALUES ({', '.join('?' for _ in novo)})", list(novo.values()))
    _hist(c, "cliente", cur.lastrowid, "criado", {"nome": nome, "documento": doc}, user)
    return c.execute("SELECT * FROM jur_clientes WHERE id=?", (cur.lastrowid,)).fetchone()


def _fmt_doc(doc):
    d = re.sub(r"\D", "", doc or "")
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return (doc or "").strip() or None


@router.post("/processos")
def processo_novo(dados: ProcIn, user=Depends(usuario_atual)):
    d = dados.model_dump(exclude_unset=True)
    c = _con()
    try:
        _schema(c)
        v = _limpar_proc(c, d)
        if not v.get("fase"):
            raise HTTPException(400, "Escolha a fase")
        if v.get("numero") and c.execute("SELECT 1 FROM jur_processos WHERE TRIM(numero)=?", (v["numero"],)).fetchone():
            raise HTTPException(409, "Ja existe processo com esse numero")
        cli = _cliente_de(c, d.get("cliente_id"), d.get("cliente_nome"), d.get("cliente_documento"), user)
        agora = _agora_txt()
        linha = {k: "" for k in PROC_TXT}
        linha.update({k: 0.0 for k in PROC_NUM})
        linha.update(_sem_nulo(dict(v), PROC_TXT, PROC_NUM))
        linha.update(criado_em=agora, atualizado_em=agora, fase_desde=agora, editado_local=1, criado_por=_nome(user))
        linha.setdefault("encerrado", 0)
        if linha.get("encerrado") is None:
            linha["encerrado"] = 0
        cur = c.execute(f"INSERT INTO jur_processos ({', '.join(linha)}) VALUES ({', '.join('?' for _ in linha)})",
                        list(linha.values()))
        pid = cur.lastrowid
        c.execute("INSERT INTO jur_processo_partes (processo_id, cliente_id, advbox_customer_id, nome, documento, papel) "
                  "VALUES (?,?,?,?,?,?)", (pid, cli["id"], cli["advbox_id"], cli["nome"], cli["documento"] or "", cli["origem"] or ""))
        if (d.get("parte_contraria") or "").strip():
            c.execute("INSERT INTO jur_processo_partes (processo_id, nome, documento, papel) VALUES (?,?,?,?)",
                      (pid, d["parte_contraria"].strip().upper(), "", CONTRARIA))
        _hist(c, "processo", pid, "criado", {k: v[k] for k in v if k in EDIT_PROC}, user)
        c.commit()
        return processo(pid, user)
    finally:
        c.close()


@router.patch("/processos/{pid}")
def processo_editar(pid: int, dados: ProcIn, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        r = c.execute("SELECT * FROM jur_processos WHERE id=?", (pid,)).fetchone()
        if not r:
            raise HTTPException(404, "Processo nao encontrado")
        v = _sem_nulo(_limpar_proc(c, dados.model_dump(exclude_unset=True)), PROC_TXT, PROC_NUM)
        if not v:
            raise HTTPException(400, "Nada para mudar")
        antes = {k: r[k] for k in v}
        mudou = {k: v[k] for k in v if (r[k] if not isinstance(r[k], str) else r[k].strip()) != v[k]}
        if not mudou:
            return processo(pid, user)
        if "fase" in mudou:
            mudou["fase_desde"] = _agora_txt()
        mudou.update(atualizado_em=_agora_txt(), editado_local=1)
        c.execute("UPDATE jur_processos SET " + ", ".join(f"{k}=?" for k in mudou) + " WHERE id=?", list(mudou.values()) + [pid])
        acao = "moveu de fase" if "fase" in mudou else ("encerrado" if mudou.get("encerrado") == 1 else "editado")
        _hist(c, "processo", pid, acao, {"antes": {k: antes.get(k) for k in mudou if k in antes},
                                         "depois": {k: mudou[k] for k in mudou if k in antes}}, user)
        c.commit()
        return processo(pid, user)
    finally:
        c.close()


class ParteIn(BaseModel):
    cliente_id: Optional[int] = None
    nome: Optional[str] = None
    documento: Optional[str] = None
    contraria: bool = False


@router.post("/processos/{pid}/partes")
def parte_nova(pid: int, dados: ParteIn, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        if not c.execute("SELECT 1 FROM jur_processos WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "Processo nao encontrado")
        if dados.contraria:
            if not (dados.nome or "").strip():
                raise HTTPException(400, "Informe o nome da parte contraria")
            c.execute("INSERT INTO jur_processo_partes (processo_id, nome, documento, papel) VALUES (?,?,?,?)",
                      (pid, dados.nome.strip().upper(), _fmt_doc(dados.documento) or "", CONTRARIA))
            det = {"parte_contraria": dados.nome}
        else:
            cli = _cliente_de(c, dados.cliente_id, dados.nome, dados.documento, user)
            if c.execute("SELECT 1 FROM jur_processo_partes WHERE processo_id=? AND cliente_id=?", (pid, cli["id"])).fetchone():
                raise HTTPException(409, "Esse cliente ja esta no processo")
            c.execute("INSERT INTO jur_processo_partes (processo_id, cliente_id, advbox_customer_id, nome, documento, papel) "
                      "VALUES (?,?,?,?,?,?)", (pid, cli["id"], cli["advbox_id"], cli["nome"], cli["documento"] or "", cli["origem"] or ""))
            det = {"cliente": cli["nome"]}
        c.execute("UPDATE jur_processos SET editado_local=1, atualizado_em=? WHERE id=?", (_agora_txt(), pid))
        _hist(c, "processo", pid, "parte incluida", det, user)
        c.commit()
        return processo(pid, user)
    finally:
        c.close()


@router.delete("/processos/{pid}/partes/{parte_id}")
def parte_tirar(pid: int, parte_id: int, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        r = c.execute("SELECT * FROM jur_processo_partes WHERE id=? AND processo_id=?", (parte_id, pid)).fetchone()
        if not r:
            raise HTTPException(404, "Parte nao encontrada")
        c.execute("DELETE FROM jur_processo_partes WHERE id=?", (parte_id,))
        c.execute("UPDATE jur_processos SET editado_local=1, atualizado_em=? WHERE id=?", (_agora_txt(), pid))
        _hist(c, "processo", pid, "parte retirada", {"nome": r["nome"], "papel": r["papel"]}, user)
        c.commit()
        return processo(pid, user)
    finally:
        c.close()


# ------------------------------------------------------------------ clientes
@router.get("/clientes")
def clientes(q: Optional[str] = None, limite: int = 300, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        sql, args = ("SELECT cl.*, (SELECT COUNT(*) FROM jur_processo_partes pp JOIN jur_processos p ON p.id=pp.processo_id "
                     "WHERE pp.cliente_id=cl.id AND COALESCE(pp.papel,'')<>'PARTE CONTRÁRIA' AND COALESCE(p.encerrado,0)=0) AS ativos, "
                     "(SELECT COUNT(*) FROM jur_processo_partes pp WHERE pp.cliente_id=cl.id "
                     "AND COALESCE(pp.papel,'')<>'PARTE CONTRÁRIA') AS processos FROM jur_clientes cl WHERE 1=1"), []
        if q:
            dig = re.sub(r"\D", "", q)
            sql += " AND (cl.nome LIKE ? OR cl.email LIKE ?" + (" OR REPLACE(REPLACE(REPLACE(cl.documento,'.',''),'-',''),'/','') LIKE ? "
                                                              "OR REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(cl.celular,cl.telefone),' ',''),'-',''),'(',''),')','') LIKE ?"
                                                              if len(dig) >= 4 else "") + ")"
            args += ["%" + q + "%", "%" + q + "%"] + (["%" + dig + "%", "%" + dig + "%"] if len(dig) >= 4 else [])
        sql += " ORDER BY ativos DESC, cl.nome COLLATE NOCASE LIMIT ?"
        args.append(max(1, min(int(limite), 2000)))
        return {"clientes": [dict(r) for r in c.execute(sql, args)]}
    finally:
        c.close()


@router.get("/clientes/{cid}")
def cliente(cid: int, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        r = c.execute("SELECT * FROM jur_clientes WHERE id=?", (cid,)).fetchone()
        if not r:
            raise HTTPException(404, "Cliente nao encontrado")
        d = dict(r)
        d["processos"] = [_proc(x) for x in c.execute(
            SQL_PROC + " WHERE p.id IN (SELECT processo_id FROM jur_processo_partes WHERE cliente_id=? "
            "AND COALESCE(papel,'')<>'PARTE CONTRÁRIA') ORDER BY COALESCE(p.encerrado,0), p.id DESC", (cid,))]
        d["historico"] = [dict(x) for x in c.execute(
            "SELECT acao, detalhe, por, em FROM jur_historico WHERE entidade='cliente' AND entidade_id=? ORDER BY id DESC LIMIT 40", (cid,))]
        return d
    finally:
        c.close()


class ClienteIn(BaseModel):
    nome: Optional[str] = None
    documento: Optional[str] = None
    rg: Optional[str] = None
    email: Optional[str] = None
    telefone: Optional[str] = None
    celular: Optional[str] = None
    origem: Optional[str] = None
    nascimento: Optional[str] = None
    estado_civil: Optional[str] = None
    profissao: Optional[str] = None
    logradouro: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    estado: Optional[str] = None
    cep: Optional[str] = None
    notas: Optional[str] = None


EDIT_CLI = tuple(ClienteIn.model_fields.keys())


@router.post("/clientes")
def cliente_novo(dados: ClienteIn, user=Depends(usuario_atual)):
    d = {k: (v.strip() if isinstance(v, str) else v) for k, v in dados.model_dump(exclude_unset=True).items()}
    if not (d.get("nome") or ""):
        raise HTTPException(400, "Informe o nome")
    c = _con()
    try:
        _schema(c)
        dig = re.sub(r"\D", "", d.get("documento") or "")
        if dig:
            for r in c.execute("SELECT id, nome, documento FROM jur_clientes WHERE documento IS NOT NULL"):
                if re.sub(r"\D", "", r["documento"] or "") == dig:
                    raise HTTPException(409, f"Ja existe cliente com esse CPF/CNPJ: {r['nome']} (#{r['id']})")
        d["nome"] = d["nome"].upper()
        d["documento"] = _fmt_doc(d.get("documento")) or ""
        agora = _agora_txt()
        d = dict({k: "" for k in CLI_TXT}, **_sem_nulo(d, CLI_TXT))
        d.update(criado_em=agora, atualizado_em=agora, editado_local=1, criado_por=_nome(user))
        cur = c.execute(f"INSERT INTO jur_clientes ({', '.join(d)}) VALUES ({', '.join('?' for _ in d)})", list(d.values()))
        _hist(c, "cliente", cur.lastrowid, "criado", {k: d[k] for k in d if k in EDIT_CLI}, user)
        c.commit()
        return cliente(cur.lastrowid, user)
    finally:
        c.close()


@router.patch("/clientes/{cid}")
def cliente_editar(cid: int, dados: ClienteIn, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        r = c.execute("SELECT * FROM jur_clientes WHERE id=?", (cid,)).fetchone()
        if not r:
            raise HTTPException(404, "Cliente nao encontrado")
        d = {k: ((v.strip() or None) if isinstance(v, str) else v) for k, v in dados.model_dump(exclude_unset=True).items()}
        if "nome" in d:
            if not d["nome"]:
                raise HTTPException(400, "O nome nao pode ficar vazio")
            d["nome"] = d["nome"].upper()
        if "documento" in d:
            d["documento"] = _fmt_doc(d["documento"])
        d = _sem_nulo(d, CLI_TXT)
        mudou = {k: v for k, v in d.items() if r[k] != v}
        if not mudou:
            return cliente(cid, user)
        antes = {k: r[k] for k in mudou}
        mudou.update(atualizado_em=_agora_txt(), editado_local=1)
        c.execute("UPDATE jur_clientes SET " + ", ".join(f"{k}=?" for k in mudou) + " WHERE id=?", list(mudou.values()) + [cid])
        if "nome" in mudou or "documento" in mudou:          # a parte leva o nome/CPF novo
            c.execute("UPDATE jur_processo_partes SET nome=COALESCE(?, nome), documento=COALESCE(?, documento) WHERE cliente_id=?",
                      (mudou.get("nome"), mudou.get("documento"), cid))
        _hist(c, "cliente", cid, "editado", {"antes": antes, "depois": {k: mudou[k] for k in antes}}, user)
        c.commit()
        return cliente(cid, user)
    finally:
        c.close()


@router.get("/resumo")
def resumo(user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        mes = datetime.now().strftime("%Y-%m")
        ativos = [dict(r) for r in c.execute("SELECT etapa, fase, fase_desde, atualizado_em, criado_em FROM jur_processos "
                                             "WHERE COALESCE(encerrado,0)=0")]
        parados = sum(1 for a in ativos if (_dias(a["fase_desde"] or a["atualizado_em"] or a["criado_em"]) or 0) > 30)
        return {"ativos": len(ativos), "parados_30d": parados,
                "novos_mes": c.execute("SELECT COUNT(*) FROM jur_processos WHERE substr(criado_em,1,7)=?", (mes,)).fetchone()[0],
                "clientes": c.execute("SELECT COUNT(*) FROM jur_clientes").fetchone()[0],
                "movidos_mes": c.execute("SELECT COUNT(*) FROM jur_historico WHERE entidade='processo' AND acao='moveu de fase' "
                                         "AND substr(em,1,7)=?", (mes,)).fetchone()[0]}
    finally:
        c.close()
