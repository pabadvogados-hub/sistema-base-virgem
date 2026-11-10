# -*- coding: utf-8 -*-
"""
Cobranca automatica de ENVIO DE DOCUMENTOS (pos-assinatura) - Escritório Modelo Advogados.

Diferente da cobranca de assinatura, aqui NAO ha "assinou" no ZapSign como
trava. O cliente manda os documentos e a EQUIPE encerra manualmente quando
recebe tudo (--recebido). Cadencia mais gentil (a cada 2 dias), pois juntar
documento leva tempo.

Braco de envio = D-API (numero oficial, via wa_dapi.py) desde 24/09/2026.

Comandos:
  Inscrever (checklist padrao do caso comum):
    python cobranca_documentos.py --enroll --nome "Jetro Correa de Sousa" --tel "19995153482"
  Processar a fila (simulacao, padrao):
    python cobranca_documentos.py
  Processar de verdade:
    python cobranca_documentos.py --enviar
  Encerrar quando recebeu os documentos:
    python cobranca_documentos.py --recebido --nome "Jetro Correa de Sousa"
  Ver a fila:
    python cobranca_documentos.py --status
"""
import os, sys, io, json
from datetime import datetime, timezone

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
    load_dotenv("/opt/escritorio/config/.env")

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
INTERVALO = 2      # dias entre mensagens (cadencia gentil)
MAX_TOQUES = 5     # apos isso, para e alerta o time
FILA = os.path.join(os.path.dirname(__file__), "cobranca_documentos_fila.json")

FIG_TIPO = "sticker"
FIGURINHAS = {1: "", 2: "", 3: "", 4: "", 5: ""}

# Checklist padrao (caso trabalhista comum). Editavel por cliente no enroll (--checklist).
# REGRA DA PAM (11/09/2026): extrato bancario NAO entra na cobranca
# automatica. Cliente tem MEDO DE GOLPE quando pedem extrato pelo WhatsApp; na fase
# pre-processual confiamos no relato e a equipe pede o extrato PESSOALMENTE
# na fase seguinte.
# REGRA DO TITULAR (23/09/2026): o B.O. NAO entra no checklist padrao. Ele era o
# primeiro item da lista e so saia se a transcricao negasse assalto
# expressamente; como quase nenhuma conversa toca no assunto, ele sobrava e era
# cobrado ate 5 vezes de quem nunca foi assaltado (varios casos em 08/2026).
# Agora e o contrario: so entra com sinal de assalto no
# caso (ver `_teve_assalto`).
ITEM_BO = "Boletim de Ocorrencia do roubo/sequestro"
CHECKLIST_PADRAO = [
    "Holerites que voce tiver guardado (mesmo os poucos)",
    # ITEM SEPARADO EM DOIS (Advogada, 21/10/2026). Era um item so, "Termo de rescisao (TRCT) e
    # comprovante do que caiu na conta", e isso prendeu um cliente por tres dias em
    # Aguardando Documentos: sem registro em carteira o TRCT nao existe, mas o comprovante
    # existe (ele recebeu por Pix). A poda e por item e conservadora, entao
    # nao podia tirar metade, e a varredura so fechava o item com arquivo classificado como
    # TRCT: item impossivel de cumprir e impossivel de podar.
    "Termo de rescisao (TRCT)",
    "Comprovante do que caiu na conta (Pix, deposito ou extrato)",
    "Fotos, prints de conversa ou audios da epoca",
    "Nome de 1 ou 2 colegas que possam servir de testemunha",
]
# E-MAIL NAO E COBRADO (Advogada, 24/09/2026, reforcado em 25/09): estado civil e
# e-mail nao sao obrigatorios; saem da ligacao de fechamento quando houver.
_NAO_COBRAR = r"e-?mail"


