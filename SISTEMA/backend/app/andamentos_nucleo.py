"""Linha do tempo do processo (28/09/2026, migracao do ADVBOX).

No ADVBOX a equipe escreve o andamento de cada processo como tarefa ou
COMENTARIO, e o historico do processo junta tudo. Aqui isso vira
`jur_andamentos`: o que veio do ADVBOX (so LIDO: GET /history/{id}) e o que a
equipe escreve no sistema, lado a lado.

O ADVBOX nao devolve id no historico. A chave de cada item e o hash de
processo + criado_em + tipo + autor + texto: rodar de novo nao duplica.

Quem usa:
  - routers/andamentos.py (tela: ficha do processo e ficha da tarefa);
  - SYNC/andamentos_espelho_advbox.py (madrugada: le os processos ativos);
  - audiencias_vigia.py (checklist da Dra. Advogada: juntadas, testemunhas,
    pedido de audiencia virtual) le daqui em vez de ir ao ADVBOX.
"""
import hashlib
import os
import sqlite3
import time
from pathlib import Path

DB = str(Path(__file__).resolve().parents[1] / 'sistema_esc.db')
ADVBOX_API = 'https://app.advbox.com.br/api/v1'


def con(db=None):
    c = sqlite3.connect(db or DB, timeout=30)
    c.row_factory = sqlite3.Row
    garantir(c)
    return c


