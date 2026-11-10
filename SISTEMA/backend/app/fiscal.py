# -*- coding: utf-8 -*-
"""O FISCAL: olha a operação inteira e aponta o que vai dar problema, antes de dar.

07/11/2026: "sempre coloque um revisor para avaliar que está tudo fluindo bem, inclua um
agente fiscal do trabalho para antecipar e mitigar os problemas".

Não é um painel de números: é uma lista de coisas ERRADAS, cada uma com o que fazer. Toda
verificação aqui nasceu de um problema que aconteceu de verdade, não de hipótese:

| verificação | de onde veio |
|---|---|
| card duplicado | 07/11: 185 números com dois cards, 5 com cliente fechado (Nivaldo, Otoniel) |
| fechado sem nome | 07/11: o Galdino, que a Advogada não achou no sistema |
| telefone invisível | 07/11: 2.113 cards que a busca não encontrava |
| produto divergente | 06/11: o Ícaro, campanha de GR fechando trabalhista |
| parado esperando documento | a régua do GR: 4 dias sem CNH e o caso congela |
| tarefa velha | 07/11: tarefa de ligar aberta desde 03/11 sem ninguém ver |
| esteira parada | se o cron morre, ninguém percebe até o cliente reclamar |

REGRA DE OURO: o fiscal NÃO conserta nada e NÃO fala com cliente nenhum. Ele aponta, com a
providência escrita, e quem resolve é gente. Motor sugere, humano conclui.
"""
import os
import re
import sqlite3
import time

QUADROS_FECHAMENTO = (11, 18, 20)
ALTA, MEDIA, BAIXA = 'alta', 'media', 'baixa'


def _fechado(pipeline_id, etapa=''):
    return (pipeline_id in QUADROS_FECHAMENTO
            or str(etapa or '').startswith('Fechou'))


def _dict(r):
    return {k: r[k] for k in r.keys()}


def achado(tipo, gravidade, titulo, providencia, **extra):
    d = {'tipo': tipo, 'gravidade': gravidade, 'titulo': titulo,
         'providencia': providencia}
    d.update(extra)
    return d


# --------------------------------------------------------------------------- 1. duplicata

def cards_duplicados(con):
    """O mesmo telefone em mais de um card: a conversa do cliente partida em dois.

    É o pior dos defeitos silenciosos: a vendedora atende num card, a esteira anda no outro,
    e o cliente recebe mensagem repetida ou nenhuma. Medido em 07/11: 185 números, e em 5
    deles um dos cards é cliente fechado.
    """
    from . import busca_contato as bc
    grupos = {}
    for r in con.execute(
            "select id, coalesce(contato_telefone,'') tel, coalesce(contato_nome,'') nome, "
            "coalesce(pipeline_id,0) q, coalesce(etapa,'') etapa, coalesce(criado_em,'') c "
            "from wa_conversas where coalesce(contato_telefone,'')<>''"):
        v = bc.variantes(r['tel'])
        if not v:
            continue
        grupos.setdefault(max(v, key=len), []).append(_dict(r))

    fora = []
    for numero, cards in grupos.items():
        if len(cards) < 2:
            continue
        # par JA JUNTADO nao volta a ser apontado: juntar nao apaga o card secundario
        # (regra da casa desde 03/11), ele fica marcado, e o fiscal entende a marca.
        # Sem isto o mesmo par aparece todo dia e a equipe aprende a ignorar o relatorio.
        if any(str(c['nome'] or '').startswith('[DUPLICADO') for c in cards):
            continue
        tem_fechado = any(_fechado(c['q'], c['etapa']) for c in cards)
        ids = sorted(c['id'] for c in cards)
        quem = next((c['nome'] for c in cards if c['nome'] and not c['nome'].isdigit()), '')
        fora.append(achado(
            'card-duplicado', ALTA if tem_fechado else BAIXA,
            '%s tem %d cards com o mesmo telefone (%s)'
            % (quem or numero, len(cards), ', '.join('#%s' % i for i in ids)),
            'Juntar num card só: manter o que tem o histórico e a esteira andando, e fechar '
            'o outro. Enquanto houver dois, o cliente pode receber mensagem em dobro.'
            if tem_fechado else
            'Conferir quando sobrar tempo: lead repetido, sem fechamento envolvido.',
            telefone=numero, conversa_id=ids[0], cards=ids,
            detalhe=[{'id': c['id'], 'nome': c['nome'], 'quadro': c['q'],
                      'etapa': c['etapa']} for c in sorted(cards, key=lambda x: x['id'])]))
    return sorted(fora, key=lambda x: (x['gravidade'] != ALTA, x['conversa_id']))


