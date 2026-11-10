# -*- coding: utf-8 -*-
"""Teor nos autos (v10, 06/10/2026): quando a publicacao e curta, ler a decisao dentro do processo.

Pedido da Advogada: "ela nao leu a intimacao dentro do processo na integra". No TJSP (e-SAJ) a publicacao
costuma ser so o aviso ("Vistos. Int.", 75 a 700 caracteres) e o teor fica nos autos. O SYNC traz os
documentos do processo com `tem_markdown`; este modulo escolhe o documento de decisao certo (sentenca,
decisao, despacho, acordao, ato ordinatorio) publicado logo antes da intimacao, baixa o texto, guarda
numa tabela (uma busca por intimacao) e devolve para o classificador ler junto com a publicacao.

Bateria: python -m app.controladoria_motor.teor_autos
"""
import datetime as dt
import json
import re
import time
import unicodedata

CURTA = 900                 # caracteres: abaixo disso a publicacao nao traz o teor
JANELA_ANTES = 25           # dias: o documento tem de ser de pouco antes da disponibilizacao
JANELA_DEPOIS = 1
TETO_TEXTO = 24000          # caracteres do teor levados ao classificador
TRECHO = 700                # caracteres mostrados na ficha

_DECISAO = re.compile(r'SENTEN|DECIS|DESPACH|ACORD|ATO ORDINAT|JULGAMENT|HOMOLOG|DELIBERA|VOTO')
_NAO = re.compile(r'CERTID|COMPROVANTE|PROCURA|CONTRATO|PETI|PLANILHA|CALCULO|GUIA|DOCUMENTO MIGRADO|PEDIDO')

DDL = """CREATE TABLE IF NOT EXISTS motor_teor_autos (
    intimacao_id INTEGER PRIMARY KEY, processo TEXT, documento_id INTEGER, nome TEXT, tipo_documento TEXT,
    data TEXT, texto TEXT, motivo TEXT, criado_em REAL)"""


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or ''))
    return ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn').upper()


def _data(v):
    try:
        return dt.date.fromisoformat(str(v or '')[:10])
    except ValueError:
        return None


def publicacao_curta(it, so_cabecalho=None):
    """A publicacao nao traz o teor: curta, ou so o cabecalho (teor nos autos)."""
    texto = str(it.get('texto') or '')
    if len(texto.strip()) < CURTA:
        return True
    return bool(so_cabecalho and so_cabecalho(texto))


def escolher_documento(itens, disponibilizacao):
    """O documento de decisao mais recente dentro da janela antes da disponibilizacao, com texto no SYNC."""
    disp = _data(disponibilizacao) or dt.date.today()
    ini, fim = disp - dt.timedelta(days=JANELA_ANTES), disp + dt.timedelta(days=JANELA_DEPOIS)
    cands = []
    for d in itens or []:
        if not isinstance(d, dict) or d.get('tipo') != 'documento' or not d.get('tem_markdown'):
            continue
        quando = _data(d.get('data'))
        if not quando or quando < ini or quando > fim:
            continue
        rotulo = _norm((d.get('tipo_documento') or '') + ' ' + (d.get('nome') or ''))
        if _NAO.search(rotulo) and not _DECISAO.search(_norm(d.get('tipo_documento') or '')):
            continue
        peso = 2 if _DECISAO.search(_norm(d.get('tipo_documento') or '')) else (1 if _DECISAO.search(rotulo) else 0)
        if not peso and 'HTML' not in _norm(d.get('mime') or '') and not str(d.get('nome') or '').lower().endswith('.html'):
            continue                      # sem rotulo de decisao, so o html do tribunal (gproc) serve
        cands.append((peso, quando, d))
    if not cands:
        return None
    cands.sort(key=lambda x: (x[1], x[0]))
    return cands[-1][2]


def _limpar(md):
    t = str(md or '')
    t = re.sub(r'\A\s*---\n.*?\n---[ \t]*\n?', '', t, count=1, flags=re.S)   # cabecalho YAML do SYNC
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', t)          # imagens
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)       # links
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


def garantir(con):
    con.execute(DDL)