def garantir(c):
    c.execute("""CREATE TABLE IF NOT EXISTS jur_andamentos (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   processo_id INTEGER, processo_numero TEXT, lawsuit_id INTEGER,
                   tipo TEXT, texto TEXT, autor TEXT, autor_id INTEGER, responsavel TEXT,
                   data_tarefa TEXT, prazo TEXT, criado_em TEXT, origem TEXT,
                   chave TEXT UNIQUE, editado_em TEXT, removido INTEGER DEFAULT 0)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_andam_proc ON jur_andamentos (processo_id, criado_em)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_andam_num ON jur_andamentos (processo_numero)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_andam_lid ON jur_andamentos (lawsuit_id)")
    c.execute("""CREATE TABLE IF NOT EXISTS jur_andamentos_sync (
                   processo_id INTEGER PRIMARY KEY, lawsuit_id INTEGER,
                   sincronizado_em TEXT, itens INTEGER)""")


def agora_txt():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _token():
    t = os.environ.get('ADVBOX_API_TOKEN')
    if t:
        return t
    for arq in ('/opt/escritorio/config/.env',):
        try:
            for linha in open(arq, encoding='utf-8'):
                if linha.strip().startswith('ADVBOX_API_TOKEN='):
                    return linha.split('=', 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return ''


def _get(rota, params=None, paciencia=False):
    """GET no ADVBOX (so leitura). None quando nao deu para ler.

    paciencia=False (tela): um 429 devolve None na hora, em vez de segurar a
    pessoa esperando o limite do ADVBOX. O robo da madrugada usa paciencia."""
    if (os.getenv('SISTEMA_JURIDICO') or 'nenhum').strip().lower() == 'nenhum' or not _token():
        return None      # sem sistema juridico externo: a linha do tempo e so a do sistema
    import requests
    h = {'Authorization': 'Bearer ' + _token(), 'User-Agent': 'SistemaEscritorio/1.0', 'Accept': 'application/json'}
    for tentativa in range(4 if paciencia else 1):
        try:
            r = requests.get(ADVBOX_API + rota, headers=h, params=params or {}, timeout=90 if paciencia else 12)
        except Exception:
            if paciencia:
                time.sleep(10)
                continue
            return None
        if r.status_code == 429:
            if paciencia:
                time.sleep(20 * (tentativa + 1))
                continue
            return None
        if r.status_code >= 400:
            return None
        try:
            return r.json() or {}
        except ValueError:
            return None
    return None


def advbox_historico(lawsuit_id, paciencia=False):
    """/history do processo: so os 20 itens MAIS RECENTES (o ADVBOX nao pagina), com autor."""
    j = _get('/history/%s' % int(lawsuit_id), None, paciencia)
    return None if j is None else (j.get('data') or [])


def advbox_posts(params, paciencia=True, pausa=2.2):
    """/posts paginado (lista completa de tarefas e comentarios, com id e responsaveis).
    Sem filtro de conclusao traz as que tem alguem pendente; com completed_start/end,
    as concluidas no periodo. None se alguma pagina falhou."""
    out, off, total = [], 0, None
    while True:
        j = _get('/posts', dict(params, limit=100, offset=off), paciencia)
        if j is None:
            return None
        d = j.get('data') or []
        if total is None:
            total = j.get('totalCount') or 0
        out += d
        off += 100
        if not d or off >= total:
            break
        time.sleep(pausa)
    return out


def chave(lawsuit_id, criado_em, tipo, texto):
    """Mesma chave para o item vindo do /history (tem autor, so os 20 ultimos) e
    do /posts (tem tudo, sem autor): processo + criado_em + tipo + texto."""
    base = '%s|%s|%s|%s' % (lawsuit_id, str(criado_em or '')[:19], (tipo or '').strip(), (texto or '').strip())
    return 'adv:' + hashlib.sha1(base.encode('utf-8')).hexdigest()[:24]


def gravar_item(c, processo_id, numero, lawsuit_id, tipo, texto, autor, responsavel,
                data_tarefa, prazo, criado_em):
    """Grava um item do ADVBOX. Se ja existe, so completa autor/responsavel que faltavam.
    Devolve 1 se era novo."""
    k = chave(lawsuit_id, criado_em, tipo, texto)
    cur = c.execute(
        'INSERT OR IGNORE INTO jur_andamentos (processo_id, processo_numero, lawsuit_id, tipo, texto, '
        'autor, responsavel, data_tarefa, prazo, criado_em, origem, chave) '
        "VALUES (?,?,?,?,?,?,?,?,?,?,'advbox',?)",
        (processo_id, numero or None, lawsuit_id, (tipo or '').strip() or None, (texto or '').strip() or None,
         (autor or '').strip() or None, (responsavel or '').strip() or None,
         str(data_tarefa or '')[:10] or None, str(prazo or '')[:10] or None,
         str(criado_em or '')[:19] or None, k))
    if cur.rowcount:
        return 1
    if autor or responsavel:
        c.execute("UPDATE jur_andamentos SET autor=COALESCE(NULLIF(autor,''), ?), "
                  "responsavel=COALESCE(NULLIF(responsavel,''), ?) WHERE chave=?",
                  ((autor or '').strip() or None, (responsavel or '').strip() or None, k))
    return 0


def gravar_advbox(c, processo_id, numero, lawsuit_id, historico):
    """Grava o /history do ADVBOX (os 20 mais recentes, com autor). Devolve os novos."""
    return sum(gravar_item(c, processo_id, numero or x.get('process_number'), lawsuit_id, x.get('task'),
                           x.get('comments'), x.get('author'), x.get('responsible'), x.get('start'),
                           x.get('date_deadline'), x.get('created_at'))
               for x in historico or [])


def gravar_post(c, processo_id, numero, p):
    """Grava um item do /posts (a lista completa de tarefas; sem autor, com os responsaveis)."""
    resp = ', '.join(u.get('name') or '' for u in (p.get('users') or []) if u.get('name'))
    return gravar_item(c, processo_id, numero, p.get('lawsuits_id'), p.get('task'), p.get('notes'),
                       None, resp, p.get('date'), p.get('date_deadline'), p.get('created_at'))


def sincronizar(c, processo_id, lawsuit_id, numero, paciencia=False):
    """Le o ADVBOX e grava. Devolve (novos, lidos) ou None se o ADVBOX nao respondeu."""
    h = advbox_historico(lawsuit_id, paciencia)
    if h is None:
        return None
    novos = gravar_advbox(c, processo_id, numero, lawsuit_id, h)
    c.execute('INSERT OR REPLACE INTO jur_andamentos_sync (processo_id, lawsuit_id, sincronizado_em, itens) '
              'VALUES (?,?,?,?)', (processo_id, lawsuit_id, agora_txt(), len(h)))
    c.commit()
    return novos, len(h)


def historico_local(lawsuit_id=None, numero=None, db=None):
    """No formato do /history do ADVBOX (created_at, comments, task, author), do
    mais novo para o mais antigo. Lista vazia quando nao ha nada guardado."""
    if not (lawsuit_id or numero):
        return []
    c = con(db)
    try:
        linhas = c.execute(
            "SELECT criado_em, texto, tipo, autor FROM jur_andamentos WHERE removido=0 "
            "AND (lawsuit_id=? OR processo_numero=?) ORDER BY criado_em DESC",
            (lawsuit_id or -1, numero or '#')).fetchall()
    finally:
        c.close()
    return [{'created_at': r['criado_em'], 'comments': r['texto'] or '', 'task': r['tipo'],
             'author': r['autor']} for r in linhas]
