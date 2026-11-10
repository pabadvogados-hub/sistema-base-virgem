# -*- coding: utf-8 -*-
"""A Lua no Hermes: o Hermes pensa, o Sistema Escritório Modelo age (fatia 1, Linear PAM-32).

POR QUE
A Lua de hoje enxerga só as últimas 16 mensagens e esquece o que a responsável ensinou: "nunca no
ADVBOX" foi repetido três vezes. No Hermes ela ganha memória, skills e a alma da IVA. As mãos
continuam no Sistema Escritório Modelo: toda ação passa pelo executar() de hoje, com as travas de hoje.

O QUE ESTE MÓDULO GUARDA
Funções puras ou com dependências injetadas, para testar sem banco, sem rede e sem Hermes
(tests/test_lua_hermes.py, T1 a T18). O routers/agentes.py só as chama.

AS TRÊS TRAVAS QUE NASCEM AQUI
1. Rota interna: só com a chave certa, vinda de dentro do servidor, e sempre como a responsável (uid 5),
   nunca com o uid que vier no corpo.
2. Operador técnico vindo do Hermes vira prévia e só roda com o "sim" da responsável. Ele é um terminal
   por procuração, e é por ali que passaria uma ordem escondida num PDF da reclamada.
3. Trava do processual (decisão D7 da gestão): a Lua vê o prazo processual para conferir, mas a
   responsável só vê o fatal interno do escritório (D-2 úteis).

Spec: LUA_HERMES/SPEC_lua_hermes.md, seções 8, 9, 10 e 12-A.
"""
import datetime as dt
import hmac
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

LUA_UID = 5
AGENTE = "Lua (Hermes)"
SESSAO = os.environ.get("LUA_SESSAO", "lua")

INTERRUPTOR = os.environ.get("LUA_CEREBRO_ARQUIVO", "/opt/escritorio/config/lua_cerebro")
MEMORIA = os.environ.get("LUA_MEMORIA_ARQUIVO", "/opt/hermes/home/profiles/lua/memories/MEMORY.md")
HERMES_URL = os.environ.get("LUA_HERMES_URL", "http://127.0.0.1:8644/v1/chat/completions")

# Endereços de dentro do servidor. O MCP da Lua chama a rota direto em 127.0.0.1:8000, sem passar
# pelo nginx; por isso uma chamada com cabeçalho de proxy é tratada como vinda de fora.
_DE_DENTRO = {"127.0.0.1"}
_CABECALHOS_DE_PROXY = ("x-forwarded-for", "x-real-ip", "forwarded")

# Chaves que o Hermes nunca escolhe: o "sim" é da responsável e quem age é sempre ela.
CHAVES_PROIBIDAS = ("confirmado", "forcar", "uid", "usuario_id")

# Ferramentas que, vindas do Hermes, sempre viram prévia. As que já têm prévia no motor de hoje
# (mensagem_cliente, responder_lead, mover_lead, advbox_*, sistema_api) seguem pelo executar().
SEMPRE_PREVIA = {"operador_tecnico", "dar_ciencia", "cancelar_compromisso", "enviar_email", "encaminhar_email"}
# Decisão D26 (08/11/2026): depois de ler texto de fora no turno, nenhuma ação interna roda sem prévia e sim.
ROTULOS_DA_PREVIA = {
    "escrever_peca": "Pedir esta peça à produção", "revisar_peca": "Mandar para a revisão da Valentina",
    "marcar_checklist": "Marcar no checklist da tarefa", "conferir_tarefa": "Responder a conferência da tarefa",
    "editar_card": "Alterar o card", "girar_documentos": "Girar os documentos", "criar_tarefa": "Criar a tarefa",
    "priorizar_tarefa": "Priorizar a tarefa", "reabrir_tarefa": "Reabrir a tarefa", "criar_compromisso": "Criar o compromisso",
    "mudar_compromisso": "Mudar o compromisso", "compromisso_cumprido": "Marcar o compromisso como cumprido",
    "anotar_lead": "Anotar no card do lead", "pegar_lead": "Pegar o lead", "criar_conteudo": "Criar o conteúdo",
    "refazer_conteudo": "Refazer o conteúdo", "mudar_conteudo": "Mudar o conteúdo",
}
# Decisão D11: concluir e remarcar têm volta e seguem diretos, salvo se o turno já leu conteúdo de fora, que pode
# trazer ordem escondida.
PREVIA_SE_LEU_DE_FORA = {"concluir_tarefa", "remarcar_tarefa"} | set(ROTULOS_DA_PREVIA)
# O texto destas é de terceiros: não alimenta a trava do processual, porque um documento não pode escolher as datas.
TEXTO_SEM_VARREDURA = {"ler_documento", "ler_chat", "ver_lead", "ler_autos", "buscar_jurisprudencia", "ler_drive", "buscar_internet", "ler_email", "ler_anexo", "ler_log_vigia", "buscar_por_nome"}
# Estas trazem texto de fora, e o turno passa a pedir prévia. As intimações vêm do tribunal e seguem alimentando a trava.
LEITURA_DE_FORA = TEXTO_SEM_VARREDURA | {"intimacoes_pendentes", "leads_quentes", "sistema_api"}
TETO_PREVIA = 9000
ARQUIVO_SIGILO = os.environ.get("LUA_SIGILO_ARQUIVO", "/opt/escritorio/dados/lua_sigilo_dia.txt")
MARCA_DE_ANEXO = "[Anexo "
AVISO_SEGREDO = "Processo em segredo de justiça: não leve nada destes autos para fora do escritório."      # a frase de lua_autos.py
# Depois que a conversa fecha (o Hermes passou do tempo limite), a prévia não chegaria à responsável: nada disso roda.
_ESCRITA_POR_PREVIA_NA_FERRAMENTA = {"mensagem_cliente", "responder_lead", "mover_lead", "advbox_criar_tarefa", "advbox_andamento"}

