# -*- coding: utf-8 -*-
"""
Termometro de leads trabalhistas (quadro "Reclamantes", id 10) — classificador.

Le as conversas do Sistema Escritório Modelo (sqlite local na VPS), classifica cada lead com
Claude Haiku (mesma chave da SDR de reclamantes) e grava na tabela lead_termometro:

  temperatura: quentissimo (violacao + ja saiu da empresa, ou caso urgente na empresa:
                            rescisao indireta, acidente/doenca do trabalho)
               quente      (violacao + ainda trabalhando)
               morno       (indicio de caso, falta informacao)
               frio        (sem indicio / prescricao bienal provavel)
               sem_caso    (assunto nao trabalhista)
               sem_resposta(lead nunca mandou texto — classificado SEM gastar API)
  situacao:    saiu | trabalhando | nao_informado
  violacoes:   JSON (horas extras, intervalo, acumulo de funcao, acidente, ...)
  resumo:      1 linha pronta pro advogado ligar

INCREMENTAL: so reclassifica conversa que tem mensagem nova desde a ultima
rodada (compara max(id) de wa_mensagens). Carga inicial ~400 chamadas Haiku;
depois, so o delta do dia.

Efeitos no board (salvo com --dry):
  - tag do card ganha o marcador 🔥/🟡/⚪ (preserva o texto da tag existente);
  - lead 🔥 que esta numa coluna de entrada sobe para "Lead Quente" (fila prioritaria);
  - reclamante com 12+ contatos nossos SEM resposta e inscrito no fluxo
    'reaquecimento' (roda mesmo sem creditos de API; o fluxo para se o lead responder).

USO (VPS):
  /opt/escritorio/venv/bin/python SYNC/termometro_leads.py            # classifica + aplica
  /opt/escritorio/venv/bin/python SYNC/termometro_leads.py --dry      # so imprime, nao grava
  /opt/escritorio/venv/bin/python SYNC/termometro_leads.py --max 15   # limita o lote
Cron: 13h30, antes do relatorio das 14h (relatorio_termometro.py).
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(os.getenv("ESC_CONFIG_ENV", "/opt/escritorio/config/.env"))
load_dotenv("/opt/escritorio/config/.env")  # ANTHROPIC_API_KEY (nao sobrescreve o que ja veio)

DB = os.getenv("SISTEMA_DB", "/opt/escritorio/SISTEMA/backend/sistema_esc.db")
MODELO = "claude-haiku-4-5"
PIPELINE_ESC_LEADS = 10
ETAPA_CAIXA = "Caixa de Entrada"
ETAPAS_ENTRADA = ("Caixa de Entrada", "Campanha Fast-food", "Campanha Supermercado",
                  "Campanha Acidente de Trabalho", "Campanha Geral")
ETAPA_LEAD_QUENTE = "Lead Quente"      # fila prioritaria dos 🔥
ETAPAS_FECHADAS = ("Ganho", "Perdido", "Perdido - Sem resposta", "Perdido - Sem interesse",
                   "Contrato / Assinatura", "Lead Desqualificado")
ETAPAS_SEM_MOVER = ("Lead Qualificado", "Contatado", "Pré fechamento [fluxo aquecimento]")
ETAPA_DESQUALIFICADO = "Lead Desqualificado"
# Trabalhador nao e desqualificado: quem ja tem advogado vai para Perdido - Sem interesse,
# quem nao quer agora entra no reaquecimento anual e continua no funil.
ETAPA_PERDIDO_SEM_INTERESSE = "Perdido - Sem interesse"
FLUXO_BIMESTRAL = "reaquecimento_bimestral"   # passa a ser anual: 4, 8 e 12 meses
MIN_CONTATOS_REAQUECIMENTO = 12
MARCADOR = {"quentissimo": "🔥", "quente": "🟡", "morno": "⚪"}
TEMPS_VALIDAS = {"quentissimo", "quente", "morno", "frio", "sem_caso"}
COMERCIAL_VALIDOS = {"segue", "ja_tem_advogado", "nao_quer", "adiar"}

DDL = """
create table if not exists lead_termometro (
    conversa_id     integer primary key,
    temperatura     text,
    situacao        text,
    violacoes       text,
    resumo          text,
    ultima_msg_id   integer,
    classificado_em text,
    modelo          text
)
"""

PROMPT = """Você é o classificador de leads trabalhistas do Escritório Modelo Advogados (defesa do trabalhador, reclamante).
Você recebe a conversa de WhatsApp de um lead com o escritório e devolve APENAS um JSON válido, sem markdown, no formato:
{"temperatura":"quentissimo|quente|morno|frio|sem_caso","situacao":"saiu|trabalhando|nao_informado","comercial":"segue|ja_tem_advogado|nao_quer|adiar","violacoes":["..."],"resumo":"..."}

