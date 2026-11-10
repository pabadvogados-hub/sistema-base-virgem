# -*- coding: utf-8 -*-
"""CONSULTA AOS AUTOS ANTES DA SUGESTAO - Sistema Escritório Modelo (05/10/2026).

Por que existe. Em 05/10/2026 a Dra. Advogada foi fechar a conferencia de um
despacho do caso A x empresa A ("intime-se a parte embargada a fim de que se
manifeste") e perguntou: "quando voce recepciona um despacho, voce vai ate o
processo para verificar e depois sugere? Nesse caso, quem e a embargada?".
O motor lia so o texto da publicacao e tinha sugerido OPOR embargos. Pelos
autos, os embargos eram da outra parte e a embargada eramos nos: a tarefa ia
receber baixa sem a manifestacao.

O que este modulo faz, por CODIGO (nenhuma chamada de IA):
  - le os autos pela MESMA fonte da aba Movimentacoes da tela do processo
    (SYNC /processos/{n}/autos);
  - numera os movimentos pela ordem (no Projudi bate com o "Seq." da tela);
  - quando o ato fala de embargos, lista cada peticao de embargos, diz qual e
    nossa (pelo NOSSO registro de protocolo) e qual estava pendente quando o
    juiz despachou, e conclui de quem e o prazo.

Limite conhecido (medido em 05/10/2026): o SYNC nao manda "Movimentado por"
nem o complemento do movimento, e no Projudi (TJPR) nao manda as peticoes das
partes. Por isso a conclusao vem sempre com a evidencia ao lado, para a pessoa
confirmar; quando nao da para concluir, o resultado e INDEFINIDO ("a conferir
nos autos"), nunca um palpite.
"""
import datetime as dt
import re
import unicodedata

FUSO_HORAS = -3          # Brasilia; o SYNC entrega em UTC


