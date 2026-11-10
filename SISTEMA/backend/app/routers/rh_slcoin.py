""
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..deps import usuario_atual

router = APIRouter(prefix="/api/rh/slcoin", tags=["rh"])

_DB = str(Path(__file__).resolve().parents[2] / "sistema_esc.db")

FOTOS = Path(os.getenv("RH_ARQUIVOS_DIR", "/var/lib/esc_rh")) / "desejos"
FOTO_MAX = 8 * 1024 * 1024
FOTO_EXT = {"jpg", "jpeg", "png", "webp", "heic", "pdf"}
REGULAMENTO = "regulamento_slcoin"
VERSAO = "2026-10"
PATAMARES = ("Pausa", "Cuidado", "Encontro", "Grande experiência", "Sonho")

ESCADA = [("Pausa", 100, 35.0), ("Cuidado", 300, 120.0), ("Encontro", 600, 270.0),
          ("Grande experiência", 1000, 500.0), ("Sonho", 3500, 2500.0)]


def _con():
    c = sqlite3.connect(_DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _admin(user):
    return (getattr(user, "papel", "") or "") == "admin"


def _coluna_foto(c):
    if "foto" not in [r[1] for r in c.execute("PRAGMA table_info(rh_slcoin_desejos)")]:
        c.execute("ALTER TABLE rh_slcoin_desejos ADD COLUMN foto TEXT")
        c.commit()


def _tem(c, tabela):
    return bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                          (tabela,)).fetchone())