# --------------------------------------------------------------------------- 2. sem nome

def fechado_sem_nome(con):
    """Cliente fechado cujo card não mostra o nome de uma pessoa.

    Foi assim que a Advogada não achou o Galdino: o card dizia 557399812345.
    """
    from . import nome_card as nc
    fora = []
    for r in con.execute(
            "select id, coalesce(contato_nome,'') nome, coalesce(contato_telefone,'') tel, "
            "coalesce(pipeline_id,0) q, coalesce(etapa,'') etapa from wa_conversas "
            "where pipeline_id in (%s) or coalesce(etapa,'') like 'Fechou%%'"
            % ','.join(map(str, QUADROS_FECHAMENTO))):
        if nc.nome_ilegivel(r['nome']):
            fora.append(achado(
                'fechado-sem-nome', ALTA,
                'Cliente fechado sem nome no card: #%s mostra "%s"' % (r['id'], r['nome']),
                'Se ele já mandou CNH, a esteira põe o nome sozinho na próxima rodada. Se '
                'nunca mandou, só quem falou com ele sabe: digitar o nome na ficha.',
                conversa_id=r['id'], telefone=r['tel'], quadro=r['q'], etapa=r['etapa']))
    return fora


# --------------------------------------------------------------------------- 3. invisível

def busca_por_telefone_intacta(caminho=None):
    """O conserto da busca por telefone ainda está no código?

    MUDOU DE IDEIA EM 07/11/2026, e vale registrar por quê. A primeira versão listava os
    2.113 cards gravados sem o nono dígito, um achado por card, com a providência dizendo
    "nenhuma ação". Um teste pegou: fiscal que aponta o que não precisa de ação é ruído, e
    ruído ensina a equipe a ignorar o fiscal inteiro.

    O que interessa não é o card: é se alguém desfez o conserto. Uma linha removida de
    `atendimento.py` e 2.113 clientes voltam a "não existir" na busca, em silêncio. Então a
    verificação olha o código, e só fala quando há o que fazer.
    """
    caminho = caminho or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      'routers', 'atendimento.py')
    if not os.path.exists(caminho):
        return [achado('busca-telefone', ALTA,
                       'Não achei %s para conferir a busca por telefone' % caminho,
                       'Conferir se o arquivo mudou de lugar; sem ele não sei dizer se a '
                       'busca ainda acha cliente com telefone sem o nono dígito.')]
    try:
        with open(caminho, encoding='utf-8', errors='replace') as fh:
            texto = fh.read()
    except OSError as e:
        return [achado('busca-telefone', MEDIA,
                       'Não consegui ler %s: %s' % (caminho, str(e)[:60]),
                       'Conferir permissão do arquivo.')]
    if 'busca_contato.padroes_like' in texto:
        return []
    return [achado(
        'busca-telefone', ALTA,
        'A busca por telefone voltou a procurar só o número inteiro',
        'Devolver a chamada a busca_contato.padroes_like em atendimento.py. Sem ela, os '
        '2.113 cards gravados sem o nono dígito somem da busca e a equipe conclui que o '
        'cliente não existe, como aconteceu com o Galdino em 07/11/2026.',
        arquivo=caminho)]


# --------------------------------------------------------------------------- 4. produto

