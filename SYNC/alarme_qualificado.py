# -*- coding: utf-8 -*-
"""ALARME DE LEAD TOTALMENTE QUALIFICADO: liga agora.

O termometro roda uma vez por dia, entao um lead que chega a tarde ficaria um dia inteiro sem
ninguem saber que ele esta no ponto. Este alarme roda de 15 em 15 minutos e classifica NA HORA.

Criterio de "ligar agora":
  violacao trabalhista identificada  E  (a pessoa JA SAIU da empresa  OU  e caso urgente mesmo
  estando na empresa: rescisao indireta, acidente ou doenca do trabalho).
Quem ja saiu tem prescricao bienal correndo; quem esta sofrendo na empresa (salario atrasado,
FGTS sem deposito, assedio, acidente) precisa de orientacao antes de qualquer decisao.

A leitura e feita pelo Claude Code do servidor (ESC_CLAUDE_BIN). Cada lead so dispara UMA vez.
Destinos: o vendedor da vez (VENDEDORES) e os telefones de ALARME_DESTINO / QUENTES_AVISO_TEL.

Uso:
  alarme_qualificado.py            simula
  alarme_qualificado.py --enviar   dispara o alarme de verdade
"""
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv(os.getenv('ESC_CONFIG_ENV', '/opt/escritorio/config/.env'))
load_dotenv('/opt/escritorio/config/.env', override=False)

import requests

DB = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
ESTADO = os.path.join(HERE, 'alarme_qualificado_state.json')
CLAUDE_BIN = os.getenv('ESC_CLAUDE_BIN', os.path.expanduser('~/.local/bin/claude'))
JANELA_DIAS = int(os.getenv('ALARME_JANELA_DIAS', '7'))     # so lead recente
MAX_POR_RODADA = int(os.getenv('ALARME_MAX', '8'))
ETAPA_ALVO = 'Lead Quente'

# quem recebe o alarme: os telefones de ALARME_DESTINO (ou QUENTES_AVISO_TEL), mais o vendedor da vez
# (roteamento_vendedoras.py, equipe em VENDEDORES). Tudo vazio = so a tarefa na tela.
DESTINOS = [t.strip() for t in os.getenv(
    'ALARME_DESTINO', os.getenv('QUENTES_AVISO_TEL') or os.getenv('TEL_ALERTA_EQUIPE', '')).split(',') if t.strip()]
import roteamento_vendedoras as ROTA  # noqa: E402

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
    d = ''.join(c for c in str(telefone or '') if c.isdigit())
    if not d:
        return False, "sem destino"
    d = d if d.startswith('55') else '55' + d
    try:
        ok, info = wa_dapi.enviar_texto(d, texto)
        return bool(ok), json.dumps(info, ensure_ascii=False)[:140]
    except Exception as e:
        return False, str(e)[:140]


def _con(ro=True):
    if ro:
        return sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=20)
    c = sqlite3.connect(DB, timeout=20)
    c.execute('pragma busy_timeout=8000')
    return c


def conversa(cid, limite=25):
    con = _con()
    rows = con.execute(
        "select origem, conteudo from wa_mensagens where conversa_id=? and length(conteudo)>1 "
        "order by id desc limit ?", (cid, limite)).fetchall()
    con.close()
    rows.reverse()
    quem = {'contato': 'LEAD', 'ia': 'NOS', 'atendente': 'NOS', 'nota': 'NOTA'}
    return '\n'.join(f"{quem.get(o, o)}: {' '.join((c or '').split())[:220]}" for o, c in rows)


