"""Calendario forense — feriados nacionais fixos + moveis (base Pascoa) +
estaduais/comarca (JSON editavel) + suspensao de prazos 20/12 a 20/01.

Principio de seguranca: na duvida, MENOS feriado. Adicionar um feriado falso
empurra o vencimento pra frente (perigoso: acha que tem mais prazo do que tem);
omitir um feriado real puxa o vencimento pra tras (seguro: age antes). Por isso
os feriados duvidosos entram como AVISO, nao como bloqueio silencioso.
"""
from __future__ import annotations
import json, unicodedata
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

_LOCAIS = json.loads((Path(__file__).parent / 'feriados_locais.json').read_text('utf-8'))

# Nacionais de data fixa (MM-DD). Consciencia Negra nacional desde 2024 (Lei 14.759/2023).
NACIONAIS_FIXOS = {
    '01-01': 'Confraternizacao Universal',
    '04-21': 'Tiradentes',
    '05-01': 'Dia do Trabalho',
    '09-07': 'Independencia',
    '10-12': 'N. Sra. Aparecida',
    '11-02': 'Finados',
    '11-15': 'Proclamacao da Republica',
    '11-20': 'Consciencia Negra',
    '12-25': 'Natal',
}


def _norm(s: str) -> str:
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
    return s.strip().lower()


@lru_cache(maxsize=64)
def _pascoa(ano: int) -> date:
    """Domingo de Pascoa — algoritmo de Meeus/Jones/Butcher (Gauss)."""
    a = ano % 19
    b = ano // 100
    c = ano % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


@lru_cache(maxsize=256)
def _feriados_ano(ano: int, uf: str, comarca: str) -> frozenset:
    """Conjunto de datas-feriado (date) do ano para a jurisdicao dada.
    Retorna tupla (datas_certas, datas_forenses_duvidosas) via frozenset de (date, tipo)."""
    p = _pascoa(ano)
    out = {}
    # nacionais fixos
    for mmdd, nome in NACIONAIS_FIXOS.items():
        mm, dd = mmdd.split('-')
        out[date(ano, int(mm), int(dd))] = ('nacional', nome)
    # movel: Sexta-feira Santa (nacional). Carnaval e Corpus Christi sao PONTO FACULTATIVO: dia util
    # (Advogada, 03/11/2026), no fatal e no interno. So pulam com data avulsa em DATAS trazendo a normativa
    # do tribunal; sem ela, o prazo que atravessa um deles ganha AVISO (ver ponto_facultativo_nome).
    out[p - timedelta(days=2)] = ('nacional', 'Sexta-feira Santa')
    # estaduais / comarca (fixos MM-DD)
    for mmdd in _LOCAIS.get('UF', {}).get(uf, []):
        mm, dd = mmdd.split('-')
        out.setdefault(date(ano, int(mm), int(dd)), ('estadual', f'Feriado estadual {uf}'))
    if comarca:
        for mmdd in _LOCAIS.get('COMARCA', {}).get(f'{uf}:{comarca}', []):
            mm, dd = mmdd.split('-')
            out.setdefault(date(ano, int(mm), int(dd)), ('comarca', f'Feriado {comarca}/{uf}'))
    # datas avulsas de um ano especifico (ISO AAAA-MM-DD), para feriado ADIADO pelo tribunal
    # (ex.: TRT6 em 2026: Dia do Servidor transferido de 28/10 para 30/10, Portaria GP 495/2025).
    # Fica em DATAS, por UF ou 'UF:comarca', e so vale no ano gravado: nunca vira feriado fixo.
    for chave in (uf, f'{uf}:{comarca}' if comarca else ''):
        if not chave:
            continue
        for iso, nome in (_LOCAIS.get('DATAS', {}).get(chave) or {}).items():
            try:
                d = date.fromisoformat(str(iso)[:10])
            except ValueError:
                continue
            if d.year == ano:
                out.setdefault(d, ('estadual', nome or f'Feriado forense {chave} ({iso})'))
    return frozenset(out.items())


def _mapa(ano: int, uf: str, comarca: str) -> dict:
    return dict(_feriados_ano(ano, uf or '', _norm(comarca)))


def em_suspensao(d: date) -> bool:
    """Suspensao de prazos processuais: 20/12 a 20/01 (art. 775-A CLT; art. 220 CPC).
    O prazo nao corre; se a intimacao cai dentro, so comeca em 21/01."""
    if (d.month == 12 and d.day >= 20):
        return True
    if (d.month == 1 and d.day <= 20):
        return True
    return False


def eh_dia_util(d: date, uf: str | None = '', comarca: str | None = '') -> bool:
    """Dia util forense: seg-sex, nao feriado, fora da suspensao 20/12-20/01.
    `uf` None vale como vazio (so feriado nacional e movel)."""
    if d.weekday() >= 5:
        return False
    if em_suspensao(d):
        return False
    if d in _mapa(d.year, uf, comarca):
        return False
    return True


