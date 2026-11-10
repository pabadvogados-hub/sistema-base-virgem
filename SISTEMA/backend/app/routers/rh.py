""
import os
import re
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..deps import usuario_atual

router = APIRouter(prefix="/api/rh", tags=["rh"])

_DB = str(Path(__file__).resolve().parents[2] / "sistema_esc.db")
ARQUIVOS = Path(os.getenv("RH_ARQUIVOS_DIR", "/var/lib/esc_rh"))
MAX_ARQUIVO = 15 * 1024 * 1024
EXT_OK = {"pdf", "jpg", "jpeg", "png", "webp", "heic", "doc", "docx"}

DOMINIO_EQUIPE = (os.getenv("RH_DOMINIO_EQUIPE") or "").strip().lower()
ROBOS = tuple(x.strip().lower() for x in os.getenv("RH_CONTAS_ROBO", "robo@,automacao@").split(",") if x.strip())

STATUS = ("admissao", "ativo", "afastado", "desligado")
CONTRATOS = ("CLT", "Associado", "Estagio", "Aprendiz", "Prestador", "PJ", "Socio", "Autonomo")
TIPOS_DOC = ("RG/CNH", "CPF", "CTPS", "Comprovante de endereco", "Titulo de eleitor", "Certidao",
             "Foto", "ASO", "Contrato de trabalho", "Termo LGPD", "Holerite", "Atestado", "Outro")
TIPOS_AUSENCIA = ("ferias", "folga", "atestado", "falta", "outro")

ADMISSAO = [
    ("docs_pessoais", "Documentos pessoais (RG ou CNH e CPF)"),
    ("ctps", "Carteira de trabalho digital e PIS"),
    ("endereco", "Comprovante de endereço"),
    ("banco", "Dados bancários ou PIX"),
    ("foto", "Foto 3x4"),
    ("aso", "ASO admissional (exame)"),
    ("contabilidade", "Dados enviados à contabilidade"),
    ("contrato", "Contrato de trabalho assinado"),
    ("lgpd", "Termo de confidencialidade e LGPD assinado"),
    ("esocial", "Registro no eSocial (contabilidade)"),
    ("acessos", "Acessos criados (e-mail e sistema)"),
]

_DOCS = ("docs_pessoais", "Documentos pessoais (RG ou CNH e CPF)")
_ENDERECO = ("endereco", "Comprovante de endereço")
_LGPD = ("lgpd", "Termo de confidencialidade e LGPD assinado")
_ACESSOS = ("acessos", "Acessos criados (e-mail e sistema)")
_PRESTADOR = [
    ("docs_pessoais", "Documentos pessoais (RG ou CNH e CPF) do responsável"),
    ("cnpj", "CNPJ ou MEI ativo (cartão CNPJ); autônomo: cadastro para RPA"),
    _ENDERECO,
    ("banco", "Dados bancários ou PIX (em nome do CNPJ, quando houver)"),
    ("contrato", "Contrato de prestação de serviços assinado"),
    ("nota_fiscal", "Combinado de nota fiscal ou RPA para cada pagamento"),
    _LGPD, _ACESSOS,
]
ADMISSAO_POR_VINCULO = {
    "CLT": ADMISSAO,
    "Estagio": [
        _DOCS,
        ("matricula", "Declaração de matrícula da faculdade (semestre atual)"),
        _ENDERECO,
        ("banco", "Dados bancários ou PIX (bolsa-estágio)"),
        ("tce", "Termo de compromisso de estágio assinado (estagiário, escritório e faculdade)"),
        ("plano_atividades", "Plano de atividades anexo ao termo de compromisso"),
        ("supervisor", "Advogado supervisor definido (até 10 estagiários por supervisor)"),
        ("seguro", "Seguro contra acidentes pessoais contratado"),
        ("oab_estagiario", "Inscrição de estagiário na OAB (quando houver)"),
        ("contabilidade", "Dados enviados à contabilidade (bolsa, auxílio-transporte e seguro)"),
        ("esocial", "Cadastro no eSocial como estagiário (S-2300, pela contabilidade)"),
        _LGPD, _ACESSOS,
    ],
    "Aprendiz": [
        _DOCS,
        ("ctps", "Carteira de trabalho digital e PIS"),
        _ENDERECO,
        ("banco", "Dados bancários ou PIX"),
        ("foto", "Foto 3x4"),
        ("aso", "ASO admissional (exame)"),
        ("escola", "Matrícula e frequência escolar (se não concluiu o ensino médio)"),
        ("curso_aprendizagem", "Matrícula no programa de aprendizagem (entidade qualificada, ex.: SENAC ou CIEE)"),
        ("responsavel", "Assinatura do responsável legal (se menor de 18 anos)"),
        ("contabilidade", "Dados enviados à contabilidade"),
        ("contrato", "Contrato de aprendizagem assinado (prazo determinado, até 2 anos)"),
        _LGPD,
        ("esocial", "Registro no eSocial como aprendiz (contabilidade)"),
        _ACESSOS,
    ],
    "Associado": [
        _DOCS,
        ("oab", "Carteira da OAB e certidão de regularidade"),
        _ENDERECO,
        ("banco", "Dados bancários ou PIX (participação nos resultados)"),
        ("contrato", "Contrato de associação assinado (sem vínculo de emprego)"),
        ("averbacao_oab", "Contrato de associação averbado no registro da sociedade na OAB"),
        ("contabilidade", "Forma de pagamento combinada com a contabilidade (RPA ou nota fiscal)"),
        _LGPD, _ACESSOS,
    ],
    "Prestador": _PRESTADOR,
    "PJ": _PRESTADOR,
    "Autonomo": _PRESTADOR,
    "Socio": [
        _DOCS,
        ("oab", "Carteira da OAB (sócio advogado)"),
        ("contrato", "Contrato social ou alteração com o sócio, registrado na OAB"),
        ("banco", "Conta para pró-labore e distribuição de lucros"),
        _ACESSOS,
    ],
}
ITENS = {k for roteiro in ADMISSAO_POR_VINCULO.values() for k, _ in roteiro}


