"""Audiencias do Sistema Escritório Modelo (28/09/2026): o registro unico e a ponte com a agenda central.

`audiencia_registro` e a lista de audiencias do escritorio, uma linha por
(processo, data). Quem escreve:
  - audiencias_vigia.py (6h50): junta ADVBOX, tribunal (SYNC), intimacoes,
    agendas, tarefas e o que foi lancado na tela, e poe na agenda central o
    que ainda nao esta la;
  - a tela Agenda > Audiencias: lanca, corrige, redesigna, marca realizada.
Quem le: lembretes_compromisso.py (lembrete ao cliente, chave processo+data),
o relatorio de sexta da Advogada e a tela.

Linha lancada ou corrigida na tela fica com editado_local=1: o vigia nao
sobrescreve hora, modalidade, local nem tipo, e nunca a da como substituida.

Evento na agenda: `evento_nosso=1` quando foi o sistema que criou (marcador
aud:<processo>:<data>). So evento nosso e movido ou cancelado sozinho; o que a
equipe cadastrou a mao e so ligado.
"""
import datetime as dt
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

DB = str(Path(__file__).resolve().parents[1] / 'sistema_esc.db')
CNJ = re.compile(r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}')
SUBTIPOS = {'inicial': 'INICIAL', 'una': 'UNA', 'instrucao': 'DE INSTRUÇÃO',
            'conciliacao': 'DE CONCILIAÇÃO', 'audiencia': ''}
MODALIDADES = {'virtual': 'virtual', 'presencial': 'presencial', 'hibrida': 'híbrida'}
STATUS = ('ativa', 'realizada', 'cancelada', 'redesignada', 'substituida', 'nao_vista')
COLS = {'evento_id': 'TEXT', 'evento_link': 'TEXT', 'evento_nosso': 'INTEGER DEFAULT 0',
        'origem': 'TEXT', 'editado_local': 'INTEGER DEFAULT 0', 'obs': 'TEXT',
        'resultado': 'TEXT', 'criado_por': 'TEXT', 'atualizado_por': 'TEXT',
        'atualizado_em': 'TEXT'}
# quem precisa ver a audiencia na propria agenda: os convidados de sempre do
# PF (Advogada e as caixas do operacional), a Advogada, que prepara, e a Advogada,
# que confere na controladoria
from .juridico.contatos import CFG as _CFG
EXTRA_CONVIDADOS = tuple(e for e in (_CFG.REVISORA_EMAIL,) if e)


def norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', s).upper().strip()


def agora_txt():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def con(db=None):
    c = sqlite3.connect(db or DB, timeout=30)
    c.row_factory = sqlite3.Row
    garantir(c)
    return c


def garantir(c):
    c.execute("""CREATE TABLE IF NOT EXISTS audiencia_registro (
                   processo TEXT NOT NULL, data TEXT NOT NULL, hora TEXT,
                   subtipo TEXT, modalidade TEXT, local TEXT, cliente TEXT,
                   adversa TEXT, lawsuit_id INTEGER, fontes TEXT,
                   divergencia TEXT, status TEXT, visto_em TEXT,
                   PRIMARY KEY (processo, data))""")
    c.execute("""CREATE TABLE IF NOT EXISTS audiencia_providencia (
                   processo TEXT NOT NULL, data TEXT NOT NULL, advbox TEXT,
                   alertas TEXT, criado_em TEXT, PRIMARY KEY (processo, data))""")
    tem = {r[1] for r in c.execute('PRAGMA table_info(audiencia_registro)')}
    for col, tipo in COLS.items():
        if col not in tem:
            c.execute('ALTER TABLE audiencia_registro ADD COLUMN %s %s' % (col, tipo))


def limpar_nome(nome):
    """'EMPRESA EXEMPLO LTDA_T_G' -> 'EMPRESA EXEMPLO LTDA'.
    O sufixo e a etiqueta de origem que o ADVBOX pendura no nome."""
    return re.sub(r'(_[A-Z]{1,3})+$', '', str(nome or '').strip()).strip()


def partes(c, numero):
    """(cliente, adversa, lawsuit_id) pelo cadastro do sistema."""
    p = c.execute('SELECT id, advbox_id FROM jur_processos WHERE numero=?', (numero,)).fetchone()
    cli, adv, lid = [], [], None
    if p:
        lid = p['advbox_id'] or None
        for r in c.execute('SELECT nome, papel FROM jur_processo_partes WHERE processo_id=?', (p['id'],)):
            (adv if norm(r['papel']).startswith('PARTE CONTR') else cli).append(limpar_nome(r['nome']))
    if not cli:
        try:
            t = c.execute('SELECT cliente, parte_adversa FROM advbox_titularidade WHERE numero=?',
                          (numero,)).fetchone()
            if t:
                cli, adv = [limpar_nome(t['cliente'])], [limpar_nome(t['parte_adversa'])]
        except sqlite3.OperationalError:
            pass
    return ' / '.join(x for x in cli if x), ' / '.join(x for x in adv if x), lid


