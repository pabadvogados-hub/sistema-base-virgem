""
import asyncio
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from ..config import settings
from ..deps import usuario_atual
from ..portal import dados, lado, processo_info, resumo as resumo_ia, traducao, voz as voz_google
from ..portal.limpeza import limpar_documento
from ..portal.jornada import jornada as jornada_do_processo

router = APIRouter(prefix='/api/portal', tags=['portal-cliente'])
seguranca = HTTPBearer(auto_error=False)

JWT_DIAS = 7
OTP_MINUTOS = 10
OTP_MAX_TENTATIVAS = 5
LIMITE_PEDIDOS_DOC = 5
LIMITE_PEDIDOS_IP = 20
JANELA = 900

_falhas: dict[str, list[float]] = {}


def _ip(request: Request) -> str:
    xff = request.headers.get('x-forwarded-for', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.client.host if request.client else 'desconhecido'


def _conta(chave: str) -> int:
    agora = time.time()
    recentes = [t for t in _falhas.get(chave, []) if agora - t < JANELA]
    if recentes:
        _falhas[chave] = recentes
    else:
        _falhas.pop(chave, None)
    return len(recentes)


def _registra(*chaves: str):
    agora = time.time()
    for c in chaves:
        _falhas.setdefault(c, []).append(agora)


OCUPADO = 'O portal esta ocupado agora. Tente de novo em 1 minuto.'


def _banco_rapido():
    ""
    con = sqlite3.connect(dados.DB_PATH, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _com_folga(fn, tentativas=4):
    ""
    espera = 0.5
    for i in range(tentativas):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if 'locked' not in str(e).lower() or i == tentativas - 1:
                raise
            time.sleep(espera)
            espera *= 2


def _sem_marcas(t):
    ""
    if not t:
        return t
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t, flags=re.S)
    t = re.sub(r'__(.+?)__', r'\1', t, flags=re.S)
    t = re.sub(r'(?m)^[ \t]{0,3}#{1,6}[ \t]+', '', t)
    t = re.sub(r'(?m)^[ \t]*[-*•][ \t]+', '', t)
    t = t.replace('*', '')
    t = re.sub(r'(?m)^([^\n.!?:;]{3,80}?)[ \t]*\n[ \t]*\n', r'\1.\n\n', t)
    return t.strip()


def criar_token_cliente(cliente: dict) -> str:
    agora = datetime.now(timezone.utc)
    return jwt.encode({
        'sub': f"portal:{cliente['id']}",
        'tipo': 'cliente',
        'cliente_id': cliente['id'],
        'doc': cliente['cpf'],
        'nome': cliente['nome'],
        'iat': agora,
        'exp': agora + timedelta(days=JWT_DIAS),
    }, settings.JWT_SECRET, algorithm='HS256')


async def cliente_atual(cred: HTTPAuthorizationCredentials = Depends(seguranca)) -> dict:
    if cred is None:
        raise HTTPException(401, 'Faca login para continuar')
    try:
        p = jwt.decode(cred.credentials, settings.JWT_SECRET, algorithms=['HS256'])
    except jwt.PyJWTError:
        raise HTTPException(401, 'Sua sessao expirou. Entre de novo.')
    if p.get('tipo') != 'cliente':
        raise HTTPException(403, 'Token invalido para o portal')
    cli = await asyncio.to_thread(dados.resolver_cliente, p.get('doc') or '')
    if not cli or cli['id'] != p.get('cliente_id'):
        raise HTTPException(401, 'Cadastro nao encontrado. Fale com o escritorio.')
    return cli


def _telefone_br(numero: str) -> str:
    n = dados.so_digitos(numero)
    if len(n) in (10, 11):
        return '55' + n
    return n


def _enviar_whatsapp(telefone: str, mensagem: str):
    from ..whatsapp import zapi
    with dados._con() as con:
        row = con.execute(
            'SELECT telefone, zapi_instance, zapi_token, zapi_client_token '
            'FROM wa_numeros WHERE ativo=1 ORDER BY id LIMIT 1').fetchone()
    if not row:
        return False, 'nenhum numero de WhatsApp ativo'
    numero = SimpleNamespace(
        telefone=row['telefone'], zapi_instance=row['zapi_instance'],
        zapi_token=row['zapi_token'], zapi_client_token=row['zapi_client_token'])
    try:
        return zapi.enviar_texto(numero, _telefone_br(telefone), mensagem)
    except Exception as e:
        return False, str(e)[:150]


def _mascara(v: str, email=False) -> str:
    if email:
        u, _, d = (v or '').partition('@')
        return (u[:2] + '***@' + d) if d else ''
    n = dados.so_digitos(v)
    return f'({n[-11:-9]}) *****-{n[-4:]}' if len(n) >= 10 else ''


class PedidoCodigo(BaseModel):
    documento: str
    contato_id: int | None = None


class Entrada(BaseModel):
    documento: str
    codigo: str


@router.post('/auth/codigo')
async def pedir_codigo(req: PedidoCodigo, request: Request):
    ""
    doc = dados.so_digitos(req.documento)
    ip = _ip(request)
    if _conta('doc:' + doc) >= LIMITE_PEDIDOS_DOC or _conta('ip:' + ip) >= LIMITE_PEDIDOS_IP:
        raise HTTPException(429, 'Muitas tentativas. Espere alguns minutos.')
    if not dados.documento_valido(doc):
        raise HTTPException(400, 'Digite um CPF ou CNPJ valido.')

    _registra('doc:' + doc, 'ip:' + ip)
    cliente = await asyncio.to_thread(dados.resolver_cliente, doc)

    neutra = {'enviado': True,
              'mensagem': 'Se esse documento estiver no nosso cadastro, o codigo chega no WhatsApp.'}
    if not cliente:
        await asyncio.to_thread(dados.log, doc, None, 'codigo_negado', 'documento nao cadastrado', ip)
        return neutra

    processos = await asyncio.to_thread(dados.processos_do_cliente, cliente)
    if not processos:
        await asyncio.to_thread(dados.log, doc, cliente['id'], 'codigo_negado', 'sem processo elegivel', ip)
        return neutra
    contatos = await asyncio.to_thread(dados.contatos_do_cliente, cliente)
    if not contatos:
        await asyncio.to_thread(dados.log, doc, cliente['id'], 'codigo_negado', 'sem contato', ip)
        return {**neutra,
                'mensagem': 'Nao temos um celular liberado no seu cadastro. '
                            'Fale com o escritorio para liberar seu acesso.'}

    if req.contato_id is not None:
        alvo = next((c for c in contatos if c['id'] == req.contato_id), None)
        if not alvo:
            raise HTTPException(400, 'Destino invalido. Peca o codigo de novo.')
    elif len(contatos) > 1:
        return {'enviado': False, 'escolha': [
            {'id': c['id'], 'nome': c['nome'], 'destino': _mascara(c['celular'])}
            for c in contatos],
            'mensagem': 'Para onde enviamos o seu codigo de acesso?'}
    else:
        alvo = contatos[0]

    codigo = f'{random.randint(0, 999999):06d}'
    from ..security import hash_senha
    agora = time.time()
    codigo_hash = hash_senha(codigo)

    def _gravar():
        con = _banco_rapido()
        try:
            con.execute('INSERT INTO portal_otp(cpf,codigo_hash,canal,destino,criado_em,expira_em) '
                        'VALUES(?,?,?,?,?,?)',
                        (doc, codigo_hash, 'whatsapp', alvo['celular'],
                         agora, agora + OTP_MINUTOS * 60))
            con.commit()
        finally:
            con.close()

    try:
        await asyncio.to_thread(_com_folga, _gravar)
    except sqlite3.OperationalError:
        raise HTTPException(503, OCUPADO)

    primeiro = (cliente['nome'] or '').split()[0].title()
    if alvo.get('titular', True):
        abertura = (f'Olá, {primeiro}! Seu código de acesso ao Portal do Cliente '
                    f'Escritório Modelo é: {codigo}')
    else:
        quem = (alvo.get('nome') or '').split()[0].title() or 'você'
        abertura = (f'Olá, {quem}! Seu código para acompanhar o processo de '
                    f'{primeiro} no Portal do Cliente Escritório Modelo é: {codigo}')
    texto = (abertura +
             f'\n\nEle vale por {OTP_MINUTOS} minutos. Não compartilhe com ninguém.\n'
             'Se não foi você que pediu, é só ignorar esta mensagem.')
    ok, info = await asyncio.to_thread(_enviar_whatsapp, alvo['celular'], texto)
    await asyncio.to_thread(dados.log, doc, cliente['id'], 'codigo_enviado',
                            f"whatsapp para {alvo['nome']} ok={ok} {str(info)[:50]}", ip)
    return {**neutra, 'canal': 'whatsapp', 'destino': _mascara(alvo['celular'])}


@router.post('/auth/entrar')
async def entrar(req: Entrada, request: Request):
    doc = dados.so_digitos(req.documento)
    codigo = dados.so_digitos(req.codigo)
    ip = _ip(request)
    if _conta('login:' + doc) >= OTP_MAX_TENTATIVAS:
        raise HTTPException(429, 'Muitas tentativas. Peca um codigo novo em alguns minutos.')

    from ..security import verificar_senha
    agora = time.time()

    def _conferir():
        ""
        con = _banco_rapido()
        try:
            rows = con.execute(
                'SELECT id, codigo_hash, tentativas FROM portal_otp '
                'WHERE cpf=? AND usado=0 AND expira_em>=? ORDER BY id DESC LIMIT 3',
                (doc, agora)).fetchall()
            if not rows:
                return 'expirado'
            if rows[0]['tentativas'] >= OTP_MAX_TENTATIVAS:
                return 'bloqueado'
            if any(verificar_senha(codigo, r['codigo_hash']) for r in rows):
                con.execute('UPDATE portal_otp SET usado=1 WHERE cpf=? AND usado=0', (doc,))
                con.commit()
                return 'ok'
            con.execute('UPDATE portal_otp SET tentativas=tentativas+1 WHERE id=?', (rows[0]['id'],))
            con.commit()
            return 'errado'
        finally:
            con.close()

    try:
        situacao = await asyncio.to_thread(_com_folga, _conferir)
    except sqlite3.OperationalError:
        raise HTTPException(503, OCUPADO)
    if situacao == 'expirado':
        _registra('login:' + doc)
        raise HTTPException(401, 'Codigo expirado. Peca um novo.')
    if situacao == 'bloqueado':
        raise HTTPException(429, 'Codigo bloqueado por excesso de tentativas. Peca um novo.')
    if situacao == 'errado':
        _registra('login:' + doc)
        await asyncio.to_thread(dados.log, doc, None, 'login_falhou', 'codigo errado', ip)
        raise HTTPException(401, 'Codigo incorreto.')

    cliente = await asyncio.to_thread(dados.resolver_cliente, doc)
    if not cliente:
        raise HTTPException(401, 'Cadastro nao encontrado.')

    def _registrar_acesso():
        con = _banco_rapido()
        try:
            con.execute(
                'INSERT INTO portal_acesso(cliente_id,cpf,nome,celular,email,criado_em,ultimo_login,logins) '
                'VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(cliente_id) DO UPDATE SET '
                'ultimo_login=excluded.ultimo_login, logins=portal_acesso.logins+1, '
                'celular=excluded.celular, email=excluded.email',
                (cliente['id'], doc, cliente['nome'], cliente['celular'], cliente['email'], agora, agora))
            con.commit()
        finally:
            con.close()

    try:
        await asyncio.to_thread(_com_folga, _registrar_acesso)
    except sqlite3.OperationalError:
        pass
    _falhas.pop('login:' + doc, None)
    await asyncio.to_thread(dados.log, doc, cliente['id'], 'login_ok', '', ip)
    return {'token': criar_token_cliente(cliente),
            'cliente': {'nome': cliente['nome'], 'primeiro_nome': (cliente['nome'] or '').split()[0].title()}}


LISTA_IGUAL_A_TELA_MAX = 20


def _como_na_tela(nome_cliente: str, p: dict):
    ""
    try:
        esp = resumo_ia.espelho(p['numero'], p['monitorado'])
        capa = esp['capa'] or {}
        if not capa.get('classe'):
            capa.setdefault('classe', p['classe'])
            capa.setdefault('assunto', p['assunto'])
            capa.setdefault('tribunal', p['tribunal'])
            capa.setdefault('polo_passivo', p['polo_passivo'])
        res = traducao.montar_resumo(capa, esp['eventos'], p['etapa'], p['grupo'])
        ult = res.get('ultimo_andamento') or {}
        polo = lado.polo_efetivo(processo_info.polo_do_cliente(nome_cliente, capa), p.get('nosso_polo'))
        return {'jornada': lado.jornada(res['jornada'], polo), 'data': ult.get('data'), 'polo': polo}
    except Exception:
        return None


def _so_com_historico(cliente: dict, processos: list) -> list:
    ""
    try:
        if len(dados.so_digitos(cliente.get('cpf') or cliente.get('documento') or '')) != 14:
            return processos
        from ..portal import painel as pnl
        fi = pnl.fichas(dados._con, cliente['id'])
        if not fi:
            return processos
        return [p for p in processos if not p['baixado'] or p['numero'] in fi]
    except Exception:
        return processos


@router.get('/me')
async def me(tela: int = 0, cliente: dict = Depends(cliente_atual)):
    ""
    processos = await asyncio.to_thread(dados.processos_do_cliente, cliente)
    processos = await asyncio.to_thread(_so_com_historico, cliente, processos)
    andando = ([p for p in processos if not p['baixado']][:LISTA_IGUAL_A_TELA_MAX]
               if tela else [])
    trava = asyncio.Semaphore(6)

    async def calcular(p):
        async with trava:
            return await asyncio.to_thread(_como_na_tela, cliente['nome'], p)

    calculos = await asyncio.gather(*(calcular(p) for p in andando))
    na_tela = {p['numero']: c for p, c in zip(andando, calculos) if c}
    itens = []
    for p in processos:
        c = na_tela.get(p['numero'])
        j = (c['jornada'] if c
             else lado.jornada(jornada_do_processo(p['etapa'], p['grupo'], p['classe'], p['baixado']),
                               lado.polo_efetivo('', p.get('nosso_polo'))))
        info = processo_info.tribunal_do_numero(p['numero'], p.get('classe') or '')
        pt = processo_info.partes(
            {'polo_ativo': p['polo_ativo'], 'polo_passivo': p['polo_passivo']},
            p.get('clientes'), p.get('adversos'), p.get('nosso_polo'))
        itens.append({
            'numero': p['numero'],
            'titulo': p['classe'] or 'Processo',
            'assunto': p['assunto'],
            'contra': p['polo_passivo'] if p['polo_passivo'] else '',
            'tribunal': info['sigla'] or p['tribunal'],
            'link_tribunal': info['link'],
            'partes': pt,
            'responsavel': p['responsavel'],
            'jornada': _sem_recado_interno(j),
            'tem_pasta': bool(p['pasta_drive']),
            'monitorado': p['monitorado'],
            'segredo': p.get('segredo', False),
            'ultimo_andamento': (c or {}).get('data') or p['ultimo_andamento'],
            'polo': ((c or {}).get('polo')
                     or lado.polo_efetivo('', p.get('nosso_polo')) or ''),
        })
    _nome_cli = processo_info.nome_limpo(cliente['nome'])
    return {'cliente': {'nome': _nome_cli,
                        'primeiro_nome': (_nome_cli or '').split()[0].title(),
                        'email': cliente['email']},
            'whatsapp': await asyncio.to_thread(dados.whatsapp_escritorio),
            'processos': itens,
            'desbloqueio': [],
            'financeiro': await asyncio.to_thread(_situacao_fin, cliente)}


def _situacao_fin(cliente):
    ""
    try:
        from ..portal import inadimplencia
        return inadimplencia.situacao(cliente)
    except Exception:
        return {'inadimplente': False}


def _porta_fechada(sit, numero=''):
    ""
    return {
        'bloqueado': True,
        'numero': numero,
        'titulo': (sit.get('titulo') if sit.get('consultoria')
                   else 'Regularize para continuar'),
        'mensagem': sit.get('mensagem') or '',
        'qtd': sit.get('qtd') or 0,
        'valor': sit.get('valor') or 0,
        'desde': sit.get('desde') or '',
        'boleto': sit.get('boleto') or '',
    }


def _sem_recado_interno(d: dict) -> dict:
    ""
    if not isinstance(d, dict):
        return d
    d.pop('etapa_interna', None)
    for filho in ('jornada', 'trilha'):
        if isinstance(d.get(filho), dict):
            d[filho].pop('etapa_interna', None)
    return d


@router.get('/processo/{numero}')
async def processo(numero: str, request: Request, cliente: dict = Depends(cliente_atual)):
    ""
    _sit = await asyncio.to_thread(_situacao_fin, cliente)
    if _sit.get('inadimplente'):
        await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'],
                                'barrado_inadimplente', numero, _ip(request))
        return _porta_fechada(_sit, numero)
    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if not p:
        raise HTTPException(404, 'Processo nao encontrado')
    await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'], 'abriu_processo', numero, _ip(request))

    esp = await asyncio.to_thread(resumo_ia.espelho, p['numero'], p['monitorado'])
    capa = esp['capa'] or {}
    if not capa.get('classe'):
        capa.setdefault('classe', p['classe'])
        capa.setdefault('assunto', p['assunto'])
        capa.setdefault('tribunal', p['tribunal'])
        capa.setdefault('polo_passivo', p['polo_passivo'])

    res = _sem_recado_interno(
        traducao.montar_resumo(capa, esp['eventos'], p['etapa'], p['grupo']))

    polo = lado.polo_efetivo(processo_info.polo_do_cliente(cliente['nome'], capa), p.get('nosso_polo'))
    res = lado.resumo(res, polo)
    try:
        from ..portal import assistente_sentenca as _cs
        _ult = res.get('ultimo_andamento')
        if isinstance(_ult, dict) and _ult.get('titulo') in _cs.DECISOES_NA_TELA:
            _av = await asyncio.to_thread(_cs.avaliar, p['numero'], esp['eventos'],
                                          lado.tradutor(traducao.traduzir_evento, polo), dados._con)
            _linha = _cs.linha_da_tela(_av)
            if _linha:
                res['ultimo_andamento'] = dict(_ult, aviso_trava=_linha)
    except Exception:
        pass
    texto_caso = await asyncio.to_thread(
        resumo_ia.resumo_caso, p['numero'], capa, p['etapa'], esp['eventos'],
        polo, cliente['nome'], True)
    texto_mov = await asyncio.to_thread(
        resumo_ia.resumo_movimentacao, p['numero'], None, esp['eventos'], polo, True)

    parecer_doc = None
    try:
        from ..portal import painel as _pnl
        _f = await asyncio.to_thread(_pnl.fichas, dados._con, cliente['id'])
        _fp = _f.get(p['numero']) or {}
        if _fp.get('parecer_link'):
            parecer_doc = {'link': _fp['parecer_link'],
                           'nome': _fp.get('parecer_nome') or 'Parecer do caso'}
    except Exception:
        parecer_doc = None

    valor = capa.get('valor_causa')
    info = processo_info.tribunal_do_numero(p['numero'], p.get('classe') or '')
    pt = processo_info.partes(capa, p.get('clientes'), p.get('adversos'),
                              p.get('nosso_polo'))
    return {
        'numero': p['numero'],
        'link_tribunal': info['link'],
        'tribunal_sigla': info['sigla'],
        'segmento': info['segmento'],
        'partes': pt,
        'capa': {
            'classe': capa.get('classe'), 'assunto': capa.get('assunto'),
            'tribunal': capa.get('tribunal'), 'vara': capa.get('orgao_julgador'),
            'contra': capa.get('polo_passivo'), 'autor': capa.get('polo_ativo'),
            'valor_causa': valor if isinstance(valor, (int, float)) else None,
            'distribuido_em': capa.get('data_distribuicao'),
        },
        'responsavel': p['responsavel'],
        'tem_pasta': bool(p['pasta_drive']),
        'monitorado': p['monitorado'],
        'whatsapp': await asyncio.to_thread(dados.whatsapp_escritorio),
        'segredo': p.get('segredo', False) or bool(capa.get('segredo_justica')),
        'fonte': esp['fonte'],
        'resumo_caso': _sem_marcas(texto_caso),
        'resumo_movimentacao': _sem_marcas(texto_mov),
        'parecer': parecer_doc,
        'polo': polo,
        **res,
    }


