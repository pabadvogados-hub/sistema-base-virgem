# -*- coding: utf-8 -*-
"""Le do texto da intimacao os dados de AUDIENCIA ou PERICIA designada.

Por que existe: o motor de prazos classifica o ATO (contestacao, ciencia,
recurso), mas nao extrai QUANDO e ONDE o compromisso acontece. Esses dados
estao no corpo do despacho e sao o que a agenda e o lembrete do cliente
precisam: data, hora, modalidade, link da sala virtual e endereco do forum.

CUIDADO COM FALSO POSITIVO: quase toda intimacao fala a palavra "audiencia"
em algum lugar ("conforme determinado em ata de audiencia"). Designacao de
verdade tem VERBO DE CONVOCACAO perto ("comparecer a", "designada para",
"a realizar-se no dia", "notificacao de audiencia") mais DATA E HORA. Sem os
dois, devolve tem=False.

Testado contra os textos reais dos TRT 9, 15 e 17, que escrevem de tres
jeitos diferentes.
"""
import re
import unicodedata

# convocacao de verdade, nao mencao de passagem
_CONVOCA = re.compile(
    r"(COMPARECER\s+[AÀ]|DESIGNAD[AO]\s+PARA|A\s+REALIZAR-SE|REALIZAR-SE\s+NO\s+DIA|"
    r"NOTIFICAC[AÃ]O\s+DE\s+AUDI[EÊ]NCIA|INTIMAC[AÃ]O\s+DE\s+AUDI[EÊ]NCIA|"
    r"FICA\s+DESIGNAD|REDESIGNAD|"
    # inclusao em pauta tambem marca DATA na agenda, ainda que o cliente nao
    # compareca. Ele nao e lembrado; nos precisamos saber.
    r"INCLU[IÍ]D[AO]\s+NA\s+PAUTA|DATA\s+DA\s+SESS[AÃ]O|PAUTA\s+DE\s+JULGAMENTO|"
    r"SESS[AÃ]O\s+DE\s+JULGAMENTO)", re.I)

# marca de sessao de julgamento: tem data, entra na agenda, nao gera lembrete
_PAUTA = re.compile(r"(PAUTA\s+DE\s+JULGAMENTO|SESS[AÃ]O\s+DE\s+JULGAMENTO|"
                    r"INCLU[IÍ]D[AO]\s+NA\s+PAUTA|DATA\s+DA\s+SESS[AÃ]O)", re.I)

# 30/09/2026 13:50  |  30/09/2026 as 13h50  |  30/09/2026, às 13:50h
_DATA_HORA = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s*(?:,)?\s*(?:[àa]s\s*)?(\d{1,2})[:h](\d{2})", re.I)
_SO_DATA = re.compile(r"(\d{2}/\d{2}/\d{4})")

_LINK = re.compile(r"https?://[^\s,;)\]]+", re.I)

# "situada na AV X, 123, ... - CEP: 12345-678"
_ENDERECO = re.compile(
    r"situad[ao]\s+n[ao]\s+(.{10,220}?)(?:\.\s|\sFaculta|\sO\s+n[aã]o|$)", re.I | re.S)
_CEP = re.compile(r"CEP[:\s]*\d{5}-?\d{3}", re.I)

_ESPECIES = ["UNA", "INICIAL", "INSTRUCAO", "CONCILIACAO", "JULGAMENTO",
             "PERICIA", "PERICIAL"]



# Convocacao de AUDIENCIA de verdade. Precisa existir porque textos de
# audiencia costumam falar "sessao" ("compareca a sessao pelo link"), e sem
# esta precedencia uma audiencia real virava julgamento - e julgamento nao
# lembra o cliente, entao ele nunca seria avisado de comparecer.
_CONV_AUD = re.compile(
    r"(NOTIFICAC[AÃ]O\s+DE\s+AUDI[EÊ]NCIA|INTIMAC[AÃ]O\s+DE\s+AUDI[EÊ]NCIA|"
    r"COMPARECER\s+[AÀ]\s+AUDI[EÊ]NCIA|AUDI[EÊ]NCIA[^.]{0,60}DESIGNAD|"
    r"AUDI[EÊ]NCIA[^.]{0,60}REALIZAR-SE)", re.I)

def _norm(s):
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().upper()


def _modalidade(t):
    """virtual | presencial | hibrida | None.

    Hibrida vem antes: "PRESENCIAL/HIBRIDA" contem as duas palavras e o que
    vale para o cliente e que ele PODE ter de ir.
    """
    if "HIBRID" in t:
        return "hibrida"
    if ("VIRTUAL" in t or "VIDEOCONFERENCIA" in t or "TELEPRESENCIAL" in t
            or "ZOOM" in t or "MEET" in t):
        return "virtual"
    if "PRESENCIAL" in t:
        return "presencial"
    return None


def _especie(t):
    for e in _ESPECIES:
        if re.search(r"AUDI[EÊ]NCIA\s+(?:\w+\s+){0,2}" + e, t) or (e + " POR VIDEO") in t:
            return e
    if "PERICIA" in t or "PERICIAL" in t:
        return "PERICIA"
    return ""



