"""
ASSISTENTE DO DIA - Sistema Escritório Modelo (29/09/2026, pedido do Titular no audio para a Advogada).

Cada pessoa abre a tela de Tarefas e ve o "Meu dia": o que esta atrasado, o que
vence hoje e nos proximos dias, em que ponto do checklist esta cada tarefa, os
comentarios para ler e as audiencias da semana. Por cima, um resumo escrito
pelo Claude a partir desses dados (so eles: nada inventado), citando cada
tarefa como [#id], que a tela vira link para o cartao.

Fase 1 (esta): tarefas + checklist + audiencias, para toda a equipe.
Proximas: comercial (a equipe, Titular: leads de ontem, contratacao, cobranca,
reaquecimento, termometro) e marketing (Titular: metricas e plano de conteudo).

O texto fica guardado por pessoa e por dia (assistente_dia); "Atualizar" refaz.
Admin pode ver o dia de qualquer pessoa (?usuario_id=).
"""
import asyncio
import datetime as dt
import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import andamentos_nucleo as A
from ..deps import usuario_atual
from .tarefa_roteiros import garantir as garantir_roteiros, progresso
from ..tarefas_tipos import eh_comentario

router = APIRouter(prefix="/api/assistente", tags=["assistente"])

MODELO = "claude-sonnet-5"
SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

SISTEMA = """Você é o assistente de rotina da equipe do Escritório Modelo Advogados (escritório trabalhista; a maioria dos clientes é trabalhador reclamante). Recebe em JSON as tarefas e compromissos de UMA pessoa da equipe e escreve o resumo do dia dela, para ela ler ao abrir o sistema.

Regras:
- Você é o assistente de IA da pessoa, com nome próprio (campo "assistente" do JSON). Português do Brasil, tom de colega de trabalho, direto e gentil. Comece com "Bom dia, <nome>. Aqui é <assistente>." (ou "Boa tarde," depois do meio-dia).
- No máximo 9 linhas curtas. Sem emoji, sem títulos, sem travessão, sem negrito.
- Use SOMENTE o que está no JSON. Nunca invente prazo, cliente, etapa, número ou tarefa.
- Cite cada tarefa pelo código entre colchetes, exatamente assim: [#123]. A tela transforma em link. Um código por colchete, mesmo em sequência: "[#14], [#15] e [#17]". Nunca junte vários no mesmo colchete.
- Ordem de importância: atrasadas com prazo fatal; o que vence hoje; amanhã; o resto da semana.
- Se a tarefa tem checklist, diga em que ponto está usando a próxima etapa (ex.: "falta a entrevista com o cliente").
- Se houver comentários para ler, diga quantos são.
- Se houver audiência nos próximos dias, cite dia, hora e cliente.
- Se houver muita coisa atrasada, não liste tudo: diga quantas são e aponte as duas ou três mais urgentes.
- "para_conferir" NÃO são atrasadas: são tarefas que parecem já feitas no ADVBOX. Diga quantas são e peça para a pessoa confirmar (na caixa "Conferir" ou pedindo a você).
- Tarefa com "compromisso_passou" tem o prazo fatal ainda à frente: é para hoje, não está atrasada.
- Termine com uma sugestão prática de por onde começar."""


def _con():
    c = A.con()
    garantir_roteiros(c)
    c.execute("""CREATE TABLE IF NOT EXISTS assistente_dia (
                   usuario_id INTEGER NOT NULL, dia TEXT NOT NULL, texto TEXT, modelo TEXT,
                   gerado_em REAL, PRIMARY KEY (usuario_id, dia))""")
    return c


def _primeiro(nome):
    p = str(nome or "").split()
    return p[0].capitalize() if p else ""


