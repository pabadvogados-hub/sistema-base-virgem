#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PONTE DO PLANO MAX (Advogada, 07/10/2026): "substitua o consumo de creditos da API pelos tokens do plano Max".

Servico do root (systemd esc-claude-ponte) em 127.0.0.1:8791. Recebe uma pergunta (system + prompt,
opcionalmente arquivos) e roda o Claude Code da assinatura (`claude -p`), devolvendo o texto e o uso.
O backend (www-data) e as rotinas do cron falam com ele por HTTP; so o root tem a credencial do plano.

Garantias:
- ANTHROPIC_API_KEY e REMOVIDA do ambiente do subprocesso: por aqui e impossivel gastar credito de API;
- sem ferramentas (--tools "") a nao ser que venham arquivos para ler (so Read), sem sessao gravada,
  cwd numa pasta vazia (nenhum CLAUDE.md ou settings de projeto entra no prompt);
- teto diario por origem e total (config/claude_ponte.json), contagem em config/claude_ponte_uso.json;
- quando o plano devolve limite de uso, a ponte pausa (producao/claude_ponte.pausa) e responde 429:
  cada chamador cai no seu caminho de reserva (Gemini, texto padrao ou "tenta depois");
- no maximo PARALELO chamadas ao mesmo tempo; as outras esperam na fila.

    GET  /status                      -> uso de hoje, pausa, config
    POST /perguntar  {"prompt", "system", "model", "origem", "timeout", "arquivos": [caminhos]}
                                      -> {"texto", "usage": {...}, "modelo", "segundos", "stop_reason"}
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLAUDE_BIN = os.getenv('ESC_CLAUDE_BIN', '/root/.local/bin/claude')
PORTA = int(os.getenv('ESC_PONTE_PORTA', '8791'))
CONFIG = os.getenv('ESC_PONTE_CONFIG', '/opt/escritorio/config/claude_ponte.json')
USO = os.getenv('ESC_PONTE_USO', '/opt/escritorio/config/claude_ponte_uso.json')
PAUSA = os.getenv('ESC_PONTE_PAUSA', '/opt/escritorio/producao/claude_ponte.pausa')
LOG = os.getenv('ESC_PONTE_LOG', '/var/log/escritorio-claude-ponte.log')
CWD = os.getenv('ESC_PONTE_CWD', '/opt/escritorio/producao/ponte')
PARALELO = int(os.getenv('ESC_PONTE_PARALELO', '3'))
PADRAO = {'teto_dia_total': 1500, 'teto_padrao_origem': 400, 'teto_dia_por_origem': {}, 'opus': 'sonnet',
          'pausa_min': 20, 'timeout_max': 3600}
LIMITE_RE = re.compile(r'usage limit|rate limit|too many requests|limit reached|out of extra usage|\b429\b|quota|'
                       r'overloaded|hit your limit|resets at', re.I)

_sem = threading.BoundedSemaphore(PARALELO)
_lock = threading.Lock()


def log(msg):
    linha = '%s %s' % (dt.datetime.now().strftime('%d/%m %H:%M:%S'), msg)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(linha + '\n')
    except OSError:
        pass
    print(linha, flush=True)


def config():
    c = dict(PADRAO)
    try:
        with open(CONFIG, encoding='utf-8') as f:
            c.update(json.load(f) or {})
    except (OSError, ValueError):
        pass
    return c


def _hoje():
    return dt.date.today().isoformat()


def uso_hoje():
    try:
        with open(USO, encoding='utf-8') as f:
            d = json.load(f) or {}
    except (OSError, ValueError):
        d = {}
    return d, d.get(_hoje()) or {}


def registrar_uso(origem, modelo, usage, segundos, erro=False):
    with _lock:
        d, _ = uso_hoje()
        dia = d.setdefault(_hoje(), {})
        o = dia.setdefault(origem, {'chamadas': 0, 'erros': 0, 'entrada': 0, 'saida': 0, 'segundos': 0, 'modelos': {}})
        o['chamadas'] += 1
        o['erros'] += 1 if erro else 0
        o['entrada'] += int((usage or {}).get('input_tokens') or 0)
        o['saida'] += int((usage or {}).get('output_tokens') or 0)
        o['segundos'] += int(segundos or 0)
        o['modelos'][modelo] = o['modelos'].get(modelo, 0) + 1
        # guarda so os ultimos 40 dias
        for k in sorted(d)[:-40]:
            d.pop(k, None)
        tmp = USO + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, USO)


