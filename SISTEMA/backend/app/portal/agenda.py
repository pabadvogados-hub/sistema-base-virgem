""
import re
from datetime import datetime, timezone

_QUANDO = re.compile(r'\((\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})\s*([^)]*)\)')
_ACAO = re.compile(r'\b(designad|cancelad|redesignad|adiad|realizad)', re.I)
_TIPO = re.compile(r'(audi.ncia|per.cia|sess.o\s+de\s+julgamento|julgamento)', re.I)


PREPARO = {
    'Audiência una': 'A empresa precisa levar preposto e testemunhas.',
    'Audiência de instrução': 'A empresa precisa levar preposto e testemunhas.',
    'Encerramento da instrução': 'Fase de provas se encerra. Em regra não há '
                                 'testemunha a levar.',
    'Perícia': 'O perito examina o que foi pedido. A empresa costuma acompanhar '
               'com assistente técnico.',
    'Audiência de conciliação': 'Tentativa de acordo. Exige preposto com poderes '
                                'para negociar.',
    'Sessão de julgamento': 'O tribunal julga o recurso. Não exige presença.',
}


def _rotulo(descricao: str) -> str:
    ""
    d = (descricao or '').lower()
    if 'perícia' in d or 'pericia' in d:
        return 'Perícia'
    if 'encerramento' in d:
        return 'Encerramento da instrução'
    if 'julgamento' in d and 'audiência' not in d and 'audiencia' not in d:
        return 'Sessão de julgamento'
    if 'una' in d:
        return 'Audiência una'
    if 'conciliação' in d or 'conciliacao' in d:
        return 'Audiência de conciliação'
    if 'instrução' in d or 'instrucao' in d:
        return 'Audiência de instrução'
    if 'julgamento' in d:
        return 'Audiência de julgamento'
    return 'Audiência'


def _acao(descricao: str) -> str:
    m = _ACAO.search(descricao or '')
    if not m:
        return ''
    r = m.group(1).lower()
    if r.startswith('designad'):
        return 'marcada'
    if r.startswith('realizad'):
        return 'realizada'
    return 'desmarcada'


def compromissos(eventos: list, agora: datetime = None) -> list:
    ""
    agora = agora or datetime.now()
    marcados, tirados = {}, set()

    for e in eventos or []:
        desc = (e.get('descricao') or e.get('nome') or '')
        if not _TIPO.search(desc):
            continue
        m = _QUANDO.search(desc)
        if not m:
            continue
        try:
            quando = datetime.strptime(m.group(1) + ' ' + m.group(2), '%d/%m/%Y %H:%M')
        except ValueError:
            continue

        chave = quando.strftime('%Y-%m-%d %H:%M')
        acao = _acao(desc)
        if acao in ('desmarcada', 'realizada'):
            tirados.add(chave)
        elif acao == 'marcada':
            marcados[chave] = {
                'quando': quando,
                'tipo': _rotulo(desc),
                'local': (m.group(3) or '').strip(' -·'),
                'original': desc,
            }

    saida = [v for k, v in marcados.items() if k not in tirados and v['quando'] > agora]
    return sorted(saida, key=lambda x: x['quando'])


def da_empresa(processos: list, espelho_fn) -> list:
    ""
    tudo = []
    for p in processos:
        try:
            esp = espelho_fn(p['numero'], p['monitorado'])
        except Exception:
            continue
        for c in compromissos(esp.get('eventos') or []):
            c = dict(c)
            c['numero'] = p['numero']
            c['data'] = c['quando'].strftime('%d/%m/%Y')
            c['hora'] = c['quando'].strftime('%H:%M')
            c['dias'] = (c['quando'] - datetime.now()).days
            c['preparo'] = PREPARO.get(c['tipo'], '')
            c.pop('quando', None)
            c.pop('original', None)
            tudo.append(c)
    return sorted(tudo, key=lambda x: (x['data'][6:], x['data'][3:5], x['data'][:2], x['hora']))



PRIORIDADE = {'sistema': 3, 'agenda': 2, 'tribunal': 1}

_TIPO_AGENDA = re.compile(r'audi.ncia|per.cia|julgamento', re.I)
_CNJ = re.compile(r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}')