def classificar(cid):
    """Devolve dict com violacao, saiu, urgente (bool), resumo e o porque. None em erro."""
    txt = conversa(cid)
    if len(txt) < 40:
        return None
    env = dict(os.environ)
    env.pop('ANTHROPIC_API_KEY', None)          # o CLI usa o login dele
    prompt = (
        'Voce faz a triagem de leads de um escritorio trabalhista que defende TRABALHADORES. '
        'Leia a conversa e responda em JSON puro, sem texto fora do JSON:\n'
        '{"violacao": true/false, "saiu": true/false, '
        '"data_saida": "quando saiu, do jeito que a pessoa falou, ou vazio se nao disse", '
        '"direitos": ["lista curta dos direitos citados na conversa"], '
        '"caso": "ate 8 palavras", "porque": "ate 15 palavras", '
        '"urgente": true/false, "tema": "rescisao indireta | acidente | outro"}\n\n'
        'violacao = a conversa indica alguma irregularidade trabalhista concreta '
        '(hora extra nao paga, intervalo cortado, sem registro, pagamento por fora, descontos indevidos, '
        'acumulo de funcao, salario atrasado, FGTS nao depositado, assedio, acidente, etc).\n'
        'saiu = a pessoa JA SAIU da empresa (foi demitida, pediu conta, contrato acabou). '
        'Se ela ainda trabalha la, saiu=false. Se nao der para saber, saiu=false.\n'
        'urgente = a pessoa AINDA esta na empresa, mas relata situacao grave que pede orientacao ja: '
        'salario atrasado, FGTS sem deposito, assedio ou humilhacao, acidente ou doenca do trabalho, '
        'afastamento, risco a saude. Na duvida, urgente=false.\n\n'
        f'CONVERSA:\n{txt[:4000]}')
    try:
        r = subprocess.run([CLAUDE_BIN, '-p', '--model', 'sonnet', '--tools', '',
                            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}'],
                           input=prompt.encode('utf-8'), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env,
                           cwd=tempfile.gettempdir(), timeout=150)
        if r.returncode != 0:
            return None
        mm = re.search(r'\{.*\}', r.stdout.decode('utf-8', 'replace'), re.S)
        if not mm:
            return None
        d = json.loads(mm.group(0))
        dirs = d.get('direitos') or []
        if isinstance(dirs, str):
            dirs = [dirs]
        return {'violacao': bool(d.get('violacao')), 'saiu': bool(d.get('saiu')),
                'urgente': bool(d.get('urgente')), 'tema': str(d.get('tema') or '')[:40],
                'data_saida': str(d.get('data_saida') or '')[:40],
                'direitos': [str(x)[:48] for x in dirs if str(x).strip()][:6],
                'caso': str(d.get('caso') or '')[:60], 'porque': str(d.get('porque') or '')[:90]}
    except Exception as e:
        print(f'  (CLI falhou: {str(e)[:80]})')
        return None


def candidatos():
    """Leads de anuncio recentes, que falaram de verdade e ainda nao viraram cliente."""
    est = _json(ESTADO, {'vistos': {}})
    corte = (datetime.utcnow() - timedelta(days=JANELA_DIAS)).strftime('%Y-%m-%d')
    con = _con()
    clientes = set()
    for tel, cel in con.execute('select telefone, celular from jur_clientes'):
        for t in (tel, cel):
            if t:
                clientes.add(''.join(c for c in str(t) if c.isdigit())[-8:])
    out = []
    for cid, nome, tel, etapa, org in con.execute(
            "select id, contato_nome, contato_telefone, etapa, origem from wa_conversas "
            "where pipeline_id=10 and criado_em >= ?", (corte,)):   # 10 = quadro Reclamantes
        if str(cid) in est['vistos']:
            continue
        if not tel or ''.join(c for c in str(tel) if c.isdigit())[-8:] in clientes:
            continue
        ult = con.execute("select max(id) from wa_mensagens where conversa_id=? and origem='contato'",
                          (cid,)).fetchone()[0]
        falas = con.execute("select count(*) from wa_mensagens where conversa_id=? and "
                            "origem='contato' and length(conteudo)>3", (cid,)).fetchone()[0]
        if falas < 2:
            continue
        out.append({'id': cid, 'nome': nome or '', 'telefone': tel, 'etapa': etapa,
                    'origem': org or '', 'ultima_msg': ult or 0})
    con.close()
    out.sort(key=lambda x: -x['ultima_msg'])
    return out


def mover(cid, etapa):
    con = _con(ro=False)
    pip = (con.execute('select pipeline_id from wa_conversas where id=?', (cid,)).fetchone() or [10])[0]
    if pip == 10 and con.execute('select 1 from wa_stages where pipeline_id=? and nome=?',
                                 (pip, etapa)).fetchone():
        con.execute('update wa_conversas set etapa=?, atualizado_em=? where id=?',
                    (etapa, datetime.utcnow().isoformat(), cid))
        con.commit()
    con.close()


def ultima_troca(cid):
    """Ultima fala do lead e a nossa ultima, com horario. Contexto para a ligacao."""
    con = _con()

    def pega(origens):
        q = ('select conteudo, criado_em from wa_mensagens where conversa_id=? '
             'and origem in (%s) and length(conteudo)>1 order by id desc limit 1'
             % ','.join('?' * len(origens)))
        return con.execute(q, (cid,) + origens).fetchone()

    lead = pega(('contato',))
    nosso = pega(('ia', 'atendente'))
    con.close()

    def fmt(r):
        if not r:
            return None
        txt = ' '.join((r[0] or '').split())[:150]
        try:
            d = datetime.strptime(str(r[1]).replace('T', ' ').split('.')[0],
                                  '%Y-%m-%d %H:%M:%S') - timedelta(hours=3)
            quando = d.strftime('%d/%m as %H:%M')
        except Exception:
            quando = ''
        return txt, quando

    return fmt(lead), fmt(nosso)


