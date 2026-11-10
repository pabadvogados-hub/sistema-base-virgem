"""
AUDIENCIAS - Sistema Escritório Modelo (28/09/2026, pedido do Titular: largar o ADVBOX).

A audiencia passa a nascer no sistema. A tela Agenda > Audiencias le e escreve
`audiencia_registro` (ver app/audiencias_nucleo.py): lancar, corrigir,
redesignar, por na agenda central, marcar realizada ou cancelada.

Toda a equipe mexe. O que se muda aqui fica editado_local=1 e o vigia das 6h50
nao desfaz. Redesignar move o evento da agenda (e os lembretes do cliente
passam a valer para a data nova, porque a chave deles e processo+data).
Nada aqui escreve no ADVBOX.
"""
import asyncio
import datetime as dt
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import audiencias_nucleo as N
from ..deps import usuario_atual

router = APIRouter(prefix="/api/audiencias", tags=["audiencias"])

HORA = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')


def _nome(user):
    return getattr(user, "nome", None) or getattr(user, "email", None) or "sistema"


def _data_ok(v, campo="data"):
    try:
        return dt.date.fromisoformat(str(v or "")[:10]).isoformat()
    except ValueError:
        raise HTTPException(400, "%s invalida, use AAAA-MM-DD" % campo)


def _hora_ok(v):
    v = (v or "").strip()
    if v and not HORA.match(v):
        raise HTTPException(400, "hora invalida, use HH:MM")
    return v


def _linha(c, processo, data):
    r = c.execute("SELECT * FROM audiencia_registro WHERE processo=? AND data=?", (processo, data)).fetchone()
    if not r:
        raise HTTPException(404, "Audiencia nao encontrada")
    return dict(r)


def _montar(c, a, hoje):
    d = dt.date.fromisoformat(a["data"])
    a["dias"] = (d - hoje).days
    p = c.execute("SELECT criado_em, alertas, advbox FROM audiencia_providencia WHERE processo=? AND data=?",
                  (a["processo"], a["data"])).fetchone()
    a["providencia"] = dict(p) if p else None
    a["providencia_abre"] = (d - dt.timedelta(days=7)).isoformat()
    t = c.execute("SELECT id, status FROM tarefas WHERE processo_numero=? AND tipo LIKE 'PREPARA%AUDI%' "
                  "ORDER BY id DESC LIMIT 1", (a["processo"],)).fetchone()
    a["tarefa_preparacao"] = dict(t) if t else None
    a["lembretes"] = [r[0] for r in c.execute(
        "SELECT marco FROM agenda_lembretes WHERE evento_id=? ORDER BY enviado_em",
        ("aud:%s:%s" % (a["processo"], a["data"]),))]
    a["rotulo"] = N.rotulo(a)
    return a


@router.get("")
def listar(periodo: str = "proximas", user=Depends(usuario_atual)):
    hoje = dt.date.today()
    c = N.con()
    try:
        try:
            c.execute("SELECT 1 FROM agenda_lembretes LIMIT 1")
        except Exception:
            c.execute("CREATE TABLE IF NOT EXISTS agenda_lembretes (evento_id TEXT NOT NULL, marco TEXT NOT NULL, "
                      "telefone TEXT, processo TEXT, enviado_em TEXT, PRIMARY KEY (evento_id, marco))")
        sql = "SELECT * FROM audiencia_registro WHERE status NOT IN ('substituida')"
        args = []
        if periodo == "proximas":
            sql += " AND data>=? AND status IN ('ativa','nao_vista')"
            args.append(hoje.isoformat())
        elif periodo == "passadas":
            sql += " AND (data<? OR status IN ('realizada','cancelada','redesignada'))"
            args.append(hoje.isoformat())
        sql += " ORDER BY data %s, hora" % ("DESC" if periodo == "passadas" else "ASC")
        itens = [_montar(c, dict(r), hoje) for r in c.execute(sql, args).fetchall()]
        fut = [a for a in itens if a["dias"] >= 0 and a["status"] in ("ativa", "nao_vista")]
        visto = c.execute("SELECT MAX(visto_em) FROM audiencia_registro").fetchone()[0]
        return {"audiencias": itens, "atualizado_em": visto,
                "resumo": {"proximas": len(fut),
                           "semana": sum(1 for a in fut if a["dias"] <= 7),
                           "fora_da_agenda": sum(1 for a in fut if not a.get("evento_id")),
                           "com_alerta": sum(1 for a in fut if a.get("divergencia") or
                                             (a.get("providencia") or {}).get("alertas"))},
                "subtipos": N.SUBTIPOS, "modalidades": N.MODALIDADES}
    finally:
        c.close()


