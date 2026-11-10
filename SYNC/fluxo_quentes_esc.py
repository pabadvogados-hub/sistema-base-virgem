# -*- coding: utf-8 -*-
"""Fluxo dos LEADS QUENTES: da Caixa de Entrada para a LIGACAO que fecha contrato.

Publico: leads que o termometro marcou como quente ou morno e que estao parados nas
colunas de ENTRADA do quadro Reclamantes (Caixa de Entrada e colunas de campanha).
O objetivo de cada toque nao e vender por escrito, e conseguir UM HORARIO
para a doutora ligar.

Qualificacao embutida (o que o time pediu):
  - toque 1 pergunta, em uma palavra, se a pessoa JA TEM ACAO OU ADVOGADO;
    quem responde que sim sai da regua e o card vai para Lead Desqualificado;
  - quem responde qualquer outra coisa: regua para na hora, card sobe para
    Lead Quente e o time recebe aviso para ligar (QUENTES_AVISO_TEL);
  - a leitura da resposta e feita pelo Claude Code instalado no servidor (ESC_CLAUDE_BIN),
    com queda para palavra-chave se o CLI nao existir ou falhar.

Travas de seguranca:
  - quem ja e CLIENTE (telefone bate em jur_clientes) nunca entra;
  - nomes na lista REVISAO_MANUAL nunca entram sozinhos (bot, parceiro, etc);
  - 1 toque por dia por pessoa, janela de 8h as 20h;
  - dry-run e o padrao: sem --enviar nao manda nada e nao grava estado.

Uso:
  fluxo_quentes_esc.py            -> simulacao, mostra o que faria
  fluxo_quentes_esc.py --enviar   -> manda de verdade
  fluxo_quentes_esc.py --status   -> como esta a fila
"""
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv(os.getenv('ESC_CONFIG_ENV', '/opt/escritorio/config/.env'))
load_dotenv('/opt/escritorio/config/.env', override=False)

import requests

DB = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
FILA = os.path.join(HERE, 'quentes_fila.json')
PIPELINE = 10
ETAPA_ORIGEM = 'Caixa de Entrada'
# colunas de entrada do quadro Reclamantes (ver SISTEMA/backend/app/whatsapp/funis.py)
ETAPAS_ENTRADA = ('Caixa de Entrada', 'Campanha Fast-food', 'Campanha Supermercado',
                  'Campanha Acidente de Trabalho', 'Campanha Geral')
ETAPA_QUENTE = 'Lead Quente'
ETAPA_DESQUALIFICADO = 'Lead Desqualificado'
FLUXO_REAQUECE = 'reaquecimento_quente'   # regua de mensagens
FLUXO_LIGACAO = 'ligacao_agendar'         # respondeu, fila da doutora
CADENCIA = (0, 1, 3, 6)          # dias a partir da entrada na regua
JANELA = (8, 20)
MAX_POR_RODADA = 15      # trava anti-bloqueio: nunca disparar em rajada
PAUSA_ENTRE_ENVIOS = 9   # segundos entre uma mensagem e a proxima
# A resposta vira tarefa do comercial (lead_quente_tarefa.py). Para tambem avisar alguem
# por WhatsApp, por o telefone em QUENTES_AVISO_TEL (vazio = nao avisa).
ADVOGADA_TEL = os.getenv('QUENTES_AVISO_TEL') or os.getenv('TEL_ALERTA_EQUIPE', '')
DEPOIMENTOS = os.getenv('DEPOIMENTOS_LINK', '')
CLAUDE_BIN = os.getenv('ESC_CLAUDE_BIN', os.path.expanduser('~/.local/bin/claude'))

# Nao entram sozinhos: bot, parceiro, indicacao interna, canal de conteudo.
REVISAO_MANUAL = tuple(x.strip().lower() for x in os.getenv(
    'QUENTES_REVISAO_MANUAL', 'blip,indicação,indicacao').split(',') if x.strip())

import wa_dapi  # noqa: E402  saida unica de WhatsApp pela D-API


# --------------------------------------------------------------- utilidades
def _json(path, padrao):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return padrao