# Um turno da Lua por vez: duas abas ou voz e texto juntos embaralhariam o que cada turno fez.
TRAVA_DO_TURNO = threading.Lock()


class HermesIndisponivel(Exception):
    """O Hermes não respondeu (fora do ar, erro 5xx, chave recusada ou limite de uso)."""


# ----------------------------------------------------------------- interruptor (T1, T2)
def motor_da_lua(uid, arquivo_interruptor=INTERRUPTOR):
    """'hermes' só para a Lua com o interruptor ligado. Na dúvida, o motor de hoje."""
    if uid != LUA_UID:
        return "atual"
    try:
        with open(arquivo_interruptor, encoding="utf-8") as f:
            valor = f.read().strip().lower()
    except OSError:
        return "atual"
    return "hermes" if valor == "hermes" else "atual"


# ----------------------------------------------------------------- rota interna (T3, T4, T11)
def chave_ok(recebida, certa, ip, cabecalhos=None):
    """Chave certa, não vazia, vinda de 127.0.0.1 e sem ter passado por proxy."""
    if not recebida or not certa:
        return False
    if ip not in _DE_DENTRO:
        return False
    if cabecalhos and any(cabecalhos.get(h) for h in _CABECALHOS_DE_PROXY):
        return False
    return hmac.compare_digest(str(recebida).encode("utf-8"), str(certa).encode("utf-8"))


def ctx_da_rota(corpo):
    """Contexto de quem age pela rota: sempre a responsável. O corpo da chamada não escolhe o uid."""
    return {"uid": LUA_UID, "agente": AGENTE, "via_hermes": True, "acoes": []}


# ----------------------------------------------------------------- travas das ferramentas (T5 a T7, T18)
def _limpar(entrada):
    return {k: v for k, v in (entrada or {}).items() if k not in CHAVES_PROIBIDAS}


def _previa(nome, entrada):
    if nome == "enviar_email":
        from . import lua_email as LE
        return ("E-mail para enviar\nDe: %s\nPara: %s\nAssunto: %s\nAnexos: %s\n\nTexto:\n%s"
                % (os.environ.get("LUA_EMAIL_REMETENTE", "(caixa da equipe)"), entrada.get("para"), entrada.get("assunto"), LE.linha_dos_anexos(entrada.get("anexos")), entrada.get("texto")))
    if nome == "encaminhar_email":
        from . import lua_email as LE
        return ("E-mail para encaminhar\nDe: %s\nPara: %s\nComentário: %s\nId do original: %s\n\nO original:\n%s"
                % (os.environ.get("LUA_EMAIL_REMETENTE", "(caixa da equipe)"), entrada.get("para"), entrada.get("comentario") or "nenhum", entrada.get("email_id"), LE.resumo_para_encaminhar(entrada.get("email_id"))))
    if nome == "operador_tecnico":
        return "O operador técnico vai fazer no servidor: %s" % str(entrada.get("pedido") or "")[:6000]
    if nome in ROTULOS_DA_PREVIA:
        linhas = ["%s: %s" % (k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str))
                  for k, v in entrada.items()]
        return "%s\n%s" % (ROTULOS_DA_PREVIA[nome], "\n".join(linhas))
    return "%s com %s" % (nome, json.dumps(entrada, ensure_ascii=False, default=str))


def _pede_previa(nome, entrada, leu_de_fora):
    if nome in SEMPRE_PREVIA:
        return True
    if nome == "remarcar_tarefa" and entrada.get("prazo_fatal"):
        return True
    if nome == "concluir_tarefa" and entrada.get("para_todos"):
        return True
    return bool(leu_de_fora) and nome in PREVIA_SE_LEU_DE_FORA


def rota_limpa(rota):
    """Rota sem disfarce. O filtro de rotas proibidas olha o texto cru; '..' e '%61' viram outra rota na hora do envio."""
    return not re.search(r"\.\.|%|\?|#|//|\\", str(rota or ""))


def _folhas(valor):
    if isinstance(valor, str):
        yield valor
    elif isinstance(valor, dict):
        for v in valor.values():
            yield from _folhas(v)
    elif isinstance(valor, (list, tuple)):
        for v in valor:
            yield from _folhas(v)


