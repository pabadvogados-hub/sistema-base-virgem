# -*- coding: utf-8 -*-
"""
VIGIA DA CONTRATACAO — pipeline "Contratação" (Sistema Escritório Modelo, VPS).

Desenho ditado pelo Titular em 11/08/2026: o time fecha com o lead e o coloca na
etapa "Enviar Documentos". Dali em diante e tudo automatico:

  1. PEDIDO    entrou na etapa -> mensagem pedindo CNH + comprovante de
               residencia (Z-API).
  2. RECEPCAO  cada foto/PDF que o cliente manda e baixado NA HORA (a URL da
               Z-API expira) e classificado por visao de IA (Claude):
               cnh | comprovante_residencia | outro. Recebeu um e falta outro,
               a automacao avisa o que falta; incompleto ha 1 dia, lembra 1x/dia.
  3. INTAKE    CNH + comprovante reconhecidos -> confirma ao cliente, move o
               card para "Documentos Conferidos", acha a TRANSCRICAO do
               fechamento no Drive e roda o run_intake local (ficha, contrato,
               procuracao, declaracao -> ZapSign).
  4. LINKS     intake ok -> inscreve na cobranca_assinatura, envia os links de
               assinatura pela Z-API e move o card para "Contrato no ZapSign".
  5. ASSINOU   a cobranca_assinatura detecta a assinatura (varredura 3x/dia);
               o vigia ve o encerramento com assinatura e move para "Assinado"
               (a cobranca de documentos/provas ja entra sozinha).

Sem transcricao no Drive ou intake com erro -> alerta no WhatsApp do time
(alerta_telefone) e o card fica parado para acao humana; transcricao que
aparecer depois e pega na proxima rodada.

USO (cron a cada 10 min na VPS, com flock):
  py SYNC/contratacao_watcher.py [--executar]     # sem flag = DRY-RUN
  py SYNC/contratacao_watcher.py --status
Config: contratacao_config.json | Estado: contratacao_state.json (mesma pasta).
"""
import argparse
import base64
import contextlib
import importlib.util
import io
import json
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    TZ = None

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(os.getenv(
    "ESC_CONFIG_ENV",
    r"C:\AUTOMAÇÕES\AUTOMACOES_PROJETO_COMPLETO_2026-07-19\config\.env"))
if os.path.exists("/opt/escritorio/config/.env"):
    load_dotenv("/opt/escritorio/config/.env")  # ANTHROPIC_API_KEY / ZAPSIGN na VPS

import requests
sys.path.insert(0, "/opt/escritorio/SYNC")  # 24/09/2026: saida unica de WhatsApp (D-API)
import wa_dapi  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
sys.path.insert(0, _DIR)
import transcricao_drive as td  # onde a transcricao mora (compartilhado c/ producao)

CONFIG_PATH = os.path.join(_DIR, "contratacao_config.json")
STATE_PATH = os.path.join(_DIR, "contratacao_state.json")

DB = os.getenv("SISTEMA_DB", "/opt/escritorio/SISTEMA/backend/sistema_esc.db")
UPLOADS = os.getenv("SISTEMA_UPLOADS", "/opt/escritorio/SISTEMA/backend/uploads")
PIPELINE_CONTRATACAO = 20
# nomes EXATOS como gravados no wa_stages (12/08)
ETAPA_PEDIDO = "Fechou! Boas vindas e Documentos para contrato"
ETAPA_CONFERIDO = "Fase de Contratação"
ETAPA_ZAPSIGN = "Contrato no ZapSign"
ETAPA_ASSINADO = "Assinado"
# Etapa de LIGACAO (Advogada, 27/08): passados DIAS_ATE_LIGACAO dias de cobranca
# automatica sem os documentos, a automacao PARA de mandar mensagem e entrega o
# caso para um humano ligar. Mensagem para quem nao responde ha dias so queima o
# numero (o #481 levou 76 lembretes e nunca voltou).
# E COLUNA do proprio quadro da contratacao, encostada no "Fechou!" (Advogada, 27/08:
# "a ligacao nao e modulo a parte"): o funil inteiro se le num quadro so.
ETAPA_LIGAR = "Ligação: reaquecimento de documentos"
DIAS_ATE_LIGACAO = 5
# VIDEO DO 3o DIA e TAREFA PARA QUEM FECHOU (Advogada, 15/10/2026). Os lembretes seguem como estavam (uma vez
# por dia, a partir das 7h, todo dia: "manter como esta"). O que muda:
#   - no 3o DIA UTIL depois do pedido, se o cliente nao mandou NADA, o lembrete daquele dia vira o video
#     do 3o dia (comercial em giz, aprovado por ela), so para card de motorista;
#   - a ida para a ligacao continua em 5 dias corridos, mas so em dia util (tarefa nunca cai em fim de
#     semana nem feriado, regra de 15/10) e nunca no mesmo dia do video, e agora nasce TAREFA URGENTE para
#     a vendedora que fechou ligar (antes ia so um aviso no WhatsApp da Advogada).
DIA_VIDEO = 3
VIDEO_DIA3 = os.getenv("RT_VIDEO_DIA3", "https://sistema.escritorio.adv.br/media/rt_dia3_falta_cnh.mp4")
TEXTO_VIDEO_D3 = ("{nome}, separei esse vídeo rapidinho pra você. 👆 Na próxima parada, manda aqui a foto "
                  "da sua CNH (pode ser a digital) e o seu endereço, que eu já preparo o seu contrato.")
FERIADOS = {"2026-10-12", "2026-11-02", "2026-11-15", "2026-11-20", "2026-12-25", "2027-01-01", "2027-03-26",
            "2027-04-21", "2027-05-01", "2027-09-07", "2027-10-12", "2027-11-02", "2027-11-15", "2027-11-20",
            "2027-12-25"}   # feriados nacionais
import sys as _s, os as _o
_s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))), 'INTEGRACOES'))
import escritorio_config as CFG  # noqa: E402
VENDEDORAS = {e: v[1] for e, v in CFG.vendedores().items()}   # VENDEDORES_EMAILS no .env
# Coluna de perda, no fim do mesmo quadro: e ali que a Advogada le quantos leads o
# fluxo perde do "fechou" ate o contrato assinado.
ETAPA_PERDIDO = "Perdido"
FALTA_CURTO = {"cnh": "CNH", "comprovante_residencia": "comprovante de residência"}
LEMBRETE_HORAS = 3          # (historico) lembrete de 3 em 3h - substituido em 27/08
LEMBRETE_POR_DIA = 1        # 1 UNICA mensagem por dia pedindo os documentos (Advogada, 27/08)
LEMBRETE_JANELA = (7, 22)   # so lembra em horario humano
REGISTRO_LEITURA = os.path.join(_DIR, "leitura_conversa_state.json")
                            # o que ja foi lido de cada conversa,
                            # por id de MIDIA: nao relemos nada
MAX_TENTATIVAS_INTAKE = 3   # falha de IVA nao trava o card pra sempre (12/08)
LOG_INTAKE = os.getenv("ESC_LOG_INTAKE", "/var/log/escritorio-intake-detalhe.log")

MODELO_VISAO = "claude-sonnet-4-5"

_tk = os.getenv("ESC_TOKEN_GOOGLE", "")
TOKEN_GOOGLE = _tk or (
    "/opt/escritorio/config/token.json" if os.path.exists("/opt/escritorio/config/token.json")
    else r"C:\AUTOMAÇÕES\AUTOMACOES_PROJETO_COMPLETO_2026-07-19\config\token.json")

# ---- mensagens ao cliente (estilo Advogada: simples, sem travessao) --------------
MSG_PEDIDO = (
    "Oi {nome}! Agora vamos preparar o seu contrato.\n\n"
    "Para isso, preciso de duas coisas:\n"
    "📄 Foto da sua CNH (frente e verso)\n"
    "🏠 Um comprovante de residência recente (conta de luz, água ou telefone)\n\n"
    "Pode mandar foto aqui mesmo pelo WhatsApp. Assim que chegar, eu confiro "
    "na hora e já monto tudo para você.")
MSG_PARCIAL = (
    "Boa, {nome}! {recebido} chegou certinho aqui. ✅\n\n"
    "Agora só falta {faltando} para eu montar o seu contrato. Pode mandar por "
    "aqui mesmo.")
MSG_COMPLETO = (
    "Perfeito, {nome}! Recebi a sua CNH e o seu comprovante de residência. "
    "Está tudo certo. ✅\n\n"
    "Agora vou preparar o seu contrato, a procuração e a declaração. Assim que "
    "ficarem prontos, te mando o link para assinar aqui mesmo pelo celular. É "
    "rapidinho.")
MSG_COMPLETO_ENDERECO = (
    "Perfeito, {nome}! Recebi a sua CNH e anotei o endereço que você escreveu "
    "aqui. Está tudo certo. ✅\n\n"
    "Agora vou preparar o seu contrato, a procuração e a declaração. Assim que "
    "ficarem prontos, te mando o link para assinar aqui mesmo pelo celular. "
    "Depois, na organização dos documentos do processo, te peço um comprovante "
    "de residência em arquivo (conta de luz, água ou telefone), combinado?")
MSG_LEMBRETE = (
    "Oi {nome}! Para preparar o seu contrato ainda preciso de: {faltando}.\n\n"
    "Pode mandar foto aqui pelo WhatsApp quando conseguir. Qualquer dúvida, me "
    "chama.")
