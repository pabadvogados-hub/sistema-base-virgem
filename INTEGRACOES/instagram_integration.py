"""
Integracao com a API de publicacao do Instagram (Content Publishing API).

Token: System User com instagram_basic + instagram_content_publish.
Guardado em config/.env como INSTAGRAM_TOKEN (NAO e o mesmo META_ADS_TOKEN,
que serve so para anuncios e nao tem escopo organico).

Exigencias da API (nao sao escolha nossa):
  - imagem tem que ser JPEG, em URL publica HTTPS (nao aceita upload de arquivo)
  - carrossel tem de 2 a 10 itens
  - a publicacao e IMEDIATA: nao existe agendamento nativo aqui.
    Quem segura a hora e o cron que chama publicar_agenda.py.
  - nao existe endpoint para apagar post. Remocao e manual, no app.

Funcoes principais:
  get_token()                                  -> str | None
  descobrir_conta()                            -> {id, username} | None
  cota_publicacao(ig_id)                       -> int   (posts ja feitos em 24h)
  publicar_carrossel(ig_id, urls, legenda)     -> media_id | None

Endpoints (Graph API v21):
  POST /{ig_id}/media           (item do carrossel e container do carrossel)
  POST /{ig_id}/media_publish   (publica o container)
  GET  /{container_id}?fields=status_code
"""
import os
import time
import logging
import requests

log = logging.getLogger('integracoes.instagram')

BASE = 'https://graph.facebook.com/v21.0'

# quanto esperamos um container ficar pronto antes de desistir
TIMEOUT_CONTAINER = 120
INTERVALO_CHECAGEM = 5


def get_token():
    """Retorna o token de publicacao do .env. None se ausente."""
    tok = os.getenv('INSTAGRAM_TOKEN')
    if not tok:
        log.warning('INSTAGRAM_TOKEN ausente no config/.env')
    return tok


def _req(metodo, path, params=None, dados=None):
    """Chamada na Graph API com token. Retorna dict (json) ou {} em erro."""
    tok = get_token()
    if not tok:
        return {}
    try:
        if metodo == 'GET':
            params = dict(params or {})
            params['access_token'] = tok
            r = requests.get(f'{BASE}/{path}', params=params, timeout=60)
        else:
            dados = dict(dados or {})
            dados['access_token'] = tok
            r = requests.post(f'{BASE}/{path}', data=dados, timeout=120)
    except Exception as e:
        log.warning(f'{metodo} {path}: {e}')
        return {}
    if r.status_code not in (200, 201):
        log.warning(f'{metodo} {path} {r.status_code}: {r.text[:300]}')
        return {}
    return r.json() or {}


# ============================================================
# CONTA
# ============================================================

def descobrir_conta():
    """
    Retorna {'id': ..., 'username': ...} da conta do Instagram, ou None.

    Prefere o INSTAGRAM_ID do .env: o @wpcasalda.ia e ativo direto do BM 01
    (ID 17841401041699896), entao nao dependemos de achar o vinculo com Pagina.
    So cai na varredura das Paginas se o ID nao estiver configurado.
    """
    ig_id = os.getenv('INSTAGRAM_ID')
    if ig_id:
        d = _req('GET', ig_id, {'fields': 'id,username'})
        if d.get('id'):
            return {'id': d['id'], 'username': d.get('username', '')}
        log.warning(f'INSTAGRAM_ID={ig_id} nao pode ser lido com este token '
                    f'(falta instagram_basic?). Tentando pelas Paginas.')

    d = _req('GET', 'me/accounts', {'fields': 'name,instagram_business_account{id,username}'})
    for pagina in d.get('data', []):
        ig = pagina.get('instagram_business_account')
        if ig:
            log.info(f"IG encontrado: @{ig.get('username')} (pagina {pagina.get('name')})")
            return {'id': ig['id'], 'username': ig.get('username', '')}
    log.warning('Nenhuma conta do Instagram vinculada as Paginas deste token')
    return None


def cota_publicacao(ig_id):
    """Quantos posts ja foram publicados nas ultimas 24h (limite da API: 50)."""
    d = _req('GET', f'{ig_id}/content_publishing_limit', {'fields': 'quota_usage'})
    dados = d.get('data') or [{}]
    return int(dados[0].get('quota_usage', 0))


# ============================================================
# PUBLICACAO
# ============================================================

def _criar_item(ig_id, image_url):
    """Cria um item de carrossel (uma imagem). Retorna o id do container."""
    d = _req('POST', f'{ig_id}/media', dados={
        'image_url': image_url,
        'is_carousel_item': 'true',
    })
    return d.get('id')


def _criar_container(ig_id, filhos, legenda):
    """Cria o container do carrossel com os itens ja criados."""
    d = _req('POST', f'{ig_id}/media', dados={
        'media_type': 'CAROUSEL',
        'children': ','.join(filhos),
        'caption': legenda,
    })
    return d.get('id')


def _aguardar(container_id, timeout=TIMEOUT_CONTAINER):
    """
    Espera o container ficar FINISHED. A API processa a imagem de forma
    assincrona; publicar antes da hora da erro.
    Retorna True se ficou pronto.
    """
    limite = time.time() + timeout
    while time.time() < limite:
        d = _req('GET', container_id, {'fields': 'status_code,status'})
        st = d.get('status_code')
        if st == 'FINISHED':
            return True
        if st in ('ERROR', 'EXPIRED'):
            log.warning(f'Container {container_id} falhou: {st} - {d.get("status")}')
            return False
        time.sleep(INTERVALO_CHECAGEM)
    log.warning(f'Container {container_id} nao ficou pronto em {timeout}s')
    return False


def publicar_carrossel(ig_id, urls, legenda):
    """
    Publica um carrossel AGORA. urls = lista de 2 a 10 JPEGs em HTTPS publico.
    Retorna o id da midia publicada, ou None se falhou em qualquer etapa.
    """
    if not (2 <= len(urls) <= 10):
        log.warning(f'Carrossel precisa de 2 a 10 imagens (recebeu {len(urls)})')
        return None

    filhos = []
    for i, url in enumerate(urls, 1):
        item = _criar_item(ig_id, url)
        if not item:
            log.warning(f'Falhou ao criar o item {i} ({url})')
            return None
        filhos.append(item)
        log.info(f'  item {i}/{len(urls)} criado: {item}')

    container = _criar_container(ig_id, filhos, legenda)
    if not container:
        log.warning('Falhou ao criar o container do carrossel')
        return None

    if not _aguardar(container):
        return None

    d = _req('POST', f'{ig_id}/media_publish', dados={'creation_id': container})
    media_id = d.get('id')
    if media_id:
        log.info(f'Publicado: {media_id}')
    else:
        log.warning('Container pronto mas a publicacao falhou')
    return media_id


if __name__ == '__main__':
    # smoke test: so leitura, nao publica nada
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    envp = os.path.join(os.path.dirname(__file__), '..', 'config', '.env')
    if os.path.exists(envp):
        for linha in open(envp, encoding='utf-8', errors='ignore'):
            linha = linha.strip()
            if '=' in linha and not linha.startswith('#'):
                k, v = linha.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    print('Token presente:', bool(get_token()))
    conta = descobrir_conta()
    if conta:
        print(f"Conta: @{conta['username']} (id {conta['id']})")
        print('Posts nas ultimas 24h:', cota_publicacao(conta['id']), '/ 50')
    else:
        print('Nenhuma conta do Instagram acessivel com este token.')
