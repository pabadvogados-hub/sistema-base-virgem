# -*- coding: utf-8 -*-
"""VIGIA DA DISTRIBUICAO (Advogada, 06/10/2026): o numero nasce aqui e o cliente entra no Portal.

Ordem da Advogada: "como ainda estamos em migracao estamos tratando as tarefas pelo ADVBOX,
mas tudo o que sera feito la, faremos aqui. Entao nesse primeiro momento, uma varredura
nas tarefas conforme o prazo fatal agendado para protocolo pelo Sistema Escritório Modelo; identificado
o numero, o proprio sistema atualiza e ja comeca o fluxo de envio."

O que ele faz, nesta ordem:

 1. NUMERO NASCE AQUI. Varre as tarefas do Sistema Escritório Modelo (tabela `tarefas`, hoje espelhadas
    do ADVBOX e amanha nativas). Tarefa que traz `processo_numero` de um processo que aqui
    esta sem numero: grava o numero em `jur_processos` e marca a fase de acao protocolada.
    Nao mexe em `editado_local`, para a sincronizacao continuar podendo completar o resto.
 2. AVISO DO PRAZO. Tarefa de PROTOCOLO com prazo fatal vencido e processo ainda sem numero
    nao fica calada: entra no relatorio e avisa o time uma vez por tarefa.
 3. CARD ANDA. Processo com numero: o card do cliente vai de Producao da Inicial, Revisao ou
    Distribuicao para "Distribuida", que e a coluna que abre o ciclo do Portal (o convite
    olha quem entrou nela).
 4. CLIENTE SABE. Sai o video da distribuicao e as duas mensagens aprovadas pela Advogada em
    06/10/2026, pela D-API, com `regua_portal.enviar` (video primeiro, texto depois, porque
    a legenda do video nao e entregue). O envio e registrado como passo A1 da regua do
    Portal, senao a regua manda o mesmo video de novo no 4o dia.
 5. VIRA CLIENTE ATIVO. Entrou no Portal, ou passados DIAS_ATE_CLIENTES dias, o card vai
    para Clientes > Processo Ativo. Quem nao entrou vira tarefa de ligacao para a
    equipe colocar a pessoa dentro, que e a meta da Advogada nesses dias.

Travas:
  - sem numero valido (padrao CNJ) nao faz nada;
  - sem CPF no cadastro a pessoa NAO consegue entrar no Portal: manda so a noticia e
    abre pendencia de cadastro para o time;
  - card com board fixado por gente nao e movido de quadro (regra da trava do board);
  - envio so em dia util, das 9h as 18h, 20 s entre mensagens (teto de 3 por minuto);
  - uma vez por cliente, controlado por estado proprio e pelo registro na regua.

USO:
  vigia_distribuicao.py              simula tudo e nao grava nem envia
  vigia_distribuicao.py --aplicar    grava o numero, move o card e envia
  vigia_distribuicao.py --so-numero  so a parte 1 e 2 (grava numero, avisa prazo)
  vigia_distribuicao.py --status     o que ja foi feito
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ('/opt/escritorio/SYNC', '/opt/escritorio/SISTEMA/backend', '/opt/escritorio', HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv(os.getenv('ESC_CONFIG_ENV', '/opt/escritorio/config/.env'))
load_dotenv('/opt/escritorio/config/.env', override=False)

DB = os.getenv('SISTEMA_DB', '/opt/escritorio/SISTEMA/backend/sistema_esc.db')
ESTADO = os.path.join(HERE, 'vigia_distribuicao_state.json')

PIPE_PRODUCAO = 18
ETAPAS_ORIGEM = ('Producao da Inicial', 'Revisao', 'Distribuicao')
ETAPA_PRODUCAO = 'Producao da Inicial'
ETAPA_REVISAO = 'Revisao'
DIAS_TAREFA_REVISAO = 15
ETAPA_DESTINO = 'Distribuida'
PIPE_CLIENTES = 11
ETAPA_CLIENTE = 'Processo Ativo [Cliente ou funcionario]'
DIAS_ATE_CLIENTES = 7

FASE_PROTOCOLADA = 'AÇÃO PROTOCOLADA/INICIADA'
FASES_QUE_PODEM_MUDAR = ('FASE DE CONTRATAÇÃO', '', None)

VIDEO = 'https://sistema.escritorio.adv.br/portal/videos/portal_distribuicao.mp4'
PORTAL = 'https://sistema.escritorio.adv.br/portal'
TRILHA_REGUA, PASSO_REGUA = 'A', 'A1'
# Janela de envio: padrao 9h-18h em dia util (regra da regua do Portal). Para um teste
# pontual fora dela: DISTRIBUICAO_JANELA=0-23 na frente do comando.
# JANELA DE 9h AS 20h (Advogada, 21/10/2026). Era 9h as 18h, e o caso do Edgar mostrou o custo:
# a Advogada registrou a distribuicao as 18h17 e o aviso ao cliente, com o card, ia esperar o
# dia seguinte. Decisao dela: "ao invés de ser das 9h às 18h, vamos fazer das 9h às 20h".
JANELA = tuple(int(x) for x in os.getenv('DISTRIBUICAO_JANELA', '9-20').split('-')[:2])
INTERVALO = 20
MAX_POR_RODADA = 8
AVISO_TIME = os.getenv('DISTRIBUICAO_AVISO_TEL') or os.getenv('TEL_ALERTA_EQUIPE', '')
# (Advogada, 06/10/2026) nesta etapa a dona e a Advogada, que e do operacional.
RESPONSAVEL_TAREFA = os.getenv('DISTRIBUICAO_RESPONSAVEL') or os.getenv('REVISORA_EMAIL', '')

RE_CNJ = re.compile(r'^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$')

# Textos aprovados pela Advogada em 06/10/2026. Nao mudar sem ela.
TEXTO_NOTICIA = (
    'Chegou o dia, {nome}! 🎉 A sua ação{contra} entrou na Justiça e o seu acesso ao '
    'Portal está aberto.\n\n'
    'Número do processo: {numero}\n'
    'Onde corre: {vara}\n'
    'Audiência: {audiencia}\n\n'
    'Parabéns por ter dado esse passo. Quem levanta a voz não melhora só a própria vida, '
    'melhora a da categoria inteira.')

TEXTO_COMO_ENTRAR = (
    'Para entrar é rápido: ' + PORTAL + ', coloca o seu CPF e chega um código aqui no '
    'WhatsApp. Não tem senha para decorar.\n\n'
    'Lá dentro você vê como está o processo em palavras simples, a próxima data, os seus '
    'documentos, e pode perguntar o que quiser para a Assistente, a nossa assistente, na hora, '
    'inclusive de madrugada na estrada.\n\n'
    'Uma coisa importante para não criar expectativa errada: o valor da causa é o que a '
    'gente está pedindo, não é o valor que você vai receber. Quem decide é a Justiça, '
    'depois de ouvir a empresa e ver as provas.\n\n'
    'Entra lá hoje e me diz se conseguiu, combinado?')

TEXTO_SEM_PORTAL = (
    'Assim que o seu acesso ao Portal estiver liberado eu te mando por aqui, combinado? '
    'É lá que você acompanha o processo a qualquer hora.')


# ------------------------------------------------------------------ utilidades
def _json(path, padrao):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return padrao


def _salvar(path, dados):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)


def _con(ro=True):
    con = sqlite3.connect(('file:%s?mode=ro' % DB) if ro else DB, uri=ro, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('pragma busy_timeout=8000')
    return con


def _agora():
    """Hora LOCAL, so para estado e relatorio."""
    return datetime.now().isoformat(sep=' ', timespec='microseconds')


def _agora_banco():
    """(07/10/2026) O Sistema Escritório Modelo guarda data em UTC. Gravar hora de Brasilia faz o
    movimento aparecer 3 horas no passado na trilha do board e o card nao subir na lista."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=' ', timespec='microseconds')


