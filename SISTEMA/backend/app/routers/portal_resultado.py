import os
""
import asyncio
import datetime as _dt
import re
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..database import Usuario
from ..deps import usuario_atual

router = APIRouter(prefix='/api/portal-resultado', tags=['portal-resultado'])

DB = str(Path(__file__).resolve().parents[2] / 'sistema_esc.db')
QUEM_VE = {e.strip().lower() for e in os.getenv('PORTAL_RESULTADO_EMAILS', '').split(',') if e.strip()}
CASA_ID = 523
IPS_INTERNOS = {'127.0.0.1'} | {i.strip() for i in os.getenv('IP_DA_VPS', '').split(',') if i.strip()}
INICIO = _dt.datetime(2026, 9, 1)
RODADA_DESDE = _dt.datetime(2026, 9, 16)
METAS = {'entraram': 60, 'voltaram': 40, 'nota': 4.5, 'travados': 0}

ENTROU = ('login_ok', 'abriu_processo', 'abriu_painel')
USO = [
    ('Abriu o processo', 'abriu_processo'),
    ('Acessar seu processo', 'abriu_autos'),
    ('Leu um documento', 'leu_documento'),
    ('Baixou um documento', 'baixou_documento'),
    ('Ouviu a explicação', 'ouviu'),
    ('Perguntou à Cláudia', 'duvida'),
    ('Perguntou falando', 'falou_duvida'),
    ('Painel da empresa', 'abriu_painel'),
    ('Boletos', 'abriu_financeiro'),
]
TEMAS = [
    ('Valor e quando recebe', r'valor|receb|pag|dinheiro|quanto'),
    ('Resultado e sentença', r'senten|resultado|ganh|perd'),
    ('Audiência e perícia', r'audi[eê]ncia|per[ií]cia'),
    ('Como está o caso', r'resumo|situa|fase|andamento|como est'),
]
AJUSTES_INICIAIS = [
    ('2026-09-02', 'Piloto', '7 convites, começando pela família'),
    ('2026-09-11', 'Convite para todos', '32 convites no dia'),
    ('2026-09-15', 'Tela simples e voz', 'Entenda o seu caso, Ouvir e Cláudia antes'),
    ('2026-09-16', 'Portal novo e vídeo', 'Pessoa física e empresa. 41 vídeos às 19h49'),
]

DDL_AJUSTE = """CREATE TABLE IF NOT EXISTS portal_ajuste (
    id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT, titulo TEXT, detalhe TEXT,
    autor TEXT, criado_em REAL)"""


async def quem_pode(user: Usuario = Depends(usuario_atual)) -> Usuario:
    if (user.email or '').lower().strip() not in QUEM_VE:
        raise HTTPException(403, 'Modulo restrito')
    return user


def _ler():
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _dia(t):
    return _dt.datetime.fromtimestamp(t).date()


def _nome_limpo(nome):
    try:
        from ..portal import processo_info
        return processo_info.nome_limpo(nome)
    except Exception:
        return re.sub(r'_T_G$', '', nome or '').strip()


def _garantir_ajustes():
    con = sqlite3.connect(DB, timeout=10)
    try:
        con.execute(DDL_AJUSTE)
        if not con.execute('SELECT COUNT(*) FROM portal_ajuste').fetchone()[0]:
            con.executemany('INSERT INTO portal_ajuste(data,titulo,detalhe,autor,criado_em) VALUES(?,?,?,?,?)',
                            [(d, t, x, 'Sistema', time.time()) for d, t, x in AJUSTES_INICIAIS])
        con.commit()
    finally:
        con.close()