def produto_divergente(con):
    """Fechamento recente cujo produto discorda do quadro, sem ninguém ter trocado."""
    from . import produto_card as pc
    if not pc.tem_coluna_produto(con):
        return []
    fora = []
    for d in pc.cards_com_produto_divergente(con, dias=30):
        fora.append(achado(
            'produto-divergente', MEDIA,
            '#%s (%s) está marcado como %s mas o card está no quadro de %s'
            % (d['conversa_id'], (d['nome'] or 'sem nome')[:30], d['produto'],
               d['produto_do_quadro']),
            'Conferir com quem fechou e corrigir o produto na ficha do card, ou mover o '
            'card para o quadro certo. O custo por campanha depende disso.',
            conversa_id=d['conversa_id'], **{k: d[k] for k in ('produto', 'etapa')}))
    return fora


# --------------------------------------------------------------------------- 5. parado

def parado_esperando_documento(con, dias=4, ate=90):
    """Fechou, a esteira pediu documento, e o cliente não mandou nada há `dias`.

    A régua do GR já liga para ele no 4º dia útil. O fiscal existe para o caso de a régua
    não ter rodado, ou de o caso ter escapado dela.
    """
    fora = []
    for r in con.execute(
            "select v.id, coalesce(v.contato_nome,'') nome, coalesce(v.pipeline_id,0) q, "
            "coalesce(v.etapa,'') etapa, coalesce(v.atualizado_em,'') atz, "
            "(select count(*) from wa_mensagens m where m.conversa_id=v.id "
            " and m.origem='contato' and m.tipo in ('imagem','doc','documento')) docs, "
            "(select max(criado_em) from wa_mensagens m where m.conversa_id=v.id "
            " and m.origem='contato') ultima "
            "from wa_conversas v where (v.pipeline_id in (%s) "
            "or coalesce(v.etapa,'') like 'Fechou%%')"
            % ','.join(map(str, QUADROS_FECHAMENTO))):
        if r['docs']:
            continue
        # CLIENTE COM PROCESSO ATIVO NAO ESTA PARADO (achado em 07/11/2026): 11 dos 24 que
        # este alarme acusava estavam no quadro de Clientes, etapa "Processo Ativo", alguns
        # ha 68 dias. O caso deles esta andando no tribunal; eles nao devem documento
        # nenhum. Aqui so entra quem esta na FASE de contratacao e documentos.
        etapa_baixa = str(r['etapa'] or '').lower()
        if any(x in etapa_baixa for x in ('processo ativo', 'distribu', 'acompanhamento',
                                          'arquivad', 'sentenc', 'audiencia')):
            continue
        ultima = str(r['ultima'] or '')[:10]
        if not ultima:
            continue
        try:
            quando = time.mktime(time.strptime(ultima, '%Y-%m-%d'))
        except ValueError:
            continue
        parado = int((time.time() - quando) / 86400)
        if parado < dias or parado > ate:
            # acima de `ate` nao e antecipar problema, e arqueologia: ninguem vai ligar para
            # quem sumiu ha quatro meses, e cada linha dessas empurra o que importa para fora
            # da tela. Calibrado em 07/11/2026, quando a primeira rodada trouxe 1.232 pontos.
            continue
        if 'testemunha' in (r['nome'] or '').lower():
            continue              # testemunha de processo nao e venda esperando documento
        # quem JA tem tarefa aberta tem alguem cuidando: registra, mas nao acorda ninguem
        try:
            cuidando = con.execute("select count(*) from tarefas where conversa_id=? "
                                   "and status='aberta'", (r['id'],)).fetchone()[0]
        except sqlite3.Error:
            cuidando = 0
        fora.append(achado(
            'parado-sem-documento',
            MEDIA if cuidando else (ALTA if parado >= 7 else MEDIA),
            '%s (#%s) fechou e está há %d dias sem mandar documento'
            % ((r['nome'] or 'sem nome')[:30], r['id'], parado),
            ('Já há tarefa aberta para este caso: conferir se alguém está tocando.'
             if cuidando else
             'Ligar. A esteira já cobrou pelo WhatsApp e ele não respondeu com documento, '
             'e não há tarefa aberta para ele: ninguém está cuidando.'),
            conversa_id=r['id'], dias_parado=parado, etapa=r['etapa'],
            tem_tarefa=bool(cuidando)))
    return sorted(fora, key=lambda x: -x['dias_parado'])


# --------------------------------------------------------------------------- 6. tarefa velha