# lembretes de 3 em 3 horas, em rodizio (persuasao amigavel, Advogada 12/08)
LEMBRETES_3H = [
    "Oi {nome}! Você deu um passo que muitos colegas ainda não tiveram coragem "
    "de dar: levantou a voz por essa categoria. Parabéns! 🚛\n\n"
    "Para o seu caso andar, agora só falta {faltando}.\n\n"
    "Manda foto aqui mesmo pelo WhatsApp que eu já dou o próximo passo na hora.",
    "{nome}, seu processo está quase pronto para avançar! Falta só {faltando}.\n\n"
    "É rapidinho: foto pelo celular e pronto. Quanto antes chegar aqui, mais "
    "rápido o seu contrato fica pronto e a gente entra com tudo por você.",
    "Oi {nome}! Não quero deixar o seu caso esperando.\n\n"
    "Assim que você me mandar {faltando}, eu preparo seus documentos na hora e "
    "te aviso aqui. Estamos juntos nessa, combinado?",
]
MSG_ATUALIZACAO_DIARIA = (
    "Oi {nome}! Passando para te manter por dentro: seus documentos de "
    "contratação estão em elaboração pela nossa equipe. Assim que ficarem "
    "prontos, você recebe por aqui o link para assinar pelo celular.\n\n"
    "Qualquer dúvida nesse meio tempo, é só me chamar.")
MSG_LINKS = (
    "{nome}, seu contrato ficou pronto! 🎉\n\n"
    "Assina aqui pelo celular, leva uns 2 minutos:\n\n{links}\n\n"
    "No fim vai chegar um código no seu WhatsApp, de um número do ZapSign. É normal, é só "
    "digitar ele para confirmar a assinatura.\n\n"
    "Assinou, a gente já dá andamento no seu caso. Qualquer dificuldade com o "
    "link, me fala que eu te ajudo.")

ROTULO = {"cnh": "a sua CNH",
          "comprovante_residencia": "o seu comprovante de residência"}
FALTA = {"cnh": "a foto da sua CNH (frente e verso)",
         "comprovante_residencia": "o comprovante de residência (conta de luz, água ou telefone)"}


def _cfg():
    try:
        return json.load(open(CONFIG_PATH, encoding="utf-8"))
    except Exception:
        return {}


def _estado():
    if os.path.exists(STATE_PATH):
        return json.load(open(STATE_PATH, encoding="utf-8"))
    return {"conversas": {}}


