# -*- coding: utf-8 -*-
"""COMERCIAL: a mesa de trabalho do vendedor.

O vendedor fica em ligacao constante e FECHA na propria ligacao (nao agenda para a doutora).
Comissao: so no caso DISTRIBUIDO, no mes da distribuicao (valor por vendedor no .env: COMERCIAL_COMISSAO_*
no modulo Comercial). Nao ha comissao por atendimento nem por ligacao.

Equipe: VENDEDORES no .env (roteamento_vendedoras.py). Sem vendedor cadastrado, a fila nao sai.

Tres comandos:

  --fila     manda no WhatsApp do vendedor o bloco do dia, priorizado, com um CODIGO
             por lead. Sai dos que JA CONVERSARAM e estao parados no funil.
  --baixa    le as respostas dele no WhatsApp, aplica no CRM e registra a
             comissao. Ele responde "<codigo> <resultado>", por exemplo
             "812 fechou". Resultados aceitos abaixo em RESULTADOS.
  --painel   resumo do periodo: ligacoes, atendimentos, fechamentos, conversao
             e quanto cada um tem a receber.

Sem --enviar nada sai e nada e gravado. A fila do dia e dividida na proporcao da meta de
cada vendedor: --fila --vendedora <Nome>, cada um com o seu bloco, sem repetir lead no dia.
"""
import io
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv(os.getenv('ESC_CONFIG_ENV', '/opt/escritorio/config/.env'))
load_dotenv('/opt/escritorio/config/.env', override=False)

import requests
import roteamento_vendedoras as ROTA  # noqa: E402

DB = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
ESTADO = os.path.join(HERE, 'comercial_state.json')
PIPELINE = 10
ETAPAS_FILA = ('Caixa de Entrada', 'Campanha Fast-food', 'Campanha Supermercado', 'Campanha Acidente de Trabalho',
               'Campanha Geral', 'Lead Quente', 'Lead Qualificado', 'Contatado')
ETAPA_FECHOU = 'Contrato / Assinatura'
ETAPA_DESQUALIFICADO = 'Lead Desqualificado'
ETAPA_TRABALHANDO = 'Em Negociação'
ETAPA_SEM_INTERESSE = 'Perdido - Sem interesse'
ETAPA_SEM_RESPOSTA = 'Perdido - Sem resposta'
ETAPAS_ENCERRAM = (ETAPA_FECHOU, ETAPA_DESQUALIFICADO, ETAPA_SEM_INTERESSE, ETAPA_SEM_RESPOSTA)
BLOCO = int(os.getenv('COMERCIAL_BLOCO', '30'))        # total do dia, dividido pela meta de cada vendedor
JANELA = (8, 20)

VENDEDORAS = ROTA.VENDEDORAS                            # equipe do .env (VENDEDORES), com telefone e meta
LEGADO = next(iter(VENDEDORAS), '')                     # baixas antigas sem vendedor vao para o primeiro
ADVOGADA_TEL = os.getenv('COMERCIAL_PAINEL_TEL') or os.getenv('TEL_ALERTA_EQUIPE', '')   # quem recebe o painel

BACKEND_ESC = '/opt/escritorio/SISTEMA/backend'               # o modulo Comercial calcula a comissao
# planilha da mesa (opcional; vazio = nao cita no painel)
PLANILHA_LINK = os.getenv('PLANILHA_MESA_LINK', '')

ORDEM_TEMP = {'quentissimo': 0, 'quente': 1, 'morno': 2, 'frio': 3,
              'sem_resposta': 4, 'nao classificado': 5, 'sem_caso': 9}