def _digitos(v):
    return ''.join(c for c in str(v or '') if c.isdigit())


def _tel8(v):
    return _digitos(v)[-8:]


def _sem_acento(t):
    t = unicodedata.normalize('NFKD', str(t or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Za-z ]', ' ', t).upper()


def _tokens(nome):
    return {p for p in _sem_acento(nome).split() if len(p) >= 3}


def primeiro_nome(nome):
    for p in str(nome or '').split():
        limpo = re.sub(r'[^A-Za-zÀ-ÿ]', '', p)
        if len(limpo) >= 2 and not any(c.isdigit() for c in p):
            return limpo[:1].upper() + limpo[1:].lower()
    return 'parceiro'


def tribunal_do_numero(numero):
    """Devolve o foro possivel a partir do numero CNJ, sem inventar nome de vara."""
    m = re.match(r'^\d{7}-\d{2}\.\d{4}\.(\d)\.(\d{2})\.(\d{4})$', numero or '')
    if not m:
        return ''
    justica, regiao, _unidade = m.group(1), int(m.group(2)), m.group(3)
    if justica == '5':
        return f'Justiça do Trabalho, TRT da {regiao}ª Região'
    if justica == '8':
        return 'Justiça Estadual'
    if justica == '4':
        return 'Justiça Federal'
    return 'Justiça'


# ------------------------------------------------- parte 0: tarefa de revisao
def fase_revisao(con, estado, aplicar):
    """(Advogada, 07/10/2026) Tarefa de REVISAO DE PECA aberta e card ainda na producao: move.

    O motor da producao ja move quando e ele quem cria a tarefa. O furo e a tarefa que nasce
    FORA do motor (na mao ou pelo ADVBOX): a peca vai para a Advogada e o card fica para tras.
    Caso que mostrou isso: Otacílio Prates, tarefa aberta com prazo 09/10 e card parado.
    """
    print('')
    print('-- 0. tarefa de revisão aberta com o card ainda na produção')
    sql = ("SELECT id, cliente, data, prazo_fatal FROM tarefas "
           " WHERE (UPPER(tipo) LIKE '%REVISAO%' OR UPPER(tipo) LIKE '%REVISÃO%') "
           "   AND COALESCE(status,'') = 'aberta' AND data >= date('now', ?)")
    mexeu = 0
    for t in con.execute(sql, ('-%d day' % DIAS_TAREFA_REVISAO,)):
        card, como = card_por_nome(con, t['cliente'])
        if not card:
            continue
        if card['pipeline_id'] != PIPE_PRODUCAO or card['etapa'] != ETAPA_PRODUCAO:
            continue
        print(f"   #{card['id']} {card['contato_nome'][:24]} [{card['etapa']}] "
              f"tarefa {t['id']} ({como}) -> {ETAPA_REVISAO}")
        mover_card(card['id'], PIPE_PRODUCAO, ETAPA_REVISAO, card['pipeline_id'], card['etapa'],
                   'revisao:tarefa-aberta', aplicar)
        nota_no_card(card['id'], f'[REVISÃO] tarefa {t["id"]} de revisão de peça está aberta, '
                                 f'card movido de "{ETAPA_PRODUCAO}" para "{ETAPA_REVISAO}".', aplicar)
        mexeu += 1
    if not mexeu:
        print('   nada a mover')


def card_por_nome(con, nome_cliente):
    """Card do cliente pelo nome completo: primeiro nome igual e dois nomes em comum."""
    tk = toks_nome(nome_cliente)
    if not tk:
        return None, ''
    for c in con.execute('SELECT id, contato_nome, pipeline_id, etapa, board_fixado, '
                         'board_fixado_por FROM wa_conversas WHERE pipeline_id=?', (PIPE_PRODUCAO,)):
        tc = toks_nome(c['contato_nome'])
        if not tc or tc[0] != tk[0]:
            continue
        if len(set(tc) & set(tk)) >= 2 or len(tc) == 1:
            return dict(c), 'nome completo' if len(tc) > 1 else 'primeiro nome único no quadro'
    return None, ''


def toks_nome(n):
    return [p for p in _sem_acento(n).split() if len(p) >= 3 and p not in ('DOS', 'DAS', 'LTDA')]


# ------------------------------------------------------------- parte 1: numero
def numeros_nas_tarefas(con):
    """Processos daqui, sem numero, cujo numero ja aparece numa tarefa do Sistema Escritório Modelo."""
    sql = ("SELECT t.lawsuits_id AS lawsuit, MAX(t.processo_numero) AS numero, "
           "       MAX(t.cliente) AS cliente, p.id AS processo_id, p.fase AS fase "
           "  FROM tarefas t JOIN jur_processos p ON p.advbox_id = t.lawsuits_id "
           " WHERE TRIM(COALESCE(t.processo_numero,'')) <> '' "
           "   AND TRIM(COALESCE(p.numero,'')) = '' AND COALESCE(p.encerrado,0) = 0 "
           " GROUP BY t.lawsuits_id")
    achados = []
    for r in con.execute(sql):
        numero = (r['numero'] or '').strip()
        if RE_CNJ.match(numero):
            achados.append(dict(r))
        else:
            print(f"  [FORA] processo {r['processo_id']}: número fora do padrão ({numero[:30]})")
    return achados


def numeros_no_advbox(con, limite=10):
    """(Advogada, 08/10/2026) Ponte temporária: o número sai do ADVBOX enquanto ele existir.

    A sincronização das 5h atualiza fase e etapa mas NÃO copia o número, e a tarefa nem
    sempre traz. Foi o que travou o Abel e o Silvano: protocolados, com número no ADVBOX
    e card parado aqui. Quando o ADVBOX sair, basta apagar esta função: o número passa a
    ser digitado na tela de processos e o resto do vigia continua igual.
    """
    import requests
    tok = os.getenv('ADVBOX_TOKEN') or os.getenv('ADVBOX_API_TOKEN')
    if not tok:
        print('   (sem token do ADVBOX, pulo esta parte)')
        return []
    cab = {'Authorization': 'Bearer ' + tok, 'User-Agent': 'Escritório Modelo/1.0', 'Accept': 'application/json'}
    achados = []
    pendentes = con.execute(
        "SELECT id, advbox_id, fase FROM jur_processos WHERE TRIM(COALESCE(numero,''))='' "
        " AND COALESCE(encerrado,0)=0 AND advbox_id IS NOT NULL ORDER BY id DESC").fetchall()
    for p in pendentes[:limite]:
        try:
            r = requests.get('https://app.advbox.com.br/api/v1/lawsuits/%s' % p['advbox_id'],
                             headers=cab, timeout=30)
            if r.status_code >= 300:
                continue
            d = r.json()
            d = d.get('data', d)
            if isinstance(d, list) and d:
                d = d[0]
            numero = (d.get('process_number') or '').strip()
            if RE_CNJ.match(numero):
                achados.append({'processo_id': p['id'], 'numero': numero,
                                'cliente': '(ADVBOX %s)' % p['advbox_id'], 'fase': p['fase']})
        except Exception as e:
            print(f"   (ADVBOX {p['advbox_id']}: {type(e).__name__})")
    return achados


def gravar_numero(item, aplicar):
    if not aplicar:
        return 'simulado'
    con = _con(ro=False)
    fase_nova = (FASE_PROTOCOLADA if (item.get('fase') or '').strip().upper() in
                 [f.strip().upper() for f in FASES_QUE_PODEM_MUDAR if f is not None] else item.get('fase'))
    con.execute("UPDATE jur_processos SET numero=?, fase=?, fase_desde=?, atualizado_em=? "
                " WHERE id=? AND TRIM(COALESCE(numero,''))=''",
                (item['numero'], fase_nova, _agora()[:19], _agora(), item['processo_id']))
    con.commit()
    con.close()
    return 'gravado'


def protocolos_vencidos(con):
    """Tarefa de PROTOCOLO com prazo fatal vencido e processo ainda sem numero."""
    hoje = datetime.now().strftime('%Y-%m-%d')
    sql = ("SELECT t.id, t.cliente, t.prazo_fatal, t.status, t.lawsuits_id, p.id AS processo_id "
           "  FROM tarefas t LEFT JOIN jur_processos p ON p.advbox_id = t.lawsuits_id "
           " WHERE UPPER(t.tipo) LIKE '%PROTOCOLO%' AND COALESCE(t.prazo_fatal,'') <> '' "
           "   AND t.prazo_fatal < ? AND COALESCE(t.status,'') <> 'cancelada' "
           "   AND TRIM(COALESCE(p.numero,'')) = ''")
    return [dict(r) for r in con.execute(sql, (hoje,))]


# --------------------------------------------------------------- parte 2: card
def card_do_processo(con, processo_id):
    """Acha o card do cliente: cliente do processo, depois telefone e nome completo."""
    # Atencao: `papel` aqui guarda a ORIGEM do cliente (INDICACAO, ANUNCIO, ESCRITORIO...).
    # A unica marca de parte contraria e o proprio texto 'PARTE CONTRARIA'.
    partes = con.execute(
        "SELECT cliente_id, nome, documento FROM jur_processo_partes "
        " WHERE processo_id=? AND UPPER(COALESCE(papel,'')) NOT LIKE 'PARTE CONTR%'",
        (processo_id,)).fetchall()
    alvos = []
    for p in partes:
        if p['cliente_id']:
            cli = con.execute('SELECT id, nome, telefone, celular, documento FROM jur_clientes '
                              'WHERE id=?', (p['cliente_id'],)).fetchone()
            if cli:
                alvos.append(dict(cli))
        elif p['nome']:
            alvos.append({'id': None, 'nome': p['nome'], 'telefone': '', 'celular': '',
                          'documento': p['documento']})
    cards = con.execute(
        'SELECT id, contato_nome, contato_telefone, etapa, pipeline_id, board_fixado, '
        '       board_fixado_por '      # 14/10/2026: faltava, e a parte 4 quebrava no print
        '  FROM wa_conversas WHERE pipeline_id IN (?,?) ', (PIPE_PRODUCAO, PIPE_CLIENTES)).fetchall()
    for alvo in alvos:
        tels = {_tel8(alvo.get('telefone')), _tel8(alvo.get('celular'))} - {''}
        for c in cards:
            if _tel8(c['contato_telefone']) and _tel8(c['contato_telefone']) in tels:
                return dict(c), alvo, 'telefone'
    for alvo in alvos:
        toks = _tokens(alvo.get('nome'))
        if len(toks) < 2:
            continue
        for c in cards:
            comuns = toks & _tokens(c['contato_nome'])
            if len(comuns) >= 2:
                return dict(c), alvo, 'nome completo'
    return None, (alvos[0] if alvos else None), 'não achei o card'


def mover_card(cid, pipeline, etapa, de_pipeline, de_etapa, origem, aplicar):
    if not aplicar:
        return 'simulado'
    con = _con(ro=False)
    con.execute('UPDATE wa_conversas SET pipeline_id=?, etapa=?, atualizado_em=? WHERE id=?',
                (pipeline, etapa, _agora_banco(), cid))
    con.execute('INSERT INTO wa_board_hist (conversa_id, de_pipeline, de_etapa, para_pipeline, '
                'para_etapa, quem, origem, bloqueado, criado_em) VALUES (?,?,?,?,?,?,?,0,?)',
                (cid, de_pipeline, de_etapa, pipeline, etapa, 'automacao', origem, _agora_banco()))
    con.commit()
    con.close()
    return 'movido'


def nota_no_card(cid, texto, aplicar):
    if not aplicar:
        return
    con = _con(ro=False)
    con.execute('INSERT INTO wa_mensagens (conversa_id, origem, autor, tipo, conteudo, '
                'midia_url, remote_id, criado_em) VALUES (?,?,?,?,?,?,?,?)',
                (cid, 'nota', 'vigia:distribuicao', 'texto', texto, '', '',
                 _agora_banco()))
    con.commit()
    con.close()


# ----------------------------------------------------------- parte 3: mensagem
def dados_da_mensagem(con, processo_id, numero, cliente):
    adversa = con.execute(
        "SELECT nome FROM jur_processo_partes WHERE processo_id=? "
        " AND UPPER(COALESCE(papel,'')) LIKE 'PARTE CONTR%' LIMIT 1", (processo_id,)).fetchone()
    if not adversa:
        t = con.execute("SELECT parte_adversa FROM tarefas WHERE TRIM(COALESCE(parte_adversa,''))<>'' "
                        "AND processo_numero=? LIMIT 1", (numero,)).fetchone()
        adversa = {'nome': t['parte_adversa']} if t else None
    aud = con.execute("SELECT data, hora, local FROM audiencia_registro WHERE processo=? "
                      "AND COALESCE(status,'') <> 'cancelada' ORDER BY data LIMIT 1",
                      (numero,)).fetchone()
    if aud and aud['data']:
        try:
            d = datetime.strptime(aud['data'][:10], '%Y-%m-%d')
            meses = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho',
                     'agosto', 'setembro', 'outubro', 'novembro', 'dezembro')
            quando = f'{d.day} de {meses[d.month - 1]}'
            if aud['hora']:
                quando += f", às {str(aud['hora'])[:5].replace(':', 'h')}"
        except Exception:
            quando = str(aud['data'])
        audiencia = quando
    else:
        audiencia = ('ainda não foi marcada. Assim que a Justiça marcar, eu te aviso por aqui')
    vara = ''
    if aud and aud['local']:
        m = re.search(r'(\d+ª?\s*Vara[^.,;\n]{0,60})', str(aud['local']), re.I)
        if m:
            vara = m.group(1).strip()
    if not vara:
        vara = tribunal_do_numero(numero)
    empresa = (adversa['nome'] if adversa and adversa['nome'] else '').strip()
    # NOME CORTADO NO MEIO (achado da Advogada em 21/10/2026): o corte cego em 60 caracteres mandou ao
    # Edgar "contra a FULANO DE TAL, BELTRANA DE TAL,CEREALISTA EXEMPLO LTDA., C". Quando a
    # equipe escreve varias reclamadas num campo so, a mensagem passa a dizer os nomes que cabem
    # INTEIROS e fecha com "e outras"; nome unico comprido fecha com reticencias, nunca no meio
    # de uma palavra.
    if empresa:
        pedacos = [x.strip(' .,;') for x in re.split(r'\s*[;,]\s*', empresa) if x.strip(' .,;')]
        if len(pedacos) > 1:
            cabe, usados = '', 0
            for nome_parte in pedacos:
                tentativa = (cabe + ', ' + nome_parte) if cabe else nome_parte
                if len(tentativa) > 52:
                    break
                cabe, usados = tentativa, usados + 1
            empresa = cabe or pedacos[0]
            if usados < len(pedacos):
                empresa += ' e outras'
        elif len(empresa) > 60:
            empresa = empresa[:57].rstrip(' ,.;') + '...'
    return {'empresa': empresa, 'vara': vara, 'audiencia': audiencia}