def _norm(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    return ''.join(ch for ch in s if not unicodedata.combining(ch)).upper()


def hora_local(valor):
    """'2026-09-10T22:22:18.917000+00:00' (UTC) -> datetime de Brasilia. None se nao der."""
    if not valor:
        return None
    txt = str(valor).strip()
    try:
        d = dt.datetime.fromisoformat(txt.replace('Z', '+00:00'))
    except ValueError:
        try:
            d = dt.datetime.fromisoformat(txt[:19])
        except ValueError:
            return None
    if d.tzinfo is not None:
        d = d.astimezone(dt.timezone(dt.timedelta(hours=FUSO_HORAS))).replace(tzinfo=None)
    elif len(txt) > 10:
        d = d + dt.timedelta(hours=FUSO_HORAS)
    return d


def movimentos(itens):
    """Movimentos em ordem cronologica, numerados a partir de 1."""
    brutos = [x for x in (itens or []) if isinstance(x, dict) and x.get('tipo') == 'movimento']
    com_data = [(hora_local(x.get('data')), x) for x in brutos]
    com_data = [(q, x) for q, x in com_data if q]
    com_data.sort(key=lambda p: p[0])
    return [{'n': i, 'quando': q, 'descricao': str(x.get('descricao') or ''), 'classe': str(x.get('classe') or '')}
            for i, (q, x) in enumerate(com_data, 1)]


def cobertura(itens):
    """O quanto o SYNC entregou deste processo: serve para a ficha dizer se da para confiar."""
    docs = [x for x in (itens or []) if isinstance(x, dict) and x.get('tipo') == 'documento']
    pets = [d for d in docs if re.search(r'PETI|CONTEST|RECURSO|EMBARG|MANIFEST|INICIAL|RAZOES|REPLICA|IMPUGNA',
                                         _norm(d.get('tipo_documento') or d.get('nome')))]
    movs = sum(1 for x in (itens or []) if isinstance(x, dict) and x.get('tipo') == 'movimento')
    return {'movimentos': movs, 'documentos': len(docs), 'peticoes': len(pets),
            'completa': bool(pets),
            'aviso': '' if pets else 'O SYNC nao trouxe as peticoes das partes deste processo: so da para ir pela ordem dos movimentos.'}


_RE_PROTOCOLO = re.compile(r'(?:PROTOCOLAD[OA]S?(?:\s+EM)?\s*[:_ ]?\s*(\d{2})[./](\d{2})[./](\d{4}))|(?:(\d{2})[./](\d{2})[./](\d{4})\s*_\s*PROTOCOLAD)')


def datas_de_protocolo(textos):
    """Datas em que o ESCRITORIO protocolou, tiradas dos nossos registros
    ("Protocolado em 25.09.2026_...", "25.09.2026_PROTOCOLADO_...")."""
    fora = []
    for t in textos or []:
        alvo = _norm(t)
        for m in _RE_PROTOCOLO.finditer(alvo):
            g = m.groups()
            d, mes, a = (g[0], g[1], g[2]) if g[0] else (g[3], g[4], g[5])
            try:
                fora.append({'data': dt.date(int(a), int(mes), int(d)), 'embargos': bool(re.search(r'(?<![A-Z0-9])EDS?(?![A-Z0-9])|EMBARG', alvo)),
                             'texto': str(t)[:140]})
            except ValueError:
                continue
    return fora


def registros_do_escritorio(con, processo):
    """Textos dos nossos registros sobre o processo: tarefas (inclusive o espelho do
    ADVBOX) e andamentos. So leitura."""
    textos = []
    try:
        for r in con.execute("SELECT COALESCE(titulo,'') || ' ' || COALESCE(descricao,'') FROM tarefas WHERE processo_numero=?",
                             (processo,)):
            textos.append(r[0])
    except Exception:
        pass
    try:
        for r in con.execute("SELECT COALESCE(texto,'') FROM jur_andamentos WHERE processo_numero=? AND removido=0", (processo,)):
            textos.append(r[0])
    except Exception:
        pass
    return textos


def _e_decisao(m):
    return m['classe'].lower() == 'decisao' or re.match(r'PROFERID|DECISAO|DESPACHO|ACOLHID|REJEITAD|NAO CONHECID|JULGAD|HOMOLOGAD|INDEFERID|DEFERID',
                                                        _norm(m['descricao']))


def _e_julgamento(m):
    """Decisao que resolve alguma coisa (nao despacho de mero expediente nem ato ordinatorio)."""
    return bool(_e_decisao(m)) and not re.search(r'DESPACHO|MERO EXPEDIENTE|ORDINATORIO', _norm(m['descricao']))


def embargos(itens, disponibilizacao, protocolos):
    """De quem sao os embargos de que o despacho trata, e de quem e o prazo.

    itens: autos do SYNC. disponibilizacao: data do DJEN desta publicacao.
    protocolos: saida de datas_de_protocolo (nossos registros).
    Devolve dict com: despacho, embargos (lista), titularidade (NOSSO, CONTRARIA,
    AMBOS ou INDEFINIDO), explicacao, firmeza ('ordem dos movimentos' ...).
    """
    movs = movimentos(itens)
    disp = hora_local(str(disponibilizacao)[:10]) if disponibilizacao else None
    limite = (disp + dt.timedelta(days=1)) if disp else None
    decisoes = [m for m in movs if _e_decisao(m) and (not limite or m['quando'] < limite)]
    despacho = decisoes[-1] if decisoes else None
    eds = [m for m in movs if re.search(r'EMBARGOS DE DECLARA', _norm(m['descricao']))
           and (m['classe'].lower() == 'peticao' or 'PETI' in _norm(m['descricao']))]
    # Pendentes sao os embargos opostos DEPOIS do ultimo julgamento anterior ao despacho. Embargos
    # antigos, ja seguidos de decisao, nao contam (num processo do TJSP a primeira versao disto
    # deu como pendentes embargos de um ano e meio antes).
    julgou = [m for m in decisoes if _e_julgamento(m) and (not despacho or m['n'] < despacho['n'])]
    piso = julgou[-1]['n'] if julgou else 0
    datas_ed = {p['data'] for p in protocolos or [] if p.get('embargos')}
    datas_qualquer = {p['data'] for p in protocolos or []}
    lista = []
    for m in eds:
        if m['n'] <= piso:
            continue
        dia = m['quando'].date()
        if dia in datas_ed:
            de, base = 'nosso', 'nosso registro de protocolo de embargos em %s' % dia.strftime('%d/%m/%Y')
        elif dia in datas_qualquer:
            de, base = 'nosso', 'nosso registro de protocolo em %s' % dia.strftime('%d/%m/%Y')
        else:
            de, base = 'outra parte', 'nao ha registro de protocolo nosso nessa data'
        lista.append({'n': m['n'], 'quando': m['quando'].strftime('%d/%m/%Y %H:%M'), 'de': de, 'base': base,
                      'pendente_no_despacho': bool(despacho and m['n'] < despacho['n']), 'descricao': m['descricao']})
    pend = [e for e in lista if e['pendente_no_despacho']]
    nossos = [e for e in pend if e['de'] == 'nosso']
    deles = [e for e in pend if e['de'] != 'nosso']
    if not despacho or not pend:
        tit = 'INDEFINIDO'
        exp = ('Nao achei nos autos embargos pendentes antes do despacho: conferir no tribunal de quem sao os embargos.'
               if despacho else 'Nao achei o despacho nos movimentos: conferir nos autos.')
    elif deles and not nossos:
        tit = 'NOSSO'
        exp = ('Quando o juiz despachou (mov. %d, %s), os embargos pendentes eram os da outra parte (%s). A embargada e a '
               'nossa parte: cabe a nos a manifestacao.'
               % (despacho['n'], despacho['quando'].strftime('%d/%m/%Y'),
                  ', '.join('mov. %d de %s' % (e['n'], e['quando'][:10]) for e in deles)))
    elif nossos and not deles:
        tit = 'CONTRARIA'
        exp = ('Quando o juiz despachou (mov. %d, %s), os embargos pendentes eram os nossos (%s). A embargada e a outra '
               'parte: o prazo e dela.'
               % (despacho['n'], despacho['quando'].strftime('%d/%m/%Y'),
                  ', '.join('mov. %d de %s' % (e['n'], e['quando'][:10]) for e in nossos)))
    else:
        tit = 'AMBOS'
        exp = ('Quando o juiz despachou (mov. %d, %s) havia embargos das duas partes pendentes: cada uma responde aos da '
               'outra. Cabe a nos a manifestacao sobre os embargos da outra parte (%s).'
               % (despacho['n'], despacho['quando'].strftime('%d/%m/%Y'),
                  ', '.join('mov. %d de %s' % (e['n'], e['quando'][:10]) for e in deles)))
    depois = [e for e in lista if not e['pendente_no_despacho']]
    if depois and despacho:
        exp += ' Embargos juntados DEPOIS do despacho nao sao objeto dele: %s.' % ', '.join(
            'mov. %d de %s (%s)' % (e['n'], e['quando'][:10], e['de']) for e in depois)
    return {
        'despacho': {'n': despacho['n'], 'quando': despacho['quando'].strftime('%d/%m/%Y %H:%M'),
                     'descricao': despacho['descricao']} if despacho else None,
        'embargos': lista, 'titularidade': tit, 'explicacao': exp,
        'firmeza': 'Pela ordem dos movimentos e pelo nosso registro de protocolo; o SYNC nao informa quem peticionou. '
                   'Confirme no tribunal antes de fechar.',
    }


# Fase do processo pelos movimentos (caso Romildo, 05/10/2026). Marcos do PJe-JT, do mais
# recente para o mais antigo: o ultimo marco diz o grau. 'baixa' e 'transito' devolvem ao 1o grau.
_MARCOS = (
    ('remessa_tst', 3, re.compile(r'REMETIDOS OS AUTOS (PARA|AO) (O )?(TST|TRIBUNAL SUPERIOR)|RECEBIDOS OS AUTOS NO TST')),
    ('acordao', 2, re.compile(r'PUBLICAD[OA]( O)? ACORDAO|ACORDAO EM \d{2}/\d{2}/\d{4}|DISPONIBILIZAD[OA]( O)? ACORDAO')),
    ('julgamento', 2, re.compile(r'(NAO )?CONHECIDO O RECURSO|NEGADO PROVIMENTO|DADO PROVIMENTO|E (NAO )?PROVIDO|PROVIDO EM PARTE')),
    ('pauta', 2, re.compile(r'INCLUIDO EM PAUTA|RECEBIDOS OS AUTOS PARA INCLUIR EM PAUTA|CONCLUSOS OS AUTOS PARA JULGAMENTO \(RELATAR\)|'
                            r'CONCLUSOS (OS AUTOS )?A[O ]? ?RELATOR')),
    ('remessa_trt', 2, re.compile(r'REMETIDOS OS AUTOS (PARA|AO) (O )?(TRT|TRIBUNAL REGIONAL)|RECEBIDOS OS AUTOS NO TRT')),
    ('baixa', 1, re.compile(r'BAIXA DEFINITIVA|RECEBIDOS OS AUTOS (DO|VINDOS DO) (TRT|TST|TRIBUNAL)|INICIADA A EXECUCAO|INICIADA A LIQUIDACAO')),
    ('transito', 1, re.compile(r'TRANSITAD[OA] EM JULGADO')),
    ('sentenca', 1, re.compile(r'PUBLICAD[OA]( A)? SENTENCA|JULGAD[OA] (IM)?PROCEDENTE|SENTENCA EM \d{2}/\d{2}/\d{4}|PROFERIDA SENTENCA')),
)
_MARCADOR_PJE = re.compile(r'\((?:S|A|AS|OS|EM|M)\)')
_DATA_BR = re.compile(r'(\d{2})/(\d{2})/(\d{4})')
_ROTULO_GRAU = {1: '1o grau (Vara)', 2: '2o grau (TRT/TJ)', 3: 'tribunal superior'}


def fase(itens):
    """De que fase o processo esta, pelos movimentos: o ultimo marco manda. Devolve dict com
    grau, decisao ('acordao'/'sentenca'/None), publicado_em, resultados e os marcos usados."""
    movs = movimentos(itens)
    achados = []
    for m in movs:
        d = _MARCADOR_PJE.sub('', _norm(m.get('descricao') or ''))
        for nome, grau, rx in _MARCOS:
            if rx.search(d):
                achados.append({'n': m['n'], 'quando': m['quando'], 'marco': nome, 'grau': grau, 'texto': (m.get('descricao') or '')[:140]})
                break
    if not achados:
        return {'grau': None, 'rotulo': 'Fase nao identificada nos movimentos', 'decisao': None, 'marcos': []}
    ultimo = achados[-1]
    grau = ultimo['grau']
    # grau pelo campo do SYNC, quando ele vier preenchido no ultimo movimento
    g = str((movs[-1] if movs else {}).get('grau') or '').upper()
    if g in ('2', 'G2', 'SEGUNDO', '2O GRAU'):
        grau = max(grau, 2)
    recentes = [a for a in achados if a['grau'] == grau][-6:]
    decisao = None
    publicado_em = None
    resultados = []
    for a in recentes:
        if a['marco'] == 'acordao':
            decisao = 'acordao'
            dm = _DATA_BR.search(a['texto'])
            if dm and 'PUBLICAD' in _norm(a['texto']):
                publicado_em = '%s-%s-%s' % (dm.group(3), dm.group(2), dm.group(1))
        elif a['marco'] == 'julgamento':
            resultados.append(a['texto'])
        elif a['marco'] == 'sentenca' and grau == 1:
            decisao = 'sentenca'
    if grau == 2 and decisao is None and any(a['marco'] in ('julgamento', 'pauta') for a in recentes):
        decisao = 'acordao'
    rot = _ROTULO_GRAU.get(grau, '?')
    if decisao == 'acordao':
        rot += ': acordao' + (' publicado em ' + publicado_em[8:10] + '/' + publicado_em[5:7] + '/' + publicado_em[:4] if publicado_em else ' (julgamento da Turma)')
    elif decisao == 'sentenca':
        rot += ': sentenca'
    elif ultimo['marco'] in ('baixa', 'transito'):
        rot += ': ' + ('transito em julgado' if ultimo['marco'] == 'transito' else 'autos de volta a Vara (execucao/liquidacao)')
    return {'grau': grau, 'rotulo': rot, 'decisao': decisao, 'publicado_em': publicado_em,
            'resultados': resultados[-4:], 'marcos': recentes[-4:], 'base': 'mov. %s a %s' % (recentes[0]['n'], recentes[-1]['n']) if recentes else ''}


def ultimos_movimentos(itens, quantos=12):
    movs = movimentos(itens)
    return [{'n': m['n'], 'quando': m['quando'].strftime('%d/%m/%Y %H:%M'), 'descricao': m['descricao']} for m in movs[-quantos:]][::-1]


def consultar(itens, ato, texto, disponibilizacao, protocolos):
    """A consulta que o fechamento da conferencia mostra. Devolve sempre um dict."""
    # So cabe perguntar "quem e a embargada" quando a publicacao manda RESPONDER a embargos. Decisao
    # publicada que abre prazo para OPOR embargos nao tem embargada nenhuma.
    alvo = _norm(ato) + ' ' + _norm(texto)
    fala_de_embargos = bool(re.search(r'MANIFESTACAO SOBRE ED|EMBARGAD|SOBRE OS EMBARGOS|AOS EMBARGOS DE DECLARA|'
                                      r'CONTRARRAZ\w* (AOS|DOS) EMBARGOS', alvo))
    fora = {'fonte': 'SYNC, a mesma da aba Movimentacoes do processo', 'cobertura': cobertura(itens),
            'ultimos': ultimos_movimentos(itens), 'embargos': None, 'fase': fase(itens)}
    if fala_de_embargos:
        fora['embargos'] = embargos(itens, disponibilizacao, protocolos)
    return fora


if __name__ == '__main__':
    # o caso real que motivou o modulo: caso A x empresa A, TJPR, publicacao de 02/10/2026
    autos = [
        {'tipo': 'movimento', 'data': '2026-09-09T15:38:09+00:00', 'descricao': 'ACOLHIDA EM PARTE A IMPUGNAÇÃO AO CUMPRIMENTO DE SENTENÇA', 'classe': 'decisao'},
        {'tipo': 'movimento', 'data': '2026-09-10T21:39:02+00:00', 'descricao': 'EXPEDIÇÃO DE INTIMAÇÃO', 'classe': 'comunicacao'},
        {'tipo': 'movimento', 'data': '2026-09-10T21:58:38+00:00', 'descricao': 'CONFIRMADA A INTIMAÇÃO ELETRÔNICA', 'classe': 'comunicacao'},
        {'tipo': 'movimento', 'data': '2026-09-10T22:22:18.917000+00:00', 'descricao': 'JUNTADA DE PETIÇÃO DE EMBARGOS DE DECLARAÇÃO', 'classe': 'peticao'},
        {'tipo': 'movimento', 'data': '2026-09-24T04:13:54+00:00', 'descricao': 'CONCLUSOS PARA DECISÃO', 'classe': 'conclusao'},
        {'tipo': 'movimento', 'data': '2026-09-24T18:16:16+00:00', 'descricao': 'PROFERIDO DESPACHO DE MERO EXPEDIENTE', 'classe': 'decisao'},
        {'tipo': 'movimento', 'data': '2026-09-25T11:29:14+00:00', 'descricao': 'EXPEDIÇÃO DE INTIMAÇÃO', 'classe': 'comunicacao'},
        {'tipo': 'movimento', 'data': '2026-09-25T22:34:47.945000+00:00', 'descricao': 'JUNTADA DE PETIÇÃO DE EMBARGOS DE DECLARAÇÃO', 'classe': 'peticao'},
        {'tipo': 'movimento', 'data': '2026-09-28T23:14:29+00:00', 'descricao': 'JUNTADA DE PETIÇÃO DE REQUERIMENTO DE DILIGÊNCIA', 'classe': 'peticao'},
        {'tipo': 'documento', 'data': '2026-09-24T18:16:16+00:00', 'nome': 'online.pdf', 'tipo_documento': 'Outros'},
        {'tipo': 'intimacao', 'data': '2026-10-02T03:00:00+00:00', 'id': 79801},
    ]
    nossos = datas_de_protocolo(['Protocolado em 25.09.2026_ProjudPR_ED Roberto Dambros_0012740', 'Protocolar https://drive.google.com/x'])
    assert nossos == [{'data': dt.date(2026, 9, 25), 'embargos': True, 'texto': 'Protocolado em 25.09.2026_ProjudPR_ED Roberto Dambros_0012740'}], nossos
    assert hora_local('2026-09-10T22:22:18.917000+00:00').strftime('%d/%m %H:%M') == '10/09 19:22'     # igual ao Projudi
    r = embargos(autos, '2026-10-02', nossos)
    # fase pelos movimentos (caso Romildo, 05/10/2026): acordao da Turma, RO do reclamante nao provido
    trt = [
        {'tipo': 'movimento', 'data': '2026-05-10T12:00:00+00:00', 'descricao': 'Julgado(a) procedente em parte o pedido de FULANO'},
        {'tipo': 'movimento', 'data': '2026-07-01T12:00:00+00:00', 'descricao': 'Remetidos os autos para TRT da 6a Regiao'},
        {'tipo': 'movimento', 'data': '2026-09-10T17:16:00+00:00', 'descricao': 'Conclusos os autos para julgamento (relatar) a MARIA CLARA'},
        {'tipo': 'movimento', 'data': '2026-09-17T19:11:00+00:00', 'descricao': 'Incluido em pauta o processo para 29/09/2026 09:00'},
        {'tipo': 'movimento', 'data': '2026-09-30T01:12:00+00:00', 'descricao': 'Conhecido o recurso de FULANO - CPF: 000.000.000-00 e nao provido'},
        {'tipo': 'movimento', 'data': '2026-10-05T05:20:00+00:00', 'descricao': 'Publicado(a) o(a) acordao em 06/10/2026'},
    ]
    fz = fase(trt)
    assert fz['grau'] == 2 and fz['decisao'] == 'acordao' and fz['publicado_em'] == '2026-10-06', fz
    assert fz['rotulo'] == '2o grau (TRT/TJ): acordao publicado em 06/10/2026' and len(fz['resultados']) == 1, fz
    assert fase(trt[:1])['grau'] == 1 and fase(trt[:1])['decisao'] == 'sentenca'
    assert fase(trt + [{'tipo': 'movimento', 'data': '2026-12-01T12:00:00+00:00', 'descricao': 'Transitado em julgado em 30/11/2026'}])['grau'] == 1
    assert fase([])['grau'] is None
    assert r['titularidade'] == 'NOSSO', r
    assert [(e['n'], e['quando'], e['de'], e['pendente_no_despacho']) for e in r['embargos']] == [
        (4, '10/09/2026 19:22', 'outra parte', True), (8, '25/09/2026 19:34', 'nosso', False)], r['embargos']
    assert r['despacho']['n'] == 6 and 'mov. 4 de 10/09/2026' in r['explicacao'] and 'DEPOIS do despacho' in r['explicacao']
    # so os nossos pendentes: o prazo e da outra parte
    so_nossos = [dict(x) for x in autos if not (x.get('data', '').startswith('2026-09-10T22:22'))]   # copia: nao mexe no original
    so_nossos[-4]['data'] = '2026-09-23T22:34:47+00:00'       # nossos ED antes do despacho
    r2 = embargos(so_nossos, '2026-10-02', datas_de_protocolo(['Protocolado em 23.09.2026_ED']))
    assert r2['titularidade'] == 'CONTRARIA', r2
    # as duas partes embargaram antes do despacho: prazo das duas
    dois = [dict(x) for x in autos]
    dois[7]['data'] = '2026-09-22T22:34:47+00:00'
    r3 = embargos(dois, '2026-10-02', datas_de_protocolo(['Protocolado em 22.09.2026_ED']))
    assert r3['titularidade'] == 'AMBOS' and 'duas partes' in r3['explicacao'], r3
    # sem embargos nos autos: nao chuta
    r4 = embargos([x for x in autos if 'EMBARGOS' not in x.get('descricao', '')], '2026-10-02', [])
    assert r4['titularidade'] == 'INDEFINIDO', r4
    c = consultar(autos, 'MANIFESTACAO SOBRE ED', '', '2026-10-02', nossos)
    assert c['embargos']['titularidade'] == 'NOSSO' and c['cobertura']['peticoes'] == 0 and not c['cobertura']['completa']
    assert c['ultimos'][0]['descricao'].startswith('JUNTADA DE PETIÇÃO DE REQUERIMENTO') and c['ultimos'][0]['n'] == 9
    assert consultar(autos, 'CIENCIA', 'ciencia do despacho', '2026-10-02', [])['embargos'] is None
    # decisao publicada que abre prazo para OPOR embargos: nao ha embargada a procurar
    assert consultar(autos, 'EMBARGOS DE DECLARACAO', 'sentenca publicada; julgo procedente', '2026-10-02', [])['embargos'] is None
    assert consultar(autos, 'CIENCIA', 'intime-se a parte embargada para se manifestar', '2026-10-02', nossos)['embargos']['titularidade'] == 'NOSSO'
    # embargos antigos, ja seguidos de julgamento, nao sao pendentes
    velhos = [{'tipo': 'movimento', 'data': '2025-03-28T21:26:00+00:00', 'descricao': 'Embargos de Declaração Juntados', 'classe': 'peticao'},
              {'tipo': 'movimento', 'data': '2025-03-28T22:00:00+00:00', 'descricao': 'JUNTADA DE PETIÇÃO DE EMBARGOS DE DECLARAÇÃO', 'classe': 'peticao'},
              {'tipo': 'movimento', 'data': '2025-06-10T15:00:00+00:00', 'descricao': 'Decisão', 'classe': 'decisao'}] + autos
    r5 = embargos(velhos, '2026-10-02', nossos)
    assert r5['titularidade'] == 'NOSSO' and [e['quando'][:10] for e in r5['embargos']] == ['10/09/2026', '25/09/2026'], r5
    so_velhos = velhos[:3] + [{'tipo': 'movimento', 'data': '2026-09-24T18:16:16+00:00', 'descricao': 'Despacho', 'classe': 'decisao'}]
    assert embargos(so_velhos, '2026-10-02', [])['titularidade'] == 'INDEFINIDO'
    print('autos_consulta: 18/18')
