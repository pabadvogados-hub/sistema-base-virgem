# -*- coding: utf-8 -*-
"""
contratacao_documentos.py  -  o que se pede ao cliente na fase de contratacao,
como se classifica o que ele manda e quando o card esta pronto para virar
contrato.

REGRAS DA ADVOGADA (24/09/2026), depois do contrato CT. 100/2026 ter sido
assinado com os {{campos}} do modelo a mostra:

1. O pedido inicial passa a ser de TRES documentos:
     CNH  +  comprovante de endereco  +  CTPS (digital ou foto)

2. "Precisamos que ele envie nessa fase ou CTPS ou o nome da reclamada para
    preenchermos o contrato, procuracao e declaracao de hipossuficiencia."
   Entao a CTPS nao e obrigatoria em si: o que nao pode faltar e SABER CONTRA
   QUEM se vai entrar. CTPS resolve (traz o vinculo) e o cliente dizer o nome
   da empresa tambem resolve.

3. "Se nao for enviado o documento, pode ser substituido pelo texto escrito
    com os dados do cliente." Vale para os tres.

4. "CNH ilegivel deve ser verificada e feita uma nova solicitacao automatica
    para o cliente." Foto ruim nao trava o card em silencio: o robo pede de
    novo, explicando o que houve.

Este modulo NAO envia mensagem e NAO escreve estado. Ele decide e devolve
texto; quem fala com o cliente e o vigia.
"""

import re

# --------------------------------------------------------- o que se pede ---

DOCUMENTOS_DO_CONTRATO = ("cnh", "comprovante_residencia", "ctps")

ROTULO = {
    "cnh": u"a sua CNH",
    "comprovante_residencia": u"o seu comprovante de endereço",
    "ctps": u"a sua carteira de trabalho",
}

FALTA = {
    "cnh": u"a foto da sua CNH (frente e verso) ou o arquivo da CNH digital",
    "comprovante_residencia": u"um comprovante de endereço (conta de luz, água ou telefone, "
                              u"pode estar no nome de outra pessoa)",
    "ctps": u"a sua carteira de trabalho digital (o PDF do aplicativo) ou uma foto dela",
}

ROTULO_ARQ = {
    "cnh": u"CNH",
    "comprovante_residencia": u"COMPROVANTE DE ENDERECO",
    "ctps": u"CTPS",
}


MSG_PEDIDO = (
    u"Oi {nome}! Agora vamos preparar o seu contrato.\n\n"
    u"Para isso eu preciso de três coisas:\n"
    u"📄 Foto da sua CNH, frente e verso\n"
    u"🏠 Um comprovante de endereço recente, conta de luz, água ou telefone\n"
    u"📘 A sua carteira de trabalho digital, o PDF do aplicativo, ou uma foto dela\n\n"
    u"O comprovante pode estar no nome de outra pessoa, não tem problema. "
    u"Eu só preciso do endereço.\n\n"
    u"Pode mandar foto aqui mesmo pelo WhatsApp. Assim que chegar eu confiro "
    u"na hora e já monto tudo para você.")

MSG_PARCIAL = (
    u"Boa, {nome}! {recebido} chegou certinho aqui. ✅\n\n"
    u"Agora só falta {faltando} para eu montar o seu contrato. Pode mandar por "
    u"aqui mesmo.")

# Arquivo que chegou mas nao e nenhum dos documentos do contrato (Advogada, 26/10/2026):
# o cliente ouve que chegou, que serve para o caso, e o que falta para o contrato.
MSG_RECEBI_OUTROS = (
    u"Obrigada, {nome}! Recebi os arquivos que você mandou e já guardei com o seu caso 📂\n\n"
    u"Eles vão servir na sua ação. Para eu montar o seu CONTRATO, o que falta agora é "
    u"{faltando}. Pode mandar por aqui mesmo, foto pelo celular resolve.")

MSG_COMPLETO = (
    u"Perfeito, {nome}! Recebi tudo que eu precisava. ✅\n\n"
    u"Agora vou preparar o seu contrato, a procuração e a declaração. Assim que "
    u"ficarem prontos, te mando o link para assinar aqui mesmo pelo celular. É "
    u"rapidinho.")

