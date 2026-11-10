"""Modulo Voz - softphone WebRTC (Asterisk na VPS).

Entrega ao usuario logado as credenciais do ramal SIP dele, pro softphone do
navegador (JsSIP) se registrar no Asterisk via WSS. As ligacoes em si acontecem
no navegador; aqui so servimos credencial + (depois) o log/gravacao da chamada.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import usuario_atual
from ..database import get_db, Usuario

router = APIRouter(prefix='/api/voz', tags=['voz'])

import os as _os
SIP_DOMAIN = (_os.getenv('SIP_DOMAIN') or 'sistema.escritorio.adv.br').strip()
WSS_URL = (_os.getenv('SIP_WSS_URL') or 'wss://%s/asterisk-ws' % SIP_DOMAIN).strip()
ECHO_EXT = '100'
# Linha SIP do escritorio: configurada no Asterisk (tronco), nao aqui.
# True so faz sentido sem tronco SIP: a tela mostra o selo TESTE / ECO.
MODO_TESTE = False


@router.get('/credenciais')
async def credenciais(user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Ramal + senha SIP do usuario logado, pro softphone registrar."""
    row = (await db.execute(
        text("SELECT ramal, senha FROM voz_ramais WHERE usuario_email = :e"),
        {'e': user.email})).first()
    if not row:
        raise HTTPException(404, 'Sem ramal de voz configurado para este usuario')
    ramal, senha = row[0], row[1]
    return {
        'ramal': ramal,
        'senha': senha,
        'wss': WSS_URL,
        'sip_domain': SIP_DOMAIN,
        'uri': f'sip:{ramal}@{SIP_DOMAIN}',
        'echo': ECHO_EXT,
        'modo_teste': MODO_TESTE,
    }


# ============================================================
# LIGACAO PELO WHATSAPP (D-API)
# O navegador fala direto com o stream da D-API (microfone <-> WebSocket).
# Aqui: inicia a chamada (a API key nunca vai ao navegador), registra na conversa
# e, no fim, guarda a gravacao e transcreve separando Atendente / Cliente.
# Tela: dist/whatsapp-ligar.js (bloco separado, botao ao lado do "Ligar").
# ============================================================
import asyncio
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime

import requests
from fastapi import BackgroundTasks

from ..config import settings

DAPI = 'https://api.d-api.cloud/api/v1'


def _tel_wa(t) -> str:
    d = re.sub(r'\D', '', str(t or ''))
    if not d.startswith('55') and len(d) in (10, 11):
        d = '55' + d
    if len(d) == 12 and d[4] in '6789':      # celular sem o 9
        d = d[:4] + '9' + d[4:]
    return d


# ---- LINHAS: cada numero de WhatsApp conectado na D-API faz UMA ligacao por vez (limite do WhatsApp).
# Para ligar em paralelo, conecte mais numeros (wa_numeros com zapi_instance "dapi:<sessao>"): o sistema
# escolhe sozinho uma linha livre.
_EM_USO = {}   # numero_id -> {'quem', 'desde', 'callId'}  (1 worker: dict em memoria basta)


async def _linhas_dapi(db):
    rows = (await db.execute(text(
        "SELECT id, nome, telefone, zapi_instance, zapi_token FROM wa_numeros "
        "WHERE ativo = 1 AND zapi_instance LIKE 'dapi:%' ORDER BY id"))).all()
    return [{'id': r[0], 'nome': r[1] or '', 'telefone': r[2] or '', 'sessao': str(r[3])[5:], 'chave': r[4]} for r in rows]


async def _numero_dapi(db, linha_id=None):
    linhas = await _linhas_dapi(db)
    if not linhas:
        raise HTTPException(400, 'Nenhum WhatsApp do escritorio esta conectado na D-API')
    li = next((x for x in linhas if linha_id and str(x['id']) == str(linha_id)), linhas[0])
    return li['sessao'], li['chave']


def _estado_linha(li):
    try:
        r = requests.post(f'{DAPI}/calls/state', json={'sessionId': li['sessao']},
                          headers={'Authorization': li['chave'], 'Content-Type': 'application/json'}, timeout=8)
        d = (r.json() or {}).get('data') or {}
        return str(d.get('state') or 'idle'), bool(d.get('active'))
    except Exception:
        return 'desconhecido', False


