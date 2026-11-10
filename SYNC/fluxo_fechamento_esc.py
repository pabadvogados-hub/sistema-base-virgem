# -*- coding: utf-8 -*-
"""
FLUXO DE FECHAMENTO POS-REUNIAO - quadro Reclamantes (pipeline 10)

Regua de 12 contatos para o lead que saiu da reuniao de fechamento sem assinar
(a maior parte fecha entre o 8o e o 12o contato).

Regras:
  - Entra na regua: card na etapa "Pós-Reunião de Fechamento" com telefone e a
    etiqueta QUALIFICADO (dedup por final do telefone, 8 digitos).
  - Cadencia: toques nos dias D+0,1,2,4,6,8,10,12,14,17,20,25 (12 toques),
    enviados 1x/dia dentro da janela 8h-20h.
  - Lead RESPONDEU: a regua PARA e o time e avisado para assumir (fechamento e humano).
  - Card SAIU da etapa: regua encerra sozinha. Se foi para Contrato / Assinatura ou
    Ganho, o time recebe a boa noticia.
  - Compliance (Provimento 205/2021): nenhuma promessa de resultado; linguagem simples, sem travessao.

.env: FECHAMENTO_AVISO_TEL (quem recebe os avisos; vazio = nao avisa),
      DEPOIMENTOS_LINK (pasta publica de depoimentos; vazio = toque 3 sem link).

Uso:
    python SYNC/fluxo_fechamento_esc.py             # dry-run (modo sombra)
    python SYNC/fluxo_fechamento_esc.py --enviar    # envia de verdade
    python SYNC/fluxo_fechamento_esc.py --status    # mostra a fila
Cron (sombra ate o escritorio aprovar as mensagens; depois adicionar --enviar):
    5 10 * * * cd /opt/escritorio && /opt/escritorio/venv/bin/python       /opt/escritorio/SYNC/fluxo_fechamento_esc.py >> /var/log/escritorio-fechamento.log 2>&1
"""
import io
import json
import os
import sqlite3
import sys
from datetime import date, datetime

if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from dotenv import load_dotenv
load_dotenv(os.getenv("ESC_CONFIG_ENV", os.path.join(HERE, "..", "config", ".env")))
if os.path.exists("/opt/escritorio/config/.env"):
    load_dotenv("/opt/escritorio/config/.env")

import requests
import wa_dapi  # saida unica de WhatsApp: D-API

DB = os.getenv("ESC_DB", "/opt/escritorio/SISTEMA/backend/sistema_esc.db")
FILA = os.path.join(HERE, "fechamento_fila.json")
PIPELINE = 10
ETAPA = "Pós-Reunião de Fechamento"
ETAPAS_GANHO = ("Contrato / Assinatura", "Ganho")
CADENCIA = (0, 1, 2, 4, 6, 8, 10, 12, 14, 17, 20, 25)
JANELA = (8, 20)
TEL_TIME = os.getenv("FECHAMENTO_AVISO_TEL") or os.getenv("TEL_ALERTA_EQUIPE", "")

# So entra na regua quem foi QUALIFICADO pelo time: a etiqueta do card na coluna
# Pos-Reuniao precisa conter a palavra QUALIFICADO.
MARCA_QUALIFICADO = "QUALIFICADO"

# Pasta de depoimentos enviada no toque 3 (compartilhada como 'qualquer pessoa com o link').
DEPO_LINK = os.getenv("DEPOIMENTOS_LINK", "")
ADVOGADA = os.getenv("SDR_ADVOGADA_RESPONSAVEL", "Dra. Advogada")
SITE = os.getenv("SITE_URL", "")