def arg(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


# ------------------------------------------------------------------ mensagens
def _checklist_txt(checklist):
    return "\n".join(f"✅ {item}" for item in checklist)


def ja_chegou(nome):
    """Tipos que a varredura de provas ja classificou na pasta deste cliente."""
    try:
        with io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "varredura_provas_state.json"), encoding="utf-8") as f:
            st = (json.load(f).get("clientes") or {}).get(nome) or {}
    except Exception:
        return []
    # audio nao e documento: entra na pasta, mas nao se agradece como prova
    return [t for t in (st.get("tipos") or {}) if t != "audio"]


def msg(toque, nome, checklist, recebidos=None):
    p = (nome or "").split()[0].title() if nome else "amigo(a)"
    # EXTRATO BANCARIO NUNCA VAI PARA O CLIENTE (Advogada, 26/10/2026): "as vezes assusta
    # eles". Sai de qualquer texto daqui, inclusive dos cinco toques antigos; quem pede
    # e a Advogada, pela tarefa que a varredura abre quando o card anda.
    try:
        import varredura_provas as _vp
        checklist = [i for i in (checklist or []) if not _vp.RX_SO_RESPONSAVEL.search(i)]
    except Exception:
        pass
    # PEDIDO VAZIO NUNCA (Advogada, 26/10/2026): quem ja mandou alguma coisa ouve primeiro o
    # que chegou, e so entao o que falta. O texto e o MESMO do aceite da varredura, para
    # nao existirem duas versoes da mesma fala.
    chegou = ja_chegou(nome) if recebidos is None else list(recebidos)
    if chegou:
        try:
            import varredura_provas as _vp
            t = _vp.texto_aceite(nome, chegou, checklist, False, toque)
            if t:
                return t
        except Exception as e:
            print(f"  (aceite indisponivel, segue o texto padrao: {str(e)[:90]})")
    if toque == 1:
        return (f"{p}, agora que ja ta tudo assinado, bora dar entrada no seu caso o quanto antes? \U0001F680\U0001F69B\n\n"
                f"Olha so: assim que voce me mandar os documentos, eu ja PROTOCOLO a sua acao e o "
                f"seu caso comeca a andar na Justica. Ou seja, quanto antes chegar aqui, antes a "
                f"gente da o start pra voce buscar o que e seu \U0001F4AA\n\n"
                f"Separei a listinha (pode mandar foto pelo celular mesmo \U0001F4F1):\n\n"
                + _checklist_txt(checklist) +
                f"\n\nMe manda o que voce ja tiver AINDA HOJE, que o resto a gente vai completando. "
                f"Qualquer duvida de onde achar, chama aqui! \U0001F91D\nEscritório Modelo Advogados")
    if toque == 2:
        return (f"Oi, {p}! \U0001F44B Seu caso ta pronto pra entrar, ta faltando so os documentos \U0001F4C2\n\n"
                f"Se conseguir priorizar UM, manda o primeiro da sua listinha: *{(checklist[0] if checklist else 'o que voce ja tiver')}* \U0001F4B0, e ele "
                f"que fecha o seu calculo e me libera pra protocolar. Consegue me mandar ainda hoje? \U0001F64F\n\n"
                f"Pode ser foto ou print, do jeito que for mais rapido pra voce!\nEscritório Modelo Advogados")
    if toque == 3:
        return (f"{p}, tudo certo? \U0001F440 Cada dia que passa e um dia a mais pra sua acao "
                f"comecar a correr, entao bora nao deixar parado \U0001F3C3\U0001F4A8\n\n"
                f"Nao precisa ser nada formatado, foto pelo celular resolve. Me manda os documentos "
                f"hoje que amanha seu caso ja pode estar entrando! \U0001F680\n\n"
                f"Ficou com duvida em algum? Me fala que eu te explico na hora.\nEscritório Modelo Advogados")
    if toque == 4:
        return (f"Oi, {p}! \U0001F4DE Vamos destravar isso juntos pra sua acao entrar logo?\n\n"
                f"Se algum documento da listinha estiver dificil de achar, me diz qual que eu te explico "
                f"onde pegar, passo a passo \U0001F60A Manda o que ja tiver hoje e o resto a gente "
                f"resolve junto.\nEscritório Modelo Advogados")
    return (f"{p}, seu caso ta so esperando os documentos pra entrar, e eu nao quero te deixar "
            f"perdendo tempo \U0001F64F Vou pedir pra uma pessoa do time te dar uma forca pessoalmente "
            f"pra a gente reunir tudo rapido e protocolar. Voce e importante pra gente! \U0001F49B\n"
            f"Escritório Modelo Advogados")


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


