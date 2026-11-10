# -*- coding: utf-8 -*-
"""FILA DE LIGACAO: a lista de quem levantou a mao e esta esperando o telefone tocar.

A lista precisa servir para alguem que NAO conhece os casos (vendedor novo): cada lead
vem com o telefone, o que ele disse, o caso dele e um roteiro de abertura pronto.

Sai em dois lugares:
  - WhatsApp: lista enxuta, por ordem de prioridade, para trabalhar do celular
  - Drive: briefing completo em HTML, um card por lead, para o vendedor abrir
    no computador e trabalhar a fila inteira (pasta FILA_LIGACAO_PASTA_DRIVE no .env;
    vazio = nao sobe para o Drive)

A fila DRENA sozinha: quando o card sai de Lead Quente / Lead Qualificado no
CRM, a pessoa some da lista. Ou seja, mover o card e o gesto que marca "liguei".

Prioridade: quente antes de morno, e dentro de cada um, quem espera ha mais tempo.

Uso:
  fila_ligacao.py                  simula, nao envia nem sobe nada
  fila_ligacao.py --enviar         manda no WhatsApp e atualiza o Drive
  fila_ligacao.py --so-drive       so atualiza o briefing no Drive
"""
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from html import escape

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, '/opt/escritorio/INTEGRACOES')

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv(os.getenv('ESC_CONFIG_ENV', '/opt/escritorio/config/.env'))
load_dotenv('/opt/escritorio/config/.env', override=False)

import requests

DB = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
FILA = os.path.join(HERE, 'quentes_fila.json')
ROTEIROS = os.path.join(HERE, 'fila_ligacao_roteiros.json')   # cache dos roteiros
ESTADO = os.path.join(HERE, 'fila_ligacao_state.json')        # id do arquivo no Drive
PASTA_DRIVE = os.getenv('FILA_LIGACAO_PASTA_DRIVE', '')        # ID da pasta no Drive do escritorio
ETAPAS_NA_FILA = ('Lead Quente', 'Lead Qualificado')
CLAUDE_BIN = os.getenv('ESC_CLAUDE_BIN', os.path.expanduser('~/.local/bin/claude'))
# aceita lista separada por virgula: quando o vendedor entrar, e so acrescentar
DESTINOS = [t.strip() for t in (os.getenv('FILA_LIGACAO_DESTINO') or os.getenv('TEL_ALERTA_EQUIPE', '')).split(',') if t.strip()]

import wa_dapi  # noqa: E402  saida unica de WhatsApp pela D-API


def _json(path, padrao):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return padrao


def _salvar(path, dados):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)


def zapi_enviar_texto(telefone, texto):
    """Envia pela D-API (wa_dapi); o nome ficou para nao mexer em quem chama.
    ok so quando a D-API confirmou o envio."""
    d = ''.join(c for c in str(telefone) if c.isdigit())
    d = d if d.startswith('55') else '55' + d
    try:
        ok, info = wa_dapi.enviar_texto(d, texto)
        return bool(ok), json.dumps(info, ensure_ascii=False)[:150]
    except Exception as e:
        return False, str(e)[:150]


def _con_ro():
    return sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=15)


def conversa_recente(conversa_id, limite=10):
    try:
        con = _con_ro()
        rows = con.execute(
            "select origem, conteudo from wa_mensagens where conversa_id=? "
            "and length(conteudo)>1 order by id desc limit ?", (conversa_id, limite)).fetchall()
        con.close()
    except Exception:
        return ''
    rows.reverse()
    quem = {'contato': 'LEAD', 'ia': 'NOS', 'atendente': 'NOS', 'nota': 'NOTA'}
    return '\n'.join(f"{quem.get(o, o)}: {' '.join((c or '').split())[:200]}" for o, c in rows)