def pausa_ate():
    try:
        with open(PAUSA, encoding='utf-8') as f:
            return float((f.read() or '0').strip() or 0)
    except (OSError, ValueError):
        return 0.0


def pausar(motivo):
    c = config()
    ate = time.time() + 60 * int(c.get('pausa_min') or 20)
    with open(PAUSA, 'w', encoding='utf-8') as f:
        f.write('%s\n%s\n' % (ate, motivo[:300]))
    log('PAUSA ate %s: %s' % (dt.datetime.fromtimestamp(ate).strftime('%H:%M'), motivo[:160]))
    return ate


def modelo_alias(nome):
    n = (nome or '').lower()
    c = config()
    if 'haiku' in n:
        return 'haiku'
    if 'opus' in n:
        return c.get('opus') or 'sonnet'
    if n in ('sonnet', 'opus', 'haiku'):
        return n
    return 'sonnet'


def teto_estourado(origem):
    c = config()
    _, dia = uso_hoje()
    total = sum(int(v.get('chamadas') or 0) for v in dia.values())
    if total >= int(c.get('teto_dia_total') or 0):
        return 'teto diario total (%d) atingido' % c.get('teto_dia_total')
    teto = int((c.get('teto_dia_por_origem') or {}).get(origem) or c.get('teto_padrao_origem') or 0)
    if int((dia.get(origem) or {}).get('chamadas') or 0) >= teto:
        return 'teto diario da origem %s (%d) atingido' % (origem, teto)
    return None


TOKEN_ARQ = os.getenv('ESC_PONTE_TOKEN_ARQ', '/opt/escritorio/config/claude_ponte.token')
PREFIXO_ANEXO = os.path.join(tempfile.gettempdir(), 'esc_plano_')   # so o substituto (claude_plano) cria anexos