def feriado_nome(d: date, uf: str = '', comarca: str = '') -> str | None:
    info = _mapa(d.year, uf, comarca).get(d)
    return info[1] if info else None


def feriado_tipo(d: date, uf: str = '', comarca: str = '') -> str | None:
    """nacional | estadual | comarca | forense (forense = duvidoso, gera aviso)."""
    info = _mapa(d.year, uf, comarca).get(d)
    return info[0] if info else None


def proximo_dia_util(d: date, uf: str = '', comarca: str = '', incluir_hoje: bool = False) -> date:
    cur = d if incluir_hoje else d + timedelta(days=1)
    while not eh_dia_util(cur, uf, comarca):
        cur += timedelta(days=1)
    return cur


# ---- calendario unico do prazo INTERNO (fase 2, T4, 03/11/2026) --------------------
# Decisoes da Advogada (doc vivo, secao 10, Geral, 03/11): a regua D-2 usa so os feriados nacionais (fixos e
# Sexta-feira Santa); estaduais e municipais ficam fora, 28/10 e 08/12 nao contam. Carnaval e Corpus Christi sao
# ponto facultativo e NAO sao feriado do interno: contam como dia util no interno e no fatal (prazos processuais,
# melhor pecar pelo excesso), e so PULAM na promessa ao cliente (/prazo-inicial). O prazo PROCESSUAL (fatal) segue
# com uf/comarca e as datas avulsas do tribunal (eh_dia_util acima). O interno e data de casa: fim de semana e
# feriado nacional. Ferias coletivas da equipe (FERIAS_EQUIPE): as etapas que cairiam nelas sao divididas para
# DEPOIS da volta (ajustar_cadeia_ferias), sempre antes do fatal; so se nao couber voltam para antes do periodo.

def _como_data(v) -> date | None:
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def ponto_facultativo_nome(d, uf: str | None = '', comarca: str | None = '') -> str | None:
    """Nome do ponto facultativo (Carnaval segunda/terca, Corpus Christi) quando a data e um deles e NAO
    ha data avulsa cadastrada: a contagem trata como dia util e avisa para conferir se o tribunal suspendeu."""
    d = _como_data(d)
    if d is None or d.weekday() >= 5 or d in _mapa(d.year, uf, comarca):
        return None
    p = _pascoa(d.year)
    return {p - timedelta(days=48): 'Carnaval (segunda)', p - timedelta(days=47): 'Carnaval (terca)',
            p + timedelta(days=60): 'Corpus Christi'}.get(d)


def ferias_equipe(d) -> dict | None:
    """Ferias coletivas da equipe que cobrem a data (cadastro FERIAS_EQUIPE em feriados_locais.json, por
    ano: {"inicio","fim","descricao"}). So vale para o prazo INTERNO; o fatal e do tribunal."""
    d = _como_data(d)
    if d is None:
        return None
    for per in _LOCAIS.get('FERIAS_EQUIPE') or []:
        ini, fim = _como_data(per.get('inicio')), _como_data(per.get('fim'))
        if ini and fim and ini <= d <= fim:
            return per
    return None


def dia_util_interno(d) -> bool:
    """Dia util para o prazo interno: seg-sex e fora dos feriados nacionais."""
    d = _como_data(d)
    if d is None:
        return False
    return d.weekday() < 5 and d not in _mapa(d.year, '', '')


def uteis_antes_cru(d, n: int) -> date | None:
    """`n` dias uteis (calendario do interno) antes de `d`, SEM olhar as ferias da equipe. Base das contas
    D-n da cadeia; quem aplica a regra das ferias e ajustar_cadeia_ferias."""
    d = _como_data(d)
    if d is None:
        return None
    faltam = int(n)
    while faltam > 0:
        d -= timedelta(days=1)
        if dia_util_interno(d):
            faltam -= 1
    return d


def _proximo_util_interno(d: date) -> date:
    d += timedelta(days=1)
    while not dia_util_interno(d):
        d += timedelta(days=1)
    return d


def _anterior_util_interno(d: date) -> date:
    d -= timedelta(days=1)
    while not dia_util_interno(d):
        d -= timedelta(days=1)
    return d


def _ferias_cadastradas() -> list:
    out = []
    for per in _LOCAIS.get('FERIAS_EQUIPE') or []:
        ini, fim = _como_data(per.get('inicio')), _como_data(per.get('fim'))
        if ini and fim and ini <= fim:
            out.append((ini, fim))
    return sorted(out)


