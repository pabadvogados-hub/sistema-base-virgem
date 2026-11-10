# -*- coding: utf-8 -*-
"""POS-LIGACAO: o que acontece DEPOIS que o telefone desliga.

Hoje a corrente esta inteira menos o ultimo elo:
  termometro_leads (13h30) e alarme_qualificado (15/15min) dizem PARA QUEM ligar
  fila_ligacao (8h30) monta a lista e o briefing no Drive
  a ligacao sai pelo WhatsApp de dentro do sistema e fica gravada
  transcrever_ligacoes (1/min) transcreve e cola a transcricao na conversa
  ... e para aqui.

Depois disso e tudo na mao: mover o card (que e o gesto que DRENA a fila), anotar
o que ficou combinado, marcar o retorno e preencher a ficha. O numero mostra que
nao acontece: 1.201 leads classificados, 78 quentes, e 10 fichas preenchidas.
O caso do Juvenal (21/09) precisou de um cron escrito a mao so para lembrar de
ligar na segunda seguinte.

Este script le a transcricao da ligacao e escreve de volta:
  1. RESUMO do que foi tratado, na nota da conversa (uma linha por assunto);
  2. TAREFA de retorno com data, na tabela tarefas (aparece no modulo Tarefas);
  3. ETAPA do card: quem foi contatado sai de "Lead Quente" e vai para
     "Contatado", "Pos-Reuniao de Fechamento" ou "Lead Desqualificado" -
     e e isso que faz a pessoa sumir da fila de ligacao do dia seguinte;
  4. TEMPERATURA nova no termometro (a ligacao vale mais que o chat);
  5. campos da FICHA DE ENTREVISTA que deram para extrair da conversa.

NAO manda nada para o cliente. Mensagem para lead e sempre decisao do Titular.

A leitura e feita pelo Claude Code da assinatura, NUNCA pela API Anthropic
(mesmo padrao do alarme_qualificado.py).

Uso:
  pos_ligacao.py                 simula: mostra o que faria, nao grava nada
  pos_ligacao.py --aplicar       grava resumo, tarefa, etapa, temperatura e ficha
  pos_ligacao.py --conversa 1456 so essa conversa
  pos_ligacao.py --refazer       reprocessa ligacao que ja foi tratada
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

DB = "/opt/escritorio/SISTEMA/backend/sistema_esc.db"
CLAUDE_BIN = os.getenv("ESC_CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))
MARCA = "=== POS-LIGACAO"          # ja tratei esta ligacao
CABECA = "=== LIGACAO PELO WHATSAPP"

# wa_conversas.etapa guarda o NOME da coluna, nao o id. A primeira versao deste
# script gravou o id (87) e o card sumiu do quadro ate alguem corrigir a mao.
ETAPAS = {
    "contatado": "Contatado",
    "fechou": "Pos-Reuniao de Fechamento",
    "desqualificado": "Lead Desqualificado",
    "qualificado": "Lead Qualificado",
}
SAI_DA_FILA = ("Lead Quente", "Lead Qualificado")   # de onde a fila_ligacao le

PROMPT = """Voce e o assistente do Escritório Modelo Advogados, escritorio trabalhista que defende trabalhadores.
Acabou uma ligacao com um lead. Leia a transcricao e responda em JSON puro, sem texto fora do JSON:

{"resumo": ["ate 5 linhas: o CASO da pessoa e o que foi decidido. Ligacao costuma comecar com conversa fiada (time, cidade, familia, outros processos do escritorio) - isso NAO entra no resumo"],
 "combinado": "o que as duas partes combinaram fazer, em ate 20 palavras; vazio se nada foi combinado",
 "proximo_passo": "o que o ESCRITORIO tem que fazer, em ate 12 palavras; vazio se nao tem",
 "quando": "AAAA-MM-DD do proximo passo, ou vazio se a ligacao nao marcou dia",
 "quando_disse": "como a pessoa falou a data (ex: 'segunda que vem'), ou vazio",
 "desfecho": "contatado" ou "fechou" ou "desqualificado" ou "nao_atendeu",
 "temperatura": "quentissimo" ou "quente" ou "morno" ou "frio" ou "sem_caso"}

