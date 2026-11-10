# -*- coding: utf-8 -*-
"""
EMISSAO DE COBRANCA NO ASAAS (boleto/Pix) - conta do escritorio.

Por que existe, e por que NAO e pelo ADVBOX: o POP 28 manda criar a receita no
ADVBOX marcando "GERAR COBRANCA NO BOLETO". Esse checkbox so existe no painel.
A API do ADVBOX (`POST /transactions`) aceita apenas amount, date_due,
categories_id, debit_account, cost_centers_id, customers_id e lawsuits_id --
verificado em 08/09/2026 lendo a lista e o detalhe de /transactions, nenhum
campo de cobranca. Entao o boleto nasce aqui, no Asaas, e o ADVBOX recebe o
lancamento espelho para a contabilidade e o fechamento mensal continuarem
batendo.

Idempotencia por `externalReference`: rodar duas vezes nao cria dois boletos.
E a mesma trava do zapsign_token na esteira de contratacao.

SEGURANCA: `criar_cobranca` NAO envia nada sem `executar=True`. Sem a flag ela
devolve o payload que mandaria, para conferencia. Cobranca emitida chega no
cliente e nao se desfaz sem constrangimento.

Uso:
    py INTEGRACOES/asaas_cobranca.py --cliente 44.217.299/0001-06
    py INTEGRACOES/asaas_cobranca.py --ver pay_xxxxxxxx
"""
import os
import sys
import time

import requests

BASE = "https://api.asaas.com/v3"


def _headers():
    token = os.getenv("ASAAS_API_TOKEN")
    if not token:
        raise RuntimeError("ASAAS_API_TOKEN ausente no ambiente")
    return {"access_token": token, "User-Agent": "Escritorio-Advogados/1.0"}


def _req(method, endpoint, params=None, json_data=None, retries=2):
    """Devolve (status, corpo). NUNCA levanta: o 400 do Asaas traz a razao da
    recusa e essa razao precisa chegar legivel na pendencia humana."""
    url = BASE + endpoint
    for tentativa in range(retries + 1):
        try:
            r = requests.request(method, url, headers=_headers(),
                                 params=params, json=json_data, timeout=40)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 10)))
                continue
            try:
                corpo = r.json() if r.text else {}
            except ValueError:
                corpo = {"raw": r.text[:400]}
            if r.status_code >= 500 and tentativa < retries:
                time.sleep(3)
                continue
            return r.status_code, corpo
        except requests.exceptions.RequestException as e:
            if tentativa < retries:
                time.sleep(3)
                continue
            return 0, {"erro_conexao": "%s: %s" % (type(e).__name__, e)}
    return 0, {}


def erros_legiveis(corpo):
    """Transforma o bloco de erro do Asaas em uma linha por problema."""
    if not isinstance(corpo, dict):
        return [str(corpo)[:200]]
    saida = []
    for e in corpo.get("errors") or []:
        desc = e.get("description") or ""
        cod = e.get("code") or ""
        saida.append(("%s (%s)" % (desc, cod)).strip())
    if not saida and corpo.get("erro_conexao"):
        saida.append(corpo["erro_conexao"])
    return saida


def so_digitos(s):
    return "".join(c for c in str(s or "") if c.isdigit())


def telefone_asaas(telefone):
    """Celular no formato que o Asaas aceita: DDD + 9 digitos, sem o 55 do pais.
    O numero do WhatsApp costuma vir sem o 9 (ex.: 55DD8XXXXXXX): celular de 8 digitos
    (comeca com 6 a 9) ganha o 9; fixo (comeca com 2 a 5) fica como esta.
    Numero zerado ou de tamanho invalido volta vazio, porque o Asaas grava e depois
    avisa 'numero de telefone incorreto' (6 clientes do GR assim em 10/2026)."""
    tel = so_digitos(telefone)
    if tel.startswith("55") and len(tel) in (12, 13):
        tel = tel[2:]
    if len(tel) == 10 and tel[2] in "6789":
        tel = tel[:2] + "9" + tel[2:]
    if len(tel) not in (10, 11) or set(tel) == {"0"}:
        return ""
    return tel


# ============================================================
# CLIENTE
# ============================================================

def buscar_cliente(cpf_cnpj):
    """Devolve o primeiro cliente com esse CPF/CNPJ, ou None."""
    doc = so_digitos(cpf_cnpj)
    if not doc:
        return None
    st, corpo = _req("GET", "/customers", params={"cpfCnpj": doc})
    if st != 200:
        return None
    dados = corpo.get("data") or []
    return dados[0] if dados else None


def garantir_cliente(nome, cpf_cnpj, email=None, telefone=None, executar=False):
    """
    Acha o cliente pelo documento; cria se nao existir.

    Devolve (cliente_dict, criado_bool, erros). Sem `executar` ele nao cria:
    devolve (None, False, ['cliente ausente no Asaas']) para a pendencia humana
    decidir, porque cadastro errado no gateway sujita cobranca e conciliacao.
    """
    achado = buscar_cliente(cpf_cnpj)
    if achado:
        return achado, False, []
    if not executar:
        return None, False, ["cliente %s nao existe no Asaas (cadastro nao feito em dry-run)"
                             % cpf_cnpj]
    payload = {"name": nome, "cpfCnpj": so_digitos(cpf_cnpj)}
    if email:
        payload["email"] = email
    tel = telefone_asaas(telefone)
    if tel:
        payload["mobilePhone"] = tel
    st, corpo = _req("POST", "/customers", json_data=payload)
    if st in (200, 201):
        return corpo, True, []
    return None, False, erros_legiveis(corpo) or ["falha ao criar cliente (HTTP %s)" % st]