def roteiro(c):
    """Como abrir a ligacao. Gerado uma vez por lead e guardado em cache."""
    cache = _json(ROTEIROS, {})
    k = str(c['conversa_id'])
    if k in cache:
        return cache[k]
    env = dict(os.environ)
    env.pop('ANTHROPIC_API_KEY', None)          # o CLI usa o login dele
    prompt = (
        'Voce treina vendedores de um escritorio de advocacia trabalhista que defende '
        'TRABALHADORES (rescisao indireta, acidente de trabalho, fast-food, supermercado). Um vendedor novo, que nao conhece o caso, vai LIGAR para este '
        'lead agora. Escreva o briefing da ligacao.\n\n'
        'Formato exato, sem titulo e sem introducao:\n'
        'ABERTURA: uma frase para a primeira coisa a dizer ao telefone\n'
        'PERGUNTAR: duas perguntas curtas que faltam para qualificar\n'
        'ATENCAO: o risco ou a objecao dessa conversa especifica, em uma frase\n\n'
        'REGRAS: linguagem simples e didatica; NUNCA prometer resultado, valor ou ganho '
        'de causa (Provimento 205/2021 da OAB); nao usar travessao; o objetivo da '
        'ligacao e agendar a reuniao de fechamento com a advogada.\n\n'
        f"LEAD: {c.get('nome') or 'sem nome'}\n"
        f"SITUACAO: {c.get('situacao') or 'nao informado'}\n"
        f"CASO: {c.get('violacao') or 'nao detalhado'}\n"
        f"O QUE ELE RESPONDEU: \"{(c.get('resposta') or '')[:300]}\"\n\n"
        f"CONVERSA:\n{conversa_recente(c['conversa_id'])[:1800]}")
    texto = ''
    try:
        r = subprocess.run([CLAUDE_BIN, '-p', '--model', 'sonnet', '--tools', '',
                            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}'],
                           input=prompt.encode('utf-8'), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env,
                           cwd=tempfile.gettempdir(), timeout=180)
        if r.returncode == 0:
            texto = r.stdout.decode('utf-8', 'replace').strip()
    except Exception as e:
        print(f'  (roteiro pelo CLI falhou: {str(e)[:80]})')
    if not texto or len(texto) < 20:
        texto = ('ABERTURA: aqui e do escritorio Escritório Modelo, vi que voce respondeu no WhatsApp '
                 'sobre o seu caso e liguei para entender melhor.\n'
                 'PERGUNTAR: ha quanto tempo trabalha ou trabalhou nessa empresa? '
                 'Tem algum documento guardado, holerite ou extrato?\n'
                 'ATENCAO: confirmar se ja tem advogado antes de seguir.')
    texto = texto.replace(' — ', ', ').replace('—', ',')
    cache[k] = texto
    _salvar(ROTEIROS, cache)
    return texto


def fila():
    """Quem respondeu, nao tinha acao e o card segue parado esperando ligacao."""
    dados = _json(FILA, {})
    if not dados:
        return []
    con = _con_ro()
    agora = datetime.now()
    out = []
    for c in dados.values():
        if not c.get('respondeu_em') or c.get('classificacao') == 'ja_tem_acao':
            continue
        r = con.execute('select etapa, pipeline_id from wa_conversas where id=?',
                        (c['conversa_id'],)).fetchone()
        if not r or not (r[1] == 10 and r[0] in ETAPAS_NA_FILA):
            continue                      # ja foi trabalhado, sai da fila sozinho
        try:
            esperando = (agora - datetime.fromisoformat(c['respondeu_em'])).total_seconds() / 3600
        except Exception:
            esperando = 0
        d = dict(c)
        d['etapa'] = r[0]
        d['horas'] = esperando
        out.append(d)
    con.close()
    out.sort(key=lambda x: (x['temperatura'] != 'quente', -x['horas']))
    return out


def _espera(h):
    return f'{h:.0f}h' if h < 48 else f'{h/24:.0f} dias'