REGRAS DE TEMPERATURA:
- "quentissimo": relata violação de direito trabalhista E já saiu da empresa (demitido, pediu conta, contrato encerrado) há MENOS de 2 anos; OU ainda trabalha mas relata situação grave e atual (salário atrasado, FGTS sem depósito, assédio, acidente ou doença do trabalho, afastamento): caso de rescisão indireta ou acidentário.
- "quente": relata violação de direito trabalhista E ainda trabalha na empresa, sem urgência atual.
- "morno": há indício de caso trabalhista, mas falta informação essencial (não disse se ainda trabalha, ou a violação está vaga).
- "frio": sem indício de caso (conversa morreu no começo, só cumprimentos, curiosidade) OU saiu da empresa há mais de 2 anos (anote "possível prescrição bienal" no resumo).
- "sem_caso": assunto não trabalhista (fornecedor, parceria, mentoria, assunto pessoal, engano, spam).

VIOLAÇÕES típicas (use estes nomes quando couber; pode acrescentar outras):
horas extras, banco de horas irregular, intervalo, trabalho em domingo e feriado, adicional noturno, acúmulo de função, desvio de função, insalubridade, periculosidade, assédio moral, acidente de trabalho, doença ocupacional, salário atrasado, FGTS não depositado, pagamento por fora, descontos indevidos, quebra de caixa, sem registro, verbas rescisórias, estabilidade (gestante, acidentária).

RESUMO: 1 linha (máx. 110 caracteres), telegráfica, o que o advogado precisa saber para ligar.
Ex.: "Saiu em jun/26 do mercado XYZ; HE + intervalo + acúmulo caixa/repositor". Se a época da saída aparecer na conversa, inclua.

CAMPO "comercial" — a intenção do lead decide o destino dele:
- "ja_tem_advogado": ele disse que JÁ TEM advogado ou ação em andamento sobre ESTE caso. Sai de pauta.
- "nao_quer": recusou de vez (não quer processar, pediu para parar de mandar mensagem, sem interesse definitivo).
- "adiar": tem interesse mas NÃO AGORA (vai esperar sair da empresa, resolver depois, "mais pra frente").
- "segue": nenhum dos anteriores (é o padrão; na dúvida, "segue").

CONTEXTO DO CARD: junto da conversa você recebe a ETAPA no CRM e as NOTAS que o time escreveu no card.
Use-as para classificar — se a nota diz que já foi contatado, que tem advogado, que desistiu etc., isso vale
tanto quanto a fala do lead. Não contradiga o que o time registrou.