# CNH ilegivel: o robo pede outra foto sozinho (regra da Advogada 24/09)
MSG_CNH_ILEGIVEL = (
    u"{nome}, a sua CNH chegou aqui, mas não consegui ler os dados dela: "
    u"{motivo}.\n\n"
    u"Pode tirar outra foto para mim? Duas dicas que resolvem quase sempre: "
    u"apoie o documento numa superfície plana e tire a foto de cima, sem "
    u"flash, com o documento preenchendo a tela. Se você tiver o aplicativo "
    u"da Carteira Digital de Trânsito, o PDF de lá é ainda melhor.")

# CTPS nao veio: o nome da empresa resolve (regra da Advogada 24/09)
MSG_SEM_CTPS = (
    u"{nome}, ainda não recebi a sua carteira de trabalho.\n\n"
    u"Se estiver difícil de achar, me resolve do mesmo jeito: me diga o "
    u"NOME COMPLETO DA EMPRESA em que você trabalhou, do jeito que aparece no "
    u"holerite ou no crachá. Só com isso eu já consigo montar o seu contrato.")

MSG_LEMBRETE = (
    u"Oi {nome}! Para preparar o seu contrato ainda preciso de: {faltando}.\n\n"
    u"Pode mandar foto aqui pelo WhatsApp quando conseguir. Qualquer dúvida, me "
    u"chama.")

LEMBRETES = [
    u"Oi {nome}! Você deu um passo que muitos colegas ainda não tiveram coragem "
    u"de dar: levantou a voz por essa categoria. Parabéns! 🚛\n\n"
    u"Para o seu caso andar, agora só falta {faltando}.\n\n"
    u"Manda foto aqui mesmo pelo WhatsApp que eu já dou o próximo passo na hora.",

    u"{nome}, seu processo está quase pronto para avançar! Falta só {faltando}.\n\n"
    u"É rapidinho: foto pelo celular e pronto. Quanto antes chegar aqui, mais "
    u"rápido o seu contrato fica pronto e a gente entra com tudo por você.",

    u"Oi {nome}! Não quero deixar o seu caso esperando.\n\n"
    u"Assim que você me mandar {faltando}, eu preparo seus documentos na hora e "
    u"te aviso aqui. Estamos juntos nessa, combinado?",
]


# ------------------------------------------------------ o classificador ----

INSTRUCAO_CLASSIFICACAO = (
    u"Você classifica documentos enviados por clientes de um escritório "
    u"trabalhista. Responda APENAS um JSON {\"tipo\": \"...\"} com um destes "
    u"valores:\n"
    u"'cnh' = Carteira Nacional de Habilitação brasileira (frente, verso, "
    u"aberta ou a CNH digital do aplicativo);\n"
    u"'ctps' = Carteira de Trabalho, tanto a Carteira de Trabalho Digital "
    u"(PDF ou print do aplicativo, com as abas de contratos de trabalho) "
    u"quanto a carteira de papel (capa, página de qualificação civil ou "
    u"página de contrato de trabalho);\n"
    u"'comprovante_residencia' = conta de luz, água, gás, telefone, internet, "
    u"fatura ou correspondência que mostre um endereço. Vale mesmo que o nome "
    u"do titular NÃO seja o do cliente, o que interessa aqui é o endereço;\n"
    u"'outro' = qualquer outra coisa (RG avulso, holerite, extrato, TRCT, "
    u"selfie, print de conversa, foto de caminhão etc.).\n"
    u"Na dúvida entre 'ctps' e 'outro', olhe se há registro de contrato de "
    u"trabalho, PIS ou número e série de carteira: se houver, é 'ctps'."
)

TIPOS_VALIDOS = ("cnh", "ctps", "comprovante_residencia", "outro")


# ------------------------------------------------------------- o gate ------

