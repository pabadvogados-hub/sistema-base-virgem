""
import datetime as _dt
import re
import sqlite3

DB = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
TAREFAS = (2434310, 7116826)

DDL = """CREATE TABLE IF NOT EXISTS portal_atualizacao_cliente (
    id INTEGER PRIMARY KEY AUTOINCREMENT, numero TEXT, lawsuit_id INTEGER, post_id INTEGER,
    task_id INTEGER, tarefa TEXT, concluida_em TEXT, origem TEXT, observacao TEXT, lido_em REAL,
    UNIQUE(numero, post_id))"""
DDL_AVISO = """CREATE TABLE IF NOT EXISTS portal_sentenca_aviso (
    id INTEGER PRIMARY KEY AUTOINCREMENT, numero TEXT, cliente_id INTEGER, decisao_em TEXT,
    motivo TEXT, pergunta TEXT, criado_em REAL, avisado_em REAL)"""

TRANSITO = re.compile(
    r'^\s*(tr[âa]nsitad[oa] em julgado|tr[âa]nsito em julgado|certid[ãa]o de tr[âa]nsito|'
    r'baixa definitiva|arquivad[oa] definitivamente|certid[ãa]o de arquivamento definitivo)', re.I)
DECISAO_TIPO = re.compile(r'senten[çc]a|ac[óo]rd[ãa]o', re.I)
QUER_RESULTADO = re.compile(
    r'ganh|perd|resultado|senten|ac[oó]rd|decid|decis|julg|venci|procedente|quanto|valor|receber|'
    r'como (terminou|acabou|ficou)|o que (saiu|deu)|condena', re.I)


def _dia(iso):
    try:
        d = _dt.datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        if d.tzinfo:
            d = d.astimezone(_dt.timezone(_dt.timedelta(hours=-3)))
        return d.date()
    except Exception:
        try:
            return _dt.date.fromisoformat(str(iso)[:10])
        except Exception:
            return None


def _texto(e):
    return ' '.join(x for x in (e.get('descricao'), e.get('nome'), e.get('tipo_documento')) if x)


def decisoes(eventos, traduzir):
    ""
    saida = []
    for e in eventos or []:
        titulo = (traduzir(e).get('titulo') or '') if traduzir else ''
        eh = (titulo.startswith('Sentenca') or titulo.startswith('Tribunal julgou')
              or titulo.startswith('Recurso julgado')
              or DECISAO_TIPO.search(e.get('tipo_documento') or ''))
        if eh and _dia(e.get('data')):
            saida.append(e)
    saida.sort(key=lambda e: _dia(e.get('data')), reverse=True)
    return saida


def transito_depois(eventos, data):
    ""
    for e in sorted(eventos or [], key=lambda x: _dia(x.get('data')) or _dt.date.min):
        d = _dia(e.get('data'))
        if d and d >= data and (TRANSITO.search(e.get('descricao') or '') or TRANSITO.search(e.get('nome') or '')):
            return e
    return None


def atualizacao_depois(numero, data, abrir=None):
    ""
    try:
        con = abrir() if abrir else sqlite3.connect('file:%s?mode=ro' % DB, uri=True, timeout=10)
        r = con.execute('SELECT tarefa, concluida_em, origem FROM portal_atualizacao_cliente '
                        'WHERE numero=? AND concluida_em>=? ORDER BY concluida_em LIMIT 1',
                        (numero, str(data))).fetchone()
        con.close()
        return tuple(r) if r else None
    except sqlite3.OperationalError:
        return None


