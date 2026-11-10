"""Entrypoint de producao do Sistema Escritório Modelo Advogados.

Rodar com:  uvicorn app.main_vps:app --host 127.0.0.1 --port 8000

Cada modulo e carregado de forma isolada: se um falhar (dependencia ou chave
faltando), o erro vai para o log e o resto do sistema continua de pe.
A lista do que carregou fica em GET /api/sistema/modulos (so admin) e no log.
"""
import importlib
import importlib.util
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as _ErroHTTP

from .config import settings
from .database import init_db

log = logging.getLogger('main_vps')

MODULOS_OK: list[str] = []
MODULOS_FALHA: dict[str, str] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        from .routers.atendimento import seed_pipelines
        await seed_pipelines()
    except Exception as e:
        log.warning(f'Funis do CRM nao foram semeados: {e!r}')
    import asyncio
    tarefa_cache = None
    try:
        from .routers.controladoria import cache_loop
        tarefa_cache = asyncio.create_task(cache_loop())
    except Exception as e:
        log.warning(f'Cache da Controladoria nao iniciou: {e!r}')
    log.info('Modulos carregados: %s', ', '.join(MODULOS_OK))
    if MODULOS_FALHA:
        log.warning('Modulos com falha: %s', MODULOS_FALHA)
    yield
    if tarefa_cache:
        tarefa_cache.cancel()


