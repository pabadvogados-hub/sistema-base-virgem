# -*- coding: utf-8 -*-
"""COMPATIBILIDADE: o escritorio NAO usa ADVBOX.

O nucleo do sistema herdou chamadas a um sistema juridico externo (espelhos de tarefas, processos e
financeiro). Este modulo existe so para essas chamadas nao derrubarem nenhuma tela: toda funcao
devolve vazio ({}), registra no log e nao sai da maquina. O caminho oficial para integrar um
sistema juridico e INTEGRACOES/sistema_juridico.py (SISTEMA_JURIDICO=nenhum|astrea).
"""
import logging

log = logging.getLogger('sistema_juridico.compat')
ATIVO = False


class _Vazio(dict):
    """{} que tambem aceita d['data'], .get('data') e iteracao: serve de retorno para qualquer chamada."""

    def __missing__(self, chave):
        return _Vazio()


def _noop(nome):
    def f(*a, **k):
        log.info('chamada %s ignorada: o escritorio nao usa sistema juridico externo', nome)
        return _Vazio()
    f.__name__ = nome
    return f


def __getattr__(nome):
    if nome.startswith('__'):
        raise AttributeError(nome)
    return _noop(nome)


buscar_cliente = _noop('buscar_cliente')
buscar_processo = _noop('buscar_processo')


def criar_publicacao(lawsuit_id, task_id, guest_ids, comments='', from_id=None, date_deadline=None, urgent=False,
                     start_date=None, **_):
    """A tarefa nasce no sistema (tarefas_esc), nunca fora."""
    import tarefas_esc
    return tarefas_esc.criar_como_advbox(lawsuit_id, task_id, guest_ids, comments, from_id, date_deadline, urgent,
                                        start_date)
listar_tarefas = _noop('listar_tarefas')