def avaliar(numero, eventos, traduzir, abrir=None):
    ""
    ds = decisoes(eventos, traduzir)
    if not ds:
        return {'decisao': None, 'pode': False, 'motivo': 'sem sentenca ou acordao', 'transito': None}
    ultima = ds[0]
    data = _dia(ultima.get('data'))
    tr = transito_depois(eventos, data)
    at = atualizacao_depois(numero, data, abrir)
    if not tr:
        return {'decisao': ultima, 'pode': False, 'transito': None, 'atualizacao': at,
                'motivo': 'sem registro de transito em julgado depois da decisao de %s' % data.strftime('%d/%m/%Y')}
    if not at:
        return {'decisao': ultima, 'pode': False, 'transito': tr, 'avisar': True,
                'motivo': 'transitou, mas nao ha atualizacao do cliente registrada depois de %s' % data.strftime('%d/%m/%Y')}
    return {'decisao': ultima, 'pode': True, 'transito': tr, 'atualizacao': at,
            'motivo': 'transitou (%s) e o cliente foi atualizado (%s em %s)'
                      % (_dia(tr.get('data')).strftime('%d/%m/%Y'), at[0], at[1][:10])}


def bloco_para_contexto(pergunta, numero, eventos, traduzir, ler_doc, limpar=None, abrir=None):
    ""
    av = avaliar(numero, eventos, traduzir, abrir)
    d = av.get('decisao')
    if not d:
        return '', av
    quando = _dia(d.get('data')).strftime('%d/%m/%Y')
    tipo = 'ACORDAO (decisao do tribunal)' if (traduzir(d).get('titulo') or '').startswith(('Tribunal', 'Recurso julgado')) \
        or re.search(r'ac[óo]rd', _texto(d), re.I) else 'SENTENCA'
    if av['pode']:
        texto = ''
        if d.get('documento_id') and d.get('tem_texto') and ler_doc:
            try:
                texto = ler_doc(d['documento_id'])
                if limpar:
                    texto = limpar(texto)
            except Exception:
                texto = ''
        corpo = ('\n' + '=' * 40 + '\nRESULTADO LIBERADO: a %s de %s ja e definitiva (o tribunal registrou o transito '
                 'em julgado) e o escritorio ja conversou com o cliente sobre ela. VOCE PODE EXPLICAR O RESULTADO, '
                 'pedido por pedido, em portugues simples: o que foi aceito, o que foi negado e o que isso muda na '
                 'pratica. Continue SEM citar valor em reais: o valor exato sai no calculo final.' % (tipo, quando))
        if texto:
            corpo += '\nTEXTO DA DECISAO:\n' + texto[:7000]
        else:
            corpo += ('\nO texto da decisao nao veio do tribunal. Explique so o que os andamentos mostram e diga que '
                      'o escritorio tem o documento completo.')
        return corpo, av
    if not QUER_RESULTADO.search(pergunta or ''):
        return ('\n' + '=' * 40 + '\nATENCAO: existe %s de %s neste processo e o resultado dela AINDA NAO pode ser '
                'contado por aqui. Se a conversa chegar nela, diga apenas que a decisao saiu e que o escritorio conversa '
                'com o cliente sobre o resultado.' % (tipo, quando)), av
    ja = ''
    if av.get('atualizacao'):
        ja = (' O escritorio JA CONVERSOU com o cliente sobre essa decisao em %s: pode dizer isso, sem repetir '
              'o resultado, e explicar que ela ainda pode mudar porque o processo segue em recurso.'
              % _dt.date.fromisoformat(av['atualizacao'][1][:10]).strftime('%d/%m/%Y'))
    return ('\n' + '=' * 40 + '\nRESULTADO AINDA NAO LIBERADO: o cliente perguntou sobre a %s de %s, mas %s. '
            'NAO conte o resultado: nao diga se ganhou ou perdeu, nao diga o que foi aceito ou negado, nao diga '
            '"procedente" nem "improcedente". Diga, com calma e sem alarmar, que a decisao saiu, que o escritorio esta '
            'analisando com cuidado e vai conversar com ele sobre o que ela significa, e conte o que vem pela frente '
            'no andamento. Isso NAO e empurrar para a advogada: e a regra do escritorio para decisao que ainda pode '
            'mudar. Nunca diga que "nao pode falar" ou que "nao conversa" sobre o resultado: diga que o escritorio '
            'vai conversar.' % (tipo, quando, av['motivo'])) + ja, av


