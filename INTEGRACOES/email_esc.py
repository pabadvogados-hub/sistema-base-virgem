# -*- coding: utf-8 -*-
"""
E-MAIL EM NOME DO ESCRITORIO (Escritório Modelo Advogados), com anexo.

Envia pela propria caixa do escritorio. Se o correio do escritorio for Microsoft 365 -- o MX do dominio do escritorio aponta para
o Microsoft 365 (`<dominio>.mail.protection.outlook.com`) e o SPF e
`v=spf1 include:spf.protection.outlook.com -all`, com hard fail. Mandar como
@<dominio do escritorio> por um remetente de fora seria rejeitado ou cairia em
spam, que e o pior lugar para um e-mail que cobra prazo de acordo.

Entao o caminho e o proprio Microsoft 365, em dois sabores:

  graph  (preferido)  Microsoft Graph com credencial de aplicativo. O e-mail
                      sai da caixa real, fica na pasta Enviados e a resposta do
                      cliente volta para o escritorio. Precisa de um registro
                      de aplicativo no portal do Microsoft 365 com permissao
                      Mail.Send (application) e consentimento do admin.
                      Variaveis: MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET,
                      ESC_EMAIL_REMETENTE.
  smtp   (reserva)    smtp.office365.com:587 com senha de aplicativo.
                      Variaveis: ESC_SMTP_USUARIO, ESC_SMTP_SENHA,
                      ESC_EMAIL_REMETENTE. So funciona se o tenant ainda
                      permitir SMTP AUTH, que a Microsoft desliga por padrao.

REGRA DE OURO: sem remetente configurado este modulo NAO tenta improvisar
outro caminho. Ele devolve (False, motivo) para quem chamou abrir pendencia
humana. Um parecer terminativo que sai de um endereco errado e pior do que um
parecer que atrasa.

Uso:
    py INTEGRACOES/email_esc.py --diagnostico
    py INTEGRACOES/email_esc.py --teste destino@exemplo.com
"""
import base64
import mimetypes
import os
import smtplib
import sys
from email.message import EmailMessage

import requests

GRAPH = "https://graph.microsoft.com/v1.0"
LIMITE_ANEXO_MB = 3  # sendMail inline; acima disso o Graph exige upload session


def remetente():
    return os.getenv("ESC_EMAIL_REMETENTE") or os.getenv("ESC_SMTP_USUARIO")


