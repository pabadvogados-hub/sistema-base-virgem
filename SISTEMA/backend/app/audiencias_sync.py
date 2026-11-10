# -*- coding: utf-8 -*-
"""Audiências conferidas no livro do tribunal (SYNC, andamentos por processo). 07/11/2026, pedido da gestão.

O SYNC traz, em cada processo, os andamentos com o estado escrito:
    "Audiência una (rito sumaríssimo) por videoconferência designada (04/11/2026 09:30 Sala ...)"
    "Audiência una (rito sumaríssimo) por videoconferência cancelada (04/11/2026 09:30 Sala ...)"
    "Audiência inicial por videoconferência realizada (30/06/2026 14:15 SALA - C ...)"
O ÚLTIMO andamento de cada data e hora diz o estado daquela audiência.

Regras (rubber duck aprovado pela Advogada antes de subir):
- Só age no que o tribunal escreveu: designada, cancelada, realizada.
- AUSÊNCIA NÃO É CANCELAMENTO. Data que o SYNC não mostra fica como está e vai para a conferência da Flávia.
- Processo sem andamento de audiência no SYNC (ex.: TJ, CEJUSC) não é tocado.
- "não realizada" nunca é tratada como realizada: vai para a conferência.
- Audiência de julgamento (sentença) não vira compromisso de comparecimento: só aviso.
- Funções puras, testadas em tests/test_audiencias_sync.py. Só `aplicar_tarefas` e `registrar_avisos` tocam o banco.
"""
import datetime as dt
import re
import unicodedata

RX = re.compile(
    r'^\s*(audi[eê]ncia|sess[aã]o)\s+(.*?)\s*'
    r'(n[aã]o[ -]realizada|designada|redesignada|antecipada|mantida|cancelada|realizada|adiada|suspensa|convertida)'
    r'\s*\(\s*(\d{2})/(\d{2})/(\d{4})\s+(\d{2}:\d{2})\s*([^)]*)\)', re.I)


def norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', s).strip().lower()


def eh_so_julgamento(tipo, sessao=False):
    """Só a leitura da sentença, sem gente presente. Na dúvida é audiência de verdade: perder uma custa mais que um lembrete
    a mais. "Sessão" é de tribunal e o cliente não comparece, salvo a de conciliação."""
    n = norm(tipo)
    if sessao:
        return 'concilia' not in n
    return re.fullmatch(r'(de )?julgamento( (por videoconferencia|presencial|telepresencial|virtual|semipresencial|hibrida))?', n) is not None


def ler_andamento(desc):
    """Um andamento de audiência -> dict, ou None se não for andamento de audiência entendível."""
    m = RX.search(desc or '')
    if not m:
        return None
    est = norm(m.group(3))
    if est.startswith('nao'):
        estado = 'nao_realizada'
    elif est in ('designada', 'redesignada', 'antecipada', 'mantida'):
        estado = 'designada'
    elif est in ('cancelada', 'realizada'):
        estado = est
    else:
        estado = 'outro'          # adiada, suspensa, convertida: humano confere
    tipo = m.group(2).strip()
    return {
        'estado': estado,
        'data': '%s-%s-%s' % (m.group(6), m.group(5), m.group(4)),
        'hora': m.group(7),
        'tipo': tipo,
        'local': m.group(8).strip(),
        'julgamento': eh_so_julgamento(tipo, norm(m.group(1)).startswith('sess')),
        'desc': (desc or '').strip(),
    }


def livro(movimentos):
    """{(data, hora): andamento mais recente} a partir da lista de movimentos do SYNC."""
    out = {}
    for mv in sorted(movimentos or [], key=lambda x: str(x.get('data') or '')):
        a = ler_andamento(mv.get('descricao'))
        if a:
            a['quando'] = str(mv.get('data') or '')[:10]
            ant = out.get((a['data'], a['hora']))
            # "mantida" não é nova designação; cancelar e designar de novo é
            a['designada_em'] = ant['designada_em'] if (a['estado'] == 'designada' and ant and ant['estado'] == 'designada') else a['quando']
            out[(a['data'], a['hora'])] = a
    return out