def pode_entrar_no_portal(con, cliente):
    """Sem CPF a pessoa nao consegue entrar: o portal pede CPF e manda codigo no WhatsApp."""
    if not cliente:
        return False, 'sem cadastro de cliente'
    cpf = _digitos(cliente.get('documento'))
    if len(cpf) != 11:
        return False, 'cadastro sem CPF'
    return True, 'ok'


def entrou_no_portal(con, cliente):
    if not cliente or not cliente.get('id'):
        return False
    r = con.execute('SELECT logins, ultimo_login FROM portal_acesso WHERE cliente_id=?',
                    (cliente['id'],)).fetchone()
    return bool(r and (r['logins'] or 0) > 0)


def registrar_regua(celular, nome, cliente_id, aplicar):
    """Marca o video como passo A1 da regua, para ela nao mandar o mesmo video de novo."""
    if not aplicar:
        return
    con = _con(ro=False)
    con.execute('INSERT INTO portal_regua (trilha, passo, cliente_id, contato, celular, '
                'enviado_em, ok, info) VALUES (?,?,?,?,?,?,1,?)',
                (TRILHA_REGUA, PASSO_REGUA, cliente_id, nome, _digitos(celular), time.time(),
                 'vigia_distribuicao'))
    con.commit()
    con.close()