def _dia(v):
    try:
        return dt.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def montar(c, uid):
    """Os dados do dia da pessoa (sempre frescos)."""
    garantir_roteiros(c)          # quem chama pode vir de outra tela (agentes) com conexao propria
    hoje = dt.date.today()
    u = c.execute("SELECT id, nome, email, papel FROM usuarios WHERE id=?", (uid,)).fetchone()
    if not u:
        raise HTTPException(404, "Usuario nao encontrado")
    linhas = c.execute(
        "SELECT t.* FROM tarefas t JOIN tarefa_responsaveis r ON r.tarefa_id = t.id "
        "WHERE r.usuario_id=? AND r.concluido_em IS NULL AND t.status='aberta'", (uid,)).fetchall()
    grupos = {"atrasadas": [], "hoje": [], "amanha": [], "semana": [], "depois": [], "sem_prazo": [], "conferir": []}
    comentarios = []
    from .. import tarefas_conferir as TC
    conferir = TC.suspeitas(c)
    for t in linhas:
        item = {"id": t["id"], "tipo": t["tipo"], "titulo": (t["titulo"] or "")[:120],
                "cliente": (t["cliente"] or "")[:70], "processo": t["processo_numero"],
                "compromisso": t["prazo_interno"] or t["data"], "prazo_fatal": t["prazo_fatal"],
                "prioridade": t["prioridade"], "origem": t["origem"]}
        chk = progresso(c, t["id"], t["tipo"])
        if chk:
            item["checklist"] = {"feitos": chk["feitos"], "total": chk["total"], "proxima": chk["proxima"]}
        if eh_comentario(t["tipo"]):
            comentarios.append(item)
            continue
        # 29/09/2026 (Advogada: "tem tarefa marcada como atrasada que nao esta"):
        # a que parece ja feita no ADVBOX vai para "Conferir", nao para "Atrasadas"
        if t["id"] in conferir:
            item["conferir"] = conferir[t["id"]]
            grupos["conferir"].append(item)
            continue
        d = _dia(item["compromisso"])
        pf = _dia(item["prazo_fatal"])
        if d and d < hoje and pf and pf >= hoje:
            # o compromisso interno passou mas o prazo fatal nao: e para hoje, nao atrasada
            item["compromisso_passou"] = item["compromisso"]
            d = hoje
        if not d:
            grupos["sem_prazo"].append(item)
        elif d < hoje:
            grupos["atrasadas"].append(item)
        elif d == hoje:
            grupos["hoje"].append(item)
        elif d == hoje + dt.timedelta(days=1):
            grupos["amanha"].append(item)
        elif d <= hoje + dt.timedelta(days=7):
            grupos["semana"].append(item)
        else:
            grupos["depois"].append(item)
    for k in grupos:
        grupos[k].sort(key=lambda x: (not x.get("prazo_fatal"), x.get("compromisso") or "9999"))
    auds = []
    try:
        for a in c.execute("SELECT processo, data, hora, subtipo, modalidade, cliente, adversa FROM audiencia_registro "
                           "WHERE status='ativa' AND data BETWEEN ? AND ? ORDER BY data, hora",
                           (hoje.isoformat(), (hoje + dt.timedelta(days=7)).isoformat())):
            auds.append(dict(a))
    except Exception:
        pass
    from .. import agentes_nucleo as AGN
    AGN.garantir(c)
    ag = AGN.agente_de(c, uid)
    return {"usuario": {"id": u["id"], "nome": u["nome"], "primeiro_nome": _primeiro(u["nome"])},
            "agente": {"nome": ag.get("nome"), "cor": ag.get("cor"), "simbolo": ag.get("simbolo"),
                       "genero": ag.get("genero"), "padrao": bool(ag.get("padrao"))},
            "assistente": AGN.apresentacao(ag),
            "hoje": hoje.isoformat(), "dia_semana": SEMANA[hoje.weekday()],
            "tarefas": grupos, "comentarios": comentarios, "audiencias": auds,
            "contagem": dict({k: len(v) for k, v in grupos.items()}, comentarios=len(comentarios))}


def _para_ia(d):
    """Recorte do que vai para o Claude (sem excesso de texto)."""
    g = d["tarefas"]

    def corta(lst, n):
        return [{k: v for k, v in x.items() if v not in (None, "", [])} for x in lst[:n]]
    agora = dt.datetime.now()
    return {"nome": d["usuario"]["primeiro_nome"], "assistente": d.get("assistente") or "o seu assistente",
            "agora": agora.strftime("%d/%m/%Y %H:%M"),
            "dia_semana": d["dia_semana"], "periodo": "manhã" if agora.hour < 12 else "tarde",
            "atrasadas": corta(g["atrasadas"], 12), "total_atrasadas": len(g["atrasadas"]),
            "vencem_hoje": corta(g["hoje"], 15), "vencem_amanha": corta(g["amanha"], 10),
            "resto_da_semana": corta(g["semana"], 10), "sem_prazo": len(g["sem_prazo"]),
            "para_conferir": [{"id": x["id"], "tipo": x["tipo"], "motivo": x["conferir"]["motivo"]} for x in g["conferir"][:8]],
            "depois_da_semana": len(g["depois"]), "comentarios_para_ler": len(d["comentarios"]),
            "audiencias_proximos_7_dias": d["audiencias"][:8]}