def _salvar(path, dados):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)


def _tel8(tel):
    d = ''.join(c for c in str(tel or '') if c.isdigit())
    return d[-8:]


def _ddi(tel):
    d = ''.join(c for c in str(tel or '') if c.isdigit())
    return d if d.startswith('55') else '55' + d


def zapi_enviar_texto(telefone, texto):
    """Envia pela D-API (wa_dapi); o nome ficou para nao mexer em quem chama.
    ok so quando a D-API confirmou o envio."""
    if not _tel8(telefone):
        return False, 'sem destino'
    try:
        ok, info = wa_dapi.enviar_texto(_ddi(telefone), texto)
        return bool(ok), json.dumps(info, ensure_ascii=False)[:160]
    except Exception as e:
        return False, str(e)[:160]


def _con():
    con = sqlite3.connect(DB, timeout=20)
    con.execute('pragma busy_timeout=8000')
    return con


def _primeiro_nome(nome):
    """Nome utilizavel ou vazio. @lid e numero nao sao nome."""
    n = (nome or '').strip()
    if not n or '@lid' in n or n.replace(' ', '').isdigit():
        return ''
    n = re.sub(r'[^\wÀ-ÿ\s.]', '', n).strip()
    p = n.split()[0] if n.split() else ''
    return p if len(p) > 2 and not p.isdigit() else ''


# ------------------------------------------------------------ classificacao
_JA_TEM = ('ja entrei', 'ja dei entrada', 'ja processei', 'ja ajuizei', 'ja tenho advogado',
           'tenho advogado', 'tenho advogada', 'ja tenho processo', 'ja esta na justica',
           'ja ta na justica', 'outro escritorio', 'ja to processando', 'audiencia marcada')
_NEGA = ('nao tenho', 'nao entrei', 'ainda nao', 'nao ainda', 'nao tenho advogado', 'nunca entrei')


def _sem_acento(t):
    import unicodedata
    t = unicodedata.normalize('NFKD', t or '').encode('ascii', 'ignore').decode().lower()
    return ' '.join(t.split())


def classificar_resposta(texto):
    """Devolve 'ja_tem_acao' | 'quer_falar' | 'indefinido'.
    Usa o Claude Code do servidor; cai para palavra-chave se o CLI falhar."""
    env = dict(os.environ)
    env.pop('ANTHROPIC_API_KEY', None)         # assinatura, nunca API
    prompt = (
        'Voce le respostas de leads de um escritorio trabalhista que defende trabalhadores.\n'
        'Classifique a mensagem abaixo em uma destas opcoes:\n'
        '  ja_tem_acao  = a pessoa diz que JA entrou com acao, ja tem advogado ou '
        'ja esta sendo atendida por outro escritorio\n'
        '  quer_falar   = a pessoa aceita conversar, da horario, pergunta algo ou '
        'diz que NAO tem advogado\n'
        '  indefinido   = nao da para saber\n\n'
        'Atencao a negacao: "nao tenho advogado" e quer_falar, nao ja_tem_acao.\n\n'
        f'MENSAGEM: """{texto[:900]}"""\n\n'
        'Responda APENAS o JSON: {"tipo": "..."}')
    try:
        r = subprocess.run([CLAUDE_BIN, '-p', '--model', 'sonnet', '--tools', '',
                            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}'],
                           input=prompt.encode('utf-8'), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env,
                           cwd=tempfile.gettempdir(), timeout=120)
        if r.returncode == 0:
            m = re.search(r'\{.*\}', r.stdout.decode('utf-8', 'replace'), re.S)
            if m:
                t = json.loads(m.group(0)).get('tipo')
                if t in ('ja_tem_acao', 'quer_falar', 'indefinido'):
                    return t
    except Exception as e:
        print(f'    (CLI falhou, usando palavra-chave: {str(e)[:90]})')
    t = _sem_acento(texto)
    if any(n in t for n in _NEGA):
        return 'quer_falar'
    if any(s in t for s in _JA_TEM):
        return 'ja_tem_acao'
    return 'indefinido'