def backend_disponivel():
    """Devolve 'graph', 'smtp' ou None, sem tentar autenticar."""
    if all(os.getenv(k) for k in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET")) \
            and remetente():
        return "graph"
    if os.getenv("ESC_SMTP_USUARIO") and os.getenv("ESC_SMTP_SENHA"):
        return "smtp"
    return None


def _token_graph():
    url = ("https://login.microsoftonline.com/%s/oauth2/v2.0/token"
           % os.getenv("MS_TENANT_ID"))
    dados = {
        "grant_type": "client_credentials",
        "client_id": os.getenv("MS_CLIENT_ID"),
        "client_secret": os.getenv("MS_CLIENT_SECRET"),
        "scope": "https://graph.microsoft.com/.default",
    }
    r = requests.post(url, data=dados, timeout=40)
    if r.status_code != 200:
        return None, "token Graph recusado (HTTP %s): %s" % (r.status_code, r.text[:220])
    return r.json().get("access_token"), None


def _anexos_em_bytes(caminhos):
    """Le os anexos e devolve (lista_graph, lista_smtp, erros)."""
    graph, cru, erros = [], [], []
    for c in caminhos or []:
        if not c or not os.path.exists(c):
            erros.append("anexo nao encontrado: %s" % c)
            continue
        tamanho = os.path.getsize(c)
        if tamanho > LIMITE_ANEXO_MB * 1024 * 1024:
            erros.append("anexo grande demais (%.1f MB, limite %d MB): %s"
                         % (tamanho / 1048576.0, LIMITE_ANEXO_MB, os.path.basename(c)))
            continue
        with open(c, "rb") as f:
            conteudo = f.read()
        nome = os.path.basename(c)
        tipo = mimetypes.guess_type(nome)[0] or "application/octet-stream"
        graph.append({
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": nome,
            "contentType": tipo,
            "contentBytes": base64.b64encode(conteudo).decode(),
        })
        cru.append((nome, tipo, conteudo))
    return graph, cru, erros


def _enviar_graph(para, assunto, texto, html, anexos, cc, responder_para):
    token, erro = _token_graph()
    if not token:
        return False, erro
    graph_anexos, _, erros_anexo = _anexos_em_bytes(anexos)
    if erros_anexo:
        return False, "; ".join(erros_anexo)

    destinatarios = [{"emailAddress": {"address": e}} for e in _lista(para)]
    mensagem = {
        "subject": assunto,
        "body": {"contentType": "HTML" if html else "Text", "content": html or texto},
        "toRecipients": destinatarios,
    }
    if cc:
        mensagem["ccRecipients"] = [{"emailAddress": {"address": e}} for e in _lista(cc)]
    if responder_para:
        mensagem["replyTo"] = [{"emailAddress": {"address": e}} for e in _lista(responder_para)]
    if graph_anexos:
        mensagem["attachments"] = graph_anexos

    url = "%s/users/%s/sendMail" % (GRAPH, remetente())
    r = requests.post(url, timeout=90,
                      headers={"Authorization": "Bearer %s" % token,
                               "Content-Type": "application/json"},
                      json={"message": mensagem, "saveToSentItems": True})
    if r.status_code in (200, 202):
        return True, None
    return False, "Graph recusou (HTTP %s): %s" % (r.status_code, r.text[:300])


def _enviar_smtp(para, assunto, texto, html, anexos, cc, responder_para):
    usuario = os.getenv("ESC_SMTP_USUARIO")
    senha = os.getenv("ESC_SMTP_SENHA")
    de = remetente() or usuario
    _, anexos_cru, erros_anexo = _anexos_em_bytes(anexos)
    if erros_anexo:
        return False, "; ".join(erros_anexo)

    msg = EmailMessage()
    msg["From"] = "Escritório Modelo Advogados <%s>" % de
    msg["To"] = ", ".join(_lista(para))
    if cc:
        msg["Cc"] = ", ".join(_lista(cc))
    if responder_para:
        msg["Reply-To"] = ", ".join(_lista(responder_para))
    msg["Subject"] = assunto
    msg.set_content(texto or "")
    if html:
        msg.add_alternative(html, subtype="html")
    for nome, tipo, conteudo in anexos_cru:
        principal, _, sub = tipo.partition("/")
        msg.add_attachment(conteudo, maintype=principal, subtype=sub or "octet-stream",
                           filename=nome)
    try:
        with smtplib.SMTP("smtp.office365.com", 587, timeout=60) as s:
            s.starttls()
            s.login(usuario, senha)
            s.send_message(msg)
        return True, None
    except Exception as e:
        return False, "SMTP falhou: %s: %s" % (type(e).__name__, e)


def _lista(v):
    if not v:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.replace(";", ",").split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def enviar(para, assunto, texto, html=None, anexos=None, cc=None,
           responder_para=None, executar=False):
    """
    Manda o e-mail. Devolve (ok, motivo_do_erro).

    Sem `executar=True` nao manda nada: devolve (True, None) e imprime o que
    faria. Isso mantem o watcher testavel sem torrar a caixa do cliente.
    """
    destinos = _lista(para)
    if not destinos:
        return False, "sem destinatario"
    backend = backend_disponivel()
    if not backend:
        return False, ("remetente do escritorio nao configurado "
                       "(faltam MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET "
                       "ou ESC_SMTP_USUARIO/ESC_SMTP_SENHA)")
    if not executar:
        print("[email_esc/DRY-RUN] backend=%s de=%s para=%s" % (backend, remetente(), destinos))
        print("  assunto: %s" % assunto)
        print("  anexos: %s" % ([os.path.basename(a) for a in (anexos or [])],))
        return True, None
    if backend == "graph":
        return _enviar_graph(destinos, assunto, texto, html, anexos, cc, responder_para)
    return _enviar_smtp(destinos, assunto, texto, html, anexos, cc, responder_para)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.getenv("ESC_CONFIG_ENV", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", ".env")))
    if os.path.exists("/opt/escritorio/config/.env"):
        load_dotenv("/opt/escritorio/config/.env")

    if "--diagnostico" in sys.argv:
        b = backend_disponivel()
        print("backend disponivel :", b or "NENHUM")
        print("remetente          :", remetente() or "(nao definido)")
        for k in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET",
                  "ESC_SMTP_USUARIO", "ESC_SMTP_SENHA", "ESC_EMAIL_REMETENTE"):
            print("  %-20s %s" % (k, "definido" if os.getenv(k) else "-"))
        if b == "graph":
            tok, erro = _token_graph()
            print("token Graph        :", "OK" if tok else erro)
    elif "--teste" in sys.argv:
        destino = sys.argv[sys.argv.index("--teste") + 1]
        ok, erro = enviar(destino, "Teste Escritório Modelo Advogados",
                          "Teste de envio pelo modulo email_esc.",
                          executar=True)
        print("enviado:", ok, erro or "")
    else:
        print(__doc__)