def _tem_endereco_no_texto(texto):
    """O cliente escreveu o endereco em vez de mandar o comprovante."""
    t = texto or ""
    if re.search(r"\b\d{5}-?\d{3}\b", t):
        return True
    return bool(re.search(
        r"\b(?:rua|r\.|av\.?|avenida|alameda|travessa|estrada|rodovia)\s+"
        r"[^\n,;]{3,60}[,\s]+n?[º°.]?\s*\d{1,6}", t, re.I))


# ----------------------------------------------- CEP: a automacao acha ------
# REGRA DA PAM (14/10/2026, caso Francisco Roberio, conversa 1768): CEP e
# pedaco de endereco quem acha e a AUTOMACAO (ViaCEP -> busca na web pelo
# Claude da VPS); contrato NUNCA para por CEP que da para achar. Voltar a
# pedir o comprovante ao cliente e o ultimissimo recurso, quando nada se
# achou. Motivo tecnico: o INTAKE preenche {{Bairro}}/{{Cidade Estado}}/
# {{CEP}} dos campos SEPARADOS bairro/cidade_estado/cep da qualificacao;
# endereco so como frase deixa os tres abertos e a trava do preambulo barra.

_RE_CEP = re.compile(r"\b(\d{5})-?(\d{3})\b")
_UFS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT",
        "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO",
        "RR", "SC", "SP", "SE", "TO"}
_DDD_UF = {
    "11": "SP", "12": "SP", "13": "SP", "14": "SP", "15": "SP", "16": "SP",
    "17": "SP", "18": "SP", "19": "SP", "21": "RJ", "22": "RJ", "24": "RJ",
    "27": "ES", "28": "ES", "31": "MG", "32": "MG", "33": "MG", "34": "MG",
    "35": "MG", "37": "MG", "38": "MG", "41": "PR", "42": "PR", "43": "PR",
    "44": "PR", "45": "PR", "46": "PR", "47": "SC", "48": "SC", "49": "SC",
    "51": "RS", "53": "RS", "54": "RS", "55": "RS", "61": "DF", "62": "GO",
    "63": "TO", "64": "GO", "65": "MT", "66": "MT", "67": "MS", "68": "AC",
    "69": "RO", "71": "BA", "73": "BA", "74": "BA", "75": "BA", "77": "BA",
    "79": "SE", "81": "PE", "82": "AL", "83": "PB", "84": "RN", "85": "CE",
    "86": "PI", "87": "PE", "88": "CE", "89": "PI", "91": "PA", "92": "AM",
    "93": "PA", "94": "PA", "95": "RR", "96": "AP", "97": "AM", "98": "MA",
    "99": "MA"}


def _sem_acento(s):
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(ch) != "Mn")


def _parse_endereco(texto, telefone=None):
    """Heuristica barata sobre o que o cliente escreveu: rua, numero, bairro,
    cidade e UF. O que ela nao resolver, a busca na web resolve."""
    t = " ".join((texto or "").split())
    p = {}
    m = re.search(r"\b(?:rua|r\.|av\.?|avenida|alameda|travessa|estrada|rodovia|"
                  r"pra[cç]a)\s+([^\n,;]{3,50}?)\s*(?:,|n[uú]mero|n[º°o.]|\b\d)",
                  t, re.I)
    if m:
        p["rua"] = m.group(1).strip()
    m = re.search(r"(?:n[uú]mero|n[º°o.]?)\s*(\d{1,6})", t, re.I)
    if m:
        p["numero"] = m.group(1)
    m = re.search(r"\bbairro\s+(\S+(?:\s+\S+)?)", t, re.I)
    if m:
        p["bairro"] = m.group(1).strip(" ,.;")
    tokens = re.sub(r"[.,;]", " ", t).split()
    if tokens and tokens[-1].upper() in _UFS:
        p["uf"] = tokens[-1].upper()
        resto = tokens[:-1]
        if resto and not resto[-1].isdigit():
            p["cidade"] = resto[-1]
    if not p.get("uf") and telefone:
        dig = re.sub(r"\D", "", telefone or "")
        if dig.startswith("55"):
            dig = dig[2:]
        p["uf"] = _DDD_UF.get(dig[:2])
    return p