def _visao(periodo: str, tipo: str):
    agora = time.time()
    hoje = _dt.date.today()
    if periodo == '7':
        desde_dia = hoje - _dt.timedelta(days=6)
    elif periodo == '30':
        desde_dia = hoje - _dt.timedelta(days=29)
    else:
        desde_dia = INICIO.date()
    desde = _dt.datetime.combine(desde_dia, _dt.time()).timestamp()

    con = _ler()
    try:
        clientes = {}
        for r in con.execute('SELECT id, nome, documento FROM jur_clientes'):
            doc = re.sub(r'\D', '', r['documento'] or '')
            clientes[r['id']] = {'id': r['id'], 'nome': _nome_limpo(r['nome'] or ''),
                                 'tipo': 'pj' if len(doc) == 14 else 'pf'}

        def serve(cid):
            if not cid or cid == CASA_ID or cid not in clientes:
                return False
            return tipo == 'todos' or clientes[cid]['tipo'] == tipo

        ips_casa = {r[0] for r in con.execute('SELECT DISTINCT ip FROM portal_log WHERE cliente_id=?', (CASA_ID,))}
        ips_casa |= IPS_INTERNOS
        ips_casa.discard(None)
        log = [dict(r) for r in con.execute('SELECT cpf, cliente_id, acao, detalhe, ip, criado_em FROM portal_log '
                                            'ORDER BY criado_em')]
        log = [r for r in log if r['ip'] not in ips_casa and serve(r['cliente_id'])]

        def ficha(cid):
            return clientes[cid]

        def lista(ids):
            return sorted((ficha(c) for c in ids if c in clientes), key=lambda x: x['nome'])

        convidados = {r[0] for r in con.execute('SELECT DISTINCT cliente_id FROM portal_convite WHERE ok=1')
                      if serve(r[0])}
        video = {r[0] for r in con.execute('SELECT DISTINCT cliente_id FROM portal_campanha WHERE ok=1')
                 if serve(r[0])}
        pediu = {r['cliente_id'] for r in log if r['acao'] == 'codigo_enviado'}
        dias_uso, ultimo = {}, {}
        for r in log:
            if r['acao'] in ENTROU:
                dias_uso.setdefault(r['cliente_id'], set()).add(_dia(r['criado_em']))
                ultimo[r['cliente_id']] = max(ultimo.get(r['cliente_id'], 0), r['criado_em'])
        entrou = set(dias_uso)
        voltou = {c for c, d in dias_uso.items() if len(d) >= 2}
        no_periodo = {r['cliente_id'] for r in log if r['acao'] in ENTROU and r['criado_em'] >= desde}

        pedidos, entradas = {}, {}
        for r in log:
            if r['acao'] == 'codigo_enviado' and r['criado_em'] >= agora - 30 * 86400:
                p = pedidos.setdefault(r['cliente_id'], {'vezes': 0, 'ultimo': 0})
                p['vezes'] += 1
                p['ultimo'] = max(p['ultimo'], r['criado_em'])
            if r['acao'] == 'login_ok':
                entradas[r['cliente_id']] = max(entradas.get(r['cliente_id'], 0), r['criado_em'])
        travados = []
        for cid, p in pedidos.items():
            if entradas.get(cid, 0) < p['ultimo']:
                travados.append(dict(ficha(cid), vezes=p['vezes'],
                                     quando=_dt.datetime.fromtimestamp(p['ultimo']).strftime('%d/%m %Hh%M')))
        travados.sort(key=lambda x: x['quando'], reverse=True)

        por_dia = {}
        for r in log:
            if r['acao'] in ENTROU and r['criado_em'] >= desde:
                por_dia.setdefault(str(_dia(r['criado_em'])), set()).add(r['cliente_id'])
        serie, d = [], desde_dia
        while d <= hoje:
            ids = por_dia.get(str(d), set())
            serie.append({'data': str(d), 'total': len(ids), 'clientes': lista(ids)})
            d += _dt.timedelta(days=1)

        uso = []
        for rotulo, acao in USO:
            if tipo == 'pf' and acao in ('abriu_painel', 'abriu_financeiro'):
                continue
            ids = {r['cliente_id'] for r in log if r['acao'] == acao and r['criado_em'] >= desde}
            uso.append({'rotulo': rotulo, 'acao': acao, 'total': len(ids), 'clientes': lista(ids)})

        corte = RODADA_DESDE.timestamp()
        nps = [dict(r) for r in con.execute('SELECT * FROM portal_nps ORDER BY criado_em') if serve(r['cliente_id'])]

        def rodada(itens):
            resp = [n for n in itens if not n['dispensou']]
            medias = {}
            for k in ('facil', 'gostou', 'faz_sentido'):
                vals = [n[k] for n in resp if n[k]]
                medias[k] = round(sum(vals) / len(vals), 1) if vals else None
            todas = [n[k] for n in resp for k in ('facil', 'gostou', 'faz_sentido') if n[k]]
            return {'respostas': len(resp), 'dispensaram': len(itens) - len(resp), 'medias': medias,
                    'geral': round(sum(todas) / len(todas), 1) if todas else None,
                    'lista': [dict(ficha(n['cliente_id']), facil=n['facil'], gostou=n['gostou'],
                                   faz_sentido=n['faz_sentido'], comentario=(n['comentario'] or '').strip(),
                                   data=_dt.datetime.fromtimestamp(n['criado_em']).strftime('%d/%m'))
                              for n in reversed(resp)]}
        atual = rodada([n for n in nps if n['criado_em'] >= corte])
        anterior = rodada([n for n in nps if n['criado_em'] < corte])
        ultima_nota = {}
        for n in nps:
            if not n['dispensou']:
                ultima_nota[n['cliente_id']] = n
        nota_baixa = [dict(ficha(c), menor=min(v for v in (n['facil'], n['gostou'], n['faz_sentido']) if v),
                           comentario=(n['comentario'] or '').strip())
                      for c, n in ultima_nota.items()
                      if any(v for v in (n['facil'], n['gostou'], n['faz_sentido']))
                      and min(v for v in (n['facil'], n['gostou'], n['faz_sentido']) if v) <= 3]

        duvidas = [dict(r) for r in con.execute('SELECT cliente_id, numero, pergunta, sem_resposta, criado_em '
                                                'FROM portal_duvida WHERE criado_em>=? ORDER BY criado_em DESC',
                                                (desde,)) if serve(r['cliente_id'])]
        temas = []
        for rotulo, padrao in TEMAS:
            n = sum(1 for x in duvidas if re.search(padrao, (x['pergunta'] or '').lower()))
            if n:
                temas.append({'rotulo': rotulo, 'total': n})
        temas.sort(key=lambda x: -x['total'])
        sem_resposta = [x for x in duvidas if x['sem_resposta']]

        barrados = {r['cliente_id'] for r in log
                    if r['acao'] == 'barrado_inadimplente' and r['criado_em'] >= agora - 7 * 86400}
        try:
            economia = [dict(r) for r in con.execute(
                'SELECT numero, empresa, parte, motivo, visto_em FROM portal_economia_pendencia '
                'WHERE legado=0 AND dispensado IS NULL AND resolvido_em IS NULL')] if tipo != 'pf' else []
        except sqlite3.OperationalError:
            economia = []

        convite_em = {}
        for r in con.execute('SELECT cliente_id, MIN(enviado_em) t FROM portal_convite WHERE ok=1 GROUP BY cliente_id'):
            convite_em[r['cliente_id']] = r['t']
        usou = {}
        for r in log:
            for rotulo, acao in USO:
                if r['acao'] == acao:
                    usou.setdefault(r['cliente_id'], set()).add(rotulo)
        tabela = []
        for cid in (convidados | entrou):
            n = ultima_nota.get(cid)
            if cid in voltou:
                situacao = 'voltou'
            elif cid in entrou:
                situacao = 'entrou'
            elif cid in {t['id'] for t in travados}:
                situacao = 'travou'
            elif cid in pediu:
                situacao = 'pediu código'
            else:
                situacao = 'não pediu código'
            tabela.append(dict(
                ficha(cid), situacao=situacao,
                convidado=_dt.datetime.fromtimestamp(convite_em[cid]).strftime('%d/%m') if cid in convite_em else '',
                video=cid in video, dias=len(dias_uso.get(cid, ())),
                ultimo=_dt.datetime.fromtimestamp(ultimo[cid]).strftime('%d/%m %Hh%M') if cid in ultimo else '',
                ultimo_ts=ultimo.get(cid, 0), usou=sorted(usou.get(cid, ())),
                nota=(round(sum(v for v in (n['facil'], n['gostou'], n['faz_sentido']) if v)
                            / max(1, sum(1 for v in (n['facil'], n['gostou'], n['faz_sentido']) if v)), 1)
                      if n else None)))
        tabela.sort(key=lambda x: (-x['ultimo_ts'], x['nome']))

        ajustes = [dict(r) for r in con.execute('SELECT id, data, titulo, detalhe, autor FROM portal_ajuste '
                                                'ORDER BY data, id')] if con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='portal_ajuste'").fetchone() else []
    finally:
        con.close()

    pct = lambda a, b: round(a * 100 / b) if b else 0
    conv_entrou = convidados & entrou
    nota_rodada = atual if atual['respostas'] else anterior
    return {
        'gerado_em': _dt.datetime.now().strftime('%d/%m/%Y %Hh%M'),
        'periodo': periodo, 'tipo': tipo, 'desde': str(desde_dia), 'metas': METAS,
        'kpis': {
            'entraram': {'pct': pct(len(conv_entrou), len(convidados)), 'n': len(conv_entrou),
                         'de': len(convidados), 'no_periodo': len(no_periodo)},
            'voltaram': {'pct': pct(len(voltou), len(entrou)), 'n': len(voltou), 'de': len(entrou)},
            'nota': {'valor': nota_rodada['geral'], 'respostas': nota_rodada['respostas'],
                     'rodada': 'nova' if atual['respostas'] else 'anterior',
                     'respostas_rodada_nova': atual['respostas']},
            'travados': {'n': len(travados)},
        },
        'serie': serie,
        'funil': [
            {'rotulo': 'Convidados', 'total': len(convidados), 'clientes': lista(convidados)},
            {'rotulo': 'Receberam o vídeo', 'total': len(video & convidados), 'clientes': lista(video & convidados)},
            {'rotulo': 'Pediram o código', 'total': len(pediu & convidados), 'clientes': lista(pediu & convidados)},
            {'rotulo': 'Entraram', 'total': len(conv_entrou), 'clientes': lista(conv_entrou)},
            {'rotulo': 'Voltaram outro dia', 'total': len(voltou & convidados), 'clientes': lista(voltou & convidados)},
        ],
        'nunca_pediram': lista(convidados - pediu - entrou),
        'uso': uso,
        'base_uso': len(no_periodo),
        'notas': {'nova': atual, 'anterior': anterior},
        'claudia': {'total': len(duvidas), 'clientes': len({x['cliente_id'] for x in duvidas}),
                    'sem_resposta': len(sem_resposta), 'temas': temas,
                    'recentes': [dict(ficha(x['cliente_id']), pergunta=(x['pergunta'] or '').strip(),
                                      sem_resposta=bool(x['sem_resposta']),
                                      data=_dt.datetime.fromtimestamp(x['criado_em']).strftime('%d/%m'))
                                 for x in duvidas[:15]]},
        'precisa': [
            {'chave': 'travados', 'rotulo': 'Pediram o código e não entraram',
             'como': 'Ligar e entrar junto com o cliente', 'grau': 'alto', 'total': len(travados),
             'clientes': travados},
            {'chave': 'nota_baixa', 'rotulo': 'Clientes com nota 3 ou menos',
             'como': 'Entender o que ficou difícil e acompanhar a nota nova', 'grau': 'medio',
             'total': len(nota_baixa), 'clientes': nota_baixa},
            {'chave': 'barrados', 'rotulo': 'Barrados por inadimplência nos últimos 7 dias',
             'como': 'Tentaram ver o processo e caíram na tarja do financeiro', 'grau': 'medio',
             'total': len(barrados), 'clientes': lista(barrados)},
            {'chave': 'sem_resposta', 'rotulo': 'Perguntas que a Cláudia não soube responder',
             'como': 'Quando aparecer, é assunto que falta na tela', 'grau': 'medio',
             'total': len(sem_resposta),
             'clientes': [dict(ficha(x['cliente_id']), pergunta=x['pergunta']) for x in sem_resposta]},
            {'chave': 'economia', 'rotulo': 'Economia sem valor pago',
             'como': 'A rotina das 8h30 avisa a Flávia', 'grau': 'medio', 'total': len(economia),
             'itens': economia},
        ],
        'clientes': tabela,
        'ajustes': ajustes,
    }


