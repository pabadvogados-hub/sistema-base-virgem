"""
LINHA DO TEMPO DO PROCESSO - Sistema Escritório Modelo (28/09/2026, migracao do ADVBOX).

Junta, do mais novo para o mais antigo:
  - o historico do ADVBOX (tarefas e comentarios), copiado so lendo;
  - o que a equipe escreve aqui (andamento/comentario);
  - as tarefas do sistema daquele processo;
  - as mudancas feitas no cadastro (fase, partes, encerramento).

Escrever um andamento pode AVISAR pessoas: cada uma recebe uma tarefa
COMENTARIO na fila (o mesmo que o ADVBOX faz), que ela conclui quando ler.
Abrir a linha do tempo le o ADVBOX daquele processo se a copia tiver mais de
uma hora (so leitura; se o ADVBOX estiver no limite, mostra a copia).
Nada aqui escreve no ADVBOX.
"""
import asyncio
import datetime as dt
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import andamentos_nucleo as A
from ..deps import usuario_atual

router = APIRouter(prefix="/api/andamentos", tags=["andamentos"])

FRESCOR_MIN = 60           # le o ADVBOX de novo se a copia tiver mais que isso


def _nome(user):
    return getattr(user, "nome", None) or getattr(user, "email", None) or "sistema"


def _admin(user):
    return (getattr(user, "papel", "") or "") == "admin"


def _processo(c, processo_id=None, numero=None):
    if processo_id:
        r = c.execute("SELECT id, numero, advbox_id FROM jur_processos WHERE id=?", (processo_id,)).fetchone()
    elif numero:
        r = c.execute("SELECT id, numero, advbox_id FROM jur_processos WHERE numero=?", (numero.strip(),)).fetchone()
    else:
        raise HTTPException(400, "informe o processo")
    return dict(r) if r else None


def _precisa_ler(c, p):
    if not p or not p.get("advbox_id"):
        return False
    s = c.execute("SELECT sincronizado_em FROM jur_andamentos_sync WHERE processo_id=?", (p["id"],)).fetchone()
    if not s or not s[0]:
        return True
    try:
        ultimo = dt.datetime.strptime(s[0], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return (dt.datetime.now() - ultimo).total_seconds() > FRESCOR_MIN * 60


def _txt_ts(v):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(v)))
    except (TypeError, ValueError):
        return str(v or "")[:19]


def _itens(c, p, numero, user):
    out = []
    if p:
        for r in c.execute("SELECT * FROM jur_andamentos WHERE processo_id=? AND removido=0", (p["id"],)):
            meu = r["origem"] != "advbox" and (r["autor_id"] == user.id or _admin(user))
            out.append({"id": r["id"], "kind": "andamento", "origem": r["origem"], "tipo": r["tipo"],
                        "texto": r["texto"], "autor": r["autor"], "responsavel": r["responsavel"],
                        "quando": r["criado_em"], "prazo": r["prazo"], "editado_em": r["editado_em"],
                        "pode_editar": bool(meu)})
        for r in c.execute("SELECT acao, detalhe, por, em FROM jur_historico WHERE entidade='processo' "
                           "AND entidade_id=?", (p["id"],)):
            out.append({"kind": "cadastro", "acao": r["acao"], "detalhe": r["detalhe"], "autor": r["por"],
                        "quando": str(r["em"] or "")[:19]})
    num = (p or {}).get("numero") or numero
    if num:
        # tarefas nascidas no sistema; as do ADVBOX ja estao no historico dele, e
        # as de aviso de comentario sao o proprio andamento
        for t in c.execute("SELECT id, tipo, titulo, descricao, status, criado_por, criado_em, concluida_em, prazo_fatal "
                           "FROM tarefas WHERE processo_numero=? AND COALESCE(origem,'') NOT IN ('advbox','comentario')",
                           (num,)).fetchall():
            resp = [x[0] for x in c.execute("SELECT nome FROM tarefa_responsaveis WHERE tarefa_id=?", (t["id"],))]
            out.append({"kind": "tarefa", "tarefa_id": t["id"], "tipo": t["tipo"], "titulo": t["titulo"],
                        "texto": (t["descricao"] or "")[:1500], "status": t["status"], "autor": t["criado_por"],
                        "responsavel": ", ".join(x for x in resp if x), "quando": _txt_ts(t["criado_em"]),
                        "concluida_em": _txt_ts(t["concluida_em"]) if t["concluida_em"] else None,
                        "prazo": t["prazo_fatal"]})
    out.sort(key=lambda x: x.get("quando") or "", reverse=True)
    return out


@router.get("")
async def listar(processo: Optional[str] = None, processo_id: Optional[int] = None, atualizar: str = "auto",
                 user=Depends(usuario_atual)):
    def _ler():
        c = A.con()
        try:
            p = _processo(c, processo_id, processo)
            leu = None
            if p and atualizar != "0" and (atualizar == "1" or _precisa_ler(c, p)):
                leu = A.sincronizar(c, p["id"], p["advbox_id"], p["numero"])
                leu = "ok" if leu is not None else "sem_resposta"
            s = c.execute("SELECT sincronizado_em FROM jur_andamentos_sync WHERE processo_id=?",
                          ((p or {}).get("id") or 0,)).fetchone()
            return {"processo": p, "itens": _itens(c, p, processo, user),
                    "advbox_lido_em": s[0] if s else None, "advbox_agora": leu}
        finally:
            c.close()
    return await asyncio.to_thread(_ler)


