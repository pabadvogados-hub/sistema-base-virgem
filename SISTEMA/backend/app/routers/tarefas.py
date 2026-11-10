"""
MODULO DE TAREFAS (Atividades) - Sistema Escritório Modelo

Espelha o modelo do ADVBOX: uma tarefa tem N responsaveis, e CADA responsavel
tem a propria baixa (`concluido_em`), com flags de importante/urgente. E isso
que da "tarefa individual por responsavel" - a mesma tarefa aparece na fila de
duas pessoas e cada uma conclui a sua parte. A tarefa so fecha quando todos
concluiram.

Origem dos cards:
  manual  - criados na tela
  motor   - gerados da intimacao ja classificada (sync_motor_sombra)
  advbox  - espelho do ADVBOX

Idempotencia: (origem, origem_ref, tipo) e UNIQUE. Rodar o gerador duas vezes
nao duplica card.
"""
import logging
import sqlite3
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..deps import usuario_atual, requer_admin
from .. import tarefas_prazo
from .. import tarefas_atividade
from .. import tarefas_tipos as TT

log = logging.getLogger("tarefas")

router = APIRouter(prefix="/api/tarefas", tags=["tarefas"])

STATUS_VALIDOS = ("aberta", "concluida", "cancelada")
PRIORIDADES = ("normal", "importante", "urgente")

# Mesmo idioma do controladoria.py: app/routers/ -> parents[2] = backend/
_DB = str(Path(__file__).resolve().parents[2] / "sistema_esc.db")