# ----------------------------------------------------------------- consultas
def candidatos():
    """Quentes e mornos parados nas colunas de entrada, ja filtrados."""
    con = _con()
    clientes = set()
    for tel, cel in con.execute('select telefone, celular from jur_clientes'):
        for t in (tel, cel):
            if t:
                clientes.add(_tel8(t))
    rows = con.execute(
        "select t.conversa_id, t.temperatura, t.situacao, t.violacoes, "
        "       v.contato_nome, v.contato_telefone "
        "  from lead_termometro t join wa_conversas v on v.id = t.conversa_id "
        " where t.temperatura in ('quente','morno') and v.pipeline_id=? and v.etapa in (%s)"
        % ','.join('?' * len(ETAPAS_ENTRADA)),
        (PIPELINE,) + ETAPAS_ENTRADA).fetchall()
    con.close()

    entram, barrados = [], []
    for cid, temp, sit, viol, nome, tel in rows:
        n = (nome or '').lower()
        if _tel8(tel) in clientes:
            barrados.append((cid, nome, 'JA E CLIENTE'))
            continue
        if any(s in n for s in REVISAO_MANUAL):
            barrados.append((cid, nome, 'revisao manual (nome suspeito)'))
            continue
        try:
            vs = json.loads(viol) if viol else []
        except Exception:
            vs = []
        entram.append({'conversa_id': cid, 'temperatura': temp, 'situacao': sit or '',
                       'violacao': (vs[0] if vs else ''), 'nome': nome or '', 'telefone': tel})
    entram.sort(key=lambda x: (x['temperatura'] != 'quente', x['conversa_id']))
    return entram, barrados



def registrar_fluxo(conversa_id, tipo, ativo=1, encerra=None):
    """Espelha a regua em wa_fluxos para o CRM enxergar em que fluxo o lead esta.
    encerra: nome de um tipo que deve ser fechado antes de abrir o novo."""
    con = _con()
    if encerra:
        con.execute("update wa_fluxos set ativo=0, concluido_em=? "
                    "where conversa_id=? and tipo=? and ativo=1",
                    (datetime.utcnow().isoformat(), conversa_id, encerra))
    if tipo:
        ja = con.execute("select 1 from wa_fluxos where conversa_id=? and tipo=? and ativo=1",
                         (conversa_id, tipo)).fetchone()
        if not ja:
            con.execute("insert into wa_fluxos (conversa_id, tipo, ativo, iniciado_em, "
                        "etapas_enviadas, criado_por) values (?,?,?,?,?,?)",
                        (conversa_id, tipo, ativo, datetime.utcnow().isoformat(), 0,
                         'auto:quentes'))
    con.commit()
    con.close()


def _respondeu_depois(conversa_id, desde_iso):
    con = _con()
    r = con.execute(
        "select conteudo from wa_mensagens where conversa_id=? and origem='contato' "
        "and length(conteudo)>1 and replace(criado_em,'T',' ') > ? order by id desc limit 1",
        (conversa_id, desde_iso.replace('T', ' '))).fetchone()
    con.close()
    return r[0] if r else None


def mover_card(conversa_id, etapa):
    con = _con()
    existe = con.execute('select 1 from wa_stages where pipeline_id=? and nome=?',
                         (PIPELINE, etapa)).fetchone()
    if not existe:
        con.close()
        print(f'    (etapa "{etapa}" nao existe, card nao movido)')
        return False
    con.execute('update wa_conversas set etapa=?, atualizado_em=? where id=?',
                (etapa, datetime.utcnow().isoformat(), conversa_id))
    con.commit()
    con.close()
    return True