def previa_fiel(previa, args):
    """A responsável vê tudo o que vai rodar: o que a prévia cortou entra por extenso; o que não couber, não roda."""
    vista = _sem_espacos_extras(previa)
    if all(_sem_espacos_extras(f) in vista for f in _folhas(args)):
        return previa
    dados = json.dumps(args, ensure_ascii=False, default=str)
    if len(previa) + len(dados) > TETO_PREVIA:
        raise ValueError("essa ação é grande demais para caber numa prévia; divida em partes")
    return "%s\n\nDados completos: %s" % (previa, dados)


def executar_pela_rota(nome, entrada, executar, criar_pendente, ctx=None, leu_de_fora=False):
    """Executa uma ferramenta pedida pelo Hermes, como a responsável e com as travas de hoje.

    executar(ctx, nome, entrada) é o executar() oficial do routers/agentes.py.
    criar_pendente(ctx, nome, entrada, previa) grava a prévia e devolve o código dela.
    """
    ctx = ctx or ctx_da_rota({})
    if ctx.get("uid") != LUA_UID:
        raise ValueError("a rota da Lua só age como a responsável (uid %s)" % LUA_UID)
    entrada = _limpar(entrada)
    ctx["leu_de_fora"] = bool(leu_de_fora)      # mover_lead decide a prévia lá dentro, porque resolve a coluna antes
    if _pede_previa(nome, entrada, leu_de_fora):
        previa = _previa(nome, entrada)
        codigo = criar_pendente(ctx, nome, entrada, previa)
        return {"pendente": codigo, "precisa_confirmar": True, "codigo": codigo, "previa": previa,
                "instrucao": "NÃO foi feito ainda. Mostre a prévia exatamente e pergunte se pode. Só chame "
                             "confirmar_acao com esse código depois que a responsável disser que sim, numa próxima mensagem."}
    return executar(ctx, nome, entrada)


# ----------------------------------------------------------------- trava do processual (T8 a T10)
MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
         "outubro", "novembro", "dezembro")
PALAVRAS_DE_PRAZO = re.compile(r"prazo|fatal|venc|processual|protocol|[úu]ltimo dia|recorrer|limite|termin|encerr|at[ée](?=\s)",
                               re.I)
_DATA_DECLARADA_INTERNA = re.compile(r"intern[oa]|escrit[óo]rio|D-2", re.I)
_FALA_DO_PROCESSUAL = re.compile(r"process", re.I)
_DITA_COMO_PROCESSUAL = re.compile(r"processual|fatal(?!\s+intern)", re.I)
PALAVRAS_DE_EVENTO = re.compile(r"audi[eê]ncia|sess[aã]o|julgamento|per[ií]cia|reuni[aã]o", re.I)
DISTANCIA = 80
_DIA_DA_SEMANA = r"(?:(?:segunda|ter[çc]a|quarta|quinta|sexta)(?:[- ]feira)?|s[áa]bado|domingo)[,:]?\s+(?:dia\s+)?"
_FIM_DE_FRASE = re.compile(r"[!?\n]|\.(?=\s|$)")
_DIAS_POR_EXTENSO = {
    1: "primeiro|um", 2: "dois", 3: "três|tres", 4: "quatro", 5: "cinco", 6: "seis", 7: "sete", 8: "oito", 9: "nove",
    10: "dez", 11: "onze", 12: "doze", 13: "treze", 14: "quatorze|catorze", 15: "quinze", 16: "dezesseis", 17: "dezessete",
    18: "dezoito", 19: "dezenove", 20: "vinte", 30: "trinta"}


def _frase(texto, inicio, fim):
    """A frase em que o trecho [inicio, fim) está."""
    comeco = 0
    for m in _FIM_DE_FRASE.finditer(texto, 0, inicio):
        comeco = m.end()
    m = _FIM_DE_FRASE.search(texto, fim)
    return texto[comeco:m.start() if m else len(texto)]


def _e_data_de_prazo(texto, inicio, fim):
    """A data só fica se a frase dela falar de evento e não falar de prazo (A2).

    Evento e prazo na mesma frase conta como prazo: mostrar o fatal interno no lugar de uma data de audiência é
    um erro que se percebe; mostrar o processual à responsável é o que a decisão D7 proíbe.
    """
    if not PALAVRAS_DE_PRAZO.search(texto, max(0, inicio - DISTANCIA), min(len(texto), fim + DISTANCIA)):
        return False
    frase = _frase(texto, inicio, fim)
    if _DATA_DECLARADA_INTERNA.search(frase) and not _FALA_DO_PROCESSUAL.search(frase):
        return False
    return bool(PALAVRAS_DE_PRAZO.search(frase)) or not PALAVRAS_DE_EVENTO.search(frase)


def _dia_por_extenso(dia):
    if dia in _DIAS_POR_EXTENSO:
        return _DIAS_POR_EXTENSO[dia]
    return "%s e (?:%s)" % (_DIAS_POR_EXTENSO[dia - dia % 10], _DIAS_POR_EXTENSO[dia % 10].split("|", 1)[-1])


