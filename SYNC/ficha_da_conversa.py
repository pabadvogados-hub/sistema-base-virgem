# -*- coding: utf-8 -*-
"""Volta na ficha depois que o card entra no funil de fechado.

O BURACO (confirmado nos dados):
quem move o card e uma pessoa, e ela move DEPOIS de desligar. O cron do
pos_ligacao roda 2 minutos depois da ligacao, quando o card ainda esta em "Lead
Quente" ou "Caixa de Entrada". O portao `no_funil_fechado` fecha, a ficha nao e
preenchida, mas a marca "=== POS-LIGACAO" e gravada assim mesmo. Quando o card
enfim vira "Fechou!", o laco principal pula a conversa para sempre, porque ela ja
esta marcada como tratada. Ou seja: a ligacao que FECHA o contrato e justamente a
que chega cedo demais e e queimada.

Prova no banco em 24/09: 15 conversas carimbadas com zero campos de ficha, 9
delas ainda vivas. E o portao do pipeline 10 cita 4 colunas, das quais 3 nao
existem no quadro ("Pos-Reuniao de Fechamento", "Elaboracao de Contrato",
"Ganho": 0 cards cada). Isso nao precisa ser consertado abrindo o portao: quem
fecha SAI do quadro de leads e vai para os pipelines 20, 18 e 11, que ja estao
inteiros no funil. O que falta e alguem voltar.

E o que este modulo faz. Ele nao olha a marca: olha o funil e a ficha.

MATERIA-PRIMA: so as TRANSCRICOES DA LIGACAO, nunca o chat solto. Regra do
Titular em 24/09: "vai ser sempre pelo discador do whats". A versao anterior
deste arquivo lia audio e texto avulso do WhatsApp; medido no mesmo dia, 10
conversas sem ligacao renderam 2 campos no total. Chat solto nao vira entrevista.
"""
import json
import os
from datetime import datetime

ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ficha_auto_state.json")
CABECA = "=== LIGACAO PELO WHATSAPP"
MARCA_RESUMO = "=== POS-LIGACAO"
LIMITE = 14000          # mesmo teto do pos_ligacao: acima disso a extracao volta vazia
MINIMO_BLOCO = 400      # abaixo disso e ligacao que caiu, nao rende campo


def _carregar():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _salvar(est):
    tmp = ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(est, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ESTADO)


def ligacoes_da_conversa(notas):
    """Cada ligacao da conversa, da mais antiga para a mais nova, sem os blocos
    de resumo que o proprio pos_ligacao escreveu depois."""
    if not notas or CABECA not in notas:
        return []
    fora = []
    for trecho in notas.split(CABECA)[1:]:
        bloco = CABECA + trecho
        corte = bloco.find("\n\n=== ", len(CABECA))
        if corte > 0:
            bloco = bloco[:corte]
        bloco = bloco.strip()
        if len(bloco) > LIMITE:
            bloco = bloco[-LIMITE:]      # o fim e onde costuma estar o fechamento
        if len(bloco) >= MINIMO_BLOCO:
            fora.append(bloco)
    return fora


def campos_com_conteudo(db, conversa_id):
    r = db.execute("select count(*) from ficha_entrevista where conversa_id=? "
                   "and trim(coalesce(valor,''))!=''", (conversa_id,)).fetchone()
    return r[0] if r else 0


def pendentes(db, dentro_do_funil, limite=None, minimo=20):
    """Conversas que ja tem ligacao gravada, ja entraram no funil de fechado e
    ainda estao com a ficha magra.

    Nao olha a marca "=== POS-LIGACAO" de proposito: e justamente a conversa
    marcada e vazia que precisa de uma segunda chance.
    """
    est = _carregar()
    fila = []
    for c in db.execute("select id, contato_nome, etapa, pipeline_id, notas "
                        "from wa_conversas where notas like ? order by id desc",
                        ("%" + CABECA + "%",)):
        if not dentro_do_funil(c):
            continue
        blocos = ligacoes_da_conversa(c["notas"])
        if not blocos:
            continue
        tem = campos_com_conteudo(db, c["id"])
        if tem >= minimo:
            continue                      # ja esta preenchida, nao gasto modelo
        ja = est.get(str(c["id"])) or {}
        # so repete se apareceu ligacao NOVA desde a ultima tentativa
        if ja.get("ligacoes", 0) >= len(blocos) and ja.get("campos", 0) > 0:
            continue
        if ja.get("ligacoes", 0) >= len(blocos) and ja.get("tentativas", 0) >= 2:
            continue                      # duas tentativas sem render: para de insistir
        fila.append({"id": c["id"], "nome": c["contato_nome"] or "",
                     "etapa": c["etapa"] or "", "pipeline": c["pipeline_id"],
                     "blocos": blocos, "tinha": tem, "ate_msg": len(blocos)})
        if limite and len(fila) >= limite:
            break
    return fila


def preencher(db, conversa_id, campos, extrair, gravar, aplicar, log=print, blocos=None):
    """Extrai de cada ligacao e junta. A mais nova corrige a mais antiga so no
    campo que ela tambem respondeu."""
    if blocos is None:
        r = db.execute("select notas from wa_conversas where id=?", (conversa_id,)).fetchone()
        blocos = ligacoes_da_conversa(r["notas"] if r else "")
    if not blocos:
        return {}, 0, []
    junto = {}
    for i, bloco in enumerate(blocos, 1):
        parcial = extrair(bloco, campos)
        if len(blocos) > 1:
            log("   ligacao %d/%d  : %d campo(s)" % (i, len(blocos), len(parcial)))
        junto.update(parcial)
    novos, protegidos = gravar(db, conversa_id, junto, aplicar=aplicar)
    return junto, novos, protegidos


def marcar(conversa_id, ligacoes, campos_gravados):
    est = _carregar()
    antes = est.get(str(conversa_id)) or {}
    est[str(conversa_id)] = {
        "ligacoes": ligacoes,
        "campos": campos_gravados,
        "tentativas": antes.get("tentativas", 0) + 1,
        "quando": datetime.now().isoformat(sep=" ", timespec="seconds"),
    }
    _salvar(est)