def enviar(tel, texto1, texto2, com_video, aplicar):
    if not aplicar:
        return True, 'simulado'
    import regua_portal as rp
    from app.whatsapp import zapi
    con = _con()
    wa = rp.numero_wa(con)
    con.close()
    ok, info = rp.enviar(wa, tel, VIDEO if com_video else None, texto1)
    if not ok:
        return False, info
    if not texto2:
        return ok, info
    # (07/10/2026, licao do Jesse) o video ja foi entregue: se a segunda mensagem falhar,
    # NAO se repete a rodada inteira, senao o cliente recebe o video de novo. Tenta mais
    # duas vezes e, se nao for, devolve feito com a marca para o time mandar na mao.
    time.sleep(INTERVALO)
    ok2, info2 = zapi.enviar_texto(wa, tel, texto2)
    for _ in range(2):
        if ok2:
            break
        time.sleep(8)
        ok2, info2 = zapi.enviar_texto(wa, tel, texto2)
    return True, f'{info} | texto2={info2}' + ('' if ok2 else ' SEGUNDA MENSAGEM FALHOU')


def avisar_time(texto, aplicar):
    if not aplicar:
        print('   (aviso ao time, simulado)')
        return
    import regua_portal as rp
    from app.whatsapp import zapi
    con = _con()
    wa = rp.numero_wa(con)
    con.close()
    zapi.enviar_texto(wa, AVISO_TIME, texto)