ATENÇÃO — CTA DO ANÚNCIO: a primeira mensagem do lead costuma ser o botão pré-preenchido da campanha
(ex.: "Quero saber meus direitos trabalhistas", "Trabalhei em supermercado", "Sofri um acidente de trabalho",
"Posso ter mais informações sobre isso?").
Isso é frase pronta do anúncio, NÃO é relato do caso — não conta como violação nem como situação de emprego.
Se a conversa tiver só isso (ou quase só isso), classifique "frio" com resumo tipo "Só clicou no anúncio, não contou o caso".
Violações e situação só valem quando o lead relata com as palavras dele.
Baseie-se SÓ no que está na conversa. Não invente."""


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def transcricao(cur, conversa_id, max_msgs=60, max_chars=7000):
    rows = cur.execute(
        "select origem, tipo, conteudo from wa_mensagens "
        "where conversa_id=? and length(conteudo)>0 order by id desc limit ?",
        (conversa_id, max_msgs)).fetchall()
    rows.reverse()
    linhas = []
    for origem, tipo, cont in rows:
        quem = {"contato": "LEAD", "ia": "IA", "atendente": "Escritório Modelo", "nota": "NOTA"}.get(origem, origem)
        c = " ".join(cont.split())[:400]
        if tipo != "texto":
            c = f"[{tipo}] {c}"
        linhas.append(f"{quem}: {c}")
    return "\n".join(linhas)[-max_chars:]


def classificar(client, nome, tag, texto, etapa="", notas=""):
    msg = (f"Lead: {nome or '(sem nome)'}\nTag atual: {tag or '-'}\n"
           f"ETAPA NO CRM: {etapa or '-'}\n"
           f"NOTAS DO TIME NO CARD: {(' '.join((notas or '').split())[:400]) or '-'}\n"
           f"\nCONVERSA:\n{texto}")
    for tentativa in (1, 2):
        resp = client.messages.create(
            model=MODELO, max_tokens=300, system=PROMPT,
            messages=[{"role": "user", "content": msg}])
        bruto = resp.content[0].text
        m = re.search(r"\{.*\}", bruto, re.S)
        if not m:
            continue
        try:
            d = json.loads(m.group(0))
        except Exception:
            continue
        if d.get("temperatura") in TEMPS_VALIDAS:
            d.setdefault("situacao", "nao_informado")
            d.setdefault("violacoes", [])
            d.setdefault("resumo", "")
            if d.get("comercial") not in COMERCIAL_VALIDOS:
                d["comercial"] = "segue"
            return d
    return None


def _nota(cur, cid, texto):
    """Nota interna na conversa (aparece no chat ao vivo), padrao do vigia."""
    cur.execute(
        "insert into wa_mensagens (conversa_id, origem, autor, tipo, conteudo, "
        "midia_url, remote_id, criado_em) "
        "values (?,'nota','termometro','texto',?,'','',?)", (cid, texto, utcnow()))


def marcar_tag(tag_atual, temperatura):
    base = (tag_atual or "").strip()
    for emoji in MARCADOR.values():
        base = base.replace(emoji, "")
    base = base.strip()
    emoji = MARCADOR.get(temperatura)
    if not emoji:
        return base
    return f"{emoji} {base}".strip() if base else emoji


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="nao grava nada, so imprime")
    ap.add_argument("--max", type=int, default=0, help="limita quantas conversas classificar via API")
    a = ap.parse_args()

    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    con = sqlite3.connect(DB, timeout=15)
    con.execute("pragma busy_timeout=8000")
    cur = con.cursor()
    cur.execute(DDL)
    try:  # campo comercial: segue | ja_tem_advogado | nao_quer | adiar
        cur.execute("alter table lead_termometro add column comercial text")
    except sqlite3.OperationalError:
        pass  # coluna ja existe
    con.commit()

    convs = cur.execute(
        "select id, contato_nome, contato_telefone, etapa, tag, coalesce(notas,'') "
        "from wa_conversas "
        "where pipeline_id=? and upper(trim(coalesce(tag,''))) not in ('EMPRESA', 'PARCEIRO') "
        "order by id", (PIPELINE_ESC_LEADS,)).fetchall()  # empresa/parceiro nao e lead de reclamante:
    # nao recebe termometro nem tem a etiqueta reescrita pelo marcador de temperatura

    feitos_api = 0
    novos_fogo = []
    desqualificados = []
    adiados = []
    contagem = {}
    for cid, nome, tel, etapa, tag, notas in convs:
        ultima = cur.execute(
            "select max(id) from wa_mensagens where conversa_id=?", (cid,)).fetchone()[0] or 0
        prev = cur.execute(
            "select ultima_msg_id from lead_termometro where conversa_id=?", (cid,)).fetchone()
        if prev is not None and (prev[0] or 0) >= ultima:
            continue  # nada novo desde a ultima classificacao

        tem_texto_lead = cur.execute(
            "select 1 from wa_mensagens where conversa_id=? and origem='contato' "
            "and length(conteudo)>0 limit 1", (cid,)).fetchone()

        if not tem_texto_lead:
            d = {"temperatura": "sem_resposta", "situacao": "nao_informado",
                 "comercial": "segue", "violacoes": [],
                 "resumo": "Lead nunca respondeu com texto"}
            modelo_usado = "regra"
        else:
            if a.max and feitos_api >= a.max:
                continue
            try:
                d = classificar(client, nome, tag, transcricao(cur, cid), etapa, notas)
            except anthropic.BadRequestError as e:
                if "credit balance" in str(e):
                    print("[ABORTADO] chave Anthropic SEM CREDITOS — recarregar no "
                          "console.anthropic.com (mesma chave da SDR). "
                          "O que ja foi classificado ficou salvo; a proxima rodada continua de onde parou.")
                    break
                raise
            except anthropic.APIError as e:
                print(f"  [erro de API] conversa {cid}: {str(e)[:120]} — pulei")
                continue
            feitos_api += 1
            modelo_usado = MODELO
            if d is None:
                print(f"  [erro de parse] conversa {cid} ({nome}) — pulei sem gravar")
                continue

        contagem[d["temperatura"]] = contagem.get(d["temperatura"], 0) + 1
        if a.dry:
            print(f"  {cid:>4} {d['temperatura']:<12} {d['situacao']:<14} "
                  f"{(nome or '')[:25]:<25} {d['resumo'][:70]}")
            continue

        cur.execute(
            "insert into lead_termometro (conversa_id, temperatura, situacao, violacoes, "
            "resumo, ultima_msg_id, classificado_em, modelo, comercial) "
            "values (?,?,?,?,?,?,?,?,?) "
            "on conflict(conversa_id) do update set temperatura=excluded.temperatura, "
            "situacao=excluded.situacao, violacoes=excluded.violacoes, resumo=excluded.resumo, "
            "ultima_msg_id=excluded.ultima_msg_id, classificado_em=excluded.classificado_em, "
            "modelo=excluded.modelo, comercial=excluded.comercial",
            (cid, d["temperatura"], d["situacao"], json.dumps(d["violacoes"], ensure_ascii=False),
             d["resumo"][:200], ultima, utcnow(), modelo_usado, d.get("comercial", "segue")))

        # REGRA COMERCIAL: quem ja tem advogado ou recusou de
        # vez sai de pauta sozinho; quem quer "depois" entra na regua bimestral.
        comercial = d.get("comercial", "segue")
        if comercial == "ja_tem_advogado":
            # Trabalhador NAO e desqualificado. Quem ja tem advogado sai de
            # pauta como Perdido - Sem interesse, porque nao da para atuar no caso dele.
            motivo = "ja tem advogado/acao sobre o caso"
            if etapa not in ETAPAS_FECHADAS and etapa not in ETAPAS_SEM_MOVER:
                cur.execute("update wa_conversas set etapa=?, atualizado_em=? where id=?",
                            (ETAPA_PERDIDO_SEM_INTERESSE, utcnow(), cid))
                _nota(cur, cid, f"Termômetro: {motivo}. Card para "
                                f"'{ETAPA_PERDIDO_SEM_INTERESSE}'. Resumo: {d['resumo'][:120]}")
                desqualificados.append(f"{cid} {nome or tel} — {motivo}")
            elif etapa in ETAPAS_SEM_MOVER:
                _nota(cur, cid, f"Termômetro detectou: {motivo}. Card está com o time "
                                f"({etapa}), então NÃO movi — conferir.")
        elif comercial in ("nao_quer", "adiar"):
            ja_tem = cur.execute(
                "select 1 from wa_fluxos where conversa_id=? and tipo=?",
                (cid, FLUXO_BIMESTRAL)).fetchone()
            if not ja_tem and etapa not in ETAPAS_FECHADAS:
                cur.execute(
                    "insert into wa_fluxos (conversa_id, tipo, ativo, iniciado_em, "
                    "etapas_enviadas, criado_por) values (?,?,1,?,'','auto:adiar')",
                    (cid, FLUXO_BIMESTRAL, utcnow()))
                _nota(cur, cid, "Lead não quer agora ou pediu para falar mais pra frente "
                                "(regra Advogada 08/10): inscrito no reaquecimento anual, 3 toques "
                                "em 4, 8 e 12 meses; para sozinho se ele responder.")
                adiados.append(f"{cid} {nome or tel}")

        nova_tag = marcar_tag(tag, d["temperatura"])
        if nova_tag != (tag or "").strip():
            cur.execute("update wa_conversas set tag=? where id=?", (nova_tag, cid))
        if (comercial == "segue" and d["temperatura"] == "quentissimo"
                and etapa in ETAPAS_ENTRADA and etapa not in ETAPAS_FECHADAS):
            cur.execute("update wa_conversas set etapa=?, atualizado_em=? where id=?",
                        (ETAPA_LEAD_QUENTE, utcnow(), cid))
            novos_fogo.append(f"{nome or tel} — {d['resumo'][:80]}")
        con.commit()

    print(f"[{utcnow()} UTC] classificadas agora: {sum(contagem.values())} "
          f"(API: {feitos_api}) | por temperatura: {contagem or 'nada novo'}")
    if novos_fogo:
        print(f"Novos 🔥 movidos para {ETAPA_LEAD_QUENTE}:")
        for linha in novos_fogo:
            print("  " + linha)
    if desqualificados:
        print(f"Desqualificados automaticamente (já tem advogado / não quer): {len(desqualificados)}")
        for linha in desqualificados[:15]:
            print("  " + linha)
    if adiados:
        print(f"Inscritos no reaquecimento bimestral (quer depois): {len(adiados)}")
        for linha in adiados[:15]:
            print("  " + linha)

    inscritos = inscrever_reaquecimento(cur, dry=a.dry)
    if not a.dry:
        con.commit()
    if inscritos:
        print(f"Inscritos no reaquecimento (12+ contatos sem resposta): {len(inscritos)}")
        for linha in inscritos[:15]:
            print("  " + linha)
    con.close()


def inscrever_reaquecimento(cur, dry=False):
    """Reclamante do quadro Reclamantes com MIN_CONTATOS_REAQUECIMENTO+ mensagens nossas
    depois da ultima fala dele (ou desde sempre, se nunca falou) entra no fluxo
    'reaquecimento'. Uma unica vez por conversa; pula quem tem outro fluxo ativo
    e quem o termometro marcou como sem_caso. Exige 48h de silencio nosso antes
    de inscrever, pra nao emendar no mesmo dia o fim de um fluxo com o comeco do outro."""
    from datetime import timedelta
    corte48 = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
    marcas = ",".join("?" for _ in ETAPAS_FECHADAS)  # placeholders acompanham a tupla
    convs = cur.execute(
        "select c.id, c.contato_nome from wa_conversas c "
        "where c.pipeline_id=? and c.e_reclamante=1 "
        f"and c.etapa not in ({marcas}) "
        "and not exists (select 1 from wa_fluxos f where f.conversa_id=c.id "
        "                and (f.tipo='reaquecimento' or f.ativo=1)) "
        "and not exists (select 1 from lead_termometro t where t.conversa_id=c.id "
        "                and (t.temperatura='sem_caso' "
        "                     or coalesce(t.comercial,'segue')<>'segue'))",
        (PIPELINE_ESC_LEADS, *ETAPAS_FECHADAS)).fetchall()
    inscritos = []
    for cid, nome in convs:
        ult_lead = cur.execute(
            "select coalesce(max(id),0) from wa_mensagens "
            "where conversa_id=? and origem='contato'", (cid,)).fetchone()[0]
        nossos = cur.execute(
            "select count(*) from wa_mensagens where conversa_id=? "
            "and origem in ('ia','atendente') and id>?", (cid, ult_lead)).fetchone()[0]
        if nossos < MIN_CONTATOS_REAQUECIMENTO:
            continue
        ult_out = cur.execute(
            "select max(replace(criado_em,'T',' ')) from wa_mensagens "
            "where conversa_id=? and origem in ('ia','atendente')", (cid,)).fetchone()[0]
        if ult_out and ult_out > corte48:
            continue  # nossa ultima mensagem e recente demais; espera esfriar 48h
        if not dry:
            cur.execute(
                "insert into wa_fluxos (conversa_id, tipo, ativo, iniciado_em, "
                "etapas_enviadas, criado_por) values (?,'reaquecimento',1,?,'',"
                "'auto:12contatos')", (cid, utcnow()))
        inscritos.append(f"{cid} {(nome or '').strip()}".strip())
    return inscritos


if __name__ == "__main__":
    main()
