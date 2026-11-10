"""Modulo Controladoria — ponte com o SYNC (Atende Direito).

Guarda a chave sk_live no backend e faz proxy dos endpoints do SYNC:
intimacoes, processos, monitores (por OAB), busca. A tela nunca ve a chave.
"""
import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import requests
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from .. import tarefas_tipos as TT
from ..deps import usuario_atual, requer_admin
from ..database import Usuario
from ..config import settings
from ..whatsapp import zapi

log = logging.getLogger('controladoria')

router = APIRouter(prefix='/api/controladoria', tags=['controladoria'])


def _base():
    return (getattr(settings, 'SYNC_API_BASE', '') or 'https://sync.atendedireito.app/v1').rstrip('/')


def _headers():
    tk = getattr(settings, 'SYNC_API_TOKEN', '') or ''
    if not tk:
        raise HTTPException(503, 'SYNC nao configurado (falta SYNC_API_TOKEN no config/.env)')
    return {'Authorization': f'Bearer {tk}', 'Content-Type': 'application/json'}


def _get(path, params=None):
    r = requests.get(f'{_base()}{path}', headers=_headers(), params=params or {}, timeout=25)
    if r.status_code >= 400:
        raise HTTPException(502, f'SYNC {r.status_code}: {r.text[:200]}')
    return r.json()


def _post(path, body):
    r = requests.post(f'{_base()}{path}', headers=_headers(), json=body, timeout=25)
    if r.status_code >= 400:
        raise HTTPException(502, f'SYNC {r.status_code}: {r.text[:200]}')
    return r.json()


def _get_all(path, list_key, base_params=None, cap=5000):
    """Junta TODAS as paginas (paginacao por pagina/itens=200) num unico resultado."""
    params = dict(base_params or {})
    params['pagina'] = 1
    params['itens'] = 200
    out = []
    total = None
    while True:
        d = _get(path, params)
        if not isinstance(d, dict):
            break
        chunk = d.get(list_key) or []
        out.extend(chunk)
        total = d.get('total', total)
        if len(chunk) < 200 or (total is not None and len(out) >= total) or len(out) >= cap:
            break
        params['pagina'] += 1
    return {list_key: out, 'total': total if total is not None else len(out)}


def _busca_all(tipo, valor, cap=2000):
    """Junta TODAS as paginas da busca PDPJ (paginacao por cursor)."""
    out, vistos = [], set()
    total = fonte = cursor = None
    while True:
        body = {'tipo': tipo, 'valor': valor}
        if cursor:
            body['cursor'] = cursor
        d = _post('/busca', body)
        res = d.get('resultados') or []
        for r in res:
            num = r.get('numero')
            if num and num in vistos:
                continue
            if num:
                vistos.add(num)
            out.append(r)
        total = d.get('total', total)
        fonte = d.get('fonte', fonte)
        cursor = d.get('cursor')
        if not cursor or not res or len(out) >= cap or (total is not None and len(out) >= total):
            break
    return {'resultados': out, 'total': total if total is not None else len(out),
            'fonte': fonte, 'tem_mais': bool(cursor) and (total or 0) > len(out)}


# ============================================================================
# CACHE LOCAL + ALERTA DE PRAZO
# A tela le do nosso banco (instantaneo, zero 429). Um loop atualiza do SYNC.
# ============================================================================
_DB = str(Path(__file__).resolve().parents[2] / 'sistema_esc.db')


def _cache_con():
    con = sqlite3.connect(_DB, timeout=15)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE IF NOT EXISTS sync_cache (chave TEXT PRIMARY KEY, dados TEXT, atualizado_em REAL)')
    con.execute('CREATE TABLE IF NOT EXISTS sync_intim_vistas (id INTEGER PRIMARY KEY, alertado INTEGER, criado_em REAL)')
    con.execute('CREATE TABLE IF NOT EXISTS sync_intim_triagem (id INTEGER PRIMARY KEY, categoria TEXT, urgencia TEXT, acao TEXT, prazo_dias INTEGER, requer_acao INTEGER, criado_em REAL)')
    # Motor deterministico (handoff Advogada) — modo SOMBRA: calcula e reporta, NAO cadastra.
    con.execute('CREATE TABLE IF NOT EXISTS sync_motor_sombra (id INTEGER PRIMARY KEY, ato TEXT, ramo TEXT, trilha TEXT, fatal INTEGER, titularidade TEXT, responsavel TEXT, confianca REAL, vencimento TEXT, fatal_interno TEXT, dias INTEGER, contagem TEXT, prazos TEXT, evidencias TEXT, avisos TEXT, criado_em REAL)')
    # Titularidade (nosso x parte contraria) resolvida do ADVBOX, cacheada por processo.
    con.execute('CREATE TABLE IF NOT EXISTS advbox_titularidade (numero TEXT PRIMARY KEY, titularidade TEXT, nosso_polo TEXT, cliente TEXT, parte_adversa TEXT, origem TEXT, criado_em REAL)')
    return con


def cache_get(chave):
    try:
        con = _cache_con()
        r = con.execute('SELECT dados FROM sync_cache WHERE chave=?', (chave,)).fetchone()
        con.close()
        return json.loads(r[0]) if r else None
    except Exception as e:
        log.warning('cache_get %s: %s', chave, e)
        return None


def _cache_set(con, chave, dados):
    con.execute('INSERT OR REPLACE INTO sync_cache(chave,dados,atualizado_em) VALUES(?,?,?)',
                (chave, json.dumps(dados), time.time()))


def _wa_numero(con):
    r = con.execute('SELECT zapi_instance,zapi_token,zapi_client_token FROM wa_numeros WHERE id=1').fetchone()
    return SimpleNamespace(zapi_instance=r[0], zapi_token=r[1], zapi_client_token=r[2]) if r else None


def _fmt_data(s):
    s = str(s or '')
    d = s[:10].split('-')
    return f'{d[2]}/{d[1]}/{d[0]}' if len(d) == 3 else s


def _alerta_intimacoes(con, intims):
    """WhatsApp para intimacoes NOVAS com prazo. Na 1a vez SEMEIA (marca todas como
    vistas sem alertar) — evita disparar as ja existentes. Valvula: max 10/ciclo."""
    vistos = set(r[0] for r in con.execute('SELECT id FROM sync_intim_vistas'))
    seed = len(vistos) == 0
    novas = []
    for it in intims:
        iid = it.get('id')
        if iid is None or iid in vistos:
            continue
        tem_prazo = bool(it.get('data_fatal') or it.get('requer_manifestacao'))
        alertar = (not seed) and tem_prazo and bool(getattr(settings, 'CONTROLADORIA_ALERTA_ON', True))
        con.execute('INSERT OR IGNORE INTO sync_intim_vistas(id,alertado,criado_em) VALUES(?,?,?)',
                    (iid, 1 if alertar else 0, time.time()))
        if alertar:
            novas.append(it)
    con.commit()
    if not novas:
        return 0
    novas = novas[:10]
    numero = _wa_numero(con)
    tel = getattr(settings, 'CONTROLADORIA_ALERTA_TEL', '') or ''
    if not numero or not tel:
        return 0
    for it in novas:
        prazo = it.get('data_fatal') or it.get('prazo_ato')
        dias = f" ({it.get('prazo_dias')} dias)" if it.get('prazo_dias') else ''
        msg = ("*Nova intimacao com prazo*\n\n"
               f"Processo: {it.get('processo', '?')} ({it.get('tribunal', '')})\n"
               f"Orgao: {it.get('orgao', '')}\n"
               f"Tipo: {it.get('tipo_comunicacao', '')} - {it.get('tipo_ato', '')}\n"
               f"*Prazo: {_fmt_data(prazo)}{dias}*\n\n"
               f"{(it.get('resumo') or '')[:400]}\n\n-- Controladoria Escritório Modelo")
        try:
            zapi.enviar_texto(numero, tel, msg)
        except Exception as e:
            log.warning('alerta intimacao falhou: %s', e)
    return len(novas)


# 07/10/2026 (Advogada): "vamos usar o sonnet com o mesmo treinamento da IVA e tambem da nossa assistente do
# portal, a Assistente". A triagem roda no Sonnet do PLANO MAX (ponte do root), com a persona da IVA (skills
# de recurso e de embargos) e as regras de escrita da Assistente do Portal (app/portal/duvida.py).
_SYS_TRIAGEM = (
    "Você é a IVA, advogada sênior do Escritório Modelo Advogados, e trabalha em conjunto com a Dra. Advogada Responsável "
    "(OAB/UF 000.000). Vinte anos de experiência prática em direito processual, trabalhista e cível. Analisa decisões, "
    "sentenças, acórdãos e despachos como ninguém. Não opina sem base legal, não trabalha com achismo e NUNCA inventa: "
    "o que a publicação não diz, você diz que não diz. Orientada por resultado e atenta a riscos e a prazos.\n\n"
    "SUA TAREFA AQUI: ler uma intimação publicada no diário (ou o teor da decisão, quando vier junto) e fazer a "
    "triagem para a controladoria do escritório: a Dra. Advogada, a Dra. Advogada leem o seu resumo na ficha "
    "antes de decidir o que fazer. Elas são advogadas e operacional: pode usar o vocabulário técnico correto, "
    "mas vá direto ao ponto.\n\n"
    "COMO ESCREVER (regras da Assistente, nossa assistente): comece pela resposta, sem rodeio nem aviso; frases curtas; "
    "português correto, com acento e cedilha; nunca use travessão, nem markdown, nem asterisco, nem expressão em latim; "
    "nada de bordão. O resumo é lido numa ficha pequena: até seis frases.\n\n"
    "O QUE O RESUMO (acao_sugerida) TEM DE DIZER, nesta ordem: 1) o que a decisão ou o despacho determina, em uma "
    "frase; 2) de quem é o prazo (nosso, da parte contrária, de ambos ou sem prazo) e de quantos dias, pelo texto; "
    "3) o que cabe fazer (manifestação, peça, recurso e qual, juntada, ciência, comparecimento) e por quê; 4) a "
    "providência concreta para a equipe; 5) alertas: audiência ou perícia designada com data e hora, condição que "
    "suspende o prazo, dado que falta conferir nos autos. Se o texto for só um aviso curto sem o teor, diga isso e "
    "mande abrir os autos.\n\n"
    "Responda SOMENTE um JSON válido, sem texto fora dele, no formato:\n"
    '{"categoria":"ciencia|manifestacao|sentenca|recurso|audiencia|despacho|outro",'
    '"urgencia":"alta|media|baixa","requer_acao":true|false,'
    '"acao_sugerida":"o resumo, até seis frases","prazo_dias":numero_ou_null}\n'
    "Regras de categoria: ciencia = só tomar ciência, sem ato; manifestacao = exige manifestação ou resposta no "
    "prazo; sentenca = sentença publicada; recurso = decisão ou acórdão que comporta recurso; audiencia = audiência "
    "designada (mesmo que o despacho também dê outro prazo); despacho = mero expediente. urgencia alta = prazo de até "
    "5 dias, audiência próxima ou risco de preclusão; media = prazo de 6 a 15 dias; baixa = sem prazo nosso. "
    "prazo_dias = o número de dias do prazo NOSSO lido no texto, ou null."
)


def _claude_json(user_text, max_tokens=350):
    """Triagem pelo Claude do PLANO MAX (ponte do root), nao pela API (Advogada, 07/10/2026): a chave da API
    ficou sem credito e a regra passou a ser so a assinatura. Devolve o JSON ou None (a ficha segue sem
    triagem e tenta de novo no proximo ciclo)."""
    try:
        from .. import claude_plano
        return claude_plano.perguntar_json(user_text, system=_SYS_TRIAGEM, model='sonnet', origem='triagem', timeout=240)
    except Exception as e:
        log.warning('claude triagem erro: %s', str(e)[:160])
        return None


def _classificar_intimacao(it, extra=''):
    teor = (it.get('texto') or it.get('resumo') or '')[:4000]
    if not teor.strip():
        return None
    # 07/10/2026: a IVA precisa saber quem e o nosso cliente e o polo para dizer de quem e o prazo
    u = f"Tipo: {it.get('tipo_ato') or it.get('tipo_comunicacao')}\nProcesso: {it.get('processo')}\n{extra}\n\nTEOR DA INTIMAÇÃO:\n{teor}"
    return _claude_json(u)


def _quem_somos(con):
    """{numero: frase com cliente, polo e parte adversa} pelo cadastro do ADVBOX (advbox_titularidade)."""
    out = {}
    try:
        for r in con.execute('SELECT numero, cliente, nosso_polo, parte_adversa FROM advbox_titularidade'):
            if r[0] and r[1]:
                out[r[0]] = 'Nosso cliente: %s (nosso polo: %s). Parte adversa: %s.' % (
                    r[1], r[2] or 'não informado no cadastro', r[3] or 'não informada no cadastro')
    except Exception as e:
        log.warning('quem somos para a triagem: %s', e)
    return out


def _triar_pendentes(con, intims, limite=6):
    """Classifica por IA as intimacoes ainda nao triadas. Cap por ciclo: no Sonnet do plano cada uma leva
    uns 12 s e o ciclo do cache e de 5 min (07/10/2026: de 40 para 6 por ciclo)."""
    ja = set(r[0] for r in con.execute('SELECT id FROM sync_intim_triagem'))
    novos = [it for it in intims if it.get('id') and it['id'] not in ja][:limite]
    quem = _quem_somos(con) if novos else {}
    n = 0
    for it in novos:
        extra = quem.get(it.get('processo')) or 'Nosso cliente: não identificado no cadastro (conferir nos autos de quem é o prazo).'
        c = _classificar_intimacao(it, extra)   # chamada lenta ao Claude ANTES de escrever
        if not c:
            continue
        con.execute('INSERT OR REPLACE INTO sync_intim_triagem(id,categoria,urgencia,acao,prazo_dias,requer_acao,criado_em) VALUES(?,?,?,?,?,?,?)',
                    (it['id'], c.get('categoria'), c.get('urgencia'), c.get('acao_sugerida'),
                     c.get('prazo_dias') if isinstance(c.get('prazo_dias'), int) else None,
                     1 if c.get('requer_acao') else 0, time.time()))
        con.commit()   # solta o lock a cada item (nao segura durante as chamadas ao Claude)
        n += 1
    return n


# --- Motor deterministico (handoff Advogada) — modo SOMBRA --------------------
_PROJ_ROOT = Path(__file__).resolve().parents[4]   # .../AUTOMACOES Escritório Modelo (ou /opt/escritorio)


def _advbox():
    """Importa advbox_integration do projeto e garante o token no ambiente. None se indisponivel."""
    try:
        integ = str(_PROJ_ROOT / 'INTEGRACOES')
        if integ not in sys.path:
            sys.path.insert(0, integ)
        if not os.environ.get('ADVBOX_API_TOKEN'):
            tok = getattr(settings, 'ADVBOX_API_TOKEN', '') or ''
            if tok:
                os.environ['ADVBOX_API_TOKEN'] = tok
        import advbox_integration as A
        return A
    except Exception as e:
        log.warning('advbox indisponivel p/ titularidade: %s', e)
        return None


