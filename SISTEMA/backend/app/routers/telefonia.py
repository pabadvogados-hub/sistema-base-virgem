"""TELEFONIA: ligacao pela linha Nvoip do escritorio (NVOIP_NUMERO_SAIDA no .env) dentro do Sistema Escritório Modelo.

Dois jeitos de ligar, o mesmo motor:
  * na conversa: "Ligar pela linha" liga so para aquele lead;
  * aba Telefonia: o discador liga para ate 5 leads de uma vez; o primeiro que atende vai para a
    atendente, os outros sao derrubados e voltam para o topo da fila.

Como a voz anda
  A atendente entra uma vez na SALA dela: o navegador (JsSIP, ramal dela) liga para a extensao
  `discador` do contexto [softphone], que vai para [discador-agente] (ConfBridge disc_<ramal>).
  O sistema origina as ligacoes pelo AMI (usuario `discador`, so localhost) pela linha Nvoip dela.
  Lead que atende cai em [discador-lead]: o primeiro pega a trava (TRYLOCK) e entra na sala;
  o dialplan avisa este router em GET /api/telefonia/evento (so de dentro do servidor).

Fila (ordem de prioridade):
  0 derrubado (atendeu e caiu porque outro atendeu antes)   -> 1 por vez
  1 respondeu nos ultimos 15 min                            -> 1 por vez
  2 retorno marcado vencido                                  -> 1 por vez
  3 quente (coluna quente / ligar ja)                       -> 1 por vez
  4 ja tentado sem falar                                     -> ate 5 de uma vez
  5 resto da coluna                                          -> ate 5 de uma vez
Limites: 6 tentativas em 10 dias, 3 por dia, 2 h entre tentativas, 7h00-20h30 (seg-sab).
"""
import asyncio
import io
import math
import os
import re
import sqlite3
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import Usuario, get_db
from ..deps import usuario_atual

router = APIRouter(prefix='/api/telefonia', tags=['telefonia'])

DB = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
ENV = '/opt/escritorio/config/.env'
GRAV = '/opt/escritorio/ligacoes_discador'
TZ = ZoneInfo('America/Sao_Paulo')
NUMERO_SAIDA = ''.join(ch for ch in os.getenv('NVOIP_NUMERO_SAIDA', '') if ch.isdigit())   # 55DDDNUMERO
POR_VEZ_MAX = 5
CPS_INTERVALO = 2.0          # segundos entre uma chamada nova e outra (limite de CPS da Nvoip)
TENTATIVAS_10D, TENTATIVAS_DIA, INTERVALO_S = 6, 3, 2 * 3600
HORA_INI, HORA_FIM = (7, 0), (20, 30)
QUENTE = re.compile(r'quente|ligar j[aá]|retorno', re.I)
ALUCINA = re.compile(r'legenda|obrigad[oa] por assistir|inscreva-se|amara\.org|transcri[cç][aã]o (por|e legenda)', re.I)
FINAIS = ('fechou', 'sem_interesse', 'numero_errado', 'nao_ligar')


def _env(chave, padrao=''):
    v = os.environ.get(chave)
    if v:
        return v
    try:
        for linha in io.open(ENV, encoding='utf-8'):
            m = re.match(r'\s*%s\s*=\s*(.*)\s*$' % re.escape(chave), linha)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    except Exception:
        pass
    return padrao