# -------------------------------------------------------------------- textos
def texto_toque(i, c):
    nome = _primeiro_nome(c['nome'])
    ola = f'Olá, {nome}!' if nome else 'Olá!'
    viol = c.get('violacao') or ''
    sobre = f'sobre {viol}' if viol else 'sobre o que a empresa fez com você'

    if i == 0:
        return (
            f'{ola} Aqui é do Escritório Modelo Advogados, escritório trabalhista que defende o trabalhador.\n\n'
            f'Você conversou com a gente {sobre} e o seu caso ficou marcado aqui como '
            'prioridade. Não sumi não, voltei justamente porque o que você contou não '
            'pode ficar parado.\n\n'
            'Preciso de duas respostas rápidas, pode ser em uma palavra:\n\n'
            '1) Você já entrou com alguma ação ou já tem advogado cuidando disso? '
            'Responda SIM ou NÃO.\n'
            '2) Qual o melhor horário para a doutora te ligar, MANHÃ ou TARDE?\n\n'
            'É uma ligação rápida e sem compromisso, e é ela quem vai te explicar o que '
            'cabe no seu caso. Direito trabalhista tem prazo, e prazo que passa não volta.')
    if i == 1:
        return (
            f'{ola} Passando aqui de novo, rapidinho.\n\n'
            'Você não chegou a me dizer o horário. Me manda só MANHÃ ou TARDE que eu já '
            'deixo você na agenda da doutora.\n\n'
            'E se você já resolveu isso por outro caminho, me diz também. Aí eu encerro '
            'seu atendimento e paro de te chamar.')
    if i == 2:
        depo = (f'Dá uma olhada no depoimento de quem já passou por isso:\n{DEPOIMENTOS}\n\n'
                if DEPOIMENTOS else '')
        return (
            f'{ola} Muita gente acha que buscar os direitos não dá em nada e deixa passar. '
            'Depois vê o colega de trabalho receber e se arrepende.\n\n'
            + depo +
            'Se quiser falar com a doutora, é só me responder MANHÃ ou TARDE.')
    return (
        f'{ola} Essa é a última vez que te chamo, para não ficar enchendo seu WhatsApp.\n\n'
        'Seu caso continua registrado aqui com a gente. Se um dia quiser retomar, é só '
        'responder essa mensagem, a qualquer hora do dia ou da noite.\n\n'
        'Um abraço e se cuida.')