# o que ela pode responder -> (rotulo, paga_atendimento, paga_contrato, etapa_destino)
RESULTADOS = {
    'fechou':      ('fechou contrato', True,  True,  ETAPA_FECHOU),
    'fechado':     ('fechou contrato', True,  True,  ETAPA_FECHOU),
    'contrato':    ('fechou contrato', True,  True,  ETAPA_FECHOU),
    'atendeu':     ('atendeu, em negociacao', True, False, ETAPA_TRABALHANDO),
    'falou':       ('atendeu, em negociacao', True, False, ETAPA_TRABALHANDO),
    'pensando':    ('atendeu, vai pensar', True, False, ETAPA_TRABALHANDO),
    'retornar':    ('atendeu, retornar depois', True, False, ETAPA_TRABALHANDO),
    'naoatende':   ('nao atendeu', False, False, None),
    'nao atende':  ('nao atendeu', False, False, None),
    'caixa':       ('caiu na caixa postal', False, False, None),
    'naoquer':     ('nao quer', True, False, ETAPA_SEM_INTERESSE),
    'nao quer':    ('nao quer', True, False, ETAPA_SEM_INTERESSE),
    'semresposta': ('parou de responder', False, False, ETAPA_SEM_RESPOSTA),
    'sem resposta': ('parou de responder', False, False, ETAPA_SEM_RESPOSTA),
    'jatem':       ('ja tem advogado', True, False, ETAPA_DESQUALIFICADO),
    'ja tem':      ('ja tem advogado', True, False, ETAPA_DESQUALIFICADO),
    'errado':      ('telefone errado', False, False, ETAPA_DESQUALIFICADO),
}

import wa_dapi  # noqa: E402  saida unica de WhatsApp pela D-API


# ------------------------------------------------------------------ basicos
def _json(path, padrao):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return padrao


def _salvar(path, dados):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)


def _norm(t):
    t = unicodedata.normalize('NFKD', t or '').encode('ascii', 'ignore').decode().lower()
    return ' '.join(t.split())


def _tel8(t):
    d = ''.join(c for c in str(t or '') if c.isdigit())
    return d[-8:]


def zapi_enviar_texto(telefone, texto):
    """Envia pela D-API (wa_dapi); o nome ficou para nao mexer em quem chama.
    ok so quando a D-API confirmou o envio."""
    d = ''.join(c for c in str(telefone) if c.isdigit())
    d = d if d.startswith('55') else '55' + d
    try:
        ok, info = wa_dapi.enviar_texto(d, texto)
        return bool(ok), json.dumps(info, ensure_ascii=False)[:140]
    except Exception as e:
        return False, str(e)[:140]


def _con(ro=True):
    if ro:
        return sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=20)
    con = sqlite3.connect(DB, timeout=20)
    con.execute('pragma busy_timeout=8000')
    return con


def mover_card(conversa_id, etapa):
    if not etapa:
        return False
    con = _con(ro=False)
    if not con.execute('select 1 from wa_stages where pipeline_id=? and nome=?',
                       (PIPELINE, etapa)).fetchone():
        con.close()
        return False
    con.execute('update wa_conversas set etapa=?, atualizado_em=? where id=?',
                (etapa, datetime.utcnow().isoformat(), conversa_id))
    con.commit()
    con.close()
    return True


# -------------------------------------------------------------------- fila
def candidatos():
    """Quem ja conversou de verdade, esta parado no funil e nao e cliente."""
    con = _con()
    clientes = set()
    for tel, cel in con.execute('select telefone, celular from jur_clientes'):
        for t in (tel, cel):
            if t:
                clientes.add(_tel8(t))
    estado = _json(ESTADO, {'leads': {}})
    hoje = date.today().isoformat()
    out = []
    q = ('select id, contato_nome, contato_telefone, etapa, atualizado_em '
         'from wa_conversas where pipeline_id=? and etapa in (%s)' % ','.join('?' * len(ETAPAS_FILA)))
    for cid, nome, tel, etapa, atu in con.execute(q, (PIPELINE,) + ETAPAS_FILA):
        if not tel or len(''.join(c for c in str(tel) if c.isdigit())) < 10:
            continue
        if _tel8(tel) in clientes:
            continue
        reg = estado['leads'].get(str(cid), {})
        if reg.get('encerrado'):
            continue
        if reg.get('ultimo_envio') == hoje:
            continue                       # ja foi na fila de hoje
        falas = con.execute("select count(*) from wa_mensagens where conversa_id=? "
                            "and origem='contato' and length(conteudo)>2", (cid,)).fetchone()[0]
        if falas < 2:
            continue                       # so clicou no anuncio, nao vale ligacao
        t = con.execute('select temperatura, situacao, violacoes, resumo from lead_termometro '
                        'where conversa_id=?', (cid,)).fetchone()
        temp = (t[0] if t else 'nao classificado')
        if temp == 'sem_caso':
            continue
        try:
            viol = json.loads(t[2])[0] if t and t[2] and json.loads(t[2]) else ''
        except Exception:
            viol = ''
        ult = con.execute("select max(criado_em) from wa_mensagens where conversa_id=? "
                          "and origem='contato'", (cid,)).fetchone()[0]
        try:
            dias = (datetime.now() - datetime.strptime(
                str(ult).replace('T', ' ').split('.')[0], '%Y-%m-%d %H:%M:%S')).days
        except Exception:
            dias = 999
        out.append({'id': cid, 'nome': nome or '', 'telefone': tel, 'etapa': etapa,
                    'temperatura': temp, 'situacao': (t[1] if t else '') or '',
                    'violacao': viol, 'dias': dias,
                    'tentativas': len(reg.get('historico', []))})
    con.close()
    out.sort(key=lambda x: (ORDEM_TEMP.get(x['temperatura'], 6), x['tentativas'], -x['dias']))
    return out