def _resolver_titularidade(con, numeros, limite=6):
    """Resolve nosso x parte contraria via ADVBOX (origin='PARTE CONTRARIA'), cacheado por
    processo. Bounded a `limite` processos novos por ciclo (respeita rate limit)."""
    ja = set(r[0] for r in con.execute('SELECT numero FROM advbox_titularidade'))
    faltam = [n for n in dict.fromkeys(numeros) if n and n not in ja][:limite]
    if not faltam:
        return 0
    A = _advbox()
    if not A:
        return 0
    import unicodedata
    def _norm(s):
        return unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().upper().strip()

    n = 0
    for num in faltam:
        tit = {'titularidade': 'INDEFINIDO', 'nosso_polo': None, 'cliente': None, 'parte_adversa': None, 'origem': 'nao encontrado no ADVBOX'}
        try:
            lp = A.buscar_processo(numero_processo=num)
            proc = lp if isinstance(lp, dict) else (lp[0] if isinstance(lp, list) and lp else None)
            custs = (proc or {}).get('customers') or []
            # adversario: origin == PARTE CONTRARIA (acento-insensivel). nosso: os demais com origin.
            adverso = next((c for c in custs if _norm(c.get('origin')) == 'PARTE CONTRARIA'), None)
            nosso = next((c for c in custs if _norm(c.get('origin')) not in ('PARTE CONTRARIA', '') ), None) \
                or next((c for c in custs if _norm(c.get('origin')) != 'PARTE CONTRARIA'), None)
            if nosso:
                tit['cliente'] = nosso.get('name')
                tit['titularidade'] = 'NOSSO'
                tit['origem'] = f"ADVBOX (cliente origin={nosso.get('origin')})"
            if adverso:
                tit['parte_adversa'] = adverso.get('name')
            tp = _norm((proc or {}).get('type'))
            # BUG ATE 03/09/2026: a regra era "'RECLAMA' in tp -> ativo", e
            # 'RECLAMA' casa com RECLAMANTE **e com RECLAMADA**. Todo processo
            # de defesa entrava como polo ativo (um cliente re virou
            # reclamante). _polo_do_tipo testa RECLAMADA antes de RECLAMANTE.
            tit['nosso_polo'] = ('passivo' if 'DEFESA' in tp
                                 else _polo_do_tipo(tp))
        except Exception as e:
            tit['origem'] = f'erro ADVBOX: {str(e)[:60]}'
        # UPSERT, nao INSERT OR REPLACE: o REPLACE apaga a linha inteira e
        # grava so estas 7 colunas, zerando tipo/fase/etapa/responsavel/
        # pasta_drive/lawsuit_id - que o PORTAL DO CLIENTE le. Ja tinha comido
        # 24 das 169 linhas quando foi descoberto, em 03/09/2026.
        con.execute(
            'INSERT INTO advbox_titularidade'
            '(numero,titularidade,nosso_polo,cliente,parte_adversa,origem,criado_em) '
            'VALUES(?,?,?,?,?,?,?) '
            'ON CONFLICT(numero) DO UPDATE SET '
            'titularidade=excluded.titularidade, nosso_polo=excluded.nosso_polo, '
            'cliente=excluded.cliente, parte_adversa=excluded.parte_adversa, '
            'origem=excluded.origem, criado_em=excluded.criado_em',
            (num, tit['titularidade'], tit['nosso_polo'], tit['cliente'],
             tit['parte_adversa'], tit['origem'], time.time()))
        con.commit()
        n += 1
    return n


def _lista_json(v):
    """Coluna que guarda lista em JSON. Litisconsorcio e o caso comum."""
    if isinstance(v, list):
        return v
    try:
        d = json.loads(v) if v else []
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _completar_texto(con, intims, limite=60):
    """Troca o texto truncado da listagem pelo inteiro teor do detalhe.

    A listagem corta em 600 caracteres e marca com reticencia. Classificar por
    600 caracteres de publicacao judicial e classificar pelo cabecalho.

    Guarda em `sync_intim_texto` para nao repetir a chamada; so busca o que
    ainda nao tem. O limite existe para o refresh nao virar 300 chamadas.
    """
    con.execute("""CREATE TABLE IF NOT EXISTS sync_intim_texto (
                     id INTEGER PRIMARY KEY, texto TEXT, criado_em REAL)""")
    guardado = {r[0]: r[1] for r in con.execute(
        'SELECT id, texto FROM sync_intim_texto')}
    buscados = 0
    for it in intims:
        iid = it.get('id')
        if not iid:
            continue
        if iid in guardado and guardado[iid]:
            it['texto'] = guardado[iid]
            it['texto_completo'] = True
            continue
        t = str(it.get('texto') or '')
        # so vale a chamada quando a listagem admite que cortou
        if not (t.rstrip().endswith('\u2026') or t.rstrip().endswith('...')):
            it['texto_completo'] = True
            continue
        if buscados >= limite:
            it['texto_completo'] = False
            continue
        try:
            det = _get(f'/intimacoes/{int(iid)}') or {}
        except Exception as e:
            log.warning('inteiro teor %s: %s', iid, e)
            it['texto_completo'] = False
            continue
        buscados += 1
        cheio = det.get('texto') or ''
        if len(cheio) > len(t):
            it['texto'] = cheio
            it['texto_completo'] = True
            con.execute('INSERT OR REPLACE INTO sync_intim_texto(id,texto,criado_em) '
                        'VALUES(?,?,?)', (iid, cheio, time.time()))
        else:
            it['texto_completo'] = False
    if buscados:
        con.commit()
        log.info('inteiro teor buscado para %d intimacoes', buscados)
    return buscados


def _conferencia_composta(con, it, r, conf):
    """A ficha que a lista e o perfil mostram (cache) tambem precisa dizer da audiencia do despacho
    composto e trocar a providencia generica pela leitura do SYNC. Advogada, 06/10/2026, depois da v8:
    'o resumo da IA no caso da a equipe nao mudou nada' (a v8 so mexia na previa de fechar)."""
    from .. import motor_tarefas as MT
    conf = dict(conf or {})
    aud = r.get('audiencia') or {}
    prov = str(conf.get('providencia') or '')
    if not prov or prov.startswith('Ato sem pratica registrada'):
        row = con.execute('SELECT acao FROM sync_intim_triagem WHERE id=?', (it.get('id'),)).fetchone()
        leitura = str((row[0] if row else '') or it.get('resumo') or '').strip()
        if leitura:
            prov = 'Leitura da publicacao: ' + leitura[:900]
            conf['providencia'] = prov
            conf['providencia_fonte'] = 'sync'
    if aud.get('data'):
        quando = MT._br(aud['data']) + ((' ' + aud['hora']) if aud.get('hora') else '')
        aviso = ('DESPACHO COMPOSTO: alem do prazo, designa audiencia%s para %s%s. O POP 18 ao cliente, o roteiro e a '
                 'preparacao D-7 entram na cadeia ao fechar e pelo vigia de audiencias.'
                 % ((' ' + aud['especie']) if aud.get('especie') else '', quando,
                    (', ' + aud['modalidade']) if aud.get('modalidade') else ''))
        if 'DESPACHO COMPOSTO' not in prov:
            conf['providencia'] = (aviso + ' ' + prov).strip()
        conf['audiencia'] = aud
    return conf


def _motor_sombra(con, intims):
    """Roda o motor deterministico sobre as intimacoes e grava o resultado (modo sombra).
    NAO cadastra tarefa/prazo/evento — so calcula e reporta. Retorna dict id->motor."""
    try:
        from app.controladoria_motor import classificador as CL
    except Exception as e:
        log.warning('motor sombra indisponivel: %s', e)
        return {}
    titmap = {r[0]: {'titularidade': r[1], 'nosso_polo': r[2], 'cliente': r[3],
                     'parte_adversa': r[4], 'origem': r[5],
                     'clientes': r[6], 'adversos': r[7]}
              for r in con.execute('SELECT numero,titularidade,nosso_polo,cliente,'
                                   'parte_adversa,origem,clientes,adversos '
                                   'FROM advbox_titularidade')}
    # A capa do SYNC (polo_ativo/polo_passivo) e a segunda fonte de polo e de
    # parte adversa: o cadastro do ADVBOX nao tem polo em parte dos casos e
    # erra em ~22% dos que tem (calibracao de 01/10/2026).
    try:
        for p in ((cache_get('processos') or {}).get('processos') or []):
            n = p.get('numero')
            if n and n in titmap:
                titmap[n]['polo_ativo'] = p.get('polo_ativo')
                titmap[n]['polo_passivo'] = p.get('polo_passivo')
    except Exception as e:
        log.warning('capa para o motor: %s', e)
    # v10 (06/10/2026): publicacao curta (TJSP "Vistos. Int."): a decisao e lida nos autos e entra na
    # classificacao. Ate 3 leituras novas por ciclo; o resto vem da tabela motor_teor_autos.
    try:
        from app.controladoria_motor import teor_autos as TA
    except Exception as e:
        log.warning('teor_autos indisponivel: %s', e)
        TA = None
    lidos = 0
    out = {}
    for it in intims:
        if not it.get('id'):
            continue
        teor = None
        if TA:
            try:
                teor = TA.ler(con, it, _get_all if lidos < 3 else None, _get_text,
                              lambda t: CL._so_cabecalho(CL._norm(t)))
                if teor and teor.get('fonte') == 'sync':
                    lidos += 1
            except Exception as e:
                log.warning('teor nos autos %s: %s', it.get('id'), e)
        try:
            r = CL.classificar(TA.com_teor(it, teor) if (TA and teor) else it, titularidade=titmap.get(it.get('processo')))
        except Exception as e:
            log.warning('classificar %s: %s', it.get('id'), e)
            continue
        if teor:
            r['evidencias'] = list(r.get('evidencias') or []) + [
                'teor lido nos autos: %s de %s (documento %s)' % (teor.get('tipo_documento') or teor.get('nome'),
                                                                   teor.get('data'), teor.get('documento_id'))]
        principais = [p for p in r['prazos'] if not p.get('paralelo')]
        venc = principais[0].get('vencimento') if principais else None
        fatal_int = principais[0].get('prazo_fatal_interno') if principais else None
        dias = principais[0].get('dias') if principais else None
        contagem = principais[0].get('contagem') if principais else None
        con.execute('INSERT OR REPLACE INTO sync_motor_sombra(id,ato,ramo,trilha,fatal,titularidade,responsavel,confianca,vencimento,fatal_interno,dias,contagem,prazos,evidencias,avisos,criado_em) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (it['id'], r['ato'], r['ramo'], r['trilha'], 1 if r['fatal'] else 0, r['titularidade'],
                     r.get('responsavel'), r['confianca'], venc, fatal_int, dias, contagem,
                     json.dumps(r['prazos']), json.dumps(r['evidencias']), json.dumps(r['avisos']), time.time()))
        # A providencia sugerida vem da pratica do escritorio, minerada do
        # historico do ADVBOX. Sugere; nao cadastra nada.
        try:
            from app.controladoria_motor import conferencia as CF
            _t = titmap.get(it.get('processo')) or {}
            conf = CF.sugerir(r, _t.get('nosso_polo'), it,
                              _t.get('cliente'), _t.get('parte_adversa'),
                              _lista_json(_t.get('clientes')),
                              _lista_json(_t.get('adversos')))
        except Exception as e:
            log.warning('conferencia %s: %s', it.get('id'), e)
            conf = None
        # despacho composto e leitura do SYNC tambem na ficha do cache (lista e perfil), nao so na previa
        try:
            conf = _conferencia_composta(con, it, r, conf)
        except Exception as e:
            log.warning('conferencia composta %s: %s', it.get('id'), e)
        # vigia_corrige_titularidade: negado o seguimento do recurso DELES, o
        # ato cabivel continua sendo agravo de instrumento, e o classificador o
        # rotula como nosso. O prazo, porem, e de quem perdeu. A ficha nao pode
        # mostrar prazo fatal em nome do nosso cliente e, do lado, dizer que
        # nao ha nada a peticionar.
        tit_final = r['titularidade']
        # condicao_apaga_prazo: o despacho traz o numero de dias, mas manda
        # contar de um ato futuro. A titularidade continua NOSSA (o prazo sera
        # nosso), so a data ainda nao existe.
        if (conf or {}).get('condicao'):
            venc, fatal_int = None, None
            r['avisos'] = list(r.get('avisos') or []) + [
                'Prazo condicionado a ato anterior; nao comecou a correr']
        if (conf or {}).get('vigiar_em'):
            tit_final = 'CONTRARIA'
            venc, fatal_int, dias, contagem = None, None, None, None
            r['avisos'] = list(r.get('avisos') or []) + [
                'Prazo remanescente e da parte contraria; o transito se '
                'consolida em ' + str((conf or {}).get('transito_em') or '')]
        out[it['id']] = {
            'ato': r['ato'], 'trilha': r['trilha'], 'fatal': r['fatal'], 'responsavel': r.get('responsavel'),
            'titularidade': tit_final, 'cliente': r.get('cliente'), 'parte_adversa': r.get('parte_adversa'),
            'confianca': r['confianca'], 'vencimento': venc, 'prazo_fatal_interno': fatal_int,
            'dias': dias, 'contagem': contagem, 'prazos': r['prazos'], 'avisos': r['avisos'],
            'evidencias': r.get('evidencias') or [], 'conferencia': conf,
            # despacho composto (a equipe 86458, 06/10/2026): audiencia designada junto com outro prazo
            'audiencia': r.get('audiencia'),
            # v10: a decisao lida nos autos (publicacao curta), so o resumo; o texto fica em motor_teor_autos
            'teor_autos': (TA.resumo(teor) if (TA and teor) else None),
            # calibracao de 01/10/2026: o que o auto-tratamento e a tela precisam saber
            'copia_de_adverso': bool(r.get('copia_de_adverso')),
            'destinatarios': r.get('destinatarios') or [],
            'parceiro': r.get('parceiro'),
            'teor_fora_do_djen': bool(r.get('teor_fora_do_djen')),
            'sigiloso': bool(r.get('sigiloso')),
        }
    con.commit()
    return out


def _tarefas_advbox_depois(con, processo, disp):
    """Tarefas desse processo abertas a partir da publicacao: do espelho do ADVBOX
    e, desde 06/10/2026, as nascidas no proprio Sistema Escritório Modelo (motor, tela, manual).
    Tipo PECA/REVISAO/AJUSTES do motor conta como a peca que e (so leitura)."""
    if not processo:
        return []
    d = str(disp or '')[:10]
    try:
        rows = con.execute(
            "SELECT t.id, t.tipo, t.origem_ref, t.data, t.origem, "
            "(SELECT GROUP_CONCAT(nome, ', ') FROM tarefa_responsaveis r WHERE r.tarefa_id=t.id) "
            "FROM tarefas t WHERE t.origem IN ('advbox','motor','intimacao','manual','chat') "
            "AND t.status != 'cancelada' AND t.processo_numero=? AND COALESCE(t.data,'') >= ?",
            (processo, d)).fetchall()
    except Exception as e:
        log.warning('tarefas_advbox_depois %s: %s', processo, e)
        return []
    out = []
    for r in rows:
        tipo = r[1] or ''
        if r[4] == 'motor' and tipo in (TT.PECA, TT.REVISAO, TT.AJUSTES):
            tipo = TT.REVISAO_DE_PECA
        out.append({'id': r[0], 'tipo': tipo, 'origem_ref': r[2], 'data': r[3], 'origem': r[4],
                    'responsavel': r[5] or ''})
    return out


