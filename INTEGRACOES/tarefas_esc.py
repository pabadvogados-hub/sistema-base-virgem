"""Tarefas dos robos, gravadas no banco do sistema (a fonte da verdade do escritorio).

Todos os robos que abrem tarefa chamam criar_como_advbox (nome historico, mesma assinatura de
criar_publicacao) ou criar_local. Nada sai do sistema; se um dia houver sistema juridico externo,
a copia vai por INTEGRACOES/sistema_juridico.py.

Responsavel (guest_ids): um PAPEL de juridico/equipe.json ('revisora', 'titular', 'financeiro',
'automacao', ...), o id de usuario do sistema (int) ou um nome. Tipo (task_id): o nome do tipo
('PROTOCOLO', 'REVISAO DE PECA', ...).

ESC_DB existe so para teste em copia do banco.
"""
import datetime as dt
import hashlib
import os
import sqlite3
import sys
import time
import unicodedata

BACKEND = os.environ.get('ESC_BACKEND', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                    'SISTEMA', 'backend'))
DB = os.environ.get('ESC_DB', os.path.join(BACKEND, 'sistema_esc.db'))


def _equipe():
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    from app.juridico import equipe
    return equipe


def destino():
    """Mantido por compatibilidade: as tarefas nascem sempre no sistema."""
    return 'sistema'


def con(escrever=True):
    if escrever:
        c = sqlite3.connect(DB, timeout=60)
    else:
        c = sqlite3.connect('file:%s?mode=ro' % DB, uri=True, timeout=60)
    c.row_factory = sqlite3.Row
    return c


def _norm(txt):
    s = unicodedata.normalize('NFD', str(txt or ''))
    return ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn').upper().strip()


def _nome_bonito(nome):
    minusc = {'DA', 'DE', 'DO', 'DAS', 'DOS', 'E'}
    return ' '.join(p.lower() if p in minusc else p.capitalize()
                    for p in str(nome or '').split()) or None


def usuario_esc(c, quem, nome=''):
    """(usuario_id ou None, nome) a partir de um papel ('revisora'), id do sistema ou nome."""
    uid = None
    if isinstance(quem, str) and not quem.strip().lstrip('-').isdigit():
        p = _equipe().pessoa_do_papel(quem.strip().lower())
        uid = p.get('id')
        nome = nome or quem
    else:
        try:
            uid = int(quem)
        except (TypeError, ValueError):
            uid = None
    if uid:
        r = c.execute('SELECT nome FROM usuarios WHERE id=?', (uid,)).fetchone()
        if r:
            return uid, r['nome']
    primeiro = _norm(nome).split(' ')[0] if nome else ''
    if primeiro:
        achados = [r for r in c.execute('SELECT id, nome FROM usuarios WHERE ativo=1')
                   if _norm(r['nome']).split(' ')[0] == primeiro]
        if len(achados) == 1:
            return achados[0]['id'], achados[0]['nome']
    rev = _equipe().pessoa_do_papel('dona_das_tarefas')   # sem dono reconhecido: a dona das tarefas
    return rev.get('id'), rev.get('nome') or _nome_bonito(nome)


def tipo_esc(c, task_id, padrao='TAREFA'):
    if isinstance(task_id, str) and not task_id.strip().isdigit():
        return task_id.strip().upper() or padrao
    try:
        r = c.execute('SELECT nome FROM tarefa_tipos WHERE advbox_id=?', (int(task_id),)).fetchone()
        if r:
            return r['nome']
    except (sqlite3.OperationalError, TypeError, ValueError):
        pass
    return padrao


def processo_esc(c, lawsuit_id):
    """(numero, cliente) do processo: id interno de jur_processos, id externo ou o proprio numero CNJ."""
    if not lawsuit_id:
        return '', ''
    try:
        p = c.execute('SELECT id, numero FROM jur_processos WHERE numero=? OR advbox_id=? OR id=?',
                      (str(lawsuit_id), str(lawsuit_id), str(lawsuit_id))).fetchone()
    except (sqlite3.OperationalError, TypeError, ValueError):
        p = None
    if not p:
        return '', ''
    cli = c.execute("SELECT group_concat(nome, ' / ') FROM jur_processo_partes "
                    "WHERE processo_id=? AND COALESCE(papel,'') != 'PARTE CONTRÁRIA'",
                    (p['id'],)).fetchone()[0]
    return (p['numero'] or ''), (cli or '')


def titulo_de(texto, tipo):
    """Primeira linha util do texto; se for curta demais ("Ciente."), o tipo."""
    for linha in str(texto or '').splitlines():
        linha = linha.strip()
        if linha:
            return linha[:140] if len(linha) >= 12 else tipo
    return tipo