# ------------------------------------------------------------------ enroll / recebido
def _teve_assalto(cid, nome="", tel=""):
    """So pede B.O. quando o caso tem assalto/roubo/sequestro de verdade.

    Olha, nesta ordem: o campo `assalto` da ficha de entrevista (que o
    pos_ligacao preenche a partir da ligacao) e, depois, as notas da conversa.
    Na duvida devolve False: e melhor deixar de pedir um documento que existe do
    que cobrar cinco vezes um documento que nunca existiu.
    """
    import re as _re
    import sqlite3 as _sq
    if not cid:
        return False
    palavras = _re.compile(r"assalt|roubo|roubad|sequestr|latroc|rendera?m|arma em punho", _re.I)
    try:
        con = _sq.connect("/opt/escritorio/SISTEMA/backend/sistema_esc.db", timeout=15)
        r = con.execute("select valor from ficha_entrevista where conversa_id=? and campo='assalto'",
                        (cid,)).fetchone()
        if r and (r[0] or "").strip() and not _re.search(r"^\s*(nao|n/a|nunca|-)\b", r[0], _re.I):
            con.close()
            return True
        n = con.execute("select notas from wa_conversas where id=?", (cid,)).fetchone()
        con.close()
        return bool(n and n[0] and palavras.search(n[0]))
    except Exception:
        return False

def enroll_cliente(nome, tel="", checklist=None, cid=None, podar=True):
    """Inscreve o cliente na cobranca de documentos.

    Quando o chamador nao informa `checklist`, o padrao e PODADO pela
    transcricao da reuniao de fechamento (`checklist_documentos.montar`), para
    nao cobrar documento que o cliente ja disse que nao existe. Foi assim que a
    automacao pediu B.O. de roubo a um cliente por cinco toques, em 08/2026,
    depois de ele afirmar na reuniao que nunca foi assaltado. A poda so remove
    item que a transcricao contradiz EXPRESSAMENTE e, em qualquer falha, mantem
    o checklist padrao - nunca fica pior que antes.
    """
    itens = list(checklist) if checklist else None
    origem = "informado pelo chamador" if checklist else None
    # B.O. nao vem no padrao: entra so se o caso tiver assalto (regra de 23/09/2026)
    bo_cabe = _teve_assalto(cid, nome, tel)
    if itens is None and podar:
        try:
            from checklist_documentos import montar
            itens, origem = montar(nome, tel, list(CHECKLIST_PADRAO), cid=cid)
            if not itens:
                origem = "pendente: " + (origem or "?")
        except Exception as e:
            origem = f"pendente: erro {str(e)[:80]}"
    elif itens is None:
        itens, origem = list(CHECKLIST_PADRAO), "padrao (poda desligada)"
    # O B.O. e o unico item que entra por excecao, nunca por padrao: so quando a
    # ficha ou a conversa mostram assalto/roubo/sequestro. Antes era o contrario
    # e cliente que nunca foi assaltado levava cinco cobrancas.
    if bo_cabe and itens is not None and ITEM_BO not in itens:
        itens = [ITEM_BO] + list(itens)
        origem = (origem or "") + " + B.O. (caso tem assalto)"
    fila = carregar()
    fila["clientes"][nome] = {
        "nome": nome,
        "telefone": tel or "",
        "checklist": itens,
        "checklist_origem": origem,
        "data_inicio": str(datetime.now(timezone.utc).date()),
        "ultimo_envio": None,
        "toques": 0,
        "encerrado": False,
    }
    salvar(fila)
    print(f"[docs] inscrito: {nome} | tel {tel} | "
          + (f"{len(itens)} itens ({origem})" if itens else f"SEM LISTA ({origem})"))
    return True