def texto_fila(bloco, nome=''):
    L = [f'📞 *FILA DE HOJE{" · " + nome.upper() if nome else ""}* · {len(bloco)} ligacoes · {date.today():%d/%m}',
         '',
         'Ligou? Responde aqui com o codigo e o resultado.',
         'Ex: *812 fechou* · *812 atendeu* · *812 nao atende* · *812 ja tem* · *812 nao quer* · '
         '*812 sem resposta*', '']
    for i, c in enumerate(bloco, 1):
        alerta = ' 🔥' if c['temperatura'] in ('quente', 'quentissimo') else ''
        nome = c['nome'] or 'Sem nome'
        L.append(f"*{i}. {nome}*{alerta}  `#{c['id']}`")
        L.append(f"   📱 {c['telefone']}")
        sit = c['situacao'] or 'situacao nao informada'
        L.append(f"   {c['temperatura']} · {sit} · parado ha {c['dias']}d")
        if c['violacao']:
            L.append(f"   Caso: {c['violacao'][:60]}")
        if c['tentativas']:
            L.append(f"   ⚠️ ja tentamos {c['tentativas']}x")
        L.append('')
    L.append('_Marque tudo, ate quem nao atendeu: e assim que a fila e o painel sabem onde cada lead esta._')
    L.append('')
    L.append(f'📋 Planilha completa da mesa (link fixo, atualizada em horário comercial): {PLANILHA_LINK}')
    return '\n'.join(L)


def enviar_fila(enviar, nome):
    vend = VENDEDORAS.get(nome)
    if not vend:
        print(f'vendedora desconhecida: {nome}. Use uma de: {", ".join(VENDEDORAS)}')
        return
    todos = candidatos()                   # quem ja foi na fila de hoje (da outra) nao volta
    bloco = todos[:ROTA.bloco_da_fila(nome, BLOCO)]
    print(f'{nome}: fila disponivel {len(todos)} | bloco de hoje: {len(bloco)}')
    for c in bloco:
        print(f"  #{c['id']:5d} {c['temperatura']:12s} {(c['nome'] or '')[:26]:26s} {c['telefone']} "
              f"parado {c['dias']}d")
    if not bloco:
        print('ninguem para ligar hoje')
        return
    txt = texto_fila(bloco, nome)
    if not enviar:
        print('\n--- previa ---\n' + txt[:1200])
        return
    ok, resp = zapi_enviar_texto(vend['tel'], txt)
    print('envio:', 'OK' if ok else f'FALHOU {resp}')
    if ok:
        est = _json(ESTADO, {'leads': {}})
        hoje = date.today().isoformat()
        for c in bloco:
            r = est['leads'].setdefault(str(c['id']), {'historico': []})
            r['ultimo_envio'] = hoje
            r['vendedora'] = nome
            r['nome'] = c['nome']
            r['telefone'] = c['telefone']
        _salvar(ESTADO, est)


