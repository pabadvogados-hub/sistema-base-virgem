# -*- coding: utf-8 -*-
"""
dados_do_cliente.py  -  a QUALIFICACAO do cliente sai dos DOCUMENTOS dele.

REGRA DA ADVOGADA (24/09/2026, depois do contrato do Heitor Valença ter sido
assinado com os {{campos}} do modelo a mostra):

  "as informacoes para preenchimento do contrato sao SEMPRE extraidas dos
   documentos enviados pelo autor"

Ou seja: quem manda na qualificacao civil e o DOCUMENTO, nao a transcricao da
ligacao. A ligacao de fechamento e conversa comercial; ninguem dita CPF nela.
Foi exatamente por isso que o contrato CT. 100/2026 saiu vazio.

Cada campo tem um dono:

  CNH ............ nome completo, CPF, RG + orgao, nascimento, sexo, nacionalidade
  CTPS ........... PIS, numero/serie e os VINCULOS (empresa, CNPJ, admissao, saida)
  COMPROVANTE .... SO O ENDERECO. O titular e IGNORADO de proposito: a conta
                   quase nunca esta no nome do motorista (regra da Advogada).
  TRANSCRICAO .... estado civil, e-mail e o tipo da acao (o resto nao)
  CARD/CRM ....... telefone

Leitura por VISAO do Claude da assinatura (mesmo caminho do claude_vps), NUNCA
pela API e NUNCA por OCR. O OCR do Tesseract foi quem devolveu lixo na CNH do
Heitor: veio o nome da MAE e nenhum CPF.

Este modulo NAO fala com o cliente e NAO escreve em lugar nenhum. So le
documento e devolve dicionario. Quem decide o que fazer com a falta e o vigia.
"""

import io
import json
import os
import re
import subprocess
import tempfile

CLAUDE_BIN = os.getenv("CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))
TIMEOUT_VISAO = int(os.getenv("ESC_TIMEOUT_VISAO", "300"))

_EXTS = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "application/pdf": ".pdf",
}

# ---------------------------------------------------------------- visao ----

_PROMPT_CNH = u"""Você está lendo uma CNH (Carteira Nacional de Habilitação brasileira).
Pode ser a CNH física (frente, verso ou aberta), a CNH digital do app ou uma foto de tela.

Extraia APENAS o que estiver legível e responda SÓ este JSON:
{"legivel": true/false,
 "nome_completo": "",
 "cpf": "",
 "rg": "",
 "orgao_rg": "",
 "uf_rg": "",
 "data_nascimento": "DD/MM/AAAA",
 "sexo": "M ou F",
 "motivo": ""}

Regras que não podem ser quebradas:
- "nome_completo" é o nome do TITULAR. NUNCA use o nome da FILIAÇÃO (pai/mãe),
  que na CNH aparece logo abaixo e é o erro mais comum.
- Se houver a faixa de leitura automática no rodapé (as linhas com <<<), ela
  confirma o nome do titular. Use-a para conferir.
- Campo que você não conseguir ler com certeza: devolva "" (string vazia).
  NUNCA invente, NUNCA chute, NUNCA escreva observações dentro do valor do campo.
- "legivel" é false quando a foto está tão ruim que nome OU CPF não saem.
  Nesse caso explique em "motivo" em uma frase curta e em português
  (ex.: "foto escura", "documento cortado", "reflexo sobre o número").
"""

_PROMPT_CTPS = u"""Você está lendo uma CTPS (Carteira de Trabalho) brasileira: pode ser
a Carteira de Trabalho Digital (PDF do app ou print) ou a foto da carteira de papel.

Extraia APENAS o que estiver legível e responda SÓ este JSON:
{"legivel": true/false,
 "nome_completo": "",
 "cpf": "",
 "data_nascimento": "DD/MM/AAAA",
 "pis": "",
 "ctps_numero": "",
 "ctps_serie": "",
 "vinculos": [{"empresa": "", "cnpj": "", "cargo": "",
               "admissao": "DD/MM/AAAA", "saida": "DD/MM/AAAA", "salario": ""}],
 "motivo": ""}

Regras que não podem ser quebradas:
- "vinculos" traz os contratos de trabalho que aparecerem, do mais recente para
  o mais antigo. Vínculo em aberto: "saida" fica "".
- Campo que você não conseguir ler: devolva "" (string vazia). NUNCA invente e
  NUNCA escreva observações dentro do valor do campo.
"""

