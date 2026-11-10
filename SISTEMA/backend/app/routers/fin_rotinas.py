""
import hashlib
import io
import json
import os
import re
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..deps import requer_financeiro
from .fin import (BASE_VIVOS, _agora, _con, _hist, _id_por_nome, _mapas, _linha, _nome, _schema, PROJ)

router = APIRouter(prefix="/api/fin", tags=["financeiro-proprio"])



def _lista_env(nome):
    v = os.getenv(nome, "")
    return tuple(x.strip().upper() for x in v.split(",") if x.strip())


def _pct_env(nome):
    try:
        return max(0.0, float((os.getenv(nome) or "0").replace(",", "."))) / 100.0
    except ValueError:
        return 0.0


EXCLUIR_FATURAMENTO = _lista_env("FIN_EXCLUIR_FATURAMENTO")
EXCLUIR_CA_SEMPRE = _lista_env("FIN_COM_A_EXCLUIR")
EXCLUIR_CB = _lista_env("FIN_COM_B_EXCLUIR")
PCT_CA = _pct_env("FIN_COM_A_PCT")
PCT_CA_EXTRA = _pct_env("FIN_COM_A_EXTRA_PCT")
PCT_CB = _pct_env("FIN_COM_B_PCT")
NOME_CA = (os.getenv("FIN_COM_A_NOME") or "Comissionado A").strip()
NOME_CB = (os.getenv("FIN_COM_B_NOME") or "Comissionado B").strip()
try:
    DIA_VENC_COMISSAO = min(28, max(1, int(os.getenv("FIN_COM_VENCIMENTO_DIA") or 5)))
except ValueError:
    DIA_VENC_COMISSAO = 5
MESES = ["JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO", "SETEMBRO",
         "OUTUBRO", "NOVEMBRO", "DEZEMBRO"]
HIST_PLANILHA = os.getenv("FIN_HIST_PLANILHA_ID", "")
ASAAS_API = "https://api.asaas.com/v3"

DDL = [
    """CREATE TABLE IF NOT EXISTS fin_comissao_hist (
        id INTEGER PRIMARY KEY AUTOINCREMENT, parceiro TEXT NOT NULL, mes TEXT NOT NULL, cliente TEXT NOT NULL,
        sufixo INTEGER DEFAULT 0, valor_cent INTEGER, fonte TEXT,
        UNIQUE (parceiro, mes, cliente, valor_cent, fonte))""",
    """CREATE TABLE IF NOT EXISTS fin_comissao_relacao (
        id INTEGER PRIMARY KEY AUTOINCREMENT, parceiro TEXT NOT NULL, cliente TEXT NOT NULL, fonte TEXT,
        criado_em REAL, UNIQUE (parceiro, cliente))""",
    """CREATE TABLE IF NOT EXISTS fin_comissao_ajuste (
        mes TEXT NOT NULL, asaas_id TEXT NOT NULL, classe TEXT, ca_cent INTEGER, cb_cent INTEGER,
        por TEXT, em REAL, PRIMARY KEY (mes, asaas_id))""",
    """CREATE TABLE IF NOT EXISTS fin_comissao_extra (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mes TEXT NOT NULL, data TEXT, descricao TEXT NOT NULL,
        valor_cent INTEGER, ca_cent INTEGER DEFAULT 0, cb_cent INTEGER DEFAULT 0, por TEXT, em REAL)""",
    """CREATE TABLE IF NOT EXISTS fin_comissao_mes (
        mes TEXT PRIMARY KEY, ca_cent INTEGER, cb_cent INTEGER, itens TEXT,
        lancado_por TEXT, lancado_em REAL)""",
    """CREATE TABLE IF NOT EXISTS fin_extrato (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fonte TEXT NOT NULL, ref TEXT NOT NULL UNIQUE,
        data TEXT NOT NULL, valor_cent INTEGER NOT NULL, descricao TEXT, tipo_externo TEXT,
        lancamento_id INTEGER, status TEXT DEFAULT 'pendente', nota TEXT,
        importado_em REAL, conciliado_por TEXT, conciliado_em REAL)""",
    "CREATE INDEX IF NOT EXISTS ix_fin_extrato ON fin_extrato (fonte, data)",
]


def _schema2(c):
    _schema(c)
    for d in DDL:
        c.execute(d)
    cols = {r[1] for r in c.execute("PRAGMA table_info(fin_comissao_ajuste)")}
    for col in ("ca_cent", "cb_cent"):
        if col not in cols:
            c.execute(f"ALTER TABLE fin_comissao_ajuste ADD COLUMN {col} INTEGER")


def _mes_ok(mes):
    mes = (mes or date.today().isoformat()[:7])[:7]
    if not re.fullmatch(r"\d{4}-\d{2}", mes):
        raise HTTPException(400, "mes: AAAA-MM")
    return mes


