""
import base64
import hashlib
import os
import subprocess
import tempfile
import threading

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

CREDENCIAL = os.environ.get('ESC_GOOGLE_SA', '/opt/escritorio/config/credentials.json')
PASTA_CACHE = os.environ.get('ESC_AUDIO_CACHE', '/opt/escritorio/portal_audio_cache')
URL = 'https://texttospeech.googleapis.com/v1/text:synthesize'
LIMITE = 3000
VELOCIDADE = 0.95

VOZES = {
    'aoede': 'pt-BR-Chirp3-HD-Aoede',
    'leda': 'pt-BR-Chirp3-HD-Leda',
    'charon': 'pt-BR-Chirp3-HD-Charon',
    'neural2': 'pt-BR-Neural2-C',
}
VOZ_PADRAO = 'aoede'

_sessao = None
_trava = threading.Lock()


def _google() -> AuthorizedSession:
    global _sessao
    with _trava:
        if _sessao is None:
            cred = service_account.Credentials.from_service_account_file(
                CREDENCIAL, scopes=['https://www.googleapis.com/auth/cloud-platform'])
            _sessao = AuthorizedSession(cred)
        return _sessao


def voz_valida(voz: str) -> str:
    return voz if voz in VOZES else VOZ_PADRAO


def _arquivo(texto: str, voz: str) -> str:
    chave = hashlib.sha1(f'{VOZES[voz]}|{VELOCIDADE}|{texto}'.encode('utf-8')).hexdigest()
    return os.path.join(PASTA_CACHE, f'{chave}.mp3')


def limpar_texto(texto: str) -> str:
    return ' '.join((texto or '').split())[:LIMITE]


def em_cache(texto: str, voz: str) -> bool:
    return os.path.exists(_arquivo(texto, voz_valida(voz)))


def falar(texto: str, voz: str) -> bytes:
    ""
    voz = voz_valida(voz)
    caminho = _arquivo(texto, voz)
    if os.path.exists(caminho):
        with open(caminho, 'rb') as f:
            return f.read()
    corpo = {'input': {'text': texto},
             'voice': {'languageCode': 'pt-BR', 'name': VOZES[voz]},
             'audioConfig': {'audioEncoding': 'MP3', 'speakingRate': VELOCIDADE}}
    r = _google().post(URL, json=corpo, timeout=40)
    r.raise_for_status()
    mp3 = base64.b64decode(r.json()['audioContent'])
    os.makedirs(PASTA_CACHE, exist_ok=True)
    temp = caminho + '.tmp'
    with open(temp, 'wb') as f:
        f.write(mp3)
    os.replace(temp, caminho)
    return mp3


STT_URL = 'https://speech.googleapis.com/v1/speech:recognize'
FALA_MAX_SEG = 60


def transcrever(conteudo: bytes) -> str:
    ""
    with tempfile.TemporaryDirectory() as pasta:
        entrada = os.path.join(pasta, 'entrada')
        saida = os.path.join(pasta, 'saida.flac')
        with open(entrada, 'wb') as f:
            f.write(conteudo)
        r = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', entrada, '-t', str(FALA_MAX_SEG),
                            '-ac', '1', '-ar', '16000', saida], capture_output=True, timeout=60)
        if r.returncode != 0 or not os.path.exists(saida):
            raise RuntimeError('audio invalido: ' + (r.stderr or b'')[:200].decode(errors='ignore'))
        with open(saida, 'rb') as f:
            flac = f.read()
    conteudo_b64 = base64.b64encode(flac).decode()
    for modelo in (None, 'latest_long', 'latest_short'):
        config = {'encoding': 'FLAC', 'sampleRateHertz': 16000, 'languageCode': 'pt-BR',
                  'enableAutomaticPunctuation': True}
        if modelo:
            config['model'] = modelo
        try:
            resp = _google().post(STT_URL, json={'config': config, 'audio': {'content': conteudo_b64}},
                                  timeout=60)
            resp.raise_for_status()
        except Exception:
            if modelo is None:
                raise
            continue
        partes = [((res.get('alternatives') or [{}])[0].get('transcript') or '').strip()
                  for res in resp.json().get('results', [])]
        texto = ' '.join(p for p in partes if p)
        if len(texto) >= 3:
            return texto
    return ''
