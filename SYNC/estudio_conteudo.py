# -*- coding: utf-8 -*-
"""ESTUDIO DE CONTEUDO: escreve as pecas que a tela de Marketing enfileirou.

A tela so enfileira (estado 'fila'). Quem escreve e este cron, de 1 em 1 minuto,
usando o Claude Code da ASSINATURA. Nunca a API: ela vive sem saldo, e um recurso
que depende de saldo nasce morto.

LIMITES QUE NAO SE NEGOCIAM (Provimento 205/2021 da OAB):
  - conteudo informativo e educativo, nunca captacao;
  - nao promete resultado, nao estima valor, nao garante exito;
  - nao usa caso concreto de cliente como propaganda;
  - nao fala de honorarios;
  - sem sensacionalismo e sem "corra, ultimos dias".
Isso esta no pedido e e conferido na volta: peca que escorregar volta como erro,
em vez de ir pro calendario e alguem publicar sem ler.

Uso:
  estudio_conteudo.py            escreve a proxima da fila (cron)
  estudio_conteudo.py --todas    esvazia a fila
  estudio_conteudo.py --id N     refaz uma peca especifica
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime

DB = "/opt/escritorio/SISTEMA/backend/sistema_esc.db"
CLAUDE_BIN = os.getenv("ESC_CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))

# o que o escritorio faz, para a peca nascer no assunto certo
CONTEXTO = """O escritorio e o Escritório Modelo Advogados, de Cidade, liderado pela
Dra. Advogada Responsável (OAB/UF 000.000). Especialidade: direito do trabalho do
TRABALHADOR (reclamante): rescisao indireta, acidente de trabalho, trabalhadores de fast-food e
supermercado, e trabalhista em geral. Quem le e trabalhador: fala simples, direta, didatica,
sem juridiques. Nada de tratar o leitor como coitado."""

# O que a OAB proibe e o ESCRITORIO prometer RESULTADO. Descrever o que a LEI
# assegura e o proprio conteudo informativo que o Provimento 205 autoriza, entao
# "a lei garante", "direito assegurado pela CLT" e afins TEM de passar. A primeira
# versao caçava a palavra "garant" solta e reprovava peca correta.
PROIBIDO = [
    (re.compile(r"\bgarantimos\b|\bgaranto\b", re.I), "o escritorio prometendo resultado"),
    (re.compile(r"(n[oó]s|nosso escrit[oó]rio|a gente)\s+(lhe\s+)?garant\w+", re.I),
     "o escritorio prometendo resultado"),
    (re.compile(r"(resultado|[eê]xito|sucesso|vit[oó]ria|ganho)\s+garantid", re.I), "promessa de resultado"),
    (re.compile(r"garant\w+\s+(o\s+|seu\s+|a\s+)*(seu\s+)?([eê]xito|resultado|sucesso|vit[oó]ria|ganho|dinheiro)", re.I),
     "promessa de resultado"),
    (re.compile(r"voc[eê]\s+(vai|ir[aá])\s+(ganhar|receber|conseguir|levar)\b", re.I), "promessa de resultado"),
    (re.compile(r"com certeza voc[eê]\s+(vai|tem|recebe)", re.I), "promessa de resultado"),
    (re.compile(r"voc[eê] tem direito a\s*R\$|receb[ae]\s*(at[eé]\s*)?R\$\s*\d|indeniza[cç][aã]o de R\$", re.I),
     "estimativa de valor a receber"),
    (re.compile(r"\bhonor[aá]ri", re.I), "fala de honorarios"),
    (re.compile(r"[uú]ltim[oa]s? (dias|vagas|chance)|corra\b|n[aã]o perca|s[oó] hoje|agora ou nunca", re.I),
     "urgencia de propaganda"),
    (re.compile(r"melhor escrit[oó]rio|n[uú]mero 1\b|l[ií]der (nacional|do mercado)", re.I),
     "autopromocao comparativa"),
]

MOLDE = {
    "carrossel": ("um CARROSSEL de 6 a 8 slides para Instagram",
                  '"slides": [{"titulo": "ate 6 palavras", "texto": "ate 30 palavras"}]'),
    "reels": ("um ROTEIRO DE REELS de 45 a 60 segundos",
              '"slides": [{"titulo": "0:00-0:05 (o trecho)", "texto": "o que falar nesse trecho"}]'),
    "story": ("uma SEQUENCIA DE 3 A 5 STORIES",
              '"slides": [{"titulo": "story 1", "texto": "ate 25 palavras"}]'),
    "legenda": ("uma LEGENDA unica de post",
                '"slides": [{"titulo": "Post", "texto": "o texto inteiro, ate 150 palavras"}]'),
}


def log(m):
    print("%s %s" % (datetime.now().strftime("%H:%M:%S"), m), flush=True)


def escrever(item):
    """Devolve dict com titulo/slides/legenda/hashtags, ou (None, motivo)."""
    formato = (item["formato"] or "carrossel").lower()
    descricao, forma_slides = MOLDE.get(formato, MOLDE["carrossel"])

    extra = []
    if (item["publico"] or "").strip():
        extra.append("Publico especifico desta peca: " + item["publico"].strip())
    if (item["observacao"] or "").strip():
        extra.append("Pedido de quem encomendou: " + item["observacao"].strip())

    prompt = (
        CONTEXTO + "\n\n"
        "Escreva " + descricao + " sobre o tema abaixo.\n\n"
        "TEMA: " + (item["tema"] or "").strip() + "\n"
        + ("\n".join(extra) + "\n" if extra else "") + "\n"
        "REGRAS DA OAB (Provimento 205/2021) QUE NAO SE NEGOCIAM:\n"
        "- conteudo informativo e educativo. Explicar direito, nunca captar cliente.\n"
        "- NAO prometa resultado, NAO estime valor a receber, NAO garanta exito.\n"
        "- NAO cite caso concreto de cliente.\n"
        "- NAO fale de honorarios nem de preco.\n"
        "- sem sensacionalismo, sem contagem regressiva, sem 'ultimas vagas'.\n"
        "- pode convidar a procurar um advogado para analisar O CASO DA PESSOA.\n\n"
        "COMO ESCREVER:\n"
        "- lingua do trabalhador, frase curta, zero juridiques. Se usar um termo tecnico, explique na hora.\n"
        "- o primeiro slide e um gancho concreto do dia a dia dele, nao uma pergunta generica.\n"
        "- o ultimo slide fecha com o que ele deve FAZER (guardar documento, procurar orientacao).\n"
        "- NAO use travessao. Nem o longo nem o medio.\n"
        "- sem emoji dentro dos slides. Na legenda, no maximo um.\n\n"
        "Responda JSON puro, sem texto fora do JSON:\n"
        '{"titulo": "titulo interno da peca, ate 8 palavras",\n'
        ' ' + forma_slides + ',\n'
        ' "legenda": "legenda do post, ate 80 palavras",\n'
        ' "hashtags": "5 a 8 hashtags separadas por espaco"}\n')

    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)          # assinatura, nunca API
    try:
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", "sonnet", "--tools", "",
                            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
                           input=prompt.encode("utf-8"), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env,
                           cwd=tempfile.gettempdir(), timeout=240)
    except subprocess.TimeoutExpired:
        return None, "demorou demais para escrever"
    if r.returncode != 0:
        return None, ("Claude recusou: " + r.stderr.decode("utf-8", "replace")[:160])
    m = re.search(r"\{.*\}", r.stdout.decode("utf-8", "replace"), re.S)
    if not m:
        return None, "resposta sem JSON"
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None, "JSON invalido"

    slides = [s for s in (d.get("slides") or []) if isinstance(s, dict) and (s.get("texto") or "").strip()]
    if len(slides) < 2 and formato != "legenda":
        return None, "veio com menos de 2 partes"
    if not slides:
        return None, "veio sem conteudo"

    # conferencia da OAB na volta: peca que escorrega nao vai pro calendario
    texto_todo = " ".join([d.get("titulo") or "", d.get("legenda") or ""] +
                          [(s.get("titulo") or "") + " " + (s.get("texto") or "") for s in slides])
    for padrao, motivo in PROIBIDO:
        achado = padrao.search(texto_todo)
        if achado:
            ini = max(0, achado.start() - 45)
            trecho = texto_todo[ini:achado.end() + 45].replace("\n", " ")
            return None, "reprovada na conferencia da OAB (%s): ...%s..." % (motivo, trecho)

    return {
        "titulo": (d.get("titulo") or item["tema"])[:200],
        "slides": json.dumps(slides, ensure_ascii=False),
        "legenda": (d.get("legenda") or "")[:2000],
        "hashtags": (d.get("hashtags") or "")[:300],
    }, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--todas", action="store_true")
    ap.add_argument("--id", type=int)
    a = ap.parse_args()

    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma busy_timeout=8000")
    try:
        con.execute("select 1 from mkt_pauta limit 1")
    except sqlite3.OperationalError:
        return 0                          # tabela ainda nao existe: nada a fazer
    # colunas extras da pauta (trava, estatico, angulo), mantidas para a tela
    cols = {r[1] for r in con.execute("PRAGMA table_info(mkt_pauta)")}
    for nome in ("trava", "estatico", "angulo"):
        if nome not in cols:
            con.execute("ALTER TABLE mkt_pauta ADD COLUMN %s TEXT" % nome)
    con.commit()

    if a.id:
        fila = con.execute("select * from mkt_pauta where id=?", (a.id,)).fetchall()
    else:
        limite = 100 if a.todas else 1
        fila = con.execute("select * from mkt_pauta where estado='fila' order by id limit ?",
                           (limite,)).fetchall()
    if not fila:
        return 0

    for item in fila:
        log("escrevendo #%s [%s] %s" % (item["id"], item["formato"], (item["tema"] or "")[:60]))
        con.execute("update mkt_pauta set estado='gerando' where id=?", (item["id"],))
        con.commit()
        pronto, erro = escrever(item)
        agora = datetime.now().isoformat(sep=" ", timespec="seconds")
        if not pronto:
            log("   ! %s" % erro)
            con.execute("update mkt_pauta set estado='erro', erro=?, gerado_em=? where id=?",
                        (erro[:400], agora, item["id"]))
        else:
            con.execute(
                "update mkt_pauta set estado='pronto', titulo=?, slides=?, legenda=?, "
                "hashtags=?, trava=?, estatico=?, angulo=?, erro='', gerado_em=?, "
                "imagens=NULL where id=?",
                (pronto["titulo"], pronto["slides"], pronto["legenda"], pronto["hashtags"],
                 pronto.get("trava"), pronto.get("estatico"), pronto.get("angulo"),
                 agora, item["id"]))
            log("   pronto: %s (%d partes)" % (pronto["titulo"], len(json.loads(pronto["slides"]))))
        con.commit()
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