def _formatos(d):
    """Os jeitos de escrever a data, do mais longo para o mais curto."""
    dia, mes = "0?%d" % d.day, "0?%d" % d.month
    sep = r"\s*[/.\-]\s*"
    nome = MESES[d.month - 1].replace("ç", "[çc]")
    mes_nome = "%s(?:%s)?" % (nome[:3], nome[3:])
    ano = r"(?:\s+de\s+%04d)?" % d.year
    return [
        r"%04d-%02d-%02d(?:[T ]\d{2}:\d{2}(?::\d{2})?)?" % (d.year, d.month, d.day),
        r"(?:%s)\s+de\s+%s\b%s" % (_dia_por_extenso(d.day), mes_nome, ano),
        r"%s%s%s%s(?:%04d|%02d)" % (dia, sep, mes, sep, d.year, d.year % 100),
        r"%s[º°]?\s+de\s+%s\b%s" % (dia, mes_nome, ano),
        r"%s%s%s\b" % (dia, sep, mes_nome),
        r"%s%s%s" % (dia, sep, mes),
    ]


def trava_processual(texto, processuais, comuns=()):
    """Troca a data processual que aparece perto de palavra de prazo pelo fatal interno (D-2).

    processuais = {data processual: fatal interno}. comuns = datas de tarefa que não são processuais (o vencimento de
    uma tarefa sem prazo processual, por exemplo). Devolve (texto, quantas trocas).
    Data igual por coincidência fica se estiver longe de palavra de prazo, ou numa frase que fale de
    evento e não de prazo (a alma também proíbe mostrar o processual: são duas camadas).
    """
    if not texto or not processuais:
        return texto, 0
    # Data que é processual de uma tarefa e interno de outra: sem a frase dizer que é processual, não dá para saber
    # qual das duas é, e trocar daria à responsável uma data errada (A21).
    ambiguas = set(processuais) & (set(processuais.values()) | set(comuns or ()))
    por_padrao = []
    for proc, interno in processuais.items():
        for f in _formatos(proc):
            por_padrao.append((f, interno, proc))
    por_padrao.sort(key=lambda p: len(p[0]), reverse=True)
    grupos = "|".join("(%s)" % p[0] for p in por_padrao)
    # o dia da semana sai junto: "terça, 26/10" viraria "terça, 22/10", errado e com pista do processual
    padrao = re.compile(r"(?<![\d/\-])(?:%s(?P<ap>\()?)?(?:%s)(?(ap)\))(?![\d/]|[.\-]\d)(?!\s+de\s+\d{4})" % (_DIA_DA_SEMANA, grupos), re.I)
    trocas = 0

    def trocar(m):
        nonlocal trocas
        if not _e_data_de_prazo(texto, m.start(), m.end()):
            return m.group(0)
        _, interno, proc = por_padrao[m.lastindex - 2]   # o grupo 1 é o parêntese opcional
        if proc in ambiguas and not _DITA_COMO_PROCESSUAL.search(_frase(texto, m.start(), m.end())):
            return m.group(0)
        trocas += 1
        return "%s (prazo do escritório, D-2)" % interno.strftime("%d/%m")

    novo = padrao.sub(trocar, texto)
    return novo, trocas


# ----------------------------------------------------------------- registro (T12)
CHAVE_DATAS_DA_TRAVA = "_datas_da_trava"     # ler_autos avisa a trava por aqui; a rota tira antes de chegar ao modelo


def menciona_data(texto, data):
    padrao = r"(?<![\d/\-])(?:%s)(?![\d/]|[.\-]\d)" % "|".join(_formatos(data))
    return bool(re.search(padrao, texto or "", re.I))


def _datas_sabidas(pares):
    return {data_de(p): data_de(i) for p, i in (pares or {}).items() if data_de(p) and data_de(i)}


def trava_data_sabida(texto, processual, interno):
    """Para texto em que a data já é sabidamente a processual (aviso de peça, PAM-33): troca sem exigir palavra de
    prazo. Poupa só a frase que fala de evento e não fala de prazo, porque audiência pode cair no mesmo dia (E5), e
    o dia e mês sem barra, que pode ser número de cláusula ("26.10") ou item ("5-3")."""
    if not texto:
        return texto
    # a data já trocada leva o rótulo e não troca de novo: o interno de uma pode ser o processual de outra
    padrao = re.compile(r"(?<![\d/\-])(?:%s(?P<ap>\()?)?(?:%s)(?(ap)\))(?![\d/]|[.\-]\d)(?!\s*\(prazo do escrit)(?!\s+de\s+\d{4})"
                        % (_DIA_DA_SEMANA, "|".join(_formatos(processual))), re.I)

    def trocar(m):
        frase = _frase(texto, m.start(), m.end())
        if PALAVRAS_DE_EVENTO.search(frase) and not PALAVRAS_DE_PRAZO.search(frase):
            return m.group(0)
        if re.search(r"(?:^|[\s(])\d{1,2}\s*[.\-]\s*\d{1,2}\)?$", m.group(0)):
            return m.group(0)
        return "%s (prazo do escritório, D-2)" % interno.strftime("%d/%m")

    return padrao.sub(trocar, texto)


def gravar_pergunta(con, uid, conversa, pergunta, autor_id=None):
    cur = con.execute("INSERT INTO agente_mensagens (usuario_id, conversa, papel, texto, autor_id, criado_em, via) "
                      "VALUES (?,?,?,?,?,?,?)", (uid, conversa, "user", pergunta, autor_id, time.time(), "hermes"))
    con.commit()
    return cur.lastrowid