# Sala de verdade x pagina de instrucoes do tribunal. O texto do TRT15 traz
# primeiro o portal de orientacoes (sites.google.com/.../zoomadv) e so depois
# o link da sala (bit.ly/...). Pegar o primeiro manda o cliente para a pagina
# errada, entao os candidatos sao pontuados.
_SALA = re.compile(r"(zoom\.us/j/|meet\.google\.com/|teams\.microsoft\.com/|"
                   r"bit\.ly/|encurtador|/sala|videoconferencia/)", re.I)
_INSTRUCAO = re.compile(r"(sites\.google\.com|/orientac|/tutorial|pagina-inicial|"
                        r"/duvidas|/faq)", re.I)
_PERTO_SALA = re.compile(r"(SALA|ACESS|CONECT|INGRESS|LINK\s+DA)", re.I)


def _melhor_link(texto, resumo=""):
    """O link da SALA, nao o da pagina de instrucoes."""
    cands = []
    for fonte, peso_fonte in ((texto, 0), (str(resumo or ""), 1)):
        for m in _LINK.finditer(fonte):
            url = m.group(0).rstrip(".,;)")
            volta = fonte[max(0, m.start() - 160):m.start()]
            p = peso_fonte
            if _SALA.search(url):
                p += 4
            if _INSTRUCAO.search(url):
                p -= 3
            if _PERTO_SALA.search(volta):
                p += 2
            cands.append((p, url))
    if not cands:
        return ""
    cands.sort(key=lambda x: -x[0])
    return cands[0][1] if cands[0][0] > 0 else ""


def _endereco(texto):
    m = _ENDERECO.search(texto)
    if not m:
        return ""
    end = " ".join(m.group(1).split())
    # corta no CEP, que e onde o endereco termina de fato
    c = _CEP.search(end)
    if c:
        end = end[:c.end()]
    return end.strip(" ,;.")


def extrair(texto, orgao="", resumo=""):
    """Dados do compromisso designado, ou tem=False.

    `resumo` entra so como reforco de deteccao; datas e endereco saem sempre
    do TEXTO, que e a fonte oficial.
    """
    texto = str(texto or "")
    t = _norm(texto + " " + str(resumo or ""))
    avisos = []
    # a lista de intimacoes traz um preview de ~600 chars terminado em
    # reticencias. Endereco e link costumam ficar depois do corte, e endereco
    # cortado indo para cliente e acidente. Quem chama deve passar o TEOR.
    if texto.rstrip().endswith(("…", "...")) and len(texto) < 1200:
        avisos.append("Texto parece preview truncado. Conferir o teor completo.")

    if not _CONVOCA.search(texto) and not _CONVOCA.search(str(resumo or "")):
        return {"tem": False, "motivo": "sem verbo de convocacao no texto"}

    ehp = "PERICIA" in t or "PERICIAL" in t
    junto = texto + " " + str(resumo or "")
    eha = bool(_CONV_AUD.search(junto))
    # audiencia ganha de pauta: o texto de audiencia fala "sessao" o tempo todo
    ehj = (not eha) and bool(_PAUTA.search(junto))
    if not ehp and not ehj and "AUDIENCIA" not in t:
        return {"tem": False,
                "motivo": "nao fala de audiencia, pericia nem sessao de julgamento"}

    m = _DATA_HORA.search(texto)
    if m:
        dia, mes, ano = m.group(1).split("/")
        data = "%s-%s-%s" % (ano, mes, dia)
        hora = "%02d:%s" % (int(m.group(2)), m.group(3))
    else:
        d = _SO_DATA.search(texto)
        if not d:
            return {"tem": False, "motivo": "convocacao sem data no texto"}
        dia, mes, ano = d.group(1).split("/")
        data, hora = "%s-%s-%s" % (ano, mes, dia), ""
        avisos.append("Data encontrada, mas sem horario. Conferir no despacho.")

    modalidade = _modalidade(t)
    if not modalidade and not ehj:
        avisos.append("Modalidade nao declarada. Conferir se e presencial ou virtual.")

    link = _melhor_link(texto, resumo)

    endereco = _endereco(texto)
    if modalidade in ("presencial", "hibrida") and not ehj and not endereco:
        avisos.append("Presencial sem endereco no texto. NAO mandar endereco ao cliente.")
    if modalidade == "virtual" and not link:
        avisos.append("Virtual sem link no texto. Conferir antes de avisar o cliente.")

    # o que vai no campo Local do evento
    if ehj:
        # sessao de julgamento: o que importa e o orgao julgador e a pauta
        local = (orgao or "").strip() or "Sessão de julgamento"
    elif modalidade == "virtual":
        local = link or "Videoconferência"
    else:
        local = " ".join(x for x in [(orgao or "").strip(), endereco] if x).strip()

    return {
        "tem": True,
        # julgamento entra na agenda mas NAO gera lembrete: o cliente nao
        # comparece. Quem filtra isso e a rotina lembretes_compromisso.py.
        "tipo": "pericia" if ehp else ("julgamento" if ehj else "audiencia"),
        "lembra_cliente": not ehj,
        "especie": _especie(t),
        "data": data,
        "hora": hora,
        "modalidade": modalidade or "",
        "link": link,
        "endereco": endereco,
        "orgao": (orgao or "").strip(),
        "local": local,
        "avisos": avisos,
    }