@router.get("/busca-processo")
def busca_processo(q: str = "", user=Depends(usuario_atual)):
    """Processos do cadastro pelo numero ou pelo nome de uma parte (para o formulario)."""
    q = (q or "").strip()
    if len(q) < 3:
        return {"processos": []}
    c = N.con()
    try:
        like = "%" + q + "%"
        rows = c.execute(
            "SELECT DISTINCT p.numero FROM jur_processos p LEFT JOIN jur_processo_partes pp ON pp.processo_id=p.id "
            "WHERE TRIM(p.numero) != '' AND (p.numero LIKE ? OR pp.nome LIKE ?) ORDER BY p.encerrado, p.numero LIMIT 8",
            (like, like)).fetchall()
        out = []
        for (num,) in rows:
            cli, adv, _ = N.partes(c, num)
            out.append({"numero": num, "cliente": cli, "adversa": adv})
        return {"processos": out}
    finally:
        c.close()


class AudIn(BaseModel):
    processo: str
    data: str
    hora: Optional[str] = ""
    subtipo: Optional[str] = "audiencia"
    modalidade: Optional[str] = ""
    local: Optional[str] = ""
    obs: Optional[str] = ""
    agendar: bool = True


class AudPatch(BaseModel):
    hora: Optional[str] = None
    subtipo: Optional[str] = None
    modalidade: Optional[str] = None
    local: Optional[str] = None
    obs: Optional[str] = None
    nova_data: Optional[str] = None


class StatusIn(BaseModel):
    status: str
    resultado: Optional[str] = None


def _validar(subtipo, modalidade):
    if subtipo is not None and subtipo not in N.SUBTIPOS:
        raise HTTPException(400, "tipo invalido: use %s" % ", ".join(N.SUBTIPOS))
    if modalidade and modalidade not in N.MODALIDADES:
        raise HTTPException(400, "modalidade invalida: use %s" % ", ".join(N.MODALIDADES))


async def _agendar(processo, data, por):
    """Agenda fora da transacao da tela: se o Google falhar, a audiencia ja esta gravada."""
    def _rodar():
        c = N.con()
        try:
            a = _linha(c, processo, data)
            r = N.agendar(c, a, por=por)
            c.commit()
            return r
        finally:
            c.close()
    try:
        situacao, ev = await asyncio.to_thread(_rodar)
        return {"agenda": situacao, "evento_id": ev}
    except Exception as e:
        return {"agenda": "falhou", "agenda_erro": str(e)[:160]}


@router.post("")
async def criar(dados: AudIn, user=Depends(usuario_atual)):
    num = (dados.processo or "").strip()
    if not N.CNJ.fullmatch(num):
        raise HTTPException(400, "numero do processo no formato 9815290-10.0000.0.00.0420")
    data = _data_ok(dados.data)
    hora = _hora_ok(dados.hora)
    _validar(dados.subtipo or "audiencia", dados.modalidade)
    c = N.con()
    try:
        ja = c.execute("SELECT status FROM audiencia_registro WHERE processo=? AND data=?", (num, data)).fetchone()
        if ja and ja["status"] in ("ativa", "nao_vista"):
            raise HTTPException(409, "Essa audiencia ja esta no registro")
        cli, adv, lid = N.partes(c, num)
        campos = {"processo": num, "data": data, "hora": hora, "subtipo": dados.subtipo or "audiencia",
                  "modalidade": dados.modalidade or "", "local": (dados.local or "").strip(),
                  "cliente": cli, "adversa": adv, "lawsuit_id": lid, "fontes": "sistema",
                  "divergencia": "", "status": "ativa", "visto_em": N.agora_txt(), "origem": "manual",
                  "editado_local": 1, "obs": (dados.obs or "").strip(), "criado_por": _nome(user),
                  "atualizado_por": _nome(user), "atualizado_em": N.agora_txt()}
        c.execute("INSERT OR REPLACE INTO audiencia_registro (%s) VALUES (%s)"
                  % (", ".join(campos), ", ".join("?" for _ in campos)), list(campos.values()))
        c.commit()
    finally:
        c.close()
    out = {"ok": True, "processo": num, "data": data, "cliente": cli}
    if dados.agendar:
        out.update(await _agendar(num, data, _nome(user)))
    return out