class AndamentoIn(BaseModel):
    processo_id: Optional[int] = None
    processo: Optional[str] = None
    texto: str
    avisar: List[int] = []


class AndamentoPatch(BaseModel):
    texto: str


def _avisar(c, andamento_id, p, texto, ids, user):
    """Uma tarefa COMENTARIO na fila de cada pessoa avisada (modelo ADVBOX)."""
    ids = [int(i) for i in ids if int(i) > 0]
    if not ids:
        return None
    agora = time.time()
    linha1 = next((x.strip() for x in texto.splitlines() if x.strip()), "Comentário")
    cli = c.execute("SELECT group_concat(nome, ' / ') FROM jur_processo_partes WHERE processo_id=? "
                    "AND COALESCE(papel,'') != 'PARTE CONTRÁRIA'", (p["id"],)).fetchone()[0]
    adv = c.execute("SELECT group_concat(nome, ' / ') FROM jur_processo_partes WHERE processo_id=? "
                    "AND papel = 'PARTE CONTRÁRIA'", (p["id"],)).fetchone()[0]
    tid = c.execute(
        "INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, lawsuits_id, cliente, parte_adversa, data, "
        "prioridade, status, origem, origem_ref, criado_por, criado_em, atualizado_em) "
        "VALUES ('COMENTÁRIO',?,?,?,?,?,?,?,'normal','aberta','comentario',?,?,?,?)",
        (linha1[:140], texto + "\n\nEscrito por %s na linha do tempo do processo." % _nome(user),
         p["numero"] or None, str(p["advbox_id"]) if p.get("advbox_id") else None, cli, adv,
         dt.date.today().isoformat(), "andamento:%d" % andamento_id, _nome(user), agora, agora)).lastrowid
    nomes = []
    for uid in ids:
        r = c.execute("SELECT nome FROM usuarios WHERE id=? AND ativo=1", (uid,)).fetchone()
        if r:
            c.execute("INSERT OR IGNORE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) "
                      "VALUES (?,?,?,0,0)", (tid, uid, r["nome"]))
            nomes.append(r["nome"])
    return tid, nomes


@router.post("")
def criar(dados: AndamentoIn, user=Depends(usuario_atual)):
    texto = (dados.texto or "").strip()
    if not texto:
        raise HTTPException(400, "escreva o andamento")
    if len(texto) > 20000:
        raise HTTPException(400, "texto grande demais")
    c = A.con()
    try:
        p = _processo(c, dados.processo_id, dados.processo)
        if not p:
            raise HTTPException(404, "Processo nao esta no cadastro")
        agora = A.agora_txt()
        aid = c.execute(
            "INSERT INTO jur_andamentos (processo_id, processo_numero, lawsuit_id, tipo, texto, autor, autor_id, "
            "responsavel, criado_em, origem, chave) VALUES (?,?,?,?,?,?,?,?,?,'sistema',?)",
            (p["id"], p["numero"] or None, p["advbox_id"] or None, "COMENTÁRIO", texto, _nome(user), user.id,
             None, agora, "esc:%s:%s" % (user.id, time.time()))).lastrowid
        aviso = _avisar(c, aid, p, texto, dados.avisar or [], user)
        if aviso:
            c.execute("UPDATE jur_andamentos SET responsavel=? WHERE id=?", (", ".join(aviso[1]), aid))
        c.commit()
        return {"ok": True, "id": aid, "tarefa_aviso": aviso[0] if aviso else None,
                "avisados": aviso[1] if aviso else []}
    finally:
        c.close()


def _meu(c, aid, user):
    r = c.execute("SELECT * FROM jur_andamentos WHERE id=? AND removido=0", (aid,)).fetchone()
    if not r:
        raise HTTPException(404, "Andamento nao encontrado")
    if r["origem"] == "advbox":
        raise HTTPException(403, "O que veio do ADVBOX nao se edita aqui")
    if r["autor_id"] != user.id and not _admin(user):
        raise HTTPException(403, "So quem escreveu (ou um administrador) muda este andamento")
    return r


@router.patch("/{aid}")
def editar(aid: int, dados: AndamentoPatch, user=Depends(usuario_atual)):
    texto = (dados.texto or "").strip()
    if not texto:
        raise HTTPException(400, "o andamento nao pode ficar vazio")
    c = A.con()
    try:
        _meu(c, aid, user)
        c.execute("UPDATE jur_andamentos SET texto=?, editado_em=? WHERE id=?", (texto, A.agora_txt(), aid))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.delete("/{aid}")
def apagar(aid: int, user=Depends(usuario_atual)):
    c = A.con()
    try:
        _meu(c, aid, user)
        c.execute("UPDATE jur_andamentos SET removido=1, editado_em=? WHERE id=?", (A.agora_txt(), aid))
        c.commit()
        return {"ok": True}
    finally:
        c.close()