def do_dia(lv, data):
    return {k: v for k, v in (lv or {}).items() if k[0] == data}


def vale_no_dia(dia):
    """O andamento que manda no dia. Havendo audiência designada, ela vence: o tribunal pode cancelar um
    horário e designar outro no mesmo dia. Empate na data do andamento: fica o horário mais cedo."""
    ativas = [v for v in dia.values() if v['estado'] == 'designada' and not v['julgamento']]
    if ativas:
        ultimo = max(v['quando'] for v in ativas)
        return min((v for v in ativas if v['quando'] == ultimo), key=lambda v: v['hora'])
    return sorted(dia.values(), key=lambda x: (x['quando'], x['hora']))[-1]


def designadas_futuras(lv, hoje, julgamento=False):
    return sorted([v for (d, h), v in (lv or {}).items()
                   if v['estado'] == 'designada' and d >= hoje and v['julgamento'] == julgamento],
                  key=lambda v: (v['data'], v['hora']))


# ------------------------------------------------------------------ 1. lista do vigia
def ajustar_auds(auds, livros, hoje):
    """Corrige a lista consolidada do vigia pelo livro do tribunal.

    Devolve (auds_corrigida, novas_do_sync, julgamentos, conferir).
      - data que o tribunal diz cancelada ou realizada sai da lista;
      - data designada no tribunal ganha a hora do tribunal;
      - data que o tribunal não mostra FICA e vai para conferir (ausência não é cancelamento);
      - designada no tribunal que a lista não tem volta em novas_do_sync (quem chama monta o dict);
      - audiência de julgamento não entra: volta em julgamentos.
    """
    saida, conferir = [], []
    for a in auds:
        lv = livros.get(a['processo'])
        if not lv:
            saida.append(a)
            continue
        dia = do_dia(lv, a['data'])
        if not dia:
            conferir.append({'processo': a['processo'], 'data': a['data'],
                             'motivo': 'o tribunal (SYNC) não mostra audiência nesta data'})
            saida.append(a)
            continue
        ativas = [v for v in dia.values() if v['estado'] == 'designada' and not v['julgamento']]
        if ativas:
            v = vale_no_dia(dia)
            b = dict(a)
            b['hora'] = v['hora']
            b['fontes'] = list(dict.fromkeys(list(a.get('fontes') or []) + ['sync']))
            saida.append(b)
            continue
        estados = {v['estado'] for v in dia.values()}
        if estados & {'nao_realizada', 'outro'}:
            conferir.append({'processo': a['processo'], 'data': a['data'],
                             'motivo': 'andamento do tribunal: %s' % ', '.join(sorted(estados))})
            saida.append(a)
        # cancelada / realizada / só julgamento: sai da lista
    ja = {(a['processo'], a['data']) for a in saida}
    novas, julgamentos = [], []
    for proc, lv in livros.items():
        for v in designadas_futuras(lv, hoje):
            if (proc, v['data']) not in ja:
                novas.append(dict(v, processo=proc))
        for v in designadas_futuras(lv, hoje, julgamento=True):
            julgamentos.append(dict(v, processo=proc))
    return saida, novas, julgamentos, conferir


def horas_ativas(dia):
    return sorted({v['hora'] for v in dia.values() if v['estado'] == 'designada' and not v['julgamento']})


def vale_para_a_tarefa(dia, hora):
    """Duas audiências reais no mesmo dia: cada tarefa segue o andamento do horário dela, salvo se ele foi cancelado."""
    v = next((x for (_, h), x in dia.items() if h == hora), None)
    return v if v and v['estado'] != 'cancelada' else vale_no_dia(dia)