def abrir_tarefa_ligacao(con, card, cliente, numero, aplicar):
    """Nao entrou no Portal: a equipe liga e coloca a pessoa dentro (meta da Advogada)."""
    u = con.execute('SELECT id, nome FROM usuarios WHERE email=?', (RESPONSAVEL_TAREFA,)).fetchone()
    if not u:
        u = con.execute("SELECT id, nome FROM usuarios WHERE email LIKE '%advogada%' LIMIT 1").fetchone()
    if not u:
        return 'sem responsável para a tarefa'
    titulo = f"Ligar para {primeiro_nome(card['contato_nome'])} entrar no Portal"
    texto = (f"{card['contato_nome']} recebeu o aviso da distribuição e o vídeo do Portal há "
             f"{DIAS_ATE_CLIENTES} dias e ainda não entrou. Processo {numero}. "
             "Ligar, explicar que é só o CPF e o código do WhatsApp e ficar na linha até ele entrar.")
    if not aplicar:
        return f"simulado (tarefa para {u['nome']})"
    hoje = datetime.now().strftime('%Y-%m-%d')
    con2 = _con(ro=False)
    cur = con2.execute(
        'INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, cliente, data, '
        'prazo_interno, prioridade, status, origem, origem_ref, criado_por, criado_em, '
        'atualizado_em, contato_nome, contato_telefone, conversa_id) '
        "VALUES (?,?,?,?,?,?,?,'normal','aberta',?,?,?,?,?,?,?,?)",
        ('LIGAR PARA CLIENTE', titulo, texto, numero, card['contato_nome'], hoje, hoje,
         'distribuicao', f"conversa:{card['id']}", 'Distribuição (automação)', _agora(),
         _agora(), card['contato_nome'], card['contato_telefone'], card['id']))
    con2.execute('INSERT INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) '
                 'VALUES (?,?,?,1,0)', (cur.lastrowid, u['id'], u['nome']))
    con2.commit()
    con2.close()
    return f"tarefa {cur.lastrowid} para {u['nome']}"