def _limites(mes):
    ini = date.fromisoformat(mes + "-01")
    fim = (ini.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return ini.isoformat(), fim.isoformat()


def _mes_mais(mes, n):
    a, m = int(mes[:4]), int(mes[5:7]) - 1 + n
    return "%04d-%02d" % (a + m // 12, m % 12 + 1)


def _cent(v):
    return int(round(float(v) * 100))


def _r(cent):
    return round((cent or 0) / 100, 2)


def _sem_acento(s):
    return "".join(ch for ch in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(ch) != "Mn").upper()


def _env(nome):
    v = os.getenv(nome)
    if v:
        return v
    for env in (PROJ / "config" / ".env", Path("/opt/escritorio/config/.env")):
        if env.exists():
            for linha in env.read_text(encoding="utf-8").splitlines():
                if linha.startswith(nome + "="):
                    return linha.split("=", 1)[1].strip().strip('"').strip("'")
    raise HTTPException(500, f"{nome} nao configurado")


BALDES = {
    "comissoes": ("PARCEIRO", "ASSOCIADO", "COMISS"),
    "impostos": ("TAXAS", "INSS", "SIMPLES", "IMPOSTO", "IOF", "IPTU", "FGTS"),
    "sazonais": ("MARKETING", "BRANDING", "CURSOS", "PASSAGEN", "HOSPEDAGEM", "HOTEL", "SEGURO",
                 "EQUIPAMENTO", "DESPESAS COM", "CONFRATERNIZ"),
}


def _balde(cat):
    n = _sem_acento(cat)
    for b, chaves in BALDES.items():
        if any(_sem_acento(k) in n for k in chaves):
            return b
    return "fixas_variaveis"


def _excluida_faturamento(l):
    t = _sem_acento((l.get("pessoa_nome") or "") + " " + (l.get("descricao") or ""))
    return bool(EXCLUIR_FATURAMENTO) and any(x in t for x in EXCLUIR_FATURAMENTO)


def _apurar(c, mes, base):
    ini, fim = _limites(mes)
    campo = "l.pagamento" if base == "pagamento" else "l.vencimento"
    contas, cats, centros = _mapas(c)
    rows = c.execute(f"SELECT l.* FROM fin_lancamentos l WHERE {BASE_VIVOS} AND l.interno=0 AND {campo} BETWEEN ? AND ?",
                     (ini, fim)).fetchall()
    ls = [_linha(r, contas, cats, centros) for r in rows]
    rec_op, desp_op, fora, excluidas = [], [], [], []
    for l in ls:
        g = l["grupo"] or "operacional"
        if l["tipo"] == "receita" and g == "operacional":
            (excluidas if _excluida_faturamento(l) else rec_op).append(l)
        elif l["tipo"] == "despesa" and g == "operacional":
            desp_op.append(l)
        else:
            fora.append(l)
    soma = lambda xs: round(sum(x["valor"] for x in xs), 2)
    fat, dop = soma(rec_op), soma(desp_op)
    distrib = soma([x for x in fora if x["grupo"] == "distribuicao" and x["tipo"] == "despesa"])
    ll = round(fat - dop, 2)
    pct = lambda a: round(100 * a / fat, 1) if fat else None
    def por(xs, chave):
        m = {}
        for x in xs:
            k = x.get(chave) or "(sem " + chave + ")"
            m[k] = m.get(k, 0) + x["valor"]
        return sorted(({"nome": k, "total": round(v, 2)} for k, v in m.items()), key=lambda y: -y["total"])
    baldes = {}
    for x in desp_op:
        b = _balde(x["categoria"])
        baldes[b] = round(baldes.get(b, 0) + x["valor"], 2)
    abertos = [x for x in ls if not x["pagamento"]]
    return {
        "mes": mes, "base": base,
        "faturamento": fat, "despesas_operacionais": dop, "lucro_liquido": ll,
        "margem_pct": pct(ll), "despesa_receita_pct": pct(dop),
        "distribuicao": distrib, "distribuicao_receita_pct": pct(distrib),
        "baldes": baldes,
        "receitas_por_categoria": por(rec_op, "categoria"),
        "despesas_por_categoria": por(desp_op, "categoria"),
        "despesas_por_centro": por(desp_op, "centro"),
        "fora_do_resultado": [{"grupo": g, "tipo": t, "total": soma([x for x in fora if x["grupo"] == g and x["tipo"] == t])}
                              for g in ("distribuicao", "capital", "transferencia", "repasse") for t in ("receita", "despesa")
                              if any(x["grupo"] == g and x["tipo"] == t for x in fora)],
        "excluidas_faturamento": [{"id": x["id"], "descricao": x["descricao"], "pessoa": x["pessoa_nome"],
                                   "valor": x["valor"]} for x in excluidas],
        "em_aberto": {"qtd": len(abertos), "receitas": soma([x for x in abertos if x["tipo"] == "receita"]),
                      "despesas": soma([x for x in abertos if x["tipo"] == "despesa"])},
    }


@router.get("/fechamento")
def fechamento(mes: Optional[str] = None, base: str = "vencimento", user=Depends(requer_financeiro)):
    ""
    mes = _mes_ok(mes)
    base = "pagamento" if base == "pagamento" else "vencimento"
    c = _con()
    try:
        _schema2(c)
        atual = _apurar(c, mes, base)
        ant = _apurar(c, _mes_mais(mes, -1), base)
        atual["anterior"] = {k: ant[k] for k in ("mes", "faturamento", "despesas_operacionais", "lucro_liquido")}
        serie = []
        for i in range(11, -1, -1):
            m = _mes_mais(mes, -i)
            a = _apurar(c, m, base)
            serie.append({"mes": m, "faturamento": a["faturamento"], "despesas": a["despesas_operacionais"],
                          "lucro": a["lucro_liquido"]})
        atual["serie"] = serie
        return atual
    finally:
        c.close()


def _asaas_extrato(ini, fim):
    import requests
    h = {"access_token": _env("ASAAS_API_TOKEN"), "User-Agent": "SistemaEscritorio/1.0"}
    out, off = [], 0
    while True:
        r = requests.get(ASAAS_API + "/financialTransactions", headers=h,
                         params={"startDate": ini, "finishDate": fim, "offset": off, "limit": 100}, timeout=60)
        if r.status_code >= 400:
            raise HTTPException(502, f"Asaas recusou o extrato ({r.status_code})")
        j = r.json()
        out += j.get("data") or []
        if not j.get("hasMore"):
            break
        off += 100
    return out


_SUFIXO = re.compile(r"[_\s]*(B_A|P_A|_A|_B|_P|ATUAL)$")


def nome_cliente(desc):
    ""
    desc = str(desc or "")
    if "fatura nr." in desc:
        desc = re.split(r"fatura nr\.\s*\d+\s*", desc)[-1]
    nome = re.split(r"\s*\[", desc)[0].strip()
    nome = _SUFIXO.sub("", nome).strip()
    nome = re.sub(r"\.\s*Nota fiscal nr\.\s*\d+", "", nome).strip()
    nome = re.sub(r"^recebida\s*-\s*ATRASADOS\s*CONTA\s*\w+\s*-\s*", "", nome).strip()
    return nome.upper()


def _parte(desc):
    desc = str(desc or "")
    return desc.split("fatura nr.")[-1] if "fatura nr." in desc else desc


def _tem_sufixo(desc):
    p = _parte(desc)
    return any(re.search(s + r"\b", p) for s in ("_A", "_B", "_P"))


def classe_por_sufixo(desc):
    p = _parte(desc)
    if re.search(r"_B_A\b", p):
        return "COM_A+B"
    if re.search(r"_P_A\b", p):
        return "PARCEIRO+A"
    if re.search(r"_A\b", p):
        return "COM_A"
    if re.search(r"_P\b", p):
        return "PARCEIRO"
    if re.search(r"_B\b", p):
        return "COM_B"
    return None


ABREV = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10,
         "NOV": 11, "DEZ": 12}


def _mes_da_aba(aba):
    ""
    a = _sem_acento(aba).strip()
    m = re.match(r"^(A|B)\s+([A-Z]+)\.?\s*(\d{2})$", a)
    if not m:
        return None, None
    mes = ABREV.get(m.group(2)[:3])
    if not mes:
        return None, None
    return ("ca" if m.group(1) == "A" else "cb"), "20%s-%02d" % (m.group(3), mes)


def importar_historico_planilha(c):
    ""
    if not HIST_PLANILHA:
        return 0
    import openpyxl
    import requests
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    tok = None
    for p in (PROJ / "config" / "token.json", Path("/opt/escritorio/config/token.json")):
        if p.exists():
            tok = str(p)
            break
    if not tok:
        return 0
    creds = Credentials.from_authorized_user_file(tok)
    if not creds.valid:
        creds.refresh(Request())
    raw = requests.get(f"https://www.googleapis.com/drive/v3/files/{HIST_PLANILHA}",
                       headers={"Authorization": "Bearer " + creds.token},
                       params={"alt": "media", "supportsAllDrives": True}, timeout=120).content
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    n = 0
    for aba in wb.sheetnames:
        parc, mes = _mes_da_aba(aba)
        if not parc:
            continue
        for row in wb[aba].iter_rows(min_row=3, max_col=3, values_only=True):
            desc, valor = row[1], row[2]
            if not desc or "recebida" not in str(desc).lower():
                continue
            try:
                vc = _cent(valor) if valor not in (None, "") else None
            except (TypeError, ValueError):
                vc = None
            cur = c.execute("INSERT OR IGNORE INTO fin_comissao_hist (parceiro, mes, cliente, sufixo, valor_cent, fonte) "
                            "VALUES (?,?,?,?,?,?)", (parc, mes, nome_cliente(desc), 1 if _tem_sufixo(desc) else 0, vc,
                                                     "planilha:" + aba.strip()))
            n += cur.rowcount
    return n


def _calcular(c, mes):
    ini, fim = _limites(mes)
    ext = _asaas_extrato(ini, fim)
    hist_ca = {r[0] for r in c.execute("SELECT DISTINCT cliente FROM fin_comissao_hist WHERE parceiro='ca' AND mes<?", (mes,))}
    relacao_ca = {r[0] for r in c.execute("SELECT cliente FROM fin_comissao_relacao WHERE parceiro='ca'")}
    m3 = _mes_mais(mes, -3)
    rec = lambda p: {r[0] for r in c.execute("SELECT DISTINCT cliente FROM fin_comissao_hist WHERE parceiro=? AND sufixo=0 "
                                              "AND mes>=? AND mes<?", (p, m3, mes))}
    ca_rec, cb_rec = rec("ca"), rec("cb")
    ajustes = {r["asaas_id"]: dict(r) for r in c.execute("SELECT * FROM fin_comissao_ajuste WHERE mes=?", (mes,))}

    def ca_ok(nome):
        if any(x in nome for x in EXCLUIR_CA_SEMPRE):
            return False
        if any(nome in h or h in nome for h in hist_ca if h):
            return True
        return any(r in nome or nome in r for r in relacao_ca if r)

    itens, ca, cb = [], 0, 0
    for t in ext:
        v = float(t.get("value") or 0)
        d = t.get("description") or ""
        if v <= 0 or "Cobrança recebida" not in d:
            continue
        nome = nome_cliente(d)
        classe = classe_por_sufixo(d)
        motivo = "sufixo" if classe else None
        if not classe:
            eg, et = nome in ca_rec, nome in cb_rec
            classe = "COM_A+B" if eg and et else ("COM_B" if et else ("COM_A" if eg else "ESCRITORIO"))
            motivo = "historico 3 meses" if classe != "ESCRITORIO" else "sem sufixo e sem historico"
        aid = str(t.get("id") or t.get("paymentId") or "")
        aj = ajustes.get(aid) or {}
        if aj.get("classe"):
            classe, motivo = aj["classe"], "ajustado a mao"
        vc = _cent(v)
        espolio = any(x in nome for x in EXCLUIR_CB)
        ok_ca = ca_ok(nome)
        fora = classe == "FORA"
        g30 = int(round(vc * PCT_CA)) if not fora and classe in ("COM_A", "COM_A+B", "PARCEIRO+A") and ok_ca else 0
        g20 = int(round(vc * PCT_CA_EXTRA)) if not fora and classe == "COM_A" and ok_ca else 0
        t10 = int(round(vc * PCT_CB)) if not fora and classe in ("COM_B", "COM_A+B", "PARCEIRO") and not espolio else 0
        aviso = None
        if classe in ("COM_A", "COM_A+B", "PARCEIRO+A") and not ok_ca:
            aviso = NOME_CA + " nao recebe: cliente fora do historico/relacao" + (
                " (exclusao fixa)" if any(x in nome for x in EXCLUIR_CA_SEMPRE) else "")
        if classe in ("COM_B", "COM_A+B", "PARCEIRO") and espolio:
            aviso = NOME_CB + " nao recebe: cliente na lista de exclusao"
        if aj.get("ca_cent") is not None:
            g30, g20, motivo = aj["ca_cent"], 0, "valor digitado"
        if aj.get("cb_cent") is not None:
            t10, motivo = aj["cb_cent"], "valor digitado"
        ca += g30 + g20
        cb += t10
        itens.append({"asaas_id": aid, "data": t.get("date"), "descricao": d, "cliente": nome, "valor": _r(vc),
                      "classe": classe, "motivo": motivo, "ca_30": _r(g30), "ca_20": _r(g20), "cb_10": _r(t10),
                      "aviso": aviso, "sufixo": 1 if _tem_sufixo(d) else 0, "ajustado": bool(aj), "fonte": "asaas"})
    for x in c.execute("SELECT * FROM fin_comissao_extra WHERE mes=? ORDER BY data, id", (mes,)):
        ca += x["ca_cent"] or 0
        cb += x["cb_cent"] or 0
        itens.append({"asaas_id": "extra:%d" % x["id"], "extra_id": x["id"], "data": x["data"], "descricao": x["descricao"],
                      "cliente": nome_cliente(x["descricao"]), "valor": _r(x["valor_cent"]), "classe": "MANUAL",
                      "motivo": "incluido a mao por " + (x["por"] or "?").split(" ")[0], "ca_30": _r(x["ca_cent"]),
                      "ca_20": 0.0, "cb_10": _r(x["cb_cent"]), "aviso": None, "sufixo": 0, "ajustado": True,
                      "fonte": "manual"})
    return itens, ca, cb


def _ja_lancadas(c, mes):
    nome_mes = f"{MESES[int(mes[5:7]) - 1]}/{mes[:4]}"
    out = {}
    for parc, rotulo in (("ca", _sem_acento(NOME_CA)), ("cb", _sem_acento(NOME_CB))):
        r = c.execute("SELECT id, valor_cent, origem FROM fin_lancamentos WHERE excluido=0 AND removido_advbox=0 AND "
                      "(ref=? OR (tipo='despesa' AND upper(descricao) LIKE ?))",
                      (f"comissao:{mes}:{parc}", f"COMISS%{rotulo}%{_sem_acento(nome_mes).split('/')[0]}%{mes[:4]}%")).fetchone()
        if r:
            out[parc] = {"id": r["id"], "valor": _r(r["valor_cent"]), "origem": r["origem"]}
    return out


@router.get("/comissoes")
def comissoes(mes: Optional[str] = None, user=Depends(requer_financeiro)):
    mes = _mes_ok(mes)
    c = _con()
    try:
        _schema2(c)
        if not c.execute("SELECT COUNT(*) FROM fin_comissao_hist").fetchone()[0]:
            importar_historico_planilha(c)
            c.commit()
        itens, ca, cb = _calcular(c, mes)
        fech = c.execute("SELECT * FROM fin_comissao_mes WHERE mes=?", (mes,)).fetchone()
        return {"mes": mes, "itens": itens, "total_ca": _r(ca), "total_cb": _r(cb),
                "nomes": {"ca": NOME_CA, "cb": NOME_CB},
                "percentuais": {"ca": round(PCT_CA * 100, 2), "ca_extra": round(PCT_CA_EXTRA * 100, 2),
                                "cb": round(PCT_CB * 100, 2)},
                "total_recebido": round(sum(i["valor"] for i in itens), 2),
                "ja_lancadas": _ja_lancadas(c, mes),
                "fechado": ({"por": fech["lancado_por"], "em": fech["lancado_em"], "ca": _r(fech["ca_cent"]),
                             "cb": _r(fech["cb_cent"])} if fech else None),
                "historico": {"meses": [r[0] for r in c.execute("SELECT DISTINCT mes FROM fin_comissao_hist ORDER BY mes DESC LIMIT 6")],
                              "relacao_ca": c.execute("SELECT COUNT(*) FROM fin_comissao_relacao WHERE parceiro='ca'").fetchone()[0]}}
    finally:
        c.close()


CLASSES = ("ESCRITORIO", "COM_A", "COM_B", "COM_A+B", "PARCEIRO", "PARCEIRO+A", "FORA")


class AjusteIn(BaseModel):
    mes: str
    asaas_id: str
    classe: Optional[str] = None
    ca: Optional[str] = None
    cb: Optional[str] = None


def _cent_txt(v):
    if v in (None, ""):
        return None
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return int(round(float(s) * 100))
    except ValueError:
        raise HTTPException(400, "Valor invalido, ex.: 50,97")


@router.post("/comissoes/ajuste")
def comissao_ajuste(dados: AjusteIn, user=Depends(requer_financeiro)):
    mes = _mes_ok(dados.mes)
    if dados.classe and dados.classe not in CLASSES:
        raise HTTPException(400, "classe invalida")
    ca, cb = _cent_txt(dados.ca), _cent_txt(dados.cb)
    c = _con()
    try:
        _schema2(c)
        if c.execute("SELECT 1 FROM fin_comissao_mes WHERE mes=?", (mes,)).fetchone():
            raise HTTPException(409, "As comissoes deste mes ja foram lancadas")
        if dados.classe or ca is not None or cb is not None:
            c.execute("INSERT OR REPLACE INTO fin_comissao_ajuste (mes, asaas_id, classe, ca_cent, cb_cent, por, em) "
                      "VALUES (?,?,?,?,?,?,?)", (mes, dados.asaas_id, dados.classe or None, ca, cb, _nome(user), _agora()))
        else:
            c.execute("DELETE FROM fin_comissao_ajuste WHERE mes=? AND asaas_id=?", (mes, dados.asaas_id))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


class ExtraIn(BaseModel):
    mes: str
    data: Optional[str] = None
    descricao: str
    valor: Optional[str] = None
    ca: Optional[str] = None
    cb: Optional[str] = None


@router.post("/comissoes/extra")
def comissao_extra(dados: ExtraIn, user=Depends(requer_financeiro)):
    ""
    mes = _mes_ok(dados.mes)
    if not dados.descricao.strip():
        raise HTTPException(400, "Descreva a linha")
    ca, cb = _cent_txt(dados.ca) or 0, _cent_txt(dados.cb) or 0
    if not ca and not cb:
        raise HTTPException(400, "Informe o valor de " + NOME_CA + " ou de " + NOME_CB)
    c = _con()
    try:
        _schema2(c)
        if c.execute("SELECT 1 FROM fin_comissao_mes WHERE mes=?", (mes,)).fetchone():
            raise HTTPException(409, "As comissoes deste mes ja foram lancadas")
        cur = c.execute("INSERT INTO fin_comissao_extra (mes, data, descricao, valor_cent, ca_cent, cb_cent, por, em) "
                        "VALUES (?,?,?,?,?,?,?,?)", (mes, dados.data, dados.descricao.strip()[:300], _cent_txt(dados.valor),
                                                     ca, cb, _nome(user), _agora()))
        c.commit()
        return {"ok": True, "id": cur.lastrowid}
    finally:
        c.close()


@router.delete("/comissoes/extra/{xid}")
def comissao_extra_apagar(xid: int, user=Depends(requer_financeiro)):
    c = _con()
    try:
        _schema2(c)
        r = c.execute("SELECT mes FROM fin_comissao_extra WHERE id=?", (xid,)).fetchone()
        if r and c.execute("SELECT 1 FROM fin_comissao_mes WHERE mes=?", (r["mes"],)).fetchone():
            raise HTTPException(409, "As comissoes deste mes ja foram lancadas")
        c.execute("DELETE FROM fin_comissao_extra WHERE id=?", (xid,))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


class LancarIn(BaseModel):
    mes: str


@router.post("/comissoes/lancar")
def comissao_lancar(dados: LancarIn, user=Depends(requer_financeiro)):
    ""
    mes = _mes_ok(dados.mes)
    c = _con()
    try:
        _schema2(c)
        ja = _ja_lancadas(c, mes)
        itens, ca, cb = _calcular(c, mes)
        venc = _mes_mais(mes, 1) + "-%02d" % DIA_VENC_COMISSAO
        nome_mes = f"{MESES[int(mes[5:7]) - 1]}/{mes[:4]}"
        cat = _id_por_nome(c, "fin_categorias", "HONORÁRIOS DE PARCEIROS", {"natureza": "despesa", "grupo": "operacional"})
        centro = _id_por_nome(c, "fin_centros", "CUSTO PARCERIAS")
        conta = c.execute("SELECT id FROM fin_contas WHERE ativo=1 ORDER BY id LIMIT 1").fetchone() if "ativo" in {r[1] for r in c.execute("PRAGMA table_info(fin_contas)")} else c.execute("SELECT id FROM fin_contas ORDER BY id LIMIT 1").fetchone()
        criados = {}
        for parc, total, quem, rotulo in (("ca", ca, NOME_CA.upper(), _sem_acento(NOME_CA)), ("cb", cb, NOME_CB.upper(), _sem_acento(NOME_CB))):
            if parc in ja:
                criados[parc] = {"ja_existia": ja[parc]}
                continue
            if total <= 0:
                continue
            x = {"tipo": "despesa", "descricao": f"COMISSAO {rotulo} {nome_mes}", "valor_cent": total,
                 "vencimento": venc, "competencia": mes, "categoria_id": cat, "centro_id": centro,
                 "conta_id": conta["id"] if conta else None, "pessoa_nome": quem, "origem": "comissao",
                 "ref": f"comissao:{mes}:{parc}", "criado_por": _nome(user), "criado_em": _agora(),
                 "atualizado_em": _agora(), "atualizado_por": _nome(user)}
            cur = c.execute(f"INSERT INTO fin_lancamentos ({', '.join(x)}) VALUES ({', '.join('?' for _ in x)})",
                            list(x.values()))
            _hist(c, cur.lastrowid, "criado", {"comissao": parc, "mes": mes, "itens": len(itens)}, _nome(user))
            criados[parc] = {"id": cur.lastrowid, "valor": _r(total)}
        for i in itens:
            for parc, entra in (("ca", i["ca_30"] > 0), ("cb", i["cb_10"] > 0)):
                if entra:
                    c.execute("INSERT OR IGNORE INTO fin_comissao_hist (parceiro, mes, cliente, sufixo, valor_cent, fonte) "
                              "VALUES (?,?,?,?,?,?)", (parc, mes, i["cliente"], i["sufixo"], _cent(i["valor"]), "sistema"))
        c.execute("INSERT OR REPLACE INTO fin_comissao_mes (mes, ca_cent, cb_cent, itens, lancado_por, lancado_em) "
                  "VALUES (?,?,?,?,?,?)", (mes, ca, cb, json.dumps(itens, ensure_ascii=False), _nome(user), _agora()))
        c.commit()
        return {"ok": True, "criados": criados, "vencimento": venc}
    finally:
        c.close()


class RelacaoIn(BaseModel):
    parceiro: str = "ca"
    clientes: List[str]
    fonte: Optional[str] = "manual"


@router.post("/comissoes/relacao")
def comissao_relacao(dados: RelacaoIn, user=Depends(requer_financeiro)):
    ""
    if dados.parceiro not in ("ca", "cb"):
        raise HTTPException(400, "parceiro: ca ou cb")
    c = _con()
    try:
        _schema2(c)
        n = 0
        for nome in dados.clientes:
            nome = _SUFIXO.sub("", re.split(r"\s*[\(_]", str(nome))[0].strip().upper()).strip()
            if nome:
                n += c.execute("INSERT OR IGNORE INTO fin_comissao_relacao (parceiro, cliente, fonte, criado_em) "
                               "VALUES (?,?,?,?)", (dados.parceiro, nome, dados.fonte, _agora())).rowcount
        c.commit()
        return {"ok": True, "novos": n}
    finally:
        c.close()


def _parse_c6_pdf(raw, senha):
    import fitz
    doc = fitz.open(stream=raw, filetype="pdf")
    if doc.needs_pass and not doc.authenticate(senha or ""):
        raise HTTPException(400, "Senha do PDF incorreta")
    linhas = [l.strip() for p in doc for l in p.get_text().splitlines() if l.strip()]
    per = re.search(r"\(\s*(\d{2})/(\d{2})/(\d{4})\s*-\s*(\d{2})/(\d{2})/(\d{4})\s*\)", " ".join(linhas[:40]))
    if not per:
        raise HTTPException(400, "Nao reconheci o periodo do extrato (esperado extrato C6 em PDF)")
    ano_ini, mes_ini = int(per.group(3)), int(per.group(2))
    dt = re.compile(r"^\d{2}/\d{2}$")
    val = re.compile(r"^(-?)R\$\s*([\d.]+,\d{2})$")
    out, i = [], 0
    while i < len(linhas) - 1:
        if dt.match(linhas[i]) and dt.match(linhas[i + 1]):
            dia, mes = int(linhas[i][:2]), int(linhas[i][3:5])
            j, partes = i + 2, []
            while j < len(linhas) and not val.match(linhas[j]) and not dt.match(linhas[j]) and j < i + 12:
                partes.append(linhas[j])
                j += 1
            if j < len(linhas) and val.match(linhas[j]):
                m = val.match(linhas[j])
                v = float(m.group(2).replace(".", "").replace(",", "."))
                ano = ano_ini + (1 if mes < mes_ini else 0)
                out.append({"data": "%04d-%02d-%02d" % (ano, mes, dia), "valor": -v if m.group(1) else v,
                            "tipo": partes[0] if partes else "", "descricao": " ".join(partes[1:]) or (partes[0] if partes else "")})
                i = j + 1
                continue
        i += 1
    return out


def _parse_ofx(raw):
    txt = raw.decode("latin-1", errors="ignore")
    out = []
    for bloco in re.findall(r"<STMTTRN>(.*?)</STMTTRN>", txt, re.S | re.I):
        g = lambda tag: (re.search(rf"<{tag}>([^<\r\n]*)", bloco, re.I) or [None, ""])[1].strip()
        d = g("DTPOSTED")
        if len(d) < 8:
            continue
        out.append({"data": f"{d[:4]}-{d[4:6]}-{d[6:8]}", "valor": float(g("TRNAMT").replace(",", ".") or 0),
                    "tipo": g("TRNTYPE"), "descricao": g("MEMO") or g("NAME"), "fitid": g("FITID")})
    return out


def _gravar_extrato(c, fonte, itens):
    novos, vistos = 0, {}
    for it in itens:
        base = f"{it['data']}|{_cent(it['valor'])}|{it.get('descricao', '')}"
        vistos[base] = vistos.get(base, 0) + 1
        ref = it.get("ref") or (f"{fonte}:" + (it.get("fitid") or hashlib.sha1(f"{base}|{vistos[base]}".encode()).hexdigest()[:20]))
        novos += c.execute("INSERT OR IGNORE INTO fin_extrato (fonte, ref, data, valor_cent, descricao, tipo_externo, "
                           "importado_em) VALUES (?,?,?,?,?,?,?)",
                           (fonte, ref, it["data"], _cent(it["valor"]), it.get("descricao"), it.get("tipo"), _agora())).rowcount
    return novos


def _palavras(s):
    return {w for w in re.findall(r"[A-Z0-9]{3,}", _sem_acento(s)) if w not in ("PIX", "COBRANCA", "RECEBIDA", "FATURA",
                                                                                 "LTDA", "TAXA", "PAGAMENTO", "TRANSFERENCIA")}


def _sugerir(c, fonte, ini, fim):
    ""
    pend = c.execute("SELECT * FROM fin_extrato WHERE fonte=? AND status='pendente' AND data BETWEEN ? AND ?",
                     (fonte, ini, fim)).fetchall()
    usados = {r[0] for r in c.execute("SELECT lancamento_id FROM fin_extrato WHERE lancamento_id IS NOT NULL AND status='conciliado'")}
    d_ini = (date.fromisoformat(ini) - timedelta(days=10)).isoformat()
    d_fim = (date.fromisoformat(fim) + timedelta(days=10)).isoformat()
    cands = [dict(r) for r in c.execute(
        "SELECT l.* FROM fin_lancamentos l WHERE " + BASE_VIVOS + " AND l.conciliado_ref IS NULL AND "
        "((l.vencimento BETWEEN ? AND ?) OR (l.pagamento BETWEEN ? AND ?))", (d_ini, d_fim, d_ini, d_fim))]
    out = []
    for e in pend:
        tipo = "receita" if e["valor_cent"] > 0 else "despesa"
        alvo = abs(e["valor_cent"])
        de = date.fromisoformat(e["data"])
        def perto(l):
            for k in ("pagamento", "vencimento"):
                if l.get(k) and abs((date.fromisoformat(l[k]) - de).days) <= 5:
                    return True
            return False
        mesmos = [l for l in cands if l["tipo"] == tipo and abs(l["valor_cent"] - alvo) <= 1 and l["id"] not in usados and perto(l)]
        pe = _palavras(e["descricao"])
        fortes = [l for l in mesmos if pe & _palavras((l.get("pessoa_nome") or "") + " " + (l.get("descricao") or ""))]
        esc, forca = None, None
        if fortes:
            esc, forca = fortes[0], "forte"
        elif len(mesmos) == 1:
            esc, forca = mesmos[0], "so valor e data"
        if esc:
            usados.add(esc["id"])
        out.append({"extrato": dict(e), "sugestao": esc["id"] if esc else None, "forca": forca,
                    "outras": [l["id"] for l in mesmos if not esc or l["id"] != esc["id"]][:5]})
    return out


@router.post("/conciliacao/asaas/importar")
def conc_asaas_importar(mes: Optional[str] = None, user=Depends(requer_financeiro)):
    mes = _mes_ok(mes)
    ini, fim = _limites(mes)
    ext = _asaas_extrato(ini, fim)
    itens = [{"data": t.get("date"), "valor": float(t.get("value") or 0), "descricao": t.get("description"),
              "tipo": t.get("type"), "ref": "asaas:" + str(t.get("id"))} for t in ext if t.get("id") and t.get("date")]
    c = _con()
    try:
        _schema2(c)
        n = _gravar_extrato(c, "asaas", itens)
        c.commit()
        return {"ok": True, "lidos": len(itens), "novos": n}
    finally:
        c.close()


@router.post("/conciliacao/c6/importar")
async def conc_c6_importar(arquivo: UploadFile = File(...), senha: str = Form(""), user=Depends(requer_financeiro)):
    raw = await arquivo.read()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(400, "Arquivo grande demais")
    nome = (arquivo.filename or "").lower()
    itens = _parse_ofx(raw) if nome.endswith(".ofx") or raw[:200].upper().find(b"OFX") >= 0 else _parse_c6_pdf(raw, senha)
    if not itens:
        raise HTTPException(400, "Nao achei movimentos no arquivo")
    c = _con()
    try:
        _schema2(c)
        n = _gravar_extrato(c, "c6", itens)
        c.commit()
        datas = sorted(i["data"] for i in itens)
        return {"ok": True, "lidos": len(itens), "novos": n, "de": datas[0], "ate": datas[-1],
                "entradas": round(sum(i["valor"] for i in itens if i["valor"] > 0), 2),
                "saidas": round(sum(-i["valor"] for i in itens if i["valor"] < 0), 2)}
    finally:
        c.close()


@router.get("/conciliacao")
def conciliacao(fonte: str = "asaas", mes: Optional[str] = None, user=Depends(requer_financeiro)):
    if fonte not in ("asaas", "c6"):
        raise HTTPException(400, "fonte: asaas ou c6")
    mes = _mes_ok(mes)
    ini, fim = _limites(mes)
    c = _con()
    try:
        _schema2(c)
        contas, cats, centros = _mapas(c)
        sug = _sugerir(c, fonte, ini, fim)
        ids = {s["sugestao"] for s in sug if s["sugestao"]} | {i for s in sug for i in s["outras"]}
        ids |= {r[0] for r in c.execute("SELECT lancamento_id FROM fin_extrato WHERE fonte=? AND data BETWEEN ? AND ? "
                                        "AND lancamento_id IS NOT NULL", (fonte, ini, fim))}
        lanc = {}
        for i in ids:
            r = c.execute("SELECT * FROM fin_lancamentos WHERE id=?", (i,)).fetchone()
            if r:
                lanc[i] = _linha(r, contas, cats, centros)
        feitos = [dict(r) for r in c.execute("SELECT * FROM fin_extrato WHERE fonte=? AND data BETWEEN ? AND ? "
                                             "AND status<>'pendente' ORDER BY data", (fonte, ini, fim))]
        conta = c.execute("SELECT id FROM fin_contas WHERE nome LIKE ? ORDER BY id LIMIT 1",
                          ("ASAAS%" if fonte == "asaas" else "C6%",)).fetchone()
        sobras = []
        if conta:
            ligados = {r[0] for r in c.execute("SELECT lancamento_id FROM fin_extrato WHERE lancamento_id IS NOT NULL")} | \
                      {s["sugestao"] for s in sug if s["sugestao"]}
            for r in c.execute("SELECT l.* FROM fin_lancamentos l WHERE " + BASE_VIVOS + " AND l.interno=0 AND l.conta_id=? "
                               "AND COALESCE(l.pagamento, l.vencimento) BETWEEN ? AND ? ORDER BY l.vencimento",
                               (conta["id"], ini, fim)):
                if r["id"] not in ligados and not r["conciliado_ref"]:
                    sobras.append(_linha(r, contas, cats, centros))
        tot = c.execute("SELECT COUNT(*), SUM(status='conciliado'), SUM(status='ignorado') FROM fin_extrato "
                        "WHERE fonte=? AND data BETWEEN ? AND ?", (fonte, ini, fim)).fetchone()
        return {"fonte": fonte, "mes": mes, "pendentes": sug, "feitos": feitos, "lancamentos": lanc,
                "sem_extrato": sobras[:300], "contagem": {"linhas": tot[0] or 0, "conciliadas": tot[1] or 0,
                                                          "ignoradas": tot[2] or 0}}
    finally:
        c.close()


class ConcIn(BaseModel):
    pares: List[dict]
    dar_baixa: bool = True


@router.post("/conciliacao/confirmar")
def conc_confirmar(dados: ConcIn, user=Depends(requer_financeiro)):
    ""
    c = _con()
    try:
        _schema2(c)
        feitos = baixas = 0
        for p in dados.pares:
            e = c.execute("SELECT * FROM fin_extrato WHERE id=? AND status='pendente'", (p.get("extrato_id"),)).fetchone()
            l = c.execute("SELECT * FROM fin_lancamentos WHERE id=? AND excluido=0", (p.get("lancamento_id"),)).fetchone()
            if not e or not l:
                continue
            c.execute("UPDATE fin_extrato SET lancamento_id=?, status='conciliado', conciliado_por=?, conciliado_em=? WHERE id=?",
                      (l["id"], _nome(user), _agora(), e["id"]))
            campos = {"conciliado_em": _agora(), "conciliado_ref": e["ref"]}
            if dados.dar_baixa and not l["pagamento"]:
                campos["pagamento"] = e["data"]
                conta = c.execute("SELECT id FROM fin_contas WHERE nome LIKE ? ORDER BY id LIMIT 1",
                                  ("ASAAS%" if e["fonte"] == "asaas" else "C6%",)).fetchone()
                if conta and not l["conta_id"]:
                    campos["conta_id"] = conta["id"]
                baixas += 1
            if l["origem"] == "advbox":
                campos["editado_local"] = 1
            campos.update(atualizado_por=_nome(user), atualizado_em=_agora())
            c.execute("UPDATE fin_lancamentos SET " + ", ".join(f"{k}=?" for k in campos) + " WHERE id=?",
                      list(campos.values()) + [l["id"]])
            _hist(c, l["id"], "conciliado", {"extrato": e["ref"], "data": e["data"], "baixa": "pagamento" in campos}, _nome(user))
            feitos += 1
        c.commit()
        return {"ok": True, "conciliados": feitos, "baixas": baixas}
    finally:
        c.close()


class ExtIn(BaseModel):
    categoria_id: Optional[int] = None
    descricao: Optional[str] = None


@router.post("/conciliacao/{eid}/criar")
def conc_criar(eid: int, dados: ExtIn, user=Depends(requer_financeiro)):
    ""
    if not dados.categoria_id:
        raise HTTPException(400, "Escolha a categoria")
    c = _con()
    try:
        _schema2(c)
        e = c.execute("SELECT * FROM fin_extrato WHERE id=? AND status='pendente'", (eid,)).fetchone()
        if not e:
            raise HTTPException(404, "Linha do extrato nao encontrada")
        conta = c.execute("SELECT id FROM fin_contas WHERE nome LIKE ? ORDER BY id LIMIT 1",
                          ("ASAAS%" if e["fonte"] == "asaas" else "C6%",)).fetchone()
        x = {"tipo": "receita" if e["valor_cent"] > 0 else "despesa", "descricao": dados.descricao or e["descricao"],
             "valor_cent": abs(e["valor_cent"]), "vencimento": e["data"], "competencia": e["data"][:7], "pagamento": e["data"],
             "conta_id": conta["id"] if conta else None, "categoria_id": dados.categoria_id,
             "pessoa_nome": nome_cliente(e["descricao"]) if e["fonte"] == "asaas" else None,
             "origem": e["fonte"], "conciliado_em": _agora(), "conciliado_ref": e["ref"], "criado_por": _nome(user),
             "criado_em": _agora(), "atualizado_em": _agora(), "atualizado_por": _nome(user)}
        cur = c.execute(f"INSERT INTO fin_lancamentos ({', '.join(x)}) VALUES ({', '.join('?' for _ in x)})", list(x.values()))
        c.execute("UPDATE fin_extrato SET lancamento_id=?, status='conciliado', conciliado_por=?, conciliado_em=? WHERE id=?",
                  (cur.lastrowid, _nome(user), _agora(), eid))
        _hist(c, cur.lastrowid, "criado_do_extrato", {"extrato": e["ref"]}, _nome(user))
        c.commit()
        return {"ok": True, "id": cur.lastrowid}
    finally:
        c.close()


class LoteIn(BaseModel):
    fonte: str
    mes: str
    acao: str


def _eh_taxa(e):
    t = ((e["tipo_externo"] or "") + " " + (e["descricao"] or "")).upper()
    return e["valor_cent"] < 0 and ("FEE" in t or _sem_acento(e["descricao"] or "").startswith("TAXA"))


@router.post("/conciliacao/lote")
def conc_lote(dados: LoteIn, user=Depends(requer_financeiro)):
    ""
    if dados.fonte not in ("asaas", "c6") or dados.acao not in ("juntar_taxas", "ignorar_transferencias"):
        raise HTTPException(400, "fonte/acao invalida")
    mes = _mes_ok(dados.mes)
    ini, fim = _limites(mes)
    c = _con()
    try:
        _schema2(c)
        pend = c.execute("SELECT * FROM fin_extrato WHERE fonte=? AND status='pendente' AND data BETWEEN ? AND ?",
                         (dados.fonte, ini, fim)).fetchall()
        if dados.acao == "ignorar_transferencias":
            alvo = [e for e in pend if (e["tipo_externo"] or "").upper() == "TRANSFER"]
            for e in alvo:
                c.execute("UPDATE fin_extrato SET status='ignorado', nota=?, conciliado_por=?, conciliado_em=? WHERE id=?",
                          ("transferencia entre contas", _nome(user), _agora(), e["id"]))
            c.commit()
            return {"ok": True, "linhas": len(alvo)}
        taxas = [e for e in pend if _eh_taxa(e)]
        sug = {s["extrato"]["id"] for s in _sugerir(c, dados.fonte, ini, fim) if s["sugestao"]}
        taxas = [e for e in taxas if e["id"] not in sug]
        if not taxas:
            return {"ok": True, "linhas": 0}
        total = -sum(e["valor_cent"] for e in taxas)
        cat = _id_por_nome(c, "fin_categorias", "TAXAS BANCÁRIAS", {"natureza": "despesa", "grupo": "operacional"})
        conta = c.execute("SELECT id FROM fin_contas WHERE nome LIKE ? ORDER BY id LIMIT 1",
                          ("ASAAS%" if dados.fonte == "asaas" else "C6%",)).fetchone()
        ult = max(e["data"] for e in taxas)
        x = {"tipo": "despesa", "descricao": f"TAXAS {dados.fonte.upper()} {MESES[int(mes[5:7]) - 1]}/{mes[:4]} ({len(taxas)} tarifas)",
             "valor_cent": total, "vencimento": ult, "competencia": mes, "pagamento": ult, "categoria_id": cat,
             "conta_id": conta["id"] if conta else None, "origem": dados.fonte, "conciliado_em": _agora(),
             "conciliado_ref": f"{dados.fonte}:taxas:{mes}", "criado_por": _nome(user), "criado_em": _agora(),
             "atualizado_em": _agora(), "atualizado_por": _nome(user)}
        cur = c.execute(f"INSERT INTO fin_lancamentos ({', '.join(x)}) VALUES ({', '.join('?' for _ in x)})", list(x.values()))
        for e in taxas:
            c.execute("UPDATE fin_extrato SET lancamento_id=?, status='conciliado', conciliado_por=?, conciliado_em=? WHERE id=?",
                      (cur.lastrowid, _nome(user), _agora(), e["id"]))
        _hist(c, cur.lastrowid, "criado_do_extrato", {"taxas": len(taxas), "total_cent": total}, _nome(user))
        c.commit()
        return {"ok": True, "linhas": len(taxas), "total": _r(total), "id": cur.lastrowid}
    finally:
        c.close()


class IgnIn(BaseModel):
    nota: Optional[str] = None


@router.post("/conciliacao/{eid}/ignorar")
def conc_ignorar(eid: int, dados: IgnIn, user=Depends(requer_financeiro)):
    ""
    c = _con()
    try:
        _schema2(c)
        c.execute("UPDATE fin_extrato SET status='ignorado', nota=?, conciliado_por=?, conciliado_em=? WHERE id=? AND status='pendente'",
                  (dados.nota, _nome(user), _agora(), eid))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.post("/conciliacao/{eid}/desfazer")
def conc_desfazer(eid: int, user=Depends(requer_financeiro)):
    ""
    c = _con()
    try:
        _schema2(c)
        e = c.execute("SELECT * FROM fin_extrato WHERE id=?", (eid,)).fetchone()
        if not e:
            raise HTTPException(404, "Linha nao encontrada")
        if e["lancamento_id"]:
            c.execute("UPDATE fin_lancamentos SET conciliado_em=NULL, conciliado_ref=NULL WHERE id=? AND conciliado_ref=?",
                      (e["lancamento_id"], e["ref"]))
            _hist(c, e["lancamento_id"], "conciliacao_desfeita", {"extrato": e["ref"]}, _nome(user))
        c.execute("UPDATE fin_extrato SET status='pendente', lancamento_id=NULL, nota=NULL WHERE id=?", (eid,))
        c.commit()
        return {"ok": True}
    finally:
        c.close()
