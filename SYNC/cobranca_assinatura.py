# -*- coding: utf-8 -*-
"""
Cobranca automatica de assinatura (fase de contratacao) - Escritório Modelo Advogados.

Fonte da verdade = ZapSign (assinou ou nao).
Braco de envio    = D-API (numero oficial, via wa_dapi.py) desde 24/09/2026.

MODELO DE FILA (enrollment): SO cobra clientes explicitamente inscritos.
Isso evita disparar para o ZapSign inteiro / casos antigos nao relacionados.

Fluxo:
  1) O intake (ou o comando --enroll) inscreve o cliente na fila, guardando
     os tokens dos docs dele no ZapSign e a data de envio.
  2) 1x/dia o processamento verifica no ZapSign se ja assinou:
       - assinou tudo  -> agradece 1x e encerra
       - pendente      -> envia a mensagem do "dia" (D+1..D+MAX)
       - passou do MAX  -> para o automatico e ALERTA o time
  3) Um cliente so recebe 1 mensagem por dia.

Comandos:
  Inscrever (descobre os tokens pelo nome no ZapSign):
    python cobranca_assinatura.py --enroll --nome "Jetro Correa de Sousa" --tel "19995153482"
  Processar a fila (simulacao, padrao):
    python cobranca_assinatura.py
  Processar de verdade (envia pela D-API):
    python cobranca_assinatura.py --enviar
  Ver a fila:
    python cobranca_assinatura.py --status
"""
import os, sys, io, json, re
from datetime import datetime, timezone

# console em UTF-8 (evita erro de emoji no Windows)
try:
    # so reembrulha se preciso: reembrulhar stdout ja-utf8 FECHA o buffer
    # compartilhado e derruba quem importou este modulo (crash de 12/08).
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_RAIZ, "INTEGRACOES"))
from dotenv import load_dotenv
load_dotenv(os.getenv(
    "ESC_CONFIG_ENV",
    r"C:\AUTOMAÇÕES\AUTOMACOES_PROJETO_COMPLETO_2026-07-19\config\.env"))
if os.path.exists("/opt/escritorio/config/.env"):
    load_dotenv("/opt/escritorio/config/.env")  # ZAPSIGN_TOKEN na VPS

from zapsign_integration import listar_documentos, buscar_documento
import requests

# Envio pela D-API (numero oficial do escritorio) via wa_dapi.py, desde 24/09/2026:
# a Z-API desconectou em 23/09 ~14:30 e seguia devolvendo 200 sem entregar.
# Regra vigente (Advogada, 10/08/2026): NAO enviar pelo Atende Direito/ADONE - responde
# ok e NAO entrega.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # wa_dapi mora em SYNC/
import wa_dapi


def _ddi(tel):
    d = "".join(ch for ch in str(tel) if ch.isdigit())
    return d if d.startswith("55") else "55" + d


def zapi_enviar_texto(telefone, texto):
    """Nome antigo mantido; o envio sai pela D-API (wa_dapi). True so com envio aceito."""
    try:
        ok, info = wa_dapi.enviar_texto(_ddi(telefone), texto)
        if not ok:
            print(f"  (d-api recusou: {str(info)[:160]})")
        return bool(ok)
    except Exception as e:
        print(f"  (d-api erro: {str(e)[:120]})")
        return False


def _dapi_configurado():
    try:
        wa_dapi._cred()
        return True
    except Exception:
        return False

ENVIAR = "--enviar" in sys.argv
MAX_DIA = 7  # apos D+MAX para o automatico e alerta o time
TEL_AVISO_TIME = os.getenv("TEL_ALERTA_EQUIPE", "")  # avisos de assinatura concluida (vazio = nao avisa)
FILA = os.path.join(os.path.dirname(__file__), "cobranca_fila.json")

# --------------------------------------------------------------- figurinhas
# URL PUBLICA (.webp p/ figurinha; .png/.jpg vira imagem) por etapa.
# Deixe "" para nao enviar figurinha naquela etapa. So dispara em modo --enviar.
# WhatsApp exige figurinha em .webp (512x512, fundo transparente). Se o Atende
# Direito nao aceitar tipo 'sticker', troque FIG_TIPO para 'image'.
FIG_TIPO = "sticker"
FIGURINHAS = {
    "D+1": "",
    "D+2": "",
    "D+3": "",
    "D+4": "",
    "D+5": "",
    "D+6": "",
    "D+7": "",
    "ASSINOU": "",
}