@router.patch("/{processo}/{data}")
async def editar(processo: str, data: str, dados: AudPatch, user=Depends(usuario_atual)):
    _validar(dados.subtipo, dados.modalidade)
    campos = dados.model_dump(exclude_unset=True)
    nova = campos.pop("nova_data", None)
    if "hora" in campos:
        campos["hora"] = _hora_ok(campos["hora"])
    for k in ("local", "obs"):
        if k in campos:
            campos[k] = (campos[k] or "").strip()
    c = N.con()
    try:
        a = _linha(c, processo, data)
        por, agora = _nome(user), N.agora_txt()
        alvo_data = data
        if nova and _data_ok(nova, "nova data") != data:
            nova = _data_ok(nova, "nova data")
            ja = c.execute("SELECT status FROM audiencia_registro WHERE processo=? AND data=?",
                           (processo, nova)).fetchone()
            if ja and ja["status"] in ("ativa", "nao_vista"):
                raise HTTPException(409, "Ja existe audiencia desse processo nessa data")
            nova_linha = dict(a)
            nova_linha.update(campos)
            nova_linha.update({"data": nova, "status": "ativa", "editado_local": 1, "visto_em": agora,
                               "atualizado_por": por, "atualizado_em": agora, "divergencia": "",
                               "fontes": ",".join(sorted(set(str(a.get("fontes") or "").split(",")) | {"sistema"}
                                                         - {""}))})
            c.execute("INSERT OR REPLACE INTO audiencia_registro (%s) VALUES (%s)"
                      % (", ".join(nova_linha), ", ".join("?" for _ in nova_linha)), list(nova_linha.values()))
            c.execute("UPDATE audiencia_registro SET status='redesignada', editado_local=1, evento_id=NULL, "
                      "evento_link=NULL, evento_nosso=0, atualizado_por=?, atualizado_em=? "
                      "WHERE processo=? AND data=?", (por, agora, processo, data))
            alvo_data = nova
        elif campos:
            campos.update({"editado_local": 1, "atualizado_por": por, "atualizado_em": agora})
            c.execute("UPDATE audiencia_registro SET %s WHERE processo=? AND data=?"
                      % ", ".join(k + "=?" for k in campos), list(campos.values()) + [processo, data])
        c.commit()
        final = _linha(c, processo, alvo_data)
    finally:
        c.close()
    out = {"ok": True, "data": alvo_data}
    mexeu_agenda = alvo_data != data or any(k in campos for k in ("hora", "local", "subtipo", "modalidade"))
    if final.get("evento_id") and mexeu_agenda:
        try:
            await asyncio.to_thread(N.mover_evento, final["evento_id"], final, _nome(user),
                                    bool(final.get("evento_nosso")))
            out["agenda"] = "atualizada"
        except Exception as e:
            out["agenda"], out["agenda_erro"] = "falhou", str(e)[:160]
    return out


@router.post("/{processo}/{data}/agendar")
async def agendar(processo: str, data: str, user=Depends(usuario_atual)):
    c = N.con()
    try:
        _linha(c, processo, data)
    finally:
        c.close()
    r = await _agendar(processo, data, _nome(user))
    if r.get("agenda") == "falhou":
        raise HTTPException(502, "A agenda do Google recusou: %s" % r.get("agenda_erro"))
    return r


@router.post("/{processo}/{data}/status")
async def mudar_status(processo: str, data: str, dados: StatusIn, user=Depends(usuario_atual)):
    if dados.status not in ("ativa", "realizada", "cancelada"):
        raise HTTPException(400, "status: ativa, realizada ou cancelada")
    c = N.con()
    try:
        a = _linha(c, processo, data)
        c.execute("UPDATE audiencia_registro SET status=?, resultado=COALESCE(?, resultado), editado_local=1, "
                  "atualizado_por=?, atualizado_em=? WHERE processo=? AND data=?",
                  (dados.status, (dados.resultado or "").strip() or None, _nome(user), N.agora_txt(),
                   processo, data))
        if dados.status == "cancelada":
            c.execute("UPDATE audiencia_registro SET evento_id=NULL, evento_link=NULL, evento_nosso=0 "
                      "WHERE processo=? AND data=?", (processo, data))
        c.commit()
    finally:
        c.close()
    out = {"ok": True, "status": dados.status}
    ev = a.get("evento_id")
    if ev and dados.status in ("cancelada", "realizada"):
        from .. import agenda_google as AG
        try:
            if dados.status == "cancelada":
                # evento que fica na agenda continua mandando lembrete ao cliente
                await asyncio.to_thread(AG.cancelar, ev)
                out["agenda"] = "cancelada"
            else:
                await asyncio.to_thread(AG.marcar_cumprido, ev, True)
                out["agenda"] = "marcada [ok]"
        except Exception as e:
            out["agenda"], out["agenda_erro"] = "falhou", str(e)[:160]
    return out
