"""Motor de prazos deterministico — a autoridade de datas da Controladoria.

disponibilizacao no DJEN
  -> PUBLICACAO: 1o dia util seguinte            (art. 224, 2o CPC)
    -> INICIO DA CONTAGEM: 1o dia util seguinte a publicacao  (art. 224, 3o)
      -> conta N dias UTEIS (exclui o dia do comeco, inclui o do vencimento)
        -> VENCIMENTO (prorroga se cair em dia sem expediente)
          -> PRAZO FATAL INTERNO = vencimento - 2 dias UTEIS   (regra da casa)

Nenhuma IA calcula data aqui. Substitui o Legal Cloud.
"""
from __future__ import annotations
import re, unicodedata
from datetime import date, datetime, timedelta

from . import feriados as F

# ---- tribunal -> UF -----------------------------------------------------------
_TRT_UF = {
    '01': 'RJ', '1': 'RJ', '02': 'SP', '2': 'SP', '03': 'MG', '3': 'MG',
    '04': 'RS', '4': 'RS', '05': 'BA', '5': 'BA', '06': 'PE', '6': 'PE',
    '07': 'CE', '7': 'CE', '08': 'PA', '8': 'PA', '09': 'PR', '9': 'PR',
    '10': 'DF', '11': 'AM', '12': 'SC', '13': 'PB', '14': 'RO', '15': 'SP',
    '16': 'MA', '17': 'ES', '18': 'GO', '19': 'AL', '20': 'SE', '21': 'RN',
    '22': 'PI', '23': 'MT', '24': 'MS',
}
_TJ_UF = {
    'TJSP': 'SP', 'TJRJ': 'RJ', 'TJMG': 'MG', 'TJRS': 'RS', 'TJPE': 'PE',
    'TJBA': 'BA', 'TJPR': 'PR', 'TJSC': 'SC', 'TJGO': 'GO', 'TJMT': 'MT',
    'TJMS': 'MS', 'TJES': 'ES', 'TJCE': 'CE', 'TJDF': 'DF', 'TJDFT': 'DF',
}


def _norm(s: str) -> str:
    if not s:
        return ''
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().strip()


def uf_do_tribunal(tribunal: str) -> str:
    t = _norm(tribunal).upper().replace(' ', '')
    if not t:
        return ''
    if t in _TJ_UF:
        return _TJ_UF[t]
    m = re.match(r'TRT0?(\d{1,2})', t)
    if m:
        return _TRT_UF.get(m.group(1), _TRT_UF.get(m.group(1).zfill(2), ''))
    if t.startswith('TST') or t.startswith('STJ') or t.startswith('STF'):
        return 'DF'
    # TJXX generico
    if t.startswith('TJ') and len(t) >= 4:
        return t[2:4]
    return ''


# materias que aparecem depois de "de" mas NAO sao comarca
_STOP_COMARCA = {
    'direito privado', 'direito publico', 'fazenda publica', 'familia', 'familia e sucessoes',
    'orfaos e sucessoes', 'registros publicos', 'execucoes fiscais', 'direito empresarial',
    'direito bancario', 'direito privado 1', 'direito privado 2',
}


def comarca_do_orgao(orgao: str) -> str:
    """Extrai a comarca do nome do orgao. Best-effort. Retorna '' quando nao da pra
    saber (nunca inventa — na duvida o feriado de comarca so nao e aplicado)."""
    o = _norm(orgao)
    if not o:
        return ''
    pats = (
        r'[Cc]omarca de ([A-Za-z\s]+)',
        r'[Ff]oro (?:Central |Regional )?de ([A-Za-z\s]+)',
        r'[Ff]oro d[ao] ([A-Za-z\s]+)',
        r'[Vv]ara do [Tt]rabalho de ([A-Za-z\s]+)',   # TRT: "3a Vara do Trabalho de Recife"
        r'[Vv]ara [A-Za-z]* ?d[eo] ([A-Z][A-Za-z\s]+)',  # "Vara Civel de X"
        r' de ([A-Z][a-z][A-Za-z\s]+)$',               # fallback: ultima "de X" no fim
    )
    for pat in pats:
        m = re.search(pat, o)
        if m:
            c = m.group(1).strip().lower()
            c = re.split(r'\s*[-/,]\s*', c)[0].strip()
            c = re.sub(r'\s+', ' ', c)
            if c and c not in _STOP_COMARCA and len(c) > 2 and not c.isdigit():
                return c
    return ''


def _ramo_do_tribunal(tribunal: str) -> str:
    t = _norm(tribunal).upper()
    if 'TRT' in t or t.startswith('TST'):
        return 'trabalhista'
    return 'civel'


def _para_date(v) -> date | None:
    if isinstance(v, date):
        return v
    if not v:
        return None
    s = str(v)[:10]
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        try:
            return datetime.fromisoformat(str(v).replace('Z', '+00:00')).date()
        except Exception:
            return None