def _sugerir_conclusao(con, intims, motor):
    """REGRA da Advogada (01/10/2026): o motor NUNCA trata nada sozinho. Para o que
    pode ser concluido (ciencia pura, copia da parte contraria, publicacao ja
    conferida no ADVBOX) ele escreve a SUGESTAO na ficha, com o motivo e quem da
    equipe conclui; a pessoa clica em "tratada". Nada e gravado no SYNC aqui."""
    try:
        from app.controladoria_motor import autotrato as AT
    except Exception as e:
        log.warning('autotrato indisponivel: %s', e)
        return
    for it in intims:
        if it.get('id') in motor:
            it['motor'] = motor[it['id']]
    n = 0
    for it in intims:
        iid = it.get('id')
        m = motor.get(iid) if iid else None
        if not m:
            continue
        try:
            tarefas = _tarefas_advbox_depois(con, it.get('processo'), it.get('data'))
            dec = AT.decidir(it, m, tarefas, fila=intims)
        except Exception as e:
            log.warning('sugestao de conclusao %s: %s', iid, e)
            dec = None
        if not dec:
            continue
        sug = AT.sugestao_conclusao(dec, tarefas)
        m['sugestao_conclusao'] = sug
        conf = m.get('conferencia') or {}
        conf['sugestao_conclusao'] = sug
        conf['providencia'] = sug['texto'] + ' ' + (conf.get('providencia') or '')
        conf['tarefa'] = 'CONCLUIR'
        conf['quem'] = sug['quem']
        conf['abre_tarefa_de_prazo'] = False
        m['conferencia'] = conf
        n += 1
    if n:
        log.info('sugestao de conclusao em %d intimacao(oes); nenhuma tratada pelo motor', n)


def refresh_cache():
    """Puxa tudo do SYNC e grava no cache local. Roda numa thread."""
    if not (getattr(settings, 'SYNC_API_TOKEN', '') or ''):
        return
    con = _cache_con()
    try:
        _cache_set(con, 'dashboard', _get('/dashboard'))
        _cache_set(con, 'monitores', _get('/monitores'))
        _cache_set(con, 'publicacoes', _get('/oab/publicacoes'))
        inti = _get_all('/intimacoes', 'intimacoes', {'status': 'pendentes'})
        intims = inti.get('intimacoes', [])
        # inteiro teor primeiro: triagem e motor leem o mesmo campo `texto`
        try:
            _completar_texto(con, intims)
        except Exception as e:
            log.warning('inteiro teor: %s', e)
        _triar_pendentes(con, intims, limite=6)
        tri = {r[0]: {'categoria': r[1], 'urgencia': r[2], 'acao': r[3], 'prazo_dias': r[4], 'requer_acao': bool(r[5])}
               for r in con.execute('SELECT id,categoria,urgencia,acao,prazo_dias,requer_acao FROM sync_intim_triagem')}
        # Motor deterministico (SOMBRA): titularidade (bounded) + calculo de prazo.
        try:
            _resolver_titularidade(con, [it.get('processo') for it in intims], limite=6)
            motor = _motor_sombra(con, intims)
        except Exception as e:
            log.warning('motor sombra: %s', e)
            motor = {}
        # Sugestao de conclusao (01/10/2026): ciencia pura, copia da parte
        # contraria e publicacao ja conferida no ADVBOX ganham na ficha a
        # sugestao "concluir" com o motivo e quem conclui. Ninguem e tratada
        # pelo motor: a regra da Advogada e que a conclusao e sempre humana.
        try:
            _sugerir_conclusao(con, intims, motor)
        except Exception as e:
            log.warning('sugestao de conclusao: %s', e)
        for it in intims:
            if it.get('id') in tri:
                it['triagem'] = tri[it['id']]
            if it.get('id') in motor:
                it['motor'] = motor[it['id']]
        _cache_set(con, 'intimacoes', inti)
        _cache_set(con, 'processos', _get_all('/processos', 'processos'))
        _cache_set(con, 'partes', _get_all('/partes', 'entidades'))
        con.commit()
        n = _alerta_intimacoes(con, inti.get('intimacoes', []))
        if n:
            log.info('Controladoria: %d alerta(s) de intimacao enviados', n)
    except Exception as e:
        log.warning('refresh_cache: %s', e)
    finally:
        con.close()


async def cache_loop():
    """Loop de fundo: atualiza o cache do SYNC de X em X segundos."""
    seg = int(getattr(settings, 'SYNC_CACHE_SEGUNDOS', 300) or 300)
    while True:
        try:
            await asyncio.to_thread(refresh_cache)
        except Exception as e:
            log.warning('cache_loop: %s', e)
        await asyncio.sleep(seg)


@router.get('/status')
async def status(user: Usuario = Depends(usuario_atual)):
    """Diz se o SYNC esta configurado (sem expor a chave)."""
    return {'conectado': bool(getattr(settings, 'SYNC_API_TOKEN', '') or '')}


@router.get('/resumo')
async def resumo(user: Usuario = Depends(usuario_atual)):
    """Contadores pros cartoes da tela (intimacoes pendentes, processos, monitores)."""
    if not (getattr(settings, 'SYNC_API_TOKEN', '') or ''):
        return {'conectado': False, 'intimacoes_pendentes': 0, 'processos': 0, 'monitores': 0}
    inti = await asyncio.to_thread(_get, '/intimacoes', {'status': 'pendentes'})
    proc = await asyncio.to_thread(_get, '/processos')
    mons = 0
    try:
        m = await asyncio.to_thread(_get, '/monitores')
        mons = m.get('total', len(m.get('monitores', []))) if isinstance(m, dict) else 0
    except Exception:
        mons = 0
    return {'conectado': True,
            'intimacoes_pendentes': inti.get('total', 0),
            'processos': proc.get('total', 0),
            'monitores': mons}


@router.get('/dashboard')
async def dashboard(user: Usuario = Depends(usuario_atual)):
    """Painel do dia (tudo numa chamada) — prazos, intimacoes, audiencias, carteira, atividade."""
    if not (getattr(settings, 'SYNC_API_TOKEN', '') or ''):
        return {'conectado': False}
    d = cache_get('dashboard')
    if d is None:
        d = await asyncio.to_thread(_get, '/dashboard')
    if isinstance(d, dict):
        d['conectado'] = True
    return d



def _marcar_chegada(d):
    """Anexa visto_em: o dia em que a intimacao apareceu para nos.

    cache_get devolve um objeto novo a cada chamada (json.loads), entao
    escrever aqui nao suja o cache.
    """
    try:
        itens = (d or {}).get("intimacoes") or []
        if not itens:
            return d
        con = _cache_con()
        try:
            vis = {r[0]: r[1] for r in
                   con.execute("SELECT id, criado_em FROM sync_intim_vistas")}
        finally:
            con.close()
        import datetime as _dt
        for it in itens:
            try:
                t = vis.get(int(it.get("id") or 0))
            except (TypeError, ValueError):
                t = None
            it["visto_em"] = (_dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d")
                              if t else str(it.get("data") or "")[:10])
    except Exception as e:
        log.warning("marcar chegada: %s", e)
    return d


@router.get('/intimacoes')
async def intimacoes(status: str = 'pendentes', com_prazo: bool = False, dias: int = 0,
                     user: Usuario = Depends(usuario_atual)):
    """Inbox real de intimacoes, COMPLETO (todas as paginas). Le do cache quando pendentes."""
    if status == 'pendentes' and not com_prazo and not dias:
        d = cache_get('intimacoes')
        if d is not None:
            return _marcar_chegada(d)
    params = {'status': status}
    if com_prazo:
        params['com_prazo'] = 'true'
    if dias:
        params['dias'] = dias
    return _marcar_chegada(
        await asyncio.to_thread(_get_all, '/intimacoes', 'intimacoes', params))


@router.get('/publicacoes')
async def publicacoes(user: Usuario = Depends(usuario_atual)):
    """Publicacoes do DJEN por OAB (inbox)."""
    d = cache_get('publicacoes')
    return d if d is not None else await asyncio.to_thread(_get, '/oab/publicacoes')


@router.get('/processos')
async def processos(user: Usuario = Depends(usuario_atual)):
    """Carteira COMPLETA de processos sincronizados (todas as paginas)."""
    d = cache_get('processos')
    return d if d is not None else await asyncio.to_thread(_get_all, '/processos', 'processos')


@router.get('/partes')
async def partes(user: Usuario = Depends(usuario_atual)):
    """Partes/entidades da carteira, COMPLETO (todas as paginas)."""
    d = cache_get('partes')
    return d if d is not None else await asyncio.to_thread(_get_all, '/partes', 'entidades')


@router.get('/autos/{numero}')
async def autos(numero: str, user: Usuario = Depends(usuario_atual)):
    """Autos digitais (linha do tempo unificada) do processo sincronizado."""
    num = (numero or '').strip()
    return await asyncio.to_thread(_get, f'/processos/{num}/autos', {'itens': 300})


@router.get('/intimacao/{intimacao_id}')
async def intimacao_detalhe(intimacao_id: int, user: Usuario = Depends(usuario_atual)):
    """Teor completo de uma intimacao/ato (texto vindo do DJEN)."""
    return await asyncio.to_thread(_get, f'/intimacoes/{intimacao_id}')


@router.get('/lacunas')
async def lacunas(user: Usuario = Depends(usuario_atual)):
    """Status de download de documentos (o que exige certificado/PJe)."""
    return await asyncio.to_thread(_get, '/documentos/lacunas-credencial')


def _get_text(path):
    """GET que aceita texto puro OU json — devolve sempre um dict."""
    r = requests.get(f'{_base()}{path}', headers=_headers(), timeout=40)
    if r.status_code >= 400:
        raise HTTPException(502, f'SYNC {r.status_code}: {r.text[:150]}')
    try:
        j = r.json()
        return j if isinstance(j, dict) else {'markdown': str(j)}
    except Exception:
        return {'markdown': r.text}


@router.get('/documento/{documento_id}/markdown')
async def documento_markdown(documento_id: int, user: Usuario = Depends(usuario_atual)):
    """Teor (texto/markdown) do documento — quando o SYNC ja o baixou."""
    return await asyncio.to_thread(_get_text, f'/documentos/{documento_id}/markdown')


@router.get('/documento/{documento_id}/arquivo')
async def documento_arquivo(documento_id: int, user: Usuario = Depends(usuario_atual)):
    """Link (URL assinada, ~1h) pro arquivo ORIGINAL da peca no tribunal.
    Retorna {url, mime, nome, expira_em_segundos} — a tela abre a URL."""
    return await asyncio.to_thread(_get, f'/documentos/{documento_id}/arquivo')



def _norm_evento(v):
    import unicodedata
    return unicodedata.normalize('NFKD', str(v or '')).encode('ascii', 'ignore').decode().upper()


def _especie_evento(titulo):
    """audiencia | pericia | julgamento | sustentacao | compromisso.

    Era deduzido da HORA ("tem hora, logo e audiencia"), o que rotulava uma
    sessao de julgamento como audiencia. O titulo diz a verdade, e a ordem
    importa: PERICIA antes de AUDIENCIA, senao "AUDIENCIA DE PERICIA" cai no
    lugar errado.
    """
    t = _norm_evento(titulo)
    if "SUSTENTACAO" in t:
        return "sustentacao"
    if "PERICIA" in t or "PERICIAL" in t:
        return "pericia"
    # SESSAO sozinho nao serve: "Sessao de Desenvolvimento" da Dra Advogada
    # virava julgamento. Exige a palavra julgamento ou pauta.
    if "JULGAMENTO" in t or "PAUTA" in t:
        return "julgamento"
    if "AUDIENCIA" in t:
        return "audiencia"
    return "compromisso"


