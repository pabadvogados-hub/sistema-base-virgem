# -*- coding: utf-8 -*-
"""QUADRO DA CONTRATACAO: tarefa diaria da equipe comercial no modulo Tarefas do Sistema Escritório Modelo.

Pedido da Advogada em 01/10/2026. Todo dia util as 9h nasce para a a equipe (usuaria 10 do Sistema Escritório Modelo;
ela nao tem usuario no ADVBOX e estamos em migracao de sistemas) uma tarefa com o quadro de quem ainda
nao chegou a Producao da Inicial. As 9h a foto do CRM e o fechamento do dia anterior.

Ordem de trabalho (Advogada, 02/10/2026):
  1. ASSINOU E NAO MANDOU DOCUMENTOS: quem assinou o contrato e ainda nao tem a inicial pronta, em
     qualquer quadro do CRM (o primeiro e mais importante);
  2. COBRAR ASSINATURA: quem esta no Contratação com o contrato no ZapSign sem assinar;
  3. RECUPERAR: quem disse que fechou e nao respondeu ou se perdeu (sem os documentos do contrato,
     Perdido na contratacao, ou de volta ao Reclamantes). Quem marcou "sem interesse" ou foi
     desqualificado fica de fora: quem ajusta os cards e a equipe comercial.

Regras da Advogada (01/10/2026): so ACRESCENTA a tarefa do dia; nao altera nem cancela tarefa nenhuma, nao
move card, nao manda mensagem e nao conta comissao; tarefa nao concluida fica aberta e atrasada. Os
contratos ja enviados nao serao refeitos: o quadro trata todos como assinatura a cobrar.

Uso:  python3 quadro_contratacao.py            (simulacao: mostra a tarefa, nao grava nada)
      python3 quadro_contratacao.py --enviar   (abre a tarefa do dia)
"""
import io
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv(os.getenv('ESC_CONFIG_ENV', '/opt/escritorio/config/.env'))
load_dotenv('/opt/escritorio/config/.env', override=False)
import requests  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DB = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
P_LEADS, P_CONTRATACAO, P_PRODUCAO, P_CLIENTES = 10, 20, 18, 11
INICIAL_PRONTA = ('Producao da Inicial', 'Revisao', 'Distribuicao', 'Distribuida')
FORA = ('perdido - sem interesse', 'lead desqualificado', 'desqualificado')   # decisao ja tomada
DOCS_CONTRATO = {'cnh': 'CNH', 'comprovante_residencia': 'comprovante de residência'}
RESP = (int(os.getenv('QUADRO_RESP_ID', '10')), os.getenv('QUADRO_RESP_NOME', 'a equipe Araújo'))
ORIGEM = 'quadro-contratacao'


def _json(nome):
    try:
        return json.load(open(os.path.join(HERE, nome), encoding='utf-8'))
    except Exception:
        return {}


def _tel8(t):
    return ''.join(c for c in str(t or '') if c.isdigit())[-8:]


def _data(s):
    """Data da base (UTC, texto) em horario de Brasilia."""
    if not s:
        return None
    try:
        return datetime.strptime(str(s).replace('T', ' ')[:19], '%Y-%m-%d %H:%M:%S') - timedelta(hours=3)
    except ValueError:
        return None


def _assinatura(tokens):
    """Situacao do cliente nos documentos do ZapSign (so GET)."""
    tok = os.getenv('ZAPSIGN_API_TOKEN', '')
    st = []
    for t in tokens:
        try:
            r = requests.get(f"https://api.zapsign.com.br/api/v1/docs/{t['token']}/",
                             headers={'Authorization': 'Bearer ' + tok}, timeout=30)
            r.raise_for_status()
            d = r.json()
        except Exception:
            st.append('?')
            continue
        cli = [s for s in d.get('signers', []) if 'ADVOGADA' not in (s.get('name') or '').upper()]
        st.append(cli[0].get('status') if cli else d.get('status'))
    feitos = st.count('signed')
    if st and feitos == len(st):
        return 'assinou tudo', True
    if feitos:
        return f'assinou {feitos} de {len(st)}', True
    if 'link-opened' in st:
        return 'abriu o link e não assinou', True
    return 'não abriu o link', False