# -------------------------------------------------------------------- baixa
def _interpretar(texto):
    """Devolve (codigo, chave_resultado) ou (None, None)."""
    t = _norm(texto)
    m = re.match(r'^#?(\d{1,6})\s+(.+)$', t)
    if not m:
        return None, None
    cod, resto = m.group(1), m.group(2).strip()
    for chave in sorted(RESULTADOS, key=len, reverse=True):
        if resto.startswith(chave):
            return int(cod), chave
    return int(cod), None


def processar_baixa(enviar):
    """Le o WhatsApp de cada vendedor; cada um tem a sua marca de ultima mensagem lida."""
    est = _json(ESTADO, {'leads': {}, 'ultima_msg_id': 0})
    por = est.setdefault('ultima_msg_id_por', {})
    if LEGADO and LEGADO not in por:
        por[LEGADO] = est.get('ultima_msg_id', 0)
    con = _con()
    lotes = []
    for nome, vend in VENDEDORAS.items():
        conv = con.execute('select id from wa_conversas where contato_telefone like ?',
                           ('%' + _tel8(vend['tel']),)).fetchone()
        if not conv:
            print(f'{nome}: ainda nao existe conversa dela no CRM. Peca para ela mandar um oi no numero do escritorio.')
            continue
        if nome not in por:
            # primeira vez: comeca do agora, para nao ler conversa antiga como baixa
            por[nome] = con.execute('select coalesce(max(id),0) from wa_mensagens where conversa_id=?',
                                    (conv[0],)).fetchone()[0]
            print(f'{nome}: passa a ser lida a partir da mensagem {por[nome]}')
            continue
        msgs = con.execute("select id, conteudo, criado_em from wa_mensagens where conversa_id=? "
                           "and origem='contato' and id>? order by id", (conv[0], por[nome])).fetchall()
        lotes.append((nome, msgs))
    con.close()
    aplicadas = ignoradas = 0
    for nome, msgs in lotes:
        print(f'mensagens novas da {nome}: {len(msgs)}')
        for mid, texto, quando in msgs:
            por[nome] = max(por[nome], mid)
            cod, chave = _interpretar(texto or '')
            if not cod:
                continue
            if not chave:
                print(f'  #{cod}: nao entendi "{(texto or "")[:40]}"')
                ignoradas += 1
                continue
            rot, paga_at, paga_ct, etapa = RESULTADOS[chave]
            reg = est['leads'].setdefault(str(cod), {'historico': []})
            reg['historico'].append({'em': str(quando)[:19], 'resultado': chave, 'rotulo': rot,
                                     'atendimento': paga_at, 'contrato': paga_ct, 'vendedora': nome})
            if etapa in ETAPAS_ENCERRAM:
                reg['encerrado'] = True
            print(f'  {nome} #{cod} -> {rot}' + (f' | card para {etapa}' if etapa else ''))
            if enviar and etapa:
                mover_card(cod, etapa)
            if enviar and paga_ct:
                zapi_enviar_texto(ADVOGADA_TEL,
                                  f"🎉 *Contrato fechado pela {nome}*\n"
                                  f"Lead #{cod} · {reg.get('nome') or ''} {reg.get('telefone') or ''}")
            aplicadas += 1
    est['ultima_msg_id'] = por.get(LEGADO, est.get('ultima_msg_id', 0))
    if enviar:
        _salvar(ESTADO, est)
    print(f'baixas aplicadas: {aplicadas} | nao entendidas: {ignoradas}'
          + ('' if enviar else '  (simulacao, nada gravado)'))


# ------------------------------------------------------------------- painel
def _vendedora_por_email(email):
    e = (email or '').lower()
    for n in VENDEDORAS:
        if n.lower() in e:
            return n
    return None


