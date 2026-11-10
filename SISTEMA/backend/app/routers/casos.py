"""
CASOS - a entidade que vem ANTES do processo. Sistema Escritório Modelo.

O escritorio comeca a trabalhar antes de existir numero de processo: consultoria,
revisao de contrato, parecer, atendimento de lead, e tambem o contencioso que
ainda nao foi distribuido. Tudo isso e CASO.

O processo judicial e um ESTADO do caso, nao outra entidade:

    CASO (sempre existe)
      +- tipo: CONSULTORIA | CONTENCIOSO | REVISAO CONTRATUAL | LEAD | PARECER...
      +- cliente, responsavel, honorarios, pasta, anotacoes, TAREFAS
      +- ao DISTRIBUIR: ganha numero, tribunal, orgao, valor da causa, fase
         e so entao passa a casar com o SYNC e com as intimacoes

Por que nao modelar como "processo sem numero": uma consultoria nunca sera
distribuida. Ela ficaria para sempre marcada como "a distribuir", sujando a
carteira e mentindo no contador.

Distribuir e um EVENTO sobre o mesmo registro - nada e movido de lugar, entao
nada se perde no caminho.
"""
import sqlite3
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..deps import usuario_atual

router = APIRouter(prefix="/api/casos", tags=["casos"])

_DB = str(Path(__file__).resolve().parents[2] / "sistema_esc.db")

# Ponto de partida. A tela oferece estes e aceita digitar outro - os que a
# equipe usar entram sozinhos na lista (mesma ideia dos tipos de tarefa).
TIPOS_PADRAO = [
    "CONTENCIOSO", "CONSULTORIA", "REVISAO CONTRATUAL", "PARECER",
    "LEAD", "ACORDO EXTRAJUDICIAL", "ADMINISTRATIVO",
]
AREAS_PADRAO = ["TRABALHISTA", "CIVEL", "PREVIDENCIARIO", "EMPRESARIAL",
                "FAMILIA", "CONSUMIDOR", "TRIBUTARIO"]
STATUS = ("ativo", "encerrado", "arquivado")


