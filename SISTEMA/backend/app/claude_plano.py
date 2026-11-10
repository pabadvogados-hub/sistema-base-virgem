# -*- coding: utf-8 -*-
"""Claude pelo PLANO MAX no lugar da API (07/10/2026).

Quem chamava `anthropic.Anthropic(...).messages.create(...)` passa a chamar
`claude_plano.Anthropic(origem='...').messages.create(...)` com os mesmos argumentos: a chamada vai para
a ponte do root (SYNC/claude_ponte.py, 127.0.0.1:8791), que roda o Claude Code da assinatura. A resposta
imita a da API (content[0].text, stop_reason, usage, model), entao o resto do codigo nao muda.

Tambem serve direto: perguntar(...) e perguntar_json(...).

O que NAO passa por aqui: chamadas com `tools=` (agentes, Alexa), que continuam no caminho antigo.
Quando a ponte recusa (pausa do plano, teto do dia, fora do ar), levanta PlanoIndisponivel, cuja mensagem
traz "429": os chamadores que ja tinham reserva (Gemini, texto padrao) caem nela sozinhos.

Bateria: python -m app.claude_plano
"""
import base64
import json
import os
import re
import tempfile
import time

PONTE = os.getenv('ESC_CLAUDE_PONTE', 'http://127.0.0.1:8791')
_EXT = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif', 'application/pdf': '.pdf'}


class PlanoIndisponivel(Exception):
    """A ponte do plano nao atendeu (pausa, teto, fora do ar). A mensagem traz 429 de proposito:
    e o que os caminhos de reserva ja reconhecem (ia_reserva.falha_de_conta)."""


class NaoSuportado(Exception):
    pass


TOKEN_ARQ = os.getenv('ESC_PONTE_TOKEN_ARQ', '/opt/escritorio/config/claude_ponte.token')