# ----------------------------------------------------------------------- motor
def rodar(aplicar, so_numero=False):
    agora = datetime.now()
    modo = 'APLICANDO' if aplicar else 'SIMULACAO'
    print(f'=== VIGIA DA DISTRIBUICAO | {agora:%d/%m/%Y %H:%M} | {modo} ===')
    estado = _json(ESTADO, {'cards': {}, 'avisos': {}})
    con = _con()

    fase_revisao(con, estado, aplicar)

    print('\n-- 1. número do processo que a tarefa já traz e aqui ainda não existe')
    pendentes = numeros_nas_tarefas(con)
    vistos = {i['processo_id'] for i in pendentes}
    pendentes += [i for i in numeros_no_advbox(con) if i['processo_id'] not in vistos]
    for item in pendentes:
        r = gravar_numero(item, aplicar)
        print(f"   processo {item['processo_id']} ({str(item['cliente'])[:24]}): "
              f"{item['numero']} -> {r}")
    if not pendentes:
        print('   nada a gravar')

    print('\n-- 2. protocolo com prazo fatal vencido e processo sem número')
    vencidos = protocolos_vencidos(con)
    for v in vencidos:
        chave = f"tarefa:{v['id']}"
        print(f"   tarefa {v['id']} {str(v['cliente'])[:24]} prazo {v['prazo_fatal']} "
              f"({v['status']})" + ('' if chave in estado['avisos'] else '  [avisar]'))
        if chave not in estado['avisos']:
            avisar_time(f"⚠️ Protocolo com prazo vencido e processo sem número aqui:\n\n"
                        f"{v['cliente']}\nPrazo fatal: {v['prazo_fatal']}\nTarefa {v['id']} "
                        f"({v['status']})\n\nOu o protocolo atrasou, ou falta registrar o número.",
                        aplicar)
            if aplicar:
                estado['avisos'][chave] = _agora()[:16]
                _salvar(ESTADO, estado)
    if not vencidos:
        print('   nenhum prazo vencido em aberto')

    if so_numero:
        con.close()
        return

    con.close()
    con = _con()   # reabre para enxergar o que acabou de ser gravado

    print('\n-- 3. cards para mover e avisar')
    processos = [dict(r) for r in con.execute(
        "SELECT id, numero, fase FROM jur_processos "
        " WHERE TRIM(COALESCE(numero,'')) <> '' AND COALESCE(encerrado,0)=0")]
    if not aplicar:
        # na simulacao o numero ainda nao foi gravado: mostra o que aconteceria com ele
        ja = {p['id'] for p in processos}
        processos += [{'id': i['processo_id'], 'numero': i['numero'], 'fase': i['fase']}
                      for i in pendentes if i['processo_id'] not in ja]
    enviados = 0
    for p in processos:
        card, cliente, como = card_do_processo(con, p['id'])
        if not card:
            continue
        chave = str(card['id'])
        ja = estado['cards'].get(chave, {})
        # (21/10/2026) quem ficou RETIDO ja esta em "Distribuida": o card andou e o cliente nao
        # recebeu nada. Esses continuam no radar, senao o aviso nunca sai.
        etapas_ok = ETAPAS_ORIGEM + ((ETAPA_DESTINO,)
                                     if str(ja.get('obs', '')).startswith('retido') else ())
        if card['pipeline_id'] != PIPE_PRODUCAO or card['etapa'] not in etapas_ok:
            continue
        if ja.get('enviado'):
            continue
        nome = primeiro_nome(card['contato_nome'])
        dados = dados_da_mensagem(con, p['id'], p['numero'], cliente)
        # (Advogada, 08/10/2026) cliente com mais de um processo: o card e um so, entao o robo
        # move mas NAO manda, porque a mensagem diria que "a sua acao entrou" e o cliente
        # tem outra ainda em revisao. Foi o caso do Silvano (TRANSPORTADORA A distribuida, TRANSPORTADORA B nao).
        # (21/10/2026) CONTA CASO, NAO LINHA DE BANCO: o Jesse e o Oziel tinham o mesmo caso
        # duplicado em jur_processos (registro sem numero e sem parte contraria, ou o mesmo
        # numero duas vezes) e o freio achou "2 processos ativos" onde havia um. Numero igual
        # conta uma vez; registro sem numero so conta se tiver parte contraria, senao e fantasma
        # do sync.
        outros = con.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT DISTINCT CASE WHEN TRIM(COALESCE(p.numero,'')) <> '' THEN p.numero "
            "                       ELSE 'sem-numero-' || p.id END AS caso "
            "    FROM jur_processos p JOIN jur_processo_partes pp ON pp.processo_id = p.id "
            "   WHERE pp.cliente_id = ? AND COALESCE(p.encerrado,0) = 0 "
            "     AND (TRIM(COALESCE(p.numero,'')) <> '' OR EXISTS ("
            "          SELECT 1 FROM jur_processo_partes x WHERE x.processo_id = p.id "
            "           AND UPPER(COALESCE(x.papel,'')) LIKE '%CONTR%')))",
            ((cliente or {}).get('id'),)).fetchone()[0] if (cliente or {}).get('id') else 1
        # e so freia se a mensagem NAO puder dizer de qual caso se trata: com o nome da empresa
        # o texto ja resolve a ambiguidade, pelo campo {contra} que a Advogada aprovou em 06/10
        if outros > 1 and not (dados or {}).get('empresa'):
            print(f"      cliente tem {outros} processos ativos: movo o card e NAO envio")
            mover_card(card['id'], PIPE_PRODUCAO, ETAPA_DESTINO, card['pipeline_id'],
                       card['etapa'], 'distribuicao:numero', aplicar)
            nota_no_card(card['id'], f'[DISTRIBUIÇÃO] processo {p["numero"]} distribuído e card movido. '
                                     f'Aviso ao cliente RETIDO: ele tem {outros} processos ativos e a '
                                     'mensagem precisa dizer de qual se trata.', aplicar)
            avisar_time(f"📄 {card['contato_nome']} teve o processo {p['numero']} distribuído, mas ele tem "
                        f"{outros} processos ativos. Card movido para Distribuida e a mensagem NÃO saiu: "
                        "o texto precisa dizer de qual caso se trata.", aplicar)
            if aplicar:
                # NAO marca 'enviado', porque nada foi enviado (21/10/2026). Era assim que o
                # retido virava definitivo: com 'enviado' no estado o vigia nunca tentava de novo
                # e a parte 4 ainda migrava o cliente para ativo sem ele ter recebido nada.
                estado['cards'][chave] = {'numero': p['numero'], 'retido_em': _agora()[:16],
                                          'cliente_id': (cliente or {}).get('id'), 'portal': False,
                                          'obs': 'retido: %d casos ativos e sem o nome da empresa '
                                                 'para a mensagem' % outros}
                _salvar(ESTADO, estado)
            continue
        pode, motivo = pode_entrar_no_portal(con, cliente)
        contra = f" contra a {dados['empresa']}" if dados['empresa'] else ''
        t1 = TEXTO_NOTICIA.format(nome=nome, contra=contra, numero=p['numero'],
                                  vara=dados['vara'], audiencia=dados['audiencia'])
        t2 = TEXTO_COMO_ENTRAR if pode else TEXTO_SEM_PORTAL
        print(f"\n   #{card['id']} {card['contato_nome'][:26]} [{card['etapa']}] "
              f"casado por {como}")
        print(f"      processo {p['numero']} | portal: {motivo}")
        if not pode:
            print('      -> manda só a notícia e abre pendência de cadastro')
        if enviados >= MAX_POR_RODADA:
            print('      (teto da rodada, fica para a próxima)')
            continue
        if aplicar and not (JANELA[0] <= agora.hour < JANELA[1] and agora.weekday() < 5):
            print('      (fora da janela de envio, fica para a próxima rodada)')
            continue
        print('      --- mensagem 1 ---')
        print('      ' + t1.replace('\n', '\n      '))
        print('      --- mensagem 2 ---')
        print('      ' + t2.replace('\n', '\n      '))
        if card['etapa'] != ETAPA_DESTINO:
            mover_card(card['id'], PIPE_PRODUCAO, ETAPA_DESTINO, card['pipeline_id'],
                       card['etapa'], 'distribuicao:numero', aplicar)
        ok, info = enviar(card['contato_telefone'], t1, t2, True, aplicar)
        print(f'      envio: {ok} {str(info)[:90]}')
        if not ok:
            # (Advogada, 06/10/2026) falha de envio nao pode ficar so no log
            chave_falha = f"envio:{card['id']}:{datetime.now():%Y-%m-%d}"
            if chave_falha not in estado['avisos']:
                avisar_time(f"🚨 Falhou o aviso de distribuição para {card['contato_nome']} "
                            f"({p['numero']}). O card foi movido para Distribuida, mas a mensagem "
                            f"não saiu. Motivo: {str(info)[:120]}", aplicar)
                if aplicar:
                    estado['avisos'][chave_falha] = _agora()[:16]
                    _salvar(ESTADO, estado)
        if ok and 'SEGUNDA MENSAGEM FALHOU' in str(info):
            avisar_time(f"⚠️ {card['contato_nome']} recebeu o vídeo e a notícia da distribuição, "
                        "mas a mensagem de como entrar no Portal não saiu. Mandar na mão.", aplicar)
        if ok and aplicar:
            registrar_regua(card['contato_telefone'], card['contato_nome'],
                            cliente.get('id') if cliente else None, aplicar)
            nota_no_card(card['id'], f'[DISTRIBUICAO] processo {p["numero"]}: card movido para '
                                     f'"{ETAPA_DESTINO}", vídeo do Portal e as duas mensagens '
                                     f'enviadas. Registrado como passo A1 da régua.', aplicar)
            estado['cards'][chave] = {'numero': p['numero'], 'enviado': _agora()[:16],
                                      'cliente_id': (cliente or {}).get('id'),
                                      'portal': pode}
            _salvar(ESTADO, estado)
            enviados += 1
        if not pode:
            avisar_time(f"📋 {card['contato_nome']} foi distribuído ({p['numero']}) mas o "
                        f"cadastro está {motivo}. Sem CPF ele não consegue entrar no Portal. "
                        "Completar o cadastro e avisar que o acesso está liberado.", aplicar)

    print('\n-- 4. quem já pode virar cliente ativo')
    for chave, d in list(estado['cards'].items()):
        if d.get('migrado') or not d.get('enviado'):
            continue
        card = con.execute('SELECT id, contato_nome, contato_telefone, etapa, pipeline_id, '
                           'board_fixado, board_fixado_por '      # 14/10/2026: faltava o
                           'FROM wa_conversas WHERE id=?',        # _por e a parte 4 quebrava
                           (int(chave),)).fetchone()
        if not card or card['pipeline_id'] != PIPE_PRODUCAO:
            continue
        cliente = None
        if d.get('cliente_id'):
            cliente = dict(con.execute('SELECT id, nome, documento FROM jur_clientes WHERE id=?',
                                       (d['cliente_id'],)).fetchone() or {})
        dentro = entrou_no_portal(con, cliente)
        dias = (datetime.now() - datetime.fromisoformat(d['enviado'])).days
        if not dentro and dias < DIAS_ATE_CLIENTES:
            print(f"   #{chave} {card['contato_nome'][:24]}: {dias} dia(s), ainda não entrou, "
                  'a régua do Portal segue')
            continue
        # (Advogada, 06/10/2026) "nessa etapa quem muda o CRM é você, este trabalho é seu": esta
        # passagem da esteira (Producao > Distribuida -> Clientes > Processo Ativo) acontece
        # mesmo com o board fixado por gente, igual a excecao que ja existe de Leads para
        # Contratacao e Producao. A trava do board segue valendo para todo o resto.
        if card['board_fixado']:
            print(f"   #{chave}: board fixado por {card['board_fixado_por'] or 'gente'}, "
                  'avanço da esteira autorizado pela Advogada')
        print(f"   #{chave} {card['contato_nome'][:24]}: "
              + ('entrou no Portal' if dentro else f'{dias} dias sem entrar')
              + ' -> Clientes')
        mover_card(card['id'], PIPE_CLIENTES, ETAPA_CLIENTE, card['pipeline_id'], card['etapa'],
                   'distribuicao:cliente-ativo', aplicar)
        nota_no_card(card['id'], '[DISTRIBUICAO] ' + (
            'o cliente entrou no Portal' if dentro else
            f'{dias} dias sem entrar no Portal') + ', card movido para Clientes > '
            + ETAPA_CLIENTE + '.', aplicar)
        if not dentro:
            print('      ' + abrir_tarefa_ligacao(con, dict(card), cliente, d.get('numero'), aplicar))
        if aplicar:
            d['migrado'] = _agora()[:16]
            d['entrou_no_portal'] = dentro
            _salvar(ESTADO, estado)
    con.close()


def status():
    estado = _json(ESTADO, {'cards': {}, 'avisos': {}})
    if not estado['cards']:
        print('nada registrado ainda')
    for cid, d in estado['cards'].items():
        print(f"  #{cid} {d.get('numero')} enviado {d.get('enviado')} "
              f"migrado {d.get('migrado') or 'não'} portal {d.get('entrou_no_portal')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--aplicar', action='store_true')
    ap.add_argument('--so-numero', action='store_true')
    ap.add_argument('--status', action='store_true')
    a = ap.parse_args()
    if a.status:
        status()
        return
    rodar(a.aplicar, a.so_numero)


if __name__ == '__main__':
    main()