def texto_alarme(c, cl):
    lead, nosso = ultima_troca(c['id'])
    L = ['🚨 *LIGAR AGORA* 🚨', '',
         f"*{c['nome'] or 'Sem nome'}*",
         f"📱 {c['telefone']}", '',
         f"✅ Violacao: {cl['caso']}"]
    saida = (cl.get('data_saida') or '').strip()
    if cl.get('saiu'):
        L.append('✅ JA SAIU da empresa' + (f': {saida}' if saida else ''))
        L.append('   prescricao bienal ja correndo')
    else:
        L.append('⚠️ AINDA NA EMPRESA, caso urgente' + (f" ({cl['tema']})" if cl.get('tema') else ''))
        L.append('   nao orientar pedido de demissao: a advogada avalia antes')
    if cl.get('direitos'):
        L.append('')
        L.append('*Direitos que ele relatou:*')
        for d in cl['direitos']:
            L.append(f'   • {d}')
    if lead:
        L.append('')
        L.append(f'*Ultima fala dele* ({lead[1]}):')
        L.append(f'   "{lead[0]}"')
    if nosso:
        L.append(f'*Nossa ultima* ({nosso[1]}):')
        L.append(f'   "{nosso[0]}"')
    L += ['', f"_{cl['porque']}_", '',
          f"Origem: {c['origem'] or 'anuncio'} · codigo #{c['id']}",
          'Card movido para Lead Quente.', '',
          '⏱️ Ligar nos proximos minutos e o que fecha esse caso.']
    return '\n'.join(L)


def _vendedora_no_horario(vend):
    """O vendedor so recebe WhatsApp da API no horario dele (dia util). Fora dele, o alarme fica so na
    tarefa fixa da tela: o lead_quente_tarefa.py le 'vendedora' e 'texto' deste estado."""
    a = ROTA.agora_br()
    h = vend.get('horas', (9, 18))
    return a.weekday() in vend.get('dias', (0, 1, 2, 3, 4)) and h[0] <= a.hour < h[1]


def rodar(enviar):
    est = _json(ESTADO, {'vistos': {}})
    cands = candidatos()
    print(f'candidatos a analisar: {len(cands)} | analisando ate {MAX_POR_RODADA}')
    alarmes = 0
    for c in cands[:MAX_POR_RODADA]:
        cl = classificar(c['id'])
        if cl is None:
            print(f"  #{c['id']} {c['nome'][:22]}: nao consegui ler, tenta depois")
            continue
        qualificado = cl['violacao'] and (cl['saiu'] or cl.get('urgente'))
        marca = 'QUALIFICADO' if qualificado else (
            'violacao, mas ainda na empresa' if cl['violacao'] else 'sem violacao clara')
        print(f"  #{c['id']} {(c['nome'] or '')[:22]:22s} -> {marca} | {cl['caso']}")
        est['vistos'][str(c['id'])] = {'em': datetime.now().isoformat(timespec='seconds'),
                                       'violacao': cl['violacao'], 'saiu': cl['saiu'],
                                       'caso': cl['caso']}
        if qualificado:
            alarmes += 1
            txt = texto_alarme(c, cl)
            if enviar:
                mover(c['id'], ETAPA_ALVO)
                vend = ROTA.quem_recebe_demitido()
                est['vistos'][str(c['id'])]['vendedora'] = vend['nome'] if vend else ''
                est['vistos'][str(c['id'])]['texto'] = txt   # vira tarefa no Sistema Escritório Modelo (lead_quente_tarefa.py)
                no_horario = bool(vend) and _vendedora_no_horario(vend) and bool(vend.get('tel'))
                if vend and not no_horario:
                    print(f"      fora do horario de {vend['nome']}: sem WhatsApp, so a tarefa fixa")
                vtel = vend['tel'] if vend else ''
                com = f"\n\n👤 Com *{vend['nome']}*." if vend else ''
                for tel in ([vtel] if no_horario else []) + [t for t in DESTINOS if t != vtel]:
                    ok, _ = zapi_enviar_texto(tel, txt + com)
                    print(f'      alarme para {tel}: ' + ('OK' if ok else 'FALHOU'))
            else:
                print('      --- ALARME QUE SAIRIA ---')
                print(txt)
    if enviar:
        _salvar(ESTADO, est)
    print(f'\nalarmes disparados: {alarmes}' + ('' if enviar else '  (simulacao)'))


if __name__ == '__main__':
    rodar('--enviar' in sys.argv)