def enroll():
    nome = arg("--nome")
    if not nome:
        print("Uso: --enroll --nome \"Nome Completo\" [--tel \"19999999999\"]")
        return
    enroll_cliente(nome, arg("--tel") or "")


def recebido():
    nome = arg("--nome")
    fila = carregar()
    c = fila["clientes"].get(nome)
    if not c:
        print(f"'{nome}' nao esta na fila de documentos.")
        return
    c["encerrado"] = True
    salvar(fila)
    print(f"[docs] ENCERRADO: {nome} (documentos recebidos). Nao sera mais cobrado.")


def status():
    fila = carregar()
    if not fila["clientes"]:
        print("Fila vazia.")
        return
    for nome, c in fila["clientes"].items():
        flag = "ENCERRADO" if c.get("encerrado") else "ativo"
        print(f"- {nome} | tel {c.get('telefone')} | inicio {c.get('data_inicio')} "
              f"| ultimo {c.get('ultimo_envio')} | toques {c.get('toques')} | {flag}")


# ------------------------------------------------------------------ processar
def _inicial_pronta(tel):
    """True se o card do cliente ja esta na Producao em Revisao, Distribuicao ou Distribuida."""
    import sqlite3 as _sq
    d = "".join(ch for ch in str(tel or "") if ch.isdigit())[-8:]
    if len(d) < 8:
        return False
    try:
        con = _sq.connect("file:/opt/escritorio/SISTEMA/backend/sistema_esc.db?mode=ro", uri=True, timeout=15)
        r = con.execute("select etapa from wa_conversas where numero_id=1 and pipeline_id=18 and "
                        "contato_telefone like ? order by atualizado_em desc limit 1", ("%" + d,)).fetchone()
        con.close()
        return bool(r and r[0] in ("Revisao", "Distribuicao", "Distribuida"))
    except Exception:
        return False