_PROMPT_ENDERECO = u"""Você está lendo um comprovante de endereço (conta de luz, água, gás,
telefone, internet, fatura ou correspondência).

Extraia APENAS O ENDEREÇO e responda SÓ este JSON:
{"legivel": true/false,
 "logradouro": "",
 "numero": "",
 "complemento": "",
 "bairro": "",
 "cidade": "",
 "uf": "",
 "cep": "",
 "motivo": ""}

Regras que não podem ser quebradas:
- NÃO extraia o nome do titular da conta. Ele é irrelevante aqui e quase nunca
  é o nome do nosso cliente. Ignore-o por completo.
- "logradouro" vem COM o tipo por extenso e corrigido ("Rua Ianasso",
  "Avenida Brasil"). Se a conta abreviar ou quebrar a palavra, escreva a forma
  correta.
- Campo que você não conseguir ler: devolva "" (string vazia). NUNCA invente e
  NUNCA escreva observações dentro do valor do campo (nada de "grafia incerta").
- "cep" só com os 8 dígitos do endereço DO IMÓVEL. CEP da empresa que emitiu a
  conta não serve: nesse caso devolva "".
"""

_PROMPTS = {"cnh": _PROMPT_CNH, "ctps": _PROMPT_CTPS,
            "comprovante_residencia": _PROMPT_ENDERECO}


# Prompt UNICO: diz o tipo, diz se da para ler e ja extrai os campos daquele
# tipo, tudo numa passada so. Antes eram DUAS chamadas de visao por arquivo (o
# vigia classificava e a varredura classificava de novo) e nenhuma das duas
# olhava se o documento estava legivel: a CNH ilegivel do Heitor passou como
# "cnh" e o intake seguiu com ela. Uma chamada so tambem e a metade do token.
_PROMPT_LEITURA = u"""Você lê documentos que clientes de um escritório trabalhista mandam
pelo WhatsApp. Diga O QUE É, se DÁ PARA LER e extraia os dados. Responda SÓ este JSON:

{"tipo": "cnh | ctps | comprovante_residencia | outro",
 "legivel": true/false,
 "motivo": "",
 "campos": {}}

Como decidir o "tipo":
- "cnh": Carteira Nacional de Habilitação (frente, verso, aberta ou a digital do app).
- "ctps": Carteira de Trabalho, digital (PDF ou print do app, com as abas de contratos)
  ou de papel (capa, qualificação civil ou página de contrato). Na dúvida entre
  "ctps" e "outro", procure registro de contrato de trabalho, PIS ou número e
  série de carteira: se houver, é "ctps".
- "comprovante_residencia": conta de luz, água, gás, telefone, internet, fatura ou
  correspondência que mostre um endereço. VALE mesmo que o titular não seja o
  cliente: aqui só interessa o endereço.
- "outro": qualquer outra coisa (RG avulso, holerite, TRCT, extrato, selfie,
  print de conversa, foto de caminhão, foto de estrada etc.).

"legivel" é false quando a foto está ruim a ponto de os dados principais do
documento não saírem (escura, tremida, cortada, com reflexo, muito distante,
dedo na frente). Aí explique em "motivo", em UMA frase curta, em português e
falando com o cliente ("a foto ficou escura", "o documento está cortado na
lateral", "tem reflexo em cima do número"). Para "outro", "legivel" é true.

O que pôr em "campos", conforme o tipo:
- cnh: {"nome_completo","cpf","rg","orgao_rg","uf_rg","data_nascimento","sexo"}
  ATENÇÃO: "nome_completo" é o do TITULAR, NUNCA o da FILIAÇÃO (pai/mãe), que
  aparece logo abaixo e é o erro mais comum. A faixa de leitura automática do
  rodapé (linhas com <<<) confirma o nome do titular.
- ctps: {"nome_completo","cpf","data_nascimento","pis","ctps_numero","ctps_serie",
  "vinculos":[{"empresa","cnpj","cargo","admissao","saida","salario"}]}
  Os vínculos vêm do mais recente para o mais antigo; vínculo em aberto tem "saida" vazia.
- comprovante_residencia: {"logradouro","numero","complemento","bairro","cidade","uf","cep"}
  NÃO extraia o titular da conta. "logradouro" vem com o tipo por extenso e
  corrigido ("Rua Ianasso"). "cep" só o do IMÓVEL, nunca o da empresa emissora.
- outro: {} vazio.

Regra que não pode ser quebrada: campo que você não conseguir ler com certeza
devolve "" (string vazia). NUNCA invente, NUNCA chute e NUNCA escreva observação
dentro do valor do campo (nada de "grafia incerta", "a confirmar", "aproximado").
"""