def ler(con, it, get_all=None, get_text=None, so_cabecalho=None, forcar=False):
    """Devolve {'documento_id','nome','tipo_documento','data','texto','trecho','fonte'} ou None.
    Guarda o resultado (ate o 'nada achado') para nao voltar ao SYNC a cada ciclo."""
    garantir(con)
    iid = int(it.get('id') or 0)
    if not iid or (not forcar and not publicacao_curta(it, so_cabecalho)):
        return None
    row = con.execute('SELECT documento_id, nome, tipo_documento, data, texto, motivo FROM motor_teor_autos WHERE intimacao_id=?',
                      (iid,)).fetchone()
    if row and not forcar:
        if not row[4]:
            return None
        return {'documento_id': row[0], 'nome': row[1], 'tipo_documento': row[2], 'data': row[3], 'texto': row[4],
                'trecho': row[4][:TRECHO], 'fonte': 'cache'}
    if not (get_all and get_text):
        return None
    num = str(it.get('processo') or '').strip()
    if not num:
        return None
    motivo, texto, doc = '', '', None
    try:
        a = get_all('/processos/%s/autos' % num, 'autos', None, 2400)
        doc = escolher_documento((a or {}).get('autos') or [], it.get('data'))
        if not doc:
            motivo = 'nenhum documento de decisao com texto na janela de %d dias' % JANELA_ANTES
        else:
            r = get_text('/documentos/%s/markdown' % doc.get('id'))
            texto = _limpar((r or {}).get('markdown') if isinstance(r, dict) else r)[:TETO_TEXTO]
            if len(texto) < 80:
                motivo, texto = 'documento %s sem texto util' % doc.get('id'), ''
    except Exception as e:
        # falha passageira do SYNC: nao grava, para tentar de novo no proximo ciclo
        return None
    con.execute('INSERT OR REPLACE INTO motor_teor_autos (intimacao_id, processo, documento_id, nome, tipo_documento, data, texto, motivo, criado_em) '
                'VALUES (?,?,?,?,?,?,?,?,?)',
                (iid, num, (doc or {}).get('id'), (doc or {}).get('nome'), (doc or {}).get('tipo_documento'),
                 str((doc or {}).get('data') or '')[:10], texto, motivo, time.time()))
    con.commit()
    if not texto:
        return None
    return {'documento_id': doc.get('id'), 'nome': doc.get('nome'), 'tipo_documento': doc.get('tipo_documento'),
            'data': str(doc.get('data') or '')[:10], 'texto': texto, 'trecho': texto[:TRECHO], 'fonte': 'sync'}


def com_teor(it, teor):
    """A intimacao com o teor dos autos emendado ao texto, para o classificador ler tudo."""
    if not teor:
        return it
    cab = '\n\n[TEOR NOS AUTOS: %s de %s]\n' % (teor.get('tipo_documento') or teor.get('nome') or 'documento',
                                                   teor.get('data') or '?')
    return dict(it, texto=str(it.get('texto') or '') + cab + teor['texto'], teor_autos=True)


def resumo(teor):
    if not teor:
        return None
    return {k: teor.get(k) for k in ('documento_id', 'nome', 'tipo_documento', 'data', 'trecho', 'fonte')}