def conflitos_de_horario(auds, livros):
    """09/11/2026: horário EXATAMENTE igual em processos diferentes. Pede-se a redesignação da agendada
    por último. Só aponta, para a conferência da Flávia: não mexe em nada. Sem a data da designação de todas,
    ou com empate, não escolhe. Devolve dicts processo, data, motivo."""
    grupos = {}
    for a in auds:
        if a.get('hora'):
            grupos.setdefault((a['data'], a['hora']), set()).add(a['processo'])
    saida = []
    for (data, hora), procs in sorted(grupos.items()):
        if len(procs) < 2:
            continue
        quando = {p: ((livros.get(p) or {}).get((data, hora)) or {}).get('designada_em') for p in procs}
        datas = [d for d in quando.values() if d]
        ultima = max(datas) if len(datas) == len(procs) else None
        escolhidos = [p for p in procs if ultima and quando[p] == ultima]
        if len(escolhidos) == 1:
            alvo = escolhidos[0]
            outros = ', '.join(sorted(procs - {alvo}))
            saida.append({'processo': alvo, 'data': data,
                          'motivo': 'mesmo horário (%s) do processo %s. Pela regra, pede-se a redesignação da agendada '
                                    'por último: esta, designada em %s. Antes, conferir se a Dra. Advogada pode fazer uma delas.'
                                    % (hora, outros, br(ultima))})
        else:
            alvo = sorted(procs)[0]
            outros = ', '.join(sorted(procs - {alvo}))
            saida.append({'processo': alvo, 'data': data,
                          'motivo': 'mesmo horário (%s) do processo %s. O tribunal (SYNC) não mostra qual foi agendada '
                                    'por último: conferir qual antes de pedir a redesignação.' % (hora, outros)})
    return saida


# ------------------------------------------------------------------ 2. registro
def plano_registro(linhas, livros, hoje):
    """Status do audiencia_registro pelo livro. linhas: dicts com processo, data, status, editado_local.
    Devolve [(processo, data, status_novo, motivo)]."""
    plano = []
    for r in linhas:
        lv = livros.get(r['processo'])
        if not lv:
            continue
        dia = do_dia(lv, r['data'])
        if not dia:
            continue
        v = vale_no_dia(dia)
        atual = r['status']
        if v['estado'] == 'realizada':
            novo = 'realizada'
        elif v['estado'] == 'cancelada':
            novo = 'substituida' if designadas_futuras(lv, hoje) else 'cancelada'
        elif v['estado'] == 'designada' and not v['julgamento']:
            novo = 'ativa'
            if r.get('editado_local') and atual in ('cancelada', 'realizada'):
                continue          # a equipe mexeu à mão: não desfaz, só a conferência avisa
        else:
            continue
        if novo != atual:
            plano.append((r['processo'], r['data'], novo,
                          'andamento do tribunal de %s: %s' % (v['quando'], v['desc'][:150])))
    return plano


# ------------------------------------------------------------------ 3. aba de tarefas
def eh_tarefa_de_audiencia(tipo):
    return norm(tipo).startswith('audiencia')


def br(iso):
    return '%s/%s/%s' % (iso[8:10], iso[5:7], iso[:4]) if iso else ''