def _salvar(e):
    json.dump(e, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _hoje():
    return (datetime.now(TZ).date() if TZ else date.today()).isoformat()


def _agora():
    return (datetime.now(TZ) if TZ else datetime.now()).isoformat()


# ---- Z-API ------------------------------------------------------------------

def _ddi(tel):
    d = "".join(c for c in str(tel) if c.isdigit())
    return d if d.startswith("55") else "55" + d


def zapi_texto(telefone, texto):
    # 24/09/2026: a Z-API caiu; envio pelo numero oficial na D-API (SYNC/wa_dapi.py).
    # Mesmo contrato de antes: True so quando a D-API confirmou o envio.
    try:
        ok, info = wa_dapi.enviar_texto(_ddi(telefone), texto)
        if not ok:
            print(f"    (d-api erro: {str(info)[:120]})")
        return ok
    except Exception as e:
        print(f"    (d-api erro: {str(e)[:120]})")
        return False


def _video_nativo(telefone, url, texto):
    """Video nativo pela D-API e, 4 s depois, o texto numa mensagem propria: a legenda que vai junto com o
    video nao aparece para o cliente (armadilha de 25/09). Mesma receita do kit do GR. Se a rota de video
    falhar, o video vai como documento mp4 e o texto segue igual."""
    try:
        ok, _j, _st = wa_dapi._post("messages/send/video", {"to": wa_dapi.destino(telefone), "video": url})
        if not ok:
            print("    (rota de vídeo falhou, mando como documento)")
            ok = bool(wa_dapi.enviar_documento(telefone, url, "video.mp4", legenda="", ext="mp4")[0])
        if not ok:
            return False
        time.sleep(4)
        if not zapi_texto(telefone, texto):
            print("    (o vídeo saiu, o texto depois dele não: conferir a conversa)")
        return True
    except Exception as e:
        print(f"    (vídeo falhou: {str(e)[:100]})")
        return False


def _dia_util(d):
    return d.weekday() < 5 and d.isoformat() not in FERIADOS


def _dias_uteis_desde(quando, hoje=None):
    """Dias uteis desde o pedido: o dia do pedido nao conta, o 1o dia util seguinte e o dia 1."""
    try:
        d = datetime.fromisoformat(str(quando)).date()
    except Exception:
        return 0
    hoje = hoje or (datetime.now(TZ).date() if TZ else date.today())
    n = 0
    while d < hoje:
        d = date.fromordinal(d.toordinal() + 1)
        if _dia_util(d):
            n += 1
    return n


def _e_motorista(cid):
    """O video do 3o dia fala de cabine, patio e holerite: nao se aplica a esta operacao. Quem nao e motorista
    (operador de betoneira, gestante, madeira) segue com o lembrete de texto."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    try:
        r = con.execute("select coalesce(tag,''), coalesce(origem,'') from wa_conversas where id=?",
                        (cid,)).fetchone()
    finally:
        con.close()
    if not r:
        return False
    tag, origem = r[0].upper(), r[1].lower()
    # o video de 3o dia era de outra operacao; aqui todo reclamante segue com o lembrete de texto
    return False


REF_CONFIRMA = "confirma-produto:%s"


def _veio_da_campanha_gr(cid):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    try:
        r = con.execute("select lower(coalesce(origem,'')) from wa_conversas where id=?", (cid,)).fetchone()
    finally:
        con.close()
    return bool(r) and "desbloqueio gr" in r[0]


def _produto_confirmado(cid):
    """Lead da campanha do desbloqueio na coluna do trabalhista: o kit so sai depois que a vendedora
    confirma que ele fechou a ACAO TRABALHISTA, concluindo a tarefa que a ponte do GR abre
    (gr_contratacao.fase_ponte). Advogada, 21/10/2026: "trocar por pergunta". Antes a ponte movia o card
    sozinha, e o Saulo (2094), que veio do GR e virou trabalhista, levou o kit do GR."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    try:
        r = con.execute("select status from tarefas where origem_ref=? order by id desc limit 1",
                        (REF_CONFIRMA % cid,)).fetchone()
    finally:
        con.close()
    return bool(r) and r[0] == "concluida"


def _marcar_trabalhista(cid, executar):
    """Confirmado trabalhista: a etiqueta do card vira RECLAMANTE (painéis e esteira contam certo)."""
    if not executar:
        return
    con = sqlite3.connect(DB, timeout=20)
    try:
        con.execute("pragma busy_timeout=8000")
        con.execute("update wa_conversas set tag='RECLAMANTE' where id=? and coalesce(tag,'')<>'RECLAMANTE'",
                    (cid,))
        con.commit()
    finally:
        con.close()


def _vendedora_que_fechou(cid):
    """A vendedora que levou o card para o "Fechou!" (a ultima que fez isso, pelo historico do quadro). A
    Advogada e o Ton tambem movem card; ai vale a dona do card. Sem nenhuma das duas: a equipe."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    try:
        for (quem,) in con.execute("select lower(coalesce(quem,'')) from wa_board_hist where conversa_id=? "
                                   "and para_etapa like 'Fechou%' order by id desc", (cid,)):
            if quem in VENDEDORAS:
                return VENDEDORAS[quem]
        r = con.execute("select lower(coalesce(atendente_email,'')) from wa_conversas where id=?",
                        (cid,)).fetchone()
        return VENDEDORAS.get(r[0] if r else "") or "a equipe"
    finally:
        con.close()


def _tarefa_para_vendedora(cid, nome, tel, dias, faltam, executar):
    """Tarefa URGENTE, com a faixa fixa na tela, para a vendedora que fechou ligar (Advogada, 15/10/2026).
    Origem 'ligar-agora' (ja esta na faixa fixa e nenhuma rotina a fecha sozinha); uma por card."""
    import lead_quente_tarefa as LQ
    vend = _vendedora_que_fechou(cid)
    t = {"vend": vend, "origem": "ligar-agora", "ref": "rt-documentos:%s" % cid, "cid": int(cid),
         "nome": nome or "", "tel": tel or "",
         "titulo": "LIGAR: %s FECHOU A AÇÃO E NÃO MANDOU %s · %s" % (
             (nome or "Sem nome")[:30], " NEM ".join(FALTA_CURTO[f].upper() for f in faltam) or "OS DOCUMENTOS",
             tel or ""),
         "texto": LQ._limpo(
             "Ele fechou a ação trabalhista há %d dias e ainda não mandou %s, mesmo com os "
             "lembretes%s. Liga para ele hoje: é a ligação que destrava.\n\nVale a foto da CNH aberta ou o "
             "arquivo da CNH digital, e o endereço pode ser escrito na conversa, completo e com CEP. A "
             "carteira de trabalho não trava mais o contrato. A automação parou de cobrar; se o documento "
             "chegar, a esteira retoma sozinha." % (
                 dias, " nem ".join(FALTA_CURTO[f] for f in faltam) or "os documentos",
                 " e o vídeo do 3º dia" if _e_motorista(cid) else ""))}
    print("    TAREFA URGENTE para %s: %s" % (vend, t["titulo"][:80]))
    if not executar:
        return None
    return LQ.gravar(t)


def alerta_time(texto, cfg, executar):
    tel = cfg.get("alerta_telefone", "")
    if executar and tel:
        zapi_texto(tel, texto)
    print("    ALERTA TIME: " + texto.replace("\n", " ")[:200])


# ---- classificacao dos documentos (Claude visao) ----------------------------

def _baixar_midia(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    return r.content, ctype


def classificar_documento(conteudo, ctype):
    """Tipo de UM documento. Mantida por compatibilidade: desde 24/09/2026
    quem le de verdade e o leitura_da_conversa, que numa chamada so diz o
    tipo, se esta legivel e ja extrai os campos."""
    import claude_vps
    import contratacao_documentos as cdoc
    return claude_vps.classificar_arquivo(
        conteudo, ctype, instrucao=cdoc.INSTRUCAO_CLASSIFICACAO,
        validos=cdoc.TIPOS_VALIDOS)


_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "application/pdf": ".pdf"}


# ---- transcricao no Drive ---------------------------------------------------

_TOKEN_CACHE = {"access": None, "exp": 0}


def _drive_token():
    if _TOKEN_CACHE["access"] and time.time() < _TOKEN_CACHE["exp"] - 60:
        return _TOKEN_CACHE["access"]
    t = json.load(open(TOKEN_GOOGLE, encoding="utf-8"))
    r = requests.post(t.get("token_uri", "https://oauth2.googleapis.com/token"),
                      data={"client_id": t["client_id"], "client_secret": t["client_secret"],
                            "refresh_token": t["refresh_token"],
                            "grant_type": "refresh_token"}, timeout=20)
    r.raise_for_status()
    _TOKEN_CACHE["access"] = r.json()["access_token"]
    _TOKEN_CACHE["exp"] = time.time() + int(r.json().get("expires_in", 3600))
    return _TOKEN_CACHE["access"]


def _drive_get(path, **params):
    r = requests.get("https://www.googleapis.com/drive/v3/" + path,
                     headers={"Authorization": "Bearer " + _drive_token()},
                     params=params, timeout=60)
    r.raise_for_status()
    return r


def _links_da_ficha(cid):
    """Ids de arquivos do Drive colados na FICHA do card (coluna notas).
    Processo novo (Advogada 12/08): sem pasta do cliente ainda, a transcricao sobe
    solta no Drive e o link vai na ficha; o intake baixa por aqui e depois
    move o arquivo para a pasta criada."""
    return td.ids_da_ficha(DB, cid)


def _baixar_arquivo_drive(meta, cid):
    """Baixa um arquivo do Drive para o uploads; converte gdoc/docx em txt."""
    os.makedirs(UPLOADS, exist_ok=True)
    fid, nome_arq, mime = meta["id"], meta.get("name", ""), meta.get("mimeType", "")
    if mime == "application/vnd.google-apps.document":
        r = _drive_get(f"files/{fid}/export", mimeType="text/plain")
        dest = os.path.join(UPLOADS, f"auto_{cid}_transcricao.txt")
        open(dest, "wb").write(r.content)
        return dest
    r = _drive_get(f"files/{fid}", alt="media", supportsAllDrives="true")
    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" \
            or nome_arq.lower().endswith(".docx"):
        bruto = os.path.join(UPLOADS, f"auto_{cid}_transcricao.docx")
        open(bruto, "wb").write(r.content)
        from docx import Document
        texto = "\n".join(par.text for par in Document(bruto).paragraphs)
        dest = os.path.join(UPLOADS, f"auto_{cid}_transcricao.txt")
        open(dest, "w", encoding="utf-8").write(texto)
        return dest
    ext = os.path.splitext(nome_arq)[1] or ".txt"
    dest = os.path.join(UPLOADS, f"auto_{cid}_transcricao{ext}")
    open(dest, "wb").write(r.content)
    return dest


def mover_para_pasta_cliente(file_id, pasta_id):
    """Move um arquivo solto do Drive para a pasta do cliente (Advogada 12/08)."""
    try:
        meta = _drive_get(f"files/{file_id}", fields="parents,name").json()
        pais = meta.get("parents", [])
        if pasta_id in pais:
            return
        r = requests.patch("https://www.googleapis.com/drive/v3/files/" + file_id,
                           headers={"Authorization": "Bearer " + _drive_token()},
                           params={"addParents": pasta_id,
                                   "removeParents": ",".join(pais), "fields": "id"},
                           timeout=60)
        r.raise_for_status()
        print(f"    '{meta.get('name', '?')}' movido para a pasta do cliente.")
    except Exception as e:
        print(f"    (mover '{file_id[:12]}...' pra pasta falhou: {str(e)[:120]})")


ROTULO_ARQ = {"cnh": "CNH", "comprovante_residencia": "COMPROVANTE DE RESIDENCIA"}


def _pdf_do_documento(path):
    """Documento recebido no WhatsApp -> bytes de PDF (Advogada, 04/09/2026: subir
    recortado, em PDF e com o nome certo). Imagem vira PDF com recorte
    best-effort das bordas (so aplica corte sensato); PDF passa direto."""
    raw = open(path, "rb").read()
    if path.lower().endswith(".pdf"):
        return raw
    try:
        from PIL import Image, ImageFilter, ImageOps
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
        cinza = img.convert("L").filter(ImageFilter.GaussianBlur(2))
        for claro in (True, False):
            bw = cinza.point(lambda p, c=claro: 255 if ((p > 140) == c) else 0)
            caixa = bw.getbbox()
            if caixa:
                area = ((caixa[2] - caixa[0]) * (caixa[3] - caixa[1])) / float(img.width * img.height)
                if 0.30 <= area <= 0.95:      # corte sensato; senao mantem inteira
                    img = img.crop(caixa)
                    break
        buf = io.BytesIO()
        img.save(buf, "PDF", resolution=150)
        return buf.getvalue()
    except Exception as e:
        print(f"    (conversao p/ PDF falhou: {str(e)[:80]}; subo o original)")
        return raw


def _subpasta_documentos(pasta_id):
    """Id da subpasta DOCUMENTOS DO CLIENTE dentro da pasta do cliente; sem
    ela, devolve a propria pasta."""
    try:
        r = _drive_get("files",
                       q=(f"'{pasta_id}' in parents and "
                          "mimeType='application/vnd.google-apps.folder' and trashed=false"),
                       fields="files(id,name)").json()
        for f in r.get("files", []):
            if "DOCUMENTOS" in (f.get("name") or "").upper():
                return f["id"]
    except Exception as e:
        print(f"    (subpasta DOCUMENTOS: {str(e)[:80]})")
    return pasta_id


def subir_docs_contratacao(c, nome_contrato, pasta_id, executar):
    """Sobe os documentos da contratacao (CNH/comprovante recebidos no
    WhatsApp) para a pasta do cliente no Drive, em PDF nomeado (Advogada,
    04/09/2026). Idempotente pelo estado (docs_no_drive)."""
    ja = set(c.get("docs_no_drive", []))
    destino = None
    for tipo, path in (c.get("docs") or {}).items():
        if tipo in ja or not os.path.exists(path):
            continue
        nome_arq = f"{ROTULO_ARQ.get(tipo, tipo.upper())} - {nome_contrato}.pdf"
        if not executar:
            print(f"    (dry-run: subiria '{nome_arq}')")
            continue
        if destino is None:
            destino = _subpasta_documentos(pasta_id)
        corpo = _pdf_do_documento(path)
        fronteira = "===sldocs==="
        multipart = ((f"--{fronteira}\r\n"
                      "Content-Type: application/json; charset=UTF-8\r\n\r\n"
                      + json.dumps({"name": nome_arq, "parents": [destino]})
                      + f"\r\n--{fronteira}\r\nContent-Type: application/pdf\r\n\r\n"
                      ).encode("utf-8") + corpo + f"\r\n--{fronteira}--".encode("utf-8"))
        try:
            r = requests.post(
                "https://www.googleapis.com/upload/drive/v3/files"
                "?uploadType=multipart&fields=id",
                headers={"Authorization": "Bearer " + _drive_token(),
                         "Content-Type": f"multipart/related; boundary={fronteira}"},
                data=multipart, timeout=120)
            r.raise_for_status()
            ja.add(tipo)
            print(f"    '{nome_arq}' subiu para a pasta do cliente.")
        except Exception as e:
            print(f"    (upload de '{nome_arq}' falhou: {str(e)[:120]})")
    c["docs_no_drive"] = sorted(ja)


def buscar_transcricao_drive(nome, cid):
    """Acha a transcricao, da fonte mais confiavel para a menos: 1o os links na
    FICHA do card, 2o a PASTA DE ENTRADA de transcricoes (Advogada 01/09/2026), 3o o
    nome do cliente no Drive inteiro. Retorna (path_local, file_id,
    ids_da_ficha) ou (None, None, ids_da_ficha)."""
    ids_ficha = _links_da_ficha(cid)
    metas = []
    for fid in ids_ficha:
        try:
            metas.append(_drive_get(f"files/{fid}", fields="id,name,mimeType,size").json())
        except Exception as e:
            print(f"    (link da ficha {fid[:12]}... inacessivel: {str(e)[:80]})")
    # a ficha de pre-cadastro NAO e a transcricao (caso Benicio 12/08: os dois
    # links vao juntos na mesma nota) - a regra de escolha mora no
    # transcricao_drive, compartilhada com o motor da producao.
    escolhido = td.escolher(metas)
    if escolhido:
        print(f"    transcricao pela FICHA do card: {escolhido.get('name')}")
        try:
            return _baixar_arquivo_drive(escolhido, cid), escolhido["id"], ids_ficha
        except Exception as e:
            print(f"    (baixar pela ficha falhou: {str(e)[:120]})")
    # PASTA DE ENTRADA (Advogada 01/09/2026): o time sobe a transcricao ali ANTES de
    # o cliente ter pasta oficial. E fonte curada por humano, entao vem antes da
    # busca no Drive inteiro. Depois do intake, o `mover_para_pasta_cliente`
    # tira o arquivo da entrada e poe na pasta do cliente.
    q_entrada = td.query_na_entrada()
    if q_entrada:
        try:
            fs = _drive_get("files", q=q_entrada + " and trashed=false",
                            orderBy="modifiedTime desc", pageSize=100,
                            fields="files(id,name,mimeType,size)").json().get("files", [])
        except Exception as e:
            print(f"    (entrada de transcricoes inacessivel: {str(e)[:120]})")
            fs = []
        escolhido = td.escolher_na_entrada(fs, nome)
        if escolhido:
            print(f"    transcricao na ENTRADA de transcricoes: {escolhido.get('name')}")
            try:
                return _baixar_arquivo_drive(escolhido, cid), escolhido["id"], ids_ficha
            except Exception as e:
                print(f"    (baixar da entrada falhou: {str(e)[:120]})")
    achado = None
    for q in td.queries_por_nome(nome):
        try:
            fs = _drive_get("files", q=q + " and trashed=false",
                            orderBy="modifiedTime desc", pageSize=5,
                            fields="files(id,name,mimeType)").json().get("files", [])
        except Exception as e:
            print(f"    (busca no Drive falhou: {str(e)[:120]})")
            return None, None, ids_ficha
        if fs:
            achado = fs[0]
            break
    if not achado:
        return None, None, ids_ficha
    print(f"    transcricao achada pelo nome: {achado['name']}")
    try:
        return _baixar_arquivo_drive(achado, cid), achado["id"], ids_ficha
    except Exception as e:
        print(f"    (baixar transcricao falhou: {str(e)[:120]})")
        return None, None, ids_ficha


def transcricao_da_ligacao(cid):
    """4a fonte: a TRANSCRICAO DA LIGACAO DE FECHAMENTO, que ja mora na nota da conversa.

    Por que isto existe (22/09/2026): quando este vigia foi escrito, o fechamento
    era por telefone e a transcricao chegava a mao no Drive. Hoje o fechamento e
    pela LIGACAO DO WHATSAPP de dentro do sistema, e o transcrever_ligacoes.py ja
    cola a transcricao inteira na nota da conversa, com separacao de quem fala.
    O vigia ficava parado pedindo no WhatsApp uma transcricao que ja estava no banco.

    Pega o ULTIMO bloco de ligacao da nota e grava num .txt local, no mesmo lugar
    onde as outras fontes gravam. Devolve o caminho, ou None se nao houver.
    """
    import sqlite3
    cabeca = "=== LIGACAO PELO WHATSAPP"
    try:
        con = sqlite3.connect(DB)
        linha = con.execute("select notas from wa_conversas where id=?", (cid,)).fetchone()
        con.close()
    except Exception as e:
        print("    (nao consegui ler a nota da conversa: %s)" % str(e)[:120])
        return None
    notas = (linha[0] if linha else "") or ""
    if cabeca not in notas:
        return None
    bloco = notas[notas.rindex(cabeca):]
    # corta no proximo bloco que nao seja da ligacao (ex.: o resumo do pos-ligacao)
    corte = bloco.find("\n\n=== ", len(cabeca))
    if corte > 0:
        bloco = bloco[:corte]
    if len(bloco) < 400:
        print("    (a ligacao na nota e curta demais pra servir de transcricao)")
        return None
    os.makedirs(UPLOADS, exist_ok=True)
    dest = os.path.join(UPLOADS, "auto_%s_transcricao_ligacao.txt" % cid)
    with io.open(dest, "w", encoding="utf-8") as f:
        f.write(bloco)
    print("    transcricao pela LIGACAO do WhatsApp (%d caracteres)" % len(bloco))
    return dest


# ---- intake local -----------------------------------------------------------

def rodar_intake(transcricao_path, docs_paths, dados_crm=None, qualificacao=None):
    """Roda o run_intake do INTAKE/main.py em processo (mesma receita do
    router do Sistema Escritório Modelo: INTEGRACOES vence os shims do INTAKE).
    dados_crm (Advogada 12/08): origem/telefone/email do card para preencher
    TODAS as abas do ADVBOX (ex.: lead de campanha -> origem CAMPANHA)."""
    for p in (os.path.join(_ROOT, "INTAKE"), os.path.join(_ROOT, "SYNC"),
              os.path.join(_ROOT, "INTEGRACOES")):
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(
        "intake_main", os.path.join(_ROOT, "INTAKE", "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            mod.run_intake(transcricao_path, docs_paths or None,
                           dados_crm=dados_crm, qualificacao=qualificacao)
        return True, buf.getvalue()
    except BaseException as e:
        return False, buf.getvalue() + f"\nERRO: {type(e).__name__}: {e}"


def _guardar_log_intake(cid, nome, log):
    """Log INTEIRO do intake em arquivo proprio. O resumo no log do vigia corta
    em 1200 chars e some quando alguem roda o watcher na mao - foi por isso que
    a falha do Benicio (12/08) so foi diagnosticada pelo transcript do CLI."""
    try:
        with open(LOG_INTAKE, "a", encoding="utf-8") as f:
            f.write(f"\n===== INTAKE {nome} (conversa {cid}) | {_agora()} =====\n")
            f.write(log or "(sem log)")
            f.write("\n===== fim =====\n")
    except Exception as e:
        print(f"    (nao consegui gravar {LOG_INTAKE}: {str(e)[:100]})")


def _dados_crm(cid):
    """origem/telefone/email do card para o cadastro completo no ADVBOX."""
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
        r = con.execute("select origem, contato_telefone, email from wa_conversas "
                        "where id=?", (cid,)).fetchone()
        con.close()
        if r:
            return {"origem": r[0] or "", "telefone": r[1] or "", "email": r[2] or ""}
    except Exception:
        pass
    return {}


def enroll_assinatura_e_links(nome, telefone, executar):
    """Inscreve na cobranca_assinatura e devolve o texto com os links.
    O nome tem que ser o do CONTRATO (o que a IVA extraiu), nao o do card:
    no Benicio (12/08) o card chamava 'Benício', o contrato saiu como 'Benicio Arantes
    de Souza' e os links existiam mas nao foram achados - cliente ficou sem
    receber. Por isso o casamento tambem cai pro telefone (ultimos 8)."""
    sys.stdout.flush()  # o modulo reembrulha o stdout; sem flush o log anterior some
    sys.path.insert(0, _DIR)
    import cobranca_assinatura as ca
    fila = ca.carregar()
    # (29/10/2026) so inscricao VIVA conta como "ja inscrito". Antes, o cliente que
    # fechava um caso novo casava pelo telefone com a inscricao encerrada do caso
    # anterior e recebia de volta os links que ja tinha assinado.
    viva = ca.inscricao_viva(fila["clientes"], nome, telefone)
    c = viva[1] if viva else None
    if executar and not (c and c.get("links")):
        chave = None
        try:
            chave = ca.enroll_cliente(nome, telefone)
        except Exception as e:
            print(f"    (enroll assinatura falhou: {str(e)[:150]})")
        fila = ca.carregar()
        c = fila["clientes"].get(chave) if isinstance(chave, str) else None
        if not c:
            viva = ca.inscricao_viva(fila["clientes"], nome, telefone)
            c = viva[1] if viva else None
    if not c or not c.get("links"):
        return None
    return "\n".join(f"📄 {l['rotulo']}:\n{l['url']}" for l in c["links"])


def _por_telefone(clientes, telefone):
    if not telefone:
        return None
    return next((v for v in clientes.values()
                 if v.get("telefone") and v["telefone"][-8:] == str(telefone)[-8:]), None)


def _nome_do_contrato(log, padrao):
    """O nome real do cliente sai no log do intake; o do card costuma ser
    apelido ('Benício') ou ate o @lid quando o CRM nao salvou o nome. A grafia
    tem que ser identica a da fila de assinatura, entao preferimos a linha do
    proprio enroll feito la dentro."""
    for padrao_re in (r"\[enroll\] inscrito: (.+?) \|",
                      r"Cadastrando cliente no ADVBOX: (.+?)\.\.\.",
                      r"Gerando: (.+?) - Contrato de Honorarios"):
        m = re.search(padrao_re, log or "")
        if m and m.group(1).strip():
            return m.group(1).strip()
    return padrao


# ---- banco ------------------------------------------------------------------

def _conversas(etapa, pipeline=PIPELINE_CONTRATACAO):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    con.execute("pragma busy_timeout=8000")
    rows = con.execute(
        "select id, contato_nome, contato_telefone from wa_conversas "
        "where pipeline_id=? and etapa=?", (pipeline, etapa)).fetchall()
    con.close()
    import claude_vps
    return [(cid, claude_vps.nome_limpo(nome), tel) for cid, nome, tel in rows]


def _midias_novas(cid, desde_id):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    con.execute("pragma busy_timeout=8000")
    rows = con.execute(
        "select id, tipo, midia_url from wa_mensagens "
        "where conversa_id=? and id>? and origem='contato' and tipo in ('imagem','doc') "
        "and midia_url is not null and midia_url != '' order by id",
        (cid, desde_id)).fetchall()
    con.close()
    return rows


def _ultima_msg_do_contato(cid):
    """Id da ultima mensagem que o CLIENTE mandou. Serve para saber se ele deu
    algum sinal depois de um marco (a mensagem de ultimo contato, por ex.).
    Conta mensagem NOVA dele, nunca leitura ou entrega."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    con.execute("pragma busy_timeout=8000")
    r = con.execute("select coalesce(max(id), 0) from wa_mensagens "
                    "where conversa_id=? and origem='contato'", (cid,)).fetchone()
    con.close()
    return int(r[0] if r else 0)


def _mover_etapa(cid, etapa, executar):
    if not executar:
        print(f"    (dry-run: moveria conversa {cid} para '{etapa}')")
        return
    con = sqlite3.connect(DB, timeout=15)
    con.execute("pragma busy_timeout=8000")
    con.execute("update wa_conversas set etapa=?, atualizado_em=? where id=?",
                (etapa, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), cid))
    con.commit()
    con.close()
    print(f"    conversa {cid} movida para '{etapa}'")