@router.post('/processo/{numero}/ouviu')
async def ouviu_explicacao(numero: str, request: Request, cliente: dict = Depends(cliente_atual)):
    ""
    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if p:
        await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'], 'ouviu',
                                numero, _ip(request))
    return {'ok': True}


LIMITE_AUDIOS_NOVOS = 150


class PedidoAudio(BaseModel):
    texto: str
    voz: str = ''


@router.post('/processo/{numero}/audio')
async def audio_explicacao(numero: str, pedido: PedidoAudio, request: Request,
                           cliente: dict = Depends(cliente_atual)):
    ""
    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if not p:
        raise HTTPException(404, 'Processo nao encontrado')
    texto = voz_google.limpar_texto(pedido.texto)
    if not texto:
        raise HTTPException(400, 'Nada para ler')
    if not await asyncio.to_thread(voz_google.em_cache, texto, pedido.voz):
        chave = f"audio:{cliente['id']}"
        if _conta(chave) >= LIMITE_AUDIOS_NOVOS:
            raise HTTPException(429, 'Muitos audios agora. Tente daqui a pouco.')
        _registra(chave)
    try:
        mp3 = await asyncio.to_thread(voz_google.falar, texto, pedido.voz)
    except Exception:
        raise HTTPException(503, 'Voz indisponivel agora')
    return Response(content=mp3, media_type='audio/mpeg',
                    headers={'Cache-Control': 'private, max-age=86400'})