# nomes exatos que o NOSSO intake gera (evita pegar pecas avulsas)
RX_INTAKE = re.compile(r" - (Contrato de Honor|Procura|Declara)", re.IGNORECASE)


def arg(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


# ------------------------------------------------------------------ video tutorial
# "Como assinar pelo celular" (25/09/2026, pedido do Titular): vai JUNTO com o
# link de assinatura, UMA vez por cliente. Versao SMS: desde 25/09 o signatario
# com celular vai sem e-mail e o codigo de 6 letras chega por SMS.
VIDEO_TUTORIAL = "https://sistema.escritorio.adv.br/media/tutorial_assinar_zapsign_sms_v1.mp4"
LEGENDA_TUTORIAL = ("\U0001F3AC Fiz esse video rapidinho mostrando, passo a passo, como assinar "
                    "pelo celular. O codigo de 6 letras chega por SMS no seu celular. "
                    "Qualquer duvida e so me chamar aqui! \U0001F91D")


def enviar_video_tutorial(telefone):
    """Video nativo da D-API (toca na propria conversa). Se a rota de video
    falhar, manda como documento mp4 para o cliente nao ficar sem."""
    if not telefone:
        return False
    try:
        d = wa_dapi.destino(_ddi(telefone))
        ok, j, st = wa_dapi._post("messages/send/video",
                                  {"to": d, "video": VIDEO_TUTORIAL, "caption": LEGENDA_TUTORIAL})
        if ok:
            return True
        print(f"  video nativo falhou ({st}); indo como documento")
        ok, info = wa_dapi.enviar_documento(_ddi(telefone), VIDEO_TUTORIAL, "Como assinar pelo celular.mp4",
                                            legenda=LEGENDA_TUTORIAL, ext="mp4")
        return bool(ok)
    except Exception as e:
        print(f"  video tutorial falhou: {str(e)[:150]}")
        return False


def _video_junto(c):
    """Primeira mensagem com os links -> o video vai junto (uma vez so)."""
    if c.get("video_tutorial") or not c.get("links"):
        return
    if enviar_video_tutorial(c.get("telefone")):
        c["video_tutorial"] = str(datetime.now(timezone.utc).date())
        print("  + video tutorial de assinatura enviado junto")


# ------------------------------------------------------------------ mensagens
def msg(dia, nome):
    p = (nome or "").split()[0].title() if nome else "amigo(a)"
    L = {
        # D+1 - leve, acolhedor, respeita o descanso do motorista
        1: (f"Oi, {p}! Tudo joia por ai? \U0001F69B\U0001F49A\n\n"
            f"Sei que a estrada e puxada e o descanso e sagrado, entao vou ser rapidinho \U0001F64F\n\n"
            f"A parte mais dificil voce ja venceu. Agora falta so um passinho simples: assinar seus "
            f"documentos no celular (leva uns 2 minutinhos ⏱️). Ai a gente ja comeca a caminhar "
            f"juntinho com voce \U0001F91D\n\n"
            f"To aqui pro que precisar, viu? \U0001F49B\nEscritório Modelo Advogados"),
        # D+2 - aspiracao / alivio, afetuoso (sem prometer resultado)
        2: (f"{p}, que bom te encontrar por aqui \U0001F60A\n\n"
            f"Pensa so: depois de tanto rodar, ter alguem batalhando pelos SEUS direitos enquanto "
            f"voce cuida da sua vida e da sua familia ❤️ E disso que a gente cuida pra voce.\n\n"
            f"Falta so a sua assininha pra dar o start \U0001F680 Qualquer duvida, e so me chamar, ta? ⚖️\n"
            f"Escritório Modelo Advogados"),
        # D+3 - prova social ETICA (pertencimento, sem valores/promessa) + docs
        3: (f"Oi, {p}! \U0001F44B\U0001F49B\n\n"
            f"Muita gente da estrada, motorista igualzinho a voce, ja deu esse mesmo passo e hoje "
            f"dorme mais tranquilo por ter ido atras do que era seu \U0001F319✨ Voce esta a uma "
            f"assinatura de comecar essa virada.\n\n"
            f"Se o link travou, me avisa que reenvio rapidinho \U0001F501 E quando puder, separa os "
            f"holerites e o termo de rescisao que voce tiver, que dao uma forca enorme no seu caso \U0001F4AA\U0001F4C4\n"
            f"Escritório Modelo Advogados"),
        # D+4 - cuidado + urgencia gentil (prazo), sem pressao
        4: (f"{p}, sei que a correria na boleia nao para \U0001F69A\U0001F4A8\n\n"
            f"Passo aqui so com carinho pra nao deixar seu caso esperando, porque a Justica tem "
            f"seus prazos e eu quero o melhor pra voce ❤️\n\n"
            f"Consegue dar essa assinada hoje? Se pintar qualquer dificuldade, conta comigo \U0001F917\n"
            f"Escritório Modelo Advogados"),
        # D+5 - humaniza, oferece ligacao, no seu tempo
        5: (f"Oi, {p}! \U0001F4DE\U0001F49B\n\n"
            f"Se ficar mais facil, eu te ligo e a gente assina juntinho, com calma, no seu tempo. "
            f"Me diz um horario bom que eu te chamo \U0001F642\n\n"
            f"Voce nao ta sozinho nessa, a gente vai lado a lado ate o fim \U0001F91D\n"
            f"Escritório Modelo Advogados"),
        # D+6 - ultima automatica, calorosa e festiva
        6: (f"{p}, essa e minha ultima mensagem automatica por aqui \U0001F60A\n\n"
            f"Seria uma pena seu caso ficar parado tao pertinho de comecar, ne? Falta so voce \U0001F49A "
            f"Se der pra assinar hoje, ja entramos com tudo por voce.\n\n"
            f"E quando assinar... e festa desse lado! \U0001F389\U0001F973\n"
            f"Escritório Modelo Advogados"),
        # D+7 - handoff humano, com carinho
        7: (f"{p}, vou pedir com todo carinho pra uma pessoa do nosso time te chamar pessoalmente "
            f"pra te ajudar a concluir, pode ser? \U0001F91D\n\n"
            f"Voce e importante pra gente e a gente nao quer que voce perca essa oportunidade \U0001F49B\n"
            f"Escritório Modelo Advogados"),
    }
    return L.get(min(dia, MAX_DIA), L[MAX_DIA])


def cliente_assinou_tudo(c):
    """Olha o ZapSign e diz se o CLIENTE ja assinou tudo (a assinatura da Advogada nao conta)."""
    for t in (c.get("tokens") or []):
        full = buscar_documento(t["token"])
        if not full:
            return False
        for s in (full.get("signers") or []):
            if "advogada" in (s.get("name") or "").lower():
                continue
            if s.get("status") != "signed":
                return False
    return bool(c.get("tokens"))


# ------------------------------------------------- tarefa de ligacao -------
# (Advogada, 14/10/2026) "quando percebermos que o cliente abriu e nao assinou,
# geramos a tarefa para a vendedora do caso no Sistema Escritório Modelo fazer ligacao".
# Mesma receita do lead_quente_tarefa.py (tarefa urgente + faixa vermelha;
# a origem 'cobranca-assinatura' esta no ORIGENS do bloco_aviso_tarefas.js).
# Tambem nasce tarefa quando a regua esgota em D+MAX: antes era so um print
# no log ("ALERTA INTERNO") e o caso do Laerte ficou 3 semanas invisivel.

SISTEMA_DB = os.getenv("SISTEMA_DB", "/opt/escritorio/SISTEMA/backend/sistema_esc.db")
import sys as _s, os as _o
_s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))), 'INTEGRACOES'))
import escritorio_config as CFG  # noqa: E402
VENDEDORAS = CFG.vendedores()                  # VENDEDORES_EMAILS no .env
VENDEDORA_PADRAO = next(iter(VENDEDORAS.values()), (None, "comercial"))   # integral; dona de card que nao e vendedora cai aqui
ABRIU_CARENCIA_H = 3   # abriu numa rodada e segue sem assinar na seguinte -> liga
TIPO_TAREFA = "ENTRAR EM CONTATO COM O LEAD"