# 12 toques: tocar na dor com respeito, explicar o caminho de forma didatica, prazo,
# seguranca da decisao. Sem travessao e SEM promessa de resultado (Provimento OAB 205/2021).
# {nome} = primeiro nome.
MENSAGENS = (
    "Olá, {nome}. Aqui é do Escritório Modelo Advogados. Foi muito bom conversar com você na "
    "reunião. Ficou alguma dúvida sobre o que conversamos? Pode me perguntar aqui, sem "
    "compromisso.",

    "{nome}, o que você contou tem nome e tem valor na Justiça do Trabalho: hora extra que não "
    "foi paga, intervalo cortado, verbas que ficaram para trás. Você não precisa aceitar isso "
    "calado.",

    "{nome}, você não precisa acreditar só na gente. Veja quem já passou por isso:\n"
    "{depoimentos}\nSão trabalhadores como você, que decidiram buscar os seus direitos.",

    "{nome}, explicando o caminho: você assina o contrato e a procuração pelo celular, envia os "
    "documentos, a advogada prepara a ação e o escritório acompanha tudo até o fim. Você não "
    "fica sozinho em nenhuma etapa.",

    "Preciso ser transparente, {nome}: os direitos trabalhistas têm prazo. A ação precisa ser "
    "proposta em até 2 anos depois da saída da empresa. Cada mês que passa pode significar "
    "perder parte do que é seu.",

    "{nome}, se ainda está na empresa e a situação está difícil (salário atrasado, FGTS sem "
    "depósito, assédio, função acumulada), existe a rescisão indireta. A advogada avalia o seu "
    "caso antes de qualquer decisão. Me chama aqui que eu explico.",

    "{nome}, pense no que essa causa pode representar para você e para a sua família. "
    "Buscar o que é seu por direito não é aventura. É justiça.",

    "{nome}, a análise do seu caso está pronta. A equipe da " + ADVOGADA + " olhou com cuidado "
    "o que você contou. A parte difícil você já fez. Agora falta só uma resposta sua.",

    "{nome}, começar é simples: documento com foto, carteira de trabalho e comprovante de "
    "residência, tudo pelo WhatsApp. A assinatura é digital, no celular, sem cartório. O resto "
    "fica com a gente.",

    "{nome}, quem cuida do seu caso é a " + ADVOGADA + ", advogada trabalhista do escritório"
    + (". Conheça o nosso trabalho: " + SITE if SITE else "") + ".",

    "{nome}, já vimos muita gente deixar para depois e perder o prazo. Não queremos que isso "
    "aconteça com você. A sua análise continua pronta aqui. Uma resposta sua e a gente começa.",

    "{nome}, esta é a minha última mensagem. Se quiser seguir com o seu caso, é só responder "
    "aqui. A porta do Escritório Modelo Advogados segue aberta. Um abraço.",
)

MSG3_SEM_LINK = (
    "{nome}, muitos trabalhadores que atendemos decidiram buscar os seus direitos e hoje "
    "contam essa história com tranquilidade. Se quiser ouvir esses depoimentos, me manda um "
    "oi que eu te envio."
)


def _json(path, padrao):
    try:
        return json.loads(open(path, encoding="utf-8").read())
    except Exception:
        return padrao


def _salvar(path, dados):
    open(path, "w", encoding="utf-8").write(json.dumps(dados, ensure_ascii=False, indent=1))


def _tel8(tel):
    d = "".join(ch for ch in str(tel or "") if ch.isdigit())
    return d[-8:] if len(d) >= 8 else d


def _ddi(tel):
    d = "".join(ch for ch in str(tel) if ch.isdigit())
    return d if d.startswith("55") else "55" + d


def zapi_enviar_texto(telefone, texto):
    # Canal: D-API pelo numero oficial (wa_dapi); o nome da funcao ficou so para nao mexer nos chamadores.
    # ok so quando a D-API confirmou o envio (com id da mensagem). Sem telefone (aviso nao configurado): nao envia.
    if not str(telefone or "").strip():
        print("  (aviso ao time nao configurado: FECHAMENTO_AVISO_TEL vazio)")
        return False
    try:
        ok, info = wa_dapi.enviar_texto(_ddi(telefone), texto)
        if not ok:
            print(f"  (d-api falhou: {str(info)[:120]})")
        return ok
    except Exception as e:
        print(f"  (d-api erro: {str(e)[:120]})")
        return False


def _con():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    con.execute("pragma busy_timeout=8000")
    return con


def _cards_na_etapa():
    con = _con()
    rows = con.execute(
        "select id, contato_nome, contato_telefone, etapa, tag from wa_conversas "
        "where pipeline_id=?", (PIPELINE,)).fetchall()
    con.close()
    return rows


def _qualificado(tag):
    return MARCA_QUALIFICADO in (tag or "").upper()


def _respondeu_depois(conversa_id, desde_iso):
    con = _con()
    n = con.execute(
        "select count(*) from wa_mensagens where conversa_id=? and origem='contato' "
        "and criado_em > ?", (conversa_id, desde_iso)).fetchone()[0]
    con.close()
    return n > 0


def _primeiro_nome(nome):
    p = (nome or "").strip().split()
    return p[0].title() if p else "amigo"