def processar():
    hoje = datetime.now(timezone.utc).date()
    modo = "ENVIAR (producao)" if ENVIAR else "DRY-RUN (simulacao)"
    print(f"=== COBRANCA DE DOCUMENTOS | {hoje} | {modo} ===\n")
    if ENVIAR and not _dapi_configurado():
        print("Sem credencial de envio (D-API: wa_numeros id 1 / DAPI_SESSION+DAPI_TOKEN) - nada enviado.")
        return

    fila = carregar()
    if not fila["clientes"]:
        print("Fila vazia. Inscreva com --enroll.")
        return

    for nome, c in fila["clientes"].items():
        if c.get("encerrado"):
            print(f"[{nome}] encerrado. skip.")
            continue

        # INICIAL JA PRONTA (Titular, 29/09/2026: "o Heitor Valença pode parar de cobrar, ja
        # finalizamos a inicial dele"): card na Producao em Revisao, Distribuicao ou Distribuida
        # = documento nao faz mais falta. Encerra e nao manda nada.
        if _inicial_pronta(c.get("telefone")):
            c["encerrado"] = True
            print(f"[{nome}] inicial ja pronta (card em Revisao ou depois). cobranca encerrada.")
            continue

        # LISTA DO CASO (Advogada, 11/09/2026): sem lista confiavel NAO cobra as
        # cegas; reanalisa a cada rodada e avisa o time enquanto pender.
        if not _garantir_checklist(nome, c):
            continue

        # O QUE O CLIENTE JA DISSE QUE NAO TEM SAI DA LISTA (caso Abel, 25/09):
        # cliente sem registro escreveu duas vezes "nao tenho holerite" e
        # "ninguem vai querer ser testemunha" e a cobranca seguiu pedindo os dois.
        _podar_pelo_que_o_cliente_disse(nome, c)
        if not c.get("checklist"):
            c["encerrado"] = True
            print(f"[{nome}] lista zerada pelo que o cliente escreveu; cobranca encerrada.")
            # mandou o que tinha e disse que nao tem o resto: vai direto para a
            # producao da inicial, sem esperar a varredura do dia seguinte (Advogada, 25/09)
            try:
                import varredura_provas as _vp
                cid = _vp.conversa_por_tel(c.get("telefone"))
                if cid:
                    _vp.mover_para_producao(cid, ENVIAR, quem="auto:cobranca-documentos",
                                            origem="script:cobranca_documentos")
                    if ENVIAR:
                        zapi_enviar_texto(TEL_TIME,
                            f"📁 {nome}: mandou o que tinha e disse que não tem o resto. "
                            f"Card movido para Produção > Produção da Inicial.")
            except Exception as e:
                print(f"[{nome}] aviso: nao movi para a producao: {str(e)[:120]}")
            continue

        # JA MANDOU (Tobias, 10 e 12/10/2026): pausa e confere, em vez de insistir
        if c.get("pausa_ja_mandou"):
            print(f"[{nome}] pausado desde {c['pausa_ja_mandou']}: cliente disse que ja mandou "
                  "(conferencia humana). skip.")
            # pausa nao pode virar parada esquecida: relembra a Dra. Advogada a cada 3 dias
            ult = c.get("pausa_alertada") or c["pausa_ja_mandou"]
            if ENVIAR and (hoje - datetime.fromisoformat(ult).date()).days >= 3:
                zapi_enviar_texto(TEL_TIME,
                    f"📂 {nome}: a cobrança segue pausada desde {c['pausa_ja_mandou']} (ele disse "
                    f"que já mandou). Falta conferir a pasta e mover o card, ou pedir para "
                    f"retomar a cobrança.")
                c["pausa_alertada"] = str(hoje)
            continue
        prova = _disse_que_ja_mandou(c.get("telefone"), c.get("ultimo_envio") or c.get("data_inicio"))
        if prova:
            c["pausa_ja_mandou"] = str(hoje)
            print(f"[{nome}] PAUSADO: cliente disse que ja mandou | \"{prova[:120]}\"")
            if ENVIAR:
                zapi_enviar_texto(TEL_TIME,
                    f"📂 {nome} disse que já mandou os documentos (\"{prova[:100]}\"). Parei a "
                    f"cobrança dele para não insistir. Pela lista ainda falta: "
                    f"{'; '.join(c.get('checklist') or [])}. Conferir a pasta (o que a visão não "
                    f"reconheceu fica como OUTRO_) e, se estiver completo, mover o card para "
                    f"Produção da Inicial.")
            continue

        # respeita a cadencia (a cada INTERVALO dias)
        if c.get("ultimo_envio"):
            try:
                ult = datetime.fromisoformat(c["ultimo_envio"]).date()
                if (hoje - ult).days < INTERVALO:
                    print(f"[{nome}] dentro do intervalo ({INTERVALO}d). skip.")
                    continue
            except Exception:
                pass

        toque = int(c.get("toques", 0)) + 1
        if toque > MAX_TOQUES:
            print(f"[{nome}] >> toque {toque}: PARANDO. ALERTA INTERNO: cobrar documentos manualmente.")
            c["encerrado"] = True
            continue

        enviado = _disparar(nome, c["telefone"], msg(toque, c["nome"], c["checklist"]), toque)
        if enviado:
            c["ultimo_envio"] = str(hoje)
            c["toques"] = toque

    salvar(fila)
    print("\n=== fim ===")


TEL_TIME = os.getenv("TEL_ALERTA_EQUIPE", "")