_RE_CEP = re.compile(r"\b\d{5}-?\d{3}\b")
_RE_LOGRADOURO = re.compile(
    r"\b(rua|r\.|avenida|av\.?|travessa|alameda|estrada|rodovia|praca|praça)\b[^\n]{0,80}\d",
    re.IGNORECASE)


def _endereco_no_texto(cid):
    """Endereco ESCRITO pelo cliente na conversa (regra da Advogada, 04/09/2026):
    CEP ou logradouro com numero em mensagem de TEXTO dele. Devolve a mensagem
    mais recente que parece endereco, ou None."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    con.execute("pragma busy_timeout=8000")
    rows = con.execute(
        "select conteudo from wa_mensagens where conversa_id=? and origem='contato' "
        "and tipo='texto' and length(conteudo) between 8 and 400 order by id desc limit 40",
        (cid,)).fetchall()
    con.close()
    for (txt,) in rows:
        if _RE_CEP.search(txt) or _RE_LOGRADOURO.search(txt):
            return " ".join(txt.split())
    return None


def _endereco_da_entrevista(cid):
    """Endereco do CLIENTE preenchido pelo time na aba Entrevista (campo
    'endereco_cliente' da tabela ficha_entrevista). Fonte preferida: e o time
    anotando durante a ligacao de fechamento (Advogada, 04/09/2026)."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    con.execute("pragma busy_timeout=8000")
    try:
        r = con.execute(
            "select valor from ficha_entrevista where conversa_id=? and campo='endereco_cliente'",
            (cid,)).fetchone()
    except sqlite3.OperationalError:      # tabela ainda nao existe
        r = None
    con.close()
    txt = (r[0] or "").strip() if r else ""
    return " ".join(txt.split()) if len(txt) >= 8 else None