def registrar_aviso(numero, cliente_id, av, pergunta, abrir):
    ""
    import time
    try:
        con = abrir()
        con.execute(DDL_AVISO)
        con.execute('INSERT INTO portal_sentenca_aviso(numero,cliente_id,decisao_em,motivo,pergunta,criado_em) '
                    'VALUES(?,?,?,?,?,?)', (numero, cliente_id, str(_dia(av['decisao'].get('data'))), av['motivo'],
                                            (pergunta or '')[:300], time.time()))
        con.commit()
        con.close()
    except Exception:
        pass


MASCARA = {
    'Recurso julgado e aceito em parte': ('Recurso julgado', 'O tribunal julgou o recurso. O resultado o escritorio conversa com o cliente.'),
    'Recurso julgado e aceito': ('Recurso julgado', 'O tribunal julgou o recurso. O resultado o escritorio conversa com o cliente.'),
    'Recurso julgado e negado': ('Recurso julgado', 'O tribunal julgou o recurso. O resultado o escritorio conversa com o cliente.'),
    'Recurso julgado': ('Recurso julgado', 'O tribunal julgou o recurso. O resultado o escritorio conversa com o cliente.'),
    'Tribunal julgou o recurso': ('Recurso julgado', 'O tribunal julgou o recurso. O resultado o escritorio conversa com o cliente.'),
    'Sentenca proferida': ('Sentenca', 'O juiz decidiu o caso. O resultado o escritorio conversa com o cliente.'),
    'Sentenca': ('Sentenca', 'O juiz decidiu o caso. O resultado o escritorio conversa com o cliente.'),
    'Pedido de esclarecimento aceito em parte': ('Pedido de esclarecimento julgado', 'O pedido de esclarecimento sobre a decisao foi julgado.'),
    'Pedido de esclarecimento aceito': ('Pedido de esclarecimento julgado', 'O pedido de esclarecimento sobre a decisao foi julgado.'),
    'Pedido de esclarecimento negado': ('Pedido de esclarecimento julgado', 'O pedido de esclarecimento sobre a decisao foi julgado.'),
}


DE_QUEM = re.compile(r'(?:recurso|Embargos de Declara[çc][ãa]o) de (.+?)'
                     r'(?:\s+-\s+(?:CPF|CNPJ)\b|\s+e\s+(?:provido|n[ãa]o provido|desprovido)|$)', re.I)


def tradutor_com_trava(traduzir, av):
    ""
    if av.get('pode') or not av.get('decisao'):
        return traduzir

    def _t(e):
        t = dict(traduzir(e))
        troca = MASCARA.get(t.get('titulo'))
        if troca:
            m = DE_QUEM.search(t.get('tecnico') or '')
            t['titulo'], t['explicacao'] = troca
            if m and 'uma das partes' not in m.group(1).lower():
                t['explicacao'] += ' (%s de %s)' % (
                    'pedido' if t['titulo'].startswith('Pedido') else 'recurso', m.group(1).strip())
            t['tecnico'] = ''
        return t
    return _t


DECISOES_NA_TELA = {
    'Recurso julgado e aceito em parte', 'Recurso julgado e aceito', 'Recurso julgado e negado',
    'Pedido de esclarecimento aceito em parte', 'Pedido de esclarecimento aceito',
    'Pedido de esclarecimento negado', 'Sentenca', 'Sentenca proferida',
}
LINHA_PODE_MUDAR = 'Essa decisão ainda pode mudar. O escritório conversa com você sobre o que ela significa.'


def linha_da_tela(av):
    ""
    if not av or av.get('pode') or not av.get('decisao') or av.get('transito'):
        return None
    return LINHA_PODE_MUDAR


def marcar_tela(eventos, linha):
    ""
    if not linha:
        return eventos
    dias, saida = set(), []
    for t in eventos:
        if isinstance(t, dict) and t.get('titulo') in DECISOES_NA_TELA:
            dia = str(t.get('data') or '')[:10]
            if dia not in dias:
                dias.add(dia)
                t = dict(t)
                t['aviso_trava'] = linha
        saida.append(t)
    return saida