def gravar_resposta(con, uid, conversa, resposta, acoes, fontes=None):
    cur = con.execute("INSERT INTO agente_mensagens (usuario_id, conversa, papel, texto, fontes, criado_em, acoes, via) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (uid, conversa, "assistant", resposta, json.dumps((fontes or [])[:8], ensure_ascii=False),
                       time.time(), json.dumps(acoes or [], ensure_ascii=False), "hermes"))
    con.commit()
    return cur.lastrowid


def gravar_turno(con, uid, conversa, pergunta, resposta, acoes, autor_id=None):
    """Pergunta e resposta na mesma tabela de hoje, para a Advogada continuar lendo a conversa da Lua.

    No caminho real a pergunta é gravada ANTES de chamar o Hermes (gravar_pergunta), porque o
    confirmar_acao só aceita o "sim" que já estiver no banco. Esta função junta os dois passos.
    """
    gravar_pergunta(con, uid, conversa, pergunta, autor_id)
    gravar_resposta(con, uid, conversa, resposta, acoes)


# ----------------------------------------------------------------- contexto para o Hermes (T13, T14)
SEMANA = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")


def montar_mensagem(texto, agora, via, orientacoes, pendentes):
    """O que vai ao Hermes a cada mensagem da responsável: o momento, a via, as regras e as prévias abertas."""
    linhas = ["[Sistema Escritório Modelo | Responsável | %s, %s, %s | via %s]" % (
        SEMANA[agora.weekday()], agora.strftime("%d/%m/%Y"), agora.strftime("%H:%M"), via)]
    if via == "voz":
        linhas.append("A responsável falou e vai OUVIR a resposta: no máximo 3 frases curtas, sem listas nem markdown.")
    if orientacoes:
        linhas.append("Orientações da administração (siga sempre):")
        linhas.extend("- %s" % o for o in orientacoes)
    if pendentes:
        linhas.append("Ações esperando o SIM da responsável. Se a mensagem dela for um sim para uma delas, chame "
                      "confirmar_acao com o código; se for não, cancelar_acao:")
        linhas.extend("- código %s: %s" % (p["id"], (p.get("previa") or "")[:400]) for p in pendentes)
    linhas.append("")
    linhas.append("[Mensagem da responsável]")
    linhas.append(texto)
    return "\n".join(linhas)