def tarefa_esquecida(con, dias=5, ate=45, limite=40):
    """Tarefa aberta há muito tempo: alguém pediu e ninguém pegou."""
    fora = []
    try:
        linhas = con.execute(
            "select t.id, coalesce(t.titulo,'') titulo, coalesce(t.data,'') data, "
            "coalesce(t.conversa_id,0) cid, coalesce(r.nome,'(sem dono)') dono "
            "from tarefas t left join tarefa_responsaveis r on r.tarefa_id=t.id "
            "where t.status='aberta' and coalesce(t.data,'') <> '' "
            "and date(t.data) <= date('now', ?) and date(t.data) >= date('now', ?) "
            "order by t.data desc limit ?",
            ('-%d day' % int(dias), '-%d day' % int(ate), int(limite))).fetchall()
    except sqlite3.Error:
        return []
    for r in linhas:
        fora.append(achado(
            'tarefa-esquecida', MEDIA,
            'Tarefa #%s aberta desde %s com %s: %s'
            % (r['id'], str(r['data'])[:10], r['dono'], r['titulo'][:60]),
            'Concluir ou repassar. Tarefa velha some da vista e vira cliente sem resposta.',
            conversa_id=r['cid'] or None, tarefa_id=r['id'], desde=str(r['data'])[:10]))
    return fora


# --------------------------------------------------------------------------- 7. esteira parada

def esteira_parada(logs=None, minutos=90, so_quando_age=None, folga=360):
    """Um cron que morreu não avisa ninguém: o log simplesmente para de crescer."""
    logs = logs or {
        'esteira do GR': '/var/log/escritorio-gr-contratacao.log',
        'esteira do trabalhista': '/var/log/escritorio-contratacao.log',
        'vigia do front': '/var/log/escritorio-frontend-vigia.log',
        'quadro do GR': '/var/log/escritorio-quadro-gr.log',
    }
    if so_quando_age is None:
        so_quando_age = {'quadro do GR'}
    fora = []
    agora = time.time()
    for nome, caminho in logs.items():
        if not os.path.exists(caminho):
            fora.append(achado('esteira-parada', ALTA,
                               'O log da %s não existe (%s)' % (nome, caminho),
                               'Conferir se o cron ainda chama essa esteira.',
                               log=caminho))
            continue
        parado = int((agora - os.path.getmtime(caminho)) / 60)
        # ESTEIRA QUE SO FALA QUANDO AGE (achado em 07/11/2026): o quadro do GR roda a cada
        # minuto, mas so escreve no log quando move um card. Num sabado a tarde fica horas
        # calado, e isso e normal. Para essas vale a `folga`, bem maior, que ainda pega o
        # caso de ela estar morta de verdade.
        limite = folga if (so_quando_age and nome in so_quando_age) else minutos
        if parado > limite:
            fora.append(achado(
                'esteira-parada', ALTA,
                'A %s não escreve no log há %d minutos' % (nome, parado),
                'Rodar à mão uma vez e olhar o erro. Enquanto ela estiver parada, nenhum '
                'cliente dessa esteira recebe nada.',
                log=caminho, minutos_parado=parado))
    return fora


# --------------------------------------------------------------------------- o fiscal

VERIFICACOES = (
    ('cards duplicados', cards_duplicados),
    ('cliente fechado sem nome', fechado_sem_nome),
    ('produto divergente do quadro', produto_divergente),
    ('parado sem documento', parado_esperando_documento),
    ('tarefa esquecida', tarefa_esquecida),
)


def rodar(con, incluir_esteiras=True):
    """Passa todas as verificações e devolve os achados, do mais grave para o menos.

    Cada verificação é isolada: uma que quebre não derruba as outras, porque o fiscal que só
    funciona quando está tudo bem não serve para nada.
    """
    achados, erros = [], []
    for nome, fn in VERIFICACOES:
        try:
            achados.extend(fn(con))
        except Exception as e:
            erros.append({'verificacao': nome, 'erro': '%s: %s' % (type(e).__name__,
                                                                   str(e)[:120])})
    try:
        achados.extend(busca_por_telefone_intacta())
    except Exception as e:
        erros.append({'verificacao': 'busca por telefone', 'erro': str(e)[:120]})
    if incluir_esteiras:
        try:
            achados.extend(esteira_parada())
        except Exception as e:
            erros.append({'verificacao': 'esteira parada', 'erro': str(e)[:120]})
    ordem = {ALTA: 0, MEDIA: 1, BAIXA: 2}
    achados.sort(key=lambda a: (ordem.get(a['gravidade'], 9), a['tipo']))
    return {'achados': achados, 'erros': erros,
            'resumo': resumo(achados), 'quando': time.strftime('%Y-%m-%d %H:%M:%S')}