@router.get('/processo/{numero}/resumos')
async def resumos(numero: str, request: Request, cliente: dict = Depends(cliente_atual)):
    ""
    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if not p:
        raise HTTPException(404, 'Processo nao encontrado')

    esp = await asyncio.to_thread(resumo_ia.espelho, p['numero'], p['monitorado'])
    capa = esp['capa'] or {}
    if not capa.get('classe'):
        capa.setdefault('classe', p['classe'])
        capa.setdefault('assunto', p['assunto'])
        capa.setdefault('tribunal', p['tribunal'])
        capa.setdefault('polo_passivo', p['polo_passivo'])

    polo = lado.polo_efetivo(processo_info.polo_do_cliente(cliente['nome'], capa), p.get('nosso_polo'))
    texto_caso = await asyncio.to_thread(
        resumo_ia.resumo_caso, p['numero'], capa, p['etapa'], esp['eventos'],
        polo, cliente['nome'])
    texto_mov = await asyncio.to_thread(
        resumo_ia.resumo_movimentacao, p['numero'], None, esp['eventos'], polo)
    return {'resumo_caso': _sem_marcas(texto_caso), 'resumo_movimentacao': _sem_marcas(texto_mov)}


@router.get('/processo/{numero}/autos')
async def autos_completos(numero: str, request: Request = None,
                          cliente: dict = Depends(cliente_atual)):
    ""
    _sit = await asyncio.to_thread(_situacao_fin, cliente)
    if _sit.get('inadimplente'):
        raise HTTPException(402, _sit.get('mensagem')
                            or 'Regularize o seu debito para continuar.')
    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if not p:
        raise HTTPException(404, 'Processo nao encontrado')
    await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'], 'abriu_autos',
                            numero, _ip(request) if request else '')

    esp = await asyncio.to_thread(resumo_ia.espelho, p['numero'], p['monitorado'])
    if esp['fonte'] == 'segredo':
        return {'total': 0, 'eventos': [], 'fonte': 'segredo',
                'aviso': 'Este processo corre em segredo de justica e o tribunal nao '
                         'libera a consulta automatica. Fale com o escritorio para ver as pecas.'}
    if not esp['eventos']:
        return {'total': 0, 'eventos': [], 'fonte': esp['fonte'],
                'aviso': 'Ainda nao consegui trazer os andamentos deste processo. '
                         'Fale com o escritorio.'}

    _polo = lado.polo_efetivo(processo_info.polo_do_cliente(cliente['nome'], esp['capa'] or {}),
                              p.get('nosso_polo'))
    eventos = [lado.andamento(traducao.traduzir_evento(e), _polo) for e in esp['eventos']]
    try:
        from ..portal import assistente_sentenca as _cs
        _av = await asyncio.to_thread(_cs.avaliar, p['numero'], esp['eventos'],
                                      lado.tradutor(traducao.traduzir_evento, _polo), dados._con)
        eventos = _cs.marcar_tela(eventos, _cs.linha_da_tela(_av))
    except Exception:
        pass
    return {'total': esp['total'], 'eventos': eventos, 'fonte': esp['fonte'],
            'atualizado_em': time.strftime('%Y-%m-%d %H:%M')}