def _roteiro(tipo_contrato):
    ""
    return ADMISSAO_POR_VINCULO.get(tipo_contrato or "CLT", ADMISSAO)

CAMPOS_PROPRIOS = ("telefone", "email", "endereco", "banco", "pix", "contato_emergencia")
CAMPOS = ("nome", "email", "telefone", "cpf", "rg", "nascimento", "endereco", "cargo", "setor",
          "tipo_contrato", "salario", "jornada", "data_admissao", "data_desligamento", "status",
          "banco", "pix", "contato_emergencia", "obs", "usuario_id")


def _con():
    c = sqlite3.connect(_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


DDL = [
    """CREATE TABLE IF NOT EXISTS rh_colaboradores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER UNIQUE,
        nome TEXT NOT NULL, email TEXT, telefone TEXT, cpf TEXT, rg TEXT, nascimento TEXT,
        endereco TEXT, cargo TEXT, setor TEXT, tipo_contrato TEXT DEFAULT 'CLT',
        salario REAL, jornada TEXT, data_admissao TEXT, data_desligamento TEXT,
        status TEXT DEFAULT 'ativo', banco TEXT, pix TEXT, contato_emergencia TEXT, obs TEXT,
        criado_em REAL, atualizado_em REAL)""",
    """CREATE TABLE IF NOT EXISTS rh_documentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, colaborador_id INTEGER NOT NULL,
        tipo TEXT, nome TEXT, arquivo TEXT, tamanho INTEGER, enviado_por TEXT, enviado_em REAL)""",
    """CREATE TABLE IF NOT EXISTS rh_admissao (
        id INTEGER PRIMARY KEY AUTOINCREMENT, colaborador_id INTEGER NOT NULL, item TEXT NOT NULL,
        status TEXT DEFAULT 'pendente', obs TEXT, atualizado_por TEXT, atualizado_em REAL,
        UNIQUE (colaborador_id, item))""",
    """CREATE TABLE IF NOT EXISTS rh_ponto (
        id INTEGER PRIMARY KEY AUTOINCREMENT, colaborador_id INTEGER NOT NULL,
        tipo TEXT NOT NULL, ts REAL NOT NULL, dia TEXT NOT NULL, origem TEXT DEFAULT 'web',
        ip TEXT, obs TEXT, criado_por TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_rh_ponto_dia ON rh_ponto (colaborador_id, dia)",
    """CREATE TABLE IF NOT EXISTS rh_ausencias (
        id INTEGER PRIMARY KEY AUTOINCREMENT, colaborador_id INTEGER NOT NULL,
        tipo TEXT NOT NULL, inicio TEXT NOT NULL, fim TEXT NOT NULL, dias INTEGER,
        motivo TEXT, status TEXT DEFAULT 'pendente', documento_id INTEGER,
        decidido_por TEXT, decidido_em REAL, obs_decisao TEXT, criado_por TEXT, criado_em REAL)""",
]


def _schema(c):
    for d in DDL:
        c.execute(d)


def _admin(user):
    return (user.papel or "") == "admin"


def _agora():
    return time.time()


def _hoje():
    return date.today().isoformat()


def _data_ok(s, campo):
    if s in (None, ""):
        return None
    try:
        return date.fromisoformat(str(s)[:10]).isoformat()
    except ValueError:
        raise HTTPException(400, f"{campo}: data invalida, use AAAA-MM-DD")


def _semear(c):
    ""
    if c.execute("SELECT COUNT(*) FROM rh_colaboradores").fetchone()[0]:
        return
    for u in c.execute("SELECT id, nome, email, papel FROM usuarios WHERE ativo=1 ORDER BY id").fetchall():
        email = (u["email"] or "").lower()
        if any(email.startswith(r) for r in ROBOS) or (DOMINIO_EQUIPE and not email.endswith(DOMINIO_EQUIPE)):
            continue
        c.execute("INSERT OR IGNORE INTO rh_colaboradores (usuario_id, nome, email, tipo_contrato, status, "
                  "criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?)",
                  (u["id"], u["nome"], u["email"], "Socio" if u["papel"] == "admin" else None, "ativo",
                   _agora(), _agora()))
    c.commit()


def _meu(c, user, criar=True):
    r = c.execute("SELECT * FROM rh_colaboradores WHERE usuario_id=?", (user.id,)).fetchone()
    if not r and user.email:
        r = c.execute("SELECT * FROM rh_colaboradores WHERE lower(email)=? AND usuario_id IS NULL",
                      (user.email.lower(),)).fetchone()
        if r:
            c.execute("UPDATE rh_colaboradores SET usuario_id=? WHERE id=?", (user.id, r["id"]))
            c.commit()
            r = c.execute("SELECT * FROM rh_colaboradores WHERE id=?", (r["id"],)).fetchone()
    if not r and criar:
        email = (user.email or "").lower()
        if any(email.startswith(x) for x in ROBOS):
            raise HTTPException(403, "Conta de automacao nao tem ficha de RH")
        if (DOMINIO_EQUIPE and not email.endswith(DOMINIO_EQUIPE)):
            raise HTTPException(403, "Seu login ainda nao tem ficha de RH. Fale com a administracao.")
        c.execute("INSERT INTO rh_colaboradores (usuario_id, nome, email, status, criado_em, atualizado_em) "
                  "VALUES (?,?,?,?,?,?)", (user.id, user.nome or user.email, user.email, "ativo", _agora(), _agora()))
        c.commit()
        r = c.execute("SELECT * FROM rh_colaboradores WHERE usuario_id=?", (user.id,)).fetchone()
    return r


def _colab(c, user, cid):
    ""
    r = c.execute("SELECT * FROM rh_colaboradores WHERE id=?", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "Colaborador nao encontrado")
    if not _admin(user) and r["usuario_id"] != user.id:
        raise HTTPException(403, "Voce so pode ver a sua propria ficha")
    return r


def _marcas_do_dia(c, cid, dia):
    return [dict(x) for x in c.execute(
        "SELECT id, tipo, ts, obs, origem FROM rh_ponto WHERE colaborador_id=? AND dia=? ORDER BY ts", (cid, dia))]


def _minutos(marcas, ate=None):
    ""
    total, aberta = 0.0, None
    for m in marcas:
        if m["tipo"] == "entrada":
            aberta = m["ts"]
        elif m["tipo"] == "saida" and aberta is not None:
            total += max(0, m["ts"] - aberta)
            aberta = None
    if aberta is not None and ate:
        total += max(0, ate - aberta)
    return int(total // 60)


def _situacao_hoje(marcas):
    if not marcas:
        return "sem_registro"
    return "trabalhando" if marcas[-1]["tipo"] == "entrada" else "saiu"


def _ausente_hoje(c, cid):
    h = _hoje()
    return c.execute("SELECT tipo FROM rh_ausencias WHERE colaborador_id=? AND status='aprovada' "
                     "AND inicio<=? AND fim>=? LIMIT 1", (cid, h, h)).fetchone()


def _resumo_colab(c, r):
    d = dict(r)
    cid = r["id"]
    roteiro = {k for k, _ in _roteiro(r["tipo_contrato"])}
    feitos = sum(1 for (item,) in c.execute("SELECT item FROM rh_admissao WHERE colaborador_id=? "
                                            "AND status IN ('ok','na')", (cid,)) if item in roteiro)
    d["admissao_feitos"], d["admissao_total"] = feitos, len(roteiro)
    marcas = _marcas_do_dia(c, cid, _hoje())
    d["ponto_hoje"] = _situacao_hoje(marcas)
    d["minutos_hoje"] = _minutos(marcas, ate=_agora())
    aus = _ausente_hoje(c, cid)
    d["ausente_hoje"] = aus["tipo"] if aus else None
    d["pedidos_pendentes"] = c.execute("SELECT COUNT(*) FROM rh_ausencias WHERE colaborador_id=? AND status='pendente'",
                                       (cid,)).fetchone()[0]
    d["documentos"] = c.execute("SELECT COUNT(*) FROM rh_documentos WHERE colaborador_id=?", (cid,)).fetchone()[0]
    return d


@router.get("/eu")
def eu(user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        if _admin(user):
            _semear(c)
        r = _meu(c, user)
        return {"admin": _admin(user), "colaborador": _resumo_colab(c, r) if r else None,
                "admissao_itens": [{"item": k, "rotulo": v} for k, v in ADMISSAO],
                "admissao_por_vinculo": {t: [{"item": k, "rotulo": v} for k, v in rot]
                                         for t, rot in ADMISSAO_POR_VINCULO.items()},
                "tipos_documento": TIPOS_DOC, "tipos_ausencia": TIPOS_AUSENCIA,
                "status": STATUS, "contratos": CONTRATOS}
    finally:
        c.close()


@router.get("/resumo")
def resumo(user=Depends(usuario_atual)):
    ""
    c = _con()
    try:
        _schema(c)
        if _admin(user):
            _semear(c)
            rows = c.execute("SELECT * FROM rh_colaboradores WHERE status!='desligado'").fetchall()
        else:
            r = _meu(c, user)
            rows = [r] if r else []
        cs = [_resumo_colab(c, r) for r in rows]
        ids = [x["id"] for x in cs] or [0]
        marca = ",".join("?" for _ in ids)
        pend = c.execute(f"SELECT COUNT(*) FROM rh_ausencias WHERE status='pendente' AND colaborador_id IN ({marca})",
                         ids).fetchone()[0]
        return {
            "ativos": sum(1 for x in cs if x["status"] == "ativo"),
            "em_admissao": sum(1 for x in cs if x["status"] == "admissao"),
            "afastados": sum(1 for x in cs if x["status"] == "afastado"),
            "trabalhando": sum(1 for x in cs if x["ponto_hoje"] == "trabalhando"),
            "sairam": sum(1 for x in cs if x["ponto_hoje"] == "saiu"),
            "sem_registro": sum(1 for x in cs if x["ponto_hoje"] == "sem_registro" and not x["ausente_hoje"]),
            "ausentes_hoje": sum(1 for x in cs if x["ausente_hoje"]),
            "pedidos_pendentes": pend,
        }
    finally:
        c.close()


class ColabIn(BaseModel):
    nome: Optional[str] = None
    email: Optional[str] = None
    telefone: Optional[str] = None
    cpf: Optional[str] = None
    rg: Optional[str] = None
    nascimento: Optional[str] = None
    endereco: Optional[str] = None
    cargo: Optional[str] = None
    setor: Optional[str] = None
    tipo_contrato: Optional[str] = None
    salario: Optional[float] = None
    jornada: Optional[str] = None
    data_admissao: Optional[str] = None
    data_desligamento: Optional[str] = None
    status: Optional[str] = None
    banco: Optional[str] = None
    pix: Optional[str] = None
    contato_emergencia: Optional[str] = None
    obs: Optional[str] = None
    usuario_id: Optional[int] = None


def _limpar(campos):
    for k in ("nascimento", "data_admissao", "data_desligamento"):
        if k in campos:
            campos[k] = _data_ok(campos[k], k)
    if campos.get("status") and campos["status"] not in STATUS:
        raise HTTPException(400, f"status invalido: use {', '.join(STATUS)}")
    if campos.get("tipo_contrato") and campos["tipo_contrato"] not in CONTRATOS:
        raise HTTPException(400, f"contrato invalido: use {', '.join(CONTRATOS)}")
    if campos.get("cpf"):
        d = re.sub(r"\D", "", campos["cpf"])
        if len(d) != 11:
            raise HTTPException(400, "CPF precisa ter 11 digitos")
        campos["cpf"] = f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return campos


@router.get("/colaboradores")
def listar(status: Optional[str] = None, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        if _admin(user):
            _semear(c)
            sql, args = "SELECT * FROM rh_colaboradores", []
            if status and status != "todos":
                sql += " WHERE status=?"
                args.append(status)
            rows = c.execute(sql + " ORDER BY status='desligado', nome COLLATE NOCASE", args).fetchall()
        else:
            r = _meu(c, user)
            rows = [r] if r else []
        return {"colaboradores": [_resumo_colab(c, r) for r in rows]}
    finally:
        c.close()


@router.get("/colaboradores/{cid}")
def ver(cid: int, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        return _resumo_colab(c, _colab(c, user, cid))
    finally:
        c.close()


@router.post("/colaboradores")
def criar(dados: ColabIn, user=Depends(usuario_atual)):
    if not _admin(user):
        raise HTTPException(403, "So administrador cadastra colaborador")
    campos = _limpar(dados.model_dump(exclude_none=True))
    if not (campos.get("nome") or "").strip():
        raise HTTPException(400, "Informe o nome")
    campos.setdefault("status", "admissao")
    c = _con()
    try:
        _schema(c)
        campos["criado_em"] = campos["atualizado_em"] = _agora()
        cols = ", ".join(campos)
        try:
            cur = c.execute(f"INSERT INTO rh_colaboradores ({cols}) VALUES ({', '.join('?' for _ in campos)})",
                            list(campos.values()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Esse login ja esta ligado a outro colaborador")
        c.commit()
        return _resumo_colab(c, c.execute("SELECT * FROM rh_colaboradores WHERE id=?", (cur.lastrowid,)).fetchone())
    finally:
        c.close()


@router.patch("/colaboradores/{cid}")
def editar(cid: int, dados: ColabIn, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        _colab(c, user, cid)
        campos = dados.model_dump(exclude_unset=True)
        if not _admin(user):
            proibidos = [k for k in campos if k not in CAMPOS_PROPRIOS]
            if proibidos:
                raise HTTPException(403, "Na sua ficha voce so muda contato, endereco e banco")
        campos = _limpar(campos)
        if not campos:
            return _resumo_colab(c, c.execute("SELECT * FROM rh_colaboradores WHERE id=?", (cid,)).fetchone())
        campos["atualizado_em"] = _agora()
        try:
            c.execute("UPDATE rh_colaboradores SET " + ", ".join(k + "=?" for k in campos) + " WHERE id=?",
                      list(campos.values()) + [cid])
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Esse login ja esta ligado a outro colaborador")
        c.commit()
        return _resumo_colab(c, c.execute("SELECT * FROM rh_colaboradores WHERE id=?", (cid,)).fetchone())
    finally:
        c.close()


@router.get("/usuarios-livres")
def usuarios_livres(user=Depends(usuario_atual)):
    ""
    if not _admin(user):
        raise HTTPException(403, "So administrador")
    c = _con()
    try:
        _schema(c)
        rows = c.execute("SELECT id, nome, email FROM usuarios WHERE ativo=1 AND id NOT IN "
                         "(SELECT usuario_id FROM rh_colaboradores WHERE usuario_id IS NOT NULL) ORDER BY nome").fetchall()
        return {"usuarios": [dict(r) for r in rows if not any((r["email"] or "").lower().startswith(x) for x in ROBOS)]}
    finally:
        c.close()


class ItemIn(BaseModel):
    status: str
    obs: Optional[str] = None


@router.get("/colaboradores/{cid}/admissao")
def admissao(cid: int, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        col = _colab(c, user, cid)
        feitos = {r["item"]: dict(r) for r in c.execute("SELECT * FROM rh_admissao WHERE colaborador_id=?", (cid,))}
        return {"vinculo": col["tipo_contrato"] or "CLT",
                "itens": [{"item": k, "rotulo": v, "status": (feitos.get(k) or {}).get("status", "pendente"),
                           "obs": (feitos.get(k) or {}).get("obs"),
                           "atualizado_por": (feitos.get(k) or {}).get("atualizado_por"),
                           "atualizado_em": (feitos.get(k) or {}).get("atualizado_em")}
                          for k, v in _roteiro(col["tipo_contrato"])]}
    finally:
        c.close()


@router.patch("/colaboradores/{cid}/admissao/{item}")
def marcar_item(cid: int, item: str, dados: ItemIn, user=Depends(usuario_atual)):
    if not _admin(user):
        raise HTTPException(403, "So administrador marca a admissao")
    if item not in ITENS:
        raise HTTPException(404, "Item de admissao desconhecido")
    if dados.status not in ("pendente", "ok", "na"):
        raise HTTPException(400, "status: pendente, ok ou na")
    c = _con()
    try:
        _schema(c)
        col = _colab(c, user, cid)
        if item not in {k for k, _ in _roteiro(col["tipo_contrato"])}:
            raise HTTPException(404, "Esse item nao faz parte da admissao deste vinculo")
        c.execute("INSERT INTO rh_admissao (colaborador_id, item, status, obs, atualizado_por, atualizado_em) "
                  "VALUES (?,?,?,?,?,?) ON CONFLICT(colaborador_id, item) DO UPDATE SET status=excluded.status, "
                  "obs=COALESCE(excluded.obs, rh_admissao.obs), atualizado_por=excluded.atualizado_por, "
                  "atualizado_em=excluded.atualizado_em",
                  (cid, item, dados.status, dados.obs, user.nome or user.email, _agora()))
        c.commit()
        return admissao(cid, user)
    finally:
        c.close()


@router.get("/colaboradores/{cid}/documentos")
def documentos(cid: int, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        _colab(c, user, cid)
        return {"documentos": [dict(r) for r in c.execute(
            "SELECT id, tipo, nome, tamanho, enviado_por, enviado_em FROM rh_documentos WHERE colaborador_id=? "
            "ORDER BY enviado_em DESC", (cid,))]}
    finally:
        c.close()


@router.post("/colaboradores/{cid}/documentos")
async def enviar_documento(cid: int, arquivo: UploadFile = File(...), tipo: str = Form("Outro"),
                           user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        _colab(c, user, cid)
        nome = os.path.basename(arquivo.filename or "documento")
        ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
        if ext not in EXT_OK:
            raise HTTPException(400, "Formato nao aceito: envie PDF, foto (JPG/PNG) ou Word")
        raw = await arquivo.read()
        if len(raw) > MAX_ARQUIVO:
            raise HTTPException(400, "Arquivo maior que 15 MB")
        pasta = ARQUIVOS / str(cid)
        pasta.mkdir(parents=True, exist_ok=True)
        os.chmod(ARQUIVOS, 0o700)
        destino = pasta / f"{uuid.uuid4().hex}.{ext}"
        destino.write_bytes(raw)
        os.chmod(destino, 0o600)
        cur = c.execute("INSERT INTO rh_documentos (colaborador_id, tipo, nome, arquivo, tamanho, enviado_por, enviado_em) "
                        "VALUES (?,?,?,?,?,?,?)", (cid, tipo if tipo in TIPOS_DOC else "Outro", nome,
                                                   str(destino), len(raw), user.nome or user.email, _agora()))
        c.commit()
        return {"ok": True, "id": cur.lastrowid}
    finally:
        c.close()


@router.get("/documentos/{did}/arquivo")
def baixar_documento(did: int, user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        d = c.execute("SELECT * FROM rh_documentos WHERE id=?", (did,)).fetchone()
        if not d:
            raise HTTPException(404, "Documento nao encontrado")
        _colab(c, user, d["colaborador_id"])
        caminho = Path(d["arquivo"])
        if not caminho.is_file() or ARQUIVOS not in caminho.resolve().parents:
            raise HTTPException(404, "Arquivo nao esta mais no servidor")
        return FileResponse(str(caminho), filename=d["nome"])
    finally:
        c.close()


@router.delete("/documentos/{did}")
def apagar_documento(did: int, user=Depends(usuario_atual)):
    if not _admin(user):
        raise HTTPException(403, "So administrador apaga documento")
    c = _con()
    try:
        _schema(c)
        d = c.execute("SELECT * FROM rh_documentos WHERE id=?", (did,)).fetchone()
        if not d:
            raise HTTPException(404, "Documento nao encontrado")
        try:
            p = Path(d["arquivo"])
            if ARQUIVOS in p.resolve().parents:
                p.unlink(missing_ok=True)
        except Exception:
            pass
        c.execute("DELETE FROM rh_documentos WHERE id=?", (did,))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


class AjusteIn(BaseModel):
    colaborador_id: int
    tipo: str
    dia: str
    hora: str
    obs: Optional[str] = None


@router.post("/ponto/bater")
def bater_ponto(request: Request, user=Depends(usuario_atual)):
    ""
    c = _con()
    try:
        _schema(c)
        r = _meu(c, user)
        if r["status"] == "desligado":
            raise HTTPException(403, "Colaborador desligado nao bate ponto")
        hoje = _hoje()
        marcas = _marcas_do_dia(c, r["id"], hoje)
        if marcas and _agora() - marcas[-1]["ts"] < 60:
            raise HTTPException(409, "Voce acabou de bater o ponto. Espere um minuto.")
        tipo = "saida" if marcas and marcas[-1]["tipo"] == "entrada" else "entrada"
        ip = (request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for") or
              (request.client.host if request.client else ""))[:60]
        c.execute("INSERT INTO rh_ponto (colaborador_id, tipo, ts, dia, origem, ip, criado_por) VALUES (?,?,?,?,?,?,?)",
                  (r["id"], tipo, _agora(), hoje, "web", ip, user.email))
        c.commit()
        return {"ok": True, "tipo": tipo, "hoje": _marcas_do_dia(c, r["id"], hoje)}
    finally:
        c.close()


@router.get("/ponto")
def ponto_mes(colaborador_id: Optional[int] = None, mes: Optional[str] = None, user=Depends(usuario_atual)):
    ""
    c = _con()
    try:
        _schema(c)
        r = _colab(c, user, colaborador_id) if colaborador_id else _meu(c, user)
        mes = (mes or _hoje()[:7])[:7]
        if not re.fullmatch(r"\d{4}-\d{2}", mes):
            raise HTTPException(400, "mes: use AAAA-MM")
        rows = [dict(x) for x in c.execute(
            "SELECT id, tipo, ts, dia, obs, origem, criado_por FROM rh_ponto WHERE colaborador_id=? AND dia LIKE ? "
            "ORDER BY ts", (r["id"], mes + "-%"))]
        dias = {}
        for m in rows:
            dias.setdefault(m["dia"], []).append(m)
        hoje = _hoje()
        saida = []
        for d in sorted(dias):
            ms = dias[d]
            aberta = ms[-1]["tipo"] == "entrada"
            saida.append({"dia": d, "marcas": ms, "minutos": _minutos(ms, ate=_agora() if d == hoje else None),
                          "incompleto": aberta and d != hoje})
        total = sum(x["minutos"] for x in saida)
        desde = c.execute("SELECT MIN(dia) FROM rh_ponto WHERE colaborador_id=?", (r["id"],)).fetchone()[0]
        return {"colaborador": {"id": r["id"], "nome": r["nome"], "jornada": r["jornada"]}, "mes": mes, "desde": desde,
                "dias": saida, "minutos_mes": total, "dias_trabalhados": len(saida),
                "incompletos": sum(1 for x in saida if x["incompleto"])}
    finally:
        c.close()


@router.get("/ponto/hoje")
def ponto_hoje(user=Depends(usuario_atual)):
    ""
    c = _con()
    try:
        _schema(c)
        if _admin(user):
            _semear(c)
            rows = c.execute("SELECT * FROM rh_colaboradores WHERE status IN ('ativo','admissao') "
                             "ORDER BY nome COLLATE NOCASE").fetchall()
        else:
            r = _meu(c, user)
            rows = [r] if r else []
        out = []
        for r in rows:
            ms = _marcas_do_dia(c, r["id"], _hoje())
            aus = _ausente_hoje(c, r["id"])
            out.append({"id": r["id"], "nome": r["nome"], "cargo": r["cargo"], "marcas": ms,
                        "situacao": ("ausente:" + aus["tipo"]) if aus and not ms else _situacao_hoje(ms),
                        "minutos": _minutos(ms, ate=_agora())})
        return {"hoje": _hoje(), "equipe": out}
    finally:
        c.close()


@router.post("/ponto/ajuste")
def ajustar_ponto(dados: AjusteIn, user=Depends(usuario_atual)):
    ""
    if not _admin(user):
        raise HTTPException(403, "So administrador ajusta ponto")
    if dados.tipo not in ("entrada", "saida"):
        raise HTTPException(400, "tipo: entrada ou saida")
    dia = _data_ok(dados.dia, "dia")
    if not re.fullmatch(r"\d{2}:\d{2}", dados.hora or ""):
        raise HTTPException(400, "hora: use HH:MM")
    ts = datetime.fromisoformat(f"{dia}T{dados.hora}:00").timestamp()
    c = _con()
    try:
        _schema(c)
        _colab(c, user, dados.colaborador_id)
        c.execute("INSERT INTO rh_ponto (colaborador_id, tipo, ts, dia, origem, obs, criado_por) VALUES (?,?,?,?,?,?,?)",
                  (dados.colaborador_id, dados.tipo, ts, dia, "ajuste", (dados.obs or "ajuste do admin")[:300],
                   user.email))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.delete("/ponto/{pid}")
def apagar_marca(pid: int, user=Depends(usuario_atual)):
    if not _admin(user):
        raise HTTPException(403, "So administrador apaga marca de ponto")
    c = _con()
    try:
        _schema(c)
        c.execute("DELETE FROM rh_ponto WHERE id=?", (pid,))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


class AusenciaIn(BaseModel):
    tipo: str
    inicio: str
    fim: str
    motivo: Optional[str] = None
    colaborador_id: Optional[int] = None


class DecisaoIn(BaseModel):
    status: str
    obs: Optional[str] = None


@router.get("/ausencias")
def ausencias(status: Optional[str] = None, colaborador_id: Optional[int] = None, mes: Optional[str] = None,
              user=Depends(usuario_atual)):
    c = _con()
    try:
        _schema(c)
        sql = ("SELECT a.*, r.nome AS colaborador FROM rh_ausencias a JOIN rh_colaboradores r "
               "ON r.id=a.colaborador_id WHERE 1=1")
        args = []
        if not _admin(user):
            meu = _meu(c, user)
            sql += " AND a.colaborador_id=?"
            args.append(meu["id"])
        elif colaborador_id:
            sql += " AND a.colaborador_id=?"
            args.append(colaborador_id)
        if status and status != "todas":
            sql += " AND a.status=?"
            args.append(status)
        if mes and re.fullmatch(r"\d{4}-\d{2}", mes):
            ini = mes + "-01"
            fim = (date.fromisoformat(ini).replace(day=28) + timedelta(days=4)).replace(day=1).isoformat()
            sql += " AND a.inicio<? AND a.fim>=?"
            args += [fim, ini]
        rows = c.execute(sql + " ORDER BY a.status='pendente' DESC, a.inicio DESC", args).fetchall()
        return {"ausencias": [dict(r) for r in rows]}
    finally:
        c.close()


@router.post("/ausencias")
def pedir_ausencia(dados: AusenciaIn, user=Depends(usuario_atual)):
    if dados.tipo not in TIPOS_AUSENCIA:
        raise HTTPException(400, f"tipo: {', '.join(TIPOS_AUSENCIA)}")
    ini, fim = _data_ok(dados.inicio, "inicio"), _data_ok(dados.fim, "fim")
    if not ini or not fim or fim < ini:
        raise HTTPException(400, "Periodo invalido: o fim tem que ser igual ou depois do inicio")
    c = _con()
    try:
        _schema(c)
        if dados.colaborador_id and _admin(user):
            cid = _colab(c, user, dados.colaborador_id)["id"]
            status = "aprovada"
        else:
            cid = _meu(c, user)["id"]
            status = "pendente"
        dias = (date.fromisoformat(fim) - date.fromisoformat(ini)).days + 1
        cur = c.execute("INSERT INTO rh_ausencias (colaborador_id, tipo, inicio, fim, dias, motivo, status, "
                        "decidido_por, decidido_em, criado_por, criado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, dados.tipo, ini, fim, dias, (dados.motivo or "")[:500], status,
                         (user.nome or user.email) if status == "aprovada" else None,
                         _agora() if status == "aprovada" else None, user.email, _agora()))
        c.commit()
        return {"ok": True, "id": cur.lastrowid, "status": status, "dias": dias}
    finally:
        c.close()


@router.post("/ausencias/{aid}/decidir")
def decidir_ausencia(aid: int, dados: DecisaoIn, user=Depends(usuario_atual)):
    if not _admin(user):
        raise HTTPException(403, "So administrador aprova ou recusa")
    if dados.status not in ("aprovada", "recusada"):
        raise HTTPException(400, "status: aprovada ou recusada")
    c = _con()
    try:
        _schema(c)
        if not c.execute("SELECT id FROM rh_ausencias WHERE id=?", (aid,)).fetchone():
            raise HTTPException(404, "Pedido nao encontrado")
        c.execute("UPDATE rh_ausencias SET status=?, obs_decisao=?, decidido_por=?, decidido_em=? WHERE id=?",
                  (dados.status, (dados.obs or "")[:500], user.nome or user.email, _agora(), aid))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.delete("/ausencias/{aid}")
def cancelar_ausencia(aid: int, user=Depends(usuario_atual)):
    ""
    c = _con()
    try:
        _schema(c)
        a = c.execute("SELECT * FROM rh_ausencias WHERE id=?", (aid,)).fetchone()
        if not a:
            raise HTTPException(404, "Pedido nao encontrado")
        if not _admin(user):
            meu = _meu(c, user, criar=False)
            if not meu or a["colaborador_id"] != meu["id"] or a["status"] != "pendente":
                raise HTTPException(403, "Voce so cancela o seu pedido enquanto ele esta pendente")
        c.execute("DELETE FROM rh_ausencias WHERE id=?", (aid,))
        c.commit()
        return {"ok": True}
    finally:
        c.close()
