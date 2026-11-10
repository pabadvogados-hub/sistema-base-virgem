# -*- coding: utf-8 -*-
"""
leitura_da_conversa.py  -  LER ANTES DE PEDIR.

REGRA DA CASA:

  "quero que você cadastre um vigia para fazer uma leitura da conversa antes de
   fazer um novo pedido de documento, porque ela precisa:
     - ler
     - entender o que foi enviado pelo cliente
     - analisar se o enviado pelo cliente está legível
   Daí sim, você pode continuar com as fases da sua automação"

POR QUE ISSO NASCEU. O robô pedia documento sem saber direito o que já tinha.
Pior: quando o cliente mandava, a esteira só dizia QUE TIPO era o arquivo e
nunca se DAVA PARA LER. Uma CNH ilegível entrava como "cnh", o intake seguia com
ela e o contrato podia sair com CPF e RG em branco, no formato {{campo do modelo}}.

Este módulo é o PASSO ZERO de toda rodada do vigia de contratação. Ele devolve
um RETRATO da conversa e não decide nada, não fala com o cliente e não move
card. Quem decide é o `contratacao_documentos.avaliar`, que recebe este retrato.

ECONOMIA (também ordem dela: "além de gastar tokens ainda vira trabalho extra
para a equipe"):
  - mídia já lida antes não é baixada nem mandada para a visão de novo;
  - a leitura é UMA chamada por arquivo, que já devolve tipo, legibilidade e
    campos juntos (antes eram duas passadas, e nenhuma via legibilidade);
  - o que foi lido fica guardado e serve para o contrato sem releitura.
"""

import io
import json
import os
import time

try:
    import dados_do_cliente as ddc
except ImportError:                                   # rodando de outra pasta
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import dados_do_cliente as ddc


# quanto tempo um documento lido continua valendo sem reler (dias)
VALIDADE_LEITURA = 90

# quantas mídias novas ler por rodada, por conversa. A conversa de um cliente
# pode passar de 40 arquivos: ler tudo de uma vez numa rodada de
# 30 em 30 minutos trava o vigia e queima assinatura à toa. O resto entra na
# rodada seguinte, e o que interessa para o contrato (CNH, comprovante, CTPS)
# costuma vir nas primeiras mensagens depois do pedido.
MAX_POR_RODADA = 12


class LeituraDaConversa(object):
    """Guarda o que já foi lido de cada conversa, por id de mídia.

    A chave é a CONVERSA e a MÍDIA, nunca o nome do cliente. Era o nome que
    fazia a varredura recomeçar do zero quando o cliente era renomeado, e foi
    isso que duplicou a pasta inteira do Heitor no Drive.
    """

    def __init__(self, caminho):
        self.caminho = caminho
        try:
            with io.open(caminho, encoding="utf-8") as f:
                self.dados = json.load(f)
        except Exception:
            self.dados = {"conversas": {}}
        self.dados.setdefault("conversas", {})

    def _conv(self, cid):
        return self.dados["conversas"].setdefault(
            str(cid), {"midias": {}, "ultima_leitura": None})

    def lida(self, cid, mid):
        reg = self._conv(cid)["midias"].get(str(mid))
        if not reg:
            return None
        if time.time() - reg.get("em", 0) > VALIDADE_LEITURA * 86400:
            return None
        return reg

    def guardar(self, cid, mid, leitura):
        self._conv(cid)["midias"][str(mid)] = {
            "tipo": leitura.get("tipo"),
            "legivel": leitura.get("legivel"),
            "motivo": leitura.get("motivo", ""),
            "campos": leitura.get("campos") or {},
            "arquivo": leitura.get("arquivo", ""),
            "em": time.time()}

    def esquecer(self, cid, mid):
        """Tira uma mídia do registro para ela ser lida de novo na próxima
        rodada. Serve quando a leitura foi guardada errada e alguém mandou
        refazer."""
        self._conv(cid)["midias"].pop(str(mid), None)

    def salvar(self):
        tmp = self.caminho + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(self.dados, ensure_ascii=False, indent=1))
        os.replace(tmp, self.caminho)