def _con():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS tel_chamadas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, rodada TEXT, agente TEXT, usuario_email TEXT,
        conversa_id INTEGER, telefone TEXT, nome TEXT, prioridade INTEGER, modo TEXT,
        status TEXT, motivo TEXT, criado_em REAL, atendeu_em REAL, fim_em REAL, duracao INTEGER,
        uid TEXT, canal TEXT, arquivo TEXT, resultado TEXT, nota TEXT, retorno_em REAL)''')
    c.execute('CREATE INDEX IF NOT EXISTS ix_tel_conv ON tel_chamadas(conversa_id, criado_em)')
    c.execute('CREATE INDEX IF NOT EXISTS ix_tel_dia ON tel_chamadas(agente, criado_em)')
    return c


def _agora_br():
    return datetime.now(TZ)


def _no_horario():
    a = _agora_br()
    hm = (a.hour, a.minute)
    return HORA_INI <= hm <= HORA_FIM          # todos os dias, domingo inclusive


def _numero(tel):
    """'+55 (11) 91234-5678' -> '5511912345678'; None se nao for telefone brasileiro discavel."""
    d = re.sub(r'\D', '', str(tel or ''))
    if not d or len(d) > 13:
        return None
    if d.startswith('0'):
        d = d.lstrip('0')
    if not d.startswith('55'):
        d = '55' + d
    if len(d) == 12 and d[4] in '6789':      # celular antigo sem o 9
        d = d[:4] + '9' + d[4:]
    return d if len(d) in (12, 13) else None


# ---------------------------------------------------------------------------
# AMI (Asterisk Manager): uma conexao so, aberta sob demanda
# ---------------------------------------------------------------------------
class AMI:
    def __init__(self):
        self.r = self.w = None
        self.esperando = {}
        self.lock = asyncio.Lock()
        self.leitor = None

    async def conectar(self):
        if self.w and not self.w.is_closing():
            return
        self.r, self.w = await asyncio.open_connection('127.0.0.1', 5038)
        await self.r.readline()                      # banner
        self.leitor = asyncio.get_running_loop().create_task(self._ler())
        resp = await self.acao({'Action': 'Login', 'Username': _env('AMI_DISCADOR_USER', 'discador'),
                                'Secret': _env('AMI_DISCADOR_SECRET'), 'Events': 'call'})
        if resp.get('Response') != 'Success':
            raise RuntimeError('AMI recusou o login: %s' % resp.get('Message'))

    async def _ler(self):
        bloco, lista = {}, None
        try:
            while True:
                linha = await self.r.readline()
                if not linha:
                    break
                linha = linha.decode('utf-8', 'replace').rstrip('\r\n')
                if linha:
                    if ':' in linha:
                        k, v = linha.split(':', 1)
                        bloco[k.strip()] = v.strip()
                    continue
                if not bloco:
                    continue
                aid = bloco.get('ActionID')
                fut = self.esperando.get(aid)
                if fut is not None and getattr(fut, '_originate', False):
                    # Originate assincrono: a 1a resposta e so "queued"; o resultado vem no evento OriginateResponse
                    if bloco.get('Event') == 'OriginateResponse' or bloco.get('Response') == 'Error':
                        self.esperando.pop(aid, None)
                        if not fut.done():
                            fut.set_result(bloco)
                elif fut is not None:
                    if 'Event' in bloco:                # listas (CoreShowChannels)
                        if bloco['Event'].endswith('Complete'):
                            self.esperando.pop(aid, None)
                            if not fut.done():
                                fut.set_result(fut._lista if hasattr(fut, '_lista') else [])
                        else:
                            if not hasattr(fut, '_lista'):
                                fut._lista = []
                            fut._lista.append(bloco)
                    elif bloco.get('EventList') == 'start':
                        fut._lista = []
                    else:
                        self.esperando.pop(aid, None)
                        if not fut.done():
                            fut.set_result(bloco)
                bloco = {}
        except Exception:
            pass
        finally:
            for f in self.esperando.values():
                if not f.done():
                    f.set_exception(RuntimeError('AMI caiu'))
            self.esperando.clear()
            self.w = None

    async def acao(self, campos, lista=False, timeout=10):
        aid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        if campos.get('Action') == 'Originate':
            fut._originate = True
        self.esperando[aid] = fut
        partes = []
        for k, v in campos.items():
            for item in (v if isinstance(v, list) else [v]):
                partes.append('%s: %s' % (k, item))
        partes.append('ActionID: ' + aid)
        self.w.write(('\r\n'.join(partes) + '\r\n\r\n').encode())
        await self.w.drain()
        return await asyncio.wait_for(fut, timeout)

    async def fazer(self, campos, lista=False, timeout=10):
        async with self.lock:
            await self.conectar()
        return await self.acao(campos, lista, timeout)


ami = AMI()


async def _desligar_uids(uids):
    """Derruba os canais destes UNIQUEIDs (os que ainda estao tocando)."""
    uids = {u for u in uids if u}
    if not uids:
        return 0
    try:
        canais = await ami.fazer({'Action': 'CoreShowChannels'}, lista=True, timeout=8)
    except Exception:
        return 0
    n = 0
    for c in canais if isinstance(canais, list) else []:
        if c.get('Uniqueid') in uids or c.get('UniqueID') in uids:
            try:
                await ami.fazer({'Action': 'Hangup', 'Channel': c.get('Channel'), 'Cause': '16'})
                n += 1
            except Exception:
                pass
    return n


# ---------------------------------------------------------------------------
# sessoes (em memoria; o historico fica em tel_chamadas)
# ---------------------------------------------------------------------------
SESS = {}      # ramal -> dict


async def _sala_viva(ramal):
    """A atendente esta na sala disc_<ramal> do Asterisk? (a memoria do servidor se perde quando ele reinicia;
    o servico roda como www-data e nao fala com o asterisk -rx, entao pergunta pelo AMI)"""
    try:
        canais = await ami.fazer({'Action': 'CoreShowChannels'}, lista=True, timeout=8)
    except Exception:
        return False
    for c in canais if isinstance(canais, list) else []:
        if str(c.get('Channel', '')).startswith('PJSIP/%s-' % ramal) and c.get('Application') == 'ConfBridge':
            return True
    return False


def _sessao(ramal):
    return SESS.setdefault(ramal, {
        'ramal': ramal, 'email': '', 'nome': '', 'sala': False, 'ativo': False, 'pausa': False,
        'pipeline_id': None, 'etapas': [], 'por_vez': POR_VEZ_MAX, 'rodada': None,
        'tocando': {}, 'em_linha': None, 'aguardando': None, 'avulsa': None, 'msg': '',
        'tarefa': None})


async def _ramal_do(db, user):
    row = (await db.execute(text('SELECT ramal FROM voz_ramais WHERE usuario_email = :e'),
                            {'e': user.email})).first()
    if not row:
        raise HTTPException(403, 'Você ainda não tem ramal de telefone. Peça ao administrador.')
    return row[0]


def _linha(ramal):
    """Tronco PJSIP do ramal: NVOIP_TRONCO_<RAMAL> no .env, senao NVOIP_TRONCO_PADRAO (padrao 'nvoip')."""
    return _env('NVOIP_TRONCO_' + str(ramal).upper(), _env('NVOIP_TRONCO_PADRAO', 'nvoip'))


def _canais_livres():
    usados = sum(len(s['tocando']) + (1 if s['em_linha'] else 0) for s in SESS.values())
    return max(0, int(_env('NVOIP_CANAIS', '5')) - usados)


def _ocupados():
    """conversas que estao tocando ou em linha com qualquer atendente agora"""
    out = set()
    for s in SESS.values():
        out.update(x['conversa_id'] for x in s['tocando'].values())
        if s['em_linha']:
            out.add(s['em_linha']['conversa_id'])
    return out


def _fila(c, s, limite=50):
    """Leads da coluna escolhida, na ordem de prioridade, ja sem quem nao pode ser ligado agora."""
    if not s['pipeline_id'] or not s['etapas']:
        return []
    agora = time.time()
    marcas = ','.join('?' * len(s['etapas']))
    convs = c.execute(
        'SELECT id, contato_nome, contato_telefone, etapa, atualizado_em FROM wa_conversas '
        'WHERE pipeline_id=? AND etapa IN (%s) AND COALESCE(contato_telefone,"") != ""' % marcas,
        [s['pipeline_id']] + list(s['etapas'])).fetchall()
    if not convs:
        return []
    ids = [r['id'] for r in convs]
    hist = {}
    for i in range(0, len(ids), 500):
        bloco = ids[i:i + 500]
        for h in c.execute('SELECT conversa_id, status, resultado, criado_em, retorno_em, duracao FROM tel_chamadas '
                           'WHERE conversa_id IN (%s) AND criado_em > ?' % ','.join('?' * len(bloco)),
                           bloco + [agora - 30 * 86400]):
            hist.setdefault(h['conversa_id'], []).append(dict(h))
    recentes = set()
    for i in range(0, len(ids), 500):
        bloco = ids[i:i + 500]
        for r in c.execute("SELECT DISTINCT conversa_id FROM wa_mensagens WHERE origem='contato' AND conversa_id IN (%s) "
                           "AND criado_em > ?" % ','.join('?' * len(bloco)),
                           bloco + [datetime.utcfromtimestamp(agora - 900).strftime('%Y-%m-%d %H:%M:%S')]):
            recentes.add(r[0])
    # temperatura do lead (termometro da IA): os mais quentes primeiro
    temp = {}
    for i in range(0, len(ids), 500):
        bloco = ids[i:i + 500]
        for r in c.execute('SELECT conversa_id, temperatura FROM lead_termometro WHERE conversa_id IN (%s)'
                           % ','.join('?' * len(bloco)), bloco):
            temp[r[0]] = (r[1] or '').lower()
    ocupados = _ocupados()
    hoje0 = _agora_br().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    out = []
    for r in convs:
        num = _numero(r['contato_telefone'])
        if not num or r['id'] in ocupados:
            continue
        hs = hist.get(r['id'], [])
        if any(h['resultado'] in FINAIS for h in hs):
            continue
        tent = [h for h in hs if h['status'] not in ('cancelada', 'falha_linha')]   # recusa da operadora nao conta
        t10 = [h for h in tent if h['criado_em'] > agora - 10 * 86400]
        tdia = [h for h in tent if h['criado_em'] >= hoje0]
        ultima = max([h['criado_em'] for h in tent] or [0])
        derrubado = any(h['status'] == 'derrubado' and h['criado_em'] > agora - 3600 for h in tent) and \
            not any(h['status'] in ('fim', 'atendeu') and h['criado_em'] > agora - 3600 for h in tent)
        retorno = any(h['resultado'] == 'retornar' and h['retorno_em'] and h['retorno_em'] <= agora for h in hs) and \
            not any(h['criado_em'] > max(x['retorno_em'] or 0 for x in hs if x['resultado'] == 'retornar') for h in tent)
        if not derrubado and not retorno:
            if len(t10) >= TENTATIVAS_10D or len(tdia) >= TENTATIVAS_DIA or agora - ultima < INTERVALO_S:
                continue
        t = temp.get(r['id'], '')
        if derrubado:
            pr = 0                      # atendeu depois de outro: volta ja
        elif r['id'] in recentes:
            pr = 1                      # acabou de mandar mensagem
        elif retorno:
            pr = 2                      # pediu retorno para agora
        elif t == 'quentissimo':
            pr = 3                      # um de cada vez
        elif re.search(r'ligar j[aá]', r['etapa'] or '', re.I):
            pr = 4                      # coluna "ligar ja", em grupo
        elif t == 'quente' or QUENTE.search(r['etapa'] or ''):
            pr = 5
        elif t == 'morno':
            pr = 6
        elif t == 'sem_caso':
            pr = 8                      # spam/engano/B2B pelo termometro: por ultimo
        else:
            pr = 7                      # frio, sem resposta ou ainda sem termometro
        out.append({'conversa_id': r['id'], 'nome': r['contato_nome'] or '', 'telefone': num,
                    'etapa': r['etapa'], 'prioridade': pr, 'tentativas': len(t10), 'temperatura': t,
                    'atividade': str(r['atualizado_em'] or '')})
    # mesma faixa: quem foi menos tentado primeiro, depois quem mexeu na conversa mais recentemente
    out.sort(key=lambda x: (x['prioridade'], x['tentativas'], x['atividade'] and -_ts(x['atividade']) or 0))
    return out[:limite]


def _ts(txt):
    try:
        return datetime.fromisoformat(str(txt)[:19]).timestamp()
    except Exception:
        return 0


async def _originar(s, lead, rodada, modo):
    c = _con()
    try:
        cur = c.execute('INSERT INTO tel_chamadas (rodada, agente, usuario_email, conversa_id, telefone, nome, prioridade, '
                        'modo, status, criado_em) VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (rodada, s['ramal'], s['email'], lead['conversa_id'], lead['telefone'], lead['nome'],
                         lead.get('prioridade'), modo, 'tocando', time.time()))
        cid = cur.lastrowid
        c.commit()
    finally:
        c.close()
    uid = 'tel%d' % cid
    s['tocando'][str(cid)] = dict(lead, id=cid, uid=uid)
    campos = {'Action': 'Originate', 'Channel': 'PJSIP/%s@%s' % (lead['telefone'], _linha(s['ramal'])),
              'Context': 'discador-lead', 'Exten': 's', 'Priority': '1', 'Timeout': '30000',
              'CallerID': 'Escritório Modelo <%s>' % NUMERO_SAIDA, 'Async': 'true', 'ChannelId': uid,
              'Variable': ['AGENTE=%s' % s['ramal'], 'RODADA=%s' % rodada, 'LEAD=%d' % cid, 'MODO=%s' % modo,
                           'CHAVE=%s' % _env('TELEFONIA_CHAVE')]}

    async def vai():
        try:
            resp = await ami.fazer(campos, timeout=45)
            ok = resp.get('Response') == 'Success'
            motivo = {'0': 'falhou', '1': 'desligou', '3': 'nao atendeu', '5': 'ocupado', '8': 'congestionado'}.get(
                str(resp.get('Reason', '')), resp.get('Message', '') or resp.get('Reason', ''))
        except Exception as e:
            ok, motivo = False, str(e)[:80]
        if not ok:
            _terminou_sem_falar(s, cid, motivo)
    asyncio.get_running_loop().create_task(vai())
    return cid


def _nota_linha(conversa_id, texto):
    """Nota no chat no MESMO formato das ligacoes pelo WhatsApp ('iniciada por X para +55..' e depois
    'encerrada (m:ss de conversa)' / '(nao atendida)'): assim a mesa do Comercial e a moedas da equipe contam as
    ligacoes pela linha fixa igual contam as do WhatsApp (comercial_kpi._ligacoes)."""
    if not conversa_id:
        return
    c = _con()
    try:
        c.execute("INSERT INTO wa_mensagens (conversa_id, origem, autor, tipo, conteudo, midia_url, criado_em) "
                  "VALUES (?, 'nota', 'ligacao', 'texto', ?, '', ?)", (int(conversa_id), texto, str(datetime.utcnow())))
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


def _nota_inicio(s, conversa_id, telefone):
    _nota_linha(conversa_id, 'Ligacao pela linha fixa iniciada por %s para +%s.' % (s.get('nome') or s['ramal'], telefone))


def _terminou_sem_falar(s, cid, motivo):
    """Originate voltou sem atender (nao atendeu, ocupado, cancelada porque outro atendeu)."""
    item = s['tocando'].pop(str(cid), None)
    if s['avulsa'] and s['avulsa'].get('id') == cid:
        s['avulsa'] = None
        s['msg'] = 'Não atendeu (%s).' % (motivo or 'sem resposta')
    c = _con()
    try:
        st = c.execute('SELECT status, criado_em FROM tel_chamadas WHERE id=?', (cid,)).fetchone()
        if st and st['status'] == 'tocando':
            novo = 'cancelada' if (item or {}).get('cancelar') else 'nao_atendeu'
            m = (motivo or '').lower()
            if 'busy' in m or 'ocupad' in m:
                novo = 'ocupado'
            # a operadora recusou na hora (CPS, linha fora): NAO e tentativa no lead, nao vira nota
            if novo == 'nao_atendeu' and m in ('falhou', 'congestionado') and time.time() - (st['criado_em'] or 0) < 6:
                novo = 'falha_linha'
                s['falhas'] = s.get('falhas', 0) + 1
            elif novo != 'cancelada':
                s['falhas'] = 0
            c.execute('UPDATE tel_chamadas SET status=?, motivo=?, fim_em=? WHERE id=?',
                      (novo, (motivo or '')[:120], time.time(), cid))
            c.commit()
            if novo not in ('cancelada', 'falha_linha'):
                r = c.execute('SELECT conversa_id, telefone FROM tel_chamadas WHERE id=?', (cid,)).fetchone()
                if r and r['conversa_id']:
                    _nota_inicio(s, r['conversa_id'], r['telefone'])
                    _nota_linha(r['conversa_id'], 'Ligacao pela linha fixa encerrada (nao atendida, %s).' % (novo.replace('_', ' ')))
    finally:
        c.close()
    if s.get('falhas', 0) >= 5:
        # trava de seguranca: a operadora recusando em sequencia -> para, em vez de passar a fila inteira
        s['ativo'] = False
        s['falhas'] = 0
        s['msg'] = ('A operadora (Nvoip) recusou as últimas ligações na hora. Parei o discador para não perder leads; '
                    'clique em Começar a ligar de novo em 1 minuto. Se repetir, avise o administrador.')
        return
    _talvez_proxima(s, 3 if s.get('falhas') else 1.5)


def _talvez_proxima(s, atraso=1.5):
    if s.get('tarefa') and not s['tarefa'].done():
        return
    s['tarefa'] = asyncio.get_running_loop().create_task(_proxima(s, atraso))


async def _proxima(s, atraso):
    await asyncio.sleep(atraso)
    if not (s['sala'] and s['ativo']) or s['pausa'] or s['tocando'] or s['em_linha'] or s['aguardando'] or s['avulsa']:
        return
    if not _no_horario() and not s.get('fora_horario_ok'):
        s['msg'] = 'Fora do horário de ligação (7h00 às 20h30, todos os dias).'
        return
    livres = _canais_livres()
    if livres <= 0:
        s['msg'] = 'Todas as linhas ocupadas; tento de novo em instantes.'
        _talvez_proxima(s, 5)
        return
    c = _con()
    try:
        fila = _fila(c, s, 20)
    finally:
        c.close()
    if not fila:
        s['msg'] = 'Fila vazia nesta coluna: ninguém para ligar agora (limites de tentativas e intervalo).'
        return
    # SEMPRE o lote cheio (ate 5). Os mais quentes vao na frente dentro do lote.
    lote, modo = fila[:min(s['por_vez'], livres)], 'lote'
    rodada = uuid.uuid4().hex[:10]
    s['rodada'] = rodada
    s['msg'] = 'Ligando para %d lead(s)…' % len(lote)
    # a Nvoip so aceita 1 chamada nova por segundo (responde "486 CPS maximo atingido"): espaca o lote
    for i, lead in enumerate(lote):
        if i:
            await asyncio.sleep(CPS_INTERVALO)
            if s['em_linha'] or not (s['sala'] and s['ativo']) or s['pausa']:
                break                       # alguem ja atendeu (ou parou): nao disca o resto
        await _originar(s, lead, rodada, modo)


# ---------------------------------------------------------------------------
# rotas da tela
# ---------------------------------------------------------------------------
class ConfigIn(BaseModel):
    pipeline_id: int
    etapas: list
    por_vez: int = POR_VEZ_MAX


@router.get('/colunas')
async def colunas(user: Usuario = Depends(usuario_atual)):
    c = _con()
    try:
        pips = [dict(r) for r in c.execute('SELECT id, nome FROM wa_pipelines ORDER BY ordem')]
        cont = {}
        for r in c.execute("SELECT pipeline_id, etapa, COUNT(*) n FROM wa_conversas "
                           "WHERE COALESCE(contato_telefone,'') != '' GROUP BY 1, 2"):
            cont.setdefault(r['pipeline_id'], []).append({'etapa': r['etapa'], 'leads': r['n']})
        for p in pips:
            p['etapas'] = sorted(cont.get(p['id'], []), key=lambda x: -x['leads'])
        return {'pipelines': [p for p in pips if p['etapas']]}
    finally:
        c.close()


@router.post('/configurar')
async def configurar(body: ConfigIn, user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    ramal = await _ramal_do(db, user)
    s = _sessao(ramal)
    s.update(email=user.email, nome=user.nome or user.email, pipeline_id=int(body.pipeline_id),
             etapas=[str(e) for e in body.etapas][:20], por_vez=max(1, min(POR_VEZ_MAX, int(body.por_vez or 5))))
    c = _con()
    try:
        n = len(_fila(c, s, 2000))
    finally:
        c.close()
    return {'ok': True, 'ramal': ramal, 'na_fila': n, 'entrar_na_sala': 'discador'}


@router.post('/comecar')
async def comecar(body: dict = None, user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    ramal = await _ramal_do(db, user)
    s = _sessao(ramal)
    if not s['pipeline_id']:
        raise HTTPException(400, 'Escolha a coluna do CRM antes de começar.')
    s['ativo'], s['pausa'] = True, False
    s['fora_horario_ok'] = bool((body or {}).get('fora_horario')) and (user.papel == 'admin')
    if not s['sala'] and await _sala_viva(ramal):
        s['sala'] = True            # o servidor reiniciou com ela ja na linha: o Asterisk sabe
    if not s['sala']:
        s['msg'] = 'Entrando na sala…'
        return {'ok': True, 'aguardando_sala': True}
    _talvez_proxima(s, 0.2)
    return {'ok': True}


@router.post('/pausar')
async def pausar(body: dict, user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    s = _sessao(await _ramal_do(db, user))
    s['pausa'] = bool(body.get('pausa', True))
    s['msg'] = 'Pausado: termina a ligação atual e não disca mais.' if s['pausa'] else ''
    if not s['pausa']:
        _talvez_proxima(s, 0.2)
    return {'ok': True}


@router.post('/parar')
async def parar(user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    s = _sessao(await _ramal_do(db, user))
    s['ativo'] = False
    for x in s['tocando'].values():
        x['cancelar'] = True
    await _desligar_uids([x['uid'] for x in s['tocando'].values()])
    s['msg'] = 'Parado.'
    return {'ok': True}


@router.post('/desligar')
async def desligar(user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Derruba o lead em linha (a atendente continua na sala)."""
    s = _sessao(await _ramal_do(db, user))
    alvo = s['em_linha'] or s['avulsa']
    if s['avulsa'] and s['avulsa'].get('uid'):
        s['avulsa']['cancelar'] = True
    n = await _desligar_uids([alvo['uid']] if alvo else [])
    return {'ok': True, 'derrubados': n}


class LigarIn(BaseModel):
    conversa_id: int = 0
    numero: str = ''
    nome: str = ''


@router.post('/ligar')
async def ligar(body: LigarIn, user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """'Ligar pela linha' dentro da conversa: uma ligacao so, para este lead."""
    ramal = await _ramal_do(db, user)
    s = _sessao(ramal)
    s.update(email=user.email, nome=user.nome or user.email)
    if s['tocando'] or s['em_linha'] or s['avulsa']:
        raise HTTPException(409, 'Você já está em uma ligação. Desligue antes de ligar para outro lead.')
    num = _numero(body.numero)
    if not num:
        raise HTTPException(400, 'Telefone inválido para ligar: %s' % body.numero)
    if _canais_livres() <= 0:
        raise HTTPException(409, 'Todas as linhas estão ocupadas agora. Tente em instantes.')
    s['avulsa'] = {'conversa_id': body.conversa_id or None, 'telefone': num, 'nome': body.nome, 'pendente': True}
    if not s['sala'] and await _sala_viva(ramal):
        s['sala'] = True            # o servidor reiniciou com ela ja na linha: o Asterisk sabe
    if not s['sala']:
        s['msg'] = 'Entrando na sala…'
        return {'ok': True, 'aguardando_sala': True, 'entrar_na_sala': 'discador'}
    await _disparar_avulsa(s)
    return {'ok': True}


async def _disparar_avulsa(s):
    a = s['avulsa']
    if not a or not a.get('pendente'):
        return
    a['pendente'] = False
    lead = {'conversa_id': a['conversa_id'], 'telefone': a['telefone'], 'nome': a['nome'], 'prioridade': None}
    rodada = uuid.uuid4().hex[:10]
    cid = await _originar(s, lead, rodada, 'conversa')
    a.update(id=cid, uid='tel%d' % cid)
    s['msg'] = 'Chamando %s…' % (a['nome'] or a['telefone'])


class ResultadoIn(BaseModel):
    resultado: str
    nota: str = ''
    retorno_em: str = ''      # 'YYYY-MM-DDTHH:MM' (hora de Brasilia)


RESULTADOS = {'interessado': 'Interessado', 'retornar': 'Pediu retorno', 'fechou': 'Fechou',
              'sem_interesse': 'Sem interesse', 'numero_errado': 'Número errado', 'caixa_postal': 'Caixa postal',
              'nao_ligar': 'Pediu para não ligar', 'outro': 'Outro'}


@router.post('/resultado')
async def resultado(body: ResultadoIn, user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    s = _sessao(await _ramal_do(db, user))
    alvo = s['aguardando']
    if not alvo:
        raise HTTPException(400, 'Não há ligação esperando o resultado.')
    if body.resultado not in RESULTADOS:
        raise HTTPException(400, 'Resultado desconhecido.')
    ret = None
    if body.resultado == 'retornar' and body.retorno_em:
        try:
            ret = datetime.fromisoformat(body.retorno_em).replace(tzinfo=TZ).timestamp()
        except ValueError:
            raise HTTPException(400, 'Data do retorno inválida.')
    c = _con()
    try:
        c.execute('UPDATE tel_chamadas SET resultado=?, nota=?, retorno_em=? WHERE id=?',
                  (body.resultado, body.nota[:2000], ret, alvo['id']))
        c.commit()
    finally:
        c.close()
    if alvo.get('conversa_id'):
        txt = 'Resultado da ligação (%s): %s' % (user.nome or user.email, RESULTADOS[body.resultado])
        if ret:
            txt += ' — retornar em %s' % datetime.fromtimestamp(ret, TZ).strftime('%d/%m %H:%M')
        if body.nota.strip():
            txt += '\n' + body.nota.strip()
        await _nota(db, alvo['conversa_id'], txt, 'ligacao')
    s['aguardando'] = None
    s['msg'] = ''
    _talvez_proxima(s, 0.5)
    return {'ok': True}


@router.get('/estado')
async def estado(user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    ramal = await _ramal_do(db, user)
    s = _sessao(ramal)
    c = _con()
    try:
        hoje0 = _agora_br().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        placar = {}
        for r in c.execute('SELECT agente, status, COUNT(*) n, SUM(COALESCE(duracao,0)) seg FROM tel_chamadas '
                           'WHERE criado_em >= ? GROUP BY 1, 2', (hoje0,)):
            p = placar.setdefault(r['agente'], {'ligacoes': 0, 'atendidas': 0, 'segundos': 0, 'derrubados': 0})
            if r['status'] != 'cancelada':
                p['ligacoes'] += r['n']
            if r['status'] == 'fim':
                p['atendidas'] += r['n']
                p['segundos'] += r['seg'] or 0
            if r['status'] == 'derrubado':
                p['derrubados'] += r['n']
        ult = [dict(r) for r in c.execute(
            'SELECT id, nome, telefone, status, duracao, resultado, criado_em FROM tel_chamadas '
            'WHERE agente=? ORDER BY id DESC LIMIT 12', (ramal,))]
        na_fila = len(_fila(c, s, 2000)) if s['pipeline_id'] else 0
        em = s['em_linha'] or s['aguardando']
        ficha = None
        if em and em.get('conversa_id'):
            r = c.execute('SELECT id, contato_nome, contato_telefone, etapa, pipeline_id, valor, '
                          'substr(COALESCE(notas,""), -1500) notas FROM wa_conversas WHERE id=?',
                          (em['conversa_id'],)).fetchone()
            ficha = dict(r) if r else None
    finally:
        c.close()
    return {
        'ramal': ramal, 'sala': s['sala'], 'ativo': s['ativo'], 'pausa': s['pausa'], 'msg': s['msg'],
        'pipeline_id': s['pipeline_id'], 'etapas': s['etapas'], 'por_vez': s['por_vez'],
        'tocando': [{'id': x['id'], 'nome': x['nome'], 'telefone': x['telefone']} for x in s['tocando'].values()],
        'em_linha': s['em_linha'], 'aguardando': s['aguardando'], 'avulsa': s['avulsa'], 'ficha': ficha,
        'na_fila': na_fila, 'placar': placar, 'ultimas': ult, 'no_horario': _no_horario(),
        'canais_livres': _canais_livres(), 'resultados': RESULTADOS,
    }


# ---------------------------------------------------------------------------
# eventos do dialplan (Asterisk -> aqui, so de dentro do servidor)
# ---------------------------------------------------------------------------
@router.get('/evento')
async def evento(request: Request, tipo: str, agente: str, chave: str = '', rodada: str = '', lead: int = 0,
                 canal: str = '', uid: str = '', dur: int = 0):
    # so o Asterisk conhece a chave (variavel CHAVE da ligacao / global DISC_CHAVE do dialplan)
    if not (chave and request.client and request.client.host == '127.0.0.1' and chave == _env('TELEFONIA_CHAVE')):
        raise HTTPException(403, 'so o servidor')
    s = _sessao(agente)
    agora = time.time()
    if tipo == 'sala_entrou':
        s['sala'] = True
        s['msg'] = ''
        if s['avulsa'] and s['avulsa'].get('pendente'):
            await _disparar_avulsa(s)
        else:
            _talvez_proxima(s, 0.3)
    elif tipo == 'sala_saiu':
        s.update(sala=False, ativo=False)
        for x in s['tocando'].values():
            x['cancelar'] = True
        await _desligar_uids([x['uid'] for x in s['tocando'].values()] +
                             ([s['em_linha']['uid']] if s['em_linha'] else []))
        s['msg'] = 'Saiu da sala.'
    elif tipo == 'atendeu':
        item = s['tocando'].pop(str(lead), None) or {'id': lead, 'uid': uid}
        item.update(uid=uid or item.get('uid'), canal=canal, atendeu_em=agora)
        s['em_linha'] = item
        if s['avulsa'] and s['avulsa'].get('id') == lead:
            s['avulsa'] = None
        c = _con()
        try:
            c.execute("UPDATE tel_chamadas SET status='atendeu', atendeu_em=?, uid=?, canal=? WHERE id=?",
                      (agora, uid, canal, lead))
            c.commit()
            r = c.execute('SELECT conversa_id, telefone FROM tel_chamadas WHERE id=?', (lead,)).fetchone()
        finally:
            c.close()
        if r and r['conversa_id']:
            _nota_inicio(s, r['conversa_id'], r['telefone'])
        outros = list(s['tocando'].values())
        for x in outros:
            x['cancelar'] = True
        await _desligar_uids([x['uid'] for x in outros])
        s['msg'] = ''
    elif tipo in ('perdeu', 'caixa_postal'):
        # perdeu = atendeu depois de outro (volta ao topo); caixa_postal = a deteccao ouviu gravacao, nao gente
        s['tocando'].pop(str(lead), None)
        if s['avulsa'] and s['avulsa'].get('id') == lead:
            s['avulsa'] = None
            s['msg'] = 'Caiu na caixa postal.'
        c = _con()
        try:
            c.execute('UPDATE tel_chamadas SET status=?, fim_em=? WHERE id=?',
                      ('derrubado' if tipo == 'perdeu' else 'caixa_postal', agora, lead))
            c.commit()
            r = c.execute('SELECT conversa_id, telefone FROM tel_chamadas WHERE id=?', (lead,)).fetchone()
        finally:
            c.close()
        if tipo == 'caixa_postal' and r and r['conversa_id']:
            _nota_inicio(s, r['conversa_id'], r['telefone'])
            _nota_linha(r['conversa_id'], 'Ligacao pela linha fixa encerrada (nao atendida, caixa postal).')
        if not s['tocando'] and not s['em_linha']:
            _talvez_proxima(s)
    elif tipo == 'fim':
        item = s['em_linha'] if s['em_linha'] and s['em_linha'].get('id') == lead else {'id': lead}
        s['em_linha'] = None
        if not dur and item.get('atendeu_em'):
            dur = int(agora - item['atendeu_em'])
        arq = ''
        for n in sorted(os.listdir(GRAV)) if os.path.isdir(GRAV) else []:
            if n.startswith('%s_%d_' % (rodada, lead)):
                arq = os.path.join(GRAV, n)
        c = _con()
        try:
            c.execute("UPDATE tel_chamadas SET status='fim', fim_em=?, duracao=?, arquivo=? WHERE id=?",
                      (agora, int(dur or 0), arq, lead))
            c.commit()
            row = c.execute('SELECT * FROM tel_chamadas WHERE id=?', (lead,)).fetchone()
        finally:
            c.close()
        if row and row['conversa_id']:
            d = int(dur or 0)
            _nota_linha(row['conversa_id'], 'Ligacao pela linha fixa encerrada (%d:%02d de conversa).' % (d // 60, d % 60))
        if row:
            s['aguardando'] = {'id': lead, 'conversa_id': row['conversa_id'], 'nome': row['nome'],
                               'telefone': row['telefone'], 'duracao': int(dur or 0)}
            asyncio.get_running_loop().create_task(_pos_ligacao(dict(row), int(dur or 0)))
    return {'ok': True}


# ---------------------------------------------------------------------------
# depois da ligacao: gravacao + transcricao na conversa e na ficha
# ---------------------------------------------------------------------------
async def _nota(db, conversa_id, texto_nota, autor='ligacao'):
    if not conversa_id:
        return
    await db.execute(text(
        "INSERT INTO wa_mensagens (conversa_id, origem, autor, tipo, conteudo, midia_url, criado_em) "
        "VALUES (:c, 'nota', :a, 'texto', :t, '', :d)"),
        {'c': int(conversa_id), 'a': autor, 't': texto_nota, 'd': datetime.utcnow()})
    await db.commit()


def _transcrever(arquivo):
    if not arquivo or not os.path.exists(arquivo) or not settings.STT_API_KEY:
        return ''
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, 'a.ogg')
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', arquivo, '-ac', '1', '-c:a', 'libopus',
                            '-b:a', '24k', dst], check=True, timeout=300)
            dados = open(dst, 'rb').read()
        r = requests.post(settings.STT_BASE_URL.rstrip('/') + '/audio/transcriptions',
                          headers={'Authorization': 'Bearer ' + settings.STT_API_KEY},
                          files={'file': ('ligacao.ogg', dados, 'audio/ogg')},
                          data={'model': settings.STT_MODEL, 'language': 'pt', 'response_format': 'verbose_json'},
                          timeout=600)
        if r.status_code != 200:
            return ''
        j = r.json()
        linhas, ultimo = [], ''
        for sg in j.get('segments') or []:
            t = (sg.get('text') or '').strip()
            # o transcritor inventa "Legenda por ..." / "Obrigado por assistir" em trecho de silencio
            if not t or float(sg.get('no_speech_prob') or 0) > 0.6 or t.lower() == ultimo or ALUCINA.search(t):
                continue
            ultimo = t.lower()
            ini = int(float(sg.get('start') or 0))
            linhas.append('[%02d:%02d] %s' % (ini // 60, ini % 60, t))
        return '\n'.join(linhas) or (j.get('text') or '').strip()
    except Exception:
        return ''


async def _pos_ligacao(row, segundos):
    from ..database import AsyncSessionLocal
    conv = row.get('conversa_id')
    quando = datetime.fromtimestamp(row.get('criado_em') or time.time(), TZ).strftime('%d/%m/%Y %H:%M')
    dur = '%d:%02d' % (segundos // 60, segundos % 60)
    await asyncio.sleep(2)                      # o MixMonitor fecha o arquivo
    texto_t = await asyncio.to_thread(_transcrever, row.get('arquivo'))
    async with AsyncSessionLocal() as db:
        await _nota(db, conv, 'Ligação pela linha fixa encerrada: %s de conversa (%s).'
                    % (dur, row.get('agente')))
        if texto_t:
            await _nota(db, conv, '[TRANSCRICAO DA LIGACAO]\n' + texto_t[:60000], 'transcricao')
        if conv:
            # o mesmo cabecalho da ligacao pelo WhatsApp: o pos-ligacao (resumo, tarefa, temperatura) le este bloco
            bloco = '=== LIGACAO PELO WHATSAPP - %s (%s) ===\nCanal: linha telefonica Nvoip (discador)\nTRANSCRICAO:\n%s' % (
                quando, dur, texto_t or '(transcricao indisponivel)')
            await db.execute(text(
                "UPDATE wa_conversas SET notas = CASE WHEN COALESCE(TRIM(notas), '') = '' THEN :b "
                "ELSE notas || char(10) || char(10) || :b END WHERE id = :c"), {'b': bloco, 'c': int(conv)})
            await db.commit()