def _texto_do_cliente(cid, limite=120):
    """Tudo que o CLIENTE ESCREVEU na conversa, da mais recente para a mais
    antiga, num texto so.

    Regra da Advogada (24/09/2026): "se nao for enviado o documento, pode ser
    substituido pelo texto escrito com os dados do cliente". Entao CPF, CEP,
    endereco, e-mail, estado civil e nome da empresa que ele digitou valem
    tanto quanto documento. So mensagem DELE ('contato'): o que a gente
    escreveu nao e fonte de dado do cliente.
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    con.execute("pragma busy_timeout=8000")
    rows = con.execute(
        "select conteudo from wa_mensagens where conversa_id=? and origem='contato' "
        "and tipo='texto' and conteudo is not null and conteudo != '' "
        "order by id desc limit ?", (cid, limite)).fetchall()
    con.close()
    return "\n".join(" ".join((t or "").split()) for (t,) in rows)


def _entrevista_do_card(cid):
    """A aba Entrevista do card inteira, como dicionario campo -> valor.

    E onde o time anota durante a ligacao de fechamento. Depois da regra da Advogada
    de 24/09, e tambem onde entram ESTADO CIVIL e E-MAIL, que nenhum documento
    traz. Tabela ausente ou vazia devolve {} e nao derruba a rodada.
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    con.execute("pragma busy_timeout=8000")
    try:
        rows = con.execute(
            "select campo, valor from ficha_entrevista where conversa_id=?",
            (cid,)).fetchall()
    except sqlite3.OperationalError:      # tabela ainda nao existe
        rows = []
    con.close()
    return {c: (v or "").strip() for c, v in rows if (v or "").strip()}


def _baixar_para_leitura(mid, url):
    """Adaptador do download para a leitura da conversa.

    A midia_url da Z-API e temporaria (Backblaze) e EXPIRA: link velho devolve
    erro e nao ha o que recuperar. Falha aqui nao derruba a rodada, so pula
    aquele arquivo.
    """
    try:
        return _baixar_midia(url)
    except Exception as e:
        print("    (download da mídia %s falhou: %s)" % (mid, str(e)[:90]))
        return None, None