def _con():
    c = sqlite3.connect(_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


DDL = [
    """
    CREATE TABLE IF NOT EXISTS tarefas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL,
        titulo TEXT NOT NULL,
        descricao TEXT,
        processo_numero TEXT,
        lawsuits_id TEXT,
        cliente TEXT,
        parte_adversa TEXT,
        pasta_drive TEXT,
        data TEXT,
        hora TEXT,
        prazo_fatal TEXT,
        prazo_interno TEXT,
        trilha TEXT,
        prioridade TEXT DEFAULT 'normal',
        status TEXT DEFAULT 'aberta',
        origem TEXT DEFAULT 'manual',
        origem_ref TEXT,
        criado_por TEXT,
        criado_em REAL,
        atualizado_em REAL,
        concluida_em REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tarefa_responsaveis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tarefa_id INTEGER NOT NULL,
        usuario_id INTEGER,
        nome TEXT,
        concluido_em REAL,
        importante INTEGER DEFAULT 0,
        urgente INTEGER DEFAULT 0
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_tarefa_origem ON tarefas(origem, origem_ref, tipo)",
    "CREATE INDEX IF NOT EXISTS ix_tarefa_status ON tarefas(status, data)",
    "CREATE INDEX IF NOT EXISTS ix_tarefa_prazo ON tarefas(prazo_interno, status)",
    "CREATE INDEX IF NOT EXISTS ix_tarefa_processo ON tarefas(processo_numero)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_tarefa_resp ON tarefa_responsaveis(tarefa_id, usuario_id)",
    "CREATE INDEX IF NOT EXISTS ix_resp_usuario ON tarefa_responsaveis(usuario_id, concluido_em)",
]


# Colunas acrescentadas depois da 1a versao. ALTER TABLE idempotente: SQLite nao
# tem "ADD COLUMN IF NOT EXISTS", entao conferimos o PRAGMA antes.
COLUNAS_EXTRA = [
    ("contato_nome", "TEXT"),
    ("contato_telefone", "TEXT"),
    ("conversa_id", "INTEGER"),
    # id do compromisso do PFI na agenda do escritorio, para poder apagar
    # quando a tarefa termina (24/09/2026)
    ("evento_id", "TEXT"),
    # cadeia do motor das tarefas (02/10/2026): 'intim:<id>' e a posicao da
    # etapa (PECA 1, REVISAO 2, AJUSTES 3, PROTOCOLO 4). Ver app/motor_tarefas.py
    ("cadeia", "TEXT"),
    ("etapa", "INTEGER"),
]


def garantir_tabelas():
    c = _con()
    try:
        for stmt in DDL:
            c.execute(stmt)
        existentes = {r[1] for r in c.execute("PRAGMA table_info(tarefas)")}
        for nome, tipo in COLUNAS_EXTRA:
            if nome not in existentes:
                c.execute("ALTER TABLE tarefas ADD COLUMN " + nome + " " + tipo)
        c.execute("CREATE INDEX IF NOT EXISTS ix_tarefa_conversa "
                  "ON tarefas(conversa_id)")
        c.commit()
    finally:
        c.close()


garantir_tabelas()


# ============================================================
# MODELOS
# ============================================================

class ResponsavelIn(BaseModel):
    usuario_id: Optional[int] = None
    nome: Optional[str] = None
    importante: bool = False
    urgente: bool = False


class TarefaIn(BaseModel):
    tipo: str
    titulo: str
    descricao: Optional[str] = None
    processo_numero: Optional[str] = None
    lawsuits_id: Optional[str] = None
    cliente: Optional[str] = None
    parte_adversa: Optional[str] = None
    pasta_drive: Optional[str] = None
    data: Optional[str] = None
    hora: Optional[str] = None
    prazo_fatal: Optional[str] = None
    prazo_interno: Optional[str] = None
    trilha: Optional[str] = None
    prioridade: str = "normal"
    contato_nome: Optional[str] = None
    contato_telefone: Optional[str] = None
    conversa_id: Optional[int] = None
    # amarra a tarefa a intimacao que a originou. Alimenta origem_ref, que ja
    # e UNIQUE junto com (origem, tipo): a mesma intimacao nao gera duas vezes
    # o mesmo tipo de tarefa.
    intimacao_id: Optional[int] = None
    responsaveis: List[ResponsavelIn] = []


class TarefaPatch(BaseModel):
    tipo: Optional[str] = None
    titulo: Optional[str] = None
    descricao: Optional[str] = None
    data: Optional[str] = None
    hora: Optional[str] = None
    prazo_fatal: Optional[str] = None
    prazo_interno: Optional[str] = None
    prioridade: Optional[str] = None
    status: Optional[str] = None
    trilha: Optional[str] = None
    contato_nome: Optional[str] = None
    contato_telefone: Optional[str] = None
    responsaveis: Optional[List[ResponsavelIn]] = None
    # fase 2 (03/11/2026): cancelar exige motivo (decisao da Advogada, Ajustes 4 e 6). Nao e coluna: vai ao historico.
    motivo: Optional[str] = None


# ============================================================
# HELPERS
# ============================================================

def _responsaveis(c, tarefa_id):
    return [dict(r) for r in c.execute(
        "SELECT usuario_id, nome, concluido_em, importante, urgente "
        "FROM tarefa_responsaveis WHERE tarefa_id=? ORDER BY nome", (tarefa_id,))]


def _situacao(t):
    """atrasada | hoje | amanha | proxima | sem_prazo | concluida"""
    if t.get("status") == "concluida":
        return "concluida"
    alvo = t.get("prazo_interno") or t.get("data")
    if not alvo:
        return "sem_prazo"
    try:
        d = date.fromisoformat(str(alvo)[:10])
    except (ValueError, TypeError):
        return "sem_prazo"
    hoje = date.today()
    if d < hoje:
        return "atrasada"
    if d == hoje:
        return "hoje"
    if d == hoje + timedelta(days=1):
        return "amanha"
    return "proxima"


def _partes_do_processo(c, numero):
    """Cliente e parte adversa pelo cadastro do ADVBOX. ('', '') se nao houver."""
    if not (numero or "").strip():
        return "", ""
    try:
        r = c.execute("SELECT cliente, parte_adversa FROM advbox_titularidade "
                      "WHERE numero=?", (numero,)).fetchone()
        if r:
            return (r["cliente"] or ""), (r["parte_adversa"] or "")
    except Exception:
        pass
    return "", ""


def _agenda_da_tarefa(c, t):
    """Evento de agenda desta tarefa, se houver.

    Casa por origem_ref (a intimacao que gerou a tarefa) e, na falta,
    por processo + data do prazo, que cobre o evento cadastrado a mao.
    """
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS sync_intim_agenda (
                       id INTEGER PRIMARY KEY, evento_id TEXT, link TEXT, data TEXT,
                       titulo TEXT, por TEXT, espelho_outlook INTEGER, criado_em REAL)""")
        ref = t.get("origem_ref")
        if ref and str(ref).isdigit():
            r = c.execute("SELECT evento_id, link, data FROM sync_intim_agenda "
                          "WHERE id=?", (int(ref),)).fetchone()
            if r and r["evento_id"]:
                return {"evento_id": r["evento_id"], "link": r["link"], "data": r["data"]}
        num, quando = t.get("processo_numero"), (t.get("prazo_fatal") or t.get("data"))
        if num and quando:
            r = c.execute("SELECT evento_id, link, data FROM sync_intim_agenda "
                          "WHERE data=? AND titulo LIKE ?",
                          (str(quando)[:10], "%" + str(num) + "%")).fetchone()
            if r and r["evento_id"]:
                return {"evento_id": r["evento_id"], "link": r["link"], "data": r["data"]}
    except Exception as e:
        log.warning("agenda da tarefa %s: %s", t.get("id"), e)
    return None


def _montar(c, row, uid=None):
    t = dict(row)
    resp = _responsaveis(c, t["id"])
    t["responsaveis"] = resp
    t["total_responsaveis"] = len(resp)
    t["concluidos"] = sum(1 for r in resp if r["concluido_em"])
    if uid is not None:
        meu = next((r for r in resp if r["usuario_id"] == uid), None)
        t["sou_responsavel"] = meu is not None
        t["concluida_para_mim"] = bool(meu and meu["concluido_em"])
        t["importante"] = bool(meu and meu["importante"])
        t["urgente"] = bool(meu and meu["urgente"])
    t["situacao"] = _situacao(t)
    if not (t.get("cliente") or "").strip():
        cli, adv = _partes_do_processo(c, t.get("processo_numero"))
        if cli:
            t["cliente"] = cli
        if adv and not (t.get("parte_adversa") or "").strip():
            t["parte_adversa"] = adv
    ag = _agenda_da_tarefa(c, t)
    t["na_agenda"] = bool(ag)
    t["agenda_link"] = (ag or {}).get("link")
    # sem numero de processo a tarefa e de um CASO, nao de um processo: a
    # capa do card precisa dizer isso em vez de deixar o campo vazio
    t["eh_caso"] = not (t.get("processo_numero") or "").strip()
    return t


def _agora():
    return time.time()


def _evento(c, tarefa_id, processo, acao, detalhe, por, so_processo=False):
    """Evento de tarefa em jur_historico pela funcao do motor (fase 2, T5, 03/11/2026). entidade='tarefa' com o
    id como texto; havendo processo, grava tambem entidade='processo' com o CNJ, para o historico do processo.
    Nunca recebe prazo fatal. Nao faz commit; falha de historico nao derruba a acao."""
    try:
        from .. import motor_tarefas as MT
        if not so_processo:
            MT.historico(c, "tarefa", str(tarefa_id), acao, detalhe, por)
        if (processo or "").strip():
            MT.historico(c, "processo", processo.strip(), acao, "tarefa #%s: %s" % (tarefa_id, detalhe), por)
    except Exception as e:
        log.warning("historico da tarefa %s (%s): %s", tarefa_id, acao, e)


# ============================================================
# LISTAGEM
# ============================================================

@router.get("")
def listar(
    responsavel: Optional[str] = Query(None, description="id do usuario, 'me' ou 'todos'"),
    status: Optional[str] = Query("aberta"),
    situacao: Optional[str] = Query(None),
    trilha: Optional[str] = None,
    processo: Optional[str] = None,
    q: Optional[str] = None,
    de: Optional[str] = None,
    ate: Optional[str] = None,
    minhas_pendentes: bool = Query(
        True, description="Ao filtrar por responsavel, esconde o que ele ja concluiu"),
    limite: int = Query(300, le=1000),
    user=Depends(usuario_atual),
):
    c = _con()
    try:
        sql = "SELECT t.* FROM tarefas t"
        onde, args = [], []

        if responsavel and responsavel != "todos":
            try:
                uid = user.id if responsavel == "me" else int(responsavel)
            except (TypeError, ValueError):
                raise HTTPException(400, "responsavel invalido")
            sql += " JOIN tarefa_responsaveis r ON r.tarefa_id = t.id"
            onde.append("r.usuario_id = ?")
            args.append(uid)
            # A fila de uma pessoa mostra o que ELA ainda deve fazer. Se ela ja
            # concluiu a parte dela, sai da fila mesmo que a tarefa siga aberta
            # para outro responsavel (modelo ADVBOX).
            if minhas_pendentes:
                onde.append("r.concluido_em IS NULL")

        if status and status != "todas":
            onde.append("t.status = ?")
            args.append(status)
        if trilha:
            onde.append("t.trilha = ?")
            args.append(trilha)
        if processo:
            onde.append("t.processo_numero = ?")
            args.append(processo)
        if de:
            onde.append("COALESCE(t.prazo_interno, t.data) >= ?")
            args.append(de)
        if ate:
            onde.append("COALESCE(t.prazo_interno, t.data) <= ?")
            args.append(ate)
        if q:
            onde.append("(t.titulo LIKE ? OR t.cliente LIKE ? OR t.processo_numero LIKE ? "
                        "OR t.descricao LIKE ?)")
            curinga = "%" + q + "%"
            args += [curinga, curinga, curinga, curinga]

        if onde:
            sql += " WHERE " + " AND ".join(onde)
        sql += " ORDER BY COALESCE(t.prazo_interno, t.data, '9999') ASC, t.id DESC LIMIT ?"
        args.append(limite)

        itens = [_montar(c, r, user.id) for r in c.execute(sql, args)]
        if situacao:
            itens = [t for t in itens if t["situacao"] == situacao]
        return {"tarefas": itens, "total": len(itens)}
    finally:
        c.close()


@router.get("/resumo")
def resumo(user=Depends(usuario_atual)):
    """Contadores dos cartoes do topo e a fila por responsavel."""
    c = _con()
    try:
        abertas = [_montar(c, r, user.id) for r in
                   c.execute("SELECT * FROM tarefas WHERE status='aberta'")]
        minhas = [t for t in abertas
                  if t.get("sou_responsavel") and not t.get("concluida_para_mim")]

        def cont(itens, s):
            return sum(1 for t in itens if t["situacao"] == s)

        # "Resolvidas no mes", o terceiro numero do grafico da tela de
        # Atividades (espelho do ADVBOX). Conta a BAIXA de cada responsavel:
        # no modelo ADVBOX quem conclui a propria parte resolveu, mesmo que a
        # tarefa siga aberta para outra pessoa. concluido_em e carimbo Unix.
        hoje_d = date.today()
        inicio_mes = time.mktime(hoje_d.replace(day=1).timetuple())
        resolvidas_minhas = c.execute(
            "SELECT COUNT(*) FROM tarefa_responsaveis "
            "WHERE usuario_id=? AND concluido_em >= ?", (user.id, inicio_mes)).fetchone()[0]
        resolvidas_equipe = c.execute(
            "SELECT COUNT(*) FROM tarefa_responsaveis "
            "WHERE concluido_em >= ?", (inicio_mes,)).fetchone()[0]

        por_resp = []
        for r in c.execute(
            "SELECT r.usuario_id AS uid, r.nome AS nome, COUNT(*) AS abertas "
            "FROM tarefa_responsaveis r "
            "JOIN tarefas t ON t.id = r.tarefa_id AND t.status='aberta' "
            "WHERE r.concluido_em IS NULL "
            "GROUP BY r.usuario_id, r.nome ORDER BY abertas DESC"
        ):
            por_resp.append({"usuario_id": r["uid"], "nome": r["nome"],
                             "abertas": r["abertas"]})

        return {
            "minhas": {
                "total": len(minhas),
                "atrasadas": cont(minhas, "atrasada"),
                "hoje": cont(minhas, "hoje"),
                "amanha": cont(minhas, "amanha"),
                "resolvidas_mes": resolvidas_minhas,
            },
            "equipe": {
                "total": len(abertas),
                "atrasadas": cont(abertas, "atrasada"),
                "hoje": cont(abertas, "hoje"),
                "resolvidas_mes": resolvidas_equipe,
            },
            "por_responsavel": por_resp,
        }
    finally:
        c.close()


@router.get("/equipe")
def equipe(user=Depends(usuario_atual)):
    """Quem pode receber tarefa: o time e os advogados parceiros.

    Os parceiros vem com id NEGATIVO e externo=True. A tela mostra os dois
    juntos; quem grava a tarefa usa o sinal para saber que ali vai WhatsApp
    em vez de fila interna, ja que eles nao entram no sistema.
    """
    c = _con()
    try:
        time_ = [{"id": r["id"], "nome": r["nome"], "email": r["email"],
                  "papel": r["papel"], "externo": False}
                 for r in c.execute("SELECT id, nome, email, papel FROM usuarios "
                                    "WHERE ativo=1 ORDER BY nome")]
        _garantir_parceiros(c)
        parc = [{"id": -r["id"], "nome": r["nome"], "externo": True,
                 "telefone": r["telefone"]}
                for r in c.execute("SELECT * FROM tarefa_parceiros WHERE ativo=1 "
                                   "ORDER BY nome")]
        return {"equipe": time_ + parc}
    finally:
        c.close()


def _garantir_tipos(c):
    c.execute("""CREATE TABLE IF NOT EXISTS tarefa_tipos (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   nome TEXT NOT NULL UNIQUE,
                   grupo TEXT,
                   tmp INTEGER,
                   advbox_id INTEGER,
                   ativo INTEGER DEFAULT 1,
                   origem TEXT DEFAULT 'advbox',
                   criado_em TEXT)""")
    # fase 2, T1: os tipos de etapa da cadeia e do catalogo do motor, normalizados (idempotente)
    if TT.semear(c):
        c.commit()


@router.get("/tipos")
def tipos(todos: bool = False, user=Depends(usuario_atual)):
    """Catalogo de tarefas que o escritorio sabe atribuir.

    Veio do ADVBOX em 24/09/2026: 130 tipos com o TMP (tempo medio de producao)
    em minutos, mais os que ja existiam aqui. Antes disso o seletor tinha 11
    tipos chutados e aceitava texto livre - foi assim que "marcar gini Ma"
    virou tipo de tarefa.

    `tipos` continua sendo a lista de nomes, que e o que a tela ja consome.
    `catalogo` traz grupo e TMP para quem quiser mostrar mais.
    `todos=true` inclui os desligados.
    """
    c = _con()
    try:
        _garantir_tipos(c)
        onde = "" if todos else " WHERE ativo=1"
        linhas = [dict(r) for r in c.execute(
            "SELECT nome, grupo, tmp, advbox_id, ativo, origem FROM tarefa_tipos"
            + onde + " ORDER BY grupo, nome")]
        usados = [r["tipo"] for r in c.execute(
            "SELECT DISTINCT tipo FROM tarefas WHERE TRIM(COALESCE(tipo,'')) != ''")]
        # ajuste 5 (03/11/2026): sem acento e sem repeticao (PETIÇÃO DE MEIO e PETICAO DE MEIO viram uma so),
        # do mesmo jeito que o POST e o PATCH gravam.
        nomes = sorted({_norm_tipo(n) for n in [x["nome"] for x in linhas] + usados if _norm_tipo(n)})
        # fase 2, T1: a familia da etapa de cada tipo (producao, revisao, protocolo, ciencia). Acrescimo: quem le
        # so `tipos` e `catalogo` segue igual. O filtro por etapa na tela do fechamento e da fase 6.
        for x in linhas:
            x["familia"] = TT.familia_do_tipo(x["nome"])
        tipo_familia = {n: TT.familia_do_tipo(n) for n in nomes if TT.familia_do_tipo(n)}
        return {"tipos": nomes, "catalogo": linhas, "familias": TT.FAMILIAS, "tipo_familia": tipo_familia}
    finally:
        c.close()




# ============================================================================
# PARCEIROS: advogados de fora que atuam em processos nossos.
#
# Eles NAO tem login no sistema, entao nao aparecem em `usuarios`. Mas recebem
# tarefa como qualquer um do time, e sao avisados por WhatsApp, porque nao vao
# entrar aqui para ver.
#
# COMO CABEM NO ESQUEMA: `tarefa_responsaveis.usuario_id` guarda o id NEGATIVO
# do parceiro (-3 = parceiro 3). Id de usuario e sempre positivo, entao nao ha
# colisao, a fila pessoal de quem tem login nunca casa com um negativo, e nao
# foi preciso mexer em nenhuma consulta existente.
# ============================================================================
def _garantir_parceiros(c):
    c.execute("""CREATE TABLE IF NOT EXISTS tarefa_parceiros (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   nome TEXT NOT NULL,
                   telefone TEXT,
                   observacao TEXT,
                   ativo INTEGER DEFAULT 1,
                   criado_em REAL)""")


class ParceiroIn(BaseModel):
    nome: str
    telefone: str = ""
    observacao: str = ""


@router.get("/parceiros")
def listar_parceiros(incluir_inativos: bool = False, user=Depends(usuario_atual)):
    c = _con()
    try:
        _garantir_parceiros(c)
        sql = "SELECT * FROM tarefa_parceiros"
        if not incluir_inativos:
            sql += " WHERE ativo=1"
        sql += " ORDER BY nome"
        return {"parceiros": [dict(r) for r in c.execute(sql)]}
    finally:
        c.close()


@router.post("/parceiros")
def criar_parceiro(dados: ParceiroIn, user=Depends(requer_admin)):
    nome = (dados.nome or "").strip()
    if not nome:
        raise HTTPException(400, "nome obrigatorio")
    c = _con()
    try:
        _garantir_parceiros(c)
        ja = c.execute("SELECT id FROM tarefa_parceiros WHERE UPPER(nome)=?",
                       (nome.upper(),)).fetchone()
        if ja:
            c.execute("UPDATE tarefa_parceiros SET telefone=?, observacao=?, ativo=1 "
                      "WHERE id=?", (dados.telefone, dados.observacao, ja["id"]))
            c.commit()
            return {"ok": True, "id": ja["id"], "atualizado": True}
        cur = c.execute("INSERT INTO tarefa_parceiros (nome, telefone, observacao, "
                        "ativo, criado_em) VALUES (?,?,?,1,?)",
                        (nome, dados.telefone, dados.observacao, time.time()))
        c.commit()
        return {"ok": True, "id": cur.lastrowid}
    finally:
        c.close()


def _parceiro_de(usuario_id):
    """Parceiro por tras de um id negativo, ou None."""
    try:
        pid = -int(usuario_id)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    c = _con()
    try:
        _garantir_parceiros(c)
        r = c.execute("SELECT * FROM tarefa_parceiros WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


# titulos que nao servem de cumprimento: "Dr. Rafael".split()[0] e "Dr."
_TRATAMENTOS = ("DR", "DR.", "DRA", "DRA.", "SR", "SR.", "SRA", "SRA.", "EXMO", "EXMA")


def _como_chamar(nome):
    """Tratamento + primeiro nome, do jeito que se fala com um colega."""
    partes = [p for p in str(nome or "").split() if p]
    if not partes:
        return "tudo bem"
    trat = partes[0] if partes[0].upper().rstrip(".") + "." in _TRATAMENTOS \
        or partes[0].upper() in _TRATAMENTOS else ""
    resto = partes[1:] if trat else partes
    primeiro = resto[0] if resto else ""
    return (trat + " " + primeiro).strip() if trat else primeiro


def _avisar_parceiro(parceiro, tarefa, quem):
    """WhatsApp com a tarefa. Best-effort: falha aqui nao derruba o cadastro."""
    tel = "".join(ch for ch in str(parceiro.get("telefone") or "") if ch.isdigit())
    if len(tel) in (10, 11):
        tel = "55" + tel
    if len(tel) < 12:
        return False, "sem telefone valido"
    linhas = ["Olá, %s. Aqui é do Escritório Modelo Advogados." % _como_chamar(parceiro.get("nome")),
              "", "Uma tarefa foi atribuída a você:", "",
              (tarefa.get("titulo") or tarefa.get("tipo") or "Tarefa")]
    if tarefa.get("processo_numero"):
        linhas.append("Processo: %s" % tarefa["processo_numero"])
    if tarefa.get("cliente"):
        linhas.append("Cliente: %s" % tarefa["cliente"])
    if tarefa.get("prazo_fatal"):
        linhas.append("Prazo: %s" % _fmt_br(tarefa["prazo_fatal"]))
    if tarefa.get("descricao"):
        linhas += ["", str(tarefa["descricao"])[:400]]
    linhas += ["", "Cadastrada por %s." % (quem or "equipe"),
               "Qualquer dúvida é só responder por aqui."]
    texto = chr(10).join(linhas)
    try:
        from ..whatsapp import zapi
        from types import SimpleNamespace
        c = _con()
        try:
            r = c.execute("SELECT zapi_instance, zapi_token, zapi_client_token "
                          "FROM wa_numeros WHERE id=1").fetchone()
        finally:
            c.close()
        if not r:
            return False, "WhatsApp do escritorio nao configurado"
        num = SimpleNamespace(zapi_instance=r[0], zapi_token=r[1], zapi_client_token=r[2])
        return zapi.enviar_texto(num, tel, texto)
    except Exception as e:
        return False, str(e)[:120]


def _fmt_br(d):
    t = str(d or "")[:10].split("-")
    return "%s/%s/%s" % (t[2], t[1], t[0]) if len(t) == 3 else str(d)


@router.get("/prazo-inicial")
def prazo_inicial(de: str = "", uf: str = "SP", dias: int = 8,
                  user=Depends(usuario_atual)):
    """Prazo para distribuir uma reclamacao inicial.

    Regra da Advogada (02/09/2026): 8 dias CORRIDOS contados do contrato. Nao e
    prazo legal - nao ha prazo da CLT para ajuizar; e o compromisso que o
    escritorio assume com o cliente.

    Cai em sabado, domingo ou feriado? Rola para o proximo dia util: nao
    adianta prometer entrega em dia que ninguem protocola.

    O alvo interno e o mesmo D-2 dos prazos do tribunal (dois dias UTEIS
    antes). Com isso o alvo cai por volta de 5 dias uteis do contrato, que e
    como a equipe fala do mesmo prazo.
    """
    import datetime
    from ..controladoria_motor import feriados as FER

    try:
        base = (datetime.date.fromisoformat(de[:10]) if de
                else datetime.date.today())
    except ValueError:
        raise HTTPException(400, "data invalida, use AAAA-MM-DD")
    if dias < 1 or dias > 90:
        raise HTTPException(400, "dias fora da faixa (1 a 90)")

    # DIA UTIL DE ESCRITORIO, nao dia util forense. FER.eh_dia_util considera
    # nao util todo o recesso de 20/12 a 20/01, porque prazo de tribunal fica
    # suspenso ali. Este prazo nao e do tribunal: e o que prometemos ao
    # cliente, e a inicial se protocola por meio eletronico em janeiro.
    # Usando o forense, um contrato de 18/12 devolvia vencimento em 21/01 e
    # alvo interno em 17/12 - um dia ANTES do proprio contrato.
    # Ponto facultativo (Carnaval, Corpus Christi) NAO conta como dia util na promessa ao cliente (Advogada,
    # 03/11/2026); so os prazos processuais (fatal e interno) o contam.
    def _util_escritorio(d):
        return (d.weekday() < 5 and not FER.feriado_nome(d, uf)
                and not FER.ponto_facultativo_nome(d, uf))

    venc = base + datetime.timedelta(days=dias)
    rolou = None
    if not _util_escritorio(venc):
        rolou = venc.isoformat()
        while not _util_escritorio(venc):
            venc += datetime.timedelta(days=1)

    interno = FER.prazo_interno(venc)     # calendario unico do D-2 (so feriado nacional + ferias da equipe)
    if interno <= base:          # prazo curto demais para caber a folga
        interno = base

    # quantos dias uteis o prazo representa - e assim que a equipe fala dele
    uteis, d = 0, base
    while d < venc:
        d += datetime.timedelta(days=1)
        if _util_escritorio(d):
            uteis += 1

    return {
        "base": base.isoformat(),
        "dias": dias,
        "contagem": "corridos",
        "vencimento": venc.isoformat(),
        "prazo_interno": interno.isoformat(),
        "equivale_uteis": uteis,
        "rolou_de": rolou,
        "uf": uf,
        "observacao": ("Compromisso com o cliente, nao prazo legal. "
                       "Alvo interno 2 dias uteis antes."),
    }


@router.get("/por-intimacao")
def por_intimacao(ids: str = "", user=Depends(usuario_atual)):
    """Quais dessas intimacoes JA viraram tarefa. A tela de Intimacoes usa isso
    pra mostrar "ja tem tarefa" em vez de deixar a pessoa criar a segunda.

    Olha origem 'motor' (o gerador automatico) e 'intimacao' (o botao da tela):
    as duas gravam o id da intimacao em origem_ref."""
    alvo = [x.strip() for x in (ids or "").split(",") if x.strip().isdigit()]
    if not alvo:
        return {"vinculos": {}}
    alvo = alvo[:400]
    c = _con()
    try:
        marcas = ",".join("?" for _ in alvo)
        linhas = c.execute(
            "SELECT origem_ref, id, titulo, tipo, status, origem FROM tarefas "
            "WHERE origem IN ('motor','intimacao') AND origem_ref IN (" + marcas + ")",
            alvo).fetchall()
        out = {}
        for r in linhas:
            out.setdefault(str(r["origem_ref"]), []).append(
                {"id": r["id"], "titulo": r["titulo"],
                 "tipo": r["tipo"], "status": r["status"], "origem": r["origem"]})
        return {"vinculos": out}
    finally:
        c.close()


@router.get("/{tarefa_id}")
def obter(tarefa_id: int, user=Depends(usuario_atual)):
    c = _con()
    try:
        r = c.execute("SELECT * FROM tarefas WHERE id=?", (tarefa_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Tarefa nao encontrada")
        t = _montar(c, r, user.id)
        # as irmas da mesma cadeia do motor ("etapa 2 de 4", quem esta com cada uma)
        try:
            if t.get("cadeia"):
                from .. import motor_tarefas as MT
                t["cadeia_etapas"] = MT.cadeia_da_tarefa(c, tarefa_id)
                # passo a passo (07/10/2026): a proxima etapa sugerida, para o botao "Concluir e encaminhar"
                t["proxima_sugerida"] = MT.resumo_proxima(c, t)
                t["link_sugerido"] = MT.link_editavel(t.get("descricao"))
        except Exception as e:
            log.warning("cadeia da tarefa %s: %s", tarefa_id, e)
        return t
    finally:
        c.close()


# ============================================================
# ATIVIDADE: responder a quem mandou a tarefa (Advogada, 06/10/2026)
# Como no ADVBOX, embaixo do perfil. A resposta fica no fio desta tarefa
# e vira tarefa nova para quem foi marcado, com o link de volta.
# Regras e bateria em app/tarefas_atividade.py.
# ============================================================

class RespostaIn(BaseModel):
    texto: str
    para: List[int] = []
    tipo: Optional[str] = None
    prazo_fatal: Optional[str] = None
    prazo_interno: Optional[str] = None
    data: Optional[str] = None


@router.get("/{tarefa_id}/comentarios")
def comentarios(tarefa_id: int, user=Depends(usuario_atual)):
    c = _con()
    try:
        try:
            fio = tarefas_atividade.listar(c, tarefa_id)
        except LookupError:
            raise HTTPException(404, "Tarefa nao encontrada")
        fio["eu"] = {"id": user.id, "nome": user.nome}
        return fio
    finally:
        c.close()


@router.post("/{tarefa_id}/responder")
def responder(tarefa_id: int, dados: RespostaIn, user=Depends(usuario_atual)):
    c = _con()
    try:
        try:
            r = tarefas_atividade.responder(
                c, tarefa_id, dados.texto, dados.para, dados.tipo, user.id, user.nome,
                dados.prazo_fatal, dados.prazo_interno, dados.data, tarefas_prazo.pfi_de)
        except LookupError:
            raise HTTPException(404, "Tarefa nao encontrada")
        except ValueError as e:
            raise HTTPException(400, str(e))
        c.commit()
        nid = r["tarefa_id"]
        nova = _montar(c, c.execute("SELECT * FROM tarefas WHERE id=?", (nid,)).fetchone(), user.id)
        # igual ao criar: parceiro (id negativo) recebe por WhatsApp; o prazo interno vai para a agenda
        avisados, falhas = [], []
        for p in r["para"]:
            parc = _parceiro_de(p["usuario_id"])
            if not parc:
                continue
            ok, info = _avisar_parceiro(parc, {k: nova.get(k) for k in (
                "tipo", "titulo", "descricao", "processo_numero", "cliente", "prazo_fatal")},
                user.nome or user.email)
            (avisados if ok else falhas).append(
                parc["nome"] if ok else "%s (%s)" % (parc["nome"], str(info)[:60]))
        nova["avisados"] = avisados
        nova["aviso_falhou"] = falhas
        ev, erro_ag = _agendar_pfi(c, nid, user.id)
        nova["evento_id"] = ev
        if erro_ag and erro_ag != "sem prazo interno":
            nova["agenda_falhou"] = erro_ag
        fio = tarefas_atividade.listar(c, tarefa_id)
        fio["eu"] = {"id": user.id, "nome": user.nome}
        return {"comentario": r["comentario"], "tarefa": nova, "fio": fio}
    finally:
        c.close()


# ============================================================
# ESCRITA
# ============================================================

def _emails_responsaveis(c, tarefa_id):
    """E-mail de quem tem login. Parceiro entra com id NEGATIVO e nao tem
    agenda nossa: ele continua sendo avisado por WhatsApp, como ja era."""
    q = ("SELECT u.email FROM tarefa_responsaveis r JOIN usuarios u ON u.id = r.usuario_id "
         "WHERE r.tarefa_id=? AND r.usuario_id > 0 AND TRIM(COALESCE(u.email,'')) != ''")
    return [r["email"] for r in c.execute(q, (tarefa_id,))]


def _agendar_pfi(c, tarefa_id, user_id):
    """Marca o prazo interno na agenda dos responsaveis. Best-effort, igual ao
    aviso de parceiro: se o Google recusar, a tarefa ja esta gravada."""
    linha = c.execute("SELECT * FROM tarefas WHERE id=?", (tarefa_id,)).fetchone()
    if not linha:
        return None, "tarefa nao encontrada"
    t = dict(linha)
    # T2 (fase 0, 03/11/2026): tarefa cancelada ou concluida nao volta para a agenda
    if (t.get("status") or "aberta") != "aberta":
        return None, "tarefa %s" % t.get("status")
    if not t.get("prazo_interno"):
        return None, "sem prazo interno"
    if t.get("evento_id"):
        return t["evento_id"], None                # ja marcado, nao duplica
    ev, erro = tarefas_prazo.marcar_na_agenda(t, _emails_responsaveis(c, tarefa_id))
    if ev:
        c.execute("UPDATE tarefas SET evento_id=? WHERE id=?", (ev, tarefa_id))
        c.commit()
    return ev, erro


def _desagendar_pfi(c, tarefa_id):
    linha = c.execute("SELECT evento_id FROM tarefas WHERE id=?", (tarefa_id,)).fetchone()
    if not linha or not linha["evento_id"]:
        return
    # revisao de 03/11/2026: so zera se o Google apagou; se falhou, guarda o id para nova tentativa
    if tarefas_prazo.apagar_da_agenda(linha["evento_id"]):
        c.execute("UPDATE tarefas SET evento_id=NULL WHERE id=?", (tarefa_id,))
    else:
        log.warning("agenda: evento %s da tarefa %s nao foi apagado; fica para nova tentativa",
                    linha["evento_id"], tarefa_id)


def _norm_tipo(s):
    """Tipo de tarefa sem acento, em maiusculas: 'PETIÇÃO DE MEIO' e 'PETICAO DE MEIO' gravam igual
    (ajuste 5, criterio 8). A regra mora em tarefas_tipos.norm_tipo, a mesma do motor (fase 2, T1)."""
    return TT.norm_tipo(s)


def _mesmo_tipo_na_intimacao(c, intimacao_ref, tipo, fora_id=None):
    """Tarefa NAO cancelada desta intimacao com o mesmo tipo (sem acento), da tela ou da cadeia do motor.
    Decisao da Advogada (secao 10 do doc "Specs e roadmap: intimacoes e tarefas", Ajuste 5): uma por tipo."""
    if not intimacao_ref:
        return None
    for r in c.execute("SELECT id, tipo, status FROM tarefas WHERE origem IN ('motor','intimacao') "
                       "AND origem_ref=? AND COALESCE(status,'aberta') != 'cancelada'", (str(intimacao_ref),)):
        if r["id"] != fora_id and _norm_tipo(r["tipo"]) == tipo:
            return r
    return None


def _gravar_responsaveis(c, tarefa_id, responsaveis):
    c.execute("DELETE FROM tarefa_responsaveis WHERE tarefa_id=?", (tarefa_id,))
    for r in responsaveis:
        nome = r.nome
        if r.usuario_id and not nome:
            u = c.execute("SELECT nome FROM usuarios WHERE id=?", (r.usuario_id,)).fetchone()
            nome = u["nome"] if u else None
        c.execute(
            "INSERT OR REPLACE INTO tarefa_responsaveis "
            "(tarefa_id, usuario_id, nome, importante, urgente) VALUES (?,?,?,?,?)",
            (tarefa_id, r.usuario_id, nome, int(r.importante), int(r.urgente)))


@router.post("")
def criar(dados: TarefaIn, user=Depends(usuario_atual)):
    if dados.prioridade not in PRIORIDADES:
        raise HTTPException(400, "prioridade invalida")
    # ajuste 5 (03/11/2026): tipo vazio nao grava; tipo sem acento, como o motor grava
    dados.tipo = _norm_tipo(dados.tipo)
    if not dados.tipo:
        raise HTTPException(400, "Escolha o tipo da tarefa")
    # PFI automatico: tem prazo processual e ninguem preencheu o interno?
    # D-2 uteis, o padrao do escritorio (ver app/tarefas_prazo.py).
    pfi, calculado = tarefas_prazo.completar_prazos(dados)
    if calculado:
        dados.prazo_interno = pfi
    c = _con()
    try:
        # uma tarefa por tipo na intimacao: recusa ANTES de gravar, valendo tambem para a cadeia do
        # motor e para tipos gravados com acento (decisao da Advogada, secao 10, Ajuste 5)
        ja = _mesmo_tipo_na_intimacao(c, dados.intimacao_id, dados.tipo)
        if ja:
            raise HTTPException(409, "Já existe a tarefa #%d do tipo %s para esta intimação (%s)"
                                % (ja["id"], dados.tipo, ja["status"] or "aberta"))
        agora = _agora()
        cur = c.execute(
            "INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, lawsuits_id, "
            "cliente, parte_adversa, pasta_drive, data, hora, prazo_fatal, prazo_interno, "
            "trilha, prioridade, contato_nome, contato_telefone, conversa_id, "
            "status, origem, origem_ref, criado_por, criado_em, atualizado_em) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'aberta',?,?,?,?,?)",
            (dados.tipo, dados.titulo, dados.descricao, dados.processo_numero,
             dados.lawsuits_id, dados.cliente, dados.parte_adversa, dados.pasta_drive,
             dados.data, dados.hora, dados.prazo_fatal, dados.prazo_interno,
             dados.trilha, dados.prioridade, dados.contato_nome,
             dados.contato_telefone, dados.conversa_id,
             ("intimacao" if dados.intimacao_id else
              ("chat" if dados.conversa_id else "manual")),
             (str(dados.intimacao_id) if dados.intimacao_id else None),
             user.nome, agora, agora))
        tid = cur.lastrowid
        _gravar_responsaveis(c, tid, dados.responsaveis)
        _evento(c, tid, dados.processo_numero, "criada",
                "criada: %s (%s)" % (dados.tipo, "intimacao" if dados.intimacao_id else "manual"),
                user.nome or user.email)
        c.commit()
        feita = _montar(c, c.execute("SELECT * FROM tarefas WHERE id=?", (tid,)).fetchone(),
                        user.id)
        # PARCEIRO nao entra no sistema: se a tarefa e dele, ele so fica
        # sabendo se avisarmos. Best-effort - falha no WhatsApp nao pode
        # desfazer uma tarefa que ja esta gravada.
        avisados, falhas = [], []
        for r in (dados.responsaveis or []):
            p = _parceiro_de(r.usuario_id)
            if not p:
                continue
            ok, info = _avisar_parceiro(p, dados.dict() if hasattr(dados, "dict")
                                        else {}, user.nome or user.email)
            (avisados if ok else falhas).append(
                p["nome"] if ok else "%s (%s)" % (p["nome"], str(info)[:60]))
        feita["avisados"] = avisados
        feita["aviso_falhou"] = falhas
        # o prazo interno vai para a agenda de quem e responsavel
        ev, erro_ag = _agendar_pfi(c, tid, user.id)
        feita["evento_id"] = ev
        feita["prazo_interno_calculado"] = bool(calculado)
        if erro_ag and erro_ag != "sem prazo interno":
            feita["agenda_falhou"] = erro_ag
        return feita
    except sqlite3.IntegrityError:
        # (origem, origem_ref, tipo) e UNIQUE. Cair aqui significa que essa
        # intimacao ja gerou esse tipo de tarefa - avisa em vez de estourar 500.
        c.rollback()
        ja = c.execute("SELECT id, status FROM tarefas WHERE origem='intimacao' AND origem_ref=? AND tipo=?",
                       (str(dados.intimacao_id or ''), dados.tipo)).fetchone()
        raise HTTPException(409, "Já existe uma tarefa deste tipo para esta intimação"
                                 + (" (#%d, %s)" % (ja["id"], ja["status"] or "aberta") if ja else ""))
    finally:
        c.close()


@router.patch("/{tarefa_id}")
def editar(tarefa_id: int, dados: TarefaPatch, user=Depends(usuario_atual)):
    c = _con()
    try:
        atual = c.execute("SELECT * FROM tarefas WHERE id=?",
                          (tarefa_id,)).fetchone()
        if not atual:
            raise HTTPException(404, "Tarefa nao encontrada")
        campos = dados.model_dump(exclude_unset=True, exclude={"responsaveis", "motivo"})
        motivo = (dados.motivo or "").strip()
        # ajuste 5 (03/11/2026): tipo sem acento, nunca vazio; tarefa da cadeia do fechamento nao troca de
        # tipo; e uma por tipo na intimacao (decisoes da Advogada, secao 10 do doc, Ajuste 5)
        if "tipo" in campos:
            campos["tipo"] = _norm_tipo(campos["tipo"])
            if not campos["tipo"]:
                raise HTTPException(400, "Escolha o tipo da tarefa")
            if campos["tipo"] == _norm_tipo(atual["tipo"]):
                # mesmo tipo (no maximo com outro acento): nao regrava. Assim editar o titulo de uma
                # tarefa antiga "PETIÇÃO DE MEIO" nao esbarra no UNIQUE de uma irma "PETICAO DE MEIO".
                campos.pop("tipo")
            elif str(atual["cadeia"] or "").startswith("intim:") or atual["origem"] == "motor":
                # so a tarefa nascida da cadeia do fechamento; a raiz "tarefa:N" criada a mao segue livre
                raise HTTPException(409, "Tarefa da cadeia do fechamento não troca de tipo: "
                                         "cancele com motivo e crie outra")
            else:
                if atual["origem"] in ("motor", "intimacao"):
                    ja = _mesmo_tipo_na_intimacao(c, atual["origem_ref"], campos["tipo"], fora_id=tarefa_id)
                    if ja:
                        raise HTTPException(409, "Já existe a tarefa #%d do tipo %s para esta intimação (%s)"
                                            % (ja["id"], campos["tipo"], ja["status"] or "aberta"))
                # o indice UNIQUE(origem, origem_ref, tipo) conta tambem as canceladas: confere ANTES de
                # qualquer efeito na agenda, para nunca apagar o evento e depois voltar com 409
                colide = c.execute("SELECT id, status FROM tarefas WHERE origem=? AND origem_ref=? AND tipo=? "
                                   "AND id != ?", (atual["origem"], atual["origem_ref"], campos["tipo"],
                                                   tarefa_id)).fetchone() if atual["origem_ref"] else None
                if colide:
                    raise HTTPException(409, "Já existe a tarefa #%d do tipo %s nesta origem (%s): "
                                             "reabra a tarefa ou escolha outro tipo"
                                        % (colide["id"], campos["tipo"], colide["status"] or "aberta"))
        if campos.get("status") and campos["status"] not in STATUS_VALIDOS:
            raise HTTPException(400, "status invalido")
        if campos.get("prioridade") and campos["prioridade"] not in PRIORIDADES:
            raise HTTPException(400, "prioridade invalida")
        # fase 2 (03/11/2026): sem motivo nao cancela, antes de qualquer efeito (decisao da Advogada, Ajustes 4 e 6)
        cancelando = campos.get("status") == "cancelada" and (atual["status"] or "aberta") != "cancelada"
        if cancelando and not motivo:
            raise HTTPException(400, "Para cancelar a tarefa, informe o motivo.")
        # mudou o prazo processual sem dizer o interno? recalcula o D-2.
        if "prazo_fatal" in campos and "prazo_interno" not in campos:
            novo = tarefas_prazo.pfi_de(campos["prazo_fatal"])
            if novo:
                campos["prazo_interno"] = novo
        if campos:
            sets = ", ".join(k + "=?" for k in campos)
            c.execute("UPDATE tarefas SET " + sets + ", atualizado_em=? WHERE id=?",
                      list(campos.values()) + [_agora(), tarefa_id])
        # fase 2 (03/11/2026): eventos. Campos que mudaram (so os nomes: o prazo fatal nunca vai ao texto) e a
        # mudanca de status, cada uma com a acao propria
        por = user.nome or user.email
        mudou = [k for k in campos if k != "status" and str(campos[k] or "") != str(atual[k] or "")
                and not (k == "prioridade" and (campos[k] or "normal") == (atual[k] or "normal"))]
        if mudou:
            _evento(c, tarefa_id, atual["processo_numero"], "editada", "campos alterados: " + ", ".join(mudou), por)
        novo_status = campos.get("status")
        if novo_status and novo_status != (atual["status"] or "aberta"):
            if novo_status == "cancelada":
                _evento(c, tarefa_id, atual["processo_numero"], "cancelada", "cancelada. Motivo: " + motivo, por)
            elif novo_status == "concluida":
                _evento(c, tarefa_id, atual["processo_numero"], "concluida", "concluida pela edicao da tarefa", por)
            else:
                _evento(c, tarefa_id, atual["processo_numero"], "reaberta",
                        "reaberta (estava %s)" % atual["status"], por)
        # se o prazo mudou, o compromisso velho nao vale mais; e cancelar ou concluir pelo
        # PATCH tira o PFI da agenda (T2, fase 0, 03/11/2026: antes o evento ficava la).
        # Depois do UPDATE: se ele recusar, a agenda nao foi tocada (revisao de 03/11).
        if ("prazo_interno" in campos or "prazo_fatal" in campos
                or campos.get("status") in ("cancelada", "concluida")):
            _desagendar_pfi(c, tarefa_id)
        if dados.responsaveis is not None:
            _gravar_responsaveis(c, tarefa_id, dados.responsaveis)
        c.commit()
        if "prazo_interno" in campos or "prazo_fatal" in campos or dados.responsaveis is not None:
            _agendar_pfi(c, tarefa_id, user.id)
        return _montar(c, c.execute("SELECT * FROM tarefas WHERE id=?", (tarefa_id,)).fetchone(),
                       user.id)
    except sqlite3.IntegrityError:
        # UNIQUE(origem, origem_ref, tipo): tipo repetido no PATCH era 500; agora 409 legivel (criterio 7)
        c.rollback()
        ja = c.execute("SELECT id, status FROM tarefas WHERE origem=? AND origem_ref=? AND tipo=? AND id != ?",
                       (atual["origem"], atual["origem_ref"], campos.get("tipo"), tarefa_id)).fetchone()
        raise HTTPException(409, "Já existe uma tarefa deste tipo para esta intimação"
                                 + (" (#%d, %s)" % (ja["id"], ja["status"] or "aberta") if ja else ""))
    finally:
        c.close()


@router.post("/{tarefa_id}/concluir")
def concluir(tarefa_id: int, todos: bool = False, user=Depends(usuario_atual)):
    """
    Conclui a tarefa PARA O USUARIO ATUAL (modelo ADVBOX).
    `todos=true` fecha para todo mundo - use quando a tarefa acabou de fato.
    """
    c = _con()
    try:
        t = c.execute("SELECT id, processo_numero, status FROM tarefas WHERE id=?", (tarefa_id,)).fetchone()
        if not t:
            raise HTTPException(404, "Tarefa nao encontrada")
        # revisao de 03/11/2026: cancelada nao vira concluida; concluida de novo e idempotente, sem evento
        if todos and t["status"] == "cancelada":
            raise HTTPException(409, "A tarefa esta cancelada. Reabra antes de concluir.")
        if todos and t["status"] == "concluida":
            return _montar(c, c.execute("SELECT * FROM tarefas WHERE id=?", (tarefa_id,)).fetchone(), user.id)
        agora = _agora()
        if todos:
            c.execute("UPDATE tarefa_responsaveis SET concluido_em=? "
                      "WHERE tarefa_id=? AND concluido_em IS NULL", (agora, tarefa_id))
            # acabou de verdade: o PFI sai da agenda de todo mundo
            _desagendar_pfi(c, tarefa_id)
        else:
            n = c.execute("UPDATE tarefa_responsaveis SET concluido_em=? "
                          "WHERE tarefa_id=? AND usuario_id=? AND concluido_em IS NULL",
                          (agora, tarefa_id, user.id)).rowcount
            if not n:
                raise HTTPException(403, "Voce nao e responsavel por esta tarefa")

        falta = c.execute("SELECT COUNT(*) FROM tarefa_responsaveis "
                          "WHERE tarefa_id=? AND concluido_em IS NULL",
                          (tarefa_id,)).fetchone()[0]
        if falta == 0:
            c.execute("UPDATE tarefas SET status='concluida', concluida_em=?, "
                      "atualizado_em=? WHERE id=?", (agora, agora, tarefa_id))
        _evento(c, tarefa_id, t["processo_numero"], "concluida" if falta == 0 else "baixa",
                ("baixa para todos os responsaveis" if todos else "baixa de %s" % (user.nome or user.email))
                + ("; tarefa concluida" if falta == 0 else "; faltam %d responsavel(is)" % falta),
                user.nome or user.email)
        c.commit()
        feita = _montar(c, c.execute("SELECT * FROM tarefas WHERE id=?", (tarefa_id,)).fetchone(),
                        user.id)
        # Motor das tarefas (02/10/2026): a PESSOA concluiu; o motor so deixa a
        # etapa seguinte da cadeia na vez, enfileira a revisao automatica e, no
        # protocolo, marca [ok] no PF e abre ATUALIZAR CLIENTE. Best-effort.
        if falta == 0 and feita.get("cadeia"):
            try:
                from .. import motor_tarefas as MT
                from .. import agenda_google as AG
                feita["motor"] = MT.ao_concluir(c, tarefa_id, user.nome or user.email,
                                                AG if AG.disponivel() else None)
                # passo a passo (07/10/2026): o Concluir comum tambem faz a proxima etapa nascer
                nova = MT.apos_concluir(c, tarefa_id, user.nome or user.email, AG if AG.disponivel() else None)
                if nova and nova.get("id") and not nova.get("ja_existia"):
                    if not nova.get("automacao"):
                        _agendar_pfi(c, nova["id"], user.id)
                    feita["nova"] = _montar(c, c.execute("SELECT * FROM tarefas WHERE id=?", (nova["id"],)).fetchone(), user.id)
            except Exception as e:
                log.warning("motor ao concluir %s: %s", tarefa_id, e)
                feita["motor"] = {"erro": str(e)[:160]}
        return feita
    finally:
        c.close()


class EncaminharIn(BaseModel):
    link: Optional[str] = None
    obs: Optional[str] = None


@router.post("/{tarefa_id}/encaminhar")
def encaminhar(tarefa_id: int, dados: EncaminharIn, user=Depends(usuario_atual)):
    """O botao "Concluir e encaminhar" (Advogada, 07/10/2026): conclui esta tarefa e faz nascer a proxima
    etapa do gabarito da cadeia para o responsavel, com o link da entrega. Uma linha REFAZER: na
    descricao manda a peca de volta para a Assistente. Regras em app/motor_tarefas.py (passo a passo)."""
    c = _con()
    try:
        from .. import motor_tarefas as MT
        from .. import agenda_google as AG
        try:
            r = MT.encaminhar(c, tarefa_id, user.nome or user.email, dados.link, dados.obs,
                              AG if AG.disponivel() else None)
        except LookupError:
            raise HTTPException(404, "Tarefa nao encontrada")
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:
            _desagendar_pfi(c, tarefa_id)
            c.commit()
        except Exception as e:
            log.warning("desagendar %s: %s", tarefa_id, e)
        try:
            # a funcao do motor ja grava o evento da tarefa; aqui entra o do processo (fase 2, T5)
            proc = c.execute("SELECT processo_numero FROM tarefas WHERE id=?", (tarefa_id,)).fetchone()
            _evento(c, tarefa_id, proc["processo_numero"] if proc else None, "encaminhada",
                    "encaminhada para #%s" % ((r.get("nova") or {}).get("id") or "fim da cadeia"),
                    user.nome or user.email, so_processo=True)
            c.commit()
        except Exception as e:
            log.warning("historico do processo %s: %s", tarefa_id, e)
        out = {"concluida": tarefa_id, "refazer": r.get("refazer"), "links": r.get("links"), "nova": None}
        nova = r.get("nova") or {}
        if nova.get("id"):
            if not nova.get("automacao") and not nova.get("ja_existia"):
                ev, erro = _agendar_pfi(c, nova["id"], user.id)
                if erro and erro != "sem prazo interno":
                    out["agenda_falhou"] = erro
            out["nova"] = _montar(c, c.execute("SELECT * FROM tarefas WHERE id=?", (nova["id"],)).fetchone(), user.id)
            out["nova"]["automacao"] = bool(nova.get("automacao"))
        return out
    finally:
        c.close()


@router.post("/{tarefa_id}/reabrir")
def reabrir(tarefa_id: int, user=Depends(usuario_atual)):
    c = _con()
    try:
        r = c.execute("SELECT id, status, processo_numero FROM tarefas WHERE id=?", (tarefa_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Tarefa nao encontrada")
        c.execute("UPDATE tarefa_responsaveis SET concluido_em=NULL WHERE tarefa_id=?",
                  (tarefa_id,))
        c.execute("UPDATE tarefas SET status='aberta', concluida_em=NULL, atualizado_em=? "
                  "WHERE id=?", (_agora(), tarefa_id))
        _evento(c, tarefa_id, r["processo_numero"], "reaberta", "reaberta (estava %s)" % (r["status"] or "aberta"),
                user.nome or user.email)
        c.commit()
        # T2 (03/11/2026): cancelar e concluir tiram o PFI da agenda; reabrir devolve
        _agendar_pfi(c, tarefa_id, user.id)
        return _montar(c, c.execute("SELECT * FROM tarefas WHERE id=?", (tarefa_id,)).fetchone(),
                       user.id)
    finally:
        c.close()


# ============================================================
# GERADOR: intimacao classificada -> card de tarefa
# ============================================================

def _mapa_usuarios(c):
    """Primeiro nome (sem acento, minusculo) -> id. Casa 'Advogada'/'Advogada' do motor."""
    import unicodedata

    def chave(nome):
        n = unicodedata.normalize("NFKD", nome or "")
        n = "".join(ch for ch in n if not unicodedata.combining(ch))
        return n.strip().lower().split(" ")[0]

    return {chave(r["nome"]): r["id"]
            for r in c.execute("SELECT id, nome FROM usuarios WHERE ativo=1")}


def _intimacoes_do_cache(c):
    """id -> dados da intimacao (processo, tribunal, orgao, resumo)."""
    import json as _json
    row = c.execute("SELECT dados FROM sync_cache WHERE chave='intimacoes'").fetchone()
    if not row:
        return {}
    try:
        d = _json.loads(row["dados"])
    except (ValueError, TypeError):
        return {}
    itens = d.get("intimacoes") if isinstance(d, dict) else d
    return {i.get("id"): i for i in (itens or []) if isinstance(i, dict)}


@router.post("/gerar-do-motor")
def gerar_do_motor(
    limite: int = Query(200, le=1000),
    simular: bool = Query(True, description="Padrao: so mostra o que criaria"),
    user=Depends(usuario_atual),
):
    """
    Transforma as intimacoes PENDENTES ja classificadas em cards de tarefa.

    Percorre o cache de intimacoes pendentes (que e o que ainda exige acao) e
    busca a classificacao de cada uma em sync_motor_sombra. O caminho inverso
    (varrer a tabela do motor) traria 169 linhas de historico ja tratado, a
    maioria sem numero de processo resolvivel - card sem processo nao serve
    para a controladoria.

    Idempotente em duas camadas:
      1. UNIQUE (origem, origem_ref, tipo)
      2. deduplicacao por (processo, ato, vencimento) - o DJEN repete a mesma
         intimacao uma vez por advogado destinatario.

    Nao cria card para trilha CIENCIA nem para titularidade CONTRARIA.
    """
    c = _con()
    try:
        usuarios = _mapa_usuarios(c)
        intimacoes = _intimacoes_do_cache(c)
        agora = _agora()
        criados, pulados, sem_responsavel, sem_classificacao = [], 0, 0, 0
        vistos = set()

        for iid, inti in list(intimacoes.items())[:limite]:
            m = c.execute("SELECT * FROM sync_motor_sombra WHERE id=? "
                          "ORDER BY criado_em DESC LIMIT 1", (iid,)).fetchone()
            if not m:
                sem_classificacao += 1
                continue
            if m["trilha"] not in ("PECA", "MEIO") or m["titularidade"] == "CONTRARIA":
                continue

            tipo = m["ato"] or TT.ANALISE
            processo = inti.get("processo")

            # 2a camada: o DJEN repete a mesma intimacao por advogado.
            chave = (processo, tipo, m["vencimento"])
            if chave in vistos:
                pulados += 1
                continue
            vistos.add(chave)

            ref = str(iid)
            if c.execute("SELECT id FROM tarefas WHERE origem='motor' AND origem_ref=? "
                         "AND tipo=?", (ref, tipo)).fetchone():
                pulados += 1
                continue
            # mesma tarefa ja criada por outra intimacao irma
            if processo and c.execute(
                "SELECT id FROM tarefas WHERE origem='motor' AND processo_numero=? "
                "AND tipo=? AND IFNULL(prazo_fatal,'')=IFNULL(?,'')",
                (processo, tipo, m["vencimento"])).fetchone():
                pulados += 1
                continue

            titulo = tipo + " - " + processo if processo else tipo
            descricao = inti.get("resumo") or ""
            avisos = m["avisos"]
            if avisos and str(avisos) not in ("[]", "None"):
                descricao = (descricao + "\n\nAvisos do motor: " + str(avisos)).strip()

            import unicodedata
            bruto = m["responsavel"] or ""
            n = unicodedata.normalize("NFKD", bruto)
            n = "".join(ch for ch in n if not unicodedata.combining(ch))
            uid = usuarios.get(n.strip().lower().split(" ")[0])
            if uid is None:
                sem_responsavel += 1

            prioridade = "urgente" if m["fatal"] else "normal"
            payload = {
                "tipo": tipo, "titulo": titulo, "processo_numero": processo,
                "prazo_fatal": m["vencimento"], "prazo_interno": m["fatal_interno"],
                "trilha": m["trilha"], "prioridade": prioridade,
                "responsavel": m["responsavel"], "responsavel_id": uid,
                "tribunal": inti.get("tribunal"), "orgao": inti.get("orgao"),
                "confianca": m["confianca"],
            }
            if simular:
                criados.append(payload)
                continue

            cur = c.execute(
                "INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, "
                "prazo_fatal, prazo_interno, trilha, prioridade, status, origem, "
                "origem_ref, criado_por, criado_em, atualizado_em) "
                "VALUES (?,?,?,?,?,?,?,?,'aberta','motor',?,?,?,?)",
                (tipo, titulo, descricao or None, processo, m["vencimento"],
                 m["fatal_interno"], m["trilha"], prioridade, ref,
                 "motor da controladoria", agora, agora))
            tid = cur.lastrowid
            if uid is not None:
                c.execute(
                    "INSERT OR REPLACE INTO tarefa_responsaveis "
                    "(tarefa_id, usuario_id, nome, importante, urgente) VALUES (?,?,?,?,?)",
                    (tid, uid, m["responsavel"], 1 if m["fatal"] else 0,
                     1 if m["fatal"] else 0))
            criados.append(payload)

        if not simular:
            c.commit()

        return {
            "simulado": simular,
            "criados": len(criados),
            "ja_existiam_ou_repetidas": pulados,
            "sem_classificacao_no_motor": sem_classificacao,
            "sem_responsavel_mapeado": sem_responsavel,
            "amostra": criados[:12],
        }
    finally:
        c.close()


@router.delete("/{tarefa_id}")
def excluir(tarefa_id: int, user=Depends(usuario_atual)):
    if getattr(user, "papel", "") != "admin":
        raise HTTPException(403, "Somente admin exclui tarefa. Use cancelar.")
    c = _con()
    try:
        antes = c.execute("SELECT processo_numero, tipo FROM tarefas WHERE id=?", (tarefa_id,)).fetchone()
        if antes:
            _evento(c, tarefa_id, antes["processo_numero"], "excluida", "excluida: %s" % antes["tipo"],
                    user.nome or user.email)
        _desagendar_pfi(c, tarefa_id)
        c.execute("DELETE FROM tarefa_responsaveis WHERE tarefa_id=?", (tarefa_id,))
        c.execute("DELETE FROM tarefas WHERE id=?", (tarefa_id,))
        c.commit()
        return {"ok": True}
    finally:
        c.close()