def _conversa_do_cliente(tel):
    """Card do cliente pelos 8 ultimos digitos: (conversa_id, dona, nome)."""
    import sqlite3
    dig = re.sub(r"\D", "", tel or "")[-8:]
    if len(dig) < 8:
        return None, "", ""
    try:
        con = sqlite3.connect(f"file:{SISTEMA_DB}?mode=ro", uri=True, timeout=15)
        con.execute("pragma busy_timeout=8000")
        r = con.execute(
            "select id, coalesce(atendente_email,''), coalesce(contato_nome,'') "
            "from wa_conversas where replace(contato_telefone,'+','') like ? "
            "order by id desc limit 1", ("%" + dig,)).fetchone()
        con.close()
        return (r[0], r[1], r[2]) if r else (None, "", "")
    except Exception as e:
        print(f"  (conversa do cliente: {str(e)[:100]})")
        return None, "", ""


def _tarefa_ligacao(c, nome, motivo, detalhe):
    """Abre a tarefa URGENTE de ligacao para a vendedora do caso no Sistema
    Escritório Modelo. Uma por cliente e por motivo (dedup por origem+origem_ref); no
    dry-run so anuncia. Nao manda mensagem nenhuma ao cliente."""
    import sqlite3
    import time as _t
    cid, dona, nome_card = _conversa_do_cliente(c.get("telefone", ""))
    uid, vend = VENDEDORAS.get((dona or "").lower(), VENDEDORA_PADRAO)
    ref = f"{motivo}:{re.sub(r'[^A-Za-z0-9]', '', nome)[:40]}"
    titulo = (f"ASSINATURA ABERTA E NAO CONCLUIDA: {nome_card or nome}"
              if motivo == "abriu" else
              f"ASSINATURA SEM RESPOSTA (regua esgotou): {nome_card or nome}")
    links = "\n".join(f"{l.get('rotulo')}: {l.get('url')}" for l in (c.get("links") or []))
    texto = "\n".join([
        f"{nome} · {c.get('telefone', '')}", "",
        detalhe, "",
        "Ligar, entender o que travou (duvida de clausula, honorario, medo de golpe)",
        "e colher a assinatura na propria ligacao. Links dos documentos:",
        links, "",
        (f"Codigo #{cid}. " if cid else "") +
        "Depois da ligacao, atualize o card no CRM e conclua esta tarefa."])
    if not ENVIAR:
        print(f"  (dry-run: abriria tarefa urgente '{titulo}' para {vend})")
        return None
    con = sqlite3.connect(SISTEMA_DB, timeout=20)
    con.execute("pragma busy_timeout=8000")
    try:
        r = con.execute("select id from tarefas where origem='cobranca-assinatura' and origem_ref=?",
                        (ref,)).fetchone()
        if r:
            return r[0]
        hoje_s, agora = datetime.now().date().isoformat(), _t.time()
        cur = con.execute(
            "INSERT INTO tarefas (tipo, titulo, descricao, data, prazo_interno, prioridade, status, "
            "origem, origem_ref, criado_por, criado_em, atualizado_em, contato_nome, contato_telefone, "
            "conversa_id) VALUES (?,?,?,?,?,'urgente','aberta','cobranca-assinatura',?,?,?,?,?,?,?)",
            (TIPO_TAREFA, titulo, texto, hoje_s, hoje_s, ref, "Cobranca de assinatura (automacao)",
             agora, agora, nome_card or nome, c.get("telefone", ""), cid))
        con.execute("INSERT INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) "
                    "VALUES (?,?,?,1,1)", (cur.lastrowid, uid, vend))
        con.commit()
        print(f"  >> tarefa #{cur.lastrowid} ({motivo}) aberta para {vend}.")
        return cur.lastrowid
    except Exception as e:
        print(f"  (tarefa de ligacao falhou: {str(e)[:150]})")
        return None
    finally:
        con.close()