def rotulo(a):
    sub = SUBTIPOS.get(a.get('subtipo') or 'audiencia', '')
    return ('AUDIÊNCIA ' + sub).strip()


def titulo(a):
    """Padrao da agenda do escritorio: AUDIENCIA <TIPO>_<CLIENTE> X <ADVERSA>_<PROCESSO>.
    O lembrete ao cliente le a palavra AUDIENCIA, o CNJ e o que vem depois do " X "
    ("Processo contra ..."). Por isso, sem parte adversa no cadastro, o " X " nao
    entra: "X PARTE ADVERSA" viraria "Processo contra Parte Adversa" na mensagem."""
    cli = limpar_nome(a.get('cliente')) or 'CLIENTE'
    advs = [limpar_nome(x) for x in str(a.get('adversa') or '').split(' / ') if limpar_nome(x)]
    partes_txt = cli.upper()
    if advs:
        partes_txt += ' X ' + advs[0].upper() + (' E OUTROS' if len(advs) > 1 else '')
    return '%s_%s_%s' % (rotulo(a), partes_txt, a['processo'])


def descricao(a, por):
    L = ['%s%s' % (rotulo(a).capitalize(),
                   (' (%s)' % MODALIDADES.get(a.get('modalidade'), a.get('modalidade')))
                   if a.get('modalidade') else ''),
         'Processo %s' % a['processo']]
    if a.get('local'):
        L.append('Local / link: %s' % a['local'])
    if a.get('fontes'):
        L.append('De onde veio: %s' % str(a['fontes']).replace(',', ', '))
    if a.get('obs'):
        L += ['', str(a['obs'])]
    L += ['', 'Cadastrada pelo Sistema Escritório Modelo (%s) em %s. Mudou a data? Corrija em Agenda > Audiências '
              'no sistema, que a agenda acompanha.' % (por, time.strftime('%d/%m/%Y'))]
    return '\n'.join(L)


def convidados():
    from . import agenda_google as AG
    lista = list(AG.CONVIDADOS_PF)
    for e in EXTRA_CONVIDADOS:
        if e not in lista:
            lista.append(e)
    return lista


def marcador(a):
    return 'aud:%s:%s' % (a['processo'], a['data'])


def achar_evento(a, eventos):
    """Evento da agenda central que ja e esta audiencia: mesmo dia e o numero do
    processo (no marcador, na propriedade ou no titulo), ou o nome do cliente
    junto com a palavra audiencia."""
    num = re.sub(r'\D', '', a['processo'])
    cli = [w for w in re.findall(r'[A-Z]{3,}', norm(limpar_nome(a.get('cliente'))))
           if w not in ('LTDA', 'EIRELI', 'COMERCIO', 'TRANSPORTES', 'TRANSPORTE')]
    for e in eventos:
        if e.get('data') != a['data']:
            continue
        if e.get('marcador') == marcador(a) or e.get('processo') == a['processo']:
            return e
        tit = norm(e.get('titulo'))
        if num and num in re.sub(r'\D', '', tit):
            return e
        if len(cli) >= 2 and cli[0] in tit and cli[-1] in tit and 'AUDI' in tit:
            return e
    return None


def ligar(c, a, ev, nosso):
    c.execute('UPDATE audiencia_registro SET evento_id=?, evento_link=?, evento_nosso=? '
              'WHERE processo=? AND data=?',
              (ev.get('evento_id') or ev.get('id'), ev.get('link') or ev.get('htmlLink'),
               1 if nosso else 0, a['processo'], a['data']))


def agendar(c, a, eventos=None, por='vigia'):
    """Poe a audiencia na agenda central, ou liga ao evento que ja existe.
    Devolve ('ja_ligada' | 'ligada' | 'criada', evento_id)."""
    from . import agenda_google as AG
    if a.get('evento_id'):
        return 'ja_ligada', a['evento_id']
    if eventos is None:
        eventos = AG.listar(dias_atras=2, dias_frente=500)
    ev = achar_evento(a, eventos)
    if ev:
        ligar(c, a, ev, nosso=(ev.get('marcador') == marcador(a)))
        return 'ligada', ev.get('evento_id')
    ja = AG.achar_por_marcador(marcador(a))       # duplo clique / rodada repetida
    if ja:
        ligar(c, a, ja, nosso=True)
        return 'ligada', ja.get('id')
    novo = AG.criar_audiencia(titulo(a), a['data'], (a.get('hora') or None), 90,
                              descricao(a, por), convidados(), None, marcador(a),
                              a.get('local') or '', a['processo'])
    ligar(c, a, novo, nosso=True)
    return 'criada', novo.get('id')


def mover_evento(evento_id, a, por, nosso):
    """Leva o evento para a data/hora/local atuais da audiencia (aviso aos convidados).
    Evento que a equipe cadastrou a mao: so data, hora e local; titulo e texto sao dela."""
    from . import agenda_google as AG
    return AG.atualizar(evento_id, titulo=titulo(a) if nosso else None, data=a['data'],
                        hora=(a.get('hora') or None),
                        descricao=descricao(a, por) if nosso else None,
                        local=(a.get('local') or None))