def ajustar_cadeia_ferias_com_aviso(datas, fatal=None, piso=None):
    """Regra das ferias coletivas da equipe para uma cadeia de datas (Advogada, 03/11/2026): "podemos dividir os
    prazos, como estarao suspensos, para o retorno da coletiva". `datas` e a lista em ordem crescente (peca,
    revisao, ajustes, protocolo; o interno e a ultima). As que caem no periodo vao para DEPOIS da volta (primeiro
    dia util depois do fim), um dia util entre elas e na mesma ordem, com a ultima (o interno) pelo menos 2 dias
    uteis ANTES do fatal ("manter os 2 dias", Advogada, 03/11); se a ultima nao couber
    antes do fatal, a cadeia volta para ANTES do inicio do periodo (ultimo dia util antes dele), escalonada para
    tras, um dia util entre as etapas. As que nao caem no periodo nao mudam; sem cadastro nada muda; o fatal nunca
    muda. `piso` (hoje) vale depois. Se o piso cai nas ferias, so sobe para a volta quando a volta (com a cadeia
    escalonada, um dia util entre as etapas) couber com a folga de 2 dias uteis antes do fatal; se nao couber, fica em hoje. Nenhuma etapa fica
    igual ou depois do fatal por causa do piso. Devolve (lista, aviso): o aviso so existe quando nem hoje cabe
    antes do fatal (fatal no dia ou ja vencido); as datas ficam em hoje e o chamador deve avisar a equipe."""
    out = [_como_data(x) for x in datas]
    fatal = _como_data(fatal)
    for ini, fim in _ferias_cadastradas():
        if not any(x and ini <= x <= fim for x in out):
            continue
        depois, prev = [], None
        for x in out:
            if x is None:
                depois.append(None)
                continue
            cand = _proximo_util_interno(fim) if ini <= x <= fim else x
            if prev is not None and cand <= prev:
                cand = _proximo_util_interno(prev)
            depois.append(cand)
            prev = cand
        ultimo = [x for x in depois if x][-1]
        if fatal is None or ultimo <= uteis_antes_cru(fatal, 2):
            out = depois
            continue
        antes, nxt = [None] * len(out), None
        for i in range(len(out) - 1, -1, -1):
            x = out[i]
            if x is None:
                continue
            cap = _anterior_util_interno(ini) if nxt is None else _anterior_util_interno(nxt)
            antes[i] = min(x, cap)
            nxt = antes[i]
        out = antes
    piso = _como_data(piso)
    aviso = None
    if piso is not None:
        sobe = None
        for ini, fim in _ferias_cadastradas():
            if ini <= piso <= fim:
                volta, esc, prev = _proximo_util_interno(fim), [], None
                for x in out:
                    if x is None:
                        esc.append(None)
                        continue
                    cand = max(x, volta)
                    if prev is not None and cand <= prev:
                        cand = _proximo_util_interno(prev)
                    esc.append(cand)
                    prev = cand
                ult = [x for x in esc if x][-1:]
                if fatal is None or (ult and ult[0] <= uteis_antes_cru(fatal, 2)):
                    sobe = esc
        out = sobe if sobe is not None else [max(x, piso) if x else x for x in out]
        if fatal is not None and any(x and x >= fatal for x in out):
            aviso = ('Fatal ja vencido ou no dia (%s): as etapas ficaram na data de hoje; confira o prazo.'
                     % fatal.strftime('%d/%m/%Y'))
    return out, aviso


def ajustar_cadeia_ferias(datas, fatal=None, piso=None) -> list:
    """Ver ajustar_cadeia_ferias_com_aviso; esta devolve so a lista."""
    return ajustar_cadeia_ferias_com_aviso(datas, fatal, piso)[0]


def prazo_interno(fatal, dias: int = 2) -> date | None:
    """O prazo interno (PFI): `dias` dias uteis (calendario do interno) ANTES do fatal.
    Aceita date ou texto AAAA-MM-DD; sem fatal devolve None. Todas as contas de D-n do sistema passam
    por aqui (motor_prazos, motor_tarefas, tarefas_prazo, /prazo-inicial e a liquidacao da conferencia).
    Ferias coletivas da equipe (FERIAS_EQUIPE): com dias=2 o interno e a ULTIMA data da cadeia de quatro etapas
    (peca, revisao, ajustes, protocolo) e segue ajustar_cadeia_ferias, o mesmo que o motor de tarefas aplica;
    com outro numero de dias, vale a mesma regra para a data sozinha."""
    f = _como_data(fatal)
    if f is None:
        return None
    dias = int(dias)
    if dias <= 0:
        return f
    if dias == 2:
        cadeia = [uteis_antes_cru(f, n) for n in (5, 4, 3, 2)]
        return ajustar_cadeia_ferias(cadeia, f)[-1]
    return ajustar_cadeia_ferias([uteis_antes_cru(f, dias)], f)[0]


def dia_sem_expediente(d, uf: str | None = '', comarca: str | None = '') -> bool:
    """True quando a data cai em dia sem expediente forense (fim de semana, feriado, recesso).
    Base da regra "o sistema nao aceita data em dia sem expediente" (fase 5). Data ausente ou
    ilegivel nao e dia sem expediente: quem valida o preenchimento e outra regra."""
    dt = _como_data(d)
    if dt is None:
        return False
    return not eh_dia_util(dt, uf, comarca)