def montar(agora=None):
    """(titulo, texto, n_cards) do quadro do momento."""
    agora = agora or (datetime.utcnow() - timedelta(hours=3))
    hoje = agora.date()
    cst = _json('contratacao_state.json').get('conversas', {})
    fass = _json('cobranca_fila.json').get('clientes', {})
    fdoc = _json('cobranca_documentos_fila.json').get('clientes', {})
    por_tel = lambda fila, tel: next((v for v in fila.values() if _tel8(v.get('telefone')) == _tel8(tel)), None)
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=20)

    def card(cid=None, tel=None):
        sql = """select c.id, c.contato_nome, c.contato_telefone, c.pipeline_id, c.etapa, c.atualizado_em,
            (select max(criado_em) from wa_board_hist h where h.conversa_id=c.id and h.para_etapa=c.etapa and h.bloqueado=0),
            (select max(criado_em) from wa_mensagens m where m.conversa_id=c.id and m.origem='contato'),
            (select count(*) from wa_mensagens m where m.conversa_id=c.id and m.origem='nota'
               and m.conteudo like 'Ligacao pelo WhatsApp encerrada (nao atendida%' and m.criado_em >= datetime('now','-14 days'))
            from wa_conversas c """
        if cid is not None:
            r = con.execute(sql + 'where c.id=?', (int(cid),)).fetchone()
        else:
            r = con.execute(sql + "where substr(replace(c.contato_telefone,'+',''),-8)=? order by c.atualizado_em desc limit 1",
                            (_tel8(tel),)).fetchone()
        if not r:
            return None
        k = ('id', 'nome', 'tel', 'pid', 'etapa', 'atu', 'entrou', 'ucli', 'lig')
        return dict(zip(k, r))

    def ja_andou(c):
        return c['pid'] == P_CLIENTES or (c['pid'] == P_PRODUCAO and c['etapa'] in INICIAL_PRONTA)

    def fora(c):
        return (c['etapa'] or '').strip().lower() in FORA

    nomes_etapa = {P_CONTRATACAO: 'Contratação', P_PRODUCAO: 'Produção', P_LEADS: 'Leads', P_CLIENTES: 'Clientes'}
    vistos, itens = set(), []

    def base(c, nome_bom, pedido_em=None):
        ent, ult = _data(c['entrou']) or _data(c['atu']), _data(c['ucli'])
        return {'id': c['id'], 'nome': (nome_bom or c['nome'] or 'sem nome').strip().title(), 'tel': c['tel'],
                'onde': f"{nomes_etapa.get(c['pid'], 'outro quadro')} › {c['etapa']}",
                'dias': (hoje - ent.date()).days if ent else None,
                'calado': (hoje - ult.date()).days if ult else None, 'lig': c['lig'], 'trava': [], 'acao': '',
                'funil': (hoje - date.fromisoformat(pedido_em[:10])).days if pedido_em else None,
                'hoje': bool(ent and ent.date() == hoje), 'dra': False}

    # ---- 1. assinou e nao mandou documentos -------------------------------------------
    candidatos = {}
    for nome, d in fdoc.items():
        c = card(tel=d.get('telefone'))
        if c:
            candidatos[c['id']] = (c, nome, d)
    for r in con.execute("select id from wa_conversas where (pipeline_id=? and etapa='Aguardando Documentos') "
                         "or (pipeline_id=? and etapa='Assinado')", (P_PRODUCAO, P_CONTRATACAO)):
        if r[0] not in candidatos:
            c = card(cid=r[0])
            candidatos[c['id']] = (c, None, por_tel(fdoc, c['tel']) or {})
    for cid, (c, nome, d) in candidatos.items():
        if ja_andou(c) or fora(c):
            continue
        s = cst.get(str(cid), {})
        it = base(c, s.get('nome_contrato') or nome, s.get('pedido_em'))
        lista = d.get('checklist') or []
        it['trava'].append(f'faltam {len(lista)} documento(s) do caso' if lista else 'lista do caso ainda não montada')
        if d.get('toques'):
            u = d.get('ultimo_envio') or ''
            it['trava'].append(f"{d['toques']} cobrança(s) automática(s), a última em {u[8:10]}/{u[5:7]}"
                               + (', régua encerrada' if d.get('encerrado') else ''))
        it['acao'] = ('ligar e pedir: ' + '; '.join(x.split('(')[0].strip().lower() for x in lista[:4])
                      if lista else 'avisar a Dra. Advogada: falta montar a lista de documentos do caso')
        it['prio'] = 1
        itens.append(it)
        vistos.add(cid)

    # ---- 2. cobrar assinatura (Contratação com contrato no ZapSign) ---------------------
    for (cid,) in con.execute("select id from wa_conversas where pipeline_id=? and etapa<>'Perdido'", (P_CONTRATACAO,)):
        if cid in vistos:
            continue
        c = card(cid=cid)
        a = por_tel(fass, c['tel'])
        s = cst.get(str(cid), {})
        if not (a and a.get('tokens')):
            continue
        res, abriu = _assinatura(a['tokens'])
        if res == 'assinou tudo':
            continue
        it = base(c, s.get('nome_contrato') or a.get('nome'), s.get('pedido_em'))
        env_em = a.get('data_envio') or ''
        it['trava'].append('contrato' + (f' enviado em {env_em[8:10]}/{env_em[5:7]}' if env_em else '') + ': ' + res)
        if a.get('motivo_encerramento'):
            it['trava'].append('cobrança automática desligada de propósito')
            it['acao'], it['dra'] = 'falar com a Dra. Advogada antes de ligar', True
        else:
            if a.get('encerrado'):
                u = a.get('ultimo_envio') or ''
                it['trava'].append(f'lembrete automático terminou em {u[8:10]}/{u[5:7]}')
            it['acao'] = 'ligar e ajudar a assinar pelo celular' if abriu else 'ligar e pedir para abrir o link e assinar'
        it['prio'] = 2
        itens.append(it)
        vistos.add(cid)

    # ---- 3. recuperar: disse que fechou e nao respondeu ou se perdeu -----------------------
    for cid_txt, s in cst.items():
        c = card(cid=cid_txt)
        if not c or c['id'] in vistos or ja_andou(c) or fora(c):
            continue
        it = base(c, s.get('nome_contrato') or s.get('nome'), s.get('pedido_em'))
        if c['pid'] == P_CONTRATACAO and c['etapa'] != 'Perdido':
            falta = [v for k, v in DOCS_CONTRATO.items() if not (s.get('docs') or {}).get(k)]
            if falta:
                it['trava'].append('fechou e não mandou ' + ' e '.join(falta))
                it['acao'] = 'ligar e pedir ' + ' e '.join(falta) + ' para gerar o contrato'
            elif c['etapa'] == 'Fase de Contratação':
                it['acao'] = 'contrato sendo gerado: só acompanhar'
            else:
                it['acao'] = 'ligar e retomar'
        else:
            quando = s.get('perdido_em') or ''
            if quando:
                it['dias'] = (hoje - date.fromisoformat(quando[:10])).days
            it['trava'].append('fechou e se perdeu' + (f" (marcado como perdido em {quando[8:10]}/{quando[5:7]})" if quando else ''))
            it['acao'] = 'ligar e retomar: perguntar se ainda quer seguir com a ação'
        it['prio'] = 3
        itens.append(it)
        vistos.add(c['id'])
    con.close()

    itens.sort(key=lambda i: (i['prio'], i['dra'], -(i['dias'] or 0)))
    tit = {1: '1️⃣ ASSINOU E NÃO MANDOU DOCUMENTOS (o primeiro e mais importante)',
           2: '2️⃣ COBRAR ASSINATURA (Contratação)',
           3: '3️⃣ RECUPERAR: FECHOU E NÃO RESPONDEU OU SE PERDEU'}
    n = {p: sum(1 for i in itens if i['prio'] == p) for p in (1, 2, 3)}
    ontem = hoje - timedelta(days=3 if hoje.weekday() == 0 else 1)
    L = [f'📋 QUADRO DA CONTRATAÇÃO · fechamento de {ontem:%d/%m}, gerado em {agora:%d/%m %H:%M}',
         f'{len(itens)} clientes ainda não chegaram à Produção da Inicial. Trabalhe nesta ordem:',
         f'1️⃣ assinou e não mandou documentos: {n[1]} · 2️⃣ cobrar assinatura: {n[2]} · 3️⃣ recuperar: {n[3]}']
    prio = None
    for i in itens:
        if i['prio'] != prio:
            prio = i['prio']
            L += ['', tit[prio]]
        calado = f"cliente calado há {i['calado']}d" if i['calado'] is not None else 'cliente nunca escreveu'
        lig = f" · {i['lig']} ligação(ões) sem resposta" if i['lig'] else ''
        funil = f"no funil há {i['funil']}d · " if i['funil'] is not None else ''
        L.append(f"{i['nome']} #{i['id']} · 📱 {i['tel']}")
        L.append(f"   {funil}{i['onde']} há {i['dias']}d · {calado}{lig}")
        if i['trava']:
            L.append('   ' + ' · '.join(i['trava']))
        L.append(f"   ➜ {i['acao'] or 'acompanhar'}")
    L += ['', 'Esta tarefa nasce todo dia útil às 9h com o quadro do fechamento do dia anterior. Depois de cada '
              'ligação, atualize o card no CRM (quem desistiu vai para "Perdido - Sem interesse" e sai do quadro) '
              'e conclua esta tarefa quando terminar.']
    titulo = f'QUADRO DA CONTRATAÇÃO {hoje:%d/%m}: {n[1]} sem documentos, {n[2]} sem assinatura, {n[3]} a recuperar'
    return titulo, '\n'.join(L), len(itens)