def msg_assinou(nome):
    p = (nome or "").split()[0].title() if nome else "amigo(a)"
    return (f"Ihuuul, {p}! \U0001F389✅ Assinaturas confirmadas! Muito obrigado pela confianca \U0001F64F\U0001F49B\n\n"
            # sem citar documento aqui (caso Abel, 25/09): a mensagem pedia holerite e
            # TRCT a quem nunca foi registrado. O que falta sai da cobranca de documentos,
            # que ja olha a conversa e o caso antes de pedir.
            f"Agora e correr pra dar entrada no seu caso \U0001F680 O que voce ja mandou aqui "
            f"a gente ja guardou certinho. Se faltar alguma coisa pro seu caso, eu te aviso "
            f"por aqui, so o que faltar mesmo.\n\n"
            f"Bora nessa estrada juntos! \U0001F69B⚖️\nEscritório Modelo Advogados")


# ------------------------------------------------------------------ fila
def carregar():
    if os.path.exists(FILA):
        try:
            with open(FILA, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"clientes": {}}


def salvar(f):
    with open(FILA, "w", encoding="utf-8") as fp:
        json.dump(f, fp, ensure_ascii=False, indent=2)


def parse_dt(v):
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None
    except Exception:
        return None


# ------------------------------------------------------------------ enroll
def _rotulo_doc(nome_doc):
    up = (nome_doc or "").upper()
    if "CONTRATO" in up:
        return "Contrato de Honorarios"
    if "PROCURA" in up:
        return "Procuracao"
    if "DECLARA" in up:
        return "Declaracao de Hipossuficiencia"
    return "Documento"