def _escrever(d):
    import anthropic
    from ..config import settings
    from .. import ia_reserva as IR
    pedido = json.dumps(_para_ia(d), ensure_ascii=False)
    try:
        if IR.anthropic_fora() and IR.disponivel():
            raise RuntimeError("anthropic fora (reserva ativa)")
        cli = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        # sem o modo de raciocinio: com ele o modelo gastava ~700 tokens pensando e o
        # texto saia cortado ou vazio; para um resumo do dia nao faz diferenca (29/09)
        r = cli.messages.create(model=MODELO, max_tokens=1500, system=SISTEMA, thinking={"type": "disabled"},
                                messages=[{"role": "user", "content": pedido}])
        return "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        # carteira da Anthropic sem credito (29/09): o resumo sai pelo Gemini
        if not IR.disponivel() or not (IR.anthropic_fora() or IR.falha_de_conta(e)):
            raise
        if not IR.anthropic_fora():
            IR.marcar_fora(e)
        return IR.texto(SISTEMA, [{"role": "user", "content": pedido}], 2500)


@router.get("/meu-dia")
async def meu_dia(usuario_id: Optional[int] = None, refazer: bool = False, sem_texto: bool = False,
                  user=Depends(usuario_atual)):
    uid = user.id
    if usuario_id and usuario_id != user.id:
        if (getattr(user, "papel", "") or "") != "admin":
            raise HTTPException(403, "So administrador ve o dia de outra pessoa")
        uid = usuario_id

    def _dados():
        c = _con()
        try:
            d = montar(c, uid)
            r = c.execute("SELECT texto, gerado_em FROM assistente_dia WHERE usuario_id=? AND dia=?",
                          (uid, d["hoje"])).fetchone()
            return d, (dict(r) if r else None)
        finally:
            c.close()
    d, guardado = await asyncio.to_thread(_dados)
    out = dict(d)
    if sem_texto:
        return out
    if guardado and guardado["texto"] and not refazer:
        out["texto"], out["texto_gerado_em"] = guardado["texto"], guardado["gerado_em"]
        return out
    try:
        texto = await asyncio.to_thread(_escrever, d)
    except Exception as e:
        out["texto"], out["texto_erro"] = None, "O assistente não respondeu agora (%s). A lista abaixo está atualizada." % str(e)[:80]
        return out

    def _guardar():
        c = _con()
        try:
            c.execute("INSERT OR REPLACE INTO assistente_dia (usuario_id, dia, texto, modelo, gerado_em) VALUES (?,?,?,?,?)",
                      (uid, d["hoje"], texto, MODELO, time.time()))
            c.commit()
        finally:
            c.close()
    await asyncio.to_thread(_guardar)
    out["texto"], out["texto_gerado_em"] = texto, time.time()
    return out


def resolver_conferir(tarefa_id, feita, user, por=None):
    """Resposta a "Conferir": feita -> conclui a parte de quem respondeu (admin fecha para todos)
    pelo mesmo caminho da tela de Tarefas; ainda falta -> volta a contar como atrasada."""
    from . import tarefas as T
    from .. import tarefas_conferir as TC
    c = _con()
    try:
        t = c.execute("SELECT id, status FROM tarefas WHERE id=?", (int(tarefa_id),)).fetchone()
        if not t:
            raise HTTPException(404, "Tarefa nao encontrada")
        e_resp = c.execute("SELECT 1 FROM tarefa_responsaveis WHERE tarefa_id=? AND usuario_id=?",
                           (t["id"], user.id)).fetchone()
        admin = (getattr(user, "papel", "") or "") == "admin"
        if not e_resp and not admin:
            raise HTTPException(403, "So quem e responsavel pela tarefa responde")
    finally:
        c.close()
    if feita and t["status"] == "aberta":
        pendente_meu = None
        c = _con()
        try:
            pendente_meu = c.execute("SELECT 1 FROM tarefa_responsaveis WHERE tarefa_id=? AND usuario_id=? AND "
                                     "concluido_em IS NULL", (t["id"], user.id)).fetchone()
        finally:
            c.close()
        if pendente_meu:
            T.concluir(t["id"], todos=False, user=user)
        elif admin:
            T.concluir(t["id"], todos=True, user=user)
    c = _con()
    try:
        TC.resolver(c, t["id"], feita, por or getattr(user, "nome", "") or "")
        c.commit()
    finally:
        c.close()
    return {"ok": True, "tarefa_id": t["id"], "feita": bool(feita)}


class ConferirIn(BaseModel):
    feita: bool


@router.post("/conferir/{tarefa_id}")
async def conferir(tarefa_id: int, dados: ConferirIn, user=Depends(usuario_atual)):
    return await asyncio.to_thread(resolver_conferir, tarefa_id, dados.feita, user)