@router.get('/processo/{numero}/documento/{documento_id}')
async def abrir_documento(numero: str, documento_id: int, request: Request,
                          cliente: dict = Depends(cliente_atual)):
    ""
    _sit = await asyncio.to_thread(_situacao_fin, cliente)
    if _sit.get('inadimplente'):
        raise HTTPException(402, _sit.get('mensagem')
                            or 'Regularize o seu debito para continuar.')
    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if not p or not p['monitorado']:
        raise HTTPException(404, 'Documento nao encontrado')
    await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'], 'baixou_documento',
                            f'{numero} doc={documento_id}', _ip(request))
    try:
        d = await asyncio.to_thread(dados.sync_get, f'/documentos/{documento_id}/arquivo')
    except Exception as e:
        raise HTTPException(502, f'Nao consegui abrir o documento: {e}')
    url = (d or {}).get('url')
    if not url:
        raise HTTPException(404, 'Documento sem arquivo disponivel')
    return {'url': url, 'nome': (d or {}).get('nome') or 'documento',
            'mime': (d or {}).get('mime') or 'application/pdf'}


@router.get('/processo/{numero}/documento/{documento_id}/texto')
async def texto_documento(numero: str, documento_id: int, request: Request,
                          cliente: dict = Depends(cliente_atual)):
    ""
    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if not p or not p['monitorado']:
        raise HTTPException(404, 'Documento nao encontrado')
    await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'], 'leu_documento',
                            f'{numero} doc={documento_id}', _ip(request))
    try:
        bruto = await asyncio.to_thread(
            dados.sync_get, f'/documentos/{documento_id}/markdown', None, True)
    except Exception as e:
        raise HTTPException(502, f'Nao consegui abrir o teor agora: {e}')

    if isinstance(bruto, dict):
        bruto = bruto.get('texto') or bruto.get('markdown') or ''
    texto = (bruto or '').strip()

    if texto.startswith('---'):
        fim = texto.find('---', 3)
        if fim > 0:
            texto = texto[fim + 3:].lstrip()

    texto = limpar_documento(texto)

    if not texto:
        raise HTTPException(404, 'Este documento ainda nao tem o texto disponivel')
    return {'texto': texto, 'documento_id': documento_id}


class PedidoPasta(BaseModel):
    email: str | None = None