def plano_tarefas(tarefas, livros, hoje):
    """tarefas: dicts com id, tipo, data, hora, processo, evento_id. Só as abertas de audiência.
    Devolve ações: dict(acao, tarefa_id, status?, data?, hora?, comentario)."""
    acoes = []
    tarefas = [t for t in tarefas if eh_tarefa_de_audiencia(t.get('tipo')) and t.get('processo') and t.get('data')]

    # duplicatas: mesma audiência (processo e data) em mais de uma tarefa
    grupos = {}
    for t in tarefas:
        grupos.setdefault((t['processo'], t['data']), []).append(t)
    sobra = []
    for chave, ts in grupos.items():
        # fica a cópia de quem ainda tem a tarefa pendente (rubber duck 07/11: a do dia 26/11 sem
        # responsável ia ficar e a da responsável ia fechar); depois a que tem hora, evento e o menor número
        ts = sorted(ts, key=lambda t: (-len(t.get('resp') or ()), 0 if t.get('hora') else 1,
                                       0 if t.get('evento_id') else 1, t['id']))
        ficam = []
        for d in ts:
            # só é repetição com a mesma hora ou com a hora faltando: horários diferentes são audiências diferentes
            # com dois horários designados no dia, hora faltando não basta: vai para a conferência lá embaixo
            varias = len(horas_ativas(do_dia(livros.get(chave[0]), chave[1]))) >= 2
            fica = next((f for f in ficam if (f.get('hora') and f.get('hora') == d.get('hora'))
                         or (not varias and (not f.get('hora') or not d.get('hora')))), None)
            if fica is None:
                ficam.append(d)
                sobra.append(d)
                continue
            if set(d.get('resp') or ()) <= set(fica.get('resp') or ()):
                acoes.append({'acao': 'duplicada', 'tarefa_id': d['id'], 'status': 'cancelada',
                              'comentario': 'Fechada pelo vigia de audiências: duplicada da tarefa #%d '
                                            '(mesma audiência de %s).' % (fica['id'], br(chave[1]))})
            else:
                acoes.append({'acao': 'conferir', 'tarefa_id': d['id'],
                              'comentario': 'Repete a audiência da tarefa #%d (%s), mas tem outra pessoa '
                                            'responsável. Juntar à mão.' % (fica['id'], br(chave[1]))})

    abertas_por_data = {(t['processo'], t['data']) for t in sobra}
    for t in sobra:
        lv = livros.get(t['processo'])
        if not lv:
            continue
        dia = do_dia(lv, t['data'])
        if not dia:
            acoes.append({'acao': 'conferir', 'tarefa_id': t['id'],
                          'comentario': 'O tribunal (SYNC) não mostra audiência em %s neste processo.' % br(t['data'])})
            continue
        ativas = horas_ativas(dia)
        if len(ativas) >= 2:
            cobertas = {x.get('hora') for x in sobra if (x['processo'], x['data']) == (t['processo'], t['data'])}
            certo = t.get('hora') in ativas if set(ativas) <= cobertas else t.get('hora') == vale_no_dia(dia)['hora']
            if certo:
                continue
            acoes.append({'acao': 'conferir', 'tarefa_id': t['id'],
                          'comentario': 'O tribunal mostra mais de um horário designado em %s (%s). Conferir qual é o desta tarefa.'
                                        % (br(t['data']), ', '.join(ativas))})
            continue
        v = vale_para_a_tarefa(dia, t.get('hora'))
        if v['estado'] == 'designada' and not v['julgamento']:
            if t.get('hora') != v['hora']:
                acoes.append({'acao': 'hora', 'tarefa_id': t['id'], 'hora': v['hora'],
                              'comentario': 'Horário conferido no tribunal (SYNC): %s.' % v['hora']})
            continue
        if v['estado'] == 'realizada':
            # 09/11/2026: a responsável confere antes. A tarefa continua aberta, só ganha o comentário.
            acoes.append({'acao': 'realizada', 'tarefa_id': t['id'],
                          'comentario': 'O tribunal registrou a audiência de %s como REALIZADA (andamento de %s, SYNC). '
                                        'Responsável, confira e conclua a tarefa.' % (br(t['data']), br(v['quando']))})
            continue
        if v['estado'] == 'cancelada':
            novas = [n for n in designadas_futuras(lv, hoje) if (t['processo'], n['data']) not in abertas_por_data]
            if len(novas) == 1:
                n = novas[0]
                acoes.append({'acao': 'mover', 'tarefa_id': t['id'], 'data': n['data'], 'hora': n['hora'],
                              'comentario': 'Audiência REMARCADA pelo tribunal: de %s para %s às %s '
                                            '(andamento de %s, SYNC). Avisar o cliente da nova data (POP 18).'
                                            % (br(t['data']), br(n['data']), n['hora'], br(v['quando']))})
                abertas_por_data.add((t['processo'], n['data']))
            elif len(novas) > 1:
                acoes.append({'acao': 'conferir', 'tarefa_id': t['id'],
                              'comentario': 'O tribunal cancelou %s e designou %d datas novas (%s). Conferir qual é a desta tarefa.'
                                            % (br(t['data']), len(novas), ', '.join(br(n['data']) for n in novas))})
            else:
                acoes.append({'acao': 'fechar', 'tarefa_id': t['id'], 'status': 'cancelada',
                              'comentario': 'Audiência de %s CANCELADA pelo tribunal (andamento de %s, SYNC).%s'
                                            % (br(t['data']), br(v['quando']),
                                               ' Nova data já tem tarefa própria.' if designadas_futuras(lv, hoje)
                                               else ' Sem nova data no tribunal.')})
            continue
        acoes.append({'acao': 'conferir', 'tarefa_id': t['id'],
                      'comentario': 'Andamento do tribunal: %s. Conferir.' % v['desc'][:150]})
    return acoes