# --------------------------------------------------------------------- motor
def rodar(enviar=False):
    agora = datetime.now()
    if enviar and not (JANELA[0] <= agora.hour < JANELA[1]):
        print(f'fora da janela {JANELA[0]}h-{JANELA[1]}h, nada a fazer')
        return
    fila = _json(FILA, {})
    entram, barrados = candidatos()

    print(f'== FLUXO QUENTES == {agora:%d/%m %H:%M}  '
          f'{"ENVIANDO" if enviar else "SIMULACAO (use --enviar)"}')
    print(f'   candidatos: {len(entram)} | barrados: {len(barrados)}')
    for cid, nome, motivo in barrados:
        print(f'   [FORA] id {cid} {nome}: {motivo}')

    # 1) inscreve quem ainda nao esta na fila
    for c in entram:
        k = str(c['conversa_id'])
        if k not in fila:
            fila[k] = {'conversa_id': c['conversa_id'], 'nome': c['nome'],
                       'telefone': c['telefone'], 'temperatura': c['temperatura'],
                       'situacao': c['situacao'], 'violacao': c['violacao'],
                       'entrou_em': agora.isoformat(timespec='seconds'),
                       'toques': [], 'encerrado': False, 'motivo': ''}
            print(f"   [NOVO] {c['nome'] or c['telefone']} ({c['temperatura']})")
            if enviar:
                registrar_fluxo(c['conversa_id'], FLUXO_REAQUECE)

    # 2) VARREDURA OBRIGATORIA antes de qualquer toque:
    #    ninguem recebe a proxima mensagem sem antes conferir se ja respondeu.
    #    Vale tambem para quem ainda nao recebeu toque nenhum.
    for k, c in fila.items():
        if c['encerrado']:
            continue
        desde = c['toques'][-1]['em'] if c['toques'] else c['entrou_em']
        resp = _respondeu_depois(c['conversa_id'], desde)
        if not resp:
            continue
        tipo = classificar_resposta(resp)
        c['encerrado'] = True
        c['resposta'] = resp[:300]
        c['classificacao'] = tipo
        c['respondeu_em'] = datetime.now().isoformat(timespec='seconds')
        c['no_relatorio'] = True          # aparece no relatorio diario das 7h45
        if tipo == 'ja_tem_acao':
            c['motivo'] = 'ja tem acao ou advogado'
            print(f"   [DESCARTA] {c['nome']}: {resp[:70]}")
            if enviar:
                registrar_fluxo(c['conversa_id'], None, encerra=FLUXO_REAQUECE)
                mover_card(c['conversa_id'], ETAPA_DESQUALIFICADO)
                zapi_enviar_texto(ADVOGADA_TEL,
                                  f"Lead {c['nome'] or c['telefone']} respondeu que JA TEM "
                                  f"acao/advogado. Card movido para Lead Desqualificado.\n\n"
                                  f'Resposta: "{resp[:200]}"')
        else:
            c['motivo'] = 'respondeu, ligar'
            print(f"   [LIGAR] {c['nome']}: {resp[:70]}")
            if enviar:
                # TROCA DE FLUXO: sai do reaquecimento, entra na fila de ligacao
                registrar_fluxo(c['conversa_id'], FLUXO_LIGACAO, encerra=FLUXO_REAQUECE)
                mover_card(c['conversa_id'], ETAPA_QUENTE)
                zapi_enviar_texto(
                    ADVOGADA_TEL,
                    f"*LIGAR HOJE* {c['nome'] or ''} {c['telefone']}\n"
                    f"Termometro: {c['temperatura']} | {c['situacao']}\n"
                    f"Caso: {c['violacao'] or 'ver conversa'}\n\n"
                    f'Ele respondeu: "{resp[:200]}"\n\n'
                    'Card movido para Lead Quente.')

    # 3) toques da cadencia
    hoje = date.today()
    enviados = 0
    for k, c in fila.items():
        if c['encerrado']:
            continue
        i = len(c['toques'])
        if i >= len(CADENCIA):
            c['encerrado'] = True
            c['motivo'] = 'cadencia terminada sem resposta'
            continue
        entrou = datetime.fromisoformat(c['entrou_em']).date()
        if (hoje - entrou).days < CADENCIA[i]:
            continue
        if c['toques'] and datetime.fromisoformat(c['toques'][-1]['em']).date() == hoje:
            continue                      # no maximo 1 toque por dia
        texto = texto_toque(i, c)
        if not enviar:
            print(f"\n   --- SIMULA toque {i+1}/4 para {c['nome'] or c['telefone']} "
                  f"({c['temperatura']}) ---\n{texto}\n")
            continue
        if enviados >= MAX_POR_RODADA:
            print(f'   (trava de {MAX_POR_RODADA} envios por rodada atingida, '
                  'o resto sai na proxima rodada)')
            break
        if enviados:
            time.sleep(PAUSA_ENTRE_ENVIOS)
        ok, resp = zapi_enviar_texto(c['telefone'], texto)
        print(f"   [T{i+1}] {c['nome'] or c['telefone']}: {'OK' if ok else 'FALHOU ' + resp}")
        if ok:
            c['toques'].append({'i': i, 'em': datetime.now().isoformat(timespec='seconds')})
            enviados += 1

    if enviar:
        _salvar(FILA, fila)
        print(f'\n   toques enviados: {enviados} | fila: {len(fila)}')
    else:
        print('\n   (simulacao, nada foi enviado nem gravado)')


def status():
    fila = _json(FILA, {})
    if not fila:
        print('fila vazia')
        return
    ativos = [c for c in fila.values() if not c['encerrado']]
    print(f'fila: {len(fila)} | ativos: {len(ativos)}')
    for c in fila.values():
        flag = f"ENCERRADO: {c['motivo']}" if c['encerrado'] else f"toque {len(c['toques'])}/4"
        print(f"  {c['temperatura']:6s} {(c['nome'] or c['telefone'])[:26]:26s} {flag}")


if __name__ == '__main__':
    if '--status' in sys.argv:
        status()
    else:
        rodar(enviar='--enviar' in sys.argv)