def _nota(cid, texto, executar):
    """Nota interna na conversa (origem='nota'), para o time ver no chat ao vivo."""
    if not executar:
        print(f"    (dry-run: nota na conversa {cid}: {texto[:60]}...)")
        return
    con = sqlite3.connect(DB, timeout=15)
    con.execute("pragma busy_timeout=8000")
    con.execute(
        "insert into wa_mensagens (conversa_id, origem, autor, tipo, conteudo, "
        "midia_url, remote_id, criado_em) "
        "values (?,'nota','vigia:contratacao','texto',?,'','',?)",
        (cid, texto, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()
    con.close()
    print(f"    nota registrada na conversa {cid}")


# ---- travas contra retrabalho (25/09/2026) ----------------------------------

def _ja_tem_contrato(c):
    """Card que ja gerou contrato (intake ok) e voltou para 'Fechou!'. O vigia
    nao pede documento de novo nem gera um segundo contrato: foi movido por
    gente, entao gente decide (caso Teodoro Lins, 25/09)."""
    return (c or {}).get("intake") == "ok"


def _avisar_ja_tem_contrato(cid, nome, c, cfg, executar):
    if c.get("aviso_ja_tem_contrato"):
        return
    alerta_time(f"ℹ️ {nome or 'card'} voltou para 'Fechou!' mas já tem contrato gerado. "
                f"O vigia não vai pedir documento nem gerar contrato novo. Se for para "
                f"refazer o contrato, é pedir que eu refaço.", cfg, executar)
    if executar:
        c["aviso_ja_tem_contrato"] = _hoje()


def _humano_falou_recente(cid, horas=12):
    """Alguem da equipe escreveu ao cliente nas ultimas `horas`: o lembrete
    automatico do dia nao sai (caso Cosme, 25/09: a a equipe pediu CNH e
    comprovante ao meio-dia e o robo ia pedir de novo)."""
    from datetime import timedelta
    limite = (datetime.utcnow() - timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    r = con.execute("select count(*) from wa_mensagens where conversa_id=? and origem='atendente' "
                    "and autor like '%@%' and criado_em>=?", (cid, limite)).fetchone()
    con.close()
    return bool(r and r[0])


# ---- fases ------------------------------------------------------------------

def fase_pedido(est, cfg, executar):
    """Card entrou em 'Fechou!': dispara o KIT DE BOAS-VINDAS completo
    (8 mensagens, com apresentacao e e-book), que ja termina pedindo os
    documentos do contrato (Advogada, 12/08: kit plugado nesta caixa)."""
    sys.stdout.flush()
    sys.path.insert(0, _DIR)
    try:
        import kit_boasvindas as kit  # kit de boas-vindas do escritorio (opcional)
    except ImportError:
        print('  kit de boas-vindas nao instalado (SYNC/kit_boasvindas.py): fase do pedido pulada')
        return
    for cid, nome, tel in _conversas(ETAPA_PEDIDO):
        c = est["conversas"].setdefault(str(cid), {
            "nome": nome or "", "telefone": tel or "", "pedido_em": None,
            "ultima_msg_vista": 0, "docs": {}, "ultimo_lembrete": None,
            "intake": None, "alertado_hoje": None, "ultimo_update": None})
        if c["pedido_em"]:
            continue
        if not tel:
            print(f"  [conversa {cid}] {nome}: SEM TELEFONE, resolver manual.")
            continue
        if _veio_da_campanha_gr(cid):
            if not _produto_confirmado(cid):
                print(f"  [{nome}] veio da campanha do GR: kit SEGURADO até a vendedora confirmar "
                      f"que é trabalhista (tarefa {REF_CONFIRMA % cid}).")
                continue
            _marcar_trabalhista(cid, executar)
        print(f"  [{nome}] KIT DE BOAS-VINDAS -> " + ("ENVIAR" if executar else "DRY-RUN"))
        ok, enviados = kit.disparar_kit(nome or "", tel, executar)
        if executar and enviados:
            c["pedido_em"] = _agora()
            if not ok:
                print(f"    kit INCOMPLETO ({enviados}/8) — conferir log.")


def fase_recepcao(est, cfg, executar):
    """LER ANTES DE PEDIR (ordem da Advogada, 24/09/2026).

    Toda rodada comeca lendo a conversa: o que o cliente ja mandou, o que aquilo
    e e se DA PARA LER. So depois disso a esteira decide se pede, se cobra ou se
    avanca. Antes o robo classificava o TIPO do arquivo e nunca olhava se ele
    estava legivel - foi assim que a CNH ilegivel do Heitor Valença passou e o
    contrato CT. 100/2026 saiu assinado com CPF e RG em branco.

    A leitura e por CONVERSA e por MIDIA: o que ja foi lido numa rodada nao volta
    para a visao na seguinte ("alem de gastar tokens ainda vira trabalho extra
    para a equipe").

    O card que foi para a etapa de ligacao CONTINUA sendo observado: se o
    documento chegar depois do telefonema, a esteira retoma sozinha e o card
    segue para a Fase de Contratacao (Advogada, 27/08). O que ele nao recebe mais
    e lembrete automatico - dali em diante quem fala com ele e gente.
    """
    import leitura_da_conversa as lc
    import contratacao_documentos as cdoc

    registro = lc.LeituraDaConversa(REGISTRO_LEITURA)

    alvos = ([(cid, n, t, ETAPA_PEDIDO) for cid, n, t in _conversas(ETAPA_PEDIDO)]
             + [(cid, n, t, ETAPA_LIGAR) for cid, n, t in _conversas(ETAPA_LIGAR)])
    for cid, nome, tel, etapa_atual in alvos:
        c = est["conversas"].get(str(cid))
        if not c or not c["pedido_em"]:
            continue
        if _ja_tem_contrato(c):
            _avisar_ja_tem_contrato(cid, nome, c, cfg, executar)
            continue
        primeiro = (nome or "").split()[0] if nome else ""

        # ---- PASSO ZERO: ler a conversa inteira --------------------------
        def _guardar(mid, tipo, conteudo, ctype, _cid=cid):
            os.makedirs(UPLOADS, exist_ok=True)
            dest = os.path.join(
                UPLOADS, "auto_%s_%s_%s%s" % (_cid, tipo, mid, _EXT.get(ctype, ".bin")))
            with open(dest, "wb") as f:
                f.write(conteudo)
            return dest

        midias = [(mid, url) for mid, _tipo, url in _midias_novas(cid, 0)]
        retrato = lc.ler(
            cid, midias, _baixar_para_leitura, registro,
            texto_conversa=_texto_do_cliente(cid),
            entrevista=_entrevista_do_card(cid),
            card={"nome": nome, "telefone": tel, "email": (c.get("email") or "")},
            log=lambda s: print("  [%s]%s" % (nome, s)),
            guardar=(_guardar if executar else None))
        registro.salvar()
        print("  [%s] leitura: %s" % (nome, lc.resumo(retrato)))

        # Visao fora do ar = retrato incompleto. NAO decidir nada: cobrar aqui
        # seria pedir de novo o que o cliente talvez ja tenha mandado.
        if retrato["faltou_visao"]:
            print("  [%s] leitura incompleta; nao decido nesta rodada." % nome)
            continue

        # ---- o que a leitura encontrou vira o estado do card --------------
        for tipo, d in retrato["documentos"].items():
            if d.get("legivel") and d.get("arquivo"):
                c["docs"][tipo] = d["arquivo"]
        if retrato.get("dados"):
            c["qualificacao"] = retrato["dados"]      # o intake usa sem reler
            c["origem_dados"] = retrato.get("origem", {})
            # O NOME DO CARD VEM DO DOCUMENTO (Advogada, 07/11/2026). Mesma porta que a
            # esteira do GR usa: a CNH ja diz o nome completo, e o card as vezes esta com o
            # telefone, um apelido ou "empresa". Caixa alta, registro interno, SEM tarefa.
            # Nome forte e diferente nao e tocado. Regra e testes: app/nome_card.py.
            if executar:
                try:
                    import sys as _sys
                    if "/opt/escritorio/SISTEMA/backend" not in _sys.path:
                        _sys.path.insert(0, "/opt/escritorio/SISTEMA/backend")
                    from app import nome_card as _nk
                    _nk.aplicar_do_documento(
                        cid, (c.get("qualificacao") or {}).get("nome"), DB,
                        fonte="cnh", log=lambda m: print("  [%s]%s" % (nome, m)))
                except Exception as _e:
                    print("  [%s] nome pelo documento falhou: %s" % (nome, str(_e)[:80]))
        if midias:
            c["ultima_msg_vista"] = max(m for m, _ in midias)

        # ---- CEP: quem acha e a automacao (Advogada, 14/10/2026) ----------------
        # Completa bairro/cidade_estado/cep na qualificacao ANTES do portao e
        # do intake (ViaCEP -> web); sem isso o preambulo do contrato fica com
        # {{Bairro}}/{{Cidade Estado}}/{{CEP}} abertos (caso Francisco, 1768).
        if c.get("qualificacao"):
            try:
                _, obs_cep = cdoc.completar_endereco(
                    c["qualificacao"], telefone=tel, cache=c)
                if obs_cep and obs_cep not in (c.get("notas_dadas") or []):
                    _nota(cid, obs_cep, executar)
                    c.setdefault("notas_dadas", []).append(obs_cep)
            except Exception as e:
                print(f"    (completar_endereco: {str(e)[:120]})")

        # ---- o gate decide -----------------------------------------------
        aval = cdoc.avaliar(
            {t: d.get("arquivo") or True for t, d in retrato["documentos"].items()
             if d.get("legivel")},
            texto_conversa=_texto_do_cliente(cid),
            dados_do_caso=retrato.get("dados") or c.get("qualificacao") or {},
            cnh_ilegivel=retrato["ilegiveis"].get("cnh"))

        for obs in aval["observacoes"]:
            if obs not in (c.get("notas_dadas") or []):
                _nota(cid, obs, executar)
                c.setdefault("notas_dadas", []).append(obs)

        # ---- CNH ilegivel: pede outra foto, uma vez por dia ---------------
        chaves = [k for k, _, _ in aval["mensagens"]]
        if "cnh_ilegivel" in chaves and (c.get("pedido_cnh_nova") or "") != _hoje():
            motivo = retrato["ilegiveis"].get("cnh") or "não deu para ler"
            texto = cdoc.MSG_CNH_ILEGIVEL.replace("{nome}", primeiro).replace(
                "{motivo}", motivo)
            if executar and zapi_texto(tel, texto):
                c["pedido_cnh_nova"] = _hoje()
                c["ultimo_lembrete"] = _agora()
            print("  [%s] CNH ilegível (%s): pedi outra foto." % (nome, motivo))
            continue

        # ---- completo: avanca --------------------------------------------
        if aval["pronto"]:
            print("  [%s] documentos COMPLETOS (%s)." % (
                nome, ", ".join(sorted(retrato["documentos"]))))
            if executar:
                zapi_texto(tel, cdoc.MSG_COMPLETO.replace("{nome}", primeiro))
            _mover_etapa(cid, ETAPA_CONFERIDO, executar)
            c["intake"] = "pendente"
            continue

        faltam = aval["faltando"]

        # ---- chegou arquivo que a visao nao reconheceu: responde assim mesmo ----
        # Advogada, 26/10/2026: o Heitor mandou 8 papeis do acerto e a esteira ficou calada,
        # porque o aviso do que falta so saia quando algum documento era reconhecido.
        if retrato["lidas_agora"] and not retrato["documentos"]:
            if (c.get("aviso_outros") or "") != _hoje():
                texto = (cdoc.MSG_RECEBI_OUTROS.replace("{nome}", primeiro)
                         .replace("{faltando}", cdoc.texto_do_que_falta(faltam)))
                if executar and zapi_texto(tel, texto):
                    c["aviso_outros"] = _hoje()
                    c["ultimo_lembrete"] = _agora()
                print("  [%s] arquivos que nao sao documento do contrato: avisei o que falta (%s)"
                      % (nome, ", ".join(faltam)))
            else:
                print("  [%s] arquivos nao reconhecidos (ja avisei hoje)." % nome)
            continue

        # ---- chegou algo agora: responde na hora o que ainda falta --------
        if retrato["lidas_agora"] and retrato["documentos"]:
            texto = (cdoc.MSG_PARCIAL.replace("{nome}", primeiro)
                     .replace("{recebido}", cdoc.texto_do_que_chegou(
                         sorted(retrato["documentos"])))
                     .replace("{faltando}", cdoc.texto_do_que_falta(faltam)))
            if executar and zapi_texto(tel, texto):
                c["ultimo_lembrete"] = _agora()
            print("  [%s] parcial: falta %s" % (nome, faltam))
            continue

        # ---- lembrete: UMA mensagem por dia, so do que falta --------------
        if etapa_atual != ETAPA_PEDIDO:
            continue
        agora = datetime.now(TZ) if TZ else datetime.now()
        if not (LEMBRETE_JANELA[0] <= agora.hour < LEMBRETE_JANELA[1]):
            continue
        if (c.get("ultimo_lembrete") or "")[:10] == _hoje():
            continue
        # no dia em que os documentos foram pedidos o cliente ja falou com a
        # gente: o lembrete so comeca no dia seguinte (Advogada, 27/08)
        if (c.get("pedido_em") or "")[:10] == _hoje():
            continue
        if _humano_falou_recente(cid):
            print("  [%s] equipe falou com o cliente nas ultimas 12h; sem lembrete hoje." % nome)
            continue
        # 3o dia util sem NADA recebido: o lembrete do dia vira o video do 3o dia (Advogada, 15/10/2026)
        if (not retrato["documentos"] and not c.get("video_d3_em")
                and _dias_uteis_desde(c.get("pedido_em")) >= DIA_VIDEO and _e_motorista(cid)):
            texto = TEXTO_VIDEO_D3.replace("{nome}", primeiro or "parceiro")
            if executar:
                if _video_nativo(tel, VIDEO_DIA3, texto):
                    c["video_d3_em"] = _hoje()
                    c["ultimo_lembrete"] = _agora()
                    print("    VÍDEO DO 3º DIA enviado (dia útil %d)" % _dias_uteis_desde(c.get("pedido_em")))
            else:
                print("  [%s] VÍDEO DO 3º DIA (dry-run)" % nome)
            continue
        n_lembrete = int(c.get("n_lembrete", 0))
        texto = (cdoc.LEMBRETES[n_lembrete % len(cdoc.LEMBRETES)]
                 .replace("{nome}", primeiro)
                 .replace("{faltando}", cdoc.texto_do_que_falta(faltam)))
        if executar:
            if zapi_texto(tel, texto):
                c["ultimo_lembrete"] = _agora()
                c["n_lembrete"] = n_lembrete + 1
                print("    lembrete diário #%d: faltam %s" % (n_lembrete + 1, faltam))
        else:
            print("  [%s] lembrete diário (dry-run): faltam %s" % (nome, faltam))


def fase_ligacao(est, cfg, executar):
    """Passados DIAS_ATE_LIGACAO dias de cobranca automatica sem os documentos,
    o caso sai da mao da automacao e vai para a de gente (Advogada, 27/08).

    O card anda uma coluna, para a etapa de ligacao, a regua de mensagens PARA
    (a coluna do "Fechou!" e a unica que o lembrete olha) e o time recebe no
    WhatsApp o aviso com o resumo de quem ligar e por que. Se o documento
    chegar depois, a `fase_recepcao` continua vendo o card e segue com ele.

    15/10/2026 (Advogada): so em dia util, nunca no dia do video do 3o dia (o video
    precisa de um dia para agir) e com TAREFA URGENTE para a vendedora que fechou.
    """
    for cid, nome, tel in _conversas(ETAPA_PEDIDO):
        c = est["conversas"].get(str(cid))
        if not c or not c.get("pedido_em"):
            continue
        if _ja_tem_contrato(c):
            continue
        if set(c["docs"]) >= {"cnh", "comprovante_residencia"} or c.get("endereco_texto"):
            continue
        try:
            pedido_dt = datetime.fromisoformat(c["pedido_em"])
        except Exception:
            continue
        agora = datetime.now(TZ) if TZ else datetime.now()
        dias = (agora - pedido_dt).days
        if dias < DIAS_ATE_LIGACAO:
            continue
        if not _dia_util(agora.date()):
            continue                           # tarefa nunca nasce em fim de semana ou feriado
        if c.get("video_d3_em") == _hoje():
            continue                           # o video saiu hoje: da um dia para ele agir
        if (not c.get("video_d3_em") and not c["docs"] and _e_motorista(cid)
                and dias < DIAS_ATE_LIGACAO + 3):
            continue                           # o video ainda nao saiu (3o dia util chega primeiro)
        faltam = sorted({"cnh", "comprovante_residencia"} - set(c["docs"]))
        tem = sorted(c["docs"]) or None
        _mover_etapa(cid, ETAPA_LIGAR, executar)
        _tarefa_para_vendedora(cid, nome, tel, dias, faltam, executar)
        alerta_time(
            f"📞 LIGAR: {nome or 'lead sem nome no CRM'} · {tel}\n\n"
            f"Fechou com a gente há {dias} dias e não mandou "
            f"{' nem '.join(FALTA_CURTO[f] for f in faltam)}."
            + (f" Já mandou: {', '.join(FALTA_CURTO[t] for t in tem)}." if tem else "")
            + f"\nLevou {c.get('n_lembrete', 0)} lembretes automáticos e não voltou, "
            f"então parei de mandar mensagem para não queimar o número.\n\n"
            f"O card está em Contratação > {ETAPA_LIGAR} e a vendedora que fechou "
            f"recebeu tarefa urgente para ligar.", cfg, executar)
        if executar:
            c["ligacao_em"] = _hoje()


def fase_perda(est, cfg, executar):
    """Levou a mensagem de ULTIMO CONTATO e nao deu sinal ate o dia seguinte:
    vira perda declarada (Advogada, 27/08). O card vai para Contratação >
    Perdido, que existe justamente para medir o quanto este fluxo perde.

    Qualquer palavra do cliente cancela a perda, mesmo que ele nao mande o
    documento: quem respondeu ainda esta na conversa e merece o telefonema.
    A marca `ultimo_contato_em` so e posta por quem manda essa mensagem, entao
    esta fase nunca toca em quem esta so esperando ligacao.
    """
    for cid, nome, tel in _conversas(ETAPA_LIGAR):
        c = est["conversas"].get(str(cid))
        if not c or not c.get("ultimo_contato_em"):
            continue
        if c["ultimo_contato_em"] >= _hoje():
            continue  # ainda e o dia do aviso: ele tem o dia inteiro para voltar
        if _ultima_msg_do_contato(cid) > int(c.get("msg_no_ultimo_contato", 0)):
            print(f"  [{nome}] respondeu depois do último contato: NAO e perda.")
            if executar:
                c.pop("ultimo_contato_em", None)
                c.pop("msg_no_ultimo_contato", None)
            alerta_time(f"🙌 {nome} ({tel}) respondeu depois da mensagem de último "
                        f"contato. Não marquei como perdido: o card segue em "
                        f"{ETAPA_LIGAR} para alguém falar com ele.", cfg, executar)
            continue
        _mover_etapa(cid, ETAPA_PERDIDO, executar)
        if executar:
            c["perdido_em"] = _hoje()
        alerta_time(f"❌ {nome} ({tel}) não respondeu à mensagem de último contato. "
                    f"Card movido para Contratação > {ETAPA_PERDIDO}.", cfg, executar)


def fase_intake(est, cfg, executar):
    for cid, nome, tel in _conversas(ETAPA_CONFERIDO):
        c = est["conversas"].get(str(cid))
        if not c:
            c = est["conversas"].setdefault(str(cid), {
                "nome": nome or "", "telefone": tel or "", "pedido_em": _agora(),
                "ultima_msg_vista": 0, "docs": {}, "ultimo_lembrete": None,
                "intake": "pendente", "alertado_hoje": None})
        # falha de intake NAO trava mais o card pra sempre (caso Benicio, 12/08):
        # a IVA pode ter devolvido uma resposta ruim, entao tentamos de novo nas
        # rodadas seguintes ate MAX_TENTATIVAS_INTAKE antes de exigir mao humana.
        if c["intake"] == "ok":
            continue
        if c["intake"] == "falhou" and c.get("intake_tentativas", 1) >= MAX_TENTATIVAS_INTAKE:
            continue
        if not executar:
            print(f"  [{nome}] intake pendente (dry-run: nao rodo).")
            continue
        transcricao, trans_id, ids_ficha = buscar_transcricao_drive(nome or "", cid)
        if not transcricao:
            # 4a fonte: a ligacao de fechamento gravada pelo proprio sistema
            transcricao = transcricao_da_ligacao(cid)
        if not transcricao:
            if c["alertado_hoje"] != _hoje():
                alerta_time(f"⚠️ Contratação: documentos do {nome} estão completos, mas "
                            f"não achei a transcrição do fechamento. Cole o LINK dela na "
                            f"ficha do card no CRM (ou suba no Drive com o nome do cliente "
                            f"+ 'transcricao') que eu rodo o intake sozinho na próxima "
                            f"varredura.", cfg, executar)
                c["alertado_hoje"] = _hoje()
            continue
        print(f"  [{nome}] rodando intake...")
        c["intake"] = "rodando"
        _salvar(est)
        ok, log = rodar_intake(transcricao, list(c["docs"].values()),
                               dados_crm=_dados_crm(cid),
                               qualificacao=c.get("qualificacao"))
        _guardar_log_intake(cid, nome, log)
        print("    --- log intake (final) ---")
        print("    " + log[-1200:].replace("\n", "\n    "))
        # A trava da reclamada foi desligada no mesmo dia 14/10/2026, por
        # ordem da Advogada: "nao quero iva me mandando mensagem para confirmar a
        # reclamada". Sem o nome, o contrato sai com a mencao padrao "em
        # desfavor do seu empregador" e a contratacao segue. Nao existe mais
        # falha de reclamada para o vigia tratar, e por isso nao ha aviso aqui.
        if not ok:
            tent = c.get("intake_tentativas", 0) + 1
            c["intake"] = "falhou"
            c["intake_tentativas"] = tent
            resto = MAX_TENTATIVAS_INTAKE - tent
            alerta_time(f"🛑 Contratação: intake do {nome} FALHOU ({tent}/"
                        f"{MAX_TENTATIVAS_INTAKE}). Último erro: "
                        f"{log.splitlines()[-1][:200] if log else '?'} "
                        + (f"Tento de novo na próxima varredura."
                           if resto > 0 else
                           "Desisti: precisa rodar manual pelo Sistema Escritório Modelo.")
                        + f" Log: {LOG_INTAKE}", cfg, executar)
            continue
        c["intake"] = "ok"
        m_pasta = re.search(r"PASTA_ID: (\S+)", log or "")
        if m_pasta:  # move os arquivos soltos da ficha p/ a pasta criada (Advogada 12/08)
            for fid in dict.fromkeys(([trans_id] if trans_id else []) + ids_ficha):
                mover_para_pasta_cliente(fid, m_pasta.group(1))
        nome_contrato = _nome_do_contrato(log, nome)
        if nome_contrato != nome:
            print(f"    nome do contrato: {nome_contrato} (card: {nome})")
            c["nome_contrato"] = nome_contrato
        if m_pasta:
            c["pasta_id"] = m_pasta.group(1)
        if c.get("pasta_id"):  # CNH/comprovante em PDF nomeado na pasta (Advogada 04/09)
            subir_docs_contratacao(c, nome_contrato, c["pasta_id"], executar)
        links = enroll_assinatura_e_links(nome_contrato, tel, executar)
        if links:
            zapi_texto(tel, MSG_LINKS.replace("{nome}", (nome or nome_contrato).split()[0])
                                     .replace("{links}", links))
            print("    links de assinatura enviados.")
            # video "Como assinar pelo celular" junto (Titular, 25/09/2026); marca a
            # fila para o lembrete da cobranca_assinatura nao mandar de novo
            if executar:
                try:
                    import cobranca_assinatura as ca
                    fila_ca = ca.carregar()
                    _viva = ca.inscricao_viva(fila_ca["clientes"], nome_contrato, tel)
                    cc = _viva[1] if _viva else None
                    if cc is not None and not cc.get("video_tutorial") and ca.enviar_video_tutorial(tel):
                        cc["video_tutorial"] = datetime.now().strftime("%Y-%m-%d")
                        ca.salvar(fila_ca)
                        print("    video tutorial de assinatura enviado junto.")
                except Exception as e:
                    print(f"    (video tutorial falhou: {str(e)[:150]})")
        else:
            alerta_time(f"⚠️ Contratação: intake do {nome} concluiu, mas não achei os "
                        f"links no ZapSign para enviar. Conferir e mandar manual.", cfg, executar)
        _mover_etapa(cid, ETAPA_ZAPSIGN, executar)


def fase_atualizacao_diaria(est, cfg, executar):
    """Atualizacao DIARIA ao cliente enquanto esta na 'Fase de Contratação'
    (Advogada, 12/08: cliente sempre sabendo em que fase esta). As etapas de
    assinatura ja tem toque diario proprio (cobranca), entao nao duplicamos."""
    for cid, nome, tel in _conversas(ETAPA_CONFERIDO):
        c = est["conversas"].get(str(cid))
        if not c or not tel:
            continue
        # nao atualiza no mesmo dia em que entrou (ja recebeu o 'recebi tudo')
        if (c.get("pedido_em") or "")[:10] == _hoje() or c.get("ultimo_update") == _hoje():
            continue
        texto = MSG_ATUALIZACAO_DIARIA.replace("{nome}", (nome or "").split()[0])
        if executar:
            if zapi_texto(tel, texto):
                c["ultimo_update"] = _hoje()
                print(f"  [{nome}] atualizacao diaria enviada (Fase de Contratação).")
        else:
            print(f"  [{nome}] atualizacao diaria (dry-run).")


def fase_assinado(est, cfg, executar):
    sys.stdout.flush()  # idem enroll: o import reembrulha o stdout
    sys.path.insert(0, _DIR)
    import cobranca_assinatura as ca
    fila = ca.carregar()["clientes"]
    for cid, nome, tel in _conversas(ETAPA_ZAPSIGN):
        ent = fila.get(nome) or next(
            (v for v in fila.values()
             if v.get("telefone") and tel and v["telefone"][-8:] == str(tel)[-8:]), None)
        if ent and ent.get("assinou"):
            print(f"  [{nome}] assinou! Movendo para '{ETAPA_ASSINADO}'.")
            _mover_etapa(cid, ETAPA_ASSINADO, executar)

    # 'Assinado' e caixa de TRANSITO (Advogada, 11/09/2026): quem assinou segue
    # sozinho para Produção > Aguardando Documentos, onde roda a cobranca
    # de provas. Antes ninguem fazia essa travessia e os cards apodreciam aqui
    # (Ezequiel e a equipe). Usa o mover da varredura: respeita a trava do
    # board e escreve na trilha wa_board_hist.
    import varredura_provas as vp
    for cid, nome, tel in _conversas(ETAPA_ASSINADO):
        print(f"  [{nome}] Assinado -> Produção > Aguardando Documentos")
        vp.mover_para_producao(cid, executar, etapa="Aguardando Documentos",
                               quem="auto:contratacao-watcher",
                               origem="script:contratacao_watcher")
        _garantir_fila_documentos(cid, nome, tel, fila, executar)


def _garantir_fila_documentos(cid, nome, tel, fila_assinatura, executar):
    """Todo card que chega em Aguardando Documentos precisa estar na fila da cobranca de
    documentos: e por ela que a varredura de provas acha o cliente, guarda os arquivos na
    pasta e anda o card. A porta normal e a cobranca de assinatura (ao ver a assinatura),
    mas ela pula quem esta encerrado: o Jonathan Garcia (automaticas desligadas, contrato a
    mao) assinou em 07/10/2026 e ficou fora da varredura (achado de 08/10). Cliente ainda
    ativo na cobranca de assinatura fica com ela, que inscreve na proxima rodada (senao
    duplicaria); sem entrada ou com entrada encerrada, inscreve aqui. Automaticas desligadas
    pela equipe = inscricao em silencio: nenhuma mensagem, so a varredura captando."""
    t8 = re.sub(r"\D", "", str(tel or ""))[-8:]
    if not t8:
        return
    sys.stdout.flush()  # o import reembrulha o stdout
    import cobranca_documentos as cd
    if any(re.sub(r"\D", "", str(v.get("telefone") or ""))[-8:] == t8
           for v in cd.carregar()["clientes"].values()):
        return
    ent = next((v for v in fila_assinatura.values()
                if re.sub(r"\D", "", str(v.get("telefone") or ""))[-8:] == t8), None)
    if ent and not ent.get("encerrado"):
        return
    silencio = bool(ent and re.search(r"n[aã]o cobrar|automatic|deslig",
                                      ent.get("motivo_encerramento") or "", re.I))
    nome_fila = (ent or {}).get("nome") or nome
    print(f"  [{nome}] fora da fila de documentos: inscrevendo como '{nome_fila}'"
          + (" em silencio (automaticas desligadas)" if silencio else ""))
    if not executar:
        return
    cd.enroll_cliente(nome_fila, tel or "", cid=cid)
    if silencio:
        f = cd.carregar()
        c = f["clientes"][nome_fila]
        c["encerrado"] = True
        c["encerrado_motivo"] = ("inscrito em silencio pelo vigia da contratacao: automaticas "
                                 "desligadas na cobranca de assinatura; a varredura capta os "
                                 "arquivos e anda o card")
        cd.salvar(f)


def rodar(executar=False):
    print(f"=== VIGIA CONTRATACAO | {_agora()[:16]} | " + ("EXECUTAR" if executar else "DRY-RUN") + " ===")
    if not os.path.exists(DB):
        print(f"Banco nao encontrado: {DB} (rodar na VPS)."); return
    cfg = _cfg()
    est = _estado()
    fase_pedido(est, cfg, executar)
    _salvar(est)
    fase_recepcao(est, cfg, executar)
    _salvar(est)
    fase_ligacao(est, cfg, executar)
    _salvar(est)
    fase_perda(est, cfg, executar)
    _salvar(est)
    fase_intake(est, cfg, executar)
    _salvar(est)
    fase_atualizacao_diaria(est, cfg, executar)
    _salvar(est)
    fase_assinado(est, cfg, executar)
    _salvar(est)
    print("=== fim ===")


def status():
    est = _estado()
    if not est["conversas"]:
        print("Nenhuma contratacao acompanhada."); return
    for cid, c in est["conversas"].items():
        docs = ", ".join(sorted(c.get("docs", {}))) or "nenhum"
        print(f"  [{cid}] {c.get('nome')} | pedido {str(c.get('pedido_em'))[:16]} "
              f"| docs: {docs} | intake: {c.get('intake')}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--executar", action="store_true")
    p.add_argument("--status", action="store_true")
    a = p.parse_args()
    if a.status:
        status()
    else:
        rodar(a.executar)


if __name__ == "__main__":
    main()