def _solta(linha):
    if str(linha or '').isdigit():
        _EM_USO.pop(int(linha), None)


def _encerrar_na_dapi(li, call_id=''):
    """Manda a D-API encerrar o que estiver aberto nessa sessao."""
    try:
        requests.post(f'{DAPI}/calls/end',
                      json={'sessionId': li['sessao'], 'callId': str(call_id or ''),
                            'reason': 'stale'},
                      headers={'Authorization': li['chave'], 'Content-Type': 'application/json'},
                      timeout=10)
    except Exception:
        pass


def _destravar(li):
    """Linha-fantasma: a D-API diz "ativa" mas ninguem esta em ligacao.

    Acontece quando a ligacao anterior acabou sem o desligar chegar aqui, por
    exemplo o cliente desligou do lado dele, a aba foi fechada ou a pessoa trocou
    de tela. A sessao fica marcada como ativa na D-API por varios minutos e todo
    mundo leva "Linha ocupada" sem ter ninguem falando. Visto em producao:
    ligacao as 09:10 sem desligar, quatro recusas as 09:17 e so liberou as 09:31.

    So entra aqui quando _EM_USO nao tem registro da linha, ou seja, quando este
    processo nao iniciou nenhuma ligacao que ainda esteja de pe. Se alguem esta
    mesmo falando, _EM_USO tem a entrada e nada e encerrado.

    Devolve True se a linha ficou livre.
    """
    _encerrar_na_dapi(li)
    for _ in range(3):
        time.sleep(1.2)
        est, ativa = _estado_linha(li)
        if not ativa:
            print('[voz] linha %s destravada (estava "%s" sem ninguem em ligacao)'
                  % (li['id'], est), flush=True)
            return True
    return False


