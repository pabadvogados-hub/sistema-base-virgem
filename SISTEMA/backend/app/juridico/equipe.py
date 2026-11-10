# -*- coding: utf-8 -*-
"""Equipe do escritorio para o motor das tarefas, lida de juridico/equipe.json.

EQUIPE: chave (minuscula, sem acento) -> {'id', 'nome'}; inclui pessoas, papeis e apelidos.
pessoa_do_papel('revisora') -> {'id','nome'}. Ids podem vir do .env: ESC_USUARIO_ADVOGADA_ID etc.
"""
import json
import os
from pathlib import Path

ARQ = Path(__file__).resolve().parent / 'equipe.json'


def _carregar():
    try:
        cfg = json.loads(ARQ.read_text(encoding='utf-8'))
    except Exception:
        cfg = {'pessoas': {'titular': {'id': 1, 'nome': 'Titular'}, 'advogada': {'id': 2, 'nome': 'Advogada'},
                           'automacao': {'id': 3, 'nome': 'Assistente IA (automacao)'}},
               'papeis': {}, 'apelidos': {}}
    pessoas = {}
    for k, v in (cfg.get('pessoas') or {}).items():
        p = dict(v)
        env = os.getenv('ESC_USUARIO_%s_ID' % k.upper())
        if env and env.lstrip('-').isdigit():
            p['id'] = int(env)
        pessoas[k] = p
    equipe = dict(pessoas)
    for grupo in ('papeis', 'apelidos'):
        for k, alvo in (cfg.get(grupo) or {}).items():
            if alvo in pessoas:
                equipe.setdefault(k, pessoas[alvo])
    return pessoas, equipe, cfg.get('papeis') or {}


PESSOAS, EQUIPE, PAPEIS = _carregar()


def pessoa_do_papel(papel):
    chave = PAPEIS.get(papel, papel)
    return dict(PESSOAS.get(chave) or EQUIPE.get(chave) or {})


def primeiro_nome(papel):
    return (pessoa_do_papel(papel).get('nome') or '').split(' ')[0]