def _viacep_cep(cep8):
    import requests
    try:
        r = requests.get("https://viacep.com.br/ws/%s/json/" % cep8, timeout=12)
        d = r.json() if r.status_code == 200 else {}
        return None if (not d or d.get("erro")) else d
    except Exception:
        return None


def _viacep_rua(p):
    """Busca por UF + cidade + nome da rua, tentando variacoes de grafia."""
    import requests
    from urllib.parse import quote
    uf, cidade, rua = p.get("uf"), p.get("cidade"), p.get("rua")
    if not (uf and cidade and rua and len(cidade) >= 3 and len(rua) >= 3):
        return None
    tentativas, vistos = [], set()
    for v in (rua, _sem_acento(rua), rua.split()[-1], _sem_acento(rua.split()[-1])):
        v = v.strip()
        if len(v) >= 3 and v.lower() not in vistos:
            vistos.add(v.lower())
            tentativas.append(v)
    alvo_bairro = _sem_acento(p.get("bairro") or "").casefold()
    for v in tentativas:
        try:
            r = requests.get("https://viacep.com.br/ws/%s/%s/%s/json/"
                             % (uf, quote(cidade), quote(v)), timeout=12)
            lst = r.json() if r.status_code == 200 else []
        except Exception:
            lst = []
        if not isinstance(lst, list) or not lst:
            continue
        if alvo_bairro:
            for d in lst:
                if _sem_acento(d.get("bairro") or "").casefold() == alvo_bairro:
                    return d
        return lst[0]
    return None


def _cep_pela_web(endereco, p, cache):
    """Ultima instancia automatica: o Claude da VPS (assinatura, nunca API)
    pesquisa o CEP na web, como um humano faria. No maximo 1 tentativa/dia
    por card (marca cep_web_em no estado)."""
    import json as _json
    import os
    import subprocess
    from datetime import date
    hoje = date.today().isoformat()
    if cache.get("cep_web_em") == hoje:
        return None
    cache["cep_web_em"] = hoje
    try:
        from claude_vps import CLAUDE_BIN
    except Exception:
        CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # assinatura, nunca API
    dica = ", ".join(x for x in (p.get("cidade"), p.get("uf")) if x)
    prompt = (
        u"Preciso do CEP deste endereço no Brasil: \"%s\"%s.\n"
        u"Pesquise na web (sites de CEP: qualeocep, cepsbrasil, cepbrasil, "
        u"buscacep dos Correios). Atenção: o cliente pode ter trocado o tipo "
        u"do logradouro (Travessa x Rua) ou a grafia (nome com e sem acento x "
        u"Kaloré); aceite a melhor correspondência no MESMO bairro/cidade. "
        u"Se só existir CEP geral da cidade ou a faixa do bairro, use o mais "
        u"específico que achar.\n"
        u"Responda APENAS o JSON: {\"cep\": \"XXXXX-XXX\" ou null, "
        u"\"bairro\": \"...\", \"cidade\": \"...\", \"uf\": \"XX\", "
        u"\"fonte\": \"url\"}"
        % (endereco, (u" (provavelmente em %s)" % dica) if dica else u""))
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "-p", "--model", "sonnet",
             "--allowedTools", "WebSearch,WebFetch"],
            input=prompt.encode("utf-8"), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, timeout=300)
        if r.returncode != 0:
            print("    (cep web cli rc=%s: %s)" % (
                r.returncode, r.stderr.decode("utf-8", "replace")[:120]))
            return None
        m = re.search(r"\{.*\}", r.stdout.decode("utf-8", "replace"), re.S)
        d = _json.loads(m.group(0)) if m else {}
    except Exception as e:
        print("    (cep pela web falhou: %s)" % str(e)[:120])
        return None
    cep = d.get("cep") or ""
    m = _RE_CEP.search(str(cep))
    if not m:
        return None
    return {"cep": "%s-%s" % (m.group(1), m.group(2)),
            "bairro": d.get("bairro"), "localidade": d.get("cidade"),
            "uf": d.get("uf"), "fonte": d.get("fonte") or "busca na web"}


