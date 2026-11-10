# -*- coding: utf-8 -*-
"""Contatos e enderecos do escritorio, SEMPRE vindos do config/.env (nenhum valor padrao no codigo).

Tudo devolve vazio quando nao configurado: o robo que dependia do dado so registra e segue,
nunca manda e-mail/WhatsApp para um endereco inventado.

Variaveis (ver SISTEMA/backend/ENV_FRENTE_B.md):
  AGENDA_EMAIL_PRINCIPAL   conta Google da agenda central (o token do Calendar e desta conta)
  AGENDA_CONVIDADOS        e-mails convidados em todo prazo fatal, separados por virgula
  EQUIPE_EMAILS            "nome:email:papel:trilha,..." (trilha PECA/MEIO opcional), equipe convidavel pela tela
  REVISORA_EMAIL           e-mail da Dra. Advogada (revisora/dona das tarefas)
  TITULAR_EMAIL            e-mail do Dr. Titular
  CAIXA_PECAS_EMAIL        caixa de e-mail lida/enviada pelo assistente juridico (Gmail API)
  TEL_ALERTA_EQUIPE        WhatsApp (5511...) que recebe os alertas dos robos
  TEL_REVISORA             WhatsApp da Dra. Advogada (relatorio semanal de audiencias)
  VENDEDORES_EMAILS        "email:id_usuario:nome,..." dono de card do comercial
  SITE_URL                 site do escritorio (kit de boas-vindas)
  GOOGLE_PASTA_RECLAMANTE  id da pasta RECLAMANTE no Drive do escritorio
"""
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
_ENV = RAIZ / 'config' / '.env'
_CACHE = None


def _arquivo():
    global _CACHE
    if _CACHE is None:
        _CACHE = {}
        try:
            for linha in _ENV.read_text(encoding='utf-8').splitlines():
                linha = linha.strip()
                if linha and not linha.startswith('#') and '=' in linha:
                    k, v = linha.split('=', 1)
                    _CACHE[k.strip()] = v.strip().strip('"').strip("'")
        except OSError:
            pass
    return _CACHE


def valor(nome, padrao=''):
    v = os.environ.get(nome)
    if v is None:
        v = _arquivo().get(nome, padrao)
    return (v or '').strip()


def lista(nome):
    return [x.strip() for x in valor(nome).split(',') if x.strip()]


def equipe():
    """[{'nome','email','papel','trilha','padrao'}] a partir de EQUIPE_EMAILS."""
    out = []
    for item in lista('EQUIPE_EMAILS'):
        p = (item.split(':') + ['', '', '', ''])[:4]
        if '@' in p[1]:
            out.append({'nome': p[0], 'email': p[1], 'papel': p[2], 'trilha': (p[3] or None) and p[3].upper(),
                        'padrao': p[1] in lista('AGENDA_CONVIDADOS')})
    return out


def vendedores():
    """{email: (id_usuario, nome)} a partir de VENDEDORES_EMAILS."""
    out = {}
    for item in lista('VENDEDORES_EMAILS'):
        p = (item.split(':') + ['', '', ''])[:3]
        if '@' in p[0]:
            out[p[0].lower()] = (int(p[1]) if p[1].isdigit() else None, p[2] or p[0])
    return out


AGENDA_EMAIL_PRINCIPAL = valor('AGENDA_EMAIL_PRINCIPAL')
REVISORA_EMAIL = valor('REVISORA_EMAIL')
TITULAR_EMAIL = valor('TITULAR_EMAIL')
TEL_ALERTA_EQUIPE = valor('TEL_ALERTA_EQUIPE')
GOOGLE_PASTA_RECLAMANTE = valor('GOOGLE_PASTA_RECLAMANTE')