@router.get('/painel')
async def painel_da_empresa(request: Request, cliente: dict = Depends(cliente_atual)):
    ""
    from ..portal import painel as pnl

    def _montar():
        pnl.criar_tabelas(dados._con)
        fi = pnl.fichas(dados._con, cliente['id'])
        if not fi and not pnl.e_pj_consultoria(dados._con, cliente):
            return {'tem_painel': False}
        procs = dados.processos_do_cliente(cliente)
        d = pnl.montar(dados._con, cliente, procs, jornada_do_processo,
                       resumo_ia.espelho)
        d.pop('pendentes_internos', None)
        _lados = {x['numero']: lado.polo_efetivo('', x.get('nosso_polo')) for x in procs}
        for _it in d.get('processos') or []:
            _it['jornada'] = lado.jornada(_it.get('jornada'), _lados.get(_it.get('numero'), ''))
            if isinstance(_it['jornada'], dict) and _it.get('fase'):
                _it['fase'] = _it['jornada'].get('titulo') or _it['fase']
        d['tem_painel'] = True
        return d

    d = await asyncio.to_thread(_montar)
    if d.get('tem_painel'):
        await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'],
                                'abriu_painel', '', _ip(request))
    d['financeiro'] = await asyncio.to_thread(_situacao_fin, cliente)
    d['whatsapp'] = await asyncio.to_thread(dados.whatsapp_escritorio)
    return d


@router.get('/processo/{numero}/parecer')
async def parecer_em_pdf(numero: str, request: Request,
                         cliente: dict = Depends(cliente_atual)):
    ""
    _sit = await asyncio.to_thread(_situacao_fin, cliente)
    if _sit.get('inadimplente'):
        raise HTTPException(402, _sit.get('mensagem')
                            or 'Regularize o seu debito para continuar.')
    from fastapi.responses import Response
    from ..portal import painel as pnl

    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if not p:
        raise HTTPException(404, 'Processo nao encontrado')

    fi = await asyncio.to_thread(pnl.fichas, dados._con, cliente['id'])
    f = fi.get(numero) or {}
    link = f.get('parecer_link') or ''
    if not link or 'id=' not in link:
        raise HTTPException(404, 'Este processo nao tem parecer disponivel')
    doc_id = link.split('id=', 1)[1].split('&')[0]

    def _pdf():
        svc = dados._drive_service()
        meta = svc.files().get(fileId=doc_id, fields='name,mimeType',
                               supportsAllDrives=True).execute()
        if (meta.get('mimeType') or '').endswith('apps.document'):
            return meta.get('name'), svc.files().export(
                fileId=doc_id, mimeType='application/pdf').execute()
        return meta.get('name'), svc.files().get_media(fileId=doc_id).execute()

    try:
        nome, dados_pdf = await asyncio.to_thread(_pdf)
    except Exception as e:
        raise HTTPException(502, f'Nao consegui abrir o parecer agora: {e}')

    await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'],
                            'abriu_parecer', numero, _ip(request))
    limpo = ''.join(c for c in (nome or 'parecer') if c.isalnum() or c in ' -_')
    return Response(
        content=dados_pdf, media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename="{limpo[:70]}.pdf"'})


def _ids_liberados(d: dict) -> set:
    ""
    ids = set()
    if d.get('ativo'):
        ids.add(d['ativo'].get('id'))
    for chave in ('junto', 'anteriores'):
        for doc in (d.get(chave) or []):
            ids.add(doc.get('id'))
    for g in (d.get('contratos') or []):
        for doc in (g.get('documentos') or []):
            ids.add(doc.get('id'))
    ids.discard(None)
    return ids


@router.get('/contratacao')
async def contratacao_do_cliente(cliente: dict = Depends(cliente_atual)):
    ""
    from ..portal import contratacao as ctr
    d = await asyncio.to_thread(ctr.do_cliente, dados._con, cliente['id'])
    d['tem'] = bool(d.get('contratos'))
    return d


@router.get('/contratacao/{drive_id}')
async def contratacao_pdf(drive_id: str, request: Request,
                          cliente: dict = Depends(cliente_atual)):
    ""
    from fastapi.responses import Response
    from ..portal import contratacao as ctr

    def _buscar():
        d = ctr.do_cliente(dados._con, cliente['id'])
        if drive_id not in _ids_liberados(d):
            return None, None
        svc = dados._drive_service()
        meta = svc.files().get(fileId=drive_id, fields='name,mimeType',
                               supportsAllDrives=True).execute()
        if (meta.get('mimeType') or '').endswith('apps.document'):
            return meta.get('name'), svc.files().export(
                fileId=drive_id, mimeType='application/pdf').execute()
        return meta.get('name'), svc.files().get_media(fileId=drive_id).execute()

    try:
        nome, conteudo = await asyncio.to_thread(_buscar)
    except Exception as e:
        raise HTTPException(502, f'Nao consegui abrir o documento agora: {e}')
    if not conteudo:
        raise HTTPException(404, 'Documento nao encontrado')

    await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'],
                            'abriu_contratacao', drive_id, _ip(request))
    limpo = ''.join(c for c in (nome or 'documento') if c.isalnum() or c in ' -_')
    return Response(
        content=conteudo, media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename="{limpo[:70]}.pdf"'})



@router.get('/financeiro')
async def financeiro_do_cliente(request: Request, cliente: dict = Depends(cliente_atual)):
    ""
    from ..portal import financeiro as fin

    d = await asyncio.to_thread(fin.do_cliente, cliente.get('documento') or cliente.get('cpf'))
    if d.get('tem'):
        await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'],
                                'abriu_financeiro', '', _ip(request))
    return d


def _reclamante(p: dict, cliente_nome: str) -> str:
    ""
    try:
        from ..portal import painel as _pnl
        return _pnl.outra_parte(p, cliente_nome, {})
    except Exception:
        return ''


@router.get('/processo/{numero}/pasta')
async def documentos_da_pasta(numero: str, request: Request,
                              cliente: dict = Depends(cliente_atual)):
    ""
    _sit = await asyncio.to_thread(_situacao_fin, cliente)
    if _sit.get('inadimplente'):
        raise HTTPException(402, _sit.get('mensagem')
                            or 'Regularize o seu debito para continuar.')
    from ..portal import pasta_portal as pp, drive_cliente

    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if not p:
        raise HTTPException(404, 'Processo nao encontrado')
    if not p['pasta_drive']:
        return {'ok': False, 'mensagem': 'Este processo ainda nao tem pasta.'}

    def _ler():
        svc = dados._drive_service()
        sub, _como = pp.achar_pasta_do_cliente(
            svc, p['pasta_drive'], p['numero'],
            _reclamante(p, cliente['nome']), cliente['nome'])
        if not sub:
            return None, []
        return sub, pp.listar(svc, sub['id'])

    sub, arquivos = await asyncio.to_thread(_ler)
    if not sub:
        return {'ok': False,
                'mensagem': 'Ainda nao ha documento liberado neste processo.'}
    await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'],
                            'abriu_pasta', numero, _ip(request))
    return {'ok': True, 'pasta': sub['name'], 'arquivos': arquivos}