def _con():
    c = sqlite3.connect(_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


DDL = [
    """
    CREATE TABLE IF NOT EXISTS casos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        titulo TEXT NOT NULL,
        tipo TEXT NOT NULL,
        area TEXT,
        cliente_nome TEXT,
        cliente_id INTEGER,
        parte_adversa TEXT,
        responsavel TEXT,
        responsavel_id INTEGER,
        status TEXT DEFAULT 'ativo',
        valor_causa REAL,
        valor_honorarios REAL,
        contingencia REAL,
        numero_processo TEXT,
        tribunal TEXT,
        orgao TEXT,
        fase TEXT,
        etapa TEXT,
        data_distribuicao TEXT,
        jur_processo_id INTEGER,
        pasta_drive TEXT,
        notas TEXT,
        criado_por TEXT,
        criado_em REAL,
        atualizado_em REAL
    )
    """,
    # Um numero de processo so pode estar em um caso. O indice parcial deixa
    # varios casos sem numero conviverem (consultorias, nao distribuidos).
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_caso_numero ON casos(numero_processo) "
    "WHERE numero_processo IS NOT NULL AND numero_processo <> ''",
    "CREATE INDEX IF NOT EXISTS ix_caso_status ON casos(status, tipo)",
    "CREATE INDEX IF NOT EXISTS ix_caso_cliente ON casos(cliente_nome)",
]


def garantir_tabelas():
    c = _con()
    try:
        for stmt in DDL:
            c.execute(stmt)
        # ligacao com o card do CRM (wa_conversas): o caso acompanha o lead
        # do primeiro contato ate o fim, e a etapa do funil vem de la.
        cc = {r[1] for r in c.execute("PRAGMA table_info(casos)")}
        if "conversa_id" not in cc:
            c.execute("ALTER TABLE casos ADD COLUMN conversa_id INTEGER")
            c.execute("CREATE INDEX IF NOT EXISTS ix_caso_conversa ON casos(conversa_id)")
        # campos do formulario "Criar novo processo" do ADVBOX
        for nome, tipo_col in (("protocolo", "TEXT"), ("processo_originario", "TEXT"),
                               ("pasta_identificacao", "TEXT"), ("origem", "TEXT"),
                               ("tipo_acao", "TEXT")):
            if nome not in cc:
                c.execute("ALTER TABLE casos ADD COLUMN " + nome + " " + tipo_col)
        c.execute("CREATE INDEX IF NOT EXISTS ix_caso_fase ON casos(fase, etapa)")

        # tarefas nascem com o caso, antes de existir numero de processo
        cols = {r[1] for r in c.execute("PRAGMA table_info(tarefas)")}
        if cols and "caso_id" not in cols:
            c.execute("ALTER TABLE tarefas ADD COLUMN caso_id INTEGER")
            c.execute("CREATE INDEX IF NOT EXISTS ix_tarefa_caso ON tarefas(caso_id)")
        c.commit()
    finally:
        c.close()


garantir_tabelas()


class CasoIn(BaseModel):
    titulo: str
    tipo: str
    area: Optional[str] = None
    cliente_nome: Optional[str] = None
    cliente_id: Optional[int] = None
    parte_adversa: Optional[str] = None
    responsavel: Optional[str] = None
    responsavel_id: Optional[int] = None
    valor_causa: Optional[float] = None
    valor_honorarios: Optional[float] = None
    contingencia: Optional[float] = None
    pasta_drive: Optional[str] = None
    notas: Optional[str] = None
    conversa_id: Optional[int] = None
    fase: Optional[str] = None
    etapa: Optional[str] = None
    protocolo: Optional[str] = None
    processo_originario: Optional[str] = None
    pasta_identificacao: Optional[str] = None
    origem: Optional[str] = None
    tipo_acao: Optional[str] = None


class CasoPatch(BaseModel):
    titulo: Optional[str] = None
    tipo: Optional[str] = None
    area: Optional[str] = None
    cliente_nome: Optional[str] = None
    parte_adversa: Optional[str] = None
    responsavel: Optional[str] = None
    responsavel_id: Optional[int] = None
    status: Optional[str] = None
    valor_causa: Optional[float] = None
    valor_honorarios: Optional[float] = None
    contingencia: Optional[float] = None
    pasta_drive: Optional[str] = None
    notas: Optional[str] = None
    conversa_id: Optional[int] = None
    fase: Optional[str] = None
    etapa: Optional[str] = None
    protocolo: Optional[str] = None
    processo_originario: Optional[str] = None
    pasta_identificacao: Optional[str] = None
    origem: Optional[str] = None
    numero_processo: Optional[str] = None
    tribunal: Optional[str] = None
    orgao: Optional[str] = None
    tipo_acao: Optional[str] = None


class DistribuirIn(BaseModel):
    """O que a Advogada preenche no dia da distribuicao."""
    numero_processo: str
    tribunal: Optional[str] = None
    orgao: Optional[str] = None
    valor_causa: Optional[float] = None
    fase: Optional[str] = None
    etapa: Optional[str] = None
    responsavel: Optional[str] = None
    valor_honorarios: Optional[float] = None
    data_distribuicao: Optional[str] = None


def _montar(c, row):
    d = dict(row)
    d["distribuido"] = bool((d.get("numero_processo") or "").strip())
    tar = c.execute(
        "SELECT COUNT(*) a, SUM(CASE WHEN status='aberta' THEN 1 ELSE 0 END) ab "
        "FROM tarefas WHERE caso_id=? OR (processo_numero IS NOT NULL "
        "AND processo_numero <> '' AND processo_numero = ?)",
        (d["id"], d.get("numero_processo") or "\x00")).fetchone()
    d["tarefas_total"] = tar["a"] or 0
    d["tarefas_abertas"] = tar["ab"] or 0

    # etapa do funil vem do CRM, nao e copiada: o card do lead e a fonte
    d["crm"] = None
    if d.get("conversa_id"):
        try:
            r = c.execute(
                "SELECT c.id, c.contato_nome, c.contato_telefone, c.etapa, "
                "c.pipeline_id, p.nome AS pipeline, s.cor AS cor "
                "FROM wa_conversas c "
                "LEFT JOIN wa_pipelines p ON p.id = c.pipeline_id "
                "LEFT JOIN wa_stages s ON s.pipeline_id = c.pipeline_id AND s.nome = c.etapa "
                "WHERE c.id = ?", (d["conversa_id"],)).fetchone()
            if r:
                d["crm"] = dict(r)
        except sqlite3.Error:
            pass
    return d


@router.get("/listas")
def listas(user=Depends(usuario_atual)):
    """
    Vocabulario para os seletores. Vem das TAXONOMIAS (listas fechadas,
    administradas em Configuracoes) - nao de valores distintos soltos.

    Fechar a lista e o que torna o BI confiavel: com texto livre, "CIVEL" e
    "Civel" viram duas carteiras e ninguem percebe olhando o grafico.
    """
    c = _con()
    try:
        out = {"grupos_acao": [], "tipos_acao": [], "fases": [],
               "etapas": [], "tipos_caso": []}
        mapa = {"grupo_acao": "grupos_acao", "tipo_acao": "tipos_acao",
                "fase": "fases", "etapa": "etapas", "classificacao": "tipos_caso"}
        try:
            for r in c.execute("SELECT lista, valor FROM taxonomias WHERE ativo=1 "
                               "ORDER BY lista, ordem, valor"):
                chave = mapa.get(r["lista"])
                if chave:
                    out[chave].append(r["valor"])
        except sqlite3.Error:
            pass

        out["equipe"] = [{"id": r["id"], "nome": r["nome"]} for r in c.execute(
            "SELECT id, nome FROM usuarios WHERE ativo=1 ORDER BY nome")]
        out["origens"] = ["INDICACAO", "ANUNCIO", "SITE DO ESCRITORIO", "INSTAGRAM",
                          "GOOGLE", "FACEBOOK", "BNI", "ESCRITORIO", "PARCEIRO"]
        out["areas"] = AREAS_PADRAO
        # os quatro que a tela exige preencher
        out["obrigatorios"] = ["area", "tipo_acao", "tipo", "fase"]
        return out
    finally:
        c.close()


@router.get("/tipos")
def tipos(user=Depends(usuario_atual)):
    c = _con()
    try:
        usados = [r["tipo"] for r in c.execute(
            "SELECT DISTINCT tipo FROM casos WHERE tipo IS NOT NULL")]
        areas = [r["area"] for r in c.execute(
            "SELECT DISTINCT area FROM casos WHERE area IS NOT NULL AND area <> ''")]
        return {"tipos": sorted(set(TIPOS_PADRAO) | set(usados)),
                "areas": sorted(set(AREAS_PADRAO) | set(areas))}
    finally:
        c.close()


@router.get("")
def listar(
    tipo: Optional[str] = None,
    area: Optional[str] = None,
    status: Optional[str] = Query("ativo"),
    distribuido: Optional[bool] = Query(None, description="true=so com numero; false=so sem"),
    q: Optional[str] = None,
    limite: int = Query(400, le=2000),
    user=Depends(usuario_atual),
):
    c = _con()
    try:
        onde, args = [], []
        if status and status != "todos":
            onde.append("status = ?")
            args.append(status)
        if tipo:
            onde.append("tipo = ?")
            args.append(tipo)
        if area:
            onde.append("area = ?")
            args.append(area)
        if distribuido is True:
            onde.append("numero_processo IS NOT NULL AND numero_processo <> ''")
        elif distribuido is False:
            onde.append("(numero_processo IS NULL OR numero_processo = '')")
        if q:
            onde.append("(titulo LIKE ? OR cliente_nome LIKE ? OR parte_adversa LIKE ? "
                        "OR numero_processo LIKE ?)")
            curinga = "%" + q + "%"
            args += [curinga] * 4

        sql = "SELECT * FROM casos"
        if onde:
            sql += " WHERE " + " AND ".join(onde)
        sql += " ORDER BY atualizado_em DESC LIMIT ?"
        args.append(limite)

        itens = [_montar(c, r) for r in c.execute(sql, args)]
        return {
            "casos": itens,
            "total": len(itens),
            "distribuidos": sum(1 for x in itens if x["distribuido"]),
            "a_distribuir": sum(1 for x in itens if not x["distribuido"]),
        }
    finally:
        c.close()


@router.get("/por-conversa/{conversa_id}")
def por_conversa(conversa_id: int, user=Depends(usuario_atual)):
    """
    O caso ligado a um card do CRM, se ja existir.

    Serve ao botao do Chat ao Vivo: se a conversa ja virou caso, abre o caso;
    se nao, oferece criar. Evita dois cadastros para o mesmo lead.
    """
    c = _con()
    try:
        r = c.execute("SELECT * FROM casos WHERE conversa_id=? ORDER BY id DESC LIMIT 1",
                      (conversa_id,)).fetchone()
        return {"caso": _montar(c, r) if r else None}
    finally:
        c.close()


@router.get("/{caso_id}")
def obter(caso_id: int, user=Depends(usuario_atual)):
    c = _con()
    try:
        r = c.execute("SELECT * FROM casos WHERE id=?", (caso_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Caso nao encontrado")
        d = _montar(c, r)
        d["tarefas"] = [dict(x) for x in c.execute(
            "SELECT id, tipo, titulo, status, prazo_interno, prazo_fatal, trilha "
            "FROM tarefas WHERE caso_id=? OR (processo_numero IS NOT NULL "
            "AND processo_numero <> '' AND processo_numero=?) "
            "ORDER BY COALESCE(prazo_interno, data, '9999')",
            (caso_id, d.get("numero_processo") or "\x00"))]
        return d
    finally:
        c.close()


OBRIGATORIOS = [("area", "grupo de ação"), ("tipo_acao", "tipo de ação"),
                ("tipo", "classificação do caso"), ("fase", "fase")]


@router.post("")
def criar(dados: CasoIn, user=Depends(usuario_atual)):
    # Os quatro campos que sustentam a metrica nao podem ficar vazios: caso sem
    # eles nao aparece em nenhum recorte do BI e vira ponto cego.
    faltando = [rot for campo, rot in OBRIGATORIOS
                if not (getattr(dados, campo, None) or "").strip()]
    if faltando:
        raise HTTPException(400, "Preencha: " + ", ".join(faltando))
    c = _con()
    try:
        agora = time.time()
        campos = dados.model_dump()
        campos.update({"status": "ativo", "criado_por": user.nome,
                       "criado_em": agora, "atualizado_em": agora})
        cols = ", ".join(campos)
        marc = ", ".join("?" for _ in campos)
        cur = c.execute("INSERT INTO casos (" + cols + ") VALUES (" + marc + ")",
                        list(campos.values()))
        c.commit()
        return _montar(c, c.execute("SELECT * FROM casos WHERE id=?",
                                    (cur.lastrowid,)).fetchone())
    finally:
        c.close()


@router.patch("/{caso_id}")
def editar(caso_id: int, dados: CasoPatch, user=Depends(usuario_atual)):
    campos = dados.model_dump(exclude_unset=True)
    if campos.get("status") and campos["status"] not in STATUS:
        raise HTTPException(400, "status invalido")
    c = _con()
    try:
        if not c.execute("SELECT id FROM casos WHERE id=?", (caso_id,)).fetchone():
            raise HTTPException(404, "Caso nao encontrado")
        if campos:
            sets = ", ".join(k + "=?" for k in campos)
            c.execute("UPDATE casos SET " + sets + ", atualizado_em=? WHERE id=?",
                      list(campos.values()) + [time.time(), caso_id])
            c.commit()
        return _montar(c, c.execute("SELECT * FROM casos WHERE id=?", (caso_id,)).fetchone())
    finally:
        c.close()


@router.post("/{caso_id}/distribuir")
def distribuir(caso_id: int, dados: DistribuirIn, user=Depends(usuario_atual)):
    """
    Evento da distribuicao: o caso ganha os dados judiciais e passa a casar com
    o SYNC e com as intimacoes pelo numero. O registro e o MESMO - nada e movido.
    """
    numero = (dados.numero_processo or "").strip()
    if not numero:
        raise HTTPException(400, "numero do processo obrigatorio")

    c = _con()
    try:
        atual = c.execute("SELECT * FROM casos WHERE id=?", (caso_id,)).fetchone()
        if not atual:
            raise HTTPException(404, "Caso nao encontrado")

        outro = c.execute("SELECT id, titulo FROM casos WHERE numero_processo=? AND id<>?",
                          (numero, caso_id)).fetchone()
        if outro:
            raise HTTPException(409, "O processo %s ja esta no caso #%d (%s)"
                                % (numero, outro["id"], outro["titulo"]))

        # liga ao espelho do ADVBOX, se o processo ja existir la
        jp = c.execute("SELECT id FROM jur_processos WHERE TRIM(numero)=? LIMIT 1",
                       (numero,)).fetchone()

        campos = {k: v for k, v in dados.model_dump(exclude_unset=True).items()
                  if v is not None}
        campos["numero_processo"] = numero
        campos.setdefault("data_distribuicao", time.strftime("%Y-%m-%d"))
        campos["jur_processo_id"] = jp["id"] if jp else None
        campos["atualizado_em"] = time.time()

        sets = ", ".join(k + "=?" for k in campos)
        c.execute("UPDATE casos SET " + sets + " WHERE id=?",
                  list(campos.values()) + [caso_id])

        # tarefas ja abertas no caso passam a apontar tambem para o processo
        c.execute("UPDATE tarefas SET processo_numero=? "
                  "WHERE caso_id=? AND (processo_numero IS NULL OR processo_numero='')",
                  (numero, caso_id))
        c.commit()

        d = _montar(c, c.execute("SELECT * FROM casos WHERE id=?", (caso_id,)).fetchone())
        d["ja_no_advbox"] = jp is not None
        return d
    finally:
        c.close()