def calcular_prazo(disponibilizacao, dias: int | None, contagem: str = 'uteis',
                   tribunal: str = '', orgao: str = '', uf: str = '', comarca: str = '',
                   folga_interna: int = 2) -> dict:
    """Calcula publicacao, inicio, vencimento e prazo fatal interno.

    Retorna dict com as datas (ISO) + feriados no caminho + avisos.
    Se `dias` for None (prazo vem do despacho), devolve so publicacao/inicio e
    marca `pendente_dias=True` — o classificador injeta os dias e chama de novo.
    """
    disp = _para_date(disponibilizacao)
    avisos: list[str] = []
    if disp is None:
        return {'erro': 'data de disponibilizacao invalida', 'avisos': avisos}

    uf = uf or uf_do_tribunal(tribunal)
    comarca = comarca or comarca_do_orgao(orgao)
    if not uf:
        avisos.append('UF nao identificada pelo tribunal — feriado estadual/comarca nao aplicado')
    if tribunal and 'TRT' in _norm(tribunal).upper() and not comarca:
        avisos.append('Comarca nao identificada — conferir feriado local (TRT tem calendario por comarca)')

    publicacao = F.proximo_dia_util(disp, uf, comarca)
    inicio = F.proximo_dia_util(publicacao, uf, comarca)
    if F.em_suspensao(disp) or F.em_suspensao(publicacao):
        avisos.append('Intimacao na suspensao 20/12-20/01 — contagem so comeca em 21/01')

    out = {
        'disponibilizacao': disp.isoformat(),
        'publicacao': publicacao.isoformat(),
        'inicio_contagem': inicio.isoformat(),
        'uf': uf, 'comarca': comarca or None,
        'contagem': contagem,
        'avisos': avisos,
    }
    if dias is None:
        out['pendente_dias'] = True
        out['vencimento'] = None
        out['prazo_fatal_interno'] = None
        return out

    dias = int(dias)
    feriados_no_caminho: list[dict] = []

    def _registra_feriados(a: date, b: date):
        cur = a
        while cur <= b:
            nome = F.feriado_nome(cur, uf, comarca)
            if nome and cur.weekday() < 5:
                tipo = F.feriado_tipo(cur, uf, comarca)
                feriados_no_caminho.append({'data': cur.isoformat(), 'nome': nome, 'tipo': tipo})
                if tipo == 'forense':
                    avisos.append(f'{nome} ({cur.isoformat()}) e feriado forense — conferir se o tribunal suspendeu')
            pf = F.ponto_facultativo_nome(cur, uf, comarca)
            if pf:
                avisos.append(f'{pf} ({cur.isoformat()}): ponto facultativo contado como dia util; '
                              'confira se o tribunal suspendeu prazos')
            cur += timedelta(days=1)

    if contagem == 'corridos':
        bruto = inicio + timedelta(days=dias - 1)
        vencimento = bruto if F.eh_dia_util(bruto, uf, comarca) else F.proximo_dia_util(bruto, uf, comarca, incluir_hoje=True)
    else:
        cur = inicio
        cont = 1
        while cont < dias:
            cur = F.proximo_dia_util(cur, uf, comarca)
            cont += 1
        vencimento = cur

    _registra_feriados(disp, vencimento)
    # o interno nao e do tribunal: calendario unico de casa (feriados.prazo_interno), sem uf/comarca
    fatal = F.prazo_interno(vencimento, max(0, folga_interna))

    out['dias'] = dias
    out['vencimento'] = vencimento.isoformat()
    out['prazo_fatal_interno'] = fatal.isoformat()
    out['feriados_considerados'] = feriados_no_caminho
    return out


# ---- auto-teste ---------------------------------------------------------------
def _testes():
    casos = 0
    falhas = 0

    def chk(nome, got, esp):
        nonlocal casos, falhas
        casos += 1
        ok = got == esp
        if not ok:
            falhas += 1
        print(f"  [{'ok' if ok else 'FALHA'}] {nome}: got={got} esp={esp}")

    # 15 dias uteis civel TJSP, disp numa sexta 2026-08-14
    r = calcular_prazo('2026-08-14', 15, tribunal='TJSP')
    chk('pub apos sexta 14/08', r['publicacao'], '2026-08-17')       # segunda
    chk('inicio', r['inicio_contagem'], '2026-08-18')                 # terca
    # 5 dias uteis, disp segunda 2026-03-02
    r2 = calcular_prazo('2026-03-02', 5, tribunal='TJSP')
    chk('venc 5 uteis', r2['vencimento'], '2026-03-10')
    chk('fatal = venc - 2 uteis', r2['prazo_fatal_interno'], '2026-03-06')
    # suspensao: disp 2025-12-22 -> so conta a partir de 21/01
    r3 = calcular_prazo('2025-12-22', 8, tribunal='TRT02')
    chk('pos-suspensao publicacao em 2026-01', r3['publicacao'][:7], '2026-01')
    # UF do TRT6 = PE
    from_uf = uf_do_tribunal('TRT6')
    chk('TRT6 -> PE', from_uf, 'PE')
    chk('TRT06 -> PE', uf_do_tribunal('TRT06'), 'PE')
    chk('TJSP -> SP', uf_do_tribunal('TJSP'), 'SP')
    print(f"\n{casos - falhas}/{casos} testes ok")
    return falhas == 0


if __name__ == '__main__':
    _testes()