@router.get('/processo/{numero}/pasta/{drive_id}')
async def arquivo_da_pasta(numero: str, drive_id: str, request: Request,
                           cliente: dict = Depends(cliente_atual)):
    ""
    _sit = await asyncio.to_thread(_situacao_fin, cliente)
    if _sit.get('inadimplente'):
        raise HTTPException(402, _sit.get('mensagem')
                            or 'Regularize o seu debito para continuar.')
    from fastapi.responses import Response
    from ..portal import pasta_portal as pp, drive_cliente

    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if not p or not p['pasta_drive']:
        raise HTTPException(404, 'Processo nao encontrado')

    def _buscar():
        svc = dados._drive_service()
        sub, _como = pp.achar_pasta_do_cliente(
            svc, p['pasta_drive'], p['numero'],
            _reclamante(p, cliente['nome']), cliente['nome'])
        if not sub:
            return None, None, None
        if drive_id not in {a['id'] for a in pp.listar(svc, sub['id'])}:
            return None, None, None
        return pp.baixar(svc, drive_id)

    nome, mime, conteudo = await asyncio.to_thread(_buscar)
    if not conteudo:
        raise HTTPException(404, 'Documento nao encontrado')

    await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'],
                            'baixou_documento', f'{numero} pasta={drive_id}', _ip(request))
    limpo = ''.join(c for c in (nome or 'documento') if c.isalnum() or c in ' -_.')
    return Response(content=conteudo, media_type=mime or 'application/octet-stream',
                    headers={'Content-Disposition': f'inline; filename="{limpo[:80]}"'})


@router.post('/processo/{numero}/envio')
async def receber_do_cliente(numero: str, request: Request,
                             arquivo: UploadFile = File(...),
                             cliente: dict = Depends(cliente_atual)):
    ""
    _sit = await asyncio.to_thread(_situacao_fin, cliente)
    if _sit.get('inadimplente'):
        raise HTTPException(402, _sit.get('mensagem')
                            or 'Regularize o seu debito para continuar.')
    from ..portal import pasta_portal as pp, drive_envio

    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if not p:
        raise HTTPException(404, 'Processo nao encontrado')
    if not p['pasta_drive']:
        return {'ok': False, 'mensagem': 'Este processo ainda nao tem pasta.'}

    conteudo = await arquivo.read()
    if not conteudo:
        return {'ok': False, 'mensagem': 'O arquivo chegou vazio.'}

    def _gravar():
        svc = dados._drive_service()
        raiz = pp.id_da_pasta(p['pasta_drive'])
        alvo, _c = pp.achar_pasta_do_cliente(svc, p['pasta_drive'], p['numero'],
                                             _reclamante(p, cliente['nome']),
                                             cliente['nome'])
        if alvo and pp._norm(alvo['name']) in pp._JA_E_DO_CLIENTE:
            pais = svc.files().get(fileId=alvo['id'], fields='parents',
                                   supportsAllDrives=True).execute().get('parents')
            raiz = pais[0] if pais else raiz
        elif alvo:
            raiz = alvo['id']
        sub = (drive_envio.achar_subpasta(svc, raiz, drive_envio.NOMES_ENVIO)
               or drive_envio.criar_pasta_envio(svc, raiz))
        if not sub:
            return {'ok': False, 'mensagem': 'Nao consegui receber agora.'}
        return pp.enviar(svc, sub['id'], arquivo.filename, conteudo,
                         arquivo.content_type or 'application/octet-stream')

    r = await asyncio.to_thread(_gravar)
    if r.get('ok'):
        await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'],
                                'enviou_documento', f'{numero}: {r.get("nome")}',
                                _ip(request))
    return r


class DadoDaEmpresa(BaseModel):
    chave: str
    valor: float | None = None
    texto: str = ''
    quem: str = ''


@router.post('/painel/dado')
async def painel_dado(req: DadoDaEmpresa, request: Request, cliente: dict = Depends(cliente_atual)):
    ""
    from ..portal import painel as _pnl
    ok = await asyncio.to_thread(_pnl.gravar_dado_empresa, dados._con, cliente['id'],
                                 req.chave, req.valor, req.texto, req.quem)
    if not ok:
        raise HTTPException(404, 'Esse campo nao existe para a sua empresa')
    await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'], 'informou_dado',
                            f"{req.chave}={req.valor if req.valor is not None else req.texto}", _ip(request))
    return {'ok': True}


@router.get('/painel/agenda')
async def agenda_da_empresa(cliente: dict = Depends(cliente_atual)):
    ""
    from ..portal import agenda as ag

    def _montar():
        procs = dados.processos_do_cliente(cliente)
        vivos = [p for p in procs
                 if jornada_do_processo(p['etapa'], p['grupo'], p['classe'],
                                        p['baixado'])['trilha'] != 'ENCERRADO']
        svc, cals = None, []
        try:
            from .. import agenda_google as gcal
            if gcal.disponivel():
                svc = gcal.servico()
                cals = [c['id'] for c in
                        svc.calendarList().list().execute().get('items', [])
                        if 'holiday' not in (c.get('id') or '')]
        except Exception:
            svc, cals = None, []
        itens = ag.unificada(vivos, resumo_ia.espelho, dados._con, svc, cals)

        from ..portal import painel as pnl
        fichas = pnl.fichas(dados._con, cliente['id'])
        por_numero = {p['numero']: p for p in vivos}
        for c in itens:
            p = por_numero.get(c.get('numero'))
            if p:
                c['parte'] = pnl.outra_parte(p, cliente['nome'],
                                             fichas.get(c['numero']) or {})
        return itens

    return {'agenda': await asyncio.to_thread(_montar)}


class RespostaNps(BaseModel):
    facil: int | None = None
    gostou: int | None = None
    faz_sentido: int | None = None
    comentario: str | None = None


@router.get('/nps')
async def nps_estado(cliente: dict = Depends(cliente_atual)):
    ""
    from ..portal import nps
    mostrar = await asyncio.to_thread(nps.deve_perguntar, dados._con, cliente['id'])
    return {'perguntar': mostrar, 'perguntas': nps.PERGUNTAS}


@router.post('/nps')
async def nps_responder(req: RespostaNps, request: Request,
                        cliente: dict = Depends(cliente_atual)):
    from ..portal import nps
    notas = {'facil': req.facil, 'gostou': req.gostou, 'faz_sentido': req.faz_sentido}
    if not any(v for v in notas.values()):
        raise HTTPException(400, 'Responda ao menos uma pergunta.')
    await asyncio.to_thread(nps.registrar, dados._con, cliente['id'], cliente['cpf'],
                            notas, req.comentario or '')
    await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'], 'nps',
                            'facil=%s gostou=%s sentido=%s' % (
                                req.facil, req.gostou, req.faz_sentido), _ip(request))
    return {'ok': True}