@router.get('/visao')
async def visao(periodo: str = Query('tudo', pattern='^(7|30|tudo)$'),
                tipo: str = Query('todos', pattern='^(todos|pf|pj)$'),
                user: Usuario = Depends(quem_pode)):
    await asyncio.to_thread(_garantir_ajustes)
    return await asyncio.to_thread(_visao, periodo, tipo)


class NovoAjuste(BaseModel):
    data: str
    titulo: str
    detalhe: str = ''


@router.post('/ajustes')
async def novo_ajuste(req: NovoAjuste, user: Usuario = Depends(quem_pode)):
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', req.data or ''):
        raise HTTPException(422, 'Data no formato AAAA-MM-DD')
    titulo = (req.titulo or '').strip()[:80]
    if not titulo:
        raise HTTPException(422, 'Escreva o que mudou')

    def gravar():
        _garantir_ajustes()
        con = sqlite3.connect(DB, timeout=10)
        try:
            cur = con.execute('INSERT INTO portal_ajuste(data,titulo,detalhe,autor,criado_em) VALUES(?,?,?,?,?)',
                              (req.data, titulo, (req.detalhe or '').strip()[:240], user.nome, time.time()))
            con.commit()
            return cur.lastrowid
        finally:
            con.close()
    return {'id': await asyncio.to_thread(gravar)}


@router.delete('/ajustes/{ajuste_id}')
async def apagar_ajuste(ajuste_id: int, user: Usuario = Depends(quem_pode)):
    def apagar():
        con = sqlite3.connect(DB, timeout=10)
        try:
            con.execute('DELETE FROM portal_ajuste WHERE id=?', (ajuste_id,))
            con.commit()
        finally:
            con.close()
    await asyncio.to_thread(apagar)
    return {'ok': True}