def ler_documento_completo(path):
    """UMA chamada de visao: o que é, se dá para ler e os campos.

    Devolve {"tipo","legivel","motivo","campos"} ou None quando a visao nao
    rodou. None e falha NOSSA (CLI fora do ar, credencial vencida, formato que
    nao da para abrir); {"legivel": false} e foto ruim DO CLIENTE. Os dois
    exigem tratamentos diferentes: o primeiro retoma na proxima rodada, o
    segundo pede foto nova ao cliente.
    """
    if not os.path.exists(path) or not _ctype_do_arquivo(path):
        return None
    lido = _chamar_visao(_PROMPT_LEITURA, path)
    if lido is None:
        return None
    tipo = (lido.get("tipo") or "outro").strip().lower()
    if tipo not in ("cnh", "ctps", "comprovante_residencia", "outro"):
        tipo = "outro"
    return {"tipo": tipo,
            "legivel": bool(lido.get("legivel", True)),
            "motivo": _limpo(lido.get("motivo")),
            "campos": lido.get("campos") or {}}


def _ctype_do_arquivo(path):
    ext = os.path.splitext(path)[1].lower()
    for ctype, e in _EXTS.items():
        if e == ext:
            return ctype
    return None


def ler_documento(path, tipo):
    """Le UM documento por visao, quando o tipo JA e conhecido.

    Devolve None quando a visao nao rodou (CLI fora do ar, credencial vencida,
    formato nao suportado). None e DIFERENTE de {"legivel": false}: o primeiro
    e falha nossa, o segundo e foto ruim do cliente. Quem chama precisa tratar
    os dois casos de formas diferentes.
    """
    prompt_base = _PROMPTS.get(tipo)
    if not prompt_base or not os.path.exists(path):
        return None
    if not _ctype_do_arquivo(path):
        return None
    return _chamar_visao(prompt_base, path)