def _ficha(c, user, colaborador_id=None):
    if colaborador_id and _admin(user):
        r = c.execute("SELECT id, nome FROM rh_colaboradores WHERE id=?", (colaborador_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Colaborador nao encontrado")
        return r
    r = c.execute("SELECT id, nome FROM rh_colaboradores WHERE usuario_id=?", (user.id,)).fetchone()
    if not r and getattr(user, "email", None):
        r = c.execute("SELECT id, nome FROM rh_colaboradores WHERE lower(email)=?",
                      (user.email.lower(),)).fetchone()
    if not r:
        raise HTTPException(404, "Este login ainda nao tem ficha de RH")
    return r


def _carteira(c, colaborador_id):
    ""
    if _tem(c, "slcoin_extrato"):
        hoje = time.strftime("%Y-%m-%d")
        saldo = c.execute(
            "SELECT COALESCE(SUM(quantidade),0) FROM slcoin_extrato WHERE colaborador_id=? "
            "AND (expira_em IS NULL OR expira_em >= ?)", (colaborador_id, hoje)).fetchone()[0]
        vencendo = c.execute(
            "SELECT COALESCE(SUM(quantidade),0) FROM slcoin_extrato WHERE colaborador_id=? "
            "AND tipo='ganho' AND expira_em BETWEEN ? AND date(?, '+45 day')",
            (colaborador_id, hoje, hoje)).fetchone()[0]
        extrato = [dict(r) for r in c.execute(
            "SELECT data_credito, origem, quantidade, expira_em FROM slcoin_extrato "
            "WHERE colaborador_id=? ORDER BY id DESC LIMIT 30", (colaborador_id,))]
        return {"saldo": int(saldo), "vencendo": int(vencendo), "extrato": extrato, "fonte": "extrato"}
    try:
        from .comercial_kpi import montar_mesa
        nome = c.execute("SELECT nome FROM rh_colaboradores WHERE id=?", (colaborador_id,)).fetchone()["nome"]
        mesa = montar_mesa(nome.split()[0], forcar=False)
        pc = (mesa or {}).get("slcoin") or {}
        return {"saldo": int(pc.get("saldo") or 0), "vencendo": 0,
                "extrato": pc.get("hoje") or [], "nivel": pc.get("nivel"), "fonte": "motor"}
    except Exception:
        return {"saldo": None, "vencendo": 0, "extrato": [], "fonte": "indisponivel"}


def _gasto_no_mes(c, mes=None):
    ""
    mes = mes or time.strftime("%Y-%m")
    ini = int(time.mktime(time.strptime(mes + "-01", "%Y-%m-%d")))
    return c.execute(
        "SELECT COALESCE(SUM(valor_referencia),0) FROM rh_slcoin_resgates "
        "WHERE pedido_em >= ? AND status IN ('aprovado','comprado','entregue','lancado_folha')",
        (ini,)).fetchone()[0]


def _resgate_aberto(c, colaborador_id):
    ""
    if not _tem(c, "slcoin_extrato"):
        return {"aberto": False, "motivo": "sem_base",
                "texto": "A sua carteira ainda está sendo ligada. Assim que o histórico fechar "
                         "um mês, o resgate abre no mês em que você superar a sua própria média."}
    hoje = time.strftime("%Y-%m")
    mes = c.execute(
        "SELECT COALESCE(SUM(quantidade),0) FROM slcoin_extrato WHERE colaborador_id=? "
        "AND tipo='ganho' AND substr(data_credito,1,7)=?", (colaborador_id, hoje)).fetchone()[0]
    antes = [r[0] for r in c.execute(
        "SELECT SUM(quantidade) FROM slcoin_extrato WHERE colaborador_id=? AND tipo='ganho' "
        "AND substr(data_credito,1,7) < ? GROUP BY substr(data_credito,1,7) "
        "ORDER BY substr(data_credito,1,7) DESC LIMIT 3", (colaborador_id, hoje))]
    if not antes:
        return {"aberto": True, "motivo": "primeiro_mes",
                "texto": "Primeiro mês do programa: o resgate está aberto."}
    media = sum(antes) / len(antes)
    aberto = mes > media
    falta = max(0, int(round(media - mes)) + 1)
    return {"aberto": aberto, "motivo": "superacao", "media": round(media), "no_mes": int(mes),
            "falta": 0 if aberto else falta,
            "texto": "Resgate aberto: você já superou a sua própria média." if aberto else
                     ("O resgate abre no mês em que você supera a sua própria média dos últimos "
                      "três meses. Faltam %s Coins para abrir." % falta)}


@router.get("/trilha")
def trilha(colaborador_id: Optional[int] = Query(None), user=Depends(usuario_atual)):
    c = _con()
    try:
        f = _ficha(c, user, colaborador_id)
        cid = f["id"]
        if not _tem(c, "rh_slcoin_vitrine"):
            return {"pronto": False, "aviso": "A vitrine ainda não foi criada."}
        cart = _carteira(c, cid)
        saldo = cart["saldo"] or 0
        itens = [dict(r) for r in c.execute(
            "SELECT id, item, descricao, patamar, custo_moedas, valor_referencia AS limite "
            "FROM rh_slcoin_vitrine WHERE ativo=1 ORDER BY custo_moedas")]
        pend = c.execute(
            "SELECT vitrine_id FROM rh_slcoin_resgates WHERE colaborador_id=? AND status='pedido'",
            (cid,)).fetchone()
        obj = c.execute("SELECT vitrine_id FROM rh_slcoin_objetivo WHERE colaborador_id=?",
                        (cid,)).fetchone()
        aberto = _resgate_aberto(c, cid)
        for i in itens:
            i["falta"] = max(0, i["custo_moedas"] - saldo)
            i["posso"] = (i["falta"] == 0) and aberto["aberto"] and not pend
            i["pedido"] = bool(pend and pend["vitrine_id"] == i["id"])
            i["objetivo"] = bool(obj and obj["vitrine_id"] == i["id"])
        patamares = []
        for nome in PATAMARES:
            dentro = [i for i in itens if i["patamar"] == nome]
            if not dentro:
                continue
            custo_min = min(i["custo_moedas"] for i in dentro)
            patamares.append({"nome": nome, "itens": dentro, "alcancado": saldo >= custo_min,
                              "falta": max(0, custo_min - saldo)})
        semestre = [dict(r) for r in c.execute(
            "SELECT item, decidido_em, status FROM rh_slcoin_resgates WHERE colaborador_id=? "
            "AND status IN ('entregue','lancado_folha') ORDER BY id DESC LIMIT 12", (cid,))]
        return {"pronto": True, "colaborador": {"id": cid, "nome": f["nome"]},
                "saldo": cart["saldo"], "vencendo": cart["vencendo"], "nivel": cart.get("nivel"),
                "fonte_carteira": cart["fonte"], "patamares": patamares,
                "objetivo": obj["vitrine_id"] if obj else None, "resgate": aberto,
                "semestre": semestre, "bilhetes": len([p for p in patamares if p["alcancado"]]),
                "extrato": cart["extrato"]}
    finally:
        c.close()


class Escolha(BaseModel):
    vitrine_id: int


@router.post("/objetivo")
def objetivo(dados: Escolha, user=Depends(usuario_atual)):
    c = _con()
    try:
        f = _ficha(c, user)
        if not c.execute("SELECT 1 FROM rh_slcoin_vitrine WHERE id=? AND ativo=1",
                         (dados.vitrine_id,)).fetchone():
            raise HTTPException(404, "Experiencia nao encontrada")
        c.execute("INSERT INTO rh_slcoin_objetivo (colaborador_id, vitrine_id, definido_em) "
                  "VALUES (?,?,?) ON CONFLICT(colaborador_id) DO UPDATE SET vitrine_id=excluded.vitrine_id, "
                  "definido_em=excluded.definido_em", (f["id"], dados.vitrine_id, int(time.time())))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.post("/resgate")
def resgate(dados: Escolha, request: Request, user=Depends(usuario_atual)):
    c = _con()
    try:
        f = _ficha(c, user)
        cid = f["id"]
        if c.execute("SELECT 1 FROM rh_slcoin_resgates WHERE colaborador_id=? AND status='pedido'",
                     (cid,)).fetchone():
            raise HTTPException(400, "Voce ja tem um pedido aguardando a resposta")
        item = c.execute("SELECT * FROM rh_slcoin_vitrine WHERE id=? AND ativo=1",
                         (dados.vitrine_id,)).fetchone()
        if not item:
            raise HTTPException(404, "Experiencia nao encontrada")
        aberto = _resgate_aberto(c, cid)
        if not aberto["aberto"]:
            raise HTTPException(400, aberto["texto"])
        saldo = (_carteira(c, cid)["saldo"] or 0)
        if saldo < item["custo_moedas"]:
            raise HTTPException(400, "Faltam %s Coins para esta experiencia"
                                % (item["custo_moedas"] - saldo))
        c.execute("INSERT INTO rh_slcoin_resgates (colaborador_id, vitrine_id, item, custo_moedas, "
                  "valor_referencia, status, pedido_em) VALUES (?,?,?,?,?,'pedido',?)",
                  (cid, item["id"], item["item"], item["custo_moedas"], item["valor_referencia"],
                   int(time.time())))
        c.commit()
        return {"ok": True, "mensagem": "Pedido enviado. Assim que for aprovado, a sua experiencia sai."}
    finally:
        c.close()


@router.get("/pedidos")
def pedidos(user=Depends(usuario_atual)):
    ""
    if not _admin(user):
        raise HTTPException(403, "Acao restrita a administradores")
    c = _con()
    try:
        linhas = [dict(r) for r in c.execute(
            "SELECT r.*, col.nome, v.patamar FROM rh_slcoin_resgates r "
            "JOIN rh_colaboradores col ON col.id = r.colaborador_id "
            "LEFT JOIN rh_slcoin_vitrine v ON v.id = r.vitrine_id "
            "ORDER BY CASE WHEN r.status='pedido' THEN 0 ELSE 1 END, r.id DESC LIMIT 60")]
        equipe = c.execute("SELECT count(*) FROM rh_colaboradores WHERE setor='Comercial' "
                           "AND status='ativo'").fetchone()[0]
        return {"pedidos": linhas, "gasto_no_mes": round(_gasto_no_mes(c), 2), "equipe": equipe}
    finally:
        c.close()


class Decisao(BaseModel):
    status: str
    obs: Optional[str] = None
    competencia_folha: Optional[str] = None


@router.post("/pedidos/{pedido_id}/decidir")
def decidir(pedido_id: int, dados: Decisao, user=Depends(usuario_atual)):
    if not _admin(user):
        raise HTTPException(403, "Acao restrita a administradores")
    if dados.status not in ("aprovado", "negado", "comprado", "entregue", "lancado_folha"):
        raise HTTPException(400, "Status invalido")
    c = _con()
    try:
        p = c.execute("SELECT * FROM rh_slcoin_resgates WHERE id=?", (pedido_id,)).fetchone()
        if not p:
            raise HTTPException(404, "Pedido nao encontrado")
        c.execute("UPDATE rh_slcoin_resgates SET status=?, obs=?, competencia_folha=?, "
                  "decidido_por=?, decidido_em=? WHERE id=?",
                  (dados.status, (dados.obs or "")[:500], dados.competencia_folha,
                   getattr(user, "nome", None) or user.email, int(time.time()), pedido_id))
        c.commit()
        pendente = dados.status == "entregue" and not _tem(c, "slcoin_extrato")
        return {"ok": True, "status": dados.status,
                "debito_pendente": pendente,
                "aviso": ("A entrega foi registrada, mas o extrato do Comercial ainda não existe: "
                          "o débito das moedas fica pendente." if pendente else None)}
    finally:
        c.close()


class ItemVitrine(BaseModel):
    item: str
    patamar: str
    custo_moedas: int
    valor_referencia: Optional[float] = None
    ativo: int = 1


@router.get("/vitrine")
def ver_vitrine(user=Depends(usuario_atual)):
    ""
    c = _con()
    try:
        linhas = [dict(r) for r in c.execute(
            "SELECT * FROM rh_slcoin_vitrine ORDER BY custo_moedas")]
        if not _admin(user):
            linhas = [l for l in linhas if l.get("ativo")]
            for l in linhas:
                l["limite"] = l.pop("valor_referencia", None)
                l.pop("atualizado_por", None)
        return {"vitrine": linhas}
    finally:
        c.close()


@router.post("/vitrine/{vitrine_id}")
def editar_vitrine(vitrine_id: int, dados: ItemVitrine, user=Depends(usuario_atual)):
    if not _admin(user):
        raise HTTPException(403, "Acao restrita a administradores")
    if dados.patamar not in PATAMARES:
        raise HTTPException(400, "Patamar invalido")
    if dados.custo_moedas < 1:
        raise HTTPException(400, "O custo em Coin precisa ser maior que zero")
    c = _con()
    try:
        if not c.execute("SELECT 1 FROM rh_slcoin_vitrine WHERE id=?", (vitrine_id,)).fetchone():
            raise HTTPException(404, "Experiencia nao encontrada")
        c.execute("UPDATE rh_slcoin_vitrine SET item=?, patamar=?, custo_moedas=?, "
                  "valor_referencia=?, ativo=?, atualizado_por=?, atualizado_em=? WHERE id=?",
                  (dados.item.strip()[:120], dados.patamar, dados.custo_moedas,
                   dados.valor_referencia, 1 if dados.ativo else 0,
                   getattr(user, "nome", None) or user.email, int(time.time()), vitrine_id))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


class DecisaoDesejo(BaseModel):
    status: str
    custo_moedas: Optional[int] = None
    valor_referencia: Optional[float] = None
    patamar: Optional[str] = None


@router.post("/desejos/{desejo_id}/decidir")
def decidir_desejo(desejo_id: int, dados: DecisaoDesejo, user=Depends(usuario_atual)):
    ""
    if not _admin(user):
        raise HTTPException(403, "Acao restrita a administradores")
    if dados.status not in ("virou_vitrine", "arquivado"):
        raise HTTPException(400, "Status invalido")
    c = _con()
    try:
        d = c.execute("SELECT * FROM rh_slcoin_desejos WHERE id=?", (desejo_id,)).fetchone()
        if not d:
            raise HTTPException(404, "Desejo nao encontrado")
        vid = None
        if dados.status == "virou_vitrine":
            if not dados.custo_moedas:
                raise HTTPException(400, "Diga quantos Coins custa")
            patamar = dados.patamar or _patamar_do_custo(dados.custo_moedas)
            cur = c.execute(
                "INSERT INTO rh_slcoin_vitrine (item, descricao, patamar, custo_moedas, "
                "valor_referencia, ativo, ordem, atualizado_por, atualizado_em) "
                "VALUES (?,?,?,?,?,1,99,?,?)",
                (d["desejo"][:120], "Pedido da equipe", patamar, dados.custo_moedas,
                 dados.valor_referencia, getattr(user, "nome", None) or user.email, int(time.time())))
            vid = cur.lastrowid
        c.execute("UPDATE rh_slcoin_desejos SET status=?, decidido_por=?, decidido_em=?, "
                  "vitrine_id=? WHERE id=?",
                  (dados.status, getattr(user, "nome", None) or user.email, int(time.time()),
                   vid, desejo_id))
        c.commit()
        return {"ok": True, "vitrine_id": vid}
    finally:
        c.close()


def _patamar_do_custo(custo):
    for nome, moedas, _ in ESCADA:
        if custo <= moedas * 1.15:
            return nome
    return ESCADA[-1][0]


def precificar(valor):
    ""
    valor = float(valor or 0)
    if valor <= 0:
        return None
    for nome, moedas, limite in ESCADA:
        if valor <= limite:
            custo = moedas * (valor / limite)
            return {"patamar": nome, "custo_moedas": max(10, int(round(custo / 10.0)) * 10),
                    "valor": round(valor, 2), "limite_do_patamar": limite}
    nome, moedas, limite = ESCADA[-1]
    return {"patamar": nome, "custo_moedas": int(round(moedas * valor / limite / 10.0)) * 10,
            "valor": round(valor, 2), "limite_do_patamar": limite, "acima_da_escada": True}


@router.get("/precificar")
def ver_preco(valor: float = Query(..., description="quanto a experiencia custa em reais"),
              user=Depends(usuario_atual)):
    ""
    if not _admin(user):
        raise HTTPException(403, "Acao restrita a administradores")
    p = precificar(valor)
    if not p:
        raise HTTPException(400, "Informe o valor em reais")
    return p


@router.post("/desejo")
async def pedir_desejo(desejo: str = Form(...), link: Optional[str] = Form(None),
                       obs: Optional[str] = Form(None), foto: Optional[UploadFile] = File(None),
                       user=Depends(usuario_atual)):
    ""
    texto = (desejo or "").strip()
    if len(texto) < 3:
        raise HTTPException(400, "Escreva o que voce gostaria de viver")
    destino = None
    if foto is not None and foto.filename:
        ext = (foto.filename.rsplit(".", 1)[-1] or "").lower()
        if ext not in FOTO_EXT:
            raise HTTPException(400, "Mande imagem (jpg, png, webp, heic) ou PDF")
        bruto = await foto.read()
        if len(bruto) > FOTO_MAX:
            raise HTTPException(400, "Arquivo grande demais: o limite e 8 MB")
        if not bruto:
            raise HTTPException(400, "O arquivo chegou vazio")
    c = _con()
    try:
        _coluna_foto(c)
        f = _ficha(c, user)
        if foto is not None and foto.filename:
            pasta = FOTOS / str(f["id"])
            pasta.mkdir(parents=True, exist_ok=True)
            destino = str(pasta / ("%s.%s" % (uuid.uuid4().hex, ext)))
            with open(destino, "wb") as saida:
                saida.write(bruto)
            os.chmod(destino, 0o600)
        c.execute("INSERT INTO rh_slcoin_desejos (colaborador_id, desejo, link, obs, foto, "
                  "criado_em) VALUES (?,?,?,?,?,?)",
                  (f["id"], texto[:200], (link or "")[:400] or None,
                   (obs or "")[:400] or None, destino, int(time.time())))
        c.commit()
        return {"ok": True, "com_foto": bool(destino),
                "mensagem": "Anotado. Se entrar na vitrine, voce vai ver por aqui."}
    finally:
        c.close()


@router.get("/desejos/{desejo_id}/foto")
def foto_do_desejo(desejo_id: int, user=Depends(usuario_atual)):
    ""
    c = _con()
    try:
        _coluna_foto(c)
        d = c.execute("SELECT colaborador_id, foto, desejo FROM rh_slcoin_desejos WHERE id=?",
                      (desejo_id,)).fetchone()
        if not d or not d["foto"]:
            raise HTTPException(404, "Este desejo nao tem foto")
        if not _admin(user):
            f = _ficha(c, user)
            if f["id"] != d["colaborador_id"]:
                raise HTTPException(403, "Esta foto nao e sua")
        if not os.path.exists(d["foto"]):
            raise HTTPException(404, "O arquivo nao esta mais no servidor")
        return FileResponse(d["foto"], filename=os.path.basename(d["foto"]))
    finally:
        c.close()


@router.get("/desejos")
def listar_desejos(user=Depends(usuario_atual)):
    ""
    c = _con()
    try:
        _coluna_foto(c)
        if _admin(user):
            linhas = [dict(r) for r in c.execute(
                "SELECT d.*, col.nome FROM rh_slcoin_desejos d "
                "JOIN rh_colaboradores col ON col.id=d.colaborador_id "
                "ORDER BY CASE WHEN d.status='sugerido' THEN 0 ELSE 1 END, d.id DESC LIMIT 60")]
            for l in linhas:
                l["tem_foto"] = bool(l.pop("foto", None))
            return {"desejos": linhas, "escada": [
                {"patamar": n, "custo_moedas": m, "limite": l} for n, m, l in ESCADA]}
        f = _ficha(c, user)
        return {"desejos": [dict(r) for r in c.execute(
            "SELECT id, desejo, link, status, criado_em, (foto IS NOT NULL) AS tem_foto "
            "FROM rh_slcoin_desejos WHERE colaborador_id=? ORDER BY id DESC LIMIT 20",
            (f["id"],))]}
    finally:
        c.close()


@router.post("/ciencia")
def ciencia(request: Request, user=Depends(usuario_atual)):
    ""
    c = _con()
    try:
        f = _ficha(c, user)
        c.execute("INSERT OR IGNORE INTO rh_leitura (colaborador_id, documento, versao, lido_em, "
                  "ip, origem) VALUES (?,?,?,?,?,?)",
                  (f["id"], REGULAMENTO, VERSAO, int(time.time()),
                   (request.client.host if request.client else None), "portal_colaborador"))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.get("/ciencia")
def ciencia_quem(user=Depends(usuario_atual)):
    ""
    c = _con()
    try:
        if _admin(user):
            linhas = [dict(r) for r in c.execute(
                "SELECT l.colaborador_id, col.nome, l.versao, l.lido_em FROM rh_leitura l "
                "JOIN rh_colaboradores col ON col.id=l.colaborador_id WHERE l.documento=? "
                "ORDER BY l.lido_em DESC", (REGULAMENTO,))]
            return {"leituras": linhas}
        f = _ficha(c, user)
        r = c.execute("SELECT versao, lido_em FROM rh_leitura WHERE colaborador_id=? AND documento=?",
                      (f["id"], REGULAMENTO)).fetchone()
        return {"leu": bool(r), "versao": r["versao"] if r else None,
                "lido_em": r["lido_em"] if r else None}
    finally:
        c.close()