@router.get('/wa/linhas')
async def wa_linhas(user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Linhas de ligacao (numeros na D-API) e quem esta usando cada uma."""
    out = []
    for li in await _linhas_dapi(db):
        est, ativa = await asyncio.to_thread(_estado_linha, li)
        if not ativa:
            _EM_USO.pop(li['id'], None)
        uso = _EM_USO.get(li['id']) or {}
        out.append({'id': li['id'], 'nome': li['nome'], 'telefone': li['telefone'], 'estado': est,
                    'livre': not ativa, 'quem': uso.get('quem', '')})
    return {'linhas': out}


async def _nota(db, conversa_id, texto_nota, autor='ligacao'):
    if not conversa_id:
        return
    await db.execute(text(
        "INSERT INTO wa_mensagens (conversa_id, origem, autor, tipo, conteudo, midia_url, criado_em) "
        "VALUES (:c, 'nota', :a, 'texto', :t, '', :d)"),
        {'c': int(conversa_id), 'a': autor, 't': texto_nota, 'd': datetime.utcnow()})
    await db.commit()


@router.post('/wa/ligar')
async def wa_ligar(body: dict, user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Inicia ligacao de voz pelo WhatsApp do escritorio. Devolve so callId + wsUrl."""
    tel = _tel_wa(body.get('telefone'))
    if len(tel) < 12:
        raise HTTPException(400, 'Telefone invalido para ligar pelo WhatsApp')
    quem = getattr(user, 'nome', '') or user.email
    linhas = await _linhas_dapi(db)
    if not linhas:
        raise HTTPException(400, 'Nenhum WhatsApp do escritorio esta conectado na D-API')
    # prefere a linha da propria conversa (o cliente ve o numero que ja conhece); depois as outras
    pref = None
    if body.get('conversa_id'):
        row = (await db.execute(text("SELECT numero_id FROM wa_conversas WHERE id = :c"),
                                {'c': int(body['conversa_id'])})).first()
        pref = row[0] if row else None
    linhas.sort(key=lambda x: (x['id'] != pref, x['id']))
    ocupadas, j, usada = [], {}, None
    for li in linhas:
        est, ativa = await asyncio.to_thread(_estado_linha, li)
        if ativa:
            uso = _EM_USO.get(li['id']) or {}
            if uso.get('quem'):
                # alguem esta mesmo em ligacao: respeita e nao encerra nada
                ocupadas.append((li['nome'] or li['telefone']) + f" (com {uso['quem']})")
                continue
            # ninguem em ligacao por aqui: e sobra da anterior, entao destrava
            if not await asyncio.to_thread(_destravar, li):
                ocupadas.append((li['nome'] or li['telefone']) + f' (a D-API insiste em "{est}")')
                continue
        r = await asyncio.to_thread(requests.post, f'{DAPI}/calls/stream',
                                    json={'sessionId': li['sessao'], 'phone': tel},
                                    headers={'Authorization': li['chave'], 'Content-Type': 'application/json'},
                                    timeout=30)
        try:
            j = r.json()
        except Exception:
            j = {}
        if r.status_code in (200, 201) and j.get('wsUrl'):
            usada = li
            break
        erro = str(j.get('error') or j.get('message') or r.text)[:200]
        if len(linhas) == 1:
            raise HTTPException(502, f'D-API recusou a ligacao: {erro}')
        ocupadas.append((li['nome'] or li['telefone']) + f' (recusou: {erro[:60]})')
    if not usada:
        raise HTTPException(409, 'Linha ocupada: ' + '; '.join(ocupadas) +
                                 '. Aguarde terminar ou conecte mais um numero de WhatsApp para ligar em paralelo.')
    _EM_USO[usada['id']] = {'quem': quem, 'desde': datetime.utcnow().isoformat(), 'callId': j.get('callId')}
    por = f" pela linha {usada['nome'] or usada['telefone']}" if len(linhas) > 1 else ''
    await _nota(db, body.get('conversa_id'), f'Ligacao pelo WhatsApp iniciada por {quem} para +{tel}{por}.')
    return {'callId': j.get('callId'), 'wsUrl': j['wsUrl'], 'telefone': tel, 'linha': usada['id'],
            'linha_nome': usada['nome'] or usada['telefone']}


@router.post('/wa/estado')
async def wa_estado(body: dict, user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Estado da chamada na D-API (idle | calling | ringing | active | ending...). O navegador consulta
    enquanto chama: a D-API nem sempre avisa pelo WebSocket quando o cliente recusa/nao atende."""
    sessao, chave = await _numero_dapi(db, body.get('linha'))
    try:
        r = await asyncio.to_thread(requests.post, f'{DAPI}/calls/state',
                                    json={'sessionId': sessao, 'callId': str(body.get('callId') or '')},
                                    headers={'Authorization': chave, 'Content-Type': 'application/json'},
                                    timeout=10)
        d = (r.json() or {}).get('data') or {}
        return {'state': str(d.get('state') or ''), 'active': bool(d.get('active'))}
    except Exception:
        return {'state': '', 'active': None}


@router.post('/wa/liberar')
async def wa_liberar(body: dict, user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Solta a linha na marra, para quando ela ficar presa e ninguem estiver falando.

    O destrave ja acontece sozinho na hora de ligar; isto existe para o caso de
    alguem precisar liberar sem tentar uma ligacao.
    """
    soltas = []
    for li in await _linhas_dapi(db):
        if body.get('linha') and str(li['id']) != str(body['linha']):
            continue
        est, ativa = await asyncio.to_thread(_estado_linha, li)
        _solta(li['id'])
        livre = True if not ativa else await asyncio.to_thread(_destravar, li)
        soltas.append({'linha': li['id'], 'nome': li['nome'] or li['telefone'],
                       'estava': est, 'livre': livre})
    return {'linhas': soltas}


@router.post('/wa/desligar')
async def wa_desligar(body: dict, user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Desliga pelo servidor (garante que o celular do cliente para de tocar)."""
    sessao, chave = await _numero_dapi(db, body.get('linha'))
    _solta(body.get('linha'))
    try:
        await asyncio.to_thread(requests.post, f'{DAPI}/calls/end',
                                json={'sessionId': sessao, 'callId': str(body.get('callId') or ''),
                                      'reason': str(body.get('motivo') or 'hangup')[:30]},
                                headers={'Authorization': chave, 'Content-Type': 'application/json'},
                                timeout=10)
    except Exception:
        pass
    return {'ok': True}


def _achar_arquivos(j) -> dict:
    """Procura as URLs da gravacao em qualquer formato de resposta da D-API."""
    achados = {}

    def varre(o):
        if isinstance(o, dict):
            for k, v in o.items():
                nome = {'full': 'full', 'url': 'full', 'recordingUrl': 'full', 'recording_url': 'full',
                        'sender': 'sender', 'sender_url': 'sender', 'senderUrl': 'sender',
                        'recipient': 'recipient', 'recipient_url': 'recipient', 'recipientUrl': 'recipient'}.get(k)
                if isinstance(v, str) and v.startswith('http') and nome:
                    achados.setdefault(nome, v)
                else:
                    varre(v)
        elif isinstance(o, list):
            for v in o:
                varre(v)
    varre(j)
    return achados


def _nivel_por_trecho(wav_bytes):
    """Devolve f(inicio, fim) -> volume RMS daquele trecho do WAV (ou None se nao der para ler)."""
    try:
        import array
        import io
        import math
        import wave
        w = wave.open(io.BytesIO(wav_bytes))
        if w.getsampwidth() != 2:
            return None
        taxa, canais = w.getframerate(), w.getnchannels()
        amostras = array.array('h', w.readframes(w.getnframes()))
        if canais > 1:
            amostras = amostras[::canais]

        def rms(ini, fim):
            a, b = int(ini * taxa), max(int(fim * taxa), int(ini * taxa) + 1)
            trecho = amostras[a:b]
            if not trecho:
                return 0
            return math.sqrt(sum(x * x for x in trecho) / len(trecho))
        return rms
    except Exception:
        return None


def _segmentos(url):
    """Baixa um WAV, converte para ogg (cabe no limite do STT) e devolve [(inicio, texto)]."""
    if not url or not settings.STT_API_KEY:
        return []
    try:
        wav = requests.get(url, timeout=120).content
        if not wav:
            return []
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, 'a.wav'), os.path.join(tmp, 'a.ogg')
            with open(src, 'wb') as f:
                f.write(wav)
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', src, '-ac', '1',
                            '-c:a', 'libopus', '-b:a', '24k', dst], check=True, timeout=300)
            with open(dst, 'rb') as f:
                dados = f.read()
        r = requests.post(f"{settings.STT_BASE_URL.rstrip('/')}/audio/transcriptions",
                          headers={'Authorization': f'Bearer {settings.STT_API_KEY}'},
                          files={'file': ('ligacao.ogg', dados, 'audio/ogg')},
                          data={'model': settings.STT_MODEL, 'language': 'pt',
                                'response_format': 'verbose_json'},
                          timeout=600)
        if r.status_code != 200:
            return []
        j = r.json()
        # O transcritor "inventa" frases em trechos de silencio (ex.: "E ai" repetido).
        # Descarta o trecho se o audio dele e quase mudo, se o proprio STT acha que nao e fala,
        # ou se repete a mesma frase logo em seguida.
        nivel = _nivel_por_trecho(wav)
        segs, ultimo = [], ('', -99.0)
        for s in (j.get('segments') or []):
            txt = (s.get('text') or '').strip()
            ini, fim = float(s.get('start') or 0), float(s.get('end') or 0)
            if not txt or float(s.get('no_speech_prob') or 0) > 0.6:
                continue
            if nivel and nivel(ini, fim) < 350:
                continue
            if txt.lower() == ultimo[0] and ini - ultimo[1] < 8:
                continue
            ultimo = (txt.lower(), ini)
            segs.append((ini, txt))
        if not segs and j.get('text'):
            segs = [(0.0, str(j['text']).strip())]
        return segs
    except Exception:
        return []


def _transcricao(arquivos: dict) -> str:
    atend = _segmentos(arquivos.get('sender'))
    cli = _segmentos(arquivos.get('recipient'))
    if atend or cli:
        linhas = sorted([(t, 'Atendente', x) for t, x in atend] + [(t, 'Cliente', x) for t, x in cli])
        return '\n'.join(f'[{int(t) // 60:02d}:{int(t) % 60:02d}] {q}: {x}' for t, q, x in linhas)
    return '\n'.join(x for _, x in _segmentos(arquivos.get('full')))


async def _anexar_na_ficha(db, conversa_id, bloco: str):
    """Acrescenta no campo Notas da Ficha do cliente (wa_conversas.notas), sem apagar o que ja existe."""
    if not conversa_id or not bloco:
        return
    await db.execute(text(
        "UPDATE wa_conversas SET notas = CASE WHEN COALESCE(TRIM(notas), '') = '' THEN :b "
        "ELSE notas || char(10) || char(10) || :b END WHERE id = :c"),
        {'b': bloco, 'c': int(conversa_id)})
    await db.commit()


def _bloco_ficha(quando: str, segundos: int, url: str, texto_t: str) -> str:
    dur = '%d:%02d' % (segundos // 60, segundos % 60) if segundos else ''
    cab = f'=== LIGACAO PELO WHATSAPP - {quando}' + (f' ({dur})' if dur else '') + ' ==='
    partes = [cab, 'TRANSCRICAO:', texto_t or '(transcricao indisponivel)']
    if url:
        partes.append(f'Gravacao: {url}')
    return '\n'.join(partes)


async def _pos_ligacao(call_id: str, conversa_id, arquivos: dict, chave: str,
                       segundos: int = 0, quando: str = ''):
    """Em segundo plano: busca a gravacao se o navegador nao recebeu e grava a transcricao
    (nota no chat + campo Notas da Ficha do cliente)."""
    from ..database import AsyncSessionLocal
    if call_id and not (arquivos.get('sender') and arquivos.get('recipient')):
        for _ in range(12):                      # ate ~2 min esperando a gravacao subir
            await asyncio.sleep(10)
            try:
                r = await asyncio.to_thread(requests.get, f'{DAPI}/calls/{call_id}',
                                            headers={'Authorization': chave}, timeout=20)
                if r.status_code == 200:
                    arquivos = {**arquivos, **_achar_arquivos(r.json())}
                    if arquivos.get('sender') and arquivos.get('recipient'):
                        break
            except Exception:
                pass
    async with AsyncSessionLocal() as db:
        if not arquivos.get('full'):
            await _nota(db, conversa_id, 'Gravacao da ligacao pelo WhatsApp nao ficou disponivel.')
            return
        await _nota(db, conversa_id, f"Gravacao da ligacao pelo WhatsApp: {arquivos['full']}")
        texto_t = await asyncio.to_thread(_transcricao, arquivos)
        if texto_t:
            await _nota(db, conversa_id, '[TRANSCRICAO DA LIGACAO]\n' + texto_t[:60000], autor='transcricao')
        # Ficha do cliente > Notas: a transcricao fica guardada junto do contato (pedido 18/09)
        await _anexar_na_ficha(db, conversa_id,
                               _bloco_ficha(quando, segundos, arquivos.get('full', ''), (texto_t or '')[:60000]))


@router.post('/wa/fim')
async def wa_fim(body: dict, bg: BackgroundTasks, user: Usuario = Depends(usuario_atual),
                 db: AsyncSession = Depends(get_db)):
    """Chamado pelo navegador quando a ligacao termina."""
    _, chave = await _numero_dapi(db, body.get('linha'))
    _solta(body.get('linha'))
    seg = int(body.get('segundos') or 0)
    motivo = str(body.get('motivo') or '')[:40]
    conversa_id = body.get('conversa_id')
    if seg > 0:
        resumo = '%d:%02d de conversa' % (seg // 60, seg % 60)
    else:
        resumo = 'nao atendida'
    if motivo:
        resumo += ', ' + motivo
    await _nota(db, conversa_id, f'Ligacao pelo WhatsApp encerrada ({resumo}).')
    if seg > 0:
        arquivos = _achar_arquivos(body.get('gravacao') or {})
        from zoneinfo import ZoneInfo
        quando = datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y %H:%M')
        bg.add_task(_pos_ligacao, str(body.get('callId') or ''), conversa_id, arquivos, chave, seg, quando)
    return {'ok': True}