def _chamar_visao(prompt_base, path):
    """O unico lugar que fala com o Claude da assinatura. Nunca a API."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)      # assinatura, nunca API
    prompt = (prompt_base + u"\n\nLeia o arquivo " + path +
              u" (use a ferramenta Read) e responda APENAS o JSON pedido.")
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "-p", "--model", "sonnet", "--allowedTools", "Read"],
            input=prompt.encode("utf-8"), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, cwd=tempfile.gettempdir(),
            timeout=TIMEOUT_VISAO)
        if r.returncode != 0:
            print("    (visao rc=%s: %s)" % (
                r.returncode, r.stderr.decode("utf-8", "replace")[:120]))
            return None
        saida = r.stdout.decode("utf-8", "replace")
        m = re.search(r"\{.*\}", saida, re.S)
        if not m:
            return None
        return json.loads(m.group(0))
    except Exception as e:
        print("    (visão falhou em %s: %s)" % (os.path.basename(path), str(e)[:120]))
        return None


# --------------------------------------------------------------- limpeza ----

_LIXO = re.compile(r"\(.*?(incert|ilegiv|ilegív|confirmar|verificar|nao informad"
                   r"|não informad|aproximad|talvez|duvid).*?\)", re.I)


def _limpo(v):
    """Tira do VALOR qualquer ressalva que a IA tenha escrito dentro dele.

    No contrato do Heitor o endereco foi para o papel assinado como
    'Rua Yanasso (grafia incerta por qualidade do documento), n 1385'. Campo de
    contrato nao e lugar de recado: ou o dado esta limpo, ou esta vazio.
    """
    if not v:
        return ""
    s = re.sub(r"\s+", " ", str(v)).strip()
    s = _LIXO.sub("", s)
    # a ressalva saiu do meio da frase e deixou pontuacao orfa para tras
    # ("Rua Yanasso , n 1385"): junta a virgula de volta na palavra e
    # derruba a virgula dupla que sobra quando o trecho inteiro era a ressalva.
    s = re.sub(r"\s+([,;.])", r"\1", s)
    s = re.sub(r"([,;])\s*(?=[,;])", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,;.-")
    baixo = s.lower()
    # frase que denuncia que a IA nao sabia: derruba o valor inteiro
    for veneno in ("nao informado", "não informado", "a confirmar", "ilegivel",
                   "ilegível", "desconhecido", "nao consta", "não consta"):
        if veneno in baixo:
            return ""
    # marcador de vazio: so vale quando e o valor INTEIRO. Comparar por
    # "contem" aqui derrubava CPF, CEP e RG, que tem hifen no meio
    # ("079.169.114-42" casava com o marcador "-" e virava string vazia).
    if baixo in ("n/a", "na", "null", "none", "nulo", "-", "--", "?", "x"):
        return ""
    return s


def _so_digitos(v):
    return re.sub(r"\D", "", str(v or ""))


def _cpf_ok(cpf):
    """Valida CPF pelos digitos verificadores. Numero inventado nao passa."""
    c = _so_digitos(cpf)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for corte in (9, 10):
        soma = sum(int(c[i]) * ((corte + 1) - i) for i in range(corte))
        d = (soma * 10) % 11
        if d == 10:
            d = 0
        if d != int(c[corte]):
            return False
    return True


def _fmt_cpf(cpf):
    c = _so_digitos(cpf)
    return "%s.%s.%s-%s" % (c[:3], c[3:6], c[6:9], c[9:]) if len(c) == 11 else ""


def _fmt_cep(cep):
    c = _so_digitos(cep)
    return "%s-%s" % (c[:5], c[5:]) if len(c) == 8 else ""


# ------------------------------------------------------------------ CEP ----

def buscar_cep(logradouro, cidade, uf):
    """CEP que o comprovante nao trouxe (a Advogada autorizou procurar, 24/09).

    Usa o ViaCEP, que e publico e nao pede chave. So aceita a resposta quando
    ela e UNICA: varias ruas parecidas = nao da para escolher sozinha, melhor
    voltar vazio e avisar do que carimbar um CEP errado no contrato.
    """
    rua = re.sub(r"^(rua|r\.|av\.?|avenida|alameda|travessa|estrada|rodovia)\s+",
                 "", (logradouro or "").strip(), flags=re.I).strip()
    rua = rua.split(",")[0].strip()          # tira o numero que veio junto
    if not (rua and cidade and uf) or len(rua) < 4:
        return ""

    # A visao as vezes devolve a mesma rua com grafias diferentes entre uma
    # leitura e outra ("Ianasso" x "Ia Nasso"): o ViaCEP acha numa e nao acha
    # na outra. Entao tentamos as duas formas antes de desistir.
    tentativas = [rua]
    sem_espaco = rua.replace(" ", "")
    if sem_espaco != rua:
        tentativas.append(sem_espaco)
    if " " not in rua and len(rua) > 5:
        for i in range(2, len(rua) - 1):     # "Ianasso" -> "Ia nasso"
            tentativas.append(rua[:i] + " " + rua[i:])
            break

    try:
        import requests
        from urllib.parse import quote
        for termo in tentativas:
            url = "https://viacep.com.br/ws/%s/%s/%s/json/" % (
                quote(uf.strip()), quote(cidade.strip()), quote(termo))
            r = requests.get(url, timeout=20)
            if r.status_code != 200:
                continue
            achados = r.json()
            if not isinstance(achados, list) or not achados:
                continue
            ceps = {_fmt_cep(a.get("cep", "")) for a in achados}
            ceps.discard("")
            # um resultado so, ou varios que apontam para o MESMO CEP.
            # Ruas homonimas com CEPs diferentes: melhor voltar vazio e o
            # time resolver do que carimbar CEP errado num contrato.
            if len(ceps) == 1:
                return ceps.pop()
    except Exception as e:
        print("    (busca de CEP falhou: %s)" % str(e)[:100])
    return ""


def endereco_oficial(logradouro, cidade, uf):
    """Confere o logradouro nos Correios e devolve a grafia OFICIAL.

    Serve de corretor: a visao le a mesma rua de jeitos diferentes conforme a
    foto ("Ianasso" numa leitura, "Ya Nasso" noutra, e so a segunda existe).
    Quando o ViaCEP reconhece a rua, o contrato sai com o nome certo e com o
    CEP; quando nao reconhece, nada e inventado e o campo continua como veio.

    Devolve {"cep","logradouro","bairro"} ou {} quando nao deu para confirmar.
    """
    rua = re.sub(r"^(rua|r\.|av\.?|avenida|alameda|travessa|estrada|rodovia)\s+",
                 "", (logradouro or "").strip(), flags=re.I).strip()
    rua = rua.split(",")[0].strip()
    if not (rua and cidade and uf) or len(rua) < 4:
        return {}
    tentativas = [rua]
    if " " in rua:
        tentativas.append(rua.replace(" ", ""))
    try:
        import requests
        from urllib.parse import quote
        for termo in tentativas:
            r = requests.get("https://viacep.com.br/ws/%s/%s/%s/json/" % (
                quote(uf.strip()), quote(cidade.strip()), quote(termo)), timeout=20)
            if r.status_code != 200:
                continue
            achados = r.json()
            if not isinstance(achados, list) or not achados:
                continue
            ceps = {_fmt_cep(a.get("cep", "")) for a in achados}
            ceps.discard("")
            if len(ceps) == 1:
                a = achados[0]
                return {"cep": ceps.pop(),
                        "logradouro": _limpo(a.get("logradouro")),
                        "bairro": _limpo(a.get("bairro"))}
    except Exception as e:
        print("    (conferência de endereço falhou: %s)" % str(e)[:100])
    return {}



def endereco_por_cep(cep):
    """CEP -> {"logradouro","bairro","cidade_estado"} pelos Correios.

    Caminho inverso do `endereco_oficial`: com o CEP em maos, bairro e cidade
    nao precisam ser pedidos a ninguem. Cinco contratos a refazer estavam
    travados so por bairro, tendo o CEP impresso no documento assinado.
    """
    c = _so_digitos(cep)
    if len(c) != 8:
        return {}
    try:
        import requests
        r = requests.get("https://viacep.com.br/ws/%s/json/" % c, timeout=20)
        if r.status_code != 200:
            return {}
        d = r.json()
        if d.get("erro"):
            return {}
        cidade = _limpo(d.get("localidade"))
        uf = _limpo(d.get("uf"))
        return {"logradouro": _limpo(d.get("logradouro")),
                "bairro": _limpo(d.get("bairro")),
                "cidade_estado": ("%s/%s" % (cidade, uf)) if cidade and uf else cidade}
    except Exception as e:
        print("    (busca por CEP falhou: %s)" % str(e)[:100])
        return {}


def _razao_social(bruto):
    """So a razao social, sem a explicacao que a equipe escreve junto.

    A ficha traz coisas como "DISTRIBUIDORA SORPAN LTDA (razao social no TRCT).
    A ligacao dizia SORPAN ALIMENTOS | CNPJ 05.745.995/0001-33". No contrato
    entra a razao social e nada mais.
    """
    texto = re.split(r"[(|\n]|\. ", str(bruto or ""), 1)[0]
    texto = re.sub(r"\s+", " ", texto).strip(" ,;.-")
    return _limpo(texto)


def _so_logradouro(bruto):
    """Logradouro, numero e complemento. Sem cidade, UF e CEP.

    O modelo tem campo proprio para cidade, bairro e CEP, e repetir dentro do
    endereco deixa o contrato com a mesma informacao duas vezes na mesma frase.
    """
    texto = re.sub(r"\s+", " ", str(bruto or "")).strip()
    # corta a partir de CEP, " - Cidade/UF", "Bairro:" ou "Cidade:"
    texto = re.split(r"\s*[-,]?\s*CEP\b|\s*[-,]\s*[A-Z\u00c0-\u00dc][\w\u00c0-\u00ff. ]{2,30}/[A-Z]{2}\b"
                     r"|\s*,?\s*Bairro\s*:|\s*,?\s*Cidade\s*:",
                     texto, 1, flags=re.I)[0]
    texto = re.sub(r"\b\d{5}-?\d{3}\b", "", texto)
    return _limpo(texto)


def _vinculo_da_reclamada(vinculos):
    """(vinculo escolhido, aviso). Prefere o que ENCERROU, nao o emprego atual.

    Quem reclama normalmente reclama do vinculo que acabou. Por o empregador
    ATUAL no polo passivo e erro grave: alem de mudar a acao, pode custar o
    emprego do cliente. Havendo vinculo em aberto tambem, devolve aviso, porque
    reclamar de quem ainda emprega existe (rescisao indireta) e a escolha e da
    advogada.
    """
    validos = [v for v in (vinculos or []) if _limpo(v.get("empresa"))]
    if not validos:
        return None, ""
    encerrados = [v for v in validos if _limpo(v.get("saida"))]
    abertos = [v for v in validos if not _limpo(v.get("saida"))]
    if encerrados:
        escolhido = encerrados[0]
        aviso = ""
        if abertos:
            aviso = (u"a CTPS tem vínculo EM ABERTO com %s; usei o vínculo "
                     u"encerrado (%s, saída %s) como reclamada" % (
                         _limpo(abertos[0].get("empresa")),
                         _limpo(escolhido.get("empresa")),
                         _limpo(escolhido.get("saida"))))
        return escolhido, aviso
    return validos[0], (u"nenhum vínculo da CTPS tem data de saída; usei %s, que "
                        u"está em aberto" % _limpo(validos[0].get("empresa")))

# ------------------------------------------------------- monta a ficha ----

def _nacionalidade(sexo):
    return u"brasileira" if (sexo or "").upper().startswith("F") else u"brasileiro"


def qualificacao(docs, texto_conversa="", entrevista=None, card=None):
    """Monta a qualificacao do cliente com a precedencia da regra da Advogada.

    docs .............. {tipo: path} para ler agora, OU {tipo: {"campos": {...},
                        "legivel": bool, "motivo": str}} com o que a leitura da
                        conversa JA leu. A segunda forma e a normal: quem le e
                        o vigia de leitura, uma vez so, e aqui nao se gasta
                        visao de novo.
    texto_conversa .... o que o cliente ESCREVEU no WhatsApp (substitui documento
                        que ele nao mandou - regra da Advogada 24/09)
    entrevista ........ campos da aba Entrevista do card (dict ou None)
    card .............. {"nome":..., "telefone":..., "email":...} do CRM

    Devolve (dados, leitura), onde `leitura` conta o que aconteceu com cada
    documento para o vigia decidir se pede foto nova.
    """
    entrevista = entrevista or {}
    card = card or {}
    dados = {}
    leitura = {"ilegiveis": [], "sem_visao": [], "lidos": [], "origem": {},
               "avisos": []}

    def por(campo, valor, fonte):
        valor = _limpo(valor)
        if campo == "endereco":
            valor = _so_logradouro(valor)
        if valor and not dados.get(campo):
            dados[campo] = valor
            leitura["origem"][campo] = fonte

    # ---- 1. DOCUMENTOS (fonte soberana) ----------------------------------
    for tipo in ("cnh", "ctps", "comprovante_residencia"):
        entrada = (docs or {}).get(tipo)
        if not entrada:
            continue
        if isinstance(entrada, dict):
            # ja lido pelo vigia de leitura: aproveita, nao gasta visao de novo
            lido = dict(entrada.get("campos") or {})
            lido["legivel"] = entrada.get("legivel", True)
            lido["motivo"] = entrada.get("motivo", "")
        else:
            lido = ler_documento(entrada, tipo)
        if lido is None:
            leitura["sem_visao"].append(tipo)
            continue
        if not lido.get("legivel", True):
            leitura["ilegiveis"].append((tipo, _limpo(lido.get("motivo")) or u"sem detalhe"))
            # documento ilegivel ainda pode ter trazido 1 ou 2 campos bons
        else:
            leitura["lidos"].append(tipo)

        if tipo == "cnh":
            por("nome", lido.get("nome_completo"), "CNH")
            cpf = lido.get("cpf")
            if _cpf_ok(cpf):
                por("cpf", _fmt_cpf(cpf), "CNH")
            elif _limpo(cpf):
                leitura["ilegiveis"].append(("cnh", u"CPF lido não confere no dígito"))
            rg = _limpo(lido.get("rg"))
            orgao = _limpo(lido.get("orgao_rg"))
            uf = _limpo(lido.get("uf_rg"))
            if rg:
                por("rg", (rg + ((" " + orgao) if orgao else "") +
                           (("/" + uf) if (orgao and uf) else "")).strip(), "CNH")
            por("data_nascimento", lido.get("data_nascimento"), "CNH")
            por("sexo", lido.get("sexo"), "CNH")
            por("nacionalidade", _nacionalidade(lido.get("sexo")), "CNH")

        elif tipo == "ctps":
            por("nome", lido.get("nome_completo"), "CTPS")
            if not dados.get("cpf") and _cpf_ok(lido.get("cpf")):
                por("cpf", _fmt_cpf(lido.get("cpf")), "CTPS")
            por("data_nascimento", lido.get("data_nascimento"), "CTPS")
            por("pis", lido.get("pis"), "CTPS")
            por("ctps_numero", lido.get("ctps_numero"), "CTPS")
            por("ctps_serie", lido.get("ctps_serie"), "CTPS")
            vinc = [v for v in (lido.get("vinculos") or []) if _limpo(v.get("empresa"))]
            if vinc:
                dados["vinculos"] = vinc
                escolhido, aviso = _vinculo_da_reclamada(vinc)
                if aviso:
                    leitura.setdefault("avisos", []).append(aviso)
                if escolhido:
                    por("qual_empresa", escolhido.get("empresa"), "CTPS")
                    por("cnpj_empresa", escolhido.get("cnpj"), "CTPS")
                    por("profissao", escolhido.get("cargo"), "CTPS")
                    por("admissao", escolhido.get("admissao"), "CTPS")
                    por("saida", escolhido.get("saida"), "CTPS")

        else:  # comprovante: SO o endereco, o titular e ignorado
            log = _limpo(lido.get("logradouro"))
            num = _limpo(lido.get("numero"))
            comp = _limpo(lido.get("complemento"))
            if log:
                por("endereco", ", ".join(p for p in (
                    log, ("nº " + num) if num else "", comp) if p), "comprovante")
            por("bairro", lido.get("bairro"), "comprovante")
            cid, uf = _limpo(lido.get("cidade")), _limpo(lido.get("uf"))
            if cid:
                por("cidade_estado", (cid + "/" + uf) if uf else cid, "comprovante")
            if _so_digitos(lido.get("cep")) and len(_so_digitos(lido.get("cep"))) == 8:
                por("cep", _fmt_cep(lido.get("cep")), "comprovante")

    # ---- 2. ENTREVISTA do card -------------------------------------------
    mapa_entrevista = {
        "estado_civil": ("estado_civil", "estadocivil"),
        "email": ("email", "e_mail", "email_cliente"),
        "endereco": ("endereco_cliente", "endereco"),
        "cep": ("cep", "cep_cliente"),
        "bairro": ("bairro", "bairro_cliente"),
        "cidade_estado": ("cidade_estado", "cidade", "cidade_cliente"),
        "cpf": ("cpf", "cpf_cliente"),
        "nome": ("nome_cliente", "nome_completo"),
        "qual_empresa": ("razao_social", "empresa", "qual_empresa", "reclamada",
                         "nome_empresa"),
        "cnpj_empresa": ("cnpj", "cnpj_empresa"),
    }
    for campo, chaves in mapa_entrevista.items():
        for k in chaves:
            if entrevista.get(k):
                valor = entrevista[k]
                if campo == "qual_empresa":
                    valor = _razao_social(valor)
                por(campo, valor, "entrevista")
                break

    # ---- 3. TEXTO da conversa (substitui documento nao enviado) ----------
    # Regra da Advogada 24/09: "se nao for enviado o documento, pode ser substituido
    # pelo texto escrito com os dados do cliente".
    if texto_conversa:
        achados = dados_no_texto(texto_conversa)
        for campo, valor in achados.items():
            por(campo, valor, "texto do cliente")

    # ---- 4. CARD do CRM ---------------------------------------------------
    por("telefone", card.get("telefone"), "card")
    por("email", card.get("email"), "card")
    por("nome", card.get("nome"), "card")     # ultimo recurso, nunca o 1o

    # ---- 5. remendos finais ----------------------------------------------
    if dados.get("endereco") and dados.get("cidade_estado"):
        cidade_uf = dados["cidade_estado"].split("/")
        oficial = endereco_oficial(dados["endereco"], cidade_uf[0],
                                   cidade_uf[1] if len(cidade_uf) > 1 else "")
        if oficial:
            por("cep", oficial.get("cep"), "ViaCEP")
            if not dados.get("bairro"):
                por("bairro", oficial.get("bairro"), "ViaCEP")
            # grafia oficial dos Correios corrige o que a visao leu torto,
            # preservando o numero e o complemento que vieram do comprovante
            rua_oficial = oficial.get("logradouro")
            if rua_oficial:
                resto = dados["endereco"].split(",", 1)
                dados["endereco"] = (rua_oficial + ("," + resto[1] if len(resto) > 1 else ""))
                leitura["origem"]["endereco"] = "comprovante + ViaCEP"
    # com o CEP em maos, bairro/cidade/logradouro saem pelos Correios
    if dados.get("cep") and not (dados.get("bairro") and dados.get("cidade_estado")):
        pelo_cep = endereco_por_cep(dados["cep"])
        for campo in ("bairro", "cidade_estado"):
            if pelo_cep.get(campo):
                por(campo, pelo_cep[campo], "ViaCEP (pelo CEP)")
        # Cidade sem divisao por bairros (regra da Advogada, 01/10/2026): os Correios
        # responderam o CEP, confirmaram a cidade e devolveram o bairro VAZIO.
        # Isso prova que o bairro NAO EXISTE ali, nao que faltou procurar. Caso
        # do Jhonatan Cordeiro, em Eldorado/MS, que tem CEP unico para a cidade.
        if pelo_cep.get("cidade_estado") and not pelo_cep.get("bairro") \
                and not dados.get("bairro"):
            dados["_bairro_nao_existe"] = True
            leitura.setdefault("avisos", []).append(
                u"%s nao tem bairro registrado nos Correios (CEP unico da cidade); "
                u"o contrato sai sem o campo" % pelo_cep["cidade_estado"])
        if not dados.get("endereco") and pelo_cep.get("logradouro"):
            por("endereco", pelo_cep["logradouro"], "ViaCEP (pelo CEP)")

    if not dados.get("profissao"):
        dados["profissao"] = u"Motorista"
        leitura["origem"]["profissao"] = "padrão"
    if not dados.get("nacionalidade"):
        dados["nacionalidade"] = u"brasileiro"
        leitura["origem"]["nacionalidade"] = "padrão"

    return dados, leitura


# ------------------------------------------- dados escritos na conversa ----

_RE_CPF = re.compile(r"\b(\d{3}\.?\d{3}\.?\d{3}-?\d{2})\b")
_RE_CEP = re.compile(r"\b(\d{5})-?(\d{3})\b")
_RE_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_RE_RG = re.compile(r"\bRG\s*:?\s*([\d.\-/]{5,20})", re.I)
_RE_ENDERECO = re.compile(
    r"\b((?:rua|r\.|av\.?|avenida|alameda|travessa|estrada|rodovia)\s+[^\n,;]{3,60}"
    r"(?:,?\s*n?º?\s*\d{1,6})?)", re.I)
_CIVIS = ((u"casado", u"casado(a)"), (u"solteiro", u"solteiro(a)"),
          (u"divorciado", u"divorciado(a)"), (u"viuvo", u"viúvo(a)"),
          (u"viúvo", u"viúvo(a)"), (u"separado", u"separado(a)"),
          (u"uniao estavel", u"convivente em união estável"),
          (u"união estável", u"convivente em união estável"),
          (u"amasiado", u"convivente em união estável"))


def dados_no_texto(texto):
    """O que o CLIENTE escreveu no WhatsApp, quando nao mandou o documento.

    So pega o que tem forma inconfundivel. CPF passa pelo digito verificador:
    numero solto na conversa (valor de acordo, telefone, placa) nao vira CPF.
    """
    t = texto or ""
    out = {}
    for m in _RE_CPF.finditer(t):
        if _cpf_ok(m.group(1)):
            out["cpf"] = _fmt_cpf(m.group(1))
            break
    m = _RE_EMAIL.search(t)
    if m:
        out["email"] = m.group(1).lower()
    m = _RE_RG.search(t)
    if m:
        out["rg"] = m.group(1).strip()
    m = _RE_CEP.search(t)
    if m:
        out["cep"] = m.group(1) + "-" + m.group(2)
    m = _RE_ENDERECO.search(t)
    if m:
        out["endereco"] = re.sub(r"\s+", " ", m.group(1)).strip(" ,;.")
    baixo = t.lower()
    for chave, valor in _CIVIS:
        if re.search(r"\b" + re.escape(chave), baixo):
            out["estado_civil"] = valor
            break
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:
        print(json.dumps(ler_documento(sys.argv[1], sys.argv[2]),
                         ensure_ascii=False, indent=1))
    else:
        print("uso: dados_do_cliente.py <arquivo> <cnh|ctps|comprovante_residencia>")