def completar_endereco(qualificacao, telefone=None, cache=None):
    """Completa, EM LUGAR, os campos bairro/cidade_estado/cep da qualificacao
    a partir do endereco escrito. Escada da Advogada (14/10/2026): CEP no texto ->
    ViaCEP por rua -> busca na web; cliente so e incomodado se nada achar
    (ai o portao devolve o comprovante ao checklist).

    cache = o proprio estado do card (dict): guarda cep_achado para reaplicar
    quando a leitura reescrever a qualificacao, e cep_web_em (1 busca/dia).
    Devolve (mudou, observacao_para_nota_ou_None)."""
    q = qualificacao if isinstance(qualificacao, dict) else {}
    cache = cache if isinstance(cache, dict) else {}
    endereco = " ".join((q.get("endereco") or "").split())
    if q.get("cep") and q.get("bairro") and q.get("cidade_estado"):
        return False, None

    achado = cache.get("cep_achado")
    if achado:  # ja resolvido antes: so reaplica
        mudou = False
        for campo in ("cep", "bairro", "cidade_estado"):
            if achado.get(campo) and not q.get(campo):
                q[campo] = achado[campo]
                mudou = True
        return mudou, None
    if not endereco:
        return False, None

    p = _parse_endereco(endereco, telefone)
    d, fonte = None, None
    m = _RE_CEP.search(endereco) or _RE_CEP.search(str(q.get("cep") or ""))
    if m:
        d = _viacep_cep(m.group(1) + m.group(2)) or {}
        d["cep"] = "%s-%s" % (m.group(1), m.group(2))
        fonte = u"CEP escrito pelo cliente" + (u" (ViaCEP confirmou bairro/cidade)"
                                               if d.get("localidade") else u"")
    if not d:
        d = _viacep_rua(p)
        fonte = u"ViaCEP (UF+cidade+rua)" if d else None
    if not d:
        d = _cep_pela_web(endereco, p, cache)
        fonte = (u"busca na web: %s" % d.get("fonte")) if d else None
    if not d or not d.get("cep"):
        return False, None

    cidade = d.get("localidade") or p.get("cidade") or ""
    uf = d.get("uf") or p.get("uf") or ""
    novo = {"cep": d["cep"],
            "bairro": d.get("bairro") or p.get("bairro") or "",
            "cidade_estado": ("%s - %s" % (cidade.title(), uf.upper())).strip(" -")}
    mudou = False
    for campo, valor in novo.items():
        if valor and not q.get(campo):
            q[campo] = valor
            mudou = True
    cache["cep_achado"] = novo
    if not mudou:
        return False, None
    obs = (u"ENDEREÇO COMPLETADO PELA AUTOMAÇÃO (regra da Advogada 14/10/2026): "
           u"%s → CEP %s, bairro %s, %s. Fonte: %s. O cliente confere o "
           u"endereço quando assina." % (endereco, novo["cep"],
           novo["bairro"] or u"?", novo["cidade_estado"] or u"?", fonte))
    return True, obs


def _tem_empresa(texto, dados_do_caso=None):
    """Sabemos contra quem entrar? Vem da CTPS, da entrevista ou do que o
    cliente escreveu/falou. Sem isso o contrato nao fecha: ele nomeia a acao
    e a parte contraria."""
    if (dados_do_caso or {}).get("qual_empresa"):
        return True
    t = texto or ""
    return bool(re.search(
        r"\b(?:empresa|transportadora|trabalh(?:ei|ava|o)\s+(?:n[ao]|para|pra))\b"
        r"[^\n.;]{0,60}\b(?:ltda|me\b|eireli|s\.?a\.?|transportes?|log[ií]stica)",
        t, re.I))