def rodar(enviar=False):
    agora = datetime.now()
    print(f"=== FLUXO FECHAMENTO POS-REUNIAO | {agora:%d/%m/%Y %H:%M} | "
          + ("ENVIAR" if enviar else "SOMBRA (dry-run)") + " ===")
    fila = _json(FILA, {})
    cards = _cards_na_etapa()
    na_etapa = {}      # tel8 -> card QUALIFICADO na etapa
    etapa_por_tel = {}
    for cid, nome, tel, etapa, tag in cards:
        t8 = _tel8(tel)
        if not t8:
            continue
        etapa_por_tel[t8] = (cid, nome, tel, etapa)
        if etapa == ETAPA:
            if _qualificado(tag):
                na_etapa[t8] = (cid, nome, tel)
            elif t8 not in fila:
                print(f"  [AGUARDA QUALIFICACAO] {nome} (etiqueta: {tag or 'sem'})")

    # 1) inscreve quem chegou na etapa JA qualificado pelo time
    for t8, (cid, nome, tel) in na_etapa.items():
        if t8 not in fila:
            fila[t8] = {"conversa_id": cid, "nome": nome, "telefone": tel,
                        "d0": date.today().isoformat(),
                        "inscrito_em": agora.strftime("%Y-%m-%d %H:%M:%S"),
                        "toques": [], "respondeu": False, "encerrado": False}
            print(f"  [INSCREVER] {nome} (tel final {t8}) | QUALIFICADO | D0 hoje")

    # 2) encerra quem saiu da etapa / avisa fechamento
    for t8, c in fila.items():
        if c.get("encerrado"):
            continue
        if t8 not in na_etapa:
            info = etapa_por_tel.get(t8)
            nova = info[3] if info else "(fora do pipeline)"
            c["encerrado"] = True
            c["motivo"] = ("etiqueta QUALIFICADO removida (regua parada pelo time)"
                           if nova == ETAPA else f"saiu da etapa -> {nova}")
            print(f"  [ENCERRAR] {c['nome']} | {c['motivo']}")
            if nova in ETAPAS_GANHO:
                aviso = (f"🎉 Fluxo de fechamento: *{c['nome']}* avançou para "
                         f"'{nova}' após {len(c['toques'])} toque(s) da régua!")
                if enviar:
                    zapi_enviar_texto(TEL_TIME, aviso)
                else:
                    print(f"    (sombra: avisaria o time: {aviso})")

    # 3) respondeu? para a regua e chama humano
    for t8, c in fila.items():
        if c.get("encerrado") or c.get("respondeu"):
            continue
        desde = c.get("ultimo_toque_em") or c.get("inscrito_em")
        if _respondeu_depois(c["conversa_id"], desde):
            c["respondeu"] = True
            aviso = (f"🗣️ Fluxo de fechamento: *{c['nome']}* RESPONDEU no WhatsApp. "
                     f"Régua pausada no toque {len(c['toques'])}/12. "
                     "Hora do humano assumir e fechar! 💛")
            print(f"  [RESPONDEU] {c['nome']} | regua pausada")
            if enviar:
                zapi_enviar_texto(TEL_TIME, aviso)
            else:
                print(f"    (sombra: avisaria o time: {aviso})")

    # 4) toques do dia
    h = agora.hour
    if not (JANELA[0] <= h < JANELA[1]):
        print(f"  (fora da janela {JANELA[0]}h-{JANELA[1]}h: sem toques)")
    else:
        for t8, c in fila.items():
            if c.get("encerrado") or c.get("respondeu"):
                continue
            dias = (date.today() - date.fromisoformat(c["d0"])).days
            devidos = [i for i, d in enumerate(CADENCIA) if d <= dias]
            if not devidos:
                continue
            prox = devidos[-1]           # so o toque mais recente devido
            if prox in c["toques"]:
                continue
            if c.get("ultimo_toque_dia") == date.today().isoformat():
                continue                 # no maximo 1 toque por dia
            if prox == 2 and not DEPO_LINK:
                msg = MSG3_SEM_LINK.format(nome=_primeiro_nome(c["nome"]))
            else:
                msg = MENSAGENS[prox].format(nome=_primeiro_nome(c["nome"]),
                                             depoimentos=DEPO_LINK)
            print(f"  [TOQUE {prox + 1}/12] {c['nome']} (D+{dias})")
            if enviar:
                if zapi_enviar_texto(c["telefone"], msg):
                    c["toques"].append(prox)
                    c["ultimo_toque_dia"] = date.today().isoformat()
                    c["ultimo_toque_em"] = agora.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    print("    envio FALHOU, tenta amanha")
            else:
                print(f"    (sombra: enviaria: {msg[:90]}...)")

    if enviar:  # dry-run NUNCA salva estado (licao da varredura, 12/08)
        _salvar(FILA, fila)
    ativos = sum(1 for c in fila.values() if not c.get("encerrado"))
    print(f"=== fim | {ativos} na regua ===")


def status():
    fila = _json(FILA, {})
    if not fila:
        print("Fila vazia.")
        return
    for t8, c in fila.items():
        flag = ("ENCERRADO: " + c.get("motivo", "")) if c.get("encerrado") else (
            "RESPONDEU (humano)" if c.get("respondeu") else "ativo")
        print(f"- {c['nome']} | D0 {c['d0']} | toques {len(c['toques'])}/12 | {flag}")


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    else:
        rodar(enviar="--enviar" in sys.argv)