def resumo(achados):
    por_gravidade, por_tipo = {}, {}
    for a in achados:
        por_gravidade[a['gravidade']] = por_gravidade.get(a['gravidade'], 0) + 1
        por_tipo[a['tipo']] = por_tipo.get(a['tipo'], 0) + 1
    return {'total': len(achados), 'por_gravidade': por_gravidade, 'por_tipo': por_tipo}


def em_texto(saida, limite_por_tipo=8, gravidades=(ALTA, MEDIA)):
    """O relatório como a Advogada lê: o que pede ação primeiro, o resto em uma linha.

    Por padrão mostra alta e média, e resume as baixas. A primeira rodada real trouxe 1.232
    pontos e não cabia em tela nenhuma; calibrado em 07/11/2026. Um fiscal que ninguém lê é
    um fiscal que não existe.
    """
    L = ['FISCAL DA OPERAÇÃO · %s' % saida['quando'], '']
    r = saida['resumo']
    if not r['total'] and not saida['erros']:
        L.append('Nada a apontar: as verificações passaram todas.')
        return '\n'.join(L)
    if not r['total']:
        # NUNCA dizer "está tudo bem" quando o fiscal quebrou: um teste pegou isto em
        # 07/11/2026, com todas as verificações falhando e o relatório saindo limpo. Falso
        # "tudo certo" é pior do que não ter fiscal, porque ninguém vai conferir depois.
        L.append('Nenhum ponto encontrado, MAS o fiscal falhou nas verificações abaixo: '
                 'o silêncio aqui não quer dizer que está tudo bem.')
    L.append('%d pontos: %s' % (r['total'], ', '.join(
        '%d %s' % (n, g) for g, n in sorted(r['por_gravidade'].items()))))

    mostrar = [a for a in saida['achados'] if a['gravidade'] in gravidades]
    fora = [a for a in saida['achados'] if a['gravidade'] not in gravidades]
    vistos, total_por_tipo = {}, {}
    for a in mostrar:
        total_por_tipo[a['tipo']] = total_por_tipo.get(a['tipo'], 0) + 1
    for a in mostrar:
        vistos[a['tipo']] = vistos.get(a['tipo'], 0) + 1
        if vistos[a['tipo']] == 1:
            L.append('')
            L.append('--- %s (%d)' % (a['tipo'].upper().replace('-', ' '),
                                      total_por_tipo[a['tipo']]))
        if vistos[a['tipo']] > limite_por_tipo:
            continue
        L.append('  [%s] %s' % (a['gravidade'].upper(), a['titulo']))
        L.append('        → %s' % a['providencia'])
    for tipo, n in sorted(vistos.items()):
        if n > limite_por_tipo:
            L.append('')
            L.append('  (%s: mostrei os %d primeiros de %d)'
                     % (tipo.replace('-', ' '), limite_por_tipo, n))
    if fora:
        por_tipo = {}
        for a in fora:
            por_tipo[a['tipo']] = por_tipo.get(a['tipo'], 0) + 1
        L.append('')
        L.append('--- SEM PRESSA (%d): %s' % (
            len(fora), ', '.join('%d %s' % (n, t.replace('-', ' '))
                                 for t, n in sorted(por_tipo.items()))))
    if saida['erros']:
        L.append('')
        L.append('--- VERIFICAÇÕES QUE FALHARAM (o fiscal avisa quando ele mesmo falha)')
        for e in saida['erros']:
            L.append('  %s: %s' % (e['verificacao'], e['erro']))
    return '\n'.join(L)