def abrir(enviar):
    titulo, texto, n = montar()
    hoje = date.today().isoformat()
    ref = f'quadro:{hoje}'
    print(titulo)
    print(texto)
    if not enviar:
        print('\n(simulacao: nada gravado)')
        return
    con = sqlite3.connect(DB, timeout=20)
    con.execute('pragma busy_timeout=8000')
    if con.execute('select id from tarefas where origem=? and origem_ref=?', (ORIGEM, ref)).fetchone():
        con.close()
        print('tarefa de hoje ja existe; nada a fazer')
        return
    agora = time.time()
    cur = con.execute(
        'INSERT INTO tarefas (tipo, titulo, descricao, data, prazo_interno, prioridade, status, '
        'origem, origem_ref, criado_por, criado_em, atualizado_em) '
        "VALUES ('ACOMPANHAMENTO',?,?,?,?,'normal','aberta',?,?,?,?,?)",
        (titulo, texto, hoje, hoje, ORIGEM, ref, 'Comercial (automação)', agora, agora))
    tid = cur.lastrowid
    con.execute('INSERT INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, urgente) '
                'VALUES (?,?,?,0,0)', (tid, RESP[0], RESP[1]))
    con.commit()
    con.close()
    print(f'tarefa #{tid} aberta para {RESP[1]} ({n} clientes)')


if __name__ == '__main__':
    abrir('--enviar' in sys.argv)