def _pfi(prazo_fatal):
    """D-2 uteis, o mesmo padrao da tela (app/tarefas_prazo.py)."""
    if not prazo_fatal:
        return None
    try:
        if BACKEND not in sys.path:
            sys.path.insert(0, BACKEND)
        from app import tarefas_prazo
        return tarefas_prazo.pfi_de(prazo_fatal)
    except Exception:
        return None


def criar_local(tipo, titulo, descricao, responsaveis, processo_numero=None, cliente=None,
                data=None, prazo_fatal=None, prazo_interno=None, prioridade='normal',
                origem='robo', origem_ref=None, criado_por=None):
    """Tarefa direto no sistema, com ids de usuario do sistema.
    Para o robo que ja sabe o processo pelo numero.
    Idempotente por (origem, origem_ref, tipo). Devolve o id da tarefa."""
    robo = os.path.splitext(os.path.basename(sys.argv[0] or 'robo'))[0] or 'robo'
    c = con()
    try:
        if origem_ref:
            ja = c.execute('SELECT id FROM tarefas WHERE origem=? AND origem_ref=? AND tipo=?',
                           (origem, origem_ref, tipo)).fetchone()
            if ja:
                return ja['id']
        agora = time.time()
        tid = c.execute(
            'INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, cliente, data, prazo_fatal, '
            "prazo_interno, prioridade, status, origem, origem_ref, criado_por, criado_em, atualizado_em) "
            "VALUES (?,?,?,?,?,?,?,?,?,'aberta',?,?,?,?,?)",
            (tipo, titulo or tipo, descricao or None, processo_numero or None, cliente or None,
             data or dt.date.today().isoformat(), prazo_fatal, prazo_interno or _pfi(prazo_fatal),
             prioridade, origem, origem_ref, criado_por or 'Automação (%s)' % robo, agora, agora)).lastrowid
        for uid in responsaveis or []:
            r = c.execute('SELECT nome FROM usuarios WHERE id=?', (uid,)).fetchone()
            c.execute('INSERT OR IGNORE INTO tarefa_responsaveis (tarefa_id, usuario_id, nome, importante, '
                      'urgente) VALUES (?,?,?,?,?)', (tid, uid, r['nome'] if r else None,
                                                       int(prioridade != 'normal'), int(prioridade == 'urgente')))
        c.commit()
        return tid
    finally:
        c.close()


def criar_como_advbox(lawsuit_id, task_id, guest_ids, comments='', from_id=None,
                      date_deadline=None, urgent=False, start_date=None):
    """Assinatura historica de criar_publicacao (lawsuit_id, task_id, guest_ids...), gravando no sistema.

    Idempotente: o mesmo robo, no mesmo processo, com o mesmo tipo e o mesmo
    texto, nao abre a segunda tarefa (UNIQUE origem+origem_ref+tipo).
    """
    robo = os.path.splitext(os.path.basename(sys.argv[0] or 'robo'))[0] or 'robo'
    c = con()
    try:
        tipo = tipo_esc(c, task_id)
        numero, cliente = processo_esc(c, lawsuit_id)
        assinatura = hashlib.sha1(('%s|%s|%s|%s' % (lawsuit_id, task_id, date_deadline, comments))
                                  .encode('utf-8')).hexdigest()[:16]
        ref = '%s|%s' % (robo, assinatura)
        ja = c.execute("SELECT id FROM tarefas WHERE origem='robo' AND origem_ref=? AND tipo=?",
                       (ref, tipo)).fetchone()
        if ja:
            return {'success': True, 'id': 'esc-%d' % ja['id'], 'sistema_esc': ja['id'],
                    'ja_existia': True}
        prazo_fatal = str(date_deadline)[:10] if date_deadline else None
        agora = time.time()
        cur = c.execute(
            'INSERT INTO tarefas (tipo, titulo, descricao, processo_numero, lawsuits_id, cliente, '
            'data, prazo_fatal, prazo_interno, prioridade, status, origem, origem_ref, criado_por, '
            "criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,'aberta','robo',?,?,?,?)",
            (tipo, titulo_de(comments, tipo), comments or None, numero or None,
             str(lawsuit_id) if lawsuit_id else None, cliente or None,
             start_date or dt.date.today().isoformat(), prazo_fatal, _pfi(prazo_fatal),
             'urgente' if urgent else 'normal', ref, 'Automação (%s)' % robo, agora, agora))
        tid = cur.lastrowid
        guests = guest_ids if isinstance(guest_ids, (list, tuple)) else [guest_ids]
        for g in guests:
            uid, nome = usuario_esc(c, g)
            c.execute('INSERT OR IGNORE INTO tarefa_responsaveis '
                      '(tarefa_id, usuario_id, nome, importante, urgente) VALUES (?,?,?,?,?)',
                      (tid, uid, nome, int(bool(urgent)), int(bool(urgent))))
        c.commit()
        return {'success': True, 'id': 'esc-%d' % tid, 'sistema_esc': tid}
    finally:
        c.close()