def aplicar_tarefas(con, acoes, agora_ts):
    """Grava as ações na aba de tarefas. 'conferir' não muda a tarefa (vai só para o resumo da Flávia).
    atualizado_em = agora marca a tarefa como mexida aqui: o espelho do ADVBOX não desfaz."""
    feitas = 0
    for a in acoes:
        if a['acao'] == 'conferir':
            continue
        if a['acao'] in ('fechar', 'duplicada'):
            con.execute("UPDATE tarefas SET status=?, concluida_em=?, atualizado_em=? WHERE id=? AND status='aberta'",
                        (a['status'], agora_ts, agora_ts, a['tarefa_id']))
        elif a['acao'] == 'mover':
            con.execute("UPDATE tarefas SET data=?, hora=?, atualizado_em=? WHERE id=? AND status='aberta'",
                        (a['data'], a['hora'], agora_ts, a['tarefa_id']))
        elif a['acao'] == 'hora':
            con.execute("UPDATE tarefas SET hora=?, atualizado_em=? WHERE id=? AND status='aberta'",
                        (a['hora'], agora_ts, a['tarefa_id']))
        elif a['acao'] != 'realizada':          # 'realizada' só comenta: quem conclui é a responsável
            continue
        con.execute("INSERT INTO tarefa_comentarios (tarefa_id, texto, por_id, por_nome, para, tipo, criado_em) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (a['tarefa_id'], a['comentario'], None, 'Vigia de audiências (SYNC)', None, 'COMENTARIO', agora_ts))
        feitas += 1
    con.commit()
    return feitas


# ------------------------------------------------------------------ 4. resumo para a Flávia
def garantir_avisos(con):
    con.execute("CREATE TABLE IF NOT EXISTS audiencia_sync_avisos (chave TEXT PRIMARY KEY, criado_em TEXT)")


def so_novos(con, itens):
    """Itens de conferência entram no resumo uma vez só (não repetir todo dia o mesmo aviso)."""
    garantir_avisos(con)
    out = []
    for chave, texto in itens:
        if not con.execute("SELECT 1 FROM audiencia_sync_avisos WHERE chave=?", (chave,)).fetchone():
            out.append((chave, texto))
    return out


def sem_realizada_repetida(con, acoes):
    """O comentário de audiência realizada entra uma vez só, não a cada conferência."""
    chave = lambda a: 'realizada|%d' % a['tarefa_id']
    novas = {k for k, _ in so_novos(con, [(chave(a), a['comentario']) for a in acoes if a['acao'] == 'realizada'])}
    return [a for a in acoes if a['acao'] != 'realizada' or chave(a) in novas]


def registrar_avisos(con, itens, agora_txt):
    garantir_avisos(con)
    for chave, _ in itens:
        con.execute("INSERT OR IGNORE INTO audiencia_sync_avisos VALUES (?,?)", (chave, agora_txt))
    con.commit()


def texto_resumo(mudancas, conferir, julgamentos):
    """mudancas/conferir/julgamentos: listas de linhas prontas. Vazio -> None (não abre tarefa)."""
    if not (mudancas or conferir or julgamentos):
        return None
    partes = ['O vigia conferiu as audiências no tribunal (SYNC) e encontrou:']
    if mudancas:
        partes += ['', 'MUDANÇAS FEITAS (confira; remarcada pede aviso ao cliente pelo POP 18):'] + ['- ' + m for m in mudancas]
    if conferir:
        partes += ['', 'PARA CONFERIR (o vigia não mexeu):'] + ['- ' + c for c in conferir]
    if julgamentos:
        partes += ['', 'AUDIÊNCIA DE JULGAMENTO (sentença; não entra nos lembretes ao cliente):'] + ['- ' + j for j in julgamentos]
    return '\n'.join(partes)