def bloco_links(c):
    """Bloco reenviado em TODA mensagem de cobranca: incentiva assinar pelo celular."""
    links = c.get("links") or []
    if not links:
        return ""
    linhas = "\n".join(f"\U0001F4C4 {l['rotulo']}:\n{l['url']}" for l in links)
    return ("\n\n\U0001F449 Seus documentos pra assinar (da pra fazer rapidinho no proprio "
            "celular \U0001F4F1):\n" + linhas +
            "\n\nSe precisar de ajuda ou o link travar, e so me chamar aqui que eu te "
            "ajudo na hora! \U0001F917")


def inscricao_viva(clientes, nome="", telefone=""):
    """A inscricao que AINDA esta cobrando este cliente, ou None.

    Inscricao encerrada e de um caso ANTERIOR. Cliente que fecha um caso novo
    precisa de inscricao propria: casando por pessoa, ele recebia de volta os
    links que ja tinha assinado meses antes (Advogada, 29/10/2026, 2o contrato do
    Oziel). Casa por chave, pelo nome de dentro e pelo telefone (ultimos 8),
    como antes, mas so devolve inscricao viva.
    """
    base = (nome or "").strip().lower()
    tel8 = "".join(c for c in str(telefone or "") if c.isdigit())[-8:]
    vivas = []
    for chave, c in (clientes or {}).items():
        if c.get("encerrado"):
            continue
        mesmo_nome = bool(base) and (chave.strip().lower() == base
                                     or (c.get("nome") or "").strip().lower() == base)
        tel_c = "".join(x for x in (c.get("telefone") or "") if x.isdigit())[-8:]
        if mesmo_nome or (tel8 and tel_c == tel8):
            vivas.append((chave, c))
    return vivas[-1] if vivas else None


def _tokens_tomados(clientes):
    """Tokens de documento que ja pertencem a alguma inscricao.

    Um documento do ZapSign e de UM caso so. E isso que separa os casos do mesmo
    cliente, porque a busca por nome.startswith() nao sabe distinguir, e e
    tambem a trava de seguranca do enroll_etapa: se nao existe documento NOVO
    no ZapSign, nada e inscrito e ninguem e cobrado por engano.
    """
    tomados = set()
    for c in (clientes or {}).values():
        for t in (c.get("tokens") or []):
            if t.get("token"):
                tomados.add(t["token"])
    return tomados


def _chave_do_caso(clientes, nome):
    """Chave livre para este cliente: o caso novo nao sobrescreve o anterior."""
    if nome not in (clientes or {}):
        return nome
    for n in range(2, 30):
        k = "%s (%do caso)" % (nome, n)
        if k not in clientes:
            return k
    return nome