def _data_hora_br(valor):
    """'2026-09-30T16:50:00+00:00' -> ('2026-09-30', '13:50') no fuso de Sao Paulo.
    Sem fuso no texto: mantem como veio. So data: hora None."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    try:
        from zoneinfo import ZoneInfo
        fuso = ZoneInfo('America/Sao_Paulo')
    except Exception:                      # sem base de fusos: Brasil sem horario de verao desde 2019
        fuso = _tz(_td(hours=-3))
    s = str(valor or '')
    try:
        d = _dt.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return s[:10], (s[11:16] or None)
    if d.tzinfo is not None:
        d = d.astimezone(fuso)
    return d.strftime('%Y-%m-%d'), (d.strftime('%H:%M') if len(s) > 10 else None)


def _evento_da_audiencia(ev, eventos_google):
    """Evento da agenda central do mesmo dia com o numero do processo (so digitos) ou
    o nome do cliente (primeiro + ultimo nome) no titulo."""
    import re as _re
    num = _re.sub(r'\D', '', str(ev.get('processo') or ''))
    nome = [w for w in _re.findall(r'[A-ZÀ-Ú]{3,}', str(ev.get('polo_ativo') or '').upper())]
    for g in eventos_google:
        if g.get('data') != ev.get('data'):
            continue
        tit = str(g.get('titulo') or '').upper()
        if num and len(num) >= 15 and num in _re.sub(r'\D', '', tit):
            return g
        if len(nome) >= 2 and nome[0] in tit and nome[-1] in tit and 'AUDI' in tit:
            return g
    return None


@router.get('/agenda')
async def agenda(user: Usuario = Depends(usuario_atual)):
    """Eventos p/ o calendario: prazos (intimacoes com data_fatal) + audiencias + prazos do dashboard."""
    inti = cache_get('intimacoes') or {}
    dash = cache_get('dashboard') or {}
    procs = (cache_get('processos') or {}).get('processos') or []
    plook = {p.get('numero'): p for p in procs}
    eventos, eventos_google = [], []
    for it in (inti.get('intimacoes') or []):
        mot = it.get('motor') or {}
        # Autoridade da data = motor deterministico; SYNC e fallback.
        data_ev = mot.get('prazo_fatal_interno') or mot.get('vencimento') or it.get('data_fatal') or it.get('prazo_ato')
        if not data_ev:
            continue
        eventos.append({
            'data': str(data_ev)[:10], 'tipo': 'prazo',
            'titulo': (mot.get('ato') or it.get('tipo_ato') or it.get('tipo_comunicacao') or 'Prazo'),
            'intimacao_id': it.get('id'), 'tratada': bool(it.get('tratada')),
            'processo': it.get('processo'), 'tribunal': it.get('tribunal'),
            'orgao': it.get('orgao'), 'prazo_dias': mot.get('dias') or it.get('prazo_dias'),
            'manifestacao': bool(it.get('requer_manifestacao')),
            'resumo': (it.get('resumo') or '')[:220],
            # campos do motor (autoridade): fatal interno D-2, vencimento, trilha, titularidade
            'vencimento': mot.get('vencimento'), 'prazo_fatal_interno': mot.get('prazo_fatal_interno'),
            'data_fatal_sync': str(it.get('data_fatal') or '')[:10] or None,
            'trilha': mot.get('trilha'), 'responsavel': mot.get('responsavel'),
            'fatal': mot.get('fatal'), 'titularidade': mot.get('titularidade'),
            'confianca': mot.get('confianca'), 'avisos': (mot.get('avisos') or [])[:3],
        })
    aud = dash.get('audiencias') or {}
    for a in (aud.get('proximas') or []):
        d = a.get('data') or a.get('data_audiencia')
        if not d:
            continue
        # 28/09/2026 (aviso da Advogada): o SYNC manda a hora em UTC
        # ("2026-09-30T16:50:00+00:00" = 13:50 em Brasilia). Recortar o texto
        # mostrava toda audiencia 3h adiantada; converte para Sao Paulo.
        dia_ev, hora_ev = _data_hora_br(d)
        eventos.append({'data': dia_ev, 'hora': hora_ev, 'tipo': 'audiencia',
                        'titulo': a.get('tipo') or 'Audiência', 'processo': a.get('processo'),
                        'tribunal': a.get('tribunal')})
    for p in (dash.get('prazos') or []):
        d = p.get('data') or p.get('prazo')
        if not d:
            continue
        eventos.append({'data': str(d)[:10], 'tipo': 'prazo',
                        'titulo': p.get('descricao') or p.get('tipo') or 'Prazo',
                        'processo': p.get('processo'), 'tribunal': p.get('tribunal')})
    # dedup por (data, processo, titulo)
    vistos, uniq = set(), []
    for e in eventos:
        k = (e['data'], e.get('processo'), e['titulo'])
        if k in vistos:
            continue
        vistos.add(k)
        uniq.append(e)
    # enriquece com os polos (cliente x parte contraria) a partir da carteira
    for e in uniq:
        p = plook.get(e.get('processo'))
        if p:
            e['polo_ativo'] = p.get('polo_ativo')
            e['polo_passivo'] = p.get('polo_passivo')
            e['classe'] = p.get('classe')
    # ---- eventos REAIS da agenda do Google -------------------------------
    # Sem isto, o que a equipe cadastra some da tela: a lista acima e toda
    # derivada de intimacao/dashboard e nao sabe o que foi para o calendario.
    # Falha do Google nao pode derrubar a tela, entao vai em try/except.
    marcados = {}
    try:
        for g in await asyncio.to_thread(AG.listar):
            marcador = (g.get('marcador') or '')
            if marcador.startswith('intim:'):
                marcados[marcador[6:]] = g
            # listar() nao devolve cumprido: o marcador dele e o prefixo
            # [ok] no titulo, posto por marcar_cumprido().
            tit = g.get('titulo') or ''
            eventos_google.append({
                'data': g.get('data'), 'hora': g.get('hora'),
                'tipo': _especie_evento(tit),
                'titulo': tit, 'evento_id': g.get('evento_id'),
                'link': g.get('link'), 'cumprido': tit.lower().startswith('[ok]'),
                'cadastrado': True,
            })
    except Exception as e:
        log.warning('agenda do Google indisponivel: %s', e)

    for e in uniq:
        g = marcados.get(str(e.get('intimacao_id') or ''))
        if g:
            e['cadastrado'] = True
            e['evento_id'] = g.get('evento_id')
            e['link'] = g.get('link')

    # Audiencia do SYNC nao tem intimacao_id: liga ao evento da agenda central
    # que for do MESMO dia e trouxer o numero do processo (ou o nome do cliente)
    # no titulo. Sem isto ela ficava "a cadastrar" mesmo cadastrada, e o evento
    # aparecia de novo numa segunda linha.
    for e in uniq:
        if e.get('tipo') != 'audiencia' or e.get('cadastrado'):
            continue
        g = _evento_da_audiencia(e, eventos_google)
        if g:
            e['cadastrado'] = True
            e['evento_id'] = g.get('evento_id')
            e['link'] = g.get('link')

    # o que veio do Google e que NAO e espelho de um derivado entra na lista
    ja = {(x.get('evento_id')) for x in uniq if x.get('evento_id')}
    uniq += [x for x in eventos_google if x.get('evento_id') not in ja]

    uniq.sort(key=lambda e: (e['data'] or '', e.get('hora') or ''))
    return {'eventos': uniq, 'total': len(uniq), 'agenda_ativa': AG.disponivel()}


@router.get('/preview/{numero}')
async def preview(numero: str, user: Usuario = Depends(usuario_atual)):
    """Pre-visualizacao COMPLETA do processo (cabecalho, partes, movimentos,
    audiencias, documentos/pecas) — SEM registrar, sem custo no plano."""
    num = (numero or '').strip()
    if not num:
        raise HTTPException(400, 'numero obrigatorio')
    return await asyncio.to_thread(_get, f'/preview/{num}')


@router.get('/processo/{numero}')
async def processo_detalhe(numero: str, user: Usuario = Depends(usuario_atual)):
    """Detalhe do processo JA sincronizado na carteira (dados definitivos)."""
    num = (numero or '').strip()
    return await asyncio.to_thread(_get, f'/processos/{num}')


@router.get('/monitores')
async def listar_monitores(user: Usuario = Depends(usuario_atual)):
    d = cache_get('monitores')
    return d if d is not None else await asyncio.to_thread(_get, '/monitores')


class MonitorReq(BaseModel):
    tipo: str = 'oab'
    valor: str


@router.post('/monitores')
async def add_monitor(req: MonitorReq, user: Usuario = Depends(requer_admin)):
    """Liga o monitor por OAB — o SYNC passa a acompanhar sozinho (so admin)."""
    return await asyncio.to_thread(_post, '/monitores', {'tipo': req.tipo, 'valor': req.valor.strip()})


class BuscaReq(BaseModel):
    tipo: str = 'oab'
    valor: str
    cursor: str | None = None


@router.post('/busca')
async def busca(req: BuscaReq, user: Usuario = Depends(usuario_atual)):
    """Busca por OAB em tempo real (PDPJ). Pagina por cursor sob demanda —
    a busca ao vivo e lenta, entao volta 1 pagina + cursor pra 'carregar mais'."""
    body = {'tipo': req.tipo, 'valor': req.valor.strip()}
    if req.cursor:
        body['cursor'] = req.cursor
    return await asyncio.to_thread(_post, '/busca', body)


class RegistrarReq(BaseModel):
    numero: str
    monitorar: bool = True


@router.post('/registrar')
async def registrar(req: RegistrarReq, user: Usuario = Depends(usuario_atual)):
    """Registra 1 processo na carteira do SYNC (sincroniza + monitora, assincrono).

    Cada processo registrado conta no plano do SYNC — por isso e por processo,
    acionado pelo usuario, nunca em massa automatico.
    """
    num = (req.numero or '').strip()
    if not num:
        raise HTTPException(400, 'numero do processo obrigatorio')
    return await asyncio.to_thread(_post, f'/processos/{num}/sincronizar', {'monitorar': req.monitorar})


# ============================================================================
# CARTEIRA — a aba "Processos na carteira" da tela Controladoria.
# O front chama GET /api/controladoria/carteira e espera
# {cards, total, com_intimacao_aberta, sem_cadastro_advbox}.
# Le tudo do cache local; nao bate no SYNC durante a requisicao.
# ============================================================================
def _lista_json(v):
    try:
        d = json.loads(v) if v else []
        return [x for x in d if x] if isinstance(d, list) else []
    except Exception:
        return []


def _montar_carteira():
    con = _cache_con()
    try:
        # 1. base: processos que o SYNC monitora
        base = (cache_get('processos') or {}).get('processos') or []

        # 2. titularidade (etapa/fase/responsavel/pasta) resolvida pela Controladoria
        tit = {}
        try:
            for r in con.execute(
                    'SELECT numero, titularidade, nosso_polo, cliente, parte_adversa, '
                    'clientes, adversos, pasta_drive, etapa, fase, responsavel '
                    'FROM advbox_titularidade'):
                tit[(r[0] or '').strip()] = {
                    'titularidade': r[1], 'nosso_polo': r[2], 'cliente': r[3],
                    'parte_adversa': r[4], 'clientes': _lista_json(r[5]),
                    'adversos': _lista_json(r[6]), 'pasta_drive': r[7] or '',
                    'etapa': r[8] or '', 'fase': r[9] or '', 'responsavel': r[10] or '',
                }
        except Exception as e:
            log.warning('carteira/titularidade: %s', e)

        # 3. quais processos existem no NOSSO ADVBOX (o resto e furo de cadastro)
        no_advbox = set()
        try:
            for (num,) in con.execute(
                    "SELECT numero FROM jur_processos WHERE numero IS NOT NULL AND TRIM(numero) <> ''"):
                no_advbox.add((num or '').strip())
        except Exception as e:
            log.warning('carteira/advbox: %s', e)

        # 4. intimacoes abertas e o proximo prazo, por processo
        abertas, prazos = {}, {}
        for it in ((cache_get('intimacoes') or {}).get('intimacoes') or []):
            num = (it.get('processo') or '').strip()
            if not num or it.get('tratada'):
                continue
            abertas[num] = abertas.get(num, 0) + 1
            motor = it.get('motor') or {}
            venc = motor.get('vencimento') or it.get('data_fatal')
            if not venc:
                continue
            atual = prazos.get(num)
            if atual is None or str(venc) < str(atual['data']):
                prazos[num] = {'data': venc,
                               'ato': motor.get('ato') or it.get('tipo_ato') or 'prazo',
                               'responsavel': motor.get('responsavel') or '',
                               'intimacao_id': it.get('id'),
                               'na_agenda': False}

        # 5. o que ja foi para a agenda deixa de ser alerta vermelho
        try:
            na_agenda = {r[0] for r in con.execute('SELECT id FROM sync_intim_agenda')}
            for p in prazos.values():
                if p.get('intimacao_id') in na_agenda:
                    p['na_agenda'] = True
        except Exception:
            pass

        cards = []
        for p in base:
            num = (p.get('numero') or '').strip()
            if not num:
                continue
            t = tit.get(num) or {}
            clientes = t.get('clientes') or ([t['cliente']] if t.get('cliente') else [])
            cards.append({
                'numero': num,
                'tribunal': p.get('tribunal') or '',
                'classe': p.get('classe') or '',
                'assunto': p.get('assunto') or '',
                'orgao': p.get('orgao_julgador') or '',
                'sigiloso': bool(p.get('segredo_justica')),
                'baixado': bool(p.get('baixado')),
                'polo_ativo': p.get('polo_ativo') or '',
                'polo_passivo': p.get('polo_passivo') or '',
                'cliente': t.get('cliente') or '',
                'clientes': clientes,
                'parte_adversa': t.get('parte_adversa') or '',
                'nosso_polo': t.get('nosso_polo') or '',
                'etapa': t.get('etapa') or '',
                'fase': t.get('fase') or '',
                'responsavel': t.get('responsavel') or '',
                'pasta_drive': t.get('pasta_drive') or '',
                'intimacoes_abertas': abertas.get(num, 0),
                'proximo_prazo': prazos.get(num),
                'no_advbox': num in no_advbox,
                'ultimo_andamento': p.get('ultimo_andamento'),
            })

        # com prazo mais perto primeiro, depois quem tem intimacao aberta
        cards.sort(key=lambda c: (
            c['proximo_prazo']['data'] if c.get('proximo_prazo') else '9999',
            -c['intimacoes_abertas']))

        return {
            'cards': cards,
            'total': len(cards),
            'com_intimacao_aberta': sum(1 for c in cards if c['intimacoes_abertas']),
            'sem_cadastro_advbox': sum(1 for c in cards if not c['no_advbox']),
        }
    finally:
        con.close()


@router.get('/carteira')
async def carteira(user: Usuario = Depends(usuario_atual)):
    """Processos da carteira em formato de card, para a tela Controladoria."""
    return await asyncio.to_thread(_montar_carteira)


@router.get('/agenda/equipe')
async def agenda_equipe(user: Usuario = Depends(usuario_atual)):
    """Equipe que pode ser convidada para um compromisso da agenda."""
    def _ler():
        con = _cache_con()
        try:
            linhas = con.execute(
                'SELECT nome, email, papel FROM usuarios WHERE ativo=1 ORDER BY nome').fetchall()
        finally:
            con.close()
        return {'equipe': [{'nome': (r[0] or r[1] or '').split()[0].title(),
                            'email': r[1], 'trilha': ''} for r in linhas]}
    return await asyncio.to_thread(_ler)

# ============================================================================
# CIENTE — marcar intimacao como lida/tratada.
#
# O SYNC e a fonte da verdade: quem decide se a intimacao esta tratada e o
# POST /intimacoes/{id}/tratar la. Aqui a gente so empurra, registra QUEM deu
# ciencia (auditoria em sync_intim_tratamento) e corrige o cache na hora.
#
# Sem a correcao do cache a tela mente: a pessoa clica Ciente, atualiza a
# pagina e a intimacao volta pendente, porque a lista sai do cache local e o
# loop so refaz a leitura no proximo ciclo.
# ============================================================================
class CienteIn(BaseModel):
    ids: list = []
    motivo: str = ''


def _sync_tratar(iid):
    r = requests.post(f'{_base()}/intimacoes/{int(iid)}/tratar', headers=_headers(), timeout=45)
    if r.status_code >= 400:
        raise HTTPException(502, f'SYNC {r.status_code}: {r.text[:150]}')
    return True


def _cache_marca_tratadas(ids, valor=True):
    """Escreve tratada no cache local, para a tela nao mostrar dado velho."""
    try:
        d = cache_get('intimacoes')
        if not d:
            return
        alvo = set(int(x) for x in ids)
        for it in (d.get('intimacoes') or []):
            if int(it.get('id') or 0) in alvo:
                it['tratada'] = bool(valor)
        con = _cache_con()
        _cache_set(con, 'intimacoes', d)
        con.commit()
        con.close()
    except Exception as e:
        log.warning('cache_marca_tratadas: %s', e)


@router.post('/intimacoes/ciente')
async def marcar_ciente(dados: CienteIn, user: Usuario = Depends(usuario_atual)):
    """Da ciencia em uma ou varias intimacoes. Parcial e sucesso parcial:
    devolve o que passou e o que falhou, sem abortar o lote inteiro."""
    ids = [int(x) for x in (dados.ids or []) if str(x).strip().isdigit()]
    if not ids:
        raise HTTPException(400, 'informe ao menos um id')
    if len(ids) > 200:
        raise HTTPException(400, 'no maximo 200 por vez')

    ok, falhas = [], []
    for iid in ids:
        try:
            await asyncio.to_thread(_sync_tratar, iid)
            ok.append(iid)
        except HTTPException as e:
            falhas.append({'id': iid, 'erro': str(e.detail)[:120]})
        except Exception as e:
            falhas.append({'id': iid, 'erro': str(e)[:120]})

    if ok:
        try:
            con = _cache_con()
            agora = time.time()
            for iid in ok:
                con.execute('INSERT OR REPLACE INTO sync_intim_tratamento'
                            '(id,tratada,por,regra,motivo,criado_em) VALUES(?,?,?,?,?,?)',
                            (iid, 1, user.nome or user.email, 'ciente-manual',
                             (dados.motivo or '')[:300], agora))
            con.commit()
            con.close()
        except Exception as e:
            log.warning('auditoria ciente: %s', e)
        _cache_marca_tratadas(ok)

    return {'ok': True, 'tratadas': ok, 'falhas': falhas}

def _sync_destratar(iid):
    r = requests.delete(f'{_base()}/intimacoes/{int(iid)}/tratar', headers=_headers(), timeout=45)
    if r.status_code >= 400:
        raise HTTPException(502, f'SYNC {r.status_code}: {r.text[:150]}')
    return True


def _auditar(iid, user, tratada, regra, motivo=''):
    try:
        con = _cache_con()
        con.execute('INSERT OR REPLACE INTO sync_intim_tratamento'
                    '(id,tratada,por,regra,motivo,criado_em) VALUES(?,?,?,?,?,?)',
                    (int(iid), int(tratada), user.nome or user.email, regra,
                     (motivo or '')[:300], time.time()))
        con.commit()
        con.close()
    except Exception as e:
        log.warning('auditoria %s: %s', regra, e)


# A TELA ANTIGA (Controladoria) chama estas duas rotas desde sempre e elas NAO
# existiam: o botao "Marcar tratada" respondia 404 calado. Provavel baixa junto
# com o controladoria.py que se perdeu. Reposto aqui, e agora as duas telas
# fecham intimacao pelo mesmo caminho.
@router.post('/intimacao/{intimacao_id}/tratar')
async def tratar_uma(intimacao_id: int, user: Usuario = Depends(usuario_atual)):
    await asyncio.to_thread(_sync_tratar, intimacao_id)
    _auditar(intimacao_id, user, 1, 'ciente-manual')
    _cache_marca_tratadas([intimacao_id], True)
    return {'ok': True, 'tratada': True}


@router.delete('/intimacao/{intimacao_id}/tratar')
async def reabrir_uma(intimacao_id: int, user: Usuario = Depends(usuario_atual)):
    """Desfaz a ciencia: volta para a fila de pendentes, aqui e no SYNC."""
    await asyncio.to_thread(_sync_destratar, intimacao_id)
    _auditar(intimacao_id, user, 0, 'reabertura')
    _cache_marca_tratadas([intimacao_id], False)
    return {'ok': True, 'tratada': False}


# ============================================================================
# FECHAMENTO DA CONFERENCIA (02/10/2026): a intimacao vira a cadeia de tarefas
# no modulo Tarefas, o PF na agenda e o compromisso de cada etapa, de uma vez.
# A partir de 06/10/2026 e por aqui que a equipe cadastra prazo; o ADVBOX nao
# recebe mais nada. GET mostra o que seria criado (a pessoa confere e edita);
# POST cria o que ela confirmou e marca a intimacao como tratada. O motor
# nunca conclui: cada etapa e concluida por quem a executa (app/motor_tarefas.py).
# ============================================================================
class FechamentoIn(BaseModel):
    # sem Optional: neste ponto do arquivo o typing ainda nao foi importado (ele entra mais abaixo)
    etapas: list | None = None         # etapas editadas na tela; None = as da cadeia
    criar_pf: bool = True
    tratar: bool = True
    motivo: str = ''
    # correcoes da pessoa na conferencia (Advogada, 05/10/2026): o motor errou o ato, o
    # prazo e de outra parte, ou a data e outra. A pessoa manda; fica registrado.
    ato: str = ''
    titularidade: str = ''
    vencimento: str = ''
    observacao: str = ''


def _uf_da_intimacao(it):
    """UF do tribunal da intimacao ('' se nao der para saber). Vai para o calendario das etapas
    (feriado do tribunal, como o 30/10/2026 do TRT6), nunca mais vazio (fase 2, T4)."""
    from ..controladoria_motor import motor_prazos as MP
    return MP.uf_do_tribunal((it or {}).get('tribunal') or '')


def _ficha_para_fechamento(intimacao_id):
    it = _intimacao_do_cache(intimacao_id)
    if not it:
        raise HTTPException(404, 'Intimacao nao esta no cache (so as pendentes e as tratadas recentes)')
    m = dict(it.get('motor') or {})
    if not m:
        raise HTTPException(409, 'A ficha do motor ainda nao foi calculada para esta intimacao')
    # v10: a previa e o cadastro leem a publicacao COM o teor dos autos, igual ao motor do cache
    if m.get('teor_autos'):
        try:
            from ..controladoria_motor import teor_autos as TA
            con = _cache_con()
            try:
                teor = TA.ler(con, it)
            finally:
                con.close()
            it = TA.com_teor(it, teor)
        except Exception as e:
            log.warning('teor nos autos para o fechamento %s: %s', intimacao_id, e)
    return it, m


async def _com_teor(it, m):
    """Audiencia e sessao de julgamento: a cadeia precisa da data, que so esta no
    teor completo (o cache guarda o resumo). Busca no SYNC; sem resposta, segue sem."""
    alvo = (str(m.get('ato') or '') + ' ' + str(it.get('resumo') or '')).upper()
    if not any(x in alvo for x in ('AUDI', 'PAUTA', 'JULGAMENTO', 'SUSTENTA')):
        return it
    try:
        det = await asyncio.to_thread(_get, f"/intimacoes/{int(it['id'])}")
        if (det or {}).get('texto'):
            it = dict(it, texto=det['texto'], orgao=det.get('orgao') or it.get('orgao'))
    except Exception as e:
        log.warning('teor para o fechamento %s: %s', it.get('id'), e)
    return it


_ATOS_DO_CATALOGO = {}


def _atos_do_catalogo():
    """nome do ato -> trilha, lidos do catalogo. Alimenta a correcao do ato na tela."""
    if not _ATOS_DO_CATALOGO:
        try:
            cat = json.loads((Path(__file__).resolve().parents[1] / 'controladoria_motor'
                              / 'catalogo_atos.json').read_text(encoding='utf-8'))

            def varre(d):
                for k, v in d.items():
                    if isinstance(v, dict):
                        if 'gatilhos' in v or 'trilha' in v:
                            _ATOS_DO_CATALOGO.setdefault(k, v.get('trilha') or 'PECA')
                        else:
                            varre(v)
            varre(cat)
        except Exception as e:
            log.warning('catalogo de atos para o fechamento: %s', e)
    return _ATOS_DO_CATALOGO


async def _consulta_autos(it, m):
    """Vai aos autos ANTES de sugerir, pela mesma fonte da aba Movimentacoes do
    processo (SYNC). Sem IA. Havendo embargos, diz de quem sao e de quem e o prazo."""
    from .. import autos_consulta as AC
    num = str(it.get('processo') or '').strip()
    if not num:
        return None
    try:
        a = await asyncio.to_thread(_get_all, f'/processos/{num}/autos', 'autos', None, 2400)
    except Exception as e:
        log.warning('autos para o fechamento %s: %s', num, e)
        return {'erro': 'Nao consegui ler os autos no SYNC agora: %s' % str(getattr(e, 'detail', e))[:120]}
    itens, total = (a or {}).get('autos') or [], (a or {}).get('total')
    con = _cache_con()
    try:
        prot = AC.datas_de_protocolo(AC.registros_do_escritorio(con, it.get('processo')))
    finally:
        con.close()
    fora = AC.consultar(itens, m.get('ato'), (it.get('texto') or '') + ' ' + (it.get('resumo') or ''), it.get('data'), prot)
    fora['itens_lidos'] = len(itens)
    fora['itens_no_sync'] = total
    return fora


_ROTULO_TIT = {'NOSSO': 'nosso', 'CONTRARIA': 'da parte contraria', 'AMBOS': 'das duas partes', 'INDEFINIDO': 'a conferir'}


def _aplicar_correcoes(m, autos=None, ato='', titularidade='', vencimento='', por=''):
    """A ficha que vale no fechamento: a do motor, corrigida primeiro pelo que os
    autos mostram e depois pelo que a pessoa decidiu na tela (a pessoa manda)."""
    from .. import motor_tarefas as MT
    m = dict(m)
    original = {'ato': m.get('ato'), 'titularidade': m.get('titularidade'), 'vencimento': m.get('vencimento')}
    notas = []
    emb = (autos or {}).get('embargos') or {}
    if emb.get('titularidade') not in (None, 'INDEFINIDO', m.get('titularidade')) and not titularidade:
        notas.append('Pelos autos o prazo e %s; a leitura so do texto dizia %s.' % (
            _ROTULO_TIT.get(emb['titularidade'], emb['titularidade']), _ROTULO_TIT.get(m.get('titularidade'), m.get('titularidade'))))
        m['titularidade'] = emb['titularidade']
    # Fase pelos autos (caso Romildo): recurso de sentenca sugerido num processo que ja esta
    # em acordao do TRT/TJ vira o recurso ao tribunal superior, com ED em paralelo.
    fz = (autos or {}).get('fase') or {}
    _DE_SENTENCA = {'RECURSO ORDINARIO': 'RECURSO DE REVISTA', 'APELACAO': 'RECURSO ESPECIAL / EXTRAORDINARIO'}
    if not ato and fz.get('grau') == 2 and fz.get('decisao') == 'acordao' and str(m.get('ato') or '').upper() in _DE_SENTENCA:
        novo = _DE_SENTENCA[str(m.get('ato')).upper()]
        if novo in _atos_do_catalogo():
            notas.append('Pelos autos o processo ja esta em 2o grau (%s; %s): a decisao publicada e acordao, '
                         'nao sentenca. Ato trocado de %s para %s, com embargos de declaracao em paralelo.'
                         % (fz.get('rotulo'), fz.get('base'), m.get('ato'), novo))
            ato = novo
    ato = (ato or '').strip().upper()
    if ato and ato != str(m.get('ato') or '').upper():
        notas.append('Ato corrigido na conferencia: %s (o motor dizia %s).' % (ato, m.get('ato')))
        m['ato'] = ato
        m['trilha'] = _atos_do_catalogo().get(ato, 'PECA')
        conf = dict(m.get('conferencia') or {})
        for k in ('tarefas_sugeridas', 'vigiar_em', 'transito_em', 'sugestao_conclusao'):
            conf.pop(k, None)
        conf['providencia'] = 'Ato definido na conferencia%s: %s.' % ((' por ' + por) if por else '', ato.title())
        m['conferencia'] = conf
        m.pop('sugestao_conclusao', None)
    tit = (titularidade or '').strip().upper()
    if tit and tit != str(m.get('titularidade') or '').upper():
        notas.append('Prazo definido na conferencia como %s (antes: %s).' % (
            _ROTULO_TIT.get(tit, tit), _ROTULO_TIT.get(m.get('titularidade'), m.get('titularidade'))))
        m['titularidade'] = tit
    v = MT._data(vencimento) if vencimento else None
    if v and v.isoformat() != str(m.get('vencimento') or '')[:10]:
        notas.append('Vencimento definido na conferencia: %s (o motor calculou %s).' % (
            v.strftime('%d/%m/%Y'), m.get('vencimento') or 'nenhum'))
        m['vencimento'] = v.isoformat()
        m['prazo_fatal_interno'] = MT.FER.prazo_interno(v, 2).isoformat()
        m['prazos'] = [dict(p, vencimento=v.isoformat(), prazo_fatal_interno=m['prazo_fatal_interno'], informado_na_conferencia=True)
                       for p in (m.get('prazos') or []) if not p.get('paralelo')][:1]
    return m, notas, original


def _despacho_composto(it, m, notas):
    """a equipe 86458 (06/10/2026): o despacho recebe a emenda, DESIGNA AUDIENCIA e da 5 dias para
    documentos. O motor pegava so o prazo e a audiencia sumia. Agora: (1) a audiencia lida pelo
    classificador entra na nota da conferencia (a cadeia e o vigia de audiencias ja a usam);
    (2) quando o catalogo nao tem pratica registrada para o ato, a providencia passa a ser a
    leitura da publicacao feita pelo SYNC (triagem), em vez do texto generico."""
    from .. import motor_tarefas as MT
    m = dict(m)
    notas = list(notas or [])
    aud = m.get('audiencia') or {}
    if aud.get('data'):
        quando = MT._br(aud['data']) + ((' ' + aud['hora']) if aud.get('hora') else '')
        notas.append('Despacho composto: alem do prazo (%s), designa audiencia%s para %s%s. A cadeia traz o POP 18 '
                     '(aviso ao cliente pela automacao, fiscalizacao da Advogada e roteiro); o vigia de audiencias '
                     '(6h50) registra na agenda e abre a preparacao da Advogada (D-7).'
                     % (m.get('ato'), (' ' + aud['especie']) if aud.get('especie') else '', quando,
                        (', ' + aud['modalidade']) if aud.get('modalidade') else ''))
    conf = dict(m.get('conferencia') or {})
    prov = str(conf.get('providencia') or '')
    tri = it.get('triagem') or {}
    leitura = str(tri.get('acao') or it.get('resumo') or '').strip()
    if prov.startswith('Ato sem pratica registrada') and leitura:
        conf['providencia'] = 'Leitura da publicacao: ' + leitura[:900]
        conf['providencia_fonte'] = 'sync'
        m['conferencia'] = conf
    return m, notas


def _contagem(it, m):
    """A conta do prazo aberta, para a pessoa conferir a mao: do diario ao fatal interno."""
    principais = [p for p in (m.get('prazos') or []) if not p.get('paralelo')]
    p = principais[0] if principais else {}
    return {
        'disponibilizacao': p.get('disponibilizacao') or str(it.get('data') or '')[:10],
        'publicacao': p.get('publicacao'), 'inicio_contagem': p.get('inicio_contagem'),
        'dias': p.get('dias'), 'contagem': p.get('contagem'), 'base_legal': p.get('base_legal'),
        'vencimento': m.get('vencimento'), 'prazo_fatal_interno': m.get('prazo_fatal_interno'),
        'feriados': p.get('feriados_considerados') or [], 'uf': p.get('uf'), 'comarca': p.get('comarca'),
        'informado_na_conferencia': bool(p.get('informado_na_conferencia')),
        'paralelos': [{'ato': x.get('ato'), 'dias': x.get('dias'), 'vencimento': x.get('vencimento'),
                       'prazo_fatal_interno': x.get('prazo_fatal_interno'), 'compromisso': bool(x.get('compromisso')),
                       'hora': x.get('hora'), 'modalidade': x.get('modalidade')} for x in (m.get('prazos') or []) if x.get('paralelo')],
        'plataforma': {'prazo_dias': it.get('prazo_dias'), 'data_fatal': it.get('data_fatal')},
    }


def _cadeia_gemea(con, intimacao_id, processo, m):
    """A mesma publicacao chega como varias intimacoes (uma por destinatario). Se outra intimacao do
    mesmo processo ja virou cadeia com o mesmo vencimento (ou o mesmo ato), esta e gemea: nao abre
    nada, so pede Ciente. Advogada, 06/10/2026: 'feche uma de cada grupo e de Ciente nas outras'."""
    if not processo:
        return None
    venc = str(m.get('vencimento') or '')[:10]
    ato = str(m.get('ato') or '').upper()
    rows = con.execute("SELECT cadeia, MIN(id) a, MAX(id) b, COUNT(*) n, MIN(prazo_fatal) pf, MIN(titulo) titulo "
                       "FROM tarefas WHERE origem='motor' AND processo_numero=? AND cadeia LIKE 'intim:%' "
                       "AND cadeia NOT LIKE ? AND status != 'cancelada' GROUP BY cadeia", (processo, 'intim:%d%%' % int(intimacao_id))).fetchall()
    for r in rows:
        mesmo_prazo = venc and str(r['pf'] or '')[:10] == venc
        mesmo_ato = ato and ato in (r['titulo'] or '').upper().replace('Ã', 'A').replace('Ç', 'C')
        if mesmo_prazo or mesmo_ato:
            outra = r['cadeia'].split(':')[1].split(':')[0]
            return {'cadeia': r['cadeia'], 'intimacao': outra, 'tarefas': '#%s a #%s' % (r['a'], r['b']), 'n': r['n'],
                    'motivo': 'Esta publicacao ja foi tratada pela intimacao %s (cadeia %s, tarefas #%s a #%s, mesmo %s). '
                              'E a mesma decisao chegando por outro destinatario: de Ciente nesta.'
                              % (outra, r['cadeia'], r['a'], r['b'], 'vencimento' if mesmo_prazo else 'ato')}
    return None


@router.get('/intimacao/{intimacao_id}/fechamento')
async def fechamento_preview(intimacao_id: int, ato: str = '', titularidade: str = '', vencimento: str = '',
                             user: Usuario = Depends(usuario_atual)):
    from .. import motor_tarefas as MT
    it, m0 = _ficha_para_fechamento(intimacao_id)
    it = await _com_teor(it, m0)
    autos = await _consulta_autos(it, dict(m0, ato=ato or m0.get('ato')))
    m, notas, original = _aplicar_correcoes(m0, autos, ato, titularidade, vencimento, user.nome or user.email)
    m, notas = _despacho_composto(it, m, notas)
    cad = MT.cadeia_para(m, it, uf=_uf_da_intimacao(it))
    con = _cache_con()
    con.row_factory = sqlite3.Row
    try:
        ja = MT.ja_cadastrado(con, intimacao_id, it.get('processo'))
        gemea = _cadeia_gemea(con, intimacao_id, it.get('processo'), m)
    finally:
        con.close()
    if gemea and not (ja.get('tarefas') or []):
        notas = list(notas) + [gemea['motivo']]
        cad = dict(cad, familia='duplicada', etapas=[], pf=None, motivo_sem_cadeia=gemea['motivo'])
        ja = dict(ja, gemea=gemea)
    # Advogada, 07/11/2026: a tela escolhe quem faz na lista da equipe; aqui vai o que o motor quis dizer com o
    # nome escrito na ficha, e o prazo fatal de cada etapa (o processual, se a etapa nao tiver o seu)
    for _e in (cad.get('etapas') or []):
        if isinstance(_e, dict):
            _e.setdefault('para_ids', [r['id'] for r in MT._responsaveis_de(_e.get('para'))])
            if not _e.get('prazo_fatal') and m.get('vencimento'):
                _e['prazo_fatal'] = str(m.get('vencimento'))[:10]
    pf_existente = None
    if not ja.get('pf') and AG.disponivel():
        try:
            ev = await asyncio.to_thread(AG.achar_por_marcador, 'intim:%d' % intimacao_id)
            if ev:
                pf_existente = {'evento_id': ev.get('id'), 'link': ev.get('htmlLink'),
                                'titulo': ev.get('summary'),
                                'data': (ev.get('start') or {}).get('date')}
        except Exception as e:
            log.warning('pf existente %s: %s', intimacao_id, e)
    return {
        'intimacao': {'id': intimacao_id, 'processo': it.get('processo'), 'data': it.get('data'),
                      'tribunal': it.get('tribunal'), 'tratada': bool(it.get('tratada'))},
        'motor': {'ato': m.get('ato'), 'trilha': m.get('trilha'), 'titularidade': m.get('titularidade'),
                  'vencimento': m.get('vencimento'), 'prazo_fatal_interno': m.get('prazo_fatal_interno'),
                  'responsavel': m.get('responsavel'), 'cliente': m.get('cliente'),
                  'parte_adversa': m.get('parte_adversa'), 'confianca': m.get('confianca'),
                  'providencia': (m.get('conferencia') or {}).get('providencia'),
                  'audiencia': m.get('audiencia'),
                  'teor_autos': m.get('teor_autos'),
                  'sugestao_conclusao': m.get('sugestao_conclusao')},
        'contagem': _contagem(it, m),
        'autos': autos,
        'correcao': {'notas': notas, 'original': original},
        'atos': sorted(_atos_do_catalogo()),
        'cadeia': cad,
        'ja_cadastrado': ja,
        'pf_existente': ja.get('pf') or pf_existente,
        'agenda_disponivel': AG.disponivel(),
    }


@router.post('/intimacao/{intimacao_id}/fechamento')
async def fechamento(intimacao_id: int, dados: FechamentoIn,
                     user: Usuario = Depends(usuario_atual)):
    from .. import motor_tarefas as MT
    it, m0 = _ficha_para_fechamento(intimacao_id)
    it = await _com_teor(it, m0)
    por = user.nome or user.email
    autos = await _consulta_autos(it, dict(m0, ato=dados.ato or m0.get('ato')))
    m, notas, original = _aplicar_correcoes(m0, autos, dados.ato, dados.titularidade, dados.vencimento, por)
    m, notas = _despacho_composto(it, m, notas)
    cad = MT.cadeia_para(m, it, uf=_uf_da_intimacao(it))
    etapas = dados.etapas if dados.etapas is not None else cad['etapas']
    # gemea de uma cadeia ja aberta: nao abre tarefa nenhuma, so trata (a tela manda etapas vazias; aqui e a trava)
    if dados.etapas is None:
        con_g = _cache_con()
        con_g.row_factory = sqlite3.Row
        try:
            if _cadeia_gemea(con_g, intimacao_id, it.get('processo'), m):
                etapas = []
        finally:
            con_g.close()
    # A conexao SQLite tem de nascer e morrer NA MESMA THREAD em que grava: abrir aqui e
    # gravar em to_thread dava "SQLite objects created in a thread can only be used in that
    # same thread" (primeiro Cadastrar de verdade, Romildo 10001, 05/10/2026 20h50).
    def _gravar():
        con = _cache_con()
        con.row_factory = sqlite3.Row
        try:
            rel = MT.cadastrar(con, it, m, etapas, por, dados.criar_pf, AG if AG.disponivel() else None, _uf_da_intimacao(it), dados.observacao)
            # cada correcao e cada observacao da pessoa fica guardada: e o material de calibragem do motor
            if notas or (dados.observacao or '').strip() or any((e or {}).get('obs') for e in etapas):
                con.execute("""CREATE TABLE IF NOT EXISTS motor_correcoes (
                                 id INTEGER PRIMARY KEY AUTOINCREMENT, intimacao_id INTEGER, processo TEXT,
                                 ato_motor TEXT, ato_final TEXT, titularidade_motor TEXT, titularidade_final TEXT,
                                 vencimento_motor TEXT, vencimento_final TEXT, notas TEXT, observacao TEXT,
                                 obs_etapas TEXT, por TEXT, em REAL)""")
                con.execute('INSERT INTO motor_correcoes (intimacao_id, processo, ato_motor, ato_final, titularidade_motor, '
                            'titularidade_final, vencimento_motor, vencimento_final, notas, observacao, obs_etapas, por, em) '
                            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (intimacao_id, it.get('processo'), original['ato'], m.get('ato'), original['titularidade'],
                             m.get('titularidade'), original['vencimento'], m.get('vencimento'), ' | '.join(notas),
                             (dados.observacao or '')[:2000],
                             json.dumps([{'etapa': e.get('etapa'), 'tipo': e.get('tipo'), 'obs': e.get('obs')}
                                         for e in etapas if (e or {}).get('obs')], ensure_ascii=False), por, time.time()))
                con.commit()
            return rel
        finally:
            con.close()
    rel = await asyncio.to_thread(_gravar)
    # parceiro (Dra. Advogada hoje) nao entra no sistema: avisa por WhatsApp
    avisados = []
    try:
        from .tarefas import _parceiro_de, _avisar_parceiro
        for t in rel['tarefas']:
            for p in t.get('parceiros') or []:
                parc = _parceiro_de(p['id'])
                if parc:
                    ok, _info = _avisar_parceiro(parc, {'titulo': t['titulo'], 'processo_numero': it.get('processo'),
                                                        'cliente': m.get('cliente'), 'prazo_fatal': m.get('vencimento')}, por)
                    if ok:
                        avisados.append(parc['nome'])
    except Exception as e:
        rel['avisos'].append('aviso ao parceiro: %s' % str(e)[:120])
    tratada = False
    if dados.tratar:
        try:
            await asyncio.to_thread(_sync_tratar, intimacao_id)
            motivo = 'fechamento: ' + ', '.join('#%s %s' % (t['id'], t['tipo']) for t in rel['tarefas'])
            if rel.get('pf'):
                motivo += ' | PF ' + str(rel['pf'].get('evento_id'))
            if not rel['tarefas'] and cad.get('motivo_sem_cadeia'):
                motivo = 'fechamento sem tarefa: ' + cad['motivo_sem_cadeia'][:200]
            if notas:
                motivo += ' | ' + ' '.join(notas)[:200]
            _auditar(intimacao_id, user, 1, 'fechamento', (dados.motivo + ' ' if dados.motivo else '') + motivo)
            _cache_marca_tratadas([intimacao_id], True)
            tratada = True
        except HTTPException as e:
            rel['avisos'].append('tarefas criadas, mas o SYNC nao marcou a intimacao: %s' % e.detail)
    try:
        con = _cache_con()
        MT.historico(con, 'intimacao', intimacao_id, 'fechamento',
                     '%d tarefa(s), PF %s, tratada=%s%s' % (len(rel['tarefas']), (rel.get('pf') or {}).get('evento_id'),
                                                           tratada, (' | ' + ' '.join(notas)) if notas else ''), por)
        con.commit()
        con.close()
    except Exception as e:
        log.warning('historico do fechamento %s: %s', intimacao_id, e)
    rel['tratada'] = tratada
    rel['avisados'] = avisados
    rel['familia'] = cad.get('familia')
    rel['correcao'] = {'notas': notas, 'original': original}
    return rel

# ============================================================================
# POLOS — de que lado o escritorio esta, por processo.
#
# NAO usa advbox_titularidade.nosso_polo cru: esse campo erra em ~22% dos
# casos e a inferencia "RECLAMADA -> ativo" esta invertida na origem (cliente empresa,
# cliente RE, aparece como polo ativo). Enquanto a origem nao e corrigida, o
# polo aqui e derivado do campo tipo do ADVBOX, que e o rotulo do papel do NOSSO
# cliente e nao depende de inferencia.
#
# Quando os dois discordam a rota devolve conferir=True: a tela mostra
# "a conferir" em vez de afirmar um polo errado com cara de certeza.
# ============================================================================
_POLO_ROTULO = {'ativo': 'polo ativo', 'passivo': 'polo passivo'}


def _polo_do_tipo(tipo):
    t = (tipo or '').upper()
    if 'RECLAMADA' in t or 'RECLAMADO' in t or 'REU' in t.split():
        return 'passivo'
    if 'RECLAMANTE' in t or 'MOTORISTA' in t or 'AUTOR' in t or 'EXEQUENTE' in t:
        return 'ativo'
    return None


@router.get('/polos')
def polos(numeros: str = '', user: Usuario = Depends(usuario_atual)):
    """Papel do escritorio nos processos pedidos. Alimenta a sintese da tela
    de Intimacoes (de que lado estamos e se ha providencia nossa)."""
    alvo = [n.strip() for n in (numeros or '').split(',') if n.strip()]
    if not alvo:
        return {'polos': {}}
    alvo = alvo[:400]
    con = _cache_con()
    try:
        con.row_factory = sqlite3.Row
        marcas = ','.join('?' for _ in alvo)
        linhas = con.execute(
            'SELECT numero, tipo, nosso_polo, cliente, parte_adversa, pasta_drive, '
            'fase, etapa, responsavel, grupo FROM advbox_titularidade '
            'WHERE numero IN (' + marcas + ')', alvo).fetchall()
        out = {}
        for r in linhas:
            d = dict(r)
            do_tipo = _polo_do_tipo(d.get('tipo'))
            gravado = d.get('nosso_polo')
            polo = do_tipo or gravado
            out[d['numero']] = {
                'polo': polo,
                'polo_rotulo': _POLO_ROTULO.get(polo, 'polo a conferir'),
                'conferir': bool(do_tipo and gravado and do_tipo != gravado) or not polo,
                'tipo': d.get('tipo'),
                'cliente': d.get('cliente'),
                'parte_adversa': d.get('parte_adversa'),
                'pasta_drive': d.get('pasta_drive'),
                'fase': d.get('fase'),
                'etapa': d.get('etapa'),
                'responsavel': d.get('responsavel'),
            }
        return {'polos': out}
    finally:
        con.close()

# ============================================================================
# ATUALIZACAO DO CLIENTE
#
# Quando a providencia e so avisar o cliente, o botao da propria intimacao
# resolve: monta o texto, mostra para conferir e envia pelo WhatsApp do
# escritorio.
#
# COMO O DESTINATARIO E ENCONTRADO: processo -> jur_processo_partes ->
# jur_clientes. E vinculo por id, nao por nome. Casar por nome aqui seria
# incidente de LGPD esperando acontecer: no teste, um cliente casou com
# o telefone de outro so pelo sobrenome. Sem vinculo,
# a rota devolve pode_enviar=False e a tela pede o numero na mao.
#
# O texto NUNCA sai sozinho: o GET so rascunha, quem envia e o POST com o
# texto que a pessoa leu (e pode ter editado).
# ============================================================================
# `papel` guarda a ORIGEM do cadastro, nao o papel processual. So
# 'PARTE CONTRARIA' e adversario; ESCRITORIO, INDICACAO, ANUNCIO e
# CONTRATACAO dizem por onde o CLIENTE chegou. Ancorar no comeco
# evita casar CONTRATACAO dentro de CONTRARIA.
_PAPEL_ADVERSO = 'PARTE CONTR%'


# Titulos que nao servem de cumprimento sozinhos: "Dr. Thiago".split()[0] e "Dr."
_TRATAMENTOS = ('DR', 'DR.', 'DRA', 'DRA.', 'SR', 'SR.', 'SRA', 'SRA.')


def _tratamento(nome):
    """Tratamento + primeiro nome, do jeito que se fala com um colega."""
    partes = [p for p in str(nome or '').split() if p]
    if not partes:
        return 'Prezado'
    prim = partes[0]
    if prim.upper().rstrip('.') + '.' in _TRATAMENTOS or prim.upper() in _TRATAMENTOS:
        resto = partes[1] if len(partes) > 1 else ''
        return (prim + ' ' + resto).strip()
    return prim.capitalize()


def _primeiro_nome(nome):
    partes = [p for p in str(nome or '').replace('_', ' ').split() if len(p) > 1]
    return partes[0].capitalize() if partes else 'tudo bem'


def _intimacao_do_cache(iid):
    d = cache_get('intimacoes') or {}
    for it in (d.get('intimacoes') or []):
        if str(it.get('id')) == str(iid):
            return it
    return None


def _garantir_contato(con):
    """Com quem se fala em cada caso, quando nao e o proprio cliente.

    `processo_numero` vazio significa "vale para todos os processos deste
    cliente"; preenchido, vale so para aquele. O especifico ganha do geral.
    """
    # tipo: 'cliente_do_parceiro' = o cliente e do outro escritorio, nao se
    # fala com ele direto. 'parceria_conjunta' = o cliente e nosso e o parceiro
    # atua junto, normalmente em grupo. E a unica diferenca que muda o que se
    # pode apertar na tela.
    con.execute("""CREATE TABLE IF NOT EXISTS processo_contato (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     cliente_id INTEGER,
                     processo_numero TEXT,
                     nome TEXT NOT NULL,
                     telefone TEXT,
                     relacao TEXT,
                     tipo TEXT,
                     parceiro_id INTEGER,
                     observacao TEXT,
                     ativo INTEGER DEFAULT 1,
                     criado_por TEXT,
                     criado_em REAL)""")
    try:
        con.execute('ALTER TABLE processo_contato ADD COLUMN tipo TEXT')
    except Exception:
        pass   # ja existe


def _contato_do_processo(con, numero, cliente_id=None):
    """O contato cadastrado para o caso, se houver. Especifico vence geral."""
    try:
        _garantir_contato(con)
    except Exception as e:
        log.warning('processo_contato: %s', e)
        return None
    linhas = list(con.execute(
        'SELECT * FROM processo_contato WHERE ativo=1 AND ('
        '  processo_numero=? OR (cliente_id IS NOT NULL AND cliente_id=?))'
        ' ORDER BY CASE WHEN processo_numero=? THEN 0 ELSE 1 END',
        (numero or '', cliente_id or -1, numero or '')))
    for r in linhas:
        if (r['telefone'] or '').strip():
            tipo = (r['tipo'] if 'tipo' in r.keys() else None) or 'parceria_conjunta'
            return {'nome': r['nome'], 'telefone': r['telefone'],
                    'relacao': r['relacao'] or 'contato do caso',
                    'tipo': tipo,
                    'grupo': str(r['telefone']).endswith('-group'),
                    'observacao': r['observacao'] or ''}
    return None


def _cliente_do_processo(con, numero):
    """Cliente e telefone pelo vinculo real. None quando nao ha vinculo."""
    p = con.execute('SELECT id FROM jur_processos WHERE numero=?', (numero or '',)).fetchone()
    if not p:
        return None
    sql = ('SELECT cl.id, cl.nome, cl.celular, cl.telefone '
           'FROM jur_processo_partes pp JOIN jur_clientes cl ON cl.id = pp.cliente_id '
           'WHERE pp.processo_id=? AND pp.cliente_id IS NOT NULL '
           "AND UPPER(COALESCE(pp.papel,'')) NOT LIKE ?")
    candidatos = list(con.execute(sql, (p[0], _PAPEL_ADVERSO)))
    # Contato do caso vence o telefone do cliente: quando existe parceiro, e
    # com ele que se fala, mesmo que o cliente tenha numero no cadastro.
    for r in candidatos:
        via = _contato_do_processo(con, numero, r[0])
        if via:
            proprio = (r[2] or r[3] or '').strip()
            # Parceria conjunta: o cliente e nosso, entao ele continua sendo o
            # destinatario. O parceiro vira a alternativa. Grupo e excecao: se
            # existe grupo conjunto, e nele que a conversa acontece.
            if (via.get('tipo') == 'parceria_conjunta' and proprio
                    and not via.get('grupo')):
                return {'cliente_id': r[0], 'nome': r[1], 'telefone': proprio,
                        'alternativa': {'nome': via['nome'],
                                        'telefone': via['telefone'],
                                        'relacao': via.get('relacao') or 'o parceiro'},
                        'parceria': via,
                        'cliente_tem_numero': True}
            return {'cliente_id': r[0], 'nome': r[1], 'telefone': via['telefone'],
                    'via': via,
                    # O numero do cliente so vai como opcao quando a parceria
                    # permite falar com ele. Sendo cliente do parceiro, o numero
                    # existe no cadastro mas nao se usa: quem avisa e o parceiro.
                    'alternativa': ({'nome': r[1], 'telefone': proprio,
                                     'relacao': 'o próprio cliente'}
                                    if proprio and via.get('tipo') != 'cliente_do_parceiro'
                                    else None),
                    'cliente_tem_numero': bool(proprio)}
    for r in candidatos:
        tel = (r[2] or r[3] or '').strip()
        if tel:
            return {'cliente_id': r[0], 'nome': r[1], 'telefone': tel}
    # Sem cliente com telefone, ainda pode haver contato amarrado ao processo.
    via = _contato_do_processo(con, numero)
    if via:
        nome = candidatos[0][1] if candidatos else ''
        return {'cliente_id': candidatos[0][0] if candidatos else None,
                'nome': nome, 'telefone': via['telefone'], 'via': via}
    return None


def _so_digitos(t):
    d = ''.join(ch for ch in str(t or '') if ch.isdigit())
    if len(d) in (10, 11):
        d = '55' + d
    return d


def _limpa_para_cliente(t):
    """Tira travessao e aspas tortas do texto que vai para o cliente.

    Regra do escritorio: nada de travessao em texto de cliente. O resumo vem
    do tribunal cheio deles, entao vira virgula aqui.
    """
    t = str(t or '')
    for ruim, bom in (('\u2014', ','), ('\u2013', ','), ('\u2018', "'"),
                      ('\u2019', "'"), ('\u201c', '"'), ('\u201d', '"')):
        t = t.replace(ruim, bom)
    return ' '.join(t.split())


def _texto_atualizacao(it, nome_cliente, via=None):
    """Rascunho da mensagem para o cliente.

    Linguagem simples e sem travessao. NAO envia sozinho: quem le e edita e
    a equipe, no painel da intimacao.
    """
    m = it.get('motor') or {}
    proc = it.get('processo') or 'seu processo'
    resumo = _limpa_para_cliente(it.get('resumo'))
    if len(resumo) > 300:
        resumo = resumo[:297].rsplit(' ', 1)[0] + '.'
    venc = m.get('vencimento') or it.get('data_fatal')
    resp = m.get('responsavel')

    if via:
        # Escrever "Oi, Juvenal" para o telefone de um advogado parceiro seria
        # errado com os dois. Quem recebe e nomeado; de quem e o processo fica
        # dito na linha seguinte.
        linhas = ['%s, aqui é do Escritório Modelo Advogados.' % _tratamento(via.get('nome')), '',
                  'Segue atualização do processo de %s.'
                  % (str(nome_cliente or 'nosso cliente').title()), '']
    else:
        linhas = ['Oi, %s. Aqui é do Escritório Modelo Advogados.' % _primeiro_nome(nome_cliente), '']
    linhas.append('Passando para te atualizar sobre o processo %s.' % proc)
    if resumo:
        linhas += ['', resumo]
    if venc:
        linhas += ['', 'Já estamos cuidando disso. O prazo vai até %s%s.'
                   % (_fmt_data(venc),
                      (', e quem está preparando é a %s' % resp) if resp else '')]
        linhas.append('Te aviso assim que protocolar.')
    else:
        linhas += ['', 'Você não precisa fazer nada agora. Seguimos acompanhando '
                       'e te aviso assim que tiver novidade.']
    linhas += ['', 'Qualquer dúvida é só chamar por aqui.']
    return chr(10).join(linhas)


class AtualizacaoIn(BaseModel):
    texto: str = ''
    telefone: str = ''
    marcar_ciente: bool = False


@router.get('/intimacao/{intimacao_id}/atualizacao')
def rascunho_atualizacao(intimacao_id: int, user: Usuario = Depends(usuario_atual)):
    it = _intimacao_do_cache(intimacao_id)
    if not it:
        raise HTTPException(404, 'Intimacao nao esta no cache. Atualize a lista.')
    con = _cache_con()
    try:
        alvo = _cliente_do_processo(con, it.get('processo'))
    finally:
        con.close()
    nome = (alvo or {}).get('nome') or (it.get('motor') or {}).get('cliente') or ''
    return {
        'intimacao_id': intimacao_id,
        'processo': it.get('processo'),
        'cliente': nome,
        'telefone': (alvo or {}).get('telefone') or '',
        'pode_enviar': bool(alvo),
        'via': (alvo or {}).get('via'),
        'parceria': (alvo or {}).get('parceria'),
        'alternativa': (alvo or {}).get('alternativa'),
        'cliente_tem_numero': bool((alvo or {}).get('cliente_tem_numero')),
        'motivo': None if alvo else
                  'Processo sem cliente com telefone ligado no cadastro. '
                  'Informe o numero para enviar.',
        'texto': _texto_atualizacao(it, nome, (alvo or {}).get('via')),
    }


@router.post('/intimacao/{intimacao_id}/atualizacao')
async def enviar_atualizacao(intimacao_id: int, dados: AtualizacaoIn,
                             user: Usuario = Depends(usuario_atual)):
    it = _intimacao_do_cache(intimacao_id)
    if not it:
        raise HTTPException(404, 'Intimacao nao esta no cache. Atualize a lista.')
    espelhou = True
    texto = (dados.texto or '').strip()
    if len(texto) < 15:
        raise HTTPException(400, 'Texto muito curto para mandar ao cliente')

    con = _cache_con()
    try:
        alvo = _cliente_do_processo(con, it.get('processo'))
        tel = _so_digitos(dados.telefone or (alvo or {}).get('telefone'))
        if len(tel) < 12:
            raise HTTPException(400, 'Telefone invalido ou ausente')
        numero = _wa_numero(con)
        if not numero:
            raise HTTPException(503, 'WhatsApp do escritorio nao configurado')

        ok, info = await asyncio.to_thread(zapi.enviar_texto, numero, tel, texto)
        if not ok:
            raise HTTPException(502, 'WhatsApp recusou: %s' % str(info)[:140])

        # espelha na conversa do Chat ao Vivo, se o telefone ja tiver uma.
        # criado_em e TEXTO COM ESPACO: com 'T' a mensagem trava no topo.
        agora = time.strftime('%Y-%m-%d %H:%M:%S')
        conv = con.execute('SELECT id FROM wa_conversas WHERE contato_telefone=?',
                           (tel,)).fetchone()
        # ESPELHO BEST-EFFORT: a mensagem JA SAIU no WhatsApp. Se o registro na
        # conversa falhar, nao pode derrubar a rota - quem clicasse veria erro
        # e reenviaria, mandando duplicado ao cliente. Foi o que aconteceu em
        # 03/09: midia_url e NOT NULL e nao estava sendo preenchida.
        if conv:
            try:
                con.execute(
                    'INSERT INTO wa_mensagens (conversa_id, origem, autor, tipo, '
                    'conteudo, midia_url, criado_em, remote_id) VALUES (?,?,?,?,?,?,?,?)',
                    (conv[0], 'atendente', 'controladoria:' + (user.nome or user.email),
                     'texto', texto, '', agora, str(info or '')))
                con.execute('UPDATE wa_conversas SET ultima_mensagem=?, atualizado_em=? '
                            'WHERE id=?', (texto[:180], agora, conv[0]))
                con.commit()
            except Exception as e:
                log.warning('espelho da atualizacao na conversa: %s', e)
                espelhou = False
    finally:
        con.close()

    if dados.marcar_ciente:
        try:
            await asyncio.to_thread(_sync_tratar, intimacao_id)
            _auditar(intimacao_id, user, 1, 'ciente-por-atualizacao',
                     'cliente avisado por WhatsApp')
            _cache_marca_tratadas([intimacao_id], True)
        except Exception as e:
            log.warning('ciente apos atualizacao: %s', e)

    return {'ok': True, 'telefone': tel, 'message_id': str(info or ''),
            'na_conversa': bool(conv) and espelhou}

# ============================================================================
# EVENTO DA AGENDA — criar, ver, editar, cumprir e cancelar.
#
# Estas rotas se perderam junto com o controladoria.py; o app/agenda_google.py
# sobreviveu inteiro. Aqui e so a ponte entre a tela e ele.
#
# A agenda e o Google Calendar do escritorio (token_esc_calendar.json). Nao e
# Outlook: o Outlook e onde a Advogada espelha os PF dela, por fora.
# ============================================================================
from typing import Optional

from .. import agenda_google as AG


class EventoIn(BaseModel):
    titulo: str = ''
    peca: str = ''
    cliente: str = ''
    parte_adversa: str = ''
    processo: str = ''
    data: str = ''
    hora: str = ''
    local: str = ''
    descricao: str = ''
    prazo_fatal: bool = True
    convidados: list = []
    intimacao_id: Optional[int] = None


class EventoPatch(BaseModel):
    titulo: Optional[str] = None
    data: Optional[str] = None
    hora: Optional[str] = None
    local: Optional[str] = None
    descricao: Optional[str] = None
    convidados: Optional[list] = None


def _titulo_do_evento(d):
    """Mesmo padrao que a tela mostra na previa, para nao haver surpresa."""
    if (d.titulo or '').strip():
        return d.titulo.strip()
    peca = (d.peca or 'PEÇA').upper()
    partes = ' X '.join([d.cliente or 'CLIENTE', d.parte_adversa or 'PARTE ADVERSA'])
    if d.prazo_fatal:
        return AG.titulo_pf(peca, d.cliente or 'CLIENTE',
                            d.parte_adversa or 'PARTE ADVERSA', d.processo or '')
    return '_'.join(x for x in [peca, partes, d.processo] if x)


@router.post('/agenda/evento')
async def criar_evento_agenda(dados: EventoIn, user: Usuario = Depends(usuario_atual)):
    if not (dados.data or '').strip():
        raise HTTPException(400, 'informe a data')
    if not AG.disponivel():
        raise HTTPException(503, 'Agenda do Google indisponivel (token expirado). '
                                 'Renovar com --auth numa maquina com navegador.')
    titulo = _titulo_do_evento(dados)
    # marcador evita evento duplicado no duplo clique e liga o evento a intimacao
    marcador = ('intim:%d' % dados.intimacao_id) if dados.intimacao_id else None
    if marcador:
        try:
            ja = await asyncio.to_thread(AG.achar_por_marcador, marcador)
            if ja:
                return {'ok': True, 'ja_existia': True, 'evento_id': ja.get('id'),
                        'link': ja.get('htmlLink'), 'titulo': ja.get('summary')}
        except Exception as e:
            log.warning('achar_por_marcador: %s', e)

    convidados = dados.convidados or None
    try:
        if (dados.hora or '').strip():
            ev = await asyncio.to_thread(
                AG.criar_audiencia, titulo, dados.data, dados.hora, 90,
                dados.descricao or '', convidados, None, marcador,
                dados.local or '', dados.processo or '')
        else:
            ev = await asyncio.to_thread(
                AG.criar_prazo, titulo, dados.data, dados.descricao or '',
                convidados, None, marcador, dados.local or '',
                dados.processo or '')
    except Exception as e:
        raise HTTPException(502, 'Google recusou: %s' % str(e)[:160])

    # registro local, para auditoria de quem cadastrou o que
    try:
        con = _cache_con()
        con.execute('INSERT OR REPLACE INTO sync_intim_agenda'
                    '(id,evento_id,link,data,titulo,por,espelho_outlook,criado_em) '
                    'VALUES(?,?,?,?,?,?,?,?)',
                    (dados.intimacao_id or 0, ev.get('id'), ev.get('htmlLink'),
                     dados.data[:10], titulo, user.nome or user.email, 0, time.time()))
        con.commit()
        con.close()
    except Exception as e:
        log.warning('registro local do evento: %s', e)

    return {'ok': True, 'evento_id': ev.get('id'), 'link': ev.get('htmlLink'),
            'titulo': titulo, 'data': dados.data[:10], 'hora': dados.hora or None}


@router.get('/agenda/evento/{evento_id}')
async def ver_evento_agenda(evento_id: str, user: Usuario = Depends(usuario_atual)):
    try:
        d = await asyncio.to_thread(AG.detalhe, evento_id)
    except Exception as e:
        raise HTTPException(404, 'Evento nao encontrado: %s' % str(e)[:140])
    if d.get('cancelado'):
        raise HTTPException(404, 'Esse evento foi cancelado')
    return d


@router.patch('/agenda/evento/{evento_id}')
async def editar_evento_agenda(evento_id: str, dados: EventoPatch,
                               user: Usuario = Depends(usuario_atual)):
    try:
        await asyncio.to_thread(AG.atualizar, evento_id, dados.titulo, dados.data,
                                dados.hora, 90, dados.descricao, dados.convidados,
                                dados.local)
        return await asyncio.to_thread(AG.detalhe, evento_id)
    except Exception as e:
        raise HTTPException(502, 'Nao consegui editar: %s' % str(e)[:160])


@router.post('/agenda/evento/{evento_id}/cumprido')
async def cumprir_evento_agenda(evento_id: str, cumprido: bool = True,
                                user: Usuario = Depends(usuario_atual)):
    try:
        await asyncio.to_thread(AG.marcar_cumprido, evento_id, cumprido)
        return {'ok': True, 'cumprido': cumprido}
    except Exception as e:
        raise HTTPException(502, 'Nao consegui marcar: %s' % str(e)[:160])


@router.delete('/agenda/evento/{evento_id}')
async def cancelar_evento_agenda(evento_id: str, user: Usuario = Depends(usuario_atual)):
    try:
        await asyncio.to_thread(AG.cancelar, evento_id)
    except Exception as e:
        raise HTTPException(502, 'Nao consegui cancelar: %s' % str(e)[:160])
    try:
        con = _cache_con()
        con.execute('DELETE FROM sync_intim_agenda WHERE evento_id=?', (evento_id,))
        con.commit()
        con.close()
    except Exception as e:
        log.warning('limpeza do registro local: %s', e)
    return {'ok': True, 'cancelado': True}

# ============================================================================
# COMPROMISSO DESIGNADO NA INTIMACAO
#
# O motor classifica o ATO; nao diz QUANDO nem ONDE o compromisso acontece.
# Esses dados estao no corpo do despacho: data, hora, modalidade, link da
# sala virtual e endereco do forum. E o que a agenda e o lembrete do cliente
# precisam.
#
# LE O TEOR COMPLETO, nao o da lista. A lista traz um preview de ~600
# caracteres terminado em reticencias; o endereco e o link costumam ficar
# depois do corte, e endereco cortado indo para cliente e acidente.
# ============================================================================
from ..controladoria_motor import compromisso as CP


@router.get('/intimacao/{intimacao_id}/compromisso')
async def compromisso_da_intimacao(intimacao_id: int,
                                   user: Usuario = Depends(usuario_atual)):
    it = _intimacao_do_cache(intimacao_id) or {}
    try:
        det = await asyncio.to_thread(_get, f'/intimacoes/{int(intimacao_id)}')
    except Exception as e:
        log.warning('teor completo da intimacao %s: %s', intimacao_id, e)
        det = {}
    texto = det.get('texto') or it.get('texto') or ''
    orgao = det.get('orgao') or it.get('orgao') or ''
    resumo = det.get('resumo') or it.get('resumo') or ''

    r = CP.extrair(texto, orgao, resumo)
    r['intimacao_id'] = intimacao_id
    r['processo'] = it.get('processo') or det.get('processo') or ''
    r['tribunal'] = it.get('tribunal') or ''
    if not r.get('tem'):
        return r

    # titulo no padrao do escritorio, para a agenda
    con = _cache_con()
    try:
        con.row_factory = sqlite3.Row
        linha = con.execute(
            'SELECT cliente, parte_adversa FROM advbox_titularidade WHERE numero=?',
            (r['processo'],)).fetchone()
    finally:
        con.close()
    mot = it.get('motor') or {}
    cliente = (linha['cliente'] if linha else None) or mot.get('cliente') or ''
    adversa = (linha['parte_adversa'] if linha else None) or mot.get('parte_adversa') or ''
    if r['tipo'] == 'julgamento':
        ato, especie = 'SESSAO DE JULGAMENTO', ''
    elif r['tipo'] == 'pericia':
        ato, especie = 'PERICIA', r.get('especie') or ''
    else:
        especie = r.get('especie') or ''
        ato = ('AUDIENCIA ' + especie).strip()

    r['cliente'] = cliente
    r['parte_adversa'] = adversa
    r['titulo'] = AG.titulo_pf(ato, cliente or 'CLIENTE',
                               adversa or 'PARTE ADVERSA', r['processo'])
    partes = ['%s %s' % (r['tipo'].upper(), especie)]
    if r.get('modalidade'):
        partes.append('Modalidade: ' + r['modalidade'])
    if r.get('link'):
        partes.append('Link: ' + r['link'])
    if r.get('endereco'):
        partes.append('Endereço: ' + r['endereco'])
    if r.get('orgao'):
        partes.append('Órgão: ' + r['orgao'])
    partes.append('Processo: ' + (r['processo'] or ''))
    r['descricao'] = chr(10).join(partes)
    return r

# ============================================================================
# DUVIDA SOBRE O DESPACHO
#
# Quem trata a publicacao no dia a dia e a Advogada, e duvida sobre o teor e o
# que mais atrasa. A caixa responde a partir do TEOR COMPLETO da intimacao.
#
# TRES TRAVAS, e elas sao o motivo de existir esta rota em vez de mandar a
# pessoa para um chat generico:
#   1. so responde do texto. Nao sabe de nada fora dele e diz isso.
#   2. cita o TRECHO que sustenta a resposta, para dar para conferir.
#   3. NAO calcula prazo nem data. A autoridade de prazo e o motor
#      deterministico; duas datas diferentes na mesma tela seria pior que
#      nenhuma.
# ============================================================================
_SISTEMA_DUVIDA = """Voce ajuda a controladoria de um escritorio trabalhista a
entender o TEOR de uma publicacao judicial. Quem pergunta e a pessoa que trata
a publicacao.

