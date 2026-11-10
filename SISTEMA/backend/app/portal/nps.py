""
import time
from datetime import datetime, timedelta, timezone

DIAS_ESPERA = 1
RODADA_DESDE = datetime(2026, 9, 16, tzinfo=timezone(timedelta(hours=-3))).timestamp()
ACOES_DE_USO = ('abriu_processo', 'abriu_painel')
MAX_COMENTARIO = 600

PERGUNTAS = [
    {'chave': 'facil',
     'texto': 'Foi fácil de usar?',
     'ponta_baixa': 'Difícil', 'ponta_alta': 'Muito fácil'},
    {'chave': 'gostou',
     'texto': 'Você gostou do portal?',
     'ponta_baixa': 'Não gostei', 'ponta_alta': 'Gostei muito'},
    {'chave': 'faz_sentido',
     'texto': 'Ele te ajuda a acompanhar o seu processo?',
     'ponta_baixa': 'Não ajuda', 'ponta_alta': 'Ajuda muito'},
]

DDL = """CREATE TABLE IF NOT EXISTS portal_nps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER, cpf TEXT,
    facil INTEGER, gostou INTEGER, faz_sentido INTEGER,
    comentario TEXT, dispensou INTEGER DEFAULT 0,
    criado_em REAL)"""


def criar_tabela(abrir):
    with abrir() as con:
        con.execute(DDL)
        con.commit()


def _primeiro_login(con, cliente_id):
    r = con.execute("SELECT MIN(criado_em) FROM portal_log "
                    "WHERE acao='login_ok' AND cliente_id=?", (cliente_id,)).fetchone()
    return r[0] if r and r[0] else None


def deve_perguntar(abrir, cliente_id) -> bool:
    ""
    marcas = ','.join('?' * len(ACOES_DE_USO))
    with abrir() as con:
        con.execute(DDL)
        nesta_rodada = con.execute(
            'SELECT COUNT(*) FROM portal_nps WHERE cliente_id=? AND criado_em>=?',
            (cliente_id, RODADA_DESDE)).fetchone()[0]
        if nesta_rodada:
            return False
        primeiro = _primeiro_login(con, cliente_id)
        if not primeiro:
            return False
        avaliou_antes = con.execute(
            'SELECT COUNT(*) FROM portal_nps WHERE cliente_id=? AND criado_em<?',
            (cliente_id, RODADA_DESDE)).fetchone()[0]
        usou_o_novo = con.execute(
            f'SELECT COUNT(*) FROM portal_log WHERE cliente_id=? AND criado_em>=? '
            f'AND acao IN ({marcas})', (cliente_id, RODADA_DESDE, *ACOES_DE_USO)).fetchone()[0]
        dias_de_uso = con.execute(
            f"SELECT COUNT(DISTINCT date(criado_em,'unixepoch','-3 hours')) FROM portal_log "
            f'WHERE cliente_id=? AND acao IN ({marcas})', (cliente_id, *ACOES_DE_USO)).fetchone()[0]
    if avaliou_antes:
        return usou_o_novo > 0
    return dias_de_uso >= 2 or (time.time() - primeiro) >= DIAS_ESPERA * 86400


def registrar(abrir, cliente_id, cpf, notas: dict, comentario: str = ''):
    ""
    def nota(k):
        v = notas.get(k)
        try:
            v = int(v)
        except (TypeError, ValueError):
            return None
        return v if 1 <= v <= 5 else None

    with abrir() as con:
        con.execute(DDL)
        con.execute('INSERT INTO portal_nps(cliente_id,cpf,facil,gostou,faz_sentido,'
                    'comentario,dispensou,criado_em) VALUES(?,?,?,?,?,?,0,?)',
                    (cliente_id, cpf, nota('facil'), nota('gostou'),
                     nota('faz_sentido'), (comentario or '')[:MAX_COMENTARIO],
                     time.time()))
        con.commit()


def dispensar(abrir, cliente_id, cpf):
    ""
    with abrir() as con:
        con.execute(DDL)
        con.execute('INSERT INTO portal_nps(cliente_id,cpf,facil,gostou,faz_sentido,'
                    'comentario,dispensou,criado_em) VALUES(?,?,NULL,NULL,NULL,?,1,?)',
                    (cliente_id, cpf, '', time.time()))
        con.commit()


def resumo(abrir, desde=None):
    ""
    with abrir() as con:
        con.execute(DDL)
        onde, args = '', []
        if desde:
            onde, args = ' AND criado_em > ?', [desde]
        linhas = [dict(r) for r in con.execute(
            'SELECT facil, gostou, faz_sentido, comentario FROM portal_nps '
            'WHERE dispensou=0' + onde, args)]
        dispensaram = con.execute(
            'SELECT COUNT(*) FROM portal_nps WHERE dispensou=1' + onde,
            args).fetchone()[0]

    def media(k):
        vs = [l[k] for l in linhas if l[k] is not None]
        return round(sum(vs) / len(vs), 1) if vs else None

    return {'respostas': len(linhas), 'dispensaram': dispensaram,
            'facil': media('facil'), 'gostou': media('gostou'),
            'faz_sentido': media('faz_sentido'),
            'comentarios': [l['comentario'] for l in linhas if (l['comentario'] or '').strip()]}