# ============================================================
# COBRANCA
# ============================================================

def cobranca_por_referencia(external_reference):
    """A trava de idempotencia. Devolve a cobranca ja emitida, ou None."""
    if not external_reference:
        return None
    st, corpo = _req("GET", "/payments",
                     params={"externalReference": external_reference, "limit": 10})
    if st != 200:
        return None
    for p in corpo.get("data") or []:
        # Cobranca excluida no Asaas continua com status PENDING e ganha
        # `deleted: true` (visto em 10/09/2026). Olhar so o status confundiria
        # um boleto excluido com um ativo e impediria a reemissao.
        if p.get("deleted"):
            continue
        if (p.get("status") or "").upper() not in ("REFUNDED", "DELETED"):
            return p
    return None


def criar_cobranca(customer_id, valor, vencimento, descricao,
                   external_reference, parcelas=1, tipo="BOLETO",
                   multa_percent=None, juros_mes=None, executar=False):
    """
    Cria a cobranca. Sem `executar=True` devolve apenas o payload (dry-run).

    Devolve (cobranca_dict, ja_existia_bool, erros_lista).
    `parcelas` > 1 usa installmentCount: o Asaas gera o carne e devolve a
    primeira parcela, com installment agrupando as demais.
    """
    ja = cobranca_por_referencia(external_reference)
    if ja:
        return ja, True, []

    payload = {
        "customer": customer_id,
        "billingType": tipo,
        "value": round(float(valor), 2),
        "dueDate": str(vencimento)[:10],
        "description": (descricao or "")[:500],
        "externalReference": external_reference,
    }
    if parcelas and int(parcelas) > 1:
        n = int(parcelas)
        payload.pop("value")
        payload["installmentCount"] = n
        payload["installmentValue"] = round(float(valor) / n, 2)
        payload["totalValue"] = round(float(valor), 2)
    if multa_percent:
        payload["fine"] = {"value": float(multa_percent)}
    if juros_mes:
        payload["interest"] = {"value": float(juros_mes)}

    if not executar:
        return {"_dry_run": True, "_payload": payload}, False, []

    st, corpo = _req("POST", "/payments", json_data=payload)
    if st in (200, 201):
        return corpo, False, []
    return None, False, erros_legiveis(corpo) or ["falha ao emitir cobranca (HTTP %s)" % st]


def dados_para_envio(payment):
    """
    Junta tudo que o cliente precisa para pagar, num dict so.

    invoiceUrl e a pagina do Asaas (aceita boleto e Pix), bankSlipUrl e o PDF
    do boleto. A linha digitavel e o Pix copia-e-cola vem em chamadas proprias
    e podem falhar sem derrubar o envio: o link resolve sozinho.
    """
    if not payment or payment.get("_dry_run"):
        return {}
    pid = payment.get("id")
    saida = {
        "id": pid,
        "valor": payment.get("value") or payment.get("totalValue"),
        "vencimento": payment.get("dueDate"),
        "status": payment.get("status"),
        "invoice_url": payment.get("invoiceUrl"),
        "boleto_pdf": payment.get("bankSlipUrl"),
        "numero": payment.get("invoiceNumber"),
        "parcelamento": payment.get("installment"),
    }
    st, corpo = _req("GET", "/payments/%s/identificationField" % pid)
    if st == 200:
        saida["linha_digitavel"] = corpo.get("identificationField")
        saida["codigo_barras"] = corpo.get("barCode")
    st, corpo = _req("GET", "/payments/%s/pixQrCode" % pid)
    if st == 200 and corpo.get("success") is not False:
        saida["pix_copia_e_cola"] = corpo.get("payload")
    return saida


def baixar_boleto_pdf(payment, destino):
    """Salva o PDF do boleto em disco, para virar anexo do e-mail.
    Devolve o caminho ou None."""
    url = (payment or {}).get("bankSlipUrl")
    if not url:
        return None
    try:
        r = requests.get(url, timeout=60,
                         headers={"User-Agent": "Escritorio-Advogados/1.0"})
        if r.status_code != 200 or not r.content:
            return None
        # o Asaas devolve o PDF direto nessa URL; se vier HTML, nao serve de anexo
        if not r.content[:5].startswith(b"%PDF"):
            return None
        with open(destino, "wb") as f:
            f.write(r.content)
        return destino
    except requests.exceptions.RequestException:
        return None


# ============================================================
# CLI de conferencia
# ============================================================

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.getenv("ESC_CONFIG_ENV", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", ".env")))
    if os.path.exists("/opt/escritorio/config/.env"):
        load_dotenv("/opt/escritorio/config/.env")

    if "--cliente" in sys.argv:
        doc = sys.argv[sys.argv.index("--cliente") + 1]
        c = buscar_cliente(doc)
        print(c if c else "nao encontrado no Asaas")
    elif "--ver" in sys.argv:
        pid = sys.argv[sys.argv.index("--ver") + 1]
        st, corpo = _req("GET", "/payments/%s" % pid)
        print(st)
        if st == 200:
            for k, v in dados_para_envio(corpo).items():
                print("  %-18s %s" % (k, v))
    else:
        print(__doc__)