def _token():
    """A senha local da ponte (arquivo root:www-data 640; o backend e as rotinas do root leem)."""
    v = os.getenv('ESC_PONTE_TOKEN')
    if v:
        return v.strip()
    try:
        with open(TOKEN_ARQ, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def _post(corpo, timeout):
    import requests
    try:
        r = requests.post(PONTE + '/perguntar', json=corpo, timeout=timeout + 15, headers={'X-Ponte-Token': _token()})
    except Exception as e:
        raise PlanoIndisponivel('plano Max: ponte fora do ar [429]: %s' % str(e)[:120])
    if r.status_code == 429:
        raise PlanoIndisponivel('plano Max: %s [429]' % (r.json().get('erro') if r.headers.get('content-type', '').startswith('application/json') else r.text[:160]))
    if r.status_code >= 400:
        raise PlanoIndisponivel('plano Max: ponte respondeu %s [429]: %s' % (r.status_code, r.text[:200]))
    return r.json()


def perguntar(prompt, system=None, model='haiku', origem='backend', timeout=180, arquivos=None):
    """Pergunta ao Claude do plano. Devolve {'texto', 'usage', 'modelo', 'segundos', 'stop_reason'}."""
    return _post({'prompt': prompt, 'system': system or '', 'model': model, 'origem': origem,
                  'timeout': int(timeout), 'arquivos': list(arquivos or [])}, int(timeout))


def perguntar_json(prompt, system=None, model='haiku', origem='backend', timeout=180):
    """Pergunta e devolve o primeiro objeto JSON da resposta, ou None."""
    r = perguntar(prompt, system, model, origem, timeout)
    m = re.search(r'\{.*\}', r.get('texto') or '', re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


# ----------------------------------------------------------------------------- a imitacao da API
class _Bloco:
    def __init__(self, texto):
        self.type = 'text'
        self.text = texto


class _Usage:
    def __init__(self, u):
        u = u or {}
        self.input_tokens = int(u.get('input_tokens') or 0) + int(u.get('cache_read_input_tokens') or 0) + int(u.get('cache_creation_input_tokens') or 0)
        self.output_tokens = int(u.get('output_tokens') or 0)


class _Resposta:
    def __init__(self, r, modelo_pedido):
        self.content = [_Bloco(r.get('texto') or '')]
        self.stop_reason = r.get('stop_reason') or 'end_turn'
        self.usage = _Usage(r.get('usage'))
        self.model = r.get('modelo') or modelo_pedido
        self.id = 'plano-%d' % int(time.time() * 1000)
        self.role = 'assistant'
        self.type = 'message'
        self.segundos = r.get('segundos')


def _achatar(messages, system=None, output_config=None):
    """messages da API -> (system, prompt, arquivos temporarios). Imagens e PDFs em base64 viram arquivo
    para a ponte mandar o Claude ler com a ferramenta Read."""
    arquivos = []
    partes = []
    msgs = list(messages or [])
    for m in msgs:
        papel = 'USUÁRIO' if (m.get('role') or 'user') == 'user' else 'ASSISTENTE'
        conteudo = m.get('content')
        textos = []
        if isinstance(conteudo, str):
            textos.append(conteudo)
        else:
            for b in conteudo or []:
                if not isinstance(b, dict):
                    textos.append(str(b))
                elif b.get('type') == 'text':
                    textos.append(str(b.get('text') or ''))
                elif b.get('type') in ('image', 'document'):
                    src = b.get('source') or {}
                    if src.get('type') == 'base64' and src.get('data'):
                        ext = _EXT.get(src.get('media_type') or '', '.bin')
                        fd, caminho = tempfile.mkstemp(prefix='esc_plano_', suffix=ext)
                        with os.fdopen(fd, 'wb') as f:
                            f.write(base64.b64decode(src['data']))
                        os.chmod(caminho, 0o644)
                        arquivos.append(caminho)
                        textos.append('[ARQUIVO ANEXADO: %s]' % caminho)
                    else:
                        textos.append('[arquivo nao incluido]')
                elif b.get('type') == 'tool_result':
                    textos.append('[resultado de ferramenta: %s]' % str(b.get('content'))[:500])
        partes.append((papel, '\n'.join(t for t in textos if t)))
    if len(partes) == 1 and partes[0][0] == 'USUÁRIO':
        prompt = partes[0][1]
    else:
        prompt = ('CONVERSA ATÉ AQUI (do mais antigo ao mais novo):\n\n'
                  + '\n\n'.join('%s: %s' % (p, t) for p, t in partes)
                  + '\n\nResponda como o ASSISTENTE: só a próxima mensagem, sem prefixo.')
    sistema = system if isinstance(system, str) else ''.join(
        str(b.get('text') or '') for b in (system or []) if isinstance(b, dict))
    fmt = ((output_config or {}).get('format') or {}) if isinstance(output_config, dict) else {}
    if fmt.get('type') == 'json_schema' and fmt.get('schema'):
        sistema = (sistema or '') + ('\n\nRESPONDA SOMENTE com um JSON válido (sem comentários, sem markdown, sem texto '
                                     'antes ou depois) que siga este esquema JSON:\n' + json.dumps(fmt['schema'], ensure_ascii=False)[:8000])
    return sistema or '', prompt, arquivos


def _timeout_para(max_tokens):
    try:
        n = int(max_tokens or 0)
    except (TypeError, ValueError):
        n = 0
    return min(3600, max(150, n // 15))


class _Mensagens:
    def __init__(self, origem):
        self.origem = origem

    def create(self, **kw):
        if kw.get('tools'):
            raise NaoSuportado('chamada com tools nao passa pelo plano (use a API ou a reserva)')
        sistema, prompt, arquivos = _achatar(kw.get('messages'), kw.get('system'), kw.get('output_config'))
        try:
            r = _post({'prompt': prompt, 'system': sistema, 'model': kw.get('model') or 'sonnet', 'origem': self.origem,
                       'timeout': _timeout_para(kw.get('max_tokens')), 'arquivos': arquivos}, _timeout_para(kw.get('max_tokens')))
        finally:
            for a in arquivos:
                try:
                    os.unlink(a)
                except OSError:
                    pass
        return _Resposta(r, kw.get('model'))

    def stream(self, **kw):
        return _Stream(self, kw)


class _Stream:
    """`with cli.messages.stream(...) as s: s.get_final_message()` da API, sem transmissao: responde inteiro."""
    def __init__(self, mensagens, kw):
        self._m, self._kw, self._final = mensagens, kw, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        if self._final is None:
            self._final = self._m.create(**self._kw)
        return self._final


class Anthropic:
    """No lugar de anthropic.Anthropic(api_key=...). Aceita e ignora api_key e o resto."""
    def __init__(self, origem=None, **_ignorados):
        self.messages = _Mensagens(origem or 'backend')


# ----------------------------------------------------------------------------- bateria
def _bateria():
    import sys
    ok = total = 0

    def caso(nome, cond):
        nonlocal ok, total
        total += 1
        ok += 1 if cond else 0
        print('  [%s] %s' % ('OK ' if cond else 'X  ', nome))

    s, p, a = _achatar([{'role': 'user', 'content': 'oi'}], 'SYS')
    caso('uma mensagem de usuario vira o prompt puro', s == 'SYS' and p == 'oi' and a == [])
    s, p, a = _achatar([{'role': 'user', 'content': 'oi'}, {'role': 'assistant', 'content': 'ola'},
                        {'role': 'user', 'content': [{'type': 'text', 'text': 'tudo bem?'}]}], [{'type': 'text', 'text': 'S1'}])
    caso('historico vira conversa rotulada e pede so a proxima mensagem',
         s == 'S1' and p.startswith('CONVERSA ATÉ AQUI') and 'USUÁRIO: oi' in p and 'ASSISTENTE: ola' in p and p.rstrip().endswith('sem prefixo.'))
    img = base64.b64encode(b'\x89PNG\r\n').decode()
    s, p, a = _achatar([{'role': 'user', 'content': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': img}},
                                                     {'type': 'text', 'text': 'o que e?'}]}])
    caso('imagem em base64 vira arquivo temporario citado no prompt', len(a) == 1 and a[0].endswith('.png') and os.path.exists(a[0]) and a[0] in p)
    for x in a:
        os.unlink(x)
    s, p, a = _achatar([{'role': 'user', 'content': 'x'}], 'S', {'format': {'type': 'json_schema', 'schema': {'type': 'object'}}})
    caso('json_schema vira instrucao no system', 'RESPONDA SOMENTE com um JSON' in s and '"type": "object"' in s)
    caso('timeout cresce com max_tokens', _timeout_para(400) == 150 and _timeout_para(64000) == 3600 and _timeout_para(20000) == 1333)

    chamadas = []
    global _post
    original = _post

    def falso(corpo, timeout):
        chamadas.append(corpo)
        return {'texto': 'resposta {"ok": true}', 'usage': {'input_tokens': 10, 'output_tokens': 5}, 'modelo': 'haiku', 'stop_reason': 'end_turn', 'segundos': 1.2}
    _post = falso
    try:
        cli = Anthropic(origem='teste', api_key='ignorada')
        r = cli.messages.create(model='claude-haiku-4-5-20251001', max_tokens=300, system='S', messages=[{'role': 'user', 'content': 'oi'}],
                                thinking={'type': 'disabled'})
        caso('messages.create imita a API: content[0].text, stop_reason, usage, model',
             r.content[0].text == 'resposta {"ok": true}' and r.content[0].type == 'text' and r.stop_reason == 'end_turn'
             and r.usage.output_tokens == 5 and r.usage.input_tokens == 10 and r.model == 'haiku')
        caso('a chamada levou origem, modelo pedido e timeout', chamadas[-1]['origem'] == 'teste' and chamadas[-1]['model'] == 'claude-haiku-4-5-20251001' and chamadas[-1]['timeout'] == 150)
        with cli.messages.stream(model='claude-opus-5', max_tokens=20000, system='S', messages=[{'role': 'user', 'content': 'oi'}],
                                 output_config={'effort': 'medium', 'format': {'type': 'json_schema', 'schema': {'type': 'object'}}}) as st:
            fin = st.get_final_message()
        caso('messages.stream com get_final_message funciona e leva o esquema', fin.content[0].text.startswith('resposta') and 'RESPONDA SOMENTE' in chamadas[-1]['system'])
        caso('perguntar_json devolve o objeto', perguntar_json('oi', 'S', 'haiku', 'teste') == {'ok': True})
        try:
            cli.messages.create(model='x', max_tokens=1, messages=[{'role': 'user', 'content': 'oi'}], tools=[{'name': 'x'}])
            caso('tools e recusado', False)
        except NaoSuportado:
            caso('tools e recusado', True)

        def recusa(corpo, timeout):
            raise PlanoIndisponivel('plano Max: teto diario da origem [429]')
        _post = recusa
        try:
            cli.messages.create(model='x', max_tokens=1, messages=[{'role': 'user', 'content': 'oi'}])
            caso('recusa da ponte vira PlanoIndisponivel com 429 na mensagem', False)
        except PlanoIndisponivel as e:
            caso('recusa da ponte vira PlanoIndisponivel com 429 na mensagem', '429' in str(e))
    finally:
        _post = original
    print('claude_plano: %d/%d, bateria ok' % (ok, total) if ok == total else 'claude_plano: %d/%d FALHOU' % (ok, total))
    return ok == total


if __name__ == '__main__':
    raise SystemExit(0 if _bateria() else 1)