NAO_E_COMPROMISSO = re.compile(
    r'^\s*(PF[_ ]|PRAZO|CIENCIA|CI.NCIA|MERO EXPEDIENTE|REUNI.O|'
    r'SESS.O DE DESENVOLVIMENTO|PDI\b)', re.I)


def e_compromisso(titulo: str) -> bool:
    ""
    t = (titulo or '').strip()
    if not t or NAO_E_COMPROMISSO.search(t):
        return False
    return bool(_TIPO_AGENDA.search(t))


def do_sistema(abrir, numeros: set) -> list:
    ""
    saida = []
    try:
        with abrir() as con:
            linhas = con.execute(
                "SELECT tipo, titulo, processo_numero, data, hora FROM tarefas "
                "WHERE status != 'concluida' AND data IS NOT NULL AND data != ''").fetchall()
    except Exception:
        return saida
    hoje = datetime.now().strftime('%Y-%m-%d')
    for r in linhas:
        texto = '%s %s' % (r[0] or '', r[1] or '')
        if not e_compromisso(texto):
            continue
        numero = r[2] or ''
        if not numero:
            m = _CNJ.search(texto)
            numero = m.group(0) if m else ''
        if numero not in numeros:
            continue
        data = (r[3] or '')[:10]
        if data < hoje:
            continue
        saida.append({'fonte': 'sistema', 'numero': numero,
                      'data': '%s/%s/%s' % (data[8:10], data[5:7], data[:4]),
                      'hora': (r[4] or '')[:5], 'tipo': _rotulo(texto),
                      'local': '', 'titulo': (r[1] or '').strip()})
    return saida


def do_google(servico, calendarios: list, numeros: set, dias=180) -> list:
    ""
    from datetime import timedelta
    saida = []
    agora = datetime.now(timezone.utc)
    ate = agora + timedelta(days=dias)
    for cal in calendarios or []:
        try:
            r = servico.events().list(
                calendarId=cal, timeMin=agora.isoformat(), timeMax=ate.isoformat(),
                maxResults=250, singleEvents=True, orderBy='startTime').execute()
        except Exception:
            continue
        for e in r.get('items', []):
            titulo = e.get('summary') or ''
            if not e_compromisso(titulo):
                continue
            m = _CNJ.search('%s %s' % (titulo, e.get('description') or ''))
            if not m or m.group(0) not in numeros:
                continue
            ini = (e.get('start') or {}).get('dateTime') or (e.get('start') or {}).get('date') or ''
            if not ini:
                continue
            saida.append({'fonte': 'agenda', 'numero': m.group(0),
                          'data': '%s/%s/%s' % (ini[8:10], ini[5:7], ini[:4]),
                          'hora': ini[11:16] if 'T' in ini else '',
                          'tipo': _rotulo(titulo),
                          'local': (e.get('location') or '').strip(),
                          'titulo': titulo.strip()})
    return saida


def unificada(processos, espelho_fn, abrir=None, servico_google=None,
              calendarios=None) -> list:
    ""
    numeros = {p['numero'] for p in processos if p.get('numero')}

    itens = []
    for c in da_empresa(processos, espelho_fn):
        c['fonte'] = 'tribunal'
        itens.append(c)
    if abrir:
        itens.extend(do_sistema(abrir, numeros))
    if servico_google:
        itens.extend(do_google(servico_google, calendarios, numeros))

    melhor = {}
    for c in itens:
        chave = (c['numero'], c['data'])
        atual = melhor.get(chave)
        if not atual or PRIORIDADE[c['fonte']] > PRIORIDADE[atual['fonte']]:
            if atual:
                c = dict(c)
                c['local'] = c.get('local') or atual.get('local') or ''
                c['hora'] = c.get('hora') or atual.get('hora') or ''
            melhor[chave] = c

    def ordem(c):
        d = c['data']
        return (d[6:], d[3:5], d[:2], c.get('hora') or '')

    saida = sorted(melhor.values(), key=ordem)
    for c in saida:
        if (c.get('hora') or '') in ('00:00', '0:00'):
            c['hora'] = ''
        try:
            q = datetime.strptime(c['data'] + ' ' + (c['hora'] or '00:00'), '%d/%m/%Y %H:%M')
            c['dias'] = (q - datetime.now()).days
        except ValueError:
            c['dias'] = 0
        c['preparo'] = PREPARO.get(c.get('tipo'), '')
    return saida