def _contagem_crm(corte, est):
    """O MOTOR CENTRAL do painel: os vendedores trabalham
    direto no CRM movendo cards, entao o painel nao pode depender so das baixas
    por codigo (#id resultado) — que quase nao vem — senao chega ZERADO.
    Le tres fontes do proprio sistema:
      - contratos: entrada real do card no vigia da contratacao (pedido_em),
        atribuida pela dona do card (atendente_email);
      - avancos/encerramentos: diferenca do snapshot diario de etapa dos cards
        de cada uma (semeado na 1a rodada; conta a partir da 2a);
      - ligacoes da telefonia: mensagens origem='ligacao' nas conversas dela."""
    con = _con()
    crm = {n: {'fechou': [], 'avancou': 0, 'encerrou': 0, 'lig_tel': 0} for n in VENDEDORAS}
    atual = {}
    for cid, atend, pipe, etapa, nome_lead in con.execute(
            "select id, coalesce(atendente_email,''), pipeline_id, etapa, "
            "coalesce(contato_nome,'') from wa_conversas "
            "where atendente_email is not null and atendente_email != ''"):
        v = _vendedora_por_email(atend)
        if v:
            atual[str(cid)] = {'v': v, 'p': pipe, 'e': etapa, 'n': nome_lead}
    try:  # contratos pela entrada na contratacao: data confiavel de fechamento
        cst = _json(os.path.join(HERE, 'contratacao_state.json'), {'conversas': {}})
        for cid, c in cst.get('conversas', {}).items():
            quando = (c.get('pedido_em') or '')[:10]
            info = atual.get(str(cid))
            if info and quando and quando >= str(corte):
                crm[info['v']]['fechou'].append((c.get('nome') or info['n'] or str(cid)).split()[0])
    except Exception as e:
        print(f'(contratos via vigia indisponiveis: {str(e)[:80]})')
    AVANCO = ('Lead Qualificado', 'Contatado', 'Em Negociação',
              'Pré fechamento [fluxo aquecimento]')
    antes = est.get('snapshot_crm', {})
    for cid, cur in atual.items():
        old = antes.get(cid)
        if not old or (old.get('e') == cur['e'] and old.get('p') == cur['p']):
            continue
        if cur['p'] == 20:
            continue  # fechou ja contado pela entrada no vigia
        if cur['e'] in AVANCO:
            crm[cur['v']]['avancou'] += 1
        elif cur['e'].startswith('Perdido') or cur['e'] == ETAPA_DESQUALIFICADO:
            crm[cur['v']]['encerrou'] += 1
    # As ligacoes sao feitas pelo WhatsApp (D-API) e ficam como NOTA no card
    # (autor='ligacao': "Ligacao pelo WhatsApp iniciada por <Nome> para +55...", depois
    # "encerrada (m:ss de conversa ...)" ou "encerrada (nao atendida ...)"). O filtro antigo
    # origem='ligacao' era da central/ramal e dava zero. A nota diz QUEM LIGOU, entao conta
    # por quem ligou. Ligacao atendida NAO pontua para comissao.
    for n in VENDEDORAS:
        crm[n]['lig_atendidas'] = 0
    abertas = {}
    inicio_utc = (datetime.combine(corte, datetime.min.time()) + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')
    for cid, txt in con.execute(
            "select conversa_id, coalesce(conteudo,'') from wa_mensagens where autor='ligacao' "
            "and replace(criado_em,'T',' ') >= ? order by id", (inicio_utc,)):
        m_ini = re.search(r'iniciada por (\S+)', txt)
        if m_ini:
            quem = next((n for n in VENDEDORAS if _norm(n) == _norm(m_ini.group(1))), None)
            abertas[cid] = quem
            if quem:
                crm[quem]['lig_tel'] += 1
        elif 'encerrada' in txt and cid in abertas:
            quem = abertas.pop(cid)
            if quem and 'de conversa' in txt and 'nao atendida' not in _norm(txt):
                crm[quem]['lig_atendidas'] += 1
    con.close()
    return crm, atual


def _conversao_produto(inicio=None):
    """Conversao por assunto, anuncio e situacao. Leads = conversas que chegaram no periodo nos quadros
    Reclamantes, Clientes, Producao, Contratacao e Palestra. Contrato = entrada na esteira de contratacao,
    sem os perdidos."""
    inicio = inicio or (date.today() - timedelta(days=90))
    con = _con()
    cst = _json(os.path.join(HERE, 'contratacao_state.json'), {'conversas': {}}).get('conversas', {})
    fechou = {int(k): (v.get('pedido_em') or '')[:10] for k, v in cst.items() if not v.get('perdido_em')}
    # quem desistiu (card em Perdido no CRM) nao conta como contrato
    for cid, etapa in con.execute("select id, coalesce(etapa,'') from wa_conversas where etapa like 'Perdido%'"):
        fechou.pop(cid, None)
    sit = {cid: s for cid, s in con.execute("select conversa_id, coalesce(situacao,'') from lead_termometro")}
    hoje = date.today()
    ini_mes = hoje.replace(day=1)
    janelas = {'desde': inicio, 'mes': ini_mes}
    grupos = {}
    for cid, criado, cam, tag, origem in con.execute(
            "select id, substr(replace(criado_em,'T',' '),1,19), e_reclamante, coalesce(tag,''), coalesce(origem,'') "
            "from wa_conversas where pipeline_id in (10, 11, 18, 20, 31) and replace(criado_em,'T',' ') >= ?",
            ((datetime.combine(inicio, datetime.min.time()) + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S'),)):
        dia = (datetime.fromisoformat(criado) - timedelta(hours=3)).date()
        reclamante = bool(cam) or 'RECLAMANTE' in tag.upper()
        chaves = [('produto', 'Reclamante' if reclamante else 'Outros assuntos'),
                  ('anuncio', origem.replace('Campanha ', '') if origem else 'Orgânico/sem origem')]
        if reclamante:
            chaves.append(('situacao', {'saiu': 'Já saiu', 'trabalhando': 'Trabalha'}.get(sit.get(cid, ''), 'Não disse')))
        for jan, d0 in janelas.items():
            if dia < d0:
                continue
            for dim, nome in chaves:
                g = grupos.setdefault((jan, dim, nome), [0, 0])
                g[0] += 1
                if cid in fechou:
                    g[1] += 1
    base_mes = sum(1 for cid, d in fechou.items() if d >= str(ini_mes)) - sum(
        g[1] for (jan, dim, _), g in grupos.items() if jan == 'mes' and dim == 'produto')
    con.close()

    def linha(dim):
        nomes = sorted({n for (j, d, n) in grupos if d == dim}, key=lambda n: -grupos.get(('desde', dim, n), [0, 0])[0])
        out = []
        for n in nomes:
            a = grupos.get(('desde', dim, n), [0, 0])
            b = grupos.get(('mes', dim, n), [0, 0])
            pa = f'{a[1] / a[0] * 100:.1f}%'.replace('.', ',') if a[0] else '-'
            pb = f'{b[1] / b[0] * 100:.1f}%'.replace('.', ',') if b[0] else '-'
            out.append(f'• {n}: {a[1]}/{a[0]} ({pa}) · mês {b[1]}/{b[0]} ({pb})')
        return out
    L = ['', f'🎯 *Conversão por produto* · contratos/leads que chegaram desde {inicio:%d/%m} · e no mês']
    L += linha('produto')
    L += ['_Por anúncio:_'] + linha('anuncio')
    L += ['_Reclamante por situação:_'] + linha('situacao')
    L += [f'Contratos de leads antigos (base) no mês: {max(0, base_mes)}']
    return L


def atribuir_quem_conduziu(enviar):
    """O contrato e de quem conduziu a venda. Card que entrou em "Fechou!" nos ultimos 3 dias e ainda
    nao foi visto aqui: a vendedora com mais contato ANTES do Fechou (regra comercial_kpi.quem_conduziu) vira a dona do
    card (o mesmo que o POST /atribuir faz, sem mexer na IA). Uma vez so por card: troca manual depois e respeitada.
    Sem vendedora no contato, a dona fica como esta."""
    if BACKEND_ESC not in sys.path:
        sys.path.insert(0, BACKEND_ESC)
    import types
    if 'app.deps' not in sys.modules:
        deps = types.ModuleType('app.deps')
        deps.requer_admin = deps.usuario_atual = lambda: None
        sys.modules['app.deps'] = deps
    from app.routers import comercial_kpi as ck
    email = {v: e for e, v in ck.EMAIL_VENDEDORA.items()}
    est = _json(ESTADO, {'leads': {}})
    feitos = est.setdefault('dona_atribuida', {})
    con = _con(ro=not enviar)
    con.row_factory = None
    desde = (datetime.utcnow() - timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')
    linhas = list(con.execute("select conversa_id, min(criado_em) from wa_board_hist where para_etapa like 'Fechou%' "
                              "and bloqueado=0 group by conversa_id having min(replace(criado_em,'T',' ')) >= ?", (desde,)))
    mudou = 0
    for cid, quando in linhas:
        if str(cid) in feitos:
            continue
        atual = (con.execute('select coalesce(atendente_email,\'\') from wa_conversas where id=?', (cid,)).fetchone() or [''])[0]
        quem = ck.quem_conduziu(con, cid, str(quando).replace('T', ' ')[:19])
        alvo = email.get(quem) if quem else None
        if alvo and alvo != atual:
            print(f'  card {cid}: dona {atual or "(vazia)"} -> {alvo} (conduziu a venda)')
            if enviar:
                con.execute('update wa_conversas set atendente_email=?, atualizado_em=? where id=?',
                            (alvo, datetime.utcnow().isoformat(), cid))
                con.commit()
            mudou += 1
        if enviar:
            feitos[str(cid)] = {'de': atual, 'para': alvo or atual, 'em': datetime.now().strftime('%Y-%m-%d %H:%M')}
    con.close()
    if enviar:
        _salvar(ESTADO, est)
    print(f'dona do card por quem conduziu: {mudou} troca(s) em {len(linhas)} card(s) recentes em Fechou')


def _modulo_comercial():
    """Comissao: so no caso DISTRIBUIDO, no mes da distribuicao. Sai do MESMO calculo do modulo Comercial do
    Sistema Escritório Modelo (app/routers/comercial_kpi.py), para nao nascer um segundo placar."""
    if BACKEND_ESC not in sys.path:
        sys.path.insert(0, BACKEND_ESC)
    # O comercial_kpi so precisa do app.deps para a trava de login da rota; aqui ela nao e usada, entao entra
    # um substituto e o banco do sistema nunca e importado por este processo.
    import types
    if 'app.deps' not in sys.modules:
        deps = types.ModuleType('app.deps')
        deps.requer_admin = deps.usuario_atual = lambda: None
        sys.modules['app.deps'] = deps
    from app.routers import comercial_kpi as ck
    return ck.montar('mes')


def _conversao_do_modulo(modulo):
    """Conversao por assunto, anuncio e situacao do modulo Comercial: mesma conta da tela, com a
    situacao corrigida (quem fechou nao perde o 'ja saiu') e os contratos fechados fora da esteira."""
    cv = modulo['conversao']

    def p(v):
        return f'{v * 100:.1f}%'.replace('.', ',') if v is not None else '-'

    def linhas(dim):
        return [f"• {x['nome']}: {x['contratos']}/{x['leads']} ({p(x['pct'])}) · mês {x['contratos_mes']}/{x['leads_mes']} "
                f"({p(x['pct_mes'])})" for x in cv[dim]]
    return (['', '🎯 *Conversão* · contratos/leads desde o início da série · e no mês'] + linhas('produto')
            + ['_Reclamante por situação (já saiu x trabalha):_'] + linhas('situacao')
            + ['_Por anúncio:_'] + linhas('anuncio'))


def painel(desde=None, enviar=False):
    """Um bloco por vendedor, cada um com a sua comissao. Vai para COMERCIAL_PAINEL_TEL."""
    est = _json(ESTADO, {'leads': {}})
    corte = desde or (date.today() - timedelta(days=date.today().weekday()))
    crm, snapshot_atual = _contagem_crm(corte, est)
    primeira_vez = not est.get('snapshot_crm')
    conta = {n: {'lig': 0, 'at': 0, 'ct': 0} for n in VENDEDORAS}
    for reg in est['leads'].values():
        for h in reg.get('historico', []):
            try:
                d = datetime.strptime(h['em'][:10], '%Y-%m-%d').date()
            except Exception:
                continue
            if d < corte:
                continue
            c = conta.setdefault(h.get('vendedora') or LEGADO, {'lig': 0, 'at': 0, 'ct': 0})
            c['lig'] += 1
            c['at'] += 1 if h.get('atendimento') else 0
            c['ct'] += 1 if h.get('contrato') else 0

    def brl(v):
        return f'R$ {v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

    L = [f'📊 *Painel do comercial* · desde {corte:%d/%m}']
    saida = {}
    try:
        modulo = _modulo_comercial()
        com = modulo['comissoes']
    except Exception as e:  # o painel nunca pode morrer por causa da comissao
        modulo, com = None, {}
        print(f'(comissao por distribuicao indisponivel: {str(e)[:80]})')
    # NUMERO UNICO por vendedor: o
    # contrato oficial e o do CRM; ligacoes = telefonia (por quem ligou) +
    # baixas; atendimentos = cards avancados + baixas. Nada de duas contagens
    # paralelas na mensagem.
    for nome, c in conta.items():
        lig, at, ct = c['lig'], c['at'], c['ct']
        c2 = crm.get(nome, {'fechou': [], 'avancou': 0, 'encerrou': 0, 'lig_tel': 0, 'lig_atendidas': 0})
        fech = c2['fechou']
        ct_total = max(len(fech), ct)
        at_total = at + c2['avancou']
        lig_total = lig + c2['lig_tel']
        cv = com.get(nome)
        a_receber = cv['liberada_mes'] if cv else 0.0
        meta = VENDEDORAS.get(nome, {}).get('meta')
        L += ['', f'*{nome}*' + (f' · meta de {meta} contratos no mes' if meta else ''),
              f'📞 Ligações: *{lig_total}*'
              + (f' ({c2["lig_tel"]} pelo WhatsApp, {c2.get("lig_atendidas", 0)} atendidas'
                 + (f' + {lig} por baixa' if lig else '') + ')' if lig_total else ''),
              f'🗣️ Atendimentos: *{at_total}*'
              + (f' ({c2["avancou"]} por card avançado + {at} por baixa)' if at_total else ''),
              f'✍️ Contratos fechados: *{ct_total}*'
              + (f' ({", ".join(fech[:4])})' if fech else ''),
              (f'💰 A receber no mês: *{brl(a_receber)}* (R$ {cv["taxa"]:.0f} por card em Distribuição) · '
               f'a liberar {brl(cv["a_liberar"])} · {cv["fechados"]} fechados, {cv["distribuidos"]} em Distribuição'
               if cv else '💰 Comissão: indisponível agora (ver o módulo Comercial do Sistema Escritório Modelo)')]
        saida[nome] = {'ligacoes': lig_total, 'atendimentos': at_total,
                       'contratos': ct_total, 'a_receber': a_receber, 'crm': c2}
    if modulo:
        t = modulo['visao']['mes']
        L += ['', '*No mês*', f'⚖ Trabalhista: *{t["contratos"]} de {t["meta"]}* contratos']
    if primeira_vez:
        L += ['', '(leitor do CRM ligado agora: cards avançados começam a contar amanhã)']
    try:
        L += _conversao_do_modulo(modulo) if modulo else _conversao_produto()
    except Exception as e:  # o painel nunca pode morrer por causa deste bloco
        print(f'(conversao por produto indisponivel: {str(e)[:80]})')
    L += ['', 'Fontes: CRM (cards e contratação) + ligações pelo WhatsApp (por quem ligou) + '
              'baixas por código (#id resultado).'] + ([f'📋 Planilha da mesa: {PLANILHA_LINK}'] if PLANILHA_LINK else [])
    txt = '\n'.join(L)
    print(txt)
    if enviar:
        est['snapshot_crm'] = snapshot_atual
        _salvar(ESTADO, est)
        ok, _ = zapi_enviar_texto(ADVOGADA_TEL, txt)
        print('\nenvio:', 'OK' if ok else 'FALHOU')
    return saida


if __name__ == '__main__':
    env = '--enviar' in sys.argv
    if '--baixa' in sys.argv:
        processar_baixa(env)
        try:
            atribuir_quem_conduziu(env)
        except Exception as e:  # a baixa nunca pode morrer por causa da atribuicao
            print(f'(atribuicao por quem conduziu indisponivel: {str(e)[:120]})')
    elif '--painel' in sys.argv:
        painel(enviar=env)
    else:
        if '--vendedora' not in sys.argv:
            print('informe --vendedora <Nome> (um dos nomes em VENDEDORES: %s)' % (', '.join(VENDEDORAS) or 'nenhum'))
            sys.exit(1)
        enviar_fila(env, sys.argv[sys.argv.index('--vendedora') + 1])
