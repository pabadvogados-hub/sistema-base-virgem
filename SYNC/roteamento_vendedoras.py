# -*- coding: utf-8 -*-
"""Quem do comercial recebe o LIGAR AGORA de um lead (e a divisao da fila diaria).

A equipe vem do .env, sem nada fixo no codigo:

    VENDEDORES="Nome:email:telefone:meta:9-18, Outro:email2:5511...:5:14-20"

Campos (so o nome e obrigatorio): nome, e-mail de login no sistema, telefone com DDI para
receber o aviso no WhatsApp, meta de contratos no mes (padrao 10) e horario de trabalho em dias
uteis (padrao 9-18). Vazio = nenhum vendedor: quem_recebe_demitido() devolve None e quem chama
cai no aviso geral (QUENTES_AVISO_TEL), sem quebrar.

Regra: entre os vendedores em horario agora, vai para quem NAO recebeu o ultimo (rodizio).
Fora do horario de todos, vai para o primeiro da lista, que pega logo cedo.
Usado pelo fluxo "voce ja saiu?", pelo alarme de qualificado e pela fila do comercial.
"""
import json
import os
from datetime import datetime, timedelta

try:
    import fcntl
except ImportError:            # Windows: sem trava de arquivo (rodar os robos em uma maquina so)
    fcntl = None

HERE = os.path.dirname(os.path.abspath(__file__))
ESTADO = os.path.join(HERE, 'roteamento_vendedoras_state.json')
DIAS_UTEIS = (0, 1, 2, 3, 4)


def _carregar():
    out = {}
    if not (os.getenv('VENDEDORES') or '').strip():
        # reserva: VENDEDORES_EMAILS="email:id_usuario:nome,..." (formato da frente de tarefas)
        for item in (os.getenv('VENDEDORES_EMAILS') or '').split(','):
            p = [x.strip() for x in item.split(':')]
            if len(p) >= 3 and '@' in p[0] and p[2]:
                out[p[2]] = {'nome': p[2], 'email': p[0].lower(), 'tel': '', 'meta': 10,
                             'dias': DIAS_UTEIS, 'horas': (9, 18), 'hora_fila': 9}
        return out
    for item in (os.getenv('VENDEDORES') or '').split(','):
        p = [x.strip() for x in item.split(':')]
        if not p or not p[0]:
            continue
        nome = p[0]
        email = p[1].lower() if len(p) > 1 else ''
        tel = ''.join(ch for ch in (p[2] if len(p) > 2 else '') if ch.isdigit())
        try:
            meta = int(p[3]) if len(p) > 3 and p[3] else 10
        except ValueError:
            meta = 10
        horas = (9, 18)
        if len(p) > 4 and '-' in p[4]:
            try:
                a, b = p[4].split('-', 1)
                horas = (int(a), int(b))
            except ValueError:
                pass
        out[nome] = {'nome': nome, 'email': email, 'tel': tel, 'meta': meta,
                     'dias': DIAS_UTEIS, 'horas': horas, 'hora_fila': horas[0]}
    return out


VENDEDORAS = _carregar()
EMAILS = {v['email']: n for n, v in VENDEDORAS.items() if v['email']}


def agora_br():
    return datetime.utcnow() - timedelta(hours=3)


def _em_horario(v, a):
    return a.weekday() in v['dias'] and v['horas'][0] <= a.hour < v['horas'][1]


def quem_recebe_demitido(agora=None, registrar=True):
    """Dict do vendedor que recebe este lead (rodizio entre quem esta em horario), ou None."""
    if not VENDEDORAS:
        return None
    a = agora or agora_br()
    lista = list(VENDEDORAS.values())
    ativos = [v for v in lista if _em_horario(v, a)]
    if not ativos:
        return lista[0]
    if len(ativos) == 1 or not registrar:
        return ativos[0]
    trava = open(ESTADO + '.lock', 'w')
    try:
        if fcntl:
            fcntl.flock(trava, fcntl.LOCK_EX)
        try:
            with open(ESTADO, encoding='utf-8') as f:
                est = json.load(f)
        except Exception:
            est = {}
        ultimo = est.get('ultimo')
        nomes = [v['nome'] for v in ativos]
        i = (nomes.index(ultimo) + 1) % len(ativos) if ultimo in nomes else 0
        vez = ativos[i]
        est['ultimo'] = vez['nome']
        est.setdefault('historico', []).append({'em': a.strftime('%Y-%m-%d %H:%M'), 'para': vez['nome']})
        est['historico'] = est['historico'][-200:]
        tmp = ESTADO + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(est, f, ensure_ascii=False, indent=1)
        os.replace(tmp, ESTADO)
    finally:
        if fcntl:
            fcntl.flock(trava, fcntl.LOCK_UN)
        trava.close()
    return vez


def bloco_da_fila(nome, total):
    """Quantos leads da fila diaria vao para cada um, na proporcao da meta."""
    soma = sum(v['meta'] for v in VENDEDORAS.values())
    if not soma:
        return 0
    partes = {n: int(round(total * v['meta'] / soma)) for n, v in VENDEDORAS.items()}
    return partes.get(nome, 0)


if __name__ == '__main__':
    print('vendedores:', list(VENDEDORAS) or '(nenhum: defina VENDEDORES no .env)')
    base = agora_br().replace(minute=0)
    for h in (9, 14, 19, 22):
        v = quem_recebe_demitido(base.replace(hour=h), registrar=False)
        print('%02dh ->' % h, v['nome'] if v else None)