@router.post('/nps/dispensar')
async def nps_dispensar(request: Request, cliente: dict = Depends(cliente_atual)):
    from ..portal import nps
    await asyncio.to_thread(nps.dispensar, dados._con, cliente['id'], cliente['cpf'])
    await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'], 'nps',
                            'dispensou', _ip(request))
    return {'ok': True}


class NovoContato(BaseModel):
    cliente_id: int
    nome: str
    celular: str
    email: str | None = None


@router.get('/admin/contatos/{cliente_id}')
async def admin_listar_contatos(cliente_id: int, user=Depends(usuario_atual)):
    ""
    with dados._con() as con:
        cli = con.execute('SELECT id, nome, documento, celular, telefone, email '
                          'FROM jur_clientes WHERE id=?', (cliente_id,)).fetchone()
        if not cli:
            raise HTTPException(404, 'Cliente nao encontrado')
        contatos = [dict(r) for r in con.execute(
            'SELECT id, nome, celular, email, ativo FROM portal_contato '
            'WHERE cliente_id=? ORDER BY id', (cliente_id,))]
    return {'cliente': {'id': cli['id'], 'nome': cli['nome'], 'documento': cli['documento'],
                        'celular_cadastro': cli['celular'] or cli['telefone']},
            'contatos': contatos}


@router.post('/admin/contatos')
async def admin_add_contato(req: NovoContato, user=Depends(usuario_atual)):
    try:
        cid = await asyncio.to_thread(dados.add_contato, req.cliente_id, req.nome,
                                      req.celular, req.email or '')
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {'ok': True, 'contato_id': cid}


@router.delete('/admin/contatos/{cliente_id}/{contato_id}')
async def admin_remover_contato(cliente_id: int, contato_id: int, user=Depends(usuario_atual)):
    ok = await asyncio.to_thread(dados.remover_contato, cliente_id, contato_id)
    if not ok:
        raise HTTPException(404, 'Contato nao encontrado')
    return {'ok': True}


FALA_MAX_BYTES = 3 * 1024 * 1024
LIMITE_FALAS = 20


@router.post('/processo/{numero}/fala')
async def fala_para_texto(numero: str, request: Request, audio: UploadFile = File(...),
                          cliente: dict = Depends(cliente_atual)):
    ""
    _sit = await asyncio.to_thread(_situacao_fin, cliente)
    if _sit.get('inadimplente'):
        raise HTTPException(402, _sit.get('mensagem')
                            or 'Regularize o seu debito para continuar.')
    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if not p:
        raise HTTPException(404, 'Processo nao encontrado')
    chave = f"fala:{cliente['id']}"
    if _conta(chave) >= LIMITE_FALAS:
        raise HTTPException(429, 'Muitas gravacoes agora. Escreva a sua duvida ou tente daqui a pouco.')
    _registra(chave)
    conteudo = await audio.read(FALA_MAX_BYTES + 1)
    if len(conteudo) > FALA_MAX_BYTES:
        raise HTTPException(413, 'A gravacao ficou longa demais. Fale em ate 1 minuto.')
    try:
        texto = await asyncio.to_thread(voz_google.transcrever, conteudo)
    except Exception:
        raise HTTPException(503, 'Nao consegui entender o audio agora. Tente de novo ou escreva a sua duvida.')
    if texto:
        await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'], 'falou_duvida', numero, _ip(request))
    else:
        await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'], 'fala_vazia',
                                f'{numero} bytes={len(conteudo)}', _ip(request))
    return {'texto': texto, 'vazio': not texto}


class Pergunta(BaseModel):
    pergunta: str