# ----------------------------------------------------------------- Hermes e reserva (T15)
def perguntar(mensagem, chave, url=HERMES_URL, sessao=SESSAO, timeout=600):
    """Uma chamada ao gateway do perfil lua. Erro de rede, 5xx, 401, 403 e 429 viram HermesIndisponivel."""
    corpo = json.dumps({"model": "lua", "messages": [{"role": "user", "content": mensagem}]}).encode("utf-8")
    req = urllib.request.Request(url, data=corpo, method="POST", headers={
        "Content-Type": "application/json", "X-Hermes-Session-Id": sessao, "Authorization": "Bearer " + chave})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            dados = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code >= 500 or e.code in (401, 403, 429):
            raise HermesIndisponivel("Hermes respondeu %s" % e.code) from e
        raise
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
        raise HermesIndisponivel("Hermes fora do ar: %s" % e) from e
    try:
        return dados["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise HermesIndisponivel("resposta do Hermes sem texto") from e


def ler_memoria(arquivo=MEMORIA):
    """O MESMO arquivo de lições do Hermes, para a reserva não voltar esquecida (fonte única)."""
    try:
        with open(arquivo, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def perguntar_com_reserva(texto, perguntar_hermes, motor_atual, ler_memoria, avisar):
    """Hermes primeiro. Se ele cair, o motor de hoje responde com a mesma memória e a Advogada é avisada."""
    try:
        return perguntar_hermes(texto)
    except (HermesIndisponivel, OSError) as e:
        motivo = str(e) or e.__class__.__name__
    try:
        memoria = ler_memoria() or ""
    except Exception:
        memoria = ""
    try:
        avisar(motivo)
    except Exception:
        pass  # o aviso não pode derrubar a resposta da responsável
    return motor_atual(texto, memoria)


# ----------------------------------------------------------------- datas processuais vistas no turno (G1)
def data_de(valor):
    """Data de 'AAAA-MM-DD' (com ou sem hora), 'DD/MM/AAAA', date ou datetime. Fora disso, None."""
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    s = str(valor or "").strip()
    for formato, pedaco in (("%Y-%m-%d", s[:10]), ("%d/%m/%Y", s[:10])):
        try:
            return dt.datetime.strptime(pedaco, formato).date()
        except ValueError:
            continue
    return None


CHAVES_PROCESSUAL = ("prazo_fatal", "data_fatal", "vencimento")
CHAVES_INTERNO = ("prazo_interno", "prazo_fatal_interno", "compromisso")
# resposta longa de ferramenta chega cortada, como texto: o processual continua lá dentro
# qualquer grafia: aspas simples ou escapadas, maiúsculas, texto livre e ano com 2 dígitos (fiscal, 09/11)
_PROCESSUAL_EM_TEXTO = re.compile(r"""(?i)\\?["']?\b(?:prazo[_ ]fatal|data[_ ]fatal|vencimento)\\?["']?\s*(?:=>|[:=])\s*\\?["']?(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{2}(?:\d{2})?)""")


CHAVES_COMUNS = ("compromisso", "prazo_interno", "prazo_fatal_interno", "data")


def datas_comuns(saida):
    """As datas de tarefa que não são processuais, em tudo o que uma ferramenta devolveu."""
    achadas = set()

    def andar(x):
        if isinstance(x, dict):
            achadas.update(filter(None, (data_de(x.get(c)) for c in CHAVES_COMUNS)))
            for v in x.values():
                andar(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                andar(v)

    andar(saida)
    return achadas


def datas_processuais(saida, pfi):
    """{processual: fatal interno} de tudo o que uma ferramenta devolveu.

    Todo dicionário com uma das CHAVES_PROCESSUAL conta. O interno vem do próprio dicionário ou, se faltar, da conta
    oficial do escritório (pfi = tarefas_prazo.pfi_de, D-2 úteis). Processual igual ao interno não entra.
    """
    mapa = {}

    def anotar(proc, interno):
        interno = interno or data_de(pfi(proc.isoformat()))
        if interno and interno != proc:
            mapa[proc] = interno

    def andar(x):
        if isinstance(x, dict):
            for chave in CHAVES_PROCESSUAL:
                proc = data_de(x.get(chave))
                if proc:
                    anotar(proc, next(filter(None, (data_de(x.get(c)) for c in CHAVES_INTERNO)), None))
            for v in x.values():
                andar(v)
        elif isinstance(x, str):
            for m in _PROCESSUAL_EM_TEXTO.finditer(x):
                v = m.group(1)
                proc = data_de(v[:6] + "20" + v[6:] if len(v) == 8 else v)
                if proc:
                    anotar(proc, None)
        elif isinstance(x, (list, tuple)):
            for v in x:
                andar(v)

    andar(saida)
    return mapa


# ----------------------------------------------------------------- o turno (G2)
# As ferramentas rodam em outra requisição (o Hermes chama a rota interna enquanto o conversar() espera).
# O turno guarda, por pessoa, as datas processuais e as ações que essas chamadas produziram. Vale porque o
# backend roda com --workers 1 (um processo só); com mais de um processo, isto passa a ser uma tabela.
_TURNOS = {}
_TRAVA_TURNOS = threading.Lock()


def _turno_vazio():
    return {"processuais": {}, "acoes": [], "previas": [], "comuns": set()}


def abrir_turno(uid):
    with _TRAVA_TURNOS:
        _TURNOS[uid] = _turno_vazio()


def anotar_turno(uid, processuais, acoes, previas=None, comuns=None):
    with _TRAVA_TURNOS:
        turno = _TURNOS.get(uid)
        if turno is None:
            return                       # sem turno aberto: chamada fora de conversa, nada a guardar
        turno["processuais"].update(processuais or {})
        turno["acoes"].extend(acoes or [])
        turno["previas"].extend(previas or [])
        turno["comuns"].update(comuns or ())


def fechar_turno(uid):
    with _TRAVA_TURNOS:
        turno = _TURNOS.pop(uid, None) or _turno_vazio()
    turno.pop("confirmou", None)
    turno.pop("leu_de_fora", None)
    return turno


def _turno_aberto():
    with _TRAVA_TURNOS:
        return LUA_UID in _TURNOS


def _e_escrita(nome, entrada):
    if nome == "sistema_api":
        return str(entrada.get("metodo") or "GET").upper() != "GET"
    return nome in SEMPRE_PREVIA or nome in PREVIA_SE_LEU_DE_FORA or nome in _ESCRITA_POR_PREVIA_NA_FERRAMENTA


def _leu_de_fora():
    """Sem turno aberto (o Hermes seguiu depois do tempo limite), não dá para saber: vale como se tivesse lido."""
    with _TRAVA_TURNOS:
        turno = _TURNOS.get(LUA_UID)
        return turno is None or bool(turno.get("leu_de_fora"))


def _marcar_leitura_de_fora():
    with _TRAVA_TURNOS:
        turno = _TURNOS.get(LUA_UID)
        if turno is not None:
            turno["leu_de_fora"] = True


def _trouxe_segredo(nome, out):
    if nome not in ("ler_autos", "sistema_api", "buscar_por_nome"):
        return False
    texto = json.dumps(out, ensure_ascii=False, default=str)        # resposta longa vem como texto dentro de "parcial", com aspas escapadas
    return AVISO_SEGREDO in texto or re.search(r'(?:segredo_justica|sigiloso)\W{1,8}(?:true|1)\b', texto, re.I) is not None


def _marcar_sigilo():
    with _TRAVA_TURNOS:
        turno = _TURNOS.get(LUA_UID)
        if turno is not None:
            turno["sigilo"] = True
    try:
        with open(ARQUIVO_SIGILO, "w", encoding="utf-8") as f:
            f.write(dt.date.today().isoformat())
    except OSError:
        pass        # continua valendo o bloqueio do turno


def _sigilo_hoje():
    try:
        with open(ARQUIVO_SIGILO, encoding="utf-8") as f:
            return f.read().strip() == dt.date.today().isoformat()
    except OSError:
        return False


def _motivo_sem_internet():
    """D21 e decisão da Advogada (09/11/2026): depois de ler dado elevado ou sob segredo, sem internet pelo resto do dia.
    Sem conversa aberta, também não."""
    em_sigilo = _sigilo_hoje()
    with _TRAVA_TURNOS:
        turno = _TURNOS.get(LUA_UID)
        if turno is None:
            return "a conversa com a responsável já terminou. NÃO busquei."
        if turno.get("sigilo") or em_sigilo:
            return ("hoje você leu processo em segredo de justiça ou dado que exigiu permissão de administração, e esse conteúdo "
                    "ainda está na conversa. NÃO busquei na internet: responda com o que já tem. A busca volta amanhã.")
    return None


def acoes_sem_processual(acoes, mapa, comuns=()):
    """O resumo das ações também vai à tela da responsável: passa pela mesma trava."""
    return [trava_processual(a, mapa, comuns)[0] for a in acoes or []]


# ----------------------------------------------------------------- o "sim" de verdade (A3 a A6)
# O motor de hoje aceita como sim qualquer frase curta com "cria", "faz" ou "manda". Pelo Hermes, em que um documento
# pode ter enganado o modelo, o servidor exige um sim sem assunto novo, para a última prévia, uma ação por sim.
_NUCLEO_DO_SIM = {"sim", "pode", "podes", "confirmo", "confirmado", "confirma", "autorizo", "ok", "okay", "isso", "claro",
                  "certo", "beleza", "blz", "positivo", "fechado", "bora", "manda", "mande", "envia", "envie", "faz",
                  "faça", "faca", "segue", "siga", "perfeito", "exato", "correto"}
_COMPLEMENTO_DO_SIM = {"mandar", "enviar", "fazer", "seguir", "ser", "por", "favor", "lua", "doutora", "mesmo", "vai", "em",
                       "frente"}
TETO_DO_SIM = 60


def e_um_sim(texto):
    """Só palavras de concordância, sem assunto novo: "pode mandar" é sim; "faz a réplica do Fulano" não é."""
    texto = (texto or "").strip().lower()
    palavras = re.findall(r"[a-zà-ú]+", texto)
    if not palavras or len(texto) > TETO_DO_SIM or re.search(r"[\d?]", texto):      # número e pergunta trazem assunto novo
        return False
    return all(p in _NUCLEO_DO_SIM | _COMPLEMENTO_DO_SIM for p in palavras) and any(p in _NUCLEO_DO_SIM for p in palavras)


def _barrar_confirmacao(entrada, ultima_msg, ultima_previa):
    """Motivo para recusar um confirmar_acao vindo do Hermes, ou None. Na dúvida, recusa."""
    if not ultima_msg or not ultima_previa or not e_um_sim(ultima_msg()):
        return ("a última mensagem da responsável não é um sim claro para a prévia. NÃO executei. Mostre a prévia e pergunte "
                "de novo.")
    try:
        codigo = int(entrada.get("codigo"))
    except (TypeError, ValueError):
        return "código de prévia inválido. NÃO executei."
    atual = ultima_previa()
    if codigo != atual:
        return "o sim vale só para a última prévia mostrada (código %s). NÃO executei o código %s." % (atual, codigo)
    with _TRAVA_TURNOS:
        turno = _TURNOS.get(LUA_UID)
        if turno is None:
            return "confirmação só vale dentro de uma conversa com a responsável. NÃO executei."
        if turno.get("confirmou"):
            return "um sim vale para uma ação só. NÃO executei; mostre a próxima prévia e espere outro sim."
        turno["confirmou"] = True
    return None


def _sem_espacos_extras(texto):
    return re.sub(r"\s+", " ", texto or "").strip()


def garantir_previas(resposta, previas):
    """Toda prévia criada no turno aparece para a responsável, mesmo que o modelo tenha omitido (A7)."""
    vista = _sem_espacos_extras(resposta)
    faltam = [p for p in previas or [] if _sem_espacos_extras(p.get("previa")) not in vista]
    if not faltam:
        return resposta
    blocos = ["Esperando o seu sim (código %s):\n%s" % (p.get("codigo"), p.get("previa")) for p in faltam]
    return ((resposta or "").rstrip() + "\n\n" + "\n\n".join(blocos)).strip()


def reserva_sem_repetir(uid, motor_atual):
    """Se o Hermes caiu DEPOIS de já agir, o motor de hoje não refaz o pedido (sairia tarefa em dobro):
    a responsável recebe a lista do que já foi feito. Sem ação feita no turno, a reserva segue normal (G10)."""
    def reserva(texto, memoria):
        with _TRAVA_TURNOS:
            turno = _TURNOS.get(uid) or {}
            feitas = list(turno.get("acoes") or [])[:12]
            feitas += ["Preparei a prévia de código %s, que espera o seu sim" % p.get("codigo") for p in turno.get("previas") or []]
        if feitas:
            return ("O Hermes caiu no meio do pedido. Antes de cair, eu já fiz:\n" +
                    "\n".join("- %s" % a for a in feitas) + "\nConfira e me diga o que falta.")
        return motor_atual(texto, memoria)
    return reserva


# ----------------------------------------------------------------- o turno da Lua no Hermes (G3, G4)
def turno_hermes(texto, via, agora, orientacoes, pendentes, perguntar_hermes, motor_atual, ler_memoria, avisar,
                 processuais, previas=None, comuns=None):
    """Contexto, Hermes (ou reserva), trava do processual e sem travessão. Devolve (resposta, trocas, pela_reserva).

    processuais() é chamada DEPOIS da resposta, porque as ferramentas só rodam durante a pergunta ao Hermes.
    A reserva recebe o texto original da responsável: o motor de hoje monta o próprio contexto.
    """
    mensagem = montar_mensagem(texto, agora, via, orientacoes, pendentes)
    pela_reserva = []

    def reserva(_mensagem, memoria):
        pela_reserva.append(True)
        return motor_atual(texto, memoria)

    resposta = perguntar_com_reserva(mensagem, perguntar_hermes, reserva, ler_memoria, avisar) or ""
    mapa = processuais()                    # fecha o turno: as prévias só ficam completas depois disso
    if previas:
        resposta = garantir_previas(resposta, previas())
    resposta, trocas = trava_processual(resposta, mapa, comuns() if comuns else ())
    return sem_travessao(resposta), trocas, bool(pela_reserva)


# ----------------------------------------------------------------- a rota interna (G5 a G7)
def atender_rota(recebida, certa, ip, cabecalhos, corpo, ctx_factory, executar, criar_pendente, pfi, ultima_msg=None,
                 ultima_previa=None, falta_ler_autos=None):
    """O corpo da rota /api/agentes/hermes/ferramenta. Devolve (status http, resposta).

    A chave é conferida antes de qualquer coisa: sem ela, nem o contexto da responsável é montado.
    """
    cabecalhos = {str(k).lower(): v for k, v in (cabecalhos or {}).items()}
    if not chave_ok(recebida, certa, ip, cabecalhos):
        return 403, {"erro": "chave inválida"}
    corpo = corpo if isinstance(corpo, dict) else {}
    nome = str(corpo.get("nome") or "").strip()
    entrada = corpo.get("entrada") or {}
    if not nome or not isinstance(entrada, dict):
        return 400, {"erro": "mande o nome da ferramenta e a entrada como objeto"}
    if nome == "confirmar_acao":
        motivo = _barrar_confirmacao(entrada, ultima_msg, ultima_previa)
        if motivo:
            return 200, {"erro": motivo}
    if not _turno_aberto() and _e_escrita(nome, entrada):
        return 200, {"erro": "a conversa com a responsável já terminou, então ela não veria a prévia. NÃO fiz."}
    motivo = falta_ler_autos(nome, entrada) if falta_ler_autos else None      # regra da Advogada (D25)
    if motivo:
        return 200, {"erro": motivo}
    motivo = _motivo_sem_internet() if nome == "buscar_internet" else None
    if motivo:
        return 200, {"erro": motivo}
    if ultima_msg and MARCA_DE_ANEXO in str(ultima_msg() or ""):      # o nome do arquivo é texto de fora dentro da fala da responsável
        _marcar_leitura_de_fora()
    ctx = ctx_factory()
    try:
        out = executar_pela_rota(nome, entrada, executar, criar_pendente, ctx, leu_de_fora=_leu_de_fora())
    except TypeError as e:
        out = {"erro": "parâmetros errados para %s: %s" % (nome, str(e)[:120])}
    except Exception as e:
        out = {"erro": str(e)[:200]}
    if not isinstance(out, dict):
        out = {"resultado": out}
    if nome in LEITURA_DE_FORA:
        _marcar_leitura_de_fora()
    if ctx.get("leitura_elevada") or _trouxe_segredo(nome, out):
        _marcar_sigilo()
    previas = [{"codigo": out.get("codigo"), "previa": out.get("previa")}] if out.get("precisa_confirmar") else []
    confiavel = nome not in TEXTO_SEM_VARREDURA     # texto de fora não escolhe quais datas a trava protege (A65)
    processuais = datas_processuais(out, pfi) if confiavel else {}
    processuais.update(_datas_sabidas(out.pop(CHAVE_DATAS_DA_TRAVA, None)) if nome == "ler_autos" else {})
    anotar_turno(LUA_UID, processuais, list(ctx.get("acoes") or []), previas, datas_comuns(out) if confiavel else ())
    return 200, out


def ferramentas_para_mcp(ferramentas):
    """Do formato do Claude (input_schema) para o do MCP (inputSchema)."""
    return [{"name": f["name"], "description": f.get("description") or "",
             "inputSchema": f.get("input_schema") or {"type": "object", "properties": {}}} for f in ferramentas]


# ----------------------------------------------------------------- regra do escritório (T17)
def sem_travessao(texto):
    """Sem travessão: no meio da frase vira vírgula, entre números vira 'a', no começo da linha sai."""
    if not texto:
        return texto
    texto = re.sub(r"(?<=\d)\s*[—–]\s*(?=\d)", " a ", texto)
    texto = re.sub(r"(?m)^[ \t]*[—–][ \t]*", "", texto)
    texto = re.sub(r"\s*[—–]\s*", ", ", texto)
    return texto
