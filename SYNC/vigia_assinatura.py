# -*- coding: utf-8 -*-
"""VIGIA DA ASSINATURA TRAVADA (Advogada, 23/10/2026: "pode construir sim o vigia de assinatura travada").

A esteira conta DIAS. Ela nao ve o cliente batendo na porta. O Quirino abriu o contrato dele treze
vezes, o Ivo doze, o Leopoldo quinze, e ninguem soube. Pior: os documentos corrigidos do Valdo
e dois dos tres do Ivo nasceram SEM TELEFONE no ZapSign, ou seja, nunca houve como avisar essas
pessoas, e elas ficaram meses paradas enquanto a regua mandava lembrete de uma assinatura que era
impossivel fazer.

O que este vigia olha, documento por documento, na esteira do reclamante:

  1. MUDO            signatario pendente sem telefone e sem e-mail: nunca houve aviso. Preenche o
                     telefone do cadastro e reenvia. Sem telefone nenhum, vira tarefa para a
                     vendedora conseguir o numero.
  2. RECLAMOU        o cliente disse nas ultimas 72h que nao consegue assinar. Isso e pedido de
                     socorro explicito e tem prioridade sobre contador nenhum.
  3. SOCORRO         abriu 3 vezes ou mais e nao concluiu. Quem abre e desiste esta travando.
  4. NUNCA ABRIU     documento em "new", zero aberturas, mais de um dia util depois do envio: o
                     aviso nao chegou. Reenvia uma vez.
  5. ASSINOU PARADO  todos os documentos assinados e o card nao andou. Foi o caso do Gervásio:
                     assinou as 8h e ficou invisivel porque o alarme de lead quente o jogou numa
                     coluna que nao existe no quadro dele, e a esteira so olha quem esta em
                     "Contrato no ZapSign".

O conserto dos casos 2 e 3 e o PLANO B da assinatura: o documento passa para 'tokenWhatsapp', que e
so o codigo, sem desenho nenhum, o aviso e reenviado e a vendedora dona do card recebe tarefa. Foi
exatamente assim que o Ivo destravou hoje.

TRAVAS (o vigia erra para o lado de nao incomodar):
  * nada acontece sem --aplicar;
  * envio so em dia util, das 9h as 20h, a mesma janela da casa;
  * um toque por documento por tipo de problema, com COOLDOWN_DIAS entre repeticoes, e a troca
    para o plano B acontece uma vez por documento, nunca duas;
  * quem desistiu (recusa no GR, card em Perdido) nao e tocado;
  * o vigia NAO MOVE CARD: no caso 5 ele avisa e abre tarefa, porque a Advogada pediu para alinhar
    antes de mover card ("nao mova nada antes de me falar"). Com --mover ela libera isso.

USO:
  vigia_assinatura.py                simula e nao muda nem envia nada
  vigia_assinatura.py --aplicar      aplica o plano B, reenvia, abre tarefa e avisa
  vigia_assinatura.py --card 2648    so um card
  vigia_assinatura.py --status       o que ja foi feito, por documento
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ('/opt/escritorio/SYNC', '/opt/escritorio/INTEGRACOES', '/opt/escritorio/SISTEMA/backend', '/opt/escritorio', HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv                                              # noqa: E402
load_dotenv(os.getenv('ESC_CONFIG_ENV', '/opt/escritorio/config/.env'))
load_dotenv('/opt/escritorio/config/.env', override=False)

import zapsign_integration as zs                                            # noqa: E402

DB = os.getenv('SISTEMA_DB', '/opt/escritorio/SISTEMA/backend/sistema_esc.db')
ESTADO = os.path.join(HERE, 'vigia_assinatura_state.json')
FILA_TRAB = os.path.join(HERE, 'cobranca_fila.json')
ESTADO_GR = os.path.join(HERE, 'gr_contratacao_state.json')

ALERTA_TEL = os.getenv('ASSINATURA_ALERTA_TEL') or os.getenv('TEL_ALERTA_EQUIPE', '')
JANELA = tuple(int(x) for x in os.getenv('ASSINATURA_JANELA', '9-20').split('-')[:2])
TOQUES_SOCORRO = int(os.getenv('ASSINATURA_TOQUES_SOCORRO', '3'))
COOLDOWN_DIAS = int(os.getenv('ASSINATURA_COOLDOWN_DIAS', '3'))
HORAS_FALA = 72
HORAS_FOLGA = int(os.getenv('ASSINATURA_HORAS_FOLGA', '2'))   # uma rodada de folga da esteira
PLANO_B = 'tokenWhatsapp'
COLUNAS_MORTAS = ('Perdido', 'Lead Desqualificado', 'Desistiu')
PIPE_TRAB = 20                       # Contratação (reclamante)
COL_ZAPSIGN = 'Contrato no ZapSign'
COL_ASSINADO = 'Assinado'

# o cliente pedindo socorro. Vocabulario fechado de proposito: palavra solta pega conversa de
# outro assunto, e o preco de um falso positivo aqui e mandar mensagem errada para quem esta bem.
RX_SOCORRO = re.compile(
    r'(nao (consigo|consegui|da|deu|to conseguindo|estou conseguindo) assinar|'
    r'assinatura (incorreta|negada|invalida|errada)|nao confere|'
    r'nao (chega|chegou|recebi) (o |esse )?(link|codigo|sms|documento)|'
    r'(link|codigo) nao (chega|chegou|funciona|abre)|'
    r'(fica|esta|ta) travando|nao carrega|nao abre|da erro|deu erro|'
    r'tentei (varias vezes|bastante|assinar)|nao vai a assinatura)')


def sem_acento(t):
    t = unicodedata.normalize('NFD', t or '')
    return ''.join(c for c in t if unicodedata.category(c) != 'Mn').lower()


def digitos(x):
    return ''.join(c for c in str(x or '') if c.isdigit())


def celular11(x):
    """Telefone do jeito que o WhatsApp e o ZapSign querem: sem o 55 e COM o 9 do celular.

    Licao de hoje (23/10/2026): um cadastro guardava o celular sem o 9 e foi
    esse numero torto que eu levei para o ZapSign antes de corrigir. Mesma familia do conserto dos
    telefones do Asaas feito nesta manha.
    """
    d = digitos(x)
    if d.startswith('55') and len(d) > 11:
        d = d[2:]
    if len(d) == 10 and d[2] in '6789':      # DDD + 8 digitos de celular: falta o 9
        d = d[:2] + '9' + d[2:]
    return d if len(d) == 11 else (d or '')


def _json(path, padrao):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return padrao


def _salvar(path, dados):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)


def _con():
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def hoje():
    return datetime.now().strftime('%Y-%m-%d')


def agora():
    return datetime.now().isoformat(sep=' ', timespec='seconds')


def dia_util_e_hora():
    a = datetime.now()
    return a.weekday() < 5 and JANELA[0] <= a.hour < JANELA[1]


def dias_uteis_desde(iso):
    """Quantos dias uteis se passaram desde uma data ISO (so a parte da data)."""
    try:
        d = datetime.strptime((iso or '')[:10], '%Y-%m-%d').date()
    except Exception:
        return 0
    n, hoje_ = 0, datetime.now().date()
    while d < hoje_:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def avisar_time(texto, aplicar):
    print('   AVISO: ' + texto.replace('\n', ' ')[:170])
    if not aplicar:
        return
    try:
        import regua_portal as rp
        from app.whatsapp import zapi
        con = _con()
        wa = rp.numero_wa(con)
        con.close()
        zapi.enviar_texto(wa, ALERTA_TEL, texto)
    except Exception as e:
        print('   (nao consegui avisar: %s)' % str(e)[:90])


def enviar_cliente(tel, texto, aplicar):
    print('   MENSAGEM para %s: %s' % (tel, texto.replace('\n', ' ')[:120]))
    if not aplicar:
        return True
    try:
        import regua_portal as rp
        from app.whatsapp import zapi
        con = _con()
        wa = rp.numero_wa(con)
        con.close()
        ok, _info = zapi.enviar_texto(wa, tel, texto)
        return bool(ok)
    except Exception as e:
        print('   (nao consegui enviar: %s)' % str(e)[:90])
        return False


def tarefa(card, titulo, detalhe, ref, aplicar):
    """Tarefa para a vendedora dona do card, na mesma receita do lead quente."""
    print('   TAREFA para %s: %s' % (card['vend'], titulo[:80]))
    if not aplicar:
        return None
    try:
        import lead_quente_tarefa as LQ
        t = {'vend': card['vend'], 'origem': 'vigia-assinatura', 'ref': ref,
             'cid': int(card['cid']), 'nome': card['nome'] or '', 'tel': card['tel'] or '',
             'titulo': titulo, 'texto': LQ._limpo(detalhe)}
        tid = LQ.gravar(t)
        print('      tarefa #%s' % tid)
        return tid
    except Exception as e:
        print('   (tarefa nao gravou: %s)' % str(e)[:90])
        return None


MSG_PLANO_B = (
    '{primeiro}, deixei a sua assinatura mais simples: agora não precisa desenhar nada.\n\n'
    'Você abre o link, chega um código aqui no seu WhatsApp e é só digitar ele:\n\n{links}\n\n'
    'Se travar de novo, me chama que eu te ligo e a gente assina junto.')

MSG_REENVIO = (
    '{primeiro}, reenviei aqui os links da sua assinatura, porque pelo que vi eles não tinham '
    'chegado até você:\n\n{links}\n\n'
    'Na hora de assinar vai chegar um código no seu WhatsApp. Qualquer dificuldade, me chama.')


def marcar_fila_assinou(caso, card, aplicar):
    """Poe a marca 'assinou' na fila da assinatura do reclamante, que e o que o vigia da
    contratacao le para mover o card. So a marca: nada e enviado ao cliente.

    Por que isso existe (26/10/2026): a fila confere o ZapSign as 8h, as 13h e as 19h. Quem assina
    no meio da tarde fica parado ate a rodada seguinte. O Valdo Sarmento assinou os tres
    documentos as 14h34, depois de o vigia ter trocado a assinatura dele para codigo pelo WhatsApp,
    e o card continuou em "Contrato no ZapSign" porque a fila ainda nao sabia. Marcando aqui, o
    vigia da contratacao move o card na proxima meia hora.
    """
    fila = _json(FILA_TRAB, {})
    clientes = fila.get('clientes') or {}
    tel = digitos(caso.get('tel') or card['tel'])[-8:]
    alvo = None
    for nome, c in clientes.items():
        if nome == caso.get('nome') or (tel and digitos(c.get('telefone'))[-8:] == tel):
            alvo = (nome, c)
            break
    if not alvo or alvo[1].get('assinou'):
        return False
    print('   marco a fila: %s assinou (a fila so confere o ZapSign as 8h, 13h e 19h)' % alvo[0])
    if aplicar:
        alvo[1]['assinou'] = True
        alvo[1]['assinou_em'] = hoje()
        alvo[1]['assinou_marcado_por'] = 'vigia_assinatura'
        _salvar(FILA_TRAB, fila)
    return True


def primeiro_nome(n):
    p = (n or '').strip().split()
    return p[0].capitalize() if p else 'Oi'


CAMPOS_CARD = ('select id, contato_nome, contato_telefone, etapa, pipeline_id, atendente_email, '
               'atualizado_em from wa_conversas ')
PIPES_CONTRATACAO = (20, 26, 24)
PIPES_DEPOIS = (18, 25, 11)      # producao do trabalhista, producao do GR e clientes


def _card(r, tel='', nome=''):
    email = (r['atendente_email'] or '')
    return {'cid': r['id'], 'nome': r['contato_nome'] or nome, 'tel': r['contato_telefone'] or tel,
            'etapa': r['etapa'] or '', 'pipe': r['pipeline_id'],
            'visto_em': r['atualizado_em'] or '',
            'vend': (email.split('@')[0].split('.')[-1] or 'a equipe').capitalize()}


def card_por_id(con, cid):
    r = con.execute(CAMPOS_CARD + 'where id=?', (cid,)).fetchone()
    return _card(r) if r else None


def card_do_telefone(con, tel, nome=''):
    """Acha o card pelo telefone, preferindo o do fluxo de contratacao.

    Homonimo e numero repetido existem de monte: o Ezequiel tem cinco cards, dois deles no
    mesmo quadro, e o Quirino tem dois. Pegar "o mais recente" fazia o vigia julgar o card errado,
    entao a escolha e por quadro: primeiro a contratacao, depois a producao, e nome nunca serve de
    chave, so de ultimo desempate dentro do mesmo telefone.
    """
    d = digitos(tel)[-8:]
    if not d:
        return None
    achados = [_card(r, tel, nome) for r in
               con.execute(CAMPOS_CARD + 'where contato_telefone like ? order by atualizado_em desc',
                           ('%' + d + '%',))]
    if not achados:
        return None
    for pipes in (PIPES_CONTRATACAO, PIPES_DEPOIS):
        no_grupo = [c for c in achados if c['pipe'] in pipes]
        if no_grupo:
            return no_grupo[0]
    return achados[0]


def tipo_doc(nome):
    """Que peca e essa, independente de como o nome foi escrito nas duas esteiras."""
    t = sem_acento(nome)
    for k in ('contrato', 'procuracao', 'declaracao'):
        if k in t:
            return k
    return 'outro'


def horas_desde(iso):
    for f in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            d = datetime.strptime((iso or '')[:26], f)
            break
        except Exception:
            d = None
    if not d:
        return 999
    return (datetime.now(timezone.utc).replace(tzinfo=None) - d).total_seconds() / 3600.0


def coluna_existe(con, pipe, etapa):
    if not etapa:
        return False
    return bool(con.execute('select 1 from wa_stages where pipeline_id=? and nome=?',
                            (pipe, etapa)).fetchone())


def _con_rw():
    con = sqlite3.connect(DB, timeout=20)
    con.execute('pragma busy_timeout=8000')
    return con


def mover_card(cid, etapa_nova, aplicar, quem='auto:vigia-assinatura'):
    """Move o card de coluna (Advogada, 26/10/2026: "varrer com menos tempo e mover os cards").

    Tres cuidados, que sao as licoes da casa:
      * TRAVA DO BOARD: card fixado por gente nao e mexido por robo;
      * coluna que nao existe no quadro nao e escrita, senao o card fica invisivel, que e
        exatamente o defeito que este vigia existe para achar;
      * a trilha em wa_board_hist e escrita pelo gatilho da propria base, como nos outros vigias.
    """
    con = _con_rw()
    r = con.execute('select pipeline_id, etapa, coalesce(board_fixado,0), '
                    "coalesce(board_fixado_por,'') from wa_conversas where id=?", (cid,)).fetchone()
    if not r:
        con.close()
        return False
    pipe, de_etapa, fixado, por = r
    # TRAVA DO BOARD (regra da casa, 03/09/2026): ela vale para trocar de QUADRO, nao de coluna.
    # "Trocar de coluna dentro do mesmo board segue livre pra automacao, senao os watchers
    # parariam." Entao aqui a trava e so anotada, nunca impede. Quem respeita a trava de verdade
    # e a travessia para a producao, que troca de quadro e usa o mover da casa.
    if fixado:
        print('   (o board esta fixado por %s; trocar de coluna no mesmo quadro segue livre)'
              % (por or 'alguem'))
    if not coluna_existe(con, pipe, etapa_nova):
        print('   NAO movo: a coluna "%s" nao existe no quadro %s' % (etapa_nova, pipe))
        con.close()
        return False
    print('   MOVO o card: "%s" -> "%s"' % (de_etapa, etapa_nova))
    if aplicar:
        con.execute('update wa_conversas set etapa=?, atualizado_em=? where id=?',
                    (etapa_nova, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), cid))
        con.commit()
    con.close()
    return True


def levar_para_producao(cid, aplicar):
    """A travessia de "Assinado" para a producao, pelo mover da casa, que respeita a trava e nao
    traz de volta quem ja esta adiante. Sem isso o cliente esperaria a rodada do vigia da
    contratacao, que e de meia em meia hora."""
    print('   e dali para Produção > Aguardando Documentos')
    if not aplicar:
        return True
    try:
        import varredura_provas as vp
        vp.mover_para_producao(cid, True, etapa='Aguardando Documentos',
                               quem='auto:vigia-assinatura', origem='script:vigia_assinatura')
        inscrever_na_fila_de_documentos(cid)
        return True
    except Exception as e:
        print('   (a travessia para a producao nao foi: %s)' % str(e)[:90])
        return False


def inscrever_na_fila_de_documentos(cid):
    """Card que chega em Aguardando Documentos PRECISA estar na fila: e por ela que a
    varredura acha o cliente, guarda os arquivos na pasta e anda o card. Quando quem move
    e este vigia, a fase_assinado da esteira nao roda, e sem isto o cliente fica invisivel
    (Valdo Sarmento, 26/10/2026: 12 fotos sem classificar e card parado)."""
    try:
        con = _con()
        r = con.execute('select coalesce(contato_nome,""), coalesce(contato_telefone,"") '
                        'from wa_conversas where id=?', (cid,)).fetchone()
        con.close()
        if not r:
            return False
        import contratacao_watcher as cw
        import cobranca_assinatura as ca
        fila = {}
        try:
            fila = ca.carregar().get('clientes') or {}
        except Exception:
            pass
        cw._garantir_fila_documentos(cid, r[0], r[1], fila, True)
        return True
    except Exception as e:
        print('   (nao consegui inscrever na fila de documentos: %s)' % str(e)[:110])
        return False


def reclamou(con, cid):
    """O cliente disse nas ultimas HORAS_FALA que nao consegue assinar? Devolve a fala."""
    corte = (datetime.now(timezone.utc) - timedelta(hours=HORAS_FALA)).strftime('%Y-%m-%d %H:%M:%S')
    for m in con.execute('select conteudo from wa_mensagens where conversa_id=? and origem=? '
                         'and criado_em >= ? order by id desc limit 40',
                         (cid, 'contato', corte)):
        t = sem_acento(m['conteudo'] or '')
        if t and RX_SOCORRO.search(t):
            return (m['conteudo'] or '')[:220]
    return None


def coletar():
    """Os casos das duas esteiras: nome, telefone e os tokens dos documentos."""
    casos = []
    fila = _json(FILA_TRAB, {})
    for nome, c in (fila.get('clientes') or {}).items():
        casos.append({'origem': 'reclamante', 'nome': nome, 'tel': c.get('telefone'),
                      'docs': c.get('tokens') or c.get('docs') or [], 'cid': None,
                      'desistiu': False})
    est = _json(ESTADO_GR, {})
    for cid, c in (est.get('cards') or {}).items():
        docs = ((c.get('zapsign') or {}).get('docs')) or []
        if docs:
            casos.append({'origem': 'GR', 'nome': c.get('nome') or '', 'tel': c.get('telefone'),
                          'docs': docs, 'cid': cid, 'desistiu': bool(c.get('recusa_em'))})
    return casos


def marca(est, token, tipo):
    return ((est.get('docs') or {}).get(token) or {}).get(tipo)


def marcar(est, token, tipo, info, aplicar):
    if not aplicar:
        return
    est.setdefault('docs', {}).setdefault(token, {})[tipo] = {'quando': agora(), 'info': info}


def no_cooldown(est, token, tipo):
    m = marca(est, token, tipo)
    if not m:
        return False
    return dias_uteis_desde(m.get('quando')) < COOLDOWN_DIAS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--aplicar', action='store_true')
    ap.add_argument('--card', help='so este card')
    ap.add_argument('--nao-mover', action='store_true',
                    help='so avisa, nao move card nenhum (o padrao desde 26/10 e mover)')
    ap.add_argument('--status', action='store_true')
    a = ap.parse_args()

    est = _json(ESTADO, {'docs': {}, 'clientes': {}})
    if a.status:
        for tk, v in sorted((est.get('docs') or {}).items()):
            print('%s  %s' % (tk[:12], json.dumps(v, ensure_ascii=False)[:150]))
        print('%d documento(s) com historico' % len(est.get('docs') or {}))
        return

    print('=== VIGIA DA ASSINATURA | %s | %s ==='
          % (agora(), 'APLICANDO' if a.aplicar else 'SIMULANDO'))
    if a.aplicar and not dia_util_e_hora():
        print('fora da janela (dia util, %dh as %dh): nao incomodo ninguem agora.'
              % (JANELA[0], JANELA[1]))
        return

    con = _con()
    achados = 0
    for caso in coletar():
        # no GR o card vem do proprio estado da esteira; no reclamante, pelo telefone
        card = (card_por_id(con, caso['cid']) if caso.get('cid')
                else card_do_telefone(con, caso['tel'], caso['nome']))
        if not card:
            continue
        if a.card and str(card['cid']) != str(a.card):
            continue
        if caso['desistiu'] or card['etapa'] in COLUNAS_MORTAS:
            continue

        pendentes, assinados, falou, nome_real = [], 0, None, ''
        tipos_assinados, ultima_assinatura = set(), ''
        for t in caso['docs']:
            tk = t.get('token') if isinstance(t, dict) else t
            try:
                j = zs.buscar_documento(tk) or {}
            except Exception as e:
                print('   (nao li o documento %s: %s)' % (str(tk)[:10], str(e)[:60]))
                continue
            if j.get('status') == 'signed':
                assinados += 1
                tipos_assinados.add(tipo_doc(j.get('name') or ''))
                for s in j.get('signers') or []:
                    if 'advogada' in (s.get('name') or '').lower():
                        continue
                    ultima_assinatura = max(ultima_assinatura, (s.get('signed_at') or '')[:19])
                continue
            for s in j.get('signers') or []:
                if 'advogada' in (s.get('name') or '').lower() or s.get('status') == 'signed':
                    continue
                pendentes.append((tk, j, s))
                # o nome de verdade esta no signatario, nao no card: o perfil de WhatsApp do
                # Valdo e "valdosarmento451" e o do Ivo Quaresma e "empresa". Chamar o cliente
                # pelo apelido do perfil e o erro que ja saiu uma vez hoje.
                nome_real = nome_real or (s.get('name') or '')

        # sobra de refacao NAO e assinatura travada: quando a mesma peca ja esta assinada em outro
        # documento, o pendente e lixo do dia em que o contrato foi refeito (Leopoldo tem uma
        # "copia" do contrato parada desde julho, e o Ezequiel um jogo inteiro). Cobrar isso do
        # cliente seria pedir para ele assinar duas vezes a mesma coisa.
        sobras = [p for p in pendentes if tipo_doc(p[1].get('name') or '') in tipos_assinados]
        if sobras:
            print('   (%d documento(s) de %s sao sobra de refação, a peça já está assinada: '
                  'ignoro)' % (len(sobras), card['nome'][:22]))
            pendentes = [p for p in pendentes if p not in sobras]

        # ---------------------------------------------------------------- 5. assinou e parou
        if not pendentes and assinados:
            fantasma = not coluna_existe(con, card['pipe'], card['etapa'])
            esperado = card['etapa'] in ('Assinado', 'Assinado · aguardando pagamento',
                                         'Assinado e pagamento feito')
            # ja passou da assinatura (producao ou clientes): a esteira levou, nada a dizer
            if not fantasma and (card['pipe'] in PIPES_DEPOIS or esperado):
                continue
            # ANDA SOZINHO (Advogada, 26/10/2026: "varrer com menos tempo e mover os cards"): assinou
            # tudo e o lugar dele e "Assinado". Marca a fila, que e o que o vigia da contratacao
            # le, move o card e faz a travessia para a producao na hora, em vez de deixar o
            # cliente esperando a rodada de meia em meia hora. O Valdo Sarmento assinou as 14h34
            # e o card dele ficaria parado ate as 19h.
            if card['pipe'] == PIPE_TRAB and (fantasma or card['etapa'] == COL_ZAPSIGN):
                achados += 1
                print('\n#%s %s (%s)' % (card['cid'], card['nome'], caso['origem']))
                print('   ASSINOU TUDO%s'
                      % (' e estava em "%s", coluna que nao existe no quadro %s'
                         % (card['etapa'], card['pipe']) if fantasma else ''))
                if caso['origem'] == 'reclamante':
                    marcar_fila_assinou(caso, card, a.aplicar)
                if not a.nao_mover and mover_card(card['cid'], COL_ASSINADO, a.aplicar):
                    levar_para_producao(card['cid'], a.aplicar)
                    avisar_time('Assinatura: %s (card %s) assinou tudo. Marquei a fila, movi o '
                                'card para "Assinado" e levei para a Produção. Nada ficou '
                                'esperando rodada.' % (card['nome'], card['cid']), a.aplicar)
                    marcar(est, 'card:%s' % card['cid'], 'assinou_parado',
                           'movido para Assinado e producao', a.aplicar)
                    continue
            # coluna boa do fluxo: a esteira move a cada 30 minutos, entao da-se a folga de uma
            # rodada antes de reclamar. O Denilson assinou e andou sozinho em menos de uma hora,
            # e um alarme nesse intervalo seria barulho. Coluna fantasma nao ganha folga: dali
            # ninguem tira o card nunca.
            if not fantasma and horas_desde(ultima_assinatura) < HORAS_FOLGA:
                continue
            if not fantasma and card['pipe'] not in PIPES_CONTRATACAO:
                continue
            if no_cooldown(est, 'card:%s' % card['cid'], 'assinou_parado'):
                continue
            achados += 1
            print('\n#%s %s (%s)' % (card['cid'], card['nome'], caso['origem']))
            print('   ASSINOU TUDO e o card nao andou: esta em "%s"%s'
                  % (card['etapa'], ' (coluna que NAO EXISTE no quadro %s)' % card['pipe']
                     if fantasma else ''))
            # a fila pode simplesmente ainda nao saber: marcar resolve sozinho em meia hora
            marcou = (marcar_fila_assinou(caso, card, a.aplicar)
                      if caso['origem'] == 'reclamante' else False)
            if marcou and not fantasma:
                marcar(est, 'card:%s' % card['cid'], 'assinou_parado',
                       'marquei a fila; o vigia da contratacao move na proxima rodada', a.aplicar)
                avisar_time('Assinatura: %s (card %s) assinou todos os documentos. A fila ainda '
                            'nao sabia, entao eu marquei: o vigia da contratacao move o card para '
                            '"Assinado" na proxima rodada, sem ninguem precisar tocar.'
                            % (card['nome'], card['cid']), a.aplicar)
                continue
            avisar_time('Assinatura: %s (card %s) assinou todos os documentos e o card nao '
                        'andou. Ele esta em "%s"%s. A esteira so olha quem esta na coluna do '
                        'contrato, entao ele ficou fora do caminho e nao vai andar sozinho.'
                        % (card['nome'], card['cid'], card['etapa'],
                           ', uma coluna que nao existe no quadro dele' if fantasma else ''),
                        a.aplicar)
            tarefa(card, 'CONFERIR: %s assinou e o card não andou' % card['nome'][:26],
                   'Os documentos estão todos assinados no ZapSign. O card está em "%s". '
                   'Precisa ir para a coluna de assinado para a esteira levar ao próximo '
                   'passo.' % card['etapa'],
                   'assinatura-parado:%s' % card['cid'], a.aplicar)
            marcar(est, 'card:%s' % card['cid'], 'assinou_parado', card['etapa'], a.aplicar)
            continue

        if not pendentes:
            continue

        falou = reclamou(con, card['cid'])
        cabecalho_impresso = False
        links, plano_b_feito = [], False
        for tk, j, s in pendentes:
            nome_doc = (j.get('name') or '?')[:46]
            viu = s.get('times_viewed') or 0
            tem_tel = bool((s.get('phone_number') or '').strip())
            tem_email = bool((s.get('email') or '').strip())
            link = s.get('sign_url') or s.get('signing_link') or ''
            if link:
                links.append('%s: %s' % ((j.get('name') or 'documento').split(' - ')[0][:34], link))

            # ------------------------------------------------------------ 1. mudo
            if not tem_tel and not tem_email:
                if no_cooldown(est, tk, 'mudo'):
                    continue
                tel = celular11(caso['tel'] or card['tel'])
                achados += 1
                if not cabecalho_impresso:
                    print('\n#%s %s (%s)' % (card['cid'], card['nome'], caso['origem']))
                    cabecalho_impresso = True
                print('   MUDO: %s nasceu sem telefone e sem e-mail, nunca houve aviso' % nome_doc)
                if not tel:
                    tarefa(card, 'CONSEGUIR O NÚMERO: %s não tem telefone para assinar'
                           % card['nome'][:24],
                           'O documento "%s" está no ZapSign sem telefone e sem e-mail, então '
                           'nunca saiu aviso nenhum para ele. Precisa de um número de WhatsApp '
                           'para a assinatura andar.' % nome_doc,
                           'assinatura-sem-numero:%s' % card['cid'], a.aplicar)
                    marcar(est, tk, 'mudo', 'sem telefone nenhum', a.aplicar)
                    continue
                print('      preenchendo o telefone %s e reenviando' % tel)
                if a.aplicar:
                    ok, _r = zs.atualizar_signatario(s.get('token'), phone_country='55',
                                                     phone_number=tel,
                                                     send_automatic_whatsapp=True)
                    ok2, r2 = zs.reenviar_notificacoes(tk)
                    print('      telefone=%s | reenvio=%s %s'
                          % (ok, ok2, str(r2.get('sent_count', r2))[:40]))
                marcar(est, tk, 'mudo', 'telefone %s preenchido e reenviado' % tel, a.aplicar)
                continue

            # ------------------------------------------------------------ 2 e 3. socorro
            motivo = None
            if falou:
                motivo = 'ele escreveu: "%s"' % falou[:120]
            elif viu >= TOQUES_SOCORRO:
                motivo = 'abriu %d vezes e nao concluiu' % viu
            if motivo:
                if s.get('auth_mode') == PLANO_B or no_cooldown(est, tk, 'plano_b'):
                    continue
                if card['pipe'] not in PIPES_CONTRATACAO and not falou:
                    # ja e cliente, ou ja esta na producao, e nao reclamou agora: a peca pendente
                    # pode ter sido resolvida no papel (o Leopoldo tem uma procuracao de 08/07
                    # aberta 15 vezes, e o caso dele ja anda). Cobrar de novo seria pedir a mesma
                    # assinatura duas vezes, entao aqui o vigia so chama gente.
                    achados += 1
                    if not cabecalho_impresso:
                        print('\n#%s %s (%s)' % (card['cid'], card['nome'], caso['origem']))
                        cabecalho_impresso = True
                    print('   TRAVADO em %s: %s, mas o card ja esta no quadro %s: NAO falo com '
                          'ele, so abro tarefa.' % (nome_doc, motivo, card['pipe']))
                    tarefa(card, 'CONFERIR: %s tem documento sem assinar'
                           % (nome_real or card['nome'])[:26],
                           'O documento "%s" está pendente de assinatura no ZapSign (%s) e o caso '
                           'dele já andou. Vale abrir a pasta e ver se essa peça foi assinada no '
                           'papel antes de pedir de novo.' % (nome_doc, motivo),
                           'assinatura-cliente:%s' % card['cid'], a.aplicar)
                    marcar(est, tk, 'plano_b', 'fora da contratacao: so avisei', a.aplicar)
                    continue
                achados += 1
                if not cabecalho_impresso:
                    print('\n#%s %s (%s)' % (card['cid'], card['nome'], caso['origem']))
                    cabecalho_impresso = True
                print('   TRAVADO em %s: %s' % (nome_doc, motivo))
                print('      plano B: %s -> %s (so o codigo, sem desenho) e reenvio'
                      % (s.get('auth_mode'), PLANO_B))
                if a.aplicar:
                    ok, _r = zs.atualizar_signatario(s.get('token'), auth_mode=PLANO_B,
                                                     send_automatic_whatsapp=True)
                    ok2, r2 = zs.reenviar_notificacoes(tk)
                    print('      modo=%s | reenvio=%s %s'
                          % (ok, ok2, str(r2.get('sent_count', r2))[:40]))
                marcar(est, tk, 'plano_b', motivo[:120], a.aplicar)
                plano_b_feito = True
                continue

            # ------------------------------------------------------------ 4. nunca abriu
            if s.get('status') == 'new' and viu == 0:
                nascido = (j.get('created_at') or '')[:10]
                if dias_uteis_desde(nascido) < 1 or no_cooldown(est, tk, 'reenvio'):
                    continue
                achados += 1
                if not cabecalho_impresso:
                    print('\n#%s %s (%s)' % (card['cid'], card['nome'], caso['origem']))
                    cabecalho_impresso = True
                if card['pipe'] not in PIPES_CONTRATACAO:
                    # quem ja esta na producao ou em clientes tem documento de outro momento
                    # parado (o Ezequiel pagou o GR e tem um jogo do trabalhista de 16/10 sem
                    # abrir). Reenviar sozinho confundiria o cliente: isso e conferencia humana.
                    print('   NUNCA ABRIU: %s, criado em %s, zero abertura. O card ja esta no '
                          'quadro %s, entao NAO reenvio sozinho.' % (nome_doc, nascido, card['pipe']))
                    tarefa(card, 'CONFERIR: documento de %s nunca foi aberto'
                           % (nome_real or card['nome'])[:24],
                           'O documento "%s", criado em %s, nunca foi aberto pelo cliente, e ele '
                           'já está em outra fase. Vale conferir se essa peça ainda é necessária '
                           'antes de pedir a assinatura.' % (nome_doc, nascido),
                           'assinatura-orfa:%s' % card['cid'], a.aplicar)
                    marcar(est, tk, 'reenvio', 'orfao, so avisei', a.aplicar)
                    continue
                print('   NUNCA ABRIU: %s, criado em %s, zero abertura. Reenviando o aviso.'
                      % (nome_doc, nascido))
                if a.aplicar:
                    ok2, r2 = zs.reenviar_notificacoes(tk)
                    print('      reenvio=%s %s' % (ok2, str(r2.get('sent_count', r2))[:40]))
                marcar(est, tk, 'reenvio', 'criado em %s, zero abertura' % nascido, a.aplicar)

        # uma mensagem nossa por cliente por dia, e so quando o plano B entrou em algum documento
        if plano_b_feito and links:
            ja = ((est.get('clientes') or {}).get(str(card['cid'])) or {}).get('msg_em')
            if ja != hoje():
                texto = MSG_PLANO_B.format(primeiro=primeiro_nome(nome_real or card['nome']),
                                           links='\n'.join(links))
                if enviar_cliente(card['tel'], texto, a.aplicar) and a.aplicar:
                    est.setdefault('clientes', {}).setdefault(str(card['cid']), {})['msg_em'] = hoje()
            tarefa(card, 'LIGAR: %s não está conseguindo assinar'
                   % (nome_real or card['nome'])[:26],
                   'Ele tentou assinar e não conseguiu (%s). Troquei para assinatura só com '
                   'código no WhatsApp, sem desenho, e reenviei os links. Liga para ele e assina '
                   'junto se precisar.' % (falou or 'abriu várias vezes sem concluir'),
                   'assinatura-travada:%s:%s' % (card['cid'], hoje()), a.aplicar)

    con.close()
    if a.aplicar:
        _salvar(ESTADO, est)
    print('\n%d achado(s). %s' % (achados, 'estado gravado.' if a.aplicar
                                  else '(simulacao: nada mudou e nada foi enviado)'))


if __name__ == '__main__':
    main()
