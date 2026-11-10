# -*- coding: utf-8 -*-
"""Adaptador do SISTEMA JURIDICO externo do escritorio.

O banco do proprio sistema e a fonte da verdade (clientes, processos, tarefas, agenda). Um
sistema juridico externo, quando houver, so recebe copia. Escolha por .env:

    SISTEMA_JURIDICO=nenhum   (padrao) nada sai do sistema; cada chamada so registra no log
    SISTEMA_JURIDICO=astrea   Astrea: depende da API liberada no plano contratado (pendente)

Interface unica para os robos:
    criar_tarefa(processo, tipo, responsavel, texto, data=None, **extra) -> dict
    lancar_andamento(processo, texto, data=None, **extra)              -> dict
    buscar_processo(numero)                                             -> dict | None

PUBLICACOES (entrada das intimacoes), tambem por .env:
    ESC_FONTE_PUBLICACOES=djen        DJEN/Comunica PJe pela OAB (padrao; ja roda no SYNC)
    ESC_FONTE_PUBLICACOES=snc         SNC: esqueleto, pendente de credencial (SNC_API_URL, SNC_TOKEN)
    ESC_FONTE_PUBLICACOES=djen,snc    as duas
"""
import logging
import os

log = logging.getLogger('sistema_juridico')


def _escolha():
    return (os.getenv('SISTEMA_JURIDICO') or 'nenhum').strip().lower()


class _Nenhum:
    nome = 'nenhum'

    def criar_tarefa(self, processo, tipo, responsavel, texto, data=None, **extra):
        log.info('[sistema_juridico=nenhum] tarefa %s / %s / %s (fica so no sistema)', processo, tipo, responsavel)
        return {'ok': True, 'externo': None, 'motivo': 'SISTEMA_JURIDICO=nenhum'}

    def lancar_andamento(self, processo, texto, data=None, **extra):
        log.info('[sistema_juridico=nenhum] andamento %s (fica so no sistema)', processo)
        return {'ok': True, 'externo': None, 'motivo': 'SISTEMA_JURIDICO=nenhum'}

    def buscar_processo(self, numero):
        return None


class _Astrea(_Nenhum):
    nome = 'astrea'
    MSG = ('Integracao com o Astrea pendente: depende da API liberada no plano contratado pelo escritorio '
           '(confirmar com o suporte do Astrea). Enquanto isso use SISTEMA_JURIDICO=nenhum: tudo segue no sistema.')

    def criar_tarefa(self, *a, **k):
        raise NotImplementedError(self.MSG)

    def lancar_andamento(self, *a, **k):
        raise NotImplementedError(self.MSG)

    def buscar_processo(self, numero):
        raise NotImplementedError(self.MSG)


def atual():
    return _Astrea() if _escolha() == 'astrea' else _Nenhum()


def criar_tarefa(processo, tipo, responsavel, texto, data=None, **extra):
    return atual().criar_tarefa(processo, tipo, responsavel, texto, data, **extra)


def lancar_andamento(processo, texto, data=None, **extra):
    return atual().lancar_andamento(processo, texto, data, **extra)


def buscar_processo(numero):
    return atual().buscar_processo(numero)


# ------------------------------------------------------------------ publicacoes
def fontes_publicacoes():
    return [f.strip().lower() for f in (os.getenv('ESC_FONTE_PUBLICACOES') or 'djen').split(',') if f.strip()]


def buscar_publicacoes_snc(desde=None):
    """SNC (publicacoes e autos). Esqueleto: liga quando houver SNC_API_URL e SNC_TOKEN e o contrato da API."""
    url, tok = os.getenv('SNC_API_URL'), os.getenv('SNC_TOKEN')
    if not (url and tok):
        log.info('SNC sem credencial (SNC_API_URL/SNC_TOKEN): nada a buscar')
        return []
    raise NotImplementedError('SNC: credencial presente, mas o formato da API ainda nao foi mapeado '
                              '(pedir a documentacao ao SNC e implementar aqui).')


if __name__ == '__main__':
    os.environ['SISTEMA_JURIDICO'] = 'nenhum'
    assert criar_tarefa('1', 'PROTOCOLO', 'Advogada', 'x')['ok'] and buscar_processo('1') is None
    os.environ['SISTEMA_JURIDICO'] = 'astrea'
    try:
        criar_tarefa('1', 'PROTOCOLO', 'Advogada', 'x')
        raise SystemExit('astrea deveria levantar NotImplementedError')
    except NotImplementedError as e:
        assert 'Astrea' in str(e)
    os.environ.pop('SNC_TOKEN', None)
    assert buscar_publicacoes_snc() == [] and fontes_publicacoes() == ['djen']
    print('ok')