Regras:
- desfecho "fechou" so se a pessoa aceitou contratar de forma clara.
- desfecho "desqualificado" se ficou claro que nao ha caso (autonomo dono do caminhao,
  ja tem advogado, ja prescreveu, nao quer).
- desfecho "nao_atendeu" se a transcricao nao tem conversa de verdade.
- "quando": HOJE e {hoje} ({diasemana}). Converta o que a pessoa falou para data real.
  Se ficou de "voltar a falar" sem dia, use 3 dias uteis a frente.
- "empresa" e a RECLAMADA: onde o lead trabalha ou trabalhou. O atendente costuma citar
  outras empresas como exemplo de processos que o escritorio ja tem - essas NAO sao a
  reclamada. Se a pessoa falou o nome do patrao ou da transportadora dela, esse e o valor.
- campo da ficha que a ligacao nao disse fica string vazia. NUNCA invente.

TRANSCRICAO:
{transcricao}"""

DIAS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]



# ============================================================
# ENTREVISTA COMPLETA A PARTIR DA LIGACAO
# ------------------------------------------------------------
# Os campos NAO ficam escritos aqui: saem de app/routers/ficha_entrevista.py em
# tempo de execucao. Campo que a Advogada criar na aba entra sozinho no extrator,
# sem mexer neste arquivo.
# ============================================================
def campos_da_entrevista():
    """[(bloco, id, rotulo, opcoes)] lidos do esquema do backend. [] se falhar."""
    try:
        if "/opt/escritorio/SISTEMA/backend" not in sys.path:
            sys.path.insert(0, "/opt/escritorio/SISTEMA/backend")
        from app.routers.ficha_entrevista import BLOCOS
    except Exception as e:
        log("   ! nao consegui ler o esquema da entrevista: %s" % str(e)[:110])
        return []
    fora = []
    for b in BLOCOS:
        for c in b.get("campos", []):
            fora.append((b.get("titulo") or "", c["id"], c.get("label") or c["id"],
                         c.get("opcoes") or []))
    return fora


def extrair_ficha(transcricao, campos):
    """Le a transcricao e devolve {campo: valor} so do que foi realmente dito."""
    if not campos:
        return {}
    linhas, bloco_atual = [], None
    for bloco, cid, rotulo, opcoes in campos:
        if bloco != bloco_atual:
            linhas.append("")
            linhas.append("# " + bloco)
            bloco_atual = bloco
        op = ("  [responda exatamente um destes: " + " | ".join(x for x in opcoes if x) + "]") if opcoes else ""
        linhas.append("%s: %s%s" % (cid, rotulo, op))
    lista = "\n".join(linhas)

    prompt = (
        "Voce e o assistente do Escritório Modelo Advogados, escritorio trabalhista que defende trabalhadores.\n"
        "Abaixo esta a transcricao de uma ligacao com um cliente e, depois, a FICHA DE\n"
        "ENTREVISTA do escritorio. Preencha a ficha SO com o que a ligacao disse.\n\n"
        "REGRAS QUE NAO SE NEGOCIAM:\n"
        "1. NUNCA invente. Campo que a ligacao nao disse fica FORA do JSON. E melhor\n"
        "   devolver 10 campos certos do que 96 com chute. Ficha errada vira peticao errada.\n"
        "2. Nao deduza. Se ele falou 'ganhava uns tres mil', o salario_real e 'uns R$ 3.000\n"
        "   (ele falou por alto)'. Se nao falou, o campo nao existe na resposta.\n"
        "3. Onde o rotulo pedir LITERAL, copie as palavras dele, nao resuma.\n"
        "4. Campo com lista de opcoes so aceita uma daquelas opcoes, escrita igual.\n"
        "5. Valor em dinheiro leva R$.\n"
        "6. Responda JSON puro, uma chave por campo preenchido, sem texto fora do JSON.\n\n"
        "TRANSCRICAO DA(S) LIGACAO(OES):\n" + transcricao[:14000] + "\n\n"
        "FICHA A PREENCHER (campo: o que ele significa):\n" + lista + "\n")

    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)          # assinatura, nunca API
    try:
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", "sonnet", "--tools", "",
                            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
                           input=prompt.encode("utf-8"), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env,
                           cwd=tempfile.gettempdir(), timeout=300)
    except subprocess.TimeoutExpired:
        log("   ! a entrevista demorou demais")
        return {}
    if r.returncode != 0:
        log("   ! entrevista: " + r.stderr.decode("utf-8", "replace")[:140])
        return {}
    m = re.search(r"\{.*\}", r.stdout.decode("utf-8", "replace"), re.S)
    if not m:
        return {}
    try:
        bruto = json.loads(m.group(0))
    except ValueError:
        return {}

    validos = {c[1] for c in campos}
    opcoes_de = {c[1]: [x for x in c[3] if x] for c in campos if c[3]}
    limpo = {}
    for k, v in bruto.items():
        if k not in validos:
            continue                      # campo que nao existe na ficha: fora
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v if x)
        v = str(v or "").strip()
        if not v or v.lower() in ("nao informado", "nao mencionado", "n/a", "-", "null", "none"):
            continue
        if k in opcoes_de and v not in opcoes_de[k]:
            continue                      # select so aceita opcao valida
        limpo[k] = v[:2000]
    return limpo


def gravar_ficha(db, conversa_id, valores, aplicar):
    """Grava so em campo vazio ou que o proprio pos_ligacao escreveu antes.
    O que uma PESSOA preencheu fica intocado: a maquina nunca passa por cima."""
    # A aba grava os 96 campos de uma vez quando alguem salva o formulario, a
    # maioria VAZIOS e com o e-mail de quem salvou. Por isso o que protege o
    # trabalho humano e o VALOR ter conteudo, nunca so a linha existir.
    ja = {}
    for r in db.execute("select campo, valor, atualizado_por from ficha_entrevista "
                        "where conversa_id=?", (conversa_id,)):
        ja[r[0]] = ((r[1] or "").strip(), r[2] or "")
    novos, protegidos = 0, []
    agora = datetime.now().isoformat(sep=" ")
    for campo, valor in valores.items():
        antes, dono = ja.get(campo, ("", ""))
        if antes and dono != "pos_ligacao":
            protegidos.append(campo)      # gente escreveu de verdade: nao encosto
            continue
        if not aplicar:
            novos += 1
            continue
        db.execute(
            "insert into ficha_entrevista (conversa_id, campo, valor, atualizado_em, atualizado_por) "
            "values (?,?,?,?, 'pos_ligacao') "
            "on conflict(conversa_id, campo) do update set valor=excluded.valor, "
            "atualizado_em=excluded.atualizado_em, atualizado_por='pos_ligacao'",
            (conversa_id, campo, valor, agora))
        novos += 1
    return novos, protegidos


# ---------------------------------------------------------------------------
# FUNIL DE FECHADO: a entrevista so e preenchida
# para quem ja fechou. Preencher ficha de lead que nao fechou e trabalho jogado
# fora e enche a aba de dado que ninguem vai usar.
#   pipeline -> None = todas as colunas (menos Perdido) | set = so estas colunas
FUNIL_FECHADO = {
    20: None,                                   # Contratação
    18: None,                                   # Produção
    11: None,                                   # Clientes
    10: {"Pós-Reunião de Fechamento", "Contrato / Assinatura",
         "Elaboração de Contrato", "Ganho"},   # Reclamantes: so o fim do funil
}


def no_funil_fechado(c):
    pid = c["pipeline_id"]
    if pid not in FUNIL_FECHADO:
        return False
    etapa = (c["etapa"] or "").strip()
    if etapa.lower().startswith("perdido"):
        return False
    cols = FUNIL_FECHADO[pid]
    return True if cols is None else (etapa in cols)


def todas_ligacoes(notas, limite=14000):
    """LISTA com cada ligacao da conversa, da mais antiga para a mais nova, sem
    os blocos de resumo do pos-ligacao.

    Uma por vez, e nao tudo junto: medido em producao, 14 mil caracteres levam
    ~95s e trazem 12 campos; 60 mil estouram os 300s e voltam VAZIO. Transcricao
    grande derrubava a extracao inteira e deixava o cliente com ficha em branco.
    Cada ligacao que passar do limite fica com o FIM dela, que e onde costuma
    estar o fechamento."""
    if not notas or CABECA not in notas:
        return []
    fora = []
    for trecho in notas.split(CABECA)[1:]:
        bloco = CABECA + trecho
        corte = bloco.find("\n\n=== ", len(CABECA))
        if corte > 0:
            bloco = bloco[:corte]
        bloco = bloco.strip()
        if len(bloco) > limite:
            bloco = bloco[-limite:]
        if len(bloco) >= 400:
            fora.append(bloco)
    return fora


def ficha_de_todas(ligacoes, campos):
    """Extrai de cada ligacao e junta. A mais nova sobrescreve a antiga so no
    campo que ela tambem respondeu; o que so a antiga disse continua valendo."""
    junto = {}
    for i, bloco in enumerate(ligacoes, 1):
        parcial = extrair_ficha(bloco, campos)
        if len(ligacoes) > 1:
            log("   ligacao %d/%d  : %d campo(s)" % (i, len(ligacoes), len(parcial)))
        junto.update(parcial)
    return junto


def log(msg):
    print(msg, flush=True)


def ultima_ligacao(notas):
    """Devolve (bloco da ultima ligacao, ja_tratada)."""
    if not notas or CABECA not in notas:
        return None, False
    bloco = notas[notas.rindex(CABECA):]
    return bloco, MARCA in bloco


TENTATIVAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ligacoes_tentativas.json")
MAX_TENTATIVAS_DIA = 2


class ClaudeIndisponivel(Exception):
    """Limite do plano, CLI fora do ar ou erro de rede: a rodada para aqui."""


def _tentativas():
    try:
        return json.load(open(TENTATIVAS, encoding="utf-8"))
    except Exception:
        return {}


def pode_tentar(conversa_id):
    """No maximo MAX_TENTATIVAS_DIA chamadas ao Claude por conversa por dia."""
    hoje = datetime.now().strftime("%Y-%m-%d")
    t = _tentativas()
    reg = t.get(str(conversa_id)) or {}
    if reg.get("dia") != hoje:
        reg = {"dia": hoje, "n": 0}
    if reg["n"] >= MAX_TENTATIVAS_DIA:
        return False
    reg["n"] += 1
    t[str(conversa_id)] = reg
    try:
        json.dump(t, open(TENTATIVAS, "w", encoding="utf-8"))
    except OSError:
        pass
    return True


def perguntar(transcricao):
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)          # assinatura, nunca API
    hoje = datetime.now()
    prompt = (PROMPT
              .replace("{hoje}", hoje.strftime("%Y-%m-%d"))
              .replace("{diasemana}", DIAS[hoje.weekday()])
              .replace("{transcricao}", transcricao[:12000]))
    try:
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", "sonnet", "--tools", "",
                            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
                           input=prompt.encode("utf-8"), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env,
                           cwd=tempfile.gettempdir(), timeout=180)
    except subprocess.TimeoutExpired:
        log("   ! Claude demorou demais")
        return None
    if r.returncode != 0:
        erro = r.stderr.decode("utf-8", "replace")[:300]
        log("   ! Claude devolveu erro: " + erro[:200])
        raise ClaudeIndisponivel(erro)
    m = re.search(r"\{.*\}", r.stdout.decode("utf-8", "replace"), re.S)
    if not m:
        log("   ! resposta sem JSON")
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        log("   ! JSON invalido")
        return None


def bloco_nota(d, quando_txt):
    linhas = [MARCA + " " + datetime.now().strftime("%d/%m/%Y %H:%M") + " ==="]
    for ln in d.get("resumo") or []:
        linhas.append("- " + str(ln))
    if d.get("combinado"):
        linhas.append("COMBINADO: " + d["combinado"])
    if d.get("proximo_passo"):
        linhas.append("PROXIMO PASSO: " + d["proximo_passo"] + (" (" + quando_txt + ")" if quando_txt else ""))
    return "\n".join(linhas)


def dia_util(base, dias):
    d = base
    while dias > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            dias -= 1
    return d


def gravar_retorno(db, c, d, quando, agora):
    """Tarefa de retorno da ligacao, idempotente por conversa."""
    titulo = d["proximo_passo"][:120]
    descricao = (d.get("combinado") or "") + ("\n\nCombinado na ligacao de " + datetime.now().strftime("%d/%m/%Y"))
    prioridade = "urgente" if d.get("temperatura") in ("quentissimo", "quente") else "normal"
    ref = "conversa:%s" % c["id"]
    aberta = db.execute("select id from tarefas where origem='pos-ligacao' and origem_ref=? and tipo='ACOMPANHAMENTO' "
                        "and status='aberta'", (ref,)).fetchone()
    if aberta:
        db.execute("update tarefas set titulo=?, descricao=coalesce(descricao,'') || char(10) || char(10) || ?, data=?, "
                   "prioridade=?, atualizado_em=? where id=?",
                   (titulo, "NOVA LIGACAO: " + descricao, quando, prioridade, agora, aberta["id"]))
        log("   tarefa      : #%s atualizada (ja havia retorno aberto desta conversa)" % aberta["id"])
        return aberta["id"]
    if db.execute("select 1 from tarefas where origem='pos-ligacao' and origem_ref=? and tipo='ACOMPANHAMENTO'", (ref,)).fetchone():
        ref = "conversa:%s:%s" % (c["id"], datetime.now().strftime("%Y%m%d%H%M"))
    cur = db.execute(
        "insert into tarefas (tipo, titulo, descricao, cliente, data, prioridade, status, "
        "origem, origem_ref, criado_por, criado_em, contato_nome, contato_telefone, conversa_id) "
        "values ('ACOMPANHAMENTO', ?, ?, ?, ?, ?, 'aberta', 'pos-ligacao', ?, 'pos_ligacao', ?, ?, ?, ?)",
        (titulo, descricao, c["contato_nome"], quando, prioridade, ref, agora,
         c["contato_nome"], c["contato_telefone"], c["id"]))
    log("   tarefa      : #%s criada" % cur.lastrowid)
    return cur.lastrowid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--mover-card", dest="mover_card", action="store_true",
                    help="tambem move o card de coluna. Desligado por padrao: em 23/09 a "
                         "Advogada reclamou de lead sendo reclassificado por automacao, e "
                         "ate saber quem faz isso nenhum robo novo vai mexer em coluna.")
    ap.add_argument("--refazer", action="store_true")
    ap.add_argument("--entrevista-lote", dest="entrevista_lote", type=int, default=1,
                    help="quantas conversas do funil de fechado tem a ficha preenchida "
                         "pela conversa inteira nesta rodada (0 desliga). Cada uma leva "
                         "~95s por trecho de 14 mil caracteres, entao o padrao e 1.")
    ap.add_argument("--conversa", type=int)
    a = ap.parse_args()

    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    campos_ficha = campos_da_entrevista()
    log("esquema da entrevista: %d campos" % len(campos_ficha))
    sql = ("select id, contato_nome, contato_telefone, notas, etapa, pipeline_id "
           "from wa_conversas where notas like ? ")
    par = ["%" + CABECA + "%"]
    if a.conversa:
        sql += "and id = ? "
        par.append(a.conversa)
    sql += "order by id"

    tratadas = 0
    for c in db.execute(sql, par).fetchall():
        bloco, ja = ultima_ligacao(c["notas"])
        if not bloco:
            continue
        if ja and not a.refazer:
            continue
        curta = len(bloco) < 300
        if curta and not a.conversa:
            continue              # caiu / nao atenderam: nem entra no log da rodada
        log("")
        log("=" * 72)
        log("conversa %s - %s (%s)" % (c["id"], c["contato_nome"], c["contato_telefone"]))
        if curta:
            log("   ligacao curta demais (%d caracteres): nao atendeu / caiu" % len(bloco))
            continue
        if not pode_tentar(c["id"]):
            log("   ja tentei %d vezes hoje nesta conversa: pulo (alguem precisa olhar)" % MAX_TENTATIVAS_DIA)
            continue
        try:
            d = perguntar(bloco)
        except ClaudeIndisponivel as e:
            log("   ! Claude indisponivel (%s): PARO a rodada aqui" % str(e)[:120])
            break
        if not d:
            continue
        tratadas += 1

        quando = (d.get("quando") or "").strip()
        if not quando and d.get("desfecho") == "contatado":
            quando = dia_util(datetime.now(), 3).strftime("%Y-%m-%d")
        quando_txt = d.get("quando_disse") or ""

        log("   desfecho    : %s   temperatura: %s" % (d.get("desfecho"), d.get("temperatura")))
        for ln in d.get("resumo") or []:
            log("   resumo      : " + str(ln))
        if d.get("combinado"):
            log("   combinado   : " + d["combinado"])
        if d.get("proximo_passo"):
            log("   proximo     : %s  ->  %s %s" % (d["proximo_passo"], quando, ("(" + quando_txt + ")") if quando_txt else ""))
        # ENTREVISTA: le a ligacao de novo, agora contra os 96 campos da aba
        ficha = {}
        if d.get("desfecho") == "nao_atendeu":
            pass
        elif not no_funil_fechado(c):
            log("   entrevista  : fora do funil de fechado (%s), nao preenche" % (c["etapa"] or "?"))
        else:
            ficha = ficha_de_todas(todas_ligacoes(c["notas"]), campos_ficha)
            n_novos, protegidos = gravar_ficha(db, c["id"], ficha, aplicar=False)
            log("   entrevista  : %d campo(s) reconhecido(s), %d para gravar" % (len(ficha), n_novos))
            if protegidos:
                log("                 %d preenchido(s) por gente, nao toco: %s"
                    % (len(protegidos), ", ".join(protegidos[:6])))
            for campo in list(ficha)[:8]:
                log("                 %-24s %s" % (campo, str(ficha[campo])[:62]))

        # para onde o card vai (so com --mover-card: ver a nota no cabecalho)
        destino = ETAPAS.get(d.get("desfecho") or "", None)
        if d.get("desfecho") == "nao_atendeu" or not a.mover_card:
            destino = None
        if destino and c["etapa"] == destino:
            destino = None
        if destino:
            log("   card        : %s  ->  %s%s" % (
                c["etapa"], destino,
                "   (sai da fila de ligacao)" if c["etapa"] in SAI_DA_FILA else ""))

        if not a.aplicar:
            log("   [simulacao] nada gravado")
            continue

        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            gravar_tudo(db, c, d, quando, quando_txt, ficha, destino, agora, a)
        except Exception as e:
            db.rollback()
            log("   ! falhou ao gravar (%s): desfeito, sigo para a proxima" % str(e)[:160])
            continue
        log("   GRAVADO")

    log("")
    log("ligacoes tratadas: %d%s" % (tratadas, "" if a.aplicar else "  (simulacao)"))
    return segunda_passada(db, a, campos_ficha)


def gravar_tudo(db, c, d, quando, quando_txt, ficha, destino, agora, a):
    """Tudo o que uma ligacao grava, numa transacao so; a marca vai na nota junto."""
    if True:
        # 1. resumo na nota da conversa (traz a marca "=== POS-LIGACAO": e ela que evita o reprocesso)
        db.execute("update wa_conversas set notas = coalesce(notas,'') || char(10)||char(10) || ? where id=?",
                   (bloco_nota(d, quando_txt), c["id"]))
        # 2. tarefa de retorno (sem estourar o UNIQUE de origem/origem_ref/tipo: a segunda
        #    ligacao da mesma conversa ATUALIZA a tarefa aberta, ou abre outra com ref datada)
        if d.get("proximo_passo") and quando:
            gravar_retorno(db, c, d, quando, agora)
        # 3. etapa do card
        if destino:
            db.execute("insert into wa_board_hist (conversa_id, de_pipeline, de_etapa, para_pipeline, para_etapa, quem, origem, criado_em) "
                       "values (?,?,?,?,?,'pos_ligacao','script:pos_ligacao',?)",
                       (c["id"], c["pipeline_id"], c["etapa"], c["pipeline_id"], destino, agora))
            db.execute("update wa_conversas set etapa=?, atualizado_em=? where id=?", (destino, agora, c["id"]))
        # 4. temperatura
        if d.get("temperatura"):
            db.execute("insert or replace into lead_termometro (conversa_id, temperatura, situacao, resumo, classificado_em, modelo) "
                       "values (?,?,?,?,?,'pos-ligacao')",
                       (c["id"], d["temperatura"], d.get("desfecho") or "", (d.get("combinado") or "")[:400], agora))
        # 5. ENTREVISTA completa
        if ficha:
            n_grav, _ = gravar_ficha(db, c["id"], ficha, aplicar=True)
            log("   entrevista  : %d campo(s) gravados na aba" % n_grav)
        db.commit()


def segunda_passada(db, a, campos_ficha):
    # ---- segunda passada: ficha a partir da CONVERSA inteira ----------------
    # O laco de cima trata cada ligacao UMA vez e carimba "=== POS-LIGACAO". Só
    # que quem move o card e gente, e move depois de desligar: 2 minutos apos a
    # ligacao o card ainda esta em "Lead Quente", o portao do funil fecha, a
    # ficha e pulada e a marca fica gasta. Quando o card vira "Fechou!", ninguem
    # volta. Isto aqui volta: olha o funil e a ficha, nao a marca, e rele as
    # TRANSCRICOES DA LIGACAO (nunca chat solto).
    if a.entrevista_lote and not a.conversa:
        try:
            import ficha_da_conversa as fdc
            fila = fdc.pendentes(db, no_funil_fechado, limite=a.entrevista_lote)
            if fila:
                log("")
                log("=" * 72)
                log("ENTREVISTA PELA CONVERSA INTEIRA (%d na vez)" % len(fila))
            for item in fila:
                log("")
                log("conversa %s - %s [%s]" % (item["id"], item["nome"], item["etapa"]))
                lidos, novos, protegidos = fdc.preencher(
                    db, item["id"], campos_ficha, extrair_ficha, gravar_ficha,
                    aplicar=a.aplicar, log=log)
                log("   %d campo(s) reconhecido(s), %d gravado(s)" % (len(lidos), novos))
                if protegidos:
                    log("   %d preenchido(s) por gente, nao toco: %s"
                        % (len(protegidos), ", ".join(protegidos[:6])))
                for campo in list(lidos)[:6]:
                    log("      %-24s %s" % (campo, str(lidos[campo])[:60]))
                if a.aplicar:
                    db.commit()
                    fdc.marcar(item["id"], item["ate_msg"], novos)
        except Exception as e:
            log("   (entrevista pela conversa falhou: %s)" % str(e)[:160])

    return 0


if __name__ == "__main__":
    sys.exit(main())