REGRAS ABSOLUTAS:
1. Responda SOMENTE com o que esta no texto da publicacao fornecida. Voce nao
   tem acesso aos autos, ao processo, nem a nada alem deste texto.
2. Se a resposta nao estiver no texto, diga exatamente: "Isso nao consta neste
   despacho." e, se ajudar, diga onde a pessoa provavelmente acharia (autos,
   sistema do tribunal, contato com a vara). Nunca preencha lacuna com
   suposicao.
3. Sempre que afirmar algo, CITE entre aspas o trecho do texto que sustenta a
   afirmacao. Sem trecho, nao afirme.
4. NAO CALCULE PRAZO, DATA DE VENCIMENTO NEM CONTAGEM DE DIAS. Quem calcula
   prazo neste sistema e o motor deterministico, que ja mostra o resultado na
   tela. Se perguntarem de prazo, diga qual prazo o TEXTO menciona (se
   mencionar) e mande conferir a projecao da tela.
5. Nao invente numero de artigo, sumula ou jurisprudencia. Cite apenas o que
   estiver escrito no texto.
6. Responda em portugues do Brasil, direto e curto. No maximo 6 linhas.
   Linguagem simples: quem le e estagiaria, nao precisa de latim."""


class PerguntaIn(BaseModel):
    pergunta: str = ''


@router.post('/intimacao/{intimacao_id}/pergunta')
async def perguntar_intimacao(intimacao_id: int, dados: PerguntaIn,
                              user: Usuario = Depends(usuario_atual)):
    p = (dados.pergunta or '').strip()
    if len(p) < 3:
        raise HTTPException(400, 'Escreva a pergunta')
    if len(p) > 600:
        raise HTTPException(400, 'Pergunta muito longa')

    it = _intimacao_do_cache(intimacao_id) or {}
    try:
        det = await asyncio.to_thread(_get, f'/intimacoes/{int(intimacao_id)}')
    except Exception as e:
        log.warning('teor para duvida %s: %s', intimacao_id, e)
        det = {}
    texto = (det.get('texto') or it.get('texto') or '').strip()
    if not texto:
        raise HTTPException(404, 'Nao consegui ler o teor desta publicacao')
    texto = texto[:14000]

    from ..portal.resumo import _chamar_ia
    contexto = ('PUBLICACAO (teor completo)\n'
                'Processo: %s\nTribunal: %s\nOrgao: %s\n\n%s\n\n'
                'PERGUNTA DE QUEM ESTA TRATANDO: %s'
                % (it.get('processo') or det.get('processo') or '-',
                   it.get('tribunal') or '-',
                   it.get('orgao') or det.get('orgao') or '-', texto, p))

    resposta, uso = await asyncio.to_thread(
        _chamar_ia, _SISTEMA_DUVIDA, contexto, 700)
    if not resposta:
        raise HTTPException(503, 'A IA nao respondeu agora. Tente de novo em um minuto.')

    # registro para auditoria: da para ver depois o que foi perguntado e
    # respondido sobre cada publicacao
    try:
        con = _cache_con()
        con.execute('CREATE TABLE IF NOT EXISTS intim_duvidas ('
                    'id INTEGER PRIMARY KEY AUTOINCREMENT, intimacao_id INTEGER, '
                    'pergunta TEXT, resposta TEXT, por TEXT, criado_em TEXT)')
        con.execute('INSERT INTO intim_duvidas (intimacao_id, pergunta, resposta, '
                    'por, criado_em) VALUES (?,?,?,?,?)',
                    (intimacao_id, p, resposta, user.nome or user.email,
                     time.strftime('%Y-%m-%d %H:%M:%S')))
        con.commit()
        con.close()
    except Exception as e:
        log.warning('registro da duvida: %s', e)

    return {'ok': True, 'pergunta': p, 'resposta': resposta,
            'tokens': getattr(uso, 'output_tokens', None)}


@router.get('/intimacao/{intimacao_id}/duvidas')
def duvidas_da_intimacao(intimacao_id: int, user: Usuario = Depends(usuario_atual)):
    """Historico de perguntas ja feitas sobre esta publicacao."""
    con = _cache_con()
    try:
        con.execute('CREATE TABLE IF NOT EXISTS intim_duvidas ('
                    'id INTEGER PRIMARY KEY AUTOINCREMENT, intimacao_id INTEGER, '
                    'pergunta TEXT, resposta TEXT, por TEXT, criado_em TEXT)')
        con.row_factory = sqlite3.Row
        linhas = con.execute(
            'SELECT pergunta, resposta, por, criado_em FROM intim_duvidas '
            'WHERE intimacao_id=? ORDER BY id', (intimacao_id,)).fetchall()
        return {'duvidas': [dict(r) for r in linhas]}
    finally:
        con.close()


# ============================================================================
# JANELA DO PROCESSO (Advogada, audios de 07/11/2026)
#
# Clicar no numero do processo na intimacao abre o processo com o historico de
# tarefas, as intimacoes e o financeiro (quanto foi cobrado e recebido), como o
# ADVBOX mostra. So leitura: tarefas e financeiro do banco do sistema,
# intimacoes do SYNC (todas, tratadas ou nao).
# ============================================================================
@router.get('/processo-resumo')
async def processo_resumo(numero: str = '', user: Usuario = Depends(usuario_atual)):
    num = (numero or '').strip()
    if not num:
        raise HTTPException(400, 'informe o numero do processo')
    hoje = time.strftime('%Y-%m-%d')

    def _local():
        con = _cache_con()
        con.row_factory = sqlite3.Row
        try:
            p = con.execute('SELECT id, advbox_id FROM jur_processos WHERE numero=? '
                            'ORDER BY encerrado, id DESC LIMIT 1', (num,)).fetchone()
            tarefas = [dict(r) for r in con.execute(
                "SELECT id, tipo, titulo, status, data, prazo_fatal, origem, concluida_em FROM tarefas "
                "WHERE processo_numero=? AND COALESCE(status,'') != 'cancelada' "
                "ORDER BY CASE WHEN status='aberta' THEN 0 ELSE 1 END, COALESCE(data,'') DESC, id DESC LIMIT 400",
                (num,))]
            if tarefas:
                ids = [t['id'] for t in tarefas]
                resp = {}
                for r in con.execute('SELECT tarefa_id, nome FROM tarefa_responsaveis WHERE tarefa_id IN (%s) ORDER BY id'
                                     % ','.join('?' * len(ids)), ids):
                    resp.setdefault(r['tarefa_id'], []).append(r['nome'])
                for t in tarefas:
                    t['responsaveis'] = resp.get(t['id'], [])
                    ref = (t.get('prazo_fatal') or t.get('data') or '')[:10]
                    t['atrasada'] = t['status'] == 'aberta' and bool(ref) and ref < hoje
            cond, args = 'processo_numero=?', [num]
            if p and p['advbox_id']:
                cond += ' OR lawsuit_id=?'
                args.append(p['advbox_id'])
            lanc = [dict(r) for r in con.execute(
                'SELECT id, tipo, descricao, valor_cent, vencimento, pagamento, pessoa_nome FROM fin_lancamentos '
                'WHERE (' + cond + ') AND COALESCE(excluido,0)=0 AND COALESCE(removido_advbox,0)=0 '
                'ORDER BY COALESCE(vencimento,\'\') DESC, id DESC', args)]
            rec = {'cobrado': 0, 'recebido': 0, 'em_aberto': 0, 'atrasado': 0}
            desp = {'total': 0, 'pago': 0}
            for l in lanc:
                v = (l.pop('valor_cent') or 0) / 100.0
                l['valor'] = v
                if l['tipo'] == 'receita':
                    rec['cobrado'] += v
                    if l.get('pagamento'):
                        rec['recebido'] += v
                    else:
                        rec['em_aberto'] += v
                        if (l.get('vencimento') or '9999') < hoje:
                            rec['atrasado'] += v
                else:
                    desp['total'] += v
                    if l.get('pagamento'):
                        desp['pago'] += v
            return {'processo_id': p['id'] if p else None, 'tarefas': tarefas,
                    'financeiro': {'receitas': {k: round(v, 2) for k, v in rec.items()},
                                   'despesas': {k: round(v, 2) for k, v in desp.items()},
                                   'lancamentos': lanc[:500]}}
        finally:
            con.close()

    out = await asyncio.to_thread(_local)
    try:
        lista = await asyncio.to_thread(_get_all, '/intimacoes', 'intimacoes', {'status': 'todas', 'processo': num}, 400)
        if isinstance(lista, dict):
            lista = lista.get('intimacoes') or []
        out['intimacoes'] = sorted(
            [{'id': x.get('id'), 'data': str(x.get('data_publicacao') or x.get('data') or '')[:10],
              'tipo_ato': x.get('tipo_ato') or x.get('tipo_comunicacao'), 'orgao': x.get('orgao'),
              'resumo': str(x.get('resumo') or x.get('texto') or '')[:260], 'tratada': bool(x.get('tratada')),
              'data_fatal': str(x.get('data_fatal') or '')[:10] or None}
             for x in lista if str(x.get('processo') or '') == num],
            key=lambda x: x['data'] or '', reverse=True)
    except Exception as e:
        out['intimacoes'] = [{'id': x.get('id'), 'data': str(x.get('data') or '')[:10], 'tipo_ato': x.get('tipo_ato'),
                              'resumo': str(x.get('resumo') or '')[:260], 'tratada': bool(x.get('tratada')),
                              'data_fatal': str(x.get('data_fatal') or '')[:10] or None}
                             for x in ((cache_get('intimacoes') or {}).get('intimacoes') or [])
                             if str(x.get('processo') or '') == num]
        out['intimacoes_erro'] = 'SYNC fora do ar (%s); mostrando so as pendentes' % str(e)[:80]
    return out
