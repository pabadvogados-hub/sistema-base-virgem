"""
Integracao com API Asaas - Escritório Modelo Advogados
Modulo para consultar cobrancas, clientes e gerar boletos/PIX.

API Base: https://api.asaas.com/v3
Auth: access_token header
"""
import os
import sys
import time
import requests


ASAAS_BASE_URL = 'https://api.asaas.com/v3'


def _get_token():
    token = os.getenv('ASAAS_API_TOKEN')
    if not token:
        print('ERRO: ASAAS_API_TOKEN nao encontrado no .env')
        sys.exit(1)
    return token


def _headers():
    return {
        'access_token': _get_token(),
        'User-Agent': 'Escritorio-Advogados/1.0',
    }


def _request(method, endpoint, params=None, json_data=None, retries=2):
    url = f'{ASAAS_BASE_URL}{endpoint}'
    for tentativa in range(retries + 1):
        try:
            resp = requests.request(method, url, headers=_headers(),
                                    params=params, json=json_data, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get('Retry-After', 10))
                print(f'  Rate limit Asaas. Aguardando {wait}s...')
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json() if resp.text else {}
        except requests.exceptions.HTTPError:
            print(f'  Asaas erro ({resp.status_code}): {resp.text[:200]}')
            if tentativa < retries and resp.status_code >= 500:
                time.sleep(3)
                continue
            raise
        except requests.exceptions.RequestException as e:
            print(f'  Asaas conexao falhou: {e}')
            if tentativa < retries:
                time.sleep(3)
                continue
            raise
    return None


# ============================================================
# COBRANCAS (PAYMENTS)
# ============================================================

def listar_cobrancas(status=None, customer=None, due_date_ge=None,
                    due_date_le=None, limit=100):
    """
    Lista cobrancas do Asaas.

    status: 'PENDING', 'OVERDUE', 'RECEIVED', 'CONFIRMED' etc. (ou lista)
    customer: id do cliente no Asaas
    due_date_ge / due_date_le: YYYY-MM-DD (filtra por vencimento)
    """
    todas = []
    offset = 0
    while True:
        params = {'limit': limit, 'offset': offset}
        if status:
            params['status'] = status
        if customer:
            params['customer'] = customer
        if due_date_ge:
            params['dueDate[ge]'] = due_date_ge
        if due_date_le:
            params['dueDate[le]'] = due_date_le

        data = _request('GET', '/payments', params=params)
        registros = data.get('data', []) if data else []
        todas.extend(registros)
        total = data.get('totalCount', 0)
        has_more = data.get('hasMore', False)
        if not has_more or not registros:
            break
        offset += limit
        print(f'  Asaas cobrancas: {len(todas)}/{total}')

    return todas


def listar_cobrancas_abertas(dias_ate_vencer=7):
    """
    Retorna todas cobrancas que precisam de cobranca:
    - OVERDUE (vencidas)
    - PENDING com vencimento nos proximos N dias
    """
    from datetime import date, timedelta

    hoje = date.today()
    limite_aviso = hoje + timedelta(days=dias_ate_vencer)

    print(f'  Buscando cobrancas OVERDUE...')
    vencidas = listar_cobrancas(status='OVERDUE')

    print(f'  Buscando cobrancas PENDING ate {limite_aviso}...')
    a_vencer = listar_cobrancas(
        status='PENDING',
        due_date_ge=hoje.isoformat(),
        due_date_le=limite_aviso.isoformat(),
    )

    for p in vencidas:
        p['_situacao'] = 'VENCIDA'
    for p in a_vencer:
        p['_situacao'] = 'A_VENCER'

    return vencidas + a_vencer


def obter_linha_digitavel(payment_id):
    """Retorna linha digitavel do boleto."""
    try:
        data = _request('GET', f'/payments/{payment_id}/identificationField')
        return data.get('identificationField') if data else None
    except Exception:
        return None


def obter_pix_qrcode(payment_id):
    """
    Retorna dados do PIX: encodedImage (base64), payload (copia-e-cola),
    expirationDate.
    """
    try:
        data = _request('GET', f'/payments/{payment_id}/pixQrCode')
        if data and data.get('success'):
            return data
        return None
    except Exception:
        return None


# ============================================================
# CLIENTES
# ============================================================

def obter_cliente(customer_id):
    """Obtem dados completos de um cliente Asaas."""
    return _request('GET', f'/customers/{customer_id}')


def listar_clientes(cpf=None, nome=None, email=None, limit=100):
    """Busca clientes no Asaas."""
    params = {'limit': limit}
    if cpf:
        params['cpfCnpj'] = cpf
    if nome:
        params['name'] = nome
    if email:
        params['email'] = email
    data = _request('GET', '/customers', params=params)
    return data.get('data', []) if data else []


# ============================================================
# HELPERS
# ============================================================

def formatar_telefone_para_whatsapp(telefone):
    """Normaliza telefone: remove tudo que nao e digito."""
    if not telefone:
        return ''
    return ''.join(c for c in str(telefone) if c.isdigit())


def testar_conexao():
    print('Testando conexao Asaas...')
    try:
        data = _request('GET', '/myAccount')
        if data:
            print(f'  Conta: {data.get("name", "?")}')
            print(f'  Email: {data.get("email", "?")}')
            print(f'  Saldo disponivel: R$ {data.get("balance", 0):.2f}')
            return True
    except Exception as e:
        print(f'  ERRO: {e}')
        return False


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'config', '.env'))
    testar_conexao()
    print()
    abertas = listar_cobrancas_abertas(dias_ate_vencer=7)
    print(f'\n{len(abertas)} cobrancas em aberto ou a vencer em 7 dias.')
    for p in abertas[:5]:
        print(f'  [{p["_situacao"]}] {p["id"]} - R$ {p.get("value", 0):.2f} - '
              f'venc: {p.get("dueDate")} - cliente: {p.get("customer")}')