app = FastAPI(
    title=f'Sistema {settings.ESCRITORIO_NOME}',
    version='1.0.0',
    lifespan=lifespan,
    # Seguranca: nao expor o mapa da API publicamente
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


def incluir(modulo: str, nome: str, atributo: str = 'router') -> bool:
    """Importa app.routers.<modulo> e registra o router. Nunca derruba o app."""
    try:
        mod = importlib.import_module(f'.routers.{modulo}', package=__package__)
        app.include_router(getattr(mod, atributo))
        MODULOS_OK.append(nome)
        return True
    except Exception as e:
        MODULOS_FALHA[nome] = repr(e)[:300]
        log.warning(f'Modulo {nome} indisponivel: {e!r}')
        return False


# Ordem importa: processos_carteira ANTES de processos (la existe /{processo_id}
# tipado int que capturaria /carteira).
MODULOS = [
    ('auth', 'Login e usuarios'),
    ('atendimento', 'Atendimento (WhatsApp/CRM)'),
    ('telefonia', 'Telefonia'),
    ('financeiro', 'Financeiro (Asaas)'),
    ('processos_carteira', 'Carteira de processos'),
    ('taxonomias', 'Taxonomias'),
    ('casos', 'Casos'),
    ('processos', 'Processos'),
    ('voz', 'Ligacao pelo WhatsApp'),
    ('dashboard', 'Painel'),
    ('ficha_entrevista', 'Ficha de entrevista'),
    ('controladoria', 'Controladoria (publicacoes e prazos)'),
    ('tarefas', 'Tarefas'),
    ('video_gravado', 'Video gravado no chat'),
    ('rh', 'RH'),
    ('rh_pagamentos', 'RH - pagamentos'),
    ('rh_slcoin', 'RH - moeda de metas'),
    ('cadastro', 'Clientes e processos'),
    ('audiencias', 'Audiencias'),
    ('andamentos', 'Linha do tempo'),
    ('tarefa_roteiros', 'Roteiros de tarefa'),
    ('assistente', 'Assistente do dia'),
    ('agentes', 'Agentes da equipe'),
    ('fin', 'Financeiro proprio'),
    ('fin_rotinas', 'Financeiro - rotinas'),
    ('campanhas', 'Campanhas (Meta Ads)'),
    ('base_fluxos', 'Fluxos da base'),
    ('campanhas_produto', 'Campanhas por produto'),
    ('comercial_kpi', 'Painel do comercial'),
    ('painel_metas', 'Painel de metas (TV)'),
    ('estudio', 'Estudio de conteudo'),
    ('intake', 'Intake / contratacao'),
    ('peca', 'Gerar peca'),
    ('clientes', 'Busca de clientes'),
    ('email_leitura', 'Leitura diaria de e-mail'),
    ('prazo_audio', 'Prazo por audio'),
    ('portal_resultado', 'Resultado do Portal'),
]

for _mod, _nome in MODULOS:
    # So tenta modulos que existem no disco (as frentes podem remover/criar arquivos)
    if importlib.util.find_spec(f'{__package__}.routers.{_mod}') is not None:
        incluir(_mod, _nome)


# ===== Portal do Cliente =====
# Login por CPF/CNPJ + codigo no WhatsApp. Superficie separada do sistema interno.
# Tenta ligar ate 5 vezes (o banco pode estar ocupado por outra rotina naquele
# segundo); se falhar, responde 503 "em manutencao" em vez de 404 calado.
_portal_erro = None
for _tentativa in range(5):
    try:
        from .routers.portal import router as portal_router
        from .portal.dados import criar_tabelas as _portal_tabelas
        _portal_tabelas()
        app.include_router(portal_router)
        MODULOS_OK.append('Portal do Cliente')
        _portal_erro = None
        break
    except Exception as _e:
        _portal_erro = _e
        if isinstance(_e, (ImportError, SyntaxError)):
            break
        import time as _time
        _time.sleep(3)
if _portal_erro is not None:
    MODULOS_FALHA['Portal do Cliente'] = repr(_portal_erro)[:300]
    log.error(f'Portal do Cliente indisponivel: {_portal_erro!r}')
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse
    _portal_manutencao = APIRouter()

    @_portal_manutencao.api_route('/api/portal/{resto:path}', methods=['GET', 'POST', 'PUT', 'DELETE'])
    async def _portal_em_manutencao(resto: str):
        return JSONResponse({'detail': 'O portal esta em manutencao por alguns minutos. '
                                       'Tente de novo daqui a pouco.'}, status_code=503)
    app.include_router(_portal_manutencao)


_PAGINA_ERRO = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Portal do Cliente | {escritorio}</title><style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#f7f3f0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:24px}}
.c{{background:#fff;border:1px solid #eadfd8;border-radius:16px;padding:32px 28px;max-width:400px;
box-shadow:0 4px 20px rgba(60,10,20,.06);text-align:center}}
h1{{margin:0 0 10px;font-size:19px;color:#4e0a1a}}
p{{margin:0 0 22px;font-size:14.5px;line-height:1.6;color:#4a4048}}
a{{display:block;background:#4e0a1a;color:#fff;text-decoration:none;padding:13px;
border-radius:10px;font-size:15px;font-weight:600}}
</style></head><body><div class="c">
<h1>{titulo}</h1><p>{texto}</p>
<a href="/portal/">Voltar ao portal</a>
</div></body></html>"""


@app.exception_handler(_ErroHTTP)
async def _erro_amigavel_portal(request, exc):
    """Erro do portal com cara de gente quando o cliente esta navegando."""
    from fastapi.responses import HTMLResponse, JSONResponse
    navegando = 'text/html' in (request.headers.get('accept') or '')
    if navegando and request.url.path.startswith('/api/portal'):
        if exc.status_code in (401, 403):
            titulo = 'Sua sessao expirou'
            texto = ('Por seguranca, o acesso ao portal vale por algumas horas. '
                     'Toque no botao abaixo para entrar de novo.')
        elif exc.status_code == 404:
            titulo = 'Nao encontramos este item'
            texto = 'Volte ao portal e tente novamente pela tela do seu processo.'
        else:
            titulo = 'Tivemos um problema aqui'
            texto = ('Nao foi voce. Tente de novo em instantes; se continuar, '
                     'fale com o escritorio.')
        return HTMLResponse(_PAGINA_ERRO.format(titulo=titulo, texto=texto,
                                                escritorio=settings.ESCRITORIO_NOME),
                            status_code=exc.status_code)
    return JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                        headers=getattr(exc, 'headers', None))


@app.get('/')
async def root():
    import os
    if os.getenv('SERVIR_FRONTEND', '').strip() in ('1', 'true', 'sim'):
        from pathlib import Path
        from fastapi.responses import FileResponse
        return FileResponse(Path(__file__).resolve().parents[2] / 'frontend' / 'dist' / 'index.html')
    return {'service': f'Sistema {settings.ESCRITORIO_NOME}', 'version': '1.0.0'}


@app.get('/health')
@app.get('/api/health')
async def health():
    return {'status': 'ok', 'modulos_ok': len(MODULOS_OK), 'modulos_falha': len(MODULOS_FALHA)}


def _admin():
    from .deps import requer_admin
    return requer_admin


@app.get('/api/sistema/modulos')
async def modulos(_u=Depends(_admin())):
    return {'ok': MODULOS_OK, 'falha': MODULOS_FALHA}


# ===== Uso local (Windows, sem nginx) =====
# Com SERVIR_FRONTEND=1 o proprio backend entrega a tela (dist/), o portal e a midia.
# Na VPS isso fica DESLIGADO: quem serve os arquivos e o nginx.
import os as _os
if _os.getenv('SERVIR_FRONTEND', '').strip() in ('1', 'true', 'sim'):
    from pathlib import Path as _Path
    from fastapi.responses import FileResponse as _FileResponse
    from fastapi.staticfiles import StaticFiles as _StaticFiles
    _front = _Path(__file__).resolve().parents[2] / 'frontend'
    for _sub in ('portal', 'colaborador', 'equipe'):
        if (_front / _sub).is_dir():
            app.mount(f'/{_sub}', _StaticFiles(directory=_front / _sub, html=True), name=_sub)
    _midia = _Path(_os.getenv('ESC_MEDIA_DIR', str(_Path(__file__).resolve().parents[1] / 'media')))
    _midia.mkdir(parents=True, exist_ok=True)
    app.mount('/media', _StaticFiles(directory=_midia), name='media')
    _dist = _front / 'dist'

    @app.get('/{caminho:path}', include_in_schema=False)
    async def _spa(caminho: str):
        alvo = (_dist / caminho).resolve()
        if caminho and alvo.is_file() and _dist.resolve() in alvo.parents:
            return _FileResponse(alvo)
        return _FileResponse(_dist / 'index.html')
