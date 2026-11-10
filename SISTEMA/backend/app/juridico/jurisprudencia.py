"""Cliente sincrono da API Jurisprudencias.ai, para uso dentro do backend.

Usado por routers/peca.py para anexar jurisprudencia REAL e verificada (nunca
inventada) ao material enviado ao Opus na producao de pecas.

Docs: https://jurisprudencias.ai/api
Token: config/.env central -> JURISPRUDENCIAS_AI_TOKEN (settings.JURISPRUDENCIAS_AI_TOKEN)

Falha de rede/API/token ausente NUNCA derruba a geracao da peca: as funcoes aqui
devolvem lista/string vazia e so registram um aviso no log.
"""
import json
import logging

import requests

from ..config import settings

log = logging.getLogger('jurisprudencia')

BASE = 'https://jurisprudencias.ai/api/v1'


def _headers():
    return {'Authorization': f'Bearer {settings.JURISPRUDENCIAS_AI_TOKEN}', 'Accept': 'application/json'}


def buscar(tribunal: str, termo: str, limite: int = 5) -> list[dict]:
    """Busca decisoes reais de um tribunal por termo. [] se indisponivel ou sem token."""
    if not settings.JURISPRUDENCIAS_AI_TOKEN:
        log.warning('JURISPRUDENCIAS_AI_TOKEN ausente no config/.env - pulando busca de jurisprudencia')
        return []
    try:
        r = requests.get(f'{BASE}/courts/{tribunal}/decisions',
                          headers=_headers(), params={'q': termo, 'page': 0}, timeout=15)
        if r.status_code != 200:
            log.warning('jurisprudencias.ai %s (%s) status %s: %s',
                        tribunal, termo, r.status_code, r.content[:200])
            return []
        dados = json.loads(r.content).get('data') or []
        return dados[:limite]
    except Exception as e:
        log.warning('jurisprudencias.ai indisponivel (%s/%s): %s', tribunal, termo, e)
        return []


def bloco_verificado(termo: str, tribunais=('tst',), limite_por_tribunal: int = 5) -> str:
    """Monta o bloco de jurisprudencia REAL (processo/orgao/data/trecho/link) pronto
    para entrar no prompt do Opus. String vazia se nada foi encontrado - quem chama
    deve seguir so com o texto que o usuario digitou, sem bloquear a peca."""
    termo = (termo or '').strip()
    if not termo:
        return ''
    linhas = []
    for tribunal in tribunais:
        for d in buscar(tribunal, termo, limite=limite_por_tribunal):
            linhas.append(
                f"- Processo {d.get('process_number', '?')} ({tribunal.upper()}, "
                f"{d.get('adjudicating_body', '')}, {d.get('publication_date', '')}): "
                f"{(d.get('excerpt') or '')[:400]}\n  Fonte: {d.get('url', '')}"
            )
    if not linhas:
        return ''
    return (
        'JURISPRUDENCIA VERIFICADA (Jurisprudencias.ai) - usar/citar SOMENTE o que esta '
        'listado abaixo; nunca inventar numero de processo, ementa ou tribunal:\n'
        + '\n'.join(linhas)
    )
