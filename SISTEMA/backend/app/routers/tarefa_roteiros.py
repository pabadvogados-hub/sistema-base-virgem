"""
ROTEIRO (CHECKLIST) POR TIPO DE TAREFA - Sistema Escritório Modelo (29/09/2026).

Pedido do Titular (audio de 29/09 para a Advogada): cada tarefa traz o seu
checklist, e o assistente do dia diz em que ponto a pessoa esta ("Advogada, hoje
vence a inicial da a equipe; voce ja recebeu, esta no ponto de fazer a entrevista").

Os primeiros roteiros sairam do que o escritorio ja escreve:
  - REVISAO DE PECA: o roteiro que o Titular ditou (recebe, analisa transcricao/
    documentos/inicial, levanta duvidas, entrevista o cliente, finaliza, Advogada protocola);
  - PROTOCOLO: as instrucoes que o fechamento da inicial ja poe na tarefa da Advogada;
  - PREPARACAO PARA AUDIENCIA: os quatro pontos do checklist do vigia de audiencias.
O admin muda ou cria roteiro por PUT /api/roteiros/{tipo}.

Marcar uma etapa grava quem e quando, e escreve na linha do tempo do processo.
"""
import json
import time
import unicodedata
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import andamentos_nucleo as A
from ..deps import usuario_atual

router = APIRouter(prefix="/api/roteiros", tags=["roteiros"])

PADRAO = {
    "REVISAO DE PEÇA": [
        ("recebido", "Recebeu o caso: peça, documentos e transcrição da reunião"),
        ("analise", "Analisou a transcrição, os documentos e a inicial"),
        ("duvidas", "Anotou as dúvidas do caso"),
        ("entrevista", "Fez a entrevista com o cliente"),
        ("finalizou", "Finalizou a inicial com os ajustes"),
        ("protocolo", "Encaminhou para a Advogada protocolar"),
    ],
    "PROTOCOLO": [
        ("versao", "Conferiu que a peça é a versão aprovada na revisão"),
        ("documentos", "Separou os documentos do sumário na pasta DOCUMENTOS PROTOCOLO, na ordem"),
        ("protocolou", "Protocolou no tribunal"),
        ("comprovante", "Salvou o comprovante de protocolo na pasta do caso"),
        ("registrou", "Registrou o protocolo na linha do tempo do processo"),
    ],
    "PREPARAÇÃO PARA AUDIENCIA": [
        ("documentos", "Tudo o que precisava estar juntado está nos autos"),
        ("roteiro", "Roteiro de audiência pronto na pasta do caso (ATOS INTERNOS)"),
        ("cliente", "Cliente e testemunhas cientes de data, hora e local"),
        ("modalidade", "Modalidade confirmada (e despacho, se pedimos mudança)"),
    ],
}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.upper().split())


def garantir(c):
    c.execute("""CREATE TABLE IF NOT EXISTS tarefa_roteiros (
                   tipo_norm TEXT PRIMARY KEY, tipo TEXT, etapas TEXT,
                   atualizado_por TEXT, atualizado_em REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS tarefa_checklist (
                   tarefa_id INTEGER NOT NULL, chave TEXT NOT NULL, feito INTEGER DEFAULT 0,
                   por TEXT, em REAL, PRIMARY KEY (tarefa_id, chave))""")
    if not c.execute("SELECT COUNT(*) FROM tarefa_roteiros").fetchone()[0]:
        for tipo, etapas in PADRAO.items():
            c.execute("INSERT OR IGNORE INTO tarefa_roteiros VALUES (?,?,?,?,?)",
                      (norm(tipo), tipo, json.dumps([{"chave": k, "rotulo": r} for k, r in etapas],
                                                     ensure_ascii=False), "padrão", time.time()))
        c.commit()


def roteiro_do_tipo(c, tipo):
    r = c.execute("SELECT etapas FROM tarefa_roteiros WHERE tipo_norm=?", (norm(tipo),)).fetchone()
    return json.loads(r[0]) if r else None


def progresso(c, tarefa_id, tipo):
    """{etapas, feitos, total, proxima} ou None se o tipo nao tem roteiro."""
    etapas = roteiro_do_tipo(c, tipo)
    if not etapas:
        return None
    marc = {r[0]: r for r in c.execute("SELECT chave, feito, por, em FROM tarefa_checklist WHERE tarefa_id=?",
                                       (tarefa_id,))}
    saida = []
    for e in etapas:
        m = marc.get(e["chave"])
        saida.append({"chave": e["chave"], "rotulo": e["rotulo"], "feito": bool(m and m[1]),
                      "por": m[2] if m and m[1] else None, "em": m[3] if m and m[1] else None})
    feitos = sum(1 for e in saida if e["feito"])
    prox = next((e["rotulo"] for e in saida if not e["feito"]), None)
    return {"etapas": saida, "feitos": feitos, "total": len(saida), "proxima": prox}