def token_esperado():
    try:
        with open(TOKEN_ARQ, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def _anexos_seguros(arquivos, pasta_job):
    """So aceita anexos criados pelo substituto (prefixo fixo em /tmp), copiados para a pasta do trabalho
    dentro do cwd da ponte: o Claude le apenas o que esta ali, nunca um caminho apontado de fora."""
    saida = []
    for a in arquivos or []:
        a = os.path.realpath(str(a))
        if not a.startswith(PREFIXO_ANEXO) or not os.path.isfile(a):
            raise ValueError('anexo recusado (fora do prefixo permitido): %s' % a[:120])
        destino = os.path.join(pasta_job, os.path.basename(a))
        shutil.copy2(a, destino)
        saida.append(destino)
    return saida


def rodar(prompt, system='', modelo='sonnet', arquivos=None, timeout=180):
    """Roda o Claude do plano uma vez. Devolve (dict da resposta, erro)."""
    os.makedirs(CWD, exist_ok=True)
    env = dict(os.environ)
    env.pop('ANTHROPIC_API_KEY', None)                      # assinatura, nunca credito de API
    env.setdefault('HOME', '/root')
    cmd = [CLAUDE_BIN, '-p', '--model', modelo, '--output-format', 'json', '--no-session-persistence',
           '--setting-sources', 'user']
    pasta_job = tempfile.mkdtemp(prefix='job_', dir=CWD)
    try:
        try:
            anexos = _anexos_seguros(arquivos, pasta_job)
        except ValueError as e:
            return None, str(e)
        if anexos:
            cmd += ['--tools', 'Read', '--max-turns', '6']
            prompt = ('Antes de responder, leia com a ferramenta Read o(s) arquivo(s): %s\n\n%s'
                      % (', '.join(anexos), prompt))
        else:
            cmd += ['--tools', '']
        if (system or '').strip():
            cmd += ['--system-prompt', system]
        t0 = time.time()
        try:
            r = subprocess.run(cmd, input=prompt.encode('utf-8'), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, cwd=pasta_job, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, 'tempo esgotado (%ds)' % timeout
    finally:
        shutil.rmtree(pasta_job, ignore_errors=True)
    seg = time.time() - t0
    saida = r.stdout.decode('utf-8', 'replace')
    erro_txt = r.stderr.decode('utf-8', 'replace')
    try:
        d = json.loads(saida[saida.index('{'):]) if '{' in saida else {}
    except ValueError:
        d = {}
    texto = d.get('result') if isinstance(d, dict) else None
    if r.returncode != 0 or not isinstance(d, dict) or d.get('is_error') or texto is None:
        msg = (str(texto or '') + ' ' + erro_txt + ' ' + saida[-400:]).strip()
        return {'segundos': seg, 'usage': d.get('usage') if isinstance(d, dict) else None}, msg[:600] or ('rc=%s' % r.returncode)
    return {'texto': texto, 'usage': d.get('usage') or {}, 'stop_reason': d.get('stop_reason') or 'end_turn',
            'modelo': modelo, 'segundos': seg, 'custo_equivalente_usd': d.get('total_cost_usd')}, None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, codigo, corpo):
        dados = json.dumps(corpo, ensure_ascii=False).encode('utf-8')
        self.send_response(codigo)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        if self.path.startswith('/status') or self.path.startswith('/saude'):
            _, dia = uso_hoje()
            ate = pausa_ate()
            return self._json(200, {'ok': True, 'hoje': dia, 'pausa_ate': dt.datetime.fromtimestamp(ate).isoformat() if ate > time.time() else None,
                                    'config': config(), 'paralelo': PARALELO, 'bin': CLAUDE_BIN})
        return self._json(404, {'erro': 'rota desconhecida'})

    def do_POST(self):
        if not self.path.startswith('/perguntar'):
            return self._json(404, {'erro': 'rota desconhecida'})
        try:
            n = int(self.headers.get('Content-Length') or 0)
            corpo = json.loads(self.rfile.read(n).decode('utf-8') or '{}')
        except (ValueError, TypeError):
            return self._json(400, {'erro': 'JSON invalido'})
        esperado = token_esperado()
        if esperado and (self.headers.get('X-Ponte-Token') or corpo.get('token') or '') != esperado:
            return self._json(401, {'erro': 'senha da ponte invalida'})
        prompt = str(corpo.get('prompt') or '').strip()
        if not prompt:
            return self._json(400, {'erro': 'prompt vazio'})
        origem = re.sub(r'[^a-z0-9_.-]+', '-', str(corpo.get('origem') or 'sem-origem').lower())[:40]
        modelo = modelo_alias(corpo.get('model'))
        c = config()
        timeout = max(30, min(int(corpo.get('timeout') or 180), int(c.get('timeout_max') or 3600)))
        ate = pausa_ate()
        if ate > time.time():
            return self._json(429, {'erro': 'plano em pausa ate %s' % dt.datetime.fromtimestamp(ate).strftime('%H:%M'), 'pausa_ate': ate})
        estourou = teto_estourado(origem)
        if estourou:
            log('RECUSA %s: %s' % (origem, estourou))
            return self._json(429, {'erro': estourou})
        if not _sem.acquire(timeout=timeout):
            return self._json(503, {'erro': 'fila cheia (%d em andamento)' % PARALELO})
        try:
            r, erro = rodar(prompt, corpo.get('system') or '', modelo, corpo.get('arquivos') or [], timeout)
        finally:
            _sem.release()
        if erro:
            registrar_uso(origem, modelo, (r or {}).get('usage'), (r or {}).get('segundos'), erro=True)
            if LIMITE_RE.search(erro):
                ate = pausar(erro)
                log('LIMITE %s (%s): %s' % (origem, modelo, erro[:160]))
                return self._json(429, {'erro': 'limite do plano: ' + erro[:200], 'pausa_ate': ate})
            log('ERRO %s (%s): %s' % (origem, modelo, erro[:200]))
            return self._json(502, {'erro': erro[:400]})
        registrar_uso(origem, modelo, r.get('usage'), r.get('segundos'))
        log('OK %s %s %.1fs saida=%s' % (origem, modelo, r['segundos'], (r.get('usage') or {}).get('output_tokens')))
        return self._json(200, r)


def main():
    if '--teste' in sys.argv:
        r, erro = rodar('Responda so a palavra OK.', 'Voce responde em uma palavra.', 'haiku', None, 120)
        print(json.dumps({'resposta': r, 'erro': erro}, ensure_ascii=False)[:400])
        return 0 if not erro else 1
    os.makedirs(CWD, exist_ok=True)
    os.makedirs(os.path.dirname(USO), exist_ok=True)
    srv = ThreadingHTTPServer(('127.0.0.1', PORTA), Handler)
    srv.daemon_threads = True
    log('ponte do plano Max no ar em 127.0.0.1:%d (bin %s, paralelo %d)' % (PORTA, CLAUDE_BIN, PARALELO))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