def texto_whatsapp(f):
    if not f:
        return '✅ *Fila de ligacao vazia.* Ninguem esperando retorno agora.'
    L = [f'☎️ *FILA DE LIGACAO* · {len(f)} esperando', '',
         '_Ordem de prioridade. Ligou e resolveu? Mova o card no CRM que ele sai da lista._', '']
    for i, c in enumerate(f, 1):
        alerta = ' 🔴' if c['horas'] > 48 else ''
        L.append(f"*{i}. {c.get('nome') or 'Sem nome'}*{alerta}")
        L.append(f"   📱 {c['telefone']}")
        L.append(f"   {c['temperatura']} · {c.get('situacao') or 'situacao nao informada'} · "
                 f"esperando ha {_espera(c['horas'])}")
        L.append(f"   Caso: {c.get('violacao') or 'ver conversa'}")
        L.append(f"   Disse: \"{(c.get('resposta') or '')[:90]}\"")
        L.append('')
    L.append('📋 Briefing completo de cada um, com roteiro da ligacao, no arquivo '
             'FILA DE LIGACAO Escritório Modelo no Drive (pasta AUTOMACOES).')
    return '\n'.join(L)


def html_briefing(f):
    hoje = datetime.now().strftime('%d/%m/%Y às %H:%M')
    cards = []
    for i, c in enumerate(f, 1):
        rot = roteiro(c)
        linhas = []
        for ln in rot.split('\n'):
            ln = ln.strip()
            if not ln:
                continue
            m = re.match(r'^(ABERTURA|PERGUNTAR|ATENCAO|ATENÇÃO)\s*:\s*(.+)$', ln, re.I)
            if m:
                linhas.append(f'<p><span class="rot">{escape(m.group(1).upper())}</span>'
                              f'{escape(m.group(2))}</p>')
            else:
                linhas.append(f'<p>{escape(ln)}</p>')
        urgente = ' urgente' if c['horas'] > 48 else ''
        cards.append(f"""
        <article class="card{urgente}">
          <div class="cab">
            <span class="n">{i}</span>
            <div>
              <h3>{escape(c.get('nome') or 'Sem nome')}</h3>
              <a class="tel" href="tel:+{escape(c['telefone'])}">{escape(c['telefone'])}</a>
            </div>
            <span class="espera">{_espera(c['horas'])}</span>
          </div>
          <dl>
            <dt>Termometro</dt><dd>{escape(c['temperatura'])} · {escape(c.get('situacao') or 'nao informado')}</dd>
            <dt>Caso</dt><dd>{escape(c.get('violacao') or 'ver conversa no CRM')}</dd>
            <dt>Ele disse</dt><dd class="fala">"{escape((c.get('resposta') or '')[:400])}"</dd>
          </dl>
          <div class="roteiro">{''.join(linhas)}</div>
        </article>""")
    corpo = ''.join(cards) if cards else '<p class="vazio">Ninguem esperando ligacao agora.</p>'
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fila de Ligacao Escritório Modelo</title><style>
:root{{--bg:#F1F4F3;--sf:#fff;--ink:#101D22;--ink2:#3D4E56;--mut:#6A7C84;--hair:#D8E0E0;--ak:#0088A8;--al:#A32117}}
*{{box-sizing:border-box}}
body{{background:var(--bg);color:var(--ink);font:16px/1.6 "Segoe UI",system-ui,sans-serif;margin:0;padding:0 18px 60px}}
.wrap{{max-width:840px;margin:0 auto}}
header{{padding:38px 0 20px;border-bottom:2px solid var(--ink)}}
h1{{font:600 40px/1 "Bahnschrift","Arial Narrow",sans-serif;margin:0 0 8px;letter-spacing:.01em}}
.sub{{color:var(--ink2);margin:0}} .st{{color:var(--mut);font:12px ui-monospace,Consolas,monospace;margin-top:10px}}
.card{{background:var(--sf);border:1px solid var(--hair);margin-top:16px}}
.card.urgente{{border-left:3px solid var(--al)}}
.cab{{display:flex;align-items:center;gap:14px;padding:16px 18px;border-bottom:1px solid var(--hair);flex-wrap:wrap}}
.cab .n{{font:600 20px "Bahnschrift","Arial Narrow",sans-serif;width:34px;height:34px;flex:none;
  display:flex;align-items:center;justify-content:center;border:2px solid var(--ak);color:var(--ak);border-radius:3px}}
.cab h3{{margin:0;font-size:19px}} .cab>div{{flex:1;min-width:180px}}
.tel{{font:15px ui-monospace,Consolas,monospace;color:var(--ak);text-decoration:none}}
.espera{{font:12px ui-monospace,Consolas,monospace;color:var(--mut);border:1px solid var(--hair);padding:4px 10px;border-radius:2px;white-space:nowrap}}
.card.urgente .espera{{color:var(--al);border-color:var(--al)}}
dl{{margin:0;padding:16px 18px;display:grid;grid-template-columns:104px 1fr;gap:8px 14px;border-bottom:1px solid var(--hair)}}
dt{{font:600 11px "Bahnschrift","Arial Narrow",sans-serif;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);padding-top:3px}}
dd{{margin:0;font-size:15px;color:var(--ink2)}} dd.fala{{font-style:italic}}
.roteiro{{padding:16px 18px;background:var(--bg)}}
.roteiro p{{margin:0 0 9px;font-size:15px;color:var(--ink2)}}
.rot{{display:inline-block;font:600 11px "Bahnschrift","Arial Narrow",sans-serif;letter-spacing:.1em;
  color:var(--ak);margin-right:8px}}
.vazio{{padding:40px 0;color:var(--mut)}}
footer{{margin-top:34px;padding-top:16px;border-top:1px solid var(--hair);color:var(--mut);font-size:13px}}
@media(max-width:560px){{dl{{grid-template-columns:1fr;gap:2px}}}}
</style></head><body><div class="wrap">
<header><h1>Fila de ligação</h1>
<p class="sub">Quem respondeu no WhatsApp e está esperando o telefone tocar. Ordem de prioridade: mais quente e esperando há mais tempo primeiro.</p>
<p class="st">Atualizado em {hoje} · sai da lista sozinho quando o card mudar de etapa no CRM</p></header>
{corpo}
<footer>Objetivo da ligação: agendar a reunião de fechamento com a advogada responsável. Nunca prometer resultado ou valor, por causa do Provimento 205/2021 da OAB. Se a pessoa disser que já tem advogado ou já entrou com ação, encerrar com cordialidade e mover o card para Lead Desqualificado.</footer>
</div></body></html>"""


def salvar_drive(html):
    if not PASTA_DRIVE:
        print('FILA_LIGACAO_PASTA_DRIVE vazio: briefing nao sobe para o Drive')
        return None
    from google_integration import autenticar_google
    from googleapiclient.http import MediaInMemoryUpload
    drive, _ = autenticar_google()
    st = _json(ESTADO, {})
    media = MediaInMemoryUpload(html.encode('utf-8'), mimetype='text/html')
    if st.get('file_id'):
        f = drive.files().update(fileId=st['file_id'], media_body=media,
                                 fields='id,webViewLink', supportsAllDrives=True).execute()
    else:
        f = drive.files().create(
            body={'name': 'FILA DE LIGACAO Escritório Modelo.html', 'parents': [PASTA_DRIVE]},
            media_body=media, fields='id,webViewLink', supportsAllDrives=True).execute()
        st['file_id'] = f['id']
        _salvar(ESTADO, st)
    return f.get('webViewLink')


if __name__ == '__main__':
    f = fila()
    print(f'fila de ligacao: {len(f)} pessoas')
    for c in f:
        print(f"  {c['temperatura']:6s} {(c.get('nome') or '')[:24]:24s} {c['telefone']} "
              f"esperando {_espera(c['horas'])}")
    enviar = '--enviar' in sys.argv
    so_drive = '--so-drive' in sys.argv
    if enviar or so_drive:
        try:
            link = salvar_drive(html_briefing(f))
            print(f'\nbriefing no Drive: {link}')
        except Exception as e:
            link = ''
            print(f'\nfalha ao subir no Drive: {str(e)[:160]}')
    if enviar and not f:
        print('fila vazia, nao envia (economia de mensagem)')
    elif enviar:
        txt = texto_whatsapp(f)
        if link:
            txt += f'\n{link}'
        for tel in DESTINOS:
            ok, resp = zapi_enviar_texto(tel, txt)
            print(f'envio para {tel}: ' + ('OK' if ok else f'FALHOU {resp}'))
    elif not so_drive:
        print('\n--- previa do WhatsApp ---')
        print(texto_whatsapp(f))