def _nome(user):
    return getattr(user, "nome", None) or getattr(user, "email", None) or "sistema"


@router.get("")
def listar(user=Depends(usuario_atual)):
    c = A.con()
    try:
        garantir(c)
        return {"roteiros": [{"tipo": r[0], "etapas": json.loads(r[1]), "atualizado_por": r[2]}
                             for r in c.execute("SELECT tipo, etapas, atualizado_por FROM tarefa_roteiros ORDER BY tipo")]}
    finally:
        c.close()


class Etapa(BaseModel):
    chave: str
    rotulo: str


class RoteiroIn(BaseModel):
    etapas: List[Etapa]


@router.put("/{tipo}")
def salvar(tipo: str, dados: RoteiroIn, user=Depends(usuario_atual)):
    if (getattr(user, "papel", "") or "") != "admin":
        raise HTTPException(403, "So administrador muda roteiro")
    etapas = [{"chave": e.chave.strip()[:40], "rotulo": e.rotulo.strip()[:200]} for e in dados.etapas
              if e.chave.strip() and e.rotulo.strip()]
    if len({e["chave"] for e in etapas}) != len(etapas):
        raise HTTPException(400, "chaves repetidas")
    c = A.con()
    try:
        garantir(c)
        if etapas:
            c.execute("INSERT OR REPLACE INTO tarefa_roteiros VALUES (?,?,?,?,?)",
                      (norm(tipo), tipo.strip(), json.dumps(etapas, ensure_ascii=False), _nome(user), time.time()))
        else:
            c.execute("DELETE FROM tarefa_roteiros WHERE tipo_norm=?", (norm(tipo),))
        c.commit()
        return {"ok": True, "tipo": tipo, "etapas": etapas}
    finally:
        c.close()


def _tarefa(c, tid):
    t = c.execute("SELECT id, tipo, titulo, processo_numero FROM tarefas WHERE id=?", (tid,)).fetchone()
    if not t:
        raise HTTPException(404, "Tarefa nao encontrada")
    return t


@router.get("/tarefa/{tid}")
def da_tarefa(tid: int, user=Depends(usuario_atual)):
    c = A.con()
    try:
        garantir(c)
        t = _tarefa(c, tid)
        return {"tarefa_id": tid, "tipo": t["tipo"], "checklist": progresso(c, tid, t["tipo"])}
    finally:
        c.close()


class MarcaIn(BaseModel):
    feito: bool = True


@router.post("/tarefa/{tid}/{chave}")
def marcar(tid: int, chave: str, dados: MarcaIn, user=Depends(usuario_atual)):
    c = A.con()
    try:
        garantir(c)
        t = _tarefa(c, tid)
        etapas = roteiro_do_tipo(c, t["tipo"]) or []
        etapa = next((e for e in etapas if e["chave"] == chave), None)
        if not etapa:
            raise HTTPException(404, "Etapa nao existe no roteiro deste tipo de tarefa")
        c.execute("INSERT INTO tarefa_checklist (tarefa_id, chave, feito, por, em) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(tarefa_id, chave) DO UPDATE SET feito=excluded.feito, por=excluded.por, em=excluded.em",
                  (tid, chave, int(dados.feito), _nome(user), time.time()))
        # (nao mexe em tarefas.atualizado_em: no espelho do ADVBOX isso marcaria a
        # tarefa como editada aqui e ela pararia de receber mudancas de la)
        # na linha do tempo do processo, para quem abrir o caso ver o andamento
        p = c.execute("SELECT id, numero, advbox_id FROM jur_processos WHERE numero=?",
                      (t["processo_numero"] or "#",)).fetchone() if t["processo_numero"] else None
        if p and dados.feito:
            c.execute("INSERT INTO jur_andamentos (processo_id, processo_numero, lawsuit_id, tipo, texto, autor, "
                      "autor_id, criado_em, origem, chave) VALUES (?,?,?,?,?,?,?,?,'sistema',?)",
                      (p["id"], p["numero"], p["advbox_id"] or None, "CHECKLIST",
                       "%s: %s" % (t["tipo"], etapa["rotulo"]), _nome(user), user.id, A.agora_txt(),
                       "chk:%d:%s:%s" % (tid, chave, time.time())))
        c.commit()
        return {"ok": True, "checklist": progresso(c, tid, t["tipo"])}
    finally:
        c.close()