# item do checklist -> como o cliente se refere a ele
_ITEM_PALAVRA = [
    (r"holerite", r"h?olerite|contra.?cheque|recibo de pagamento"),  # "olerite" (Oziel)
    (r"rescisao|trct", r"rescis|trct"),
    (r"testemunha", r"testemunha"),
    (r"e-?mail", r"e-?mail"),
]
_NEGA = (r"n[aã]o\s+(tenho|tinha|tem|tive|recebi|recebia|davam|dava|existe|existia|"
         r"vai\s+querer|v[aã]o\s+querer|quer(em)?|conhe[cç]o)|nunca\s+(tive|recebi|me\s+deram)|"
         r"ningu[eé]m\s+(vai|quer)|n[aã]o\s+me\s+davam|"
         # Oziel, 07 e 08/10/2026: "Eles não me deram os olerites", "não me deram a rescisão"
         r"n[aã]o\s+me\s+(deram|deu(?!\s+tempo)|entregaram|entregou|passaram|passou|mandaram)|"
         r"n[aã]o\s+(deram|entregaram)")

# Cliente que diz que ja mandou (Tobias, 10 e 12/10/2026: "Mais eu já mandei", "eu mandei
# várias coisas e documentos. E sempre diz q falta algo") nao leva mais lembrete: a cobranca
# pausa e a Dra. Advogada confere a pasta (o que a visao nao reconheceu fica como OUTRO_).
_JA_MANDEI = (r"j[aá]\s+(te\s+|lhe\s+|os\s+|as\s+)?(mandei|enviei|encaminhei|passei)|"
              r"(mandei|enviei)\s+(tudo|v[aá]rias|v[aá]rios)")


def _disse_que_ja_mandou(tel, desde):
    """Texto do cliente, a partir do dia do ultimo lembrete, dizendo que ja mandou."""
    import sqlite3 as _sq
    import re as _re
    t8 = "".join(ch for ch in (tel or "") if ch.isdigit())[-8:]
    if not t8 or not desde:
        return None
    try:
        con = _sq.connect("file:/opt/escritorio/SISTEMA/backend/sistema_esc.db?mode=ro",
                          uri=True, timeout=15)
        cids = [r[0] for r in con.execute("select id, contato_telefone from wa_conversas")
                if "".join(ch for ch in (r[1] or "") if ch.isdigit())[-8:] == t8]
        textos = []
        for cid in cids:
            textos += [r[0] for r in con.execute(
                "select conteudo from wa_mensagens where conversa_id=? and origem='contato' "
                "and tipo in ('texto','audio') and criado_em>=? and coalesce(conteudo,'')<>''",
                (cid, str(desde)))]
        con.close()
    except Exception:
        return None
    return next((t for t in textos if _re.search(_JA_MANDEI, t, _re.I)), None)


def _textos_do_cliente(tel):
    """Textos e audios transcritos que o cliente mandou (conversa pelo telefone)."""
    import sqlite3 as _sq
    t8 = "".join(ch for ch in (tel or "") if ch.isdigit())[-8:]
    if not t8:
        return []
    try:
        con = _sq.connect("file:/opt/escritorio/SISTEMA/backend/sistema_esc.db?mode=ro",
                          uri=True, timeout=15)
        cids = [r[0] for r in con.execute("select id, contato_telefone from wa_conversas")
                if "".join(ch for ch in (r[1] or "") if ch.isdigit())[-8:] == t8]
        out = []
        for cid in cids:
            out += [r[0] for r in con.execute(
                "select conteudo from wa_mensagens where conversa_id=? and origem='contato' "
                "and tipo in ('texto','audio') and coalesce(conteudo,'')<>''", (cid,))]
        con.close()
        return out
    except Exception:
        return []


