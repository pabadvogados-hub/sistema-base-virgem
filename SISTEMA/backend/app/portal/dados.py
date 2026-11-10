""
import json
import os
import re
import sqlite3
import time
import unicodedata

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, 'sistema_esc.db')


def _con():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def so_digitos(v: str) -> str:
    return re.sub(r'\D', '', v or '')


def normalizar_nome(v: str) -> str:
    ""
    v = (v or '').split('_')[0]
    v = unicodedata.normalize('NFKD', v)
    v = ''.join(c for c in v if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', v).upper().strip()


def cpf_valido(cpf: str) -> bool:
    ""
    c = so_digitos(cpf)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for tam in (9, 10):
        soma = sum(int(c[i]) * (tam + 1 - i) for i in range(tam))
        dig = (soma * 10) % 11
        dig = 0 if dig == 10 else dig
        if dig != int(c[tam]):
            return False
    return True


def cnpj_valido(cnpj: str) -> bool:
    ""
    c = so_digitos(cnpj)
    if len(c) != 14 or c == c[0] * 14:
        return False
    for tam in (12, 13):
        pesos = list(range(tam - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(int(c[i]) * pesos[i] for i in range(tam))
        dig = soma % 11
        dig = 0 if dig < 2 else 11 - dig
        if dig != int(c[tam]):
            return False
    return True


def documento_valido(doc: str) -> bool:
    ""
    c = so_digitos(doc)
    return cpf_valido(c) if len(c) == 11 else cnpj_valido(c)


def _sync_cfg():
    from ..config import settings
    base = (getattr(settings, 'SYNC_API_BASE', '') or 'https://sync.atendedireito.app/v1').rstrip('/')
    token = getattr(settings, 'SYNC_API_TOKEN', '') or ''
    return base, token


def sync_get(path: str, params: dict = None, texto: bool = False):
    base, token = _sync_cfg()
    if not token:
        raise RuntimeError('SYNC nao configurado')
    r = requests.get(f'{base}{path}', headers={'Authorization': f'Bearer {token}'},
                     params=params or {}, timeout=45)
    if r.status_code >= 400:
        raise RuntimeError(f'SYNC {r.status_code}: {r.text[:180]}')
    if texto:
        return r.text
    try:
        return r.json()
    except Exception:
        return {'texto': r.text}


def cache_get(chave: str):
    ""
    try:
        with _con() as con:
            row = con.execute('SELECT dados FROM sync_cache WHERE chave=?', (chave,)).fetchone()
        return json.loads(row['dados']) if row else None
    except Exception:
        return None


def _e_rotulo_de_lead(nome) -> bool:
    ""
    n = (nome or '').upper()
    return ('_LEAD' in n or ' LEAD ' in n
            or n.startswith('LEAD ') or n.endswith(' LEAD'))


def _melhor_cadastro(con, linhas):
    ""
    if len(linhas) == 1:
        return linhas[0]

    def nota(r):
        pontos = 0
        if not _e_rotulo_de_lead(r['nome']):
            pontos += 8
        try:
            tem = con.execute('SELECT COUNT(*) FROM portal_contato '
                              'WHERE cliente_id=? AND ativo=1',
                              (r['id'],)).fetchone()[0]
        except Exception:
            tem = 0
        if tem:
            pontos += 4
        if (r['email'] or '').strip():
            pontos += 1
        if ((r['celular'] or '') or (r['telefone'] or '')).strip():
            pontos += 1
        return (pontos, -r['id'])

    return sorted(linhas, key=nota, reverse=True)[0]


def resolver_cliente(documento: str):
    ""
    c = so_digitos(documento)
    if len(c) not in (11, 14):
        return None
    with _con() as con:
        candidatos = []
        for row in con.execute(
                "SELECT id, nome, documento, email, celular, telefone FROM jur_clientes "
                "WHERE documento IS NOT NULL AND TRIM(documento) <> ''"):
            if so_digitos(row['documento']) == c:
                candidatos.append(row)
        if not candidatos:
            return None
        alvo = _melhor_cadastro(con, candidatos)
        ov = con.execute('SELECT celular_portal, email_portal FROM portal_acesso '
                         'WHERE cliente_id=?', (alvo['id'],)).fetchone()
    cel = so_digitos((ov['celular_portal'] if ov else '') or alvo['celular'] or alvo['telefone'] or '')
    mail = (((ov['email_portal'] if ov else '') or alvo['email'] or '').strip().lower())
    return {'id': alvo['id'], 'nome': alvo['nome'], 'cpf': c, 'documento': c,
            'email': mail, 'celular': cel}


def contatos_do_cliente(cliente: dict) -> list:
    ""
    saida = []
    with _con() as con:
        for r in con.execute(
                'SELECT id, nome, celular, email, titular, relacao FROM portal_contato '
                'WHERE cliente_id=? AND ativo=1 ORDER BY titular DESC, id',
                (cliente['id'],)):
            cel = so_digitos(r['celular'] or '')
            if cel:
                saida.append({'id': r['id'], 'nome': r['nome'] or 'Contato',
                              'celular': cel, 'email': (r['email'] or '').strip().lower(),
                              'titular': bool(r['titular']),
                              'relacao': r['relacao'] or ''})
    tem_titular = any(c['titular'] for c in saida)
    if not tem_titular and cliente.get('celular'):
        ja = {c['celular'] for c in saida}
        if cliente['celular'] not in ja:
            saida.insert(0, {'id': 0, 'nome': 'Contato do cadastro',
                             'celular': cliente['celular'],
                             'email': cliente.get('email') or '',
                             'titular': True, 'relacao': ''})
    return saida


def add_contato(cliente_id: int, nome: str, celular: str, email: str = '',
                titular: bool = True, relacao: str = '', autorizado_por: str = '') -> int:
    ""
    cel = so_digitos(celular)
    if len(cel) < 10:
        raise ValueError('celular invalido')
    agora = time.time()
    with _con() as con:
        ja = con.execute('SELECT id FROM portal_contato WHERE cliente_id=? AND celular=?',
                         (cliente_id, cel)).fetchone()
        if ja:
            con.execute('UPDATE portal_contato SET ativo=1, nome=?, email=?, titular=?, '
                        'relacao=?, autorizado_por=?, autorizado_em=? WHERE id=?',
                        (nome, (email or '').strip().lower(), 1 if titular else 0,
                         relacao, autorizado_por, agora, ja['id']))
            con.commit()
            return ja['id']
        cur = con.execute(
            'INSERT INTO portal_contato(cliente_id,nome,celular,email,ativo,criado_em,'
            'titular,relacao,autorizado_por,autorizado_em) VALUES(?,?,?,?,1,?,?,?,?,?)',
            (cliente_id, nome, cel, (email or '').strip().lower(), agora,
             1 if titular else 0, relacao, autorizado_por, agora))
        con.commit()
        return cur.lastrowid


def remover_contato(cliente_id: int, contato_id: int) -> bool:
    ""
    with _con() as con:
        cur = con.execute('UPDATE portal_contato SET ativo=0 WHERE id=? AND cliente_id=?',
                          (contato_id, cliente_id))
        con.commit()
        return cur.rowcount > 0


def whatsapp_escritorio() -> str:
    ""
    try:
        with _con() as con:
            r = con.execute('SELECT telefone FROM wa_numeros WHERE ativo=1 '
                            'ORDER BY id LIMIT 1').fetchone()
        return so_digitos(r['telefone']) if r else ''
    except Exception:
        return ''


def definir_contato_portal(cliente_id: int, celular: str = None, email: str = None):
    ""
    agora = time.time()
    with _con() as con:
        con.execute('INSERT INTO portal_acesso(cliente_id, criado_em) VALUES(?,?) '
                    'ON CONFLICT(cliente_id) DO NOTHING', (cliente_id, agora))
        if celular is not None:
            con.execute('UPDATE portal_acesso SET celular_portal=? WHERE cliente_id=?',
                        (so_digitos(celular), cliente_id))
        if email is not None:
            con.execute('UPDATE portal_acesso SET email_portal=? WHERE cliente_id=?',
                        (email.strip().lower(), cliente_id))
        con.commit()


def _titularidade_indexada():
    ""
    out = {}
    with _con() as con:
        for row in con.execute(
                "SELECT numero, cliente, clientes, adversos, etapa, fase, pasta_drive, "
                "responsavel, titularidade, nosso_polo FROM advbox_titularidade"):
            if (row['titularidade'] or '').upper() != 'NOSSO':
                continue
            out[(row['numero'] or '').strip()] = dict(row)
    return out


_RE_PASTA_DO_CASO = re.compile(r'PASTA DO CASO\s*:\s*(\S+)', re.I)
_PASTAS_ADVBOX = {'quando': 0, 'mapa': {}}


def link_de_pasta(url) -> str:
    ""
    u = (url or '').strip().rstrip('.,;')
    if re.fullmatch(r'[A-Za-z0-9_-]{20,}', u):
        return u
    return u if 'drive.google.com' in u and extrair_folder_id(u) else ''


def _pastas_do_advbox():
    ""
    agora = time.time()
    if agora - _PASTAS_ADVBOX['quando'] > 60:
        mapa = {}
        with _con() as con:
            for num, notas in con.execute(
                    "SELECT numero, notas FROM jur_processos "
                    "WHERE notas LIKE '%PASTA DO CASO%'"):
                m = _RE_PASTA_DO_CASO.search(notas or '')
                link = link_de_pasta(m.group(1)) if m else ''
                if link and (num or '').strip():
                    mapa[num.strip()] = link
        _PASTAS_ADVBOX['mapa'] = mapa
        _PASTAS_ADVBOX['quando'] = agora
    return _PASTAS_ADVBOX['mapa']


def _nomes_originais(campo):
    ""
    try:
        v = json.loads(campo) if campo else []
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
    except Exception:
        return []


def _nomes_lista(campo):
    try:
        v = json.loads(campo) if campo else []
        return [normalizar_nome(x) for x in v] if isinstance(v, list) else []
    except Exception:
        return []


def processos_do_advbox(cliente: dict) -> dict:
    ""
    out = {}
    with _con() as con:
        for r in con.execute(
                'SELECT p.numero, p.fase AS etapa_detalhada, p.etapa AS grupo, '
                'p.responsavel, p.tipo, p.area, p.encerrado, pt.papel '
                'FROM jur_processo_partes pt '
                'JOIN jur_processos p ON p.id = pt.processo_id '
                'WHERE pt.cliente_id = ?', (cliente['id'],)):
            if 'CONTR' in (r['papel'] or '').upper():
                continue
            num = (r['numero'] or '').strip()
            if num:
                out[num] = dict(r)
    return out


def casos_gr_do_cliente(cliente_id) -> list:
    ""
    return []


ESCRITORIO_MARCAS = ('SOCIEDADE INDIVIDUAL DE ADVOCACIA',
                     'ESCRITÓRIO MODELO ADVOGADOS')


def _e_o_escritorio(nome) -> bool:
    n = normalizar_nome(nome or '')
    return any(m in n for m in ESCRITORIO_MARCAS)


def _cliente_e_o_escritorio(nome_cli) -> bool:
    ""
    return any(m in (nome_cli or '') for m in ESCRITORIO_MARCAS)


def _processo_do_escritorio(t) -> bool:
    ""
    return any(_e_o_escritorio(n) for n in _nomes_lista(t.get('clientes')))


_CAPAS = {'quando': 0, 'mapa': {}}


def capas_por_num(num):
    ""
    agora = time.time()
    if agora - _CAPAS['quando'] > 60:
        mapa = {}
        for p in ((cache_get('processos') or {}).get('processos') or []):
            mapa[(p.get('numero') or '').strip()] = p
        _CAPAS['mapa'] = mapa
        _CAPAS['quando'] = agora
    return _CAPAS['mapa'].get((num or '').strip())


_ORIGEM_NAO_E_PARTE = ('INDICA', 'ANUNCIO', 'ESCRITORIO', 'BNI', 'CAMPANHA',
                       'FACEBOOK', 'INSTAGRAM', 'GOOGLE',
                       'PROGRAMA', 'CONTRATANTE')


def _partes_na_base(numero, nome_cliente=''):
    ""
    nossos, contrarias, reserva = [], [], []
    eu = normalizar_nome(nome_cliente)
    try:
        with _con() as con:
            for nome_cad, nome_parte, papel, cid in con.execute(
                    'SELECT c.nome, pt.nome, pt.papel, pt.cliente_id '
                    'FROM jur_processo_partes pt '
                    'JOIN jur_processos p ON p.id = pt.processo_id '
                    'LEFT JOIN jur_clientes c ON c.id = pt.cliente_id '
                    'WHERE p.numero = ? ORDER BY pt.id', ((numero or '').strip(),)):
                nome = (nome_cad or nome_parte or '').strip()
                if not nome:
                    continue
                pap = (papel or '').strip().upper()
                if 'PARTE CONTR' in pap:
                    destino = contrarias
                elif cid is None and not any(o in pap for o in _ORIGEM_NAO_E_PARTE):
                    destino = reserva
                else:
                    destino = nossos
                if destino is not nossos and eu and normalizar_nome(nome) == eu:
                    destino = nossos
                if nome not in destino:
                    destino.append(nome)
    except Exception:
        pass
    return nossos, contrarias + reserva


def _polo_por_tipo(tipo):
    try:
        from .processo_info import polo_do_tipo
        return polo_do_tipo(tipo) or ''
    except Exception:
        return ''


def processos_do_cliente(cliente: dict) -> list:
    ""
    from .traducao import bloqueado_no_portal

    tit = _titularidade_indexada()
    pastas_advbox = _pastas_do_advbox()
    proprios = processos_do_advbox(cliente)
    nome_cli = normalizar_nome(cliente['nome'])
    doc = cliente['cpf']
    candidatos = set(proprios)

    partes = cache_get('partes') or {}
    for ent in (partes.get('entidades') or []):
        if so_digitos(ent.get('cpf_cnpj') or '') != doc:
            continue
        for p in (ent.get('processos') or []):
            num = (p.get('numero') or '').strip()
            if num:
                candidatos.add(num)

    for num, t in tit.items():
        if nome_cli and nome_cli in _nomes_lista(t.get('clientes')):
            candidatos.add(num)

    capas = {}
    for p in ((cache_get('processos') or {}).get('processos') or []):
        capas[(p.get('numero') or '').strip()] = p

    saida = []
    for num in candidatos:
        t = tit.get(num)
        proprio = proprios.get(num)

        porta_1 = bool(t) and nome_cli in _nomes_lista(t.get('clientes'))
        porta_2 = proprio is not None
        if not (porta_1 or porta_2):
            continue
        if t and nome_cli in _nomes_lista(t.get('adversos')):
            continue

        if not _cliente_e_o_escritorio(nome_cli):
            capa_polo = (capas_por_num(num) or {}).get('polo_ativo') or ''
            if (t and _processo_do_escritorio(t)) or _e_o_escritorio(capa_polo):
                continue

        etapa = (proprio or {}).get('etapa_detalhada') or (t.get('etapa') if t else '') or ''
        grupo = (proprio or {}).get('grupo') or (t.get('fase') if t else '') or ''
        if bloqueado_no_portal(etapa):
            continue

        capa = capas.get(num) or {}

        saida.append({
            'segredo': bool(capa.get('segredo_justica')),
            'numero': num,
            'origem_vinculo': 'titularidade' if porta_1 else 'advbox',
            'etapa': etapa,
            'grupo': grupo,
            'responsavel': (proprio or {}).get('responsavel') or (t.get('responsavel') if t else '') or '',
            'pasta_drive': (pastas_advbox.get(num)
                            or link_de_pasta(t.get('pasta_drive') if t else '')),
            'monitorado': num in capas,
            'tribunal': capa.get('tribunal') or '',
            'classe': capa.get('classe') or (proprio or {}).get('tipo') or '',
            'assunto': capa.get('assunto') or '',
            'polo_ativo': capa.get('polo_ativo') or '',
            'polo_passivo': capa.get('polo_passivo') or '',
            'clientes': (_nomes_originais(t.get('clientes')) if t
                         else _partes_na_base(num, cliente['nome'])[0]),
            'adversos': (_nomes_originais(t.get('adversos')) if t
                         else _partes_na_base(num, cliente['nome'])[1]),
            'nosso_polo': ((t.get('nosso_polo') if t else '')
                           or _polo_por_tipo((proprio or {}).get('tipo'))),
            'baixado': bool(capa.get('baixado')) or bool((proprio or {}).get('encerrado')),
            'ultimo_andamento': capa.get('ultimo_andamento'),
            'data_distribuicao': capa.get('data_distribuicao'),
        })
    saida.sort(key=lambda x: (x['ultimo_andamento'] or x['data_distribuicao'] or ''), reverse=True)
    saida.sort(key=lambda x: bool(x['baixado']))
    return saida


def pode_ver(cliente: dict, numero: str):
    ""
    num = (numero or '').strip()
    for p in processos_do_cliente(cliente):
        if p['numero'] == num:
            return p
    return None


def _drive_service():
    ""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    token_path = os.path.join('/opt/escritorio', 'config', 'token.json')
    if not os.path.exists(token_path):
        raise RuntimeError('token.json do Google nao encontrado')
    creds = Credentials.from_authorized_user_file(token_path, ['https://www.googleapis.com/auth/drive'])
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, 'w') as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError('token do Google expirado e sem refresh. Precisa reautorizar.')
    return build('drive', 'v3', credentials=creds, cache_discovery=False)


def extrair_folder_id(url: str):
    if not url:
        return None
    m = re.search(r'/folders/([A-Za-z0-9_-]{10,})', url) or re.search(r'[?&]id=([A-Za-z0-9_-]{10,})', url)
    return m.group(1) if m else (url if re.fullmatch(r'[A-Za-z0-9_-]{20,}', url or '') else None)


def compartilhar_pasta(pasta_url: str, email: str, avisar: bool = True) -> dict:
    ""
    from . import drive_cliente
    folder_id = extrair_folder_id(pasta_url)
    if not folder_id:
        return {'ok': False, 'erro': 'pasta do processo ainda nao cadastrada'}
    try:
        svc = _drive_service()
        r = drive_cliente.liberar_leitura(svc, folder_id, email, avisar)
        if not r.get('ok'):
            return {'ok': False, 'erro': r.get('mensagem')}
        return r
    except Exception as e:
        return {'ok': False, 'erro': str(e)[:200]}


def listar_pasta(pasta_url: str) -> list:
    ""
    from . import drive_cliente
    folder_id = extrair_folder_id(pasta_url)
    if not folder_id:
        return []
    try:
        svc = _drive_service()
        sub = drive_cliente.achar_subpasta_do_cliente(svc, folder_id)
        if not sub:
            return []
        return drive_cliente.listar(svc, sub['id'])
    except Exception:
        return []


DDL = [
    """CREATE TABLE IF NOT EXISTS portal_acesso (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER UNIQUE, cpf TEXT UNIQUE, nome TEXT,
        celular TEXT, email TEXT, ativo INTEGER DEFAULT 1,
        criado_em REAL, ultimo_login REAL, logins INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS portal_otp (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cpf TEXT, codigo_hash TEXT, canal TEXT, destino TEXT,
        criado_em REAL, expira_em REAL, tentativas INTEGER DEFAULT 0, usado INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS portal_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cpf TEXT, cliente_id INTEGER, acao TEXT, detalhe TEXT, ip TEXT, criado_em REAL)""",
    """CREATE TABLE IF NOT EXISTS portal_contato (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER, nome TEXT, celular TEXT, email TEXT,
        ativo INTEGER DEFAULT 1, criado_em REAL)""",
    "CREATE INDEX IF NOT EXISTS ix_portal_contato_cli ON portal_contato(cliente_id, ativo)",
    """CREATE TABLE IF NOT EXISTS portal_resumo_ia (
        chave TEXT PRIMARY KEY, numero TEXT, tipo TEXT, texto TEXT,
        modelo TEXT, tokens_entrada INTEGER, tokens_saida INTEGER, criado_em REAL)""",
    "CREATE INDEX IF NOT EXISTS ix_portal_otp_cpf ON portal_otp(cpf, usado)",
    "CREATE INDEX IF NOT EXISTS ix_portal_log_cpf ON portal_log(cpf, criado_em)",
]


def criar_tabelas():
    with _con() as con:
        for ddl in DDL:
            con.execute(ddl)
        existentes = {r[1] for r in con.execute('PRAGMA table_info(portal_acesso)')}
        for coluna in ('celular_portal', 'email_portal'):
            if coluna not in existentes:
                con.execute(f'ALTER TABLE portal_acesso ADD COLUMN {coluna} TEXT')
        cols = {r[1] for r in con.execute('PRAGMA table_info(portal_contato)')}
        for coluna, tipo in (('titular', 'INTEGER DEFAULT 1'), ('relacao', 'TEXT'),
                             ('autorizado_por', 'TEXT'), ('autorizado_em', 'REAL')):
            if coluna not in cols:
                con.execute(f'ALTER TABLE portal_contato ADD COLUMN {coluna} {tipo}')
        con.commit()


def log(cpf: str, cliente_id, acao: str, detalhe: str = '', ip: str = ''):
    ""
    try:
        with _con() as con:
            con.execute('INSERT INTO portal_log(cpf,cliente_id,acao,detalhe,ip,criado_em) VALUES(?,?,?,?,?,?)',
                        (cpf, cliente_id, acao, (detalhe or '')[:400], ip, time.time()))
            con.commit()
    except Exception:
        pass