@router.post('/processo/{numero}/duvida')
async def tirar_duvida(numero: str, req: Pergunta, request: Request,
                       cliente: dict = Depends(cliente_atual)):
    _sit = await asyncio.to_thread(_situacao_fin, cliente)
    if _sit.get('inadimplente'):
        raise HTTPException(402, _sit.get('mensagem')
                            or 'Regularize o seu debito para continuar.')
    from ..portal import duvida

    p = await asyncio.to_thread(dados.pode_ver, cliente, numero)
    if not p:
        raise HTTPException(404, 'Processo nao encontrado')

    texto = (req.pergunta or '').strip()
    if len(texto) < 3:
        raise HTTPException(400, 'Escreva a sua duvida.')
    texto = texto[:duvida.MAX_PERGUNTA]

    await asyncio.to_thread(duvida.criar_tabela, dados._con)
    usadas = await asyncio.to_thread(duvida.usou_hoje, dados._con, cliente['id'])
    if usadas >= duvida.LIMITE_DIA:
        return {'resposta': 'Voce ja fez varias perguntas hoje por aqui. '
                            'Para continuar, chame a gente no WhatsApp que a '
                            'equipe te atende.', 'limite': True}

    def _montar():
        from ..portal import resumo as r_ia, resumo_v2, traducao
        esp = r_ia.espelho(p['numero'], p['monitorado'])
        capa = esp['capa'] or {}
        j = jornada_do_processo(p['etapa'], p['grupo'], p['classe'], p['baixado'],
                                eventos=esp['eventos'])

        peca_txt, peca_data = '', ''
        try:
            _doc, peca_txt = resumo_v2.achar_peca_inicial(esp['eventos'], r_ia._ler_doc)
            peca_data = ((_doc or {}).get('data') or '')[:10]
        except Exception:
            pass

        doc_txt = ''
        alvo, _t, _r = resumo_v2.escolher_movimento_relevante(
            esp['eventos'], traducao.traduzir_evento)
        if alvo and alvo.get('documento_id') and alvo.get('tem_texto'):
            try:
                doc_txt = resumo_v2._corpo(r_ia._ler_doc(alvo['documento_id']))
            except Exception:
                pass

        _polo = lado.polo_efetivo(processo_info.polo_do_cliente(cliente['nome'], capa), p.get('nosso_polo'))
        _traduzir = lado.tradutor(traducao.traduzir_evento, _polo)
        _av = {'pode': False, 'decisao': {'falha': True}, 'motivo': 'trava indisponivel'}
        try:
            from ..portal import assistente_sentenca as _cs
            try:
                _av = _cs.avaliar(p['numero'], esp['eventos'], _traduzir, dados._con)
            except Exception as _e:
                import logging
                logging.getLogger('portal').warning(f'trava da sentenca (avaliar) falhou: {str(_e)[:120]}')
            _traduzir = _cs.tradutor_com_trava(_traduzir, _av)
        except Exception as _e:
            import logging
            logging.getLogger('portal').warning(f'trava da sentenca (mascara) falhou: {str(_e)[:120]}')
        ctx = duvida.montar_contexto(p['numero'], capa, lado.jornada(j, _polo), esp['eventos'],
                                     _traduzir, peca_txt, doc_txt,
                                     peca_data, cliente_nome=cliente['nome'])
        try:
            from ..portal import assistente_docs
            ctx += assistente_docs.bloco_para_contexto(texto, esp['eventos'], _traduzir,
                                                    r_ia._ler_doc,
                                                    p['pasta_drive'] if 'pasta_drive' in p.keys() else None)
        except Exception as _e:
            import logging
            logging.getLogger('portal').warning(f'documentos da pergunta falharam: {str(_e)[:120]}')
        try:
            from ..portal import assistente_sentenca, texto_seguro
            _bloco, _av = assistente_sentenca.bloco_para_contexto(
                texto, p['numero'], esp['eventos'], _traduzir, r_ia._ler_doc,
                lambda t: texto_seguro.limpar_entrada(resumo_v2._corpo(t)), dados._con)
            ctx += _bloco
            if _av.get('avisar'):
                assistente_sentenca.registrar_aviso(p['numero'], cliente['id'], _av, texto, dados._con)
        except Exception as _e:
            import logging
            logging.getLogger('portal').warning(f'trava da sentenca falhou: {str(_e)[:120]}')
            ctx += ('\n' + '=' * 40 + '\nRESULTADO AINDA NAO LIBERADO: nao deu para conferir agora se as decisoes '
                    'deste processo ja podem ser contadas. NAO conte o resultado de sentenca nem de acordao: diga que '
                    'a decisao saiu e que o escritorio conversa com o cliente sobre o que ela significa.')
        if _polo == 'requerida':
            ctx += ('\nLADO DO CLIENTE: ele e a parte que RESPONDE ao processo (foi processado). '
                    'Quando falar de valor, e o que ele pode vir a PAGAR, nunca o que ele vai receber.')
        return ctx

    from ..portal import duvida_regras
    pronta = duvida_regras.resposta_pronta(texto)
    if pronta:
        await asyncio.to_thread(duvida.registrar, dados._con, cliente['id'],
                                cliente['cpf'], p['numero'], texto, pronta, None)
        await asyncio.to_thread(dados.log, cliente['cpf'], cliente['id'], 'duvida',
                                f'{numero}: {texto[:70]} [RESPOSTA PADRAO: tempo]',
                                _ip(request))
        return {'resposta': pronta, 'falar_com_humano': False,
                'motivo_humano': '', 'padrao': 'tempo',
                'restantes': max(0, duvida.LIMITE_DIA - usadas - 1)}

    contexto = await asyncio.to_thread(_montar)

    from ..portal import resumo as r_ia
    pergunta_completa = (contexto + '\n\n' + '=' * 40 +
                         '\nPERGUNTA DO CLIENTE: ' + texto)
    resposta, uso = await asyncio.to_thread(
        r_ia._chamar_ia, duvida.SISTEMA, pergunta_completa, 700, duvida.MODELO)

    if resposta:
        motivo = duvida.precisa_refazer(resposta, contexto)
        if motivo:
            r2, u2 = await asyncio.to_thread(
                r_ia._chamar_ia, duvida.SISTEMA,
                pergunta_completa + (duvida.CORRECAO % motivo), 700, duvida.MODELO)
            if r2 and not duvida.precisa_refazer(r2, contexto):
                resposta, uso = r2, (u2 or uso)
        resposta = duvida.limpar_formato(resposta)
        from ..portal import texto_seguro
        if texto_seguro.problema(resposta):
            resposta = texto_seguro.aproveitar(resposta)
            if texto_seguro.problema(resposta):
                resposta = None

    if not resposta:
        resposta = ('Nao consegui responder agora. Chame a gente no WhatsApp '
                    'que a equipe te ajuda.')
        uso = None

    escalar, motivo = duvida_regras.precisa_humano(texto, resposta)

    await asyncio.to_thread(duvida.registrar, dados._con, cliente['id'], cliente['cpf'],
                            p['numero'], texto, resposta, uso, uso is None)
    await asyncio.to_thread(
        dados.log, cliente['cpf'], cliente['id'], 'duvida',
        f'{numero}: {texto[:70]}' + (f' [ESCALAR: {motivo}]' if escalar else ''),
        _ip(request))
    return {'resposta': _sem_marcas(resposta),
            'falar_com_humano': escalar,
            'motivo_humano': motivo,
            'restantes': max(0, duvida.LIMITE_DIA - usadas - 1)}


@router.get('/admin/clientes')
async def admin_buscar_clientes(q: str = '', user=Depends(usuario_atual)):
    ""
    termo = (q or '').strip()
    if len(termo) < 3:
        raise HTTPException(400, 'Digite ao menos 3 letras ou o documento.')

    def _buscar():
        from ..portal.jornada import jornada as _j
        digitos = dados.so_digitos(termo)
        like = f'%{termo.upper()}%'
        with dados._con() as con:
            linhas = [dict(r) for r in con.execute(
                'SELECT id, nome, documento, email, celular, telefone FROM jur_clientes '
                'WHERE UPPER(nome) LIKE ? OR REPLACE(REPLACE(REPLACE(documento,".",""),'
                '"-",""),"/","") LIKE ? ORDER BY nome LIMIT 25',
                (like, f'%{digitos}%' if digitos else '%__nada__%'))]

        saida = []
        for c in linhas:
            doc = dados.so_digitos(c['documento'] or '')
            cli = {'id': c['id'], 'nome': c['nome'], 'cpf': doc, 'documento': doc,
                   'email': (c['email'] or '').strip().lower(),
                   'celular': dados.so_digitos(c['celular'] or c['telefone'] or '')}
            procs = dados.processos_do_cliente(cli) if dados.documento_valido(doc) else []
            vivos = sum(1 for p in procs
                        if _j(p['etapa'], p['grupo'], p['classe'], p['baixado'])['trilha'] != 'ENCERRADO')
            with dados._con() as con:
                contatos = [dict(r) for r in con.execute(
                    'SELECT id, nome, celular, email, titular, relacao, ativo '
                    'FROM portal_contato WHERE cliente_id=? ORDER BY titular DESC, id',
                    (c['id'],))]
            saida.append({
                'id': c['id'], 'nome': c['nome'],
                'documento': c['documento'] or '',
                'documento_valido': dados.documento_valido(doc),
                'email': c['email'] or '',
                'celular_cadastro': c['celular'] or c['telefone'] or '',
                'processos': len(procs), 'processos_ativos': vivos,
                'contatos': contatos,
                'entra_no_portal': bool(procs) and bool(dados.contatos_do_cliente(cli)),
            })
        return saida

    return {'clientes': await asyncio.to_thread(_buscar)}


class ContatoEquipe(BaseModel):
    cliente_id: int
    nome: str
    celular: str
    email: str | None = None
    titular: bool = True
    relacao: str | None = None


@router.post('/admin/contato')
async def admin_salvar_contato(req: ContatoEquipe, user=Depends(usuario_atual)):
    ""
    try:
        cid = await asyncio.to_thread(
            dados.add_contato, req.cliente_id, req.nome, req.celular,
            req.email or '', req.titular, req.relacao or '',
            getattr(user, 'nome', '') or getattr(user, 'email', ''))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {'ok': True, 'contato_id': cid}