def enroll_cliente(nome, tel="", _tentativas=3):
    """
    Inscreve o cliente na fila de cobranca, descobrindo no ZapSign os docs,
    o telefone do signatario e os LINKS de assinatura. Usavel pelo intake
    (auto-inscricao) e pela CLI. Retorna True se inscreveu.
    Tenta algumas vezes, pois o ZapSign pode demorar a indexar docs recem-criados.
    """
    import time
    from zapsign_integration import ZAPSIGN_BASE

    def _ultima_pagina():
        """O ZapSign lista do mais ANTIGO para o mais novo: doc recem-criado
        mora na ULTIMA pagina. Usa o count da API para varrer de tras pra frente."""
        try:
            tok = os.getenv("ZAPSIGN_TOKEN") or os.getenv("ZAPSIGN_API_TOKEN", "")
            r = requests.get(f"{ZAPSIGN_BASE}/docs/?page=1",
                             headers={"Authorization": f"Bearer {tok}"}, timeout=30)
            return max(1, (int(r.json().get("count") or 0) + 24) // 25)
        except Exception:
            return 1

    # documento que ja pertence a outra inscricao nao entra nesta (por caso, 29/10/2026)
    tomados = _tokens_tomados(carregar().get("clientes"))
    tokens, data_envio, tel_zap, links = [], None, None, []
    for tentativa in range(_tentativas):
        tokens, data_envio, tel_zap, links = [], None, None, []
        ultima = _ultima_pagina()
        for page in range(ultima, max(0, ultima - 8), -1):
            docs, _has_next = listar_documentos(status=None, page=page)
            if not docs:
                continue
            for d in docs:
                n = d.get("name", "")
                if n.lower().startswith((nome or "").lower()) and RX_INTAKE.search(n):
                    tk = d.get("token")
                    if tk in tomados:
                        continue  # documento de um caso anterior deste cliente
                    tokens.append({"token": tk, "nome_doc": n})
                    cad = parse_dt(d.get("created_at"))
                    if cad and (data_envio is None or cad.date() < data_envio):
                        data_envio = cad.date()
                    full = buscar_documento(tk) or d
                    for s in (full.get("signers") or []):
                        if "advogada" in (s.get("name") or "").lower():
                            continue
                        if s.get("phone_number"):
                            tel_zap = s["phone_number"]
                        if s.get("sign_url"):
                            links.append({"rotulo": _rotulo_doc(n), "url": s["sign_url"]})
            # NAO parar na primeira pagina com achado: os docs de um mesmo
            # cliente podem cair na fronteira entre duas paginas.
        if tokens:
            break
        if tentativa < _tentativas - 1:
            print(f"[enroll] docs ainda nao indexados no ZapSign, tentando de novo...")
            time.sleep(8)
    if not tokens:
        print(f"[enroll] nenhum doc de contratacao encontrado para '{nome}'.")
        return False
    fila = carregar()
    # o caso novo ganha chave propria em vez de sobrescrever a inscricao anterior;
    # o `nome` de dentro continua o do cliente, porque e ele que vai nas mensagens
    chave = _chave_do_caso(fila["clientes"], nome)
    fila["clientes"][chave] = {
        "nome": nome,
        "telefone": tel or tel_zap or "",
        "tokens": tokens,
        "links": links,
        "data_envio": str(data_envio) if data_envio else None,
        "ultimo_envio": None,
        "dias_enviados": [],
        "encerrado": False,
    }
    salvar(fila)
    print(f"[enroll] inscrito: {chave} | tel {tel or tel_zap} | {len(tokens)} docs "
          f"| {len(links)} links | envio {data_envio}")
    return chave


def enroll():
    nome = arg("--nome")
    tel = arg("--tel")
    if not nome:
        print("Uso: --enroll --nome \"Nome Completo\" [--tel \"19999999999\"]")
        return
    print(f"Procurando docs de '{nome}' no ZapSign...")
    enroll_cliente(nome, tel or "")


# ------------------------------------------------- auto-inscricao pelo CRM
DB_CRM = os.getenv("SISTEMA_DB", "/opt/escritorio/SISTEMA/backend/sistema_esc.db")
ETAPAS_CONTRATO = ("Contrato / Assinatura", "Elaboração de Contrato")
# pipeline Contratação (id 20): etapas onde o contrato ja pode existir
ETAPAS_CONTRATACAO_20 = ("Documentos Conferidos", "Contrato no ZapSign")


def enroll_etapa():
    """Varre o Sistema Escritório Modelo (pipeline Reclamantes) e tenta inscrever quem esta nas
    etapas de contrato e ainda nao esta na fila. O gate real e o ZapSign: se o
    contrato ainda nao existe la, nada acontece e a proxima rodada tenta de
    novo. Leitura do banco em modo somente-leitura."""
    import sqlite3
    if not os.path.exists(DB_CRM):
        print(f"[enroll-etapa] banco do Sistema Escritório Modelo nao encontrado ({DB_CRM}); pulando.")
        return
    con = sqlite3.connect(f"file:{DB_CRM}?mode=ro", uri=True, timeout=15)
    con.execute("pragma busy_timeout=8000")
    rows = con.execute(
        "select contato_nome, contato_telefone from wa_conversas "
        "where (pipeline_id=10 and etapa in (?,?)) "
        "or (pipeline_id=20 and etapa in (?,?))",
        (*ETAPAS_CONTRATO, *ETAPAS_CONTRATACAO_20)).fetchall()
    con.close()
    fila = carregar()
    # dedup olhando so inscricao VIVA (29/10/2026): quem ja assinou e voltou com um
    # caso NOVO precisa poder entrar de novo. Nao ha risco de cobrar alguem a esmo
    # porque o enroll_cliente ignora documento que ja pertence a outra inscricao:
    # sem documento novo no ZapSign ele nao inscreve ninguem.
    ja = {n.strip().lower() for n, c in fila["clientes"].items() if not c.get("encerrado")}
    # dedup tambem por TELEFONE (ultimos 8): dois cards do mesmo numero (com/sem
    # o nono digito) nao podem virar duas cobrancas (caso Jhonatan, 12/08)
    tels_ja = {"".join(c for c in (v.get("telefone") or "") if c.isdigit())[-8:]
               for v in fila["clientes"].values() if not v.get("encerrado")}
    for nome, tel in rows:
        tel8 = "".join(c for c in (tel or "") if c.isdigit())[-8:]
        if not nome or nome.strip().lower() in ja or (tel8 and tel8 in tels_ja):
            continue
        print(f"[enroll-etapa] '{nome}' esta em etapa de contrato no CRM; procurando docs no ZapSign...")
        try:
            enroll_cliente(nome.strip(), tel or "", _tentativas=1)
        except Exception as e:
            # instabilidade do ZapSign nao pode derrubar a rodada; a proxima tenta de novo
            print(f"[enroll-etapa] falhou para '{nome}': {str(e)[:150]}")


# ------------------------------------------------------------------ status
def status():
    fila = carregar()
    if not fila["clientes"]:
        print("Fila vazia.")
        return
    for nome, c in fila["clientes"].items():
        flag = "ENCERRADO" if c.get("encerrado") else "ativo"
        print(f"- {nome} | tel {c.get('telefone')} | envio {c.get('data_envio')} "
              f"| ultimo {c.get('ultimo_envio')} | dias {c.get('dias_enviados')} | {flag}")


# ------------------------------------------------------------------ processar
def processar():
    hoje = datetime.now(timezone.utc).date()
    modo = "ENVIAR (producao)" if ENVIAR else "DRY-RUN (simulacao)"
    print(f"=== COBRANCA DE ASSINATURA | {hoje} | {modo} ===\n")
    if ENVIAR and not _dapi_configurado():
        print("Sem credencial de envio (D-API: wa_numeros id 1 / DAPI_SESSION+DAPI_TOKEN) - nada enviado.")
        return

    fila = carregar()
    if not fila["clientes"]:
        print("Fila vazia. Inscreva com --enroll.")
        return

    for nome, c in fila["clientes"].items():
        if c.get("encerrado"):
            # (Advogada, 07/10/2026) parar de COBRAR nao pode parar de VERIFICAR: o Jonathan
            # assinou em 06/10 e o card ficou parado em "Contrato no ZapSign" porque a fila
            # dele estava encerrada (ele pediu para nao receber mensagem programada).
            # Aqui so marcamos que assinou, para o vigia mover o card. NADA e enviado a ele.
            if not c.get("assinou") and cliente_assinou_tudo(c):
                c["assinou"] = True
                c["assinou_em"] = str(hoje)
                salvar(fila)
                print(f"[{nome}] encerrado, mas ASSINOU no ZapSign: marcado, sem mensagem.")
                continue
            print(f"[{nome}] encerrado. skip.")
            continue

        # Pendencia do CLIENTE: so conta se o signatario que NAO e a Advogada
        # ainda nao assinou. Se o cliente ja assinou tudo (mesmo faltando a
        # assinatura da Dra. Advogada), ele nao deve mais ser cobrado.
        pend, abriu = 0, False
        for t in c["tokens"]:
            full = buscar_documento(t["token"])
            if not full:
                pend += 1
                continue
            cliente_assinou = True
            for s in (full.get("signers") or []):
                if "advogada" in (s.get("name") or "").lower():
                    continue
                if s.get("status") != "signed":
                    cliente_assinou = False
                if s.get("status") == "link-opened":
                    abriu = True   # abriu o link e parou (Advogada, 14/10/2026)
            if not cliente_assinou:
                pend += 1

        if pend == 0:
            _disparar(nome, c["telefone"], msg_assinou(c["nome"]), "ASSINOU")
            c["encerrado"] = True
            c["assinou"] = True  # distingue de encerramento por timeout (D+MAX)
            c["ultimo_envio"] = str(hoje)
            # aviso ao time (Titular, 11/08): comunicar quando o cliente assinar tudo
            if ENVIAR:
                zapi_enviar_texto(TEL_AVISO_TIME,
                    f"✅ {nome} ASSINOU todos os documentos da contratação "
                    f"(contrato, procuração e declaração). A varredura baixa os "
                    f"PDFs assinados para a pasta do cliente no Drive.")
            # transicao automatica: assinou -> comeca a cobranca de DOCUMENTOS
            try:
                from cobranca_documentos import enroll_cliente as _enroll_docs
                _enroll_docs(c["nome"], c.get("telefone", ""))
                print(f"[{nome}] -> iniciada a cobranca automatica de DOCUMENTOS.")
            except Exception as e:
                print(f"[{nome}] aviso: nao iniciou cobranca de documentos: {e}")
            continue

        # (Advogada, 14/10/2026) abriu e nao assinou: anota na 1a vez; se na rodada
        # seguinte (carencia de ABRIU_CARENCIA_H) continuar sem assinar, nasce
        # a tarefa URGENTE de ligacao para a vendedora do caso. Uma vez so.
        if abriu and not c.get("abriu_em"):
            print(f"[{nome}] abriu o link e ainda nao assinou. Anotado; se nao "
                  f"assinar ate a proxima rodada, vira tarefa de ligacao.")
            if ENVIAR:
                c["abriu_em"] = datetime.now(timezone.utc).isoformat()
                salvar(fila)
        elif abriu and not c.get("tarefa_ligacao_em"):
            visto = parse_dt(c.get("abriu_em"))
            horas = ((datetime.now(timezone.utc) - visto).total_seconds() / 3600
                     if visto else 99)
            if horas >= ABRIU_CARENCIA_H:
                _tarefa_ligacao(c, nome, "abriu",
                                "Abriu o link de assinatura no ZapSign (visto em "
                                f"{(c.get('abriu_em') or '')[:16]}) e ate agora nao concluiu. "
                                "Quem abre e nao assina costuma ter travado em alguma clausula.")
                if ENVIAR:
                    c["tarefa_ligacao_em"] = str(hoje)
                    salvar(fila)

        d0 = parse_dt(c.get("data_envio") + "T00:00:00+00:00") if c.get("data_envio") else None
        dia = (hoje - d0.date()).days if d0 else 1
        if dia < 1:
            print(f"[{nome}] enviado hoje (D0). aguarda D+1.")
            continue
        if c.get("ultimo_envio") == str(hoje):
            print(f"[{nome}] ja recebeu hoje. skip.")
            continue
        if dia > MAX_DIA:
            print(f"[{nome}] >> D+{dia}: PARANDO automatico. ALERTA INTERNO: cobrar manualmente / ligar.")
            # (14/10/2026) o "alerta interno" era so a linha acima no log e o
            # caso do Laerte ficou 3 semanas invisivel: regua esgotada agora
            # vira tarefa de ligacao para a vendedora do caso.
            _tarefa_ligacao(c, nome, "esgotou",
                            f"Levou {len(c.get('dias_enviados') or [])} lembretes automaticos de "
                            f"assinatura em {MAX_DIA} dias e nao assinou. A regua parou para nao "
                            "queimar o numero: agora e ligacao humana.")
            c["encerrado"] = True
            c["ultimo_envio"] = str(hoje)
            continue

        enviado = _disparar(nome, c["telefone"], msg(dia, c["nome"]) + bloco_links(c), f"D+{dia}")
        if enviado:  # so avanca o estado quando o envio REALMENTE acontece
            c["ultimo_envio"] = str(hoje)
            c.setdefault("dias_enviados", []).append(dia)
            _video_junto(c)
        elif not ENVIAR and c.get("links") and not c.get("video_tutorial"):
            print("  (dry-run: + video tutorial de assinatura junto)")

    salvar(fila)
    print("\n=== fim ===")


def _disparar(nome, telefone, texto, etapa):
    fig = FIGURINHAS.get(etapa, "")
    print(f"\n[{nome}] etapa {etapa} | tel: {telefone or 'SEM TELEFONE'}"
          + (f" | figurinha: {fig}" if fig else ""))
    print("  " + texto.replace("\n", "\n  "))
    if not ENVIAR:
        print("  (dry-run: nao enviado)" + ("  [+ figurinha]" if fig else ""))
        return
    if not telefone:
        print("  ERRO: sem telefone")
        return
    ok = zapi_enviar_texto(telefone, texto)
    print("  ENVIADO! (D-API)" if ok else "  ERRO no envio (D-API)")
    return ok


if __name__ == "__main__":
    if "--enroll" in sys.argv:
        enroll()
    elif "--status" in sys.argv:
        status()
    else:
        if "--enroll-etapa" in sys.argv:
            enroll_etapa()
        processar()