def _podar_pelo_que_o_cliente_disse(nome, c):
    import re as _re
    itens = [i for i in (c.get("checklist") or []) if not _re.search(_NAO_COBRAR, i, _re.I)]
    if len(itens) != len(c.get("checklist") or []):
        print(f"[{nome}] e-mail tirado da lista (nao e obrigatorio).")
        c["checklist"] = itens
    if not itens:
        return
    textos = _textos_do_cliente(c.get("telefone"))
    tirados = []
    for item in itens:
        for rx_item, rx_palavra in _ITEM_PALAVRA:
            if not _re.search(rx_item, item, _re.I):
                continue
            prova = next((t for t in textos if _re.search(rx_palavra, t, _re.I)
                          and _re.search(_NEGA, t, _re.I)), None)
            if prova:
                tirados.append((item, prova))
            break
    if not tirados:
        return
    c["checklist"] = [i for i in itens if i not in {x for x, _ in tirados}]
    c.setdefault("podados_pelo_cliente", []).extend(x for x, _ in tirados)
    for item, prova in tirados:
        print(f"[{nome}] REMOVIDO pelo que o cliente escreveu: {item} | \"{prova[:120]}\"")
    if ENVIAR:
        zapi_enviar_texto(TEL_TIME,
            f"📝 {nome}: tirei da cobranca o que o cliente disse que nao tem: "
            + "; ".join(f"{i} (\"{p[:80]}\")" for i, p in tirados)
            + ". Se precisar de prova substituta, combinar com ele pessoalmente.")


def _garantir_checklist(nome, c):
    """True = pode cobrar; False = segura este cliente nesta rodada.

    Regra da Advogada (11/09/2026, caso a equipe): a lista padrao e do caso COMUM;
    cliente sem analise do caso nao recebe cobranca as cegas. Reanalisa a
    cada rodada (transcricao OU ficha); cliente antigo ja em cobranca segue
    com a lista atual, mas tambem e reanalisado e o time e avisado.
    """
    origem = c.get("checklist_origem") or ""
    if c.get("checklist") and origem and not origem.startswith(("pendente", "padrao")):
        return True
    try:
        from checklist_documentos import montar
        itens, motivo = montar(nome, c.get("telefone") or "", list(CHECKLIST_PADRAO))
    except Exception as e:
        itens, motivo = None, f"erro {str(e)[:80]}"
    if itens:
        antes = len(c.get("checklist") or [])
        c["checklist"], c["checklist_origem"] = itens, motivo
        print(f"[{nome}] lista do caso pronta: {antes or 'sem'} -> {len(itens)} itens ({motivo})")
        return True
    # sem relato do caso NAO se cobra, nem quem ja vinha sendo cobrado: foi
    # exatamente assim que uma cliente gestante levou a lista comum
    _alertar_pendencia(nome, c, motivo, False)
    return False


def _alertar_pendencia(nome, c, motivo, ja_cobrando):
    hoje = datetime.now(timezone.utc).date()
    try:
        recente = c.get("alerta_pendencia") and \
            (hoje - datetime.fromisoformat(c["alerta_pendencia"]).date()).days < 3
    except Exception:
        recente = False
    rotulo = "SEGUREI a cobranca (nada enviado)"
    print(f"[{nome}] sem lista do caso ({motivo}); {rotulo}")
    if ENVIAR and not recente:
        ok = zapi_enviar_texto(TEL_TIME,
            f"⚠️ Cobranca de documentos: nao consegui montar a lista do caso "
            f"de *{nome}* ({motivo}). {rotulo}. Suba a transcricao da reuniao "
            f"(ou cole o link na ficha do card no CRM) que eu monto a lista "
            f"certa e sigo sozinho.")
        if ok:
            c["alerta_pendencia"] = str(hoje)


def _disparar(nome, telefone, texto, toque):
    fig = FIGURINHAS.get(toque, "")
    print(f"\n[{nome}] toque {toque} | tel: {telefone or 'SEM TELEFONE'}")
    print("  " + texto.replace("\n", "\n  "))
    if not ENVIAR:
        print("  (dry-run: nao enviado)")
        return False
    if not telefone:
        print("  ERRO: sem telefone")
        return False
    ok = zapi_enviar_texto(telefone, texto)
    print("  ENVIADO! (D-API)" if ok else "  ERRO no envio (D-API)")
    return ok


if __name__ == "__main__":
    if "--enroll" in sys.argv:
        enroll()
    elif "--recebido" in sys.argv:
        recebido()
    elif "--status" in sys.argv:
        status()
    else:
        processar()