# ----------------------------------------------------------------------------- bateria
def _bateria():
    import sqlite3
    ok = total = 0

    def caso(nome, cond):
        nonlocal ok, total
        total += 1
        ok += 1 if cond else 0
        print('  [%s] %s' % ('OK ' if cond else 'X  ', nome))

    autos = [
        {'tipo': 'movimento', 'data': '2026-10-05T10:00:00+00:00', 'descricao': 'Julgado procedente'},
        {'tipo': 'documento', 'data': '2026-09-25T10:00:00+00:00', 'nome': 'PETICAO INICIAL.pdf', 'tipo_documento': 'PETIÇÃO INICIAL', 'tem_markdown': False},
        {'tipo': 'documento', 'data': '2026-09-26T10:00:00+00:00', 'nome': 'Certido_de_Publicao.pdf', 'tipo_documento': 'Certidão de Publicação', 'tem_markdown': True, 'id': 1},
        {'tipo': 'documento', 'data': '2026-10-02T10:00:00+00:00', 'nome': 'gproc_1.html', 'tipo_documento': 'ATO ORDINATÓRIO', 'tem_markdown': True, 'id': 2, 'mime': 'text/html'},
        {'tipo': 'documento', 'data': '2026-10-05T10:00:00+00:00', 'nome': 'gproc_2.html', 'tipo_documento': 'SENTENÇA', 'tem_markdown': True, 'id': 3, 'mime': 'text/html'},
        {'tipo': 'documento', 'data': '2026-10-05T12:00:00+00:00', 'nome': 'Comprovante.pdf', 'tipo_documento': 'Comprovante de Pagamento', 'tem_markdown': True, 'id': 4},
        {'tipo': 'documento', 'data': '2021-04-15T10:00:00+00:00', 'nome': 'Decisão', 'tipo_documento': 'Decisão', 'tem_markdown': True, 'id': 5},
    ]
    d = escolher_documento(autos, '2026-10-06T03:00:00+00:00')
    caso('escolhe a SENTENCA de 05/10 (nao a certidao, nao o comprovante, nao a decisao de 2021)', d and d['id'] == 3)
    d2 = escolher_documento([x for x in autos if x.get('id') != 3], '2026-10-06')
    caso('sem sentenca, fica o ato ordinatorio de 02/10', d2 and d2['id'] == 2)
    caso('fora da janela nao serve', escolher_documento(autos, '2026-12-20') is None)
    caso('publicacao curta e detectada; longa nao',
         publicacao_curta({'texto': 'Vistos. Int.'}) and not publicacao_curta({'texto': 'x' * 1200})
         and publicacao_curta({'texto': 'y' * 1200}, so_cabecalho=lambda t: True))

    con = sqlite3.connect(':memory:')
    chamadas = []

    def get_all(path, chave, params, cap):
        chamadas.append(path)
        return {'autos': autos}

    def get_text(path):
        chamadas.append(path)
        return {'markdown': '---\nprocesso: 9782391-95.2022.8.26.3911\ntribunal: TJSP\n---\n# SENTENÇA\n\n![logo](x.png) Vistos. [link](http://x) JULGO PROCEDENTE o pedido. ' + ('texto ' * 40)}

    it = {'id': 86398, 'processo': '9782391-95.2022.8.26.3911', 'data': '2026-10-06T03:00:00+00:00', 'texto': 'Vistos. Int.'}
    t1 = ler(con, it, get_all, get_text)
    caso('le o teor da sentenca pelo SYNC e limpa imagem e link',
         t1 and t1['documento_id'] == 3 and t1['fonte'] == 'sync' and 'JULGO PROCEDENTE' in t1['texto']
         and '![' not in t1['texto'] and '(http' not in t1['texto'] and t1['trecho'] == t1['texto'][:TRECHO]
         and t1['texto'].startswith('# SENTENÇA'))
    n = len(chamadas)
    t2 = ler(con, it, get_all, get_text)
    caso('segunda leitura vem do cache, sem voltar ao SYNC', t2 and t2['fonte'] == 'cache' and len(chamadas) == n)
    caso('publicacao longa nao vai aos autos', ler(con, dict(it, id=1, texto='z' * 1500), get_all, get_text) is None and len(chamadas) == n)
    it3 = dict(it, id=2, data='2026-12-20')
    caso('sem documento na janela: guarda o motivo e nao repete a busca',
         ler(con, it3, get_all, get_text) is None and ler(con, it3, get_all, get_text) is None
         and con.execute('SELECT motivo FROM motor_teor_autos WHERE intimacao_id=2').fetchone()[0].startswith('nenhum documento')
         and len([c for c in chamadas if 'autos' in c]) == 2)
    def get_all_ruim(path, chave, params, cap):
        raise RuntimeError('SYNC 503')
    it4 = dict(it, id=4)
    caso('falha passageira do SYNC nao fica gravada: tenta de novo depois',
         ler(con, it4, get_all_ruim, get_text) is None
         and con.execute('SELECT COUNT(*) FROM motor_teor_autos WHERE intimacao_id=4').fetchone()[0] == 0
         and (ler(con, it4, get_all, get_text) or {}).get('documento_id') == 3)
    c2 = com_teor(it, t1)
    caso('com_teor emenda o teor ao texto da publicacao', c2['texto'].startswith('Vistos. Int.') and '[TEOR NOS AUTOS: SENTENÇA de 2026-10-05]' in c2['texto'] and c2['teor_autos'])
    caso('resumo para a ficha nao leva o texto inteiro', resumo(t1) and 'texto' not in resumo(t1) and resumo(t1)['tipo_documento'] == 'SENTENÇA')
    print('teor_autos: %d/%d, bateria ok' % (ok, total) if ok == total else 'teor_autos: %d/%d FALHOU' % (ok, total))
    return ok == total


if __name__ == '__main__':
    raise SystemExit(0 if _bateria() else 1)