def ler(cid, midias, baixar, registro, texto_conversa="", entrevista=None,
        card=None, log=print, max_por_rodada=MAX_POR_RODADA, guardar=None):
    """O passo zero: lê a conversa e devolve o retrato do que o cliente mandou.

    cid .............. id da conversa no CRM
    midias ........... [(mid, url), ...] das mensagens do cliente, em ordem
    baixar ........... callable(mid, url) -> (bytes, content_type) | (None, None)
    registro ......... LeituraDaConversa
    texto_conversa ... tudo que o CLIENTE escreveu (para dado sem documento)
    guardar .......... callable(mid, tipo, bytes, ctype) -> caminho local. Chamado
                       so para CNH, CTPS e comprovante LEGIVEIS, para o arquivo
                       ficar salvo e servir ao intake e ao Drive sem baixar de
                       novo (a URL da Z-API expira)

    Retrato devolvido:
      documentos ..... {tipo: {"mid","campos","legivel","motivo"}} do MELHOR
                       arquivo de cada tipo que o cliente mandou
      ilegiveis ...... {tipo: motivo} dos tipos que só vieram ilegíveis
      dados .......... a qualificação montada (nome, CPF, endereço...)
      origem ......... de onde saiu cada campo, para a nota do card
      lidas_agora .... quantas mídias foram à visão nesta rodada
      reaproveitadas . quantas vieram do registro, sem gastar nada
      faltou_visao ... True = a visão não respondeu; NÃO decidir nada nesta
                       rodada, porque o retrato está incompleto
      pendentes ...... mídias que ficaram para a próxima rodada
    """
    retrato = {"documentos": {}, "ilegiveis": {}, "dados": {}, "origem": {},
               "lidas_agora": 0, "reaproveitadas": 0, "faltou_visao": False,
               "pendentes": 0, "outros": 0}

    interessam = ("cnh", "ctps", "comprovante_residencia")
    lidos = []          # [(mid, leitura)]
    novas = 0

    for mid, url in midias:
        ja = registro.lida(cid, mid)
        if ja:
            retrato["reaproveitadas"] += 1
            lidos.append((mid, ja))
            continue

        if novas >= max_por_rodada:
            retrato["pendentes"] += 1
            continue

        conteudo, ctype = baixar(mid, url)
        if conteudo is None:
            log(u"    mídia %s: indisponível (link expirado e sem cache)." % mid)
            registro.guardar(cid, mid, {"tipo": "indisponivel", "legivel": False,
                                        "motivo": u"arquivo não recuperado"})
            continue

        caminho = _gravar_temp(conteudo, ctype)
        if not caminho:
            registro.guardar(cid, mid, {"tipo": "outro", "legivel": True})
            continue
        try:
            leitura = ddc.ler_documento_completo(caminho)
        finally:
            _apagar(caminho)

        if leitura is None:
            # falha NOSSA (visão fora do ar). Não guarda e não decide: o
            # retrato ficaria mentindo que o cliente não mandou nada.
            log(u"    visão indisponível na mídia %s; retomo daqui na próxima rodada." % mid)
            retrato["faltou_visao"] = True
            break

        novas += 1
        retrato["lidas_agora"] += 1
        # o arquivo fica salvo ANTES de a URL da Z-API expirar (ela é temporária)
        if guardar and leitura["tipo"] in interessam and leitura["legivel"]:
            try:
                leitura["arquivo"] = guardar(mid, leitura["tipo"], conteudo, ctype)
            except Exception as e:
                log(u"    (não consegui guardar a mídia %s: %s)" % (mid, str(e)[:80]))
        registro.guardar(cid, mid, leitura)
        lidos.append((mid, leitura))
        if leitura["tipo"] in interessam:
            log(u"    mídia %s -> %s%s" % (
                mid, leitura["tipo"],
                u"" if leitura["legivel"] else u" (ILEGÍVEL: %s)" % leitura["motivo"]))

    # --- o melhor arquivo de cada tipo -------------------------------------
    # Preferência: legível e com mais campos preenchidos. O cliente costuma
    # mandar a CNH duas vezes (frente e verso, ou uma tremida e outra boa);
    # vale a melhor, não a última.
    for mid, leitura in lidos:
        tipo = leitura.get("tipo")
        if tipo not in interessam:
            if tipo not in ("indisponivel",):
                retrato["outros"] += 1
            continue
        campos = leitura.get("campos") or {}
        nota = (2 if leitura.get("legivel") else 0) + min(len(
            [v for v in campos.values() if v]), 1)
        atual = retrato["documentos"].get(tipo)
        if not atual or nota > atual["_nota"]:
            retrato["documentos"][tipo] = {
                "mid": mid, "campos": campos, "legivel": bool(leitura.get("legivel")),
                "motivo": leitura.get("motivo", ""),
                "arquivo": leitura.get("arquivo", ""), "_nota": nota}

    for tipo, d in retrato["documentos"].items():
        d.pop("_nota", None)
        if not d["legivel"]:
            retrato["ilegiveis"][tipo] = d["motivo"] or u"não deu para ler"

    # --- a qualificação, sem gastar visão de novo --------------------------
    dados, detalhe = ddc.qualificacao(
        {t: {"campos": d["campos"], "legivel": d["legivel"], "motivo": d["motivo"]}
         for t, d in retrato["documentos"].items()},
        texto_conversa=texto_conversa, entrevista=entrevista, card=card)
    retrato["dados"] = dados
    retrato["origem"] = detalhe.get("origem", {})

    registro._conv(cid)["ultima_leitura"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return retrato


def resumo(retrato):
    """Uma linha para o log e para a nota do card."""
    tem = [t for t, d in retrato["documentos"].items() if d["legivel"]]
    ruins = list(retrato["ilegiveis"].keys())
    partes = [u"li %d, reaproveitei %d" % (retrato["lidas_agora"],
                                           retrato["reaproveitadas"])]
    if tem:
        partes.append(u"tenho: " + ", ".join(tem))
    if ruins:
        partes.append(u"ilegível: " + ", ".join(ruins))
    if retrato["pendentes"]:
        partes.append(u"%d para a próxima rodada" % retrato["pendentes"])
    if retrato["faltou_visao"]:
        partes.append(u"VISÃO FORA DO AR, não decidi nada")
    return u" | ".join(partes)


_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
        "image/webp": ".webp", "application/pdf": ".pdf"}


def _gravar_temp(conteudo, ctype):
    ext = _EXT.get((ctype or "").split(";")[0].strip().lower())
    if not ext:
        if conteudo[:5] == b"%PDF-":
            ext = ".pdf"
        else:
            return None
    import tempfile
    fd, caminho = tempfile.mkstemp(prefix="esc_leitura_", suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(conteudo)
    return caminho


def _apagar(caminho):
    try:
        os.unlink(caminho)
    except OSError:
        pass
