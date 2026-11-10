""
import io
import os
import time

ARQUIVO = os.getenv('ESC_INADIMPLENTES',
                    '/opt/escritorio/FINANCEIRO/portal_inadimplentes.txt')
_CACHE = {'quando': 0, 'linhas': []}
TTL = 120


def _norm(v):
    import unicodedata
    v = unicodedata.normalize('NFKD', v or '')
    v = ''.join(c for c in v if not unicodedata.combining(c))
    for suf in ('_T_G', '_P_G', '_G', '_P'):
        v = v.replace(suf, ' ')
    return ' '.join(v.upper().split())


def _so_digitos(v):
    return ''.join(c for c in (v or '') if c.isdigit())


def _lista():
    ""
    agora = time.time()
    if agora - _CACHE['quando'] < TTL:
        return _CACHE['linhas']
    linhas = []
    try:
        with io.open(ARQUIVO, encoding='utf-8') as f:
            for ln in f:
                ln = ln.split('#')[0].strip()
                if ln:
                    linhas.append(ln)
    except Exception:
        linhas = []
    _CACHE['quando'] = agora
    _CACHE['linhas'] = linhas
    return linhas


def esta_na_lista(cliente) -> bool:
    ""
    doc = _so_digitos((cliente or {}).get('documento') or (cliente or {}).get('cpf'))
    nome = _norm((cliente or {}).get('nome'))
    for ln in _lista():
        d = _so_digitos(ln)
        if d and doc and d == doc:
            return True
        n = _norm(ln)
        if n and nome and (n == nome or (len(n) >= 10 and n in nome)):
            return True
    return False


def situacao(cliente) -> dict:
    ""
    base = {'inadimplente': False, 'qtd': 0, 'valor': 0.0, 'desde': '',
            'boleto': '', 'mensagem': '', 'titulo': '', 'consultoria': False}
    try:
        if not esta_na_lista(cliente):
            return base
    except Exception:
        return base

    base['inadimplente'] = True
    base['consultoria'] = False
    base['titulo'] = 'Pendência financeira'
    base['mensagem'] = ('Há pendências financeiras no seu contrato. Para voltar '
                        'a acompanhar os processos por aqui, é só regularizar.')
    try:
        from . import financeiro as fin
        d = fin.do_cliente(_so_digitos(cliente.get('documento') or cliente.get('cpf')))
        if d.get('consultoria'):
            base['consultoria'] = True
            base['titulo'] = 'Contrato de consultoria suspenso'
            base['mensagem'] = ('O seu contrato de consultoria está suspenso por '
                                'pendência financeira. Os processos seguem '
                                'listados, mas o acompanhamento por aqui volta '
                                'assim que as mensalidades forem regularizadas.')
        vencidos = [c for c in (d.get('em_aberto') or []) if c.get('vencido')]
        if not vencidos:
            return base
        base['qtd'] = len(vencidos)
        base['valor'] = round(sum(float(c.get('valor') or 0) for c in vencidos), 2)

        def _chave(c):
            v = (c.get('vencimento') or '')[:10]
            return v[6:10] + v[3:5] + v[0:2]

        antigo = sorted(vencidos, key=_chave)[0]
        base['desde'] = antigo.get('vencimento') or ''
        base['boleto'] = antigo.get('boleto') or antigo.get('fatura') or ''
    except Exception:
        pass
    return base


def sugerir(limite_dias=30, minimo=200.0):
    ""
    import datetime
    from . import dados, financeiro as fin

    hoje = datetime.date.today()
    fora = []
    with dados._con() as con:
        linhas = list(con.execute(
            "SELECT id, nome, documento FROM jur_clientes "
            "WHERE documento IS NOT NULL AND TRIM(documento) <> ''"))
    for r in linhas:
        cli = {'nome': r['nome'], 'documento': r['documento'], 'cpf': r['documento']}
        if esta_na_lista(cli):
            continue
        try:
            d = fin.do_cliente(_so_digitos(r['documento']))
        except Exception:
            continue
        venc = [c for c in (d.get('em_aberto') or []) if c.get('vencido')]
        if not venc:
            continue
        total = sum(float(c.get('valor') or 0) for c in venc)
        if total < minimo:
            continue
        def _dias(c):
            v = (c.get('vencimento') or '')[:10]
            try:
                dt = datetime.date(int(v[6:10]), int(v[3:5]), int(v[0:2]))
                return (hoje - dt).days
            except Exception:
                return 0
        atraso = max(_dias(c) for c in venc)
        if atraso < limite_dias:
            continue
        fora.append({'id': r['id'], 'nome': r['nome'], 'valor': round(total, 2),
                     'qtd': len(venc), 'atraso_dias': atraso})
    fora.sort(key=lambda x: -x['valor'])
    return fora