def avaliar(docs_recebidos, texto_conversa="", dados_do_caso=None,
            cnh_ilegivel=None):
    """Decide o que fazer com um card na fase de documentos.

    docs_recebidos ... {"cnh": path, ...} com o que ja chegou e foi classificado
    texto_conversa ... tudo que o CLIENTE escreveu na conversa
    dados_do_caso .... o que ja se sabe (entrevista, transcricao): qual_empresa etc.
    cnh_ilegivel ..... motivo devolvido pela visao, quando a CNH nao deu para ler

    Devolve um dicionario com:
      pronto ........ True = pode rodar o intake e gerar os documentos
      faltando ...... lista de tipos que ainda faltam, para cobrar
      mensagens ..... o que dizer ao cliente AGORA (lista de textos ja prontos)
      observacoes ... o que registrar na nota do card
    """
    docs = dict(docs_recebidos or {})
    dados = dados_do_caso or {}
    faltando, mensagens, obs = [], [], []

    tem_cnh = bool(docs.get("cnh")) and not cnh_ilegivel
    tem_comprovante = bool(docs.get("comprovante_residencia"))
    tem_ctps = bool(docs.get("ctps"))

    # --- CNH: e o unico insubstituivel. Ilegivel = pede de novo, na hora ----
    if cnh_ilegivel:
        mensagens.append(("cnh_ilegivel", MSG_CNH_ILEGIVEL, {"motivo": cnh_ilegivel}))
        obs.append(u"CNH recebida mas ilegível (%s): nova foto pedida ao cliente."
                   % cnh_ilegivel)
        faltando.append("cnh")
    elif not tem_cnh:
        faltando.append("cnh")

    # --- endereco: comprovante OU escrito na conversa (com CEP) -------------
    # Sem CEP o preambulo do contrato nao fecha (caso Francisco, 14/10/2026).
    # O completar_endereco roda ANTES deste portao e acha o CEP sozinho;
    # se nem ele achou, o comprovante volta ao checklist e a regua cobra.
    if not tem_comprovante:
        tem_endereco = _tem_endereco_no_texto(texto_conversa) or dados.get("endereco")
        tem_cep = bool(dados.get("cep")) or bool(_RE_CEP.search(texto_conversa or ""))
        if tem_endereco and tem_cep:
            obs.append(u"Sem o arquivo do comprovante, mas o endereço veio por "
                       u"escrito: contrato segue com ele.")
        elif tem_endereco:
            obs.append(u"Endereço por escrito SEM CEP e a automação não achou "
                       u"o CEP nas bases nem na web: comprovante de endereço "
                       u"volta ao checklist (último recurso).")
            faltando.append("comprovante_residencia")
        else:
            faltando.append("comprovante_residencia")

    # --- CTPS OU o nome da reclamada: NAO trava mais (Advogada, 15/10/2026) -----
    # Pela regra de 14/10, sem o nome da empresa o contrato sai "em desfavor do
    # seu empregador" (INTAKE/main.py, CAMPOS_COM_PADRAO). Antes o card ficava
    # preso e o lembrete pedia a CTPS sem fim (o #481 levou 76 lembretes).
    if not tem_ctps:
        if _tem_empresa(texto_conversa, dados):
            obs.append(u"Sem CTPS, mas a empresa reclamada já é conhecida: "
                       u"contrato segue.")
        else:
            obs.append(u"Sem CTPS e sem o nome da empresa: o contrato segue "
                       u"\"em desfavor do seu empregador\" (regra de 14/10).")

    pronto = not faltando
    if pronto:
        mensagens.append(("completo", MSG_COMPLETO, {}))

    return {"pronto": pronto, "faltando": faltando,
            "mensagens": mensagens, "observacoes": obs}


def texto_do_que_falta(faltando):
    """'a foto da sua CNH e a sua carteira de trabalho digital'"""
    itens = [FALTA.get(t, t) for t in faltando]
    if not itens:
        return u""
    if len(itens) == 1:
        return itens[0]
    return u", ".join(itens[:-1]) + u" e " + itens[-1]


def texto_do_que_chegou(recebidos):
    itens = [ROTULO.get(t, t) for t in recebidos]
    if not itens:
        return u""
    frase = (itens[0] if len(itens) == 1
             else u", ".join(itens[:-1]) + u" e " + itens[-1])
    # so a primeira letra sobe: `capitalize()` rebaixaria "CNH" para "cnh"
    return frase[:1].upper() + frase[1:]
