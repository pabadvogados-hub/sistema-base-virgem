# -*- coding: utf-8 -*-
"""Um card por contato: a trava que impede dois webhooks de criarem o mesmo cliente duas vezes.

O DEFEITO, medido em 07/11/2026
185 números têm mais de um card no CRM, e **todos os 185 pares nasceram com menos de um minuto
de diferença**, a maioria com zero segundos. A mesma primeira mensagem do cliente aparece nos
dois cards. Cinco desses pares são de cliente FECHADO (Haroldo, Dimas Natan, Nivaldo, Ulisses
Correa, Otoniel): a vendedora atende num card enquanto a esteira anda no outro, e o cliente
pode receber a mesma coisa duas vezes.

A CAUSA
O webhook faz "procura o card; se não achar, cria". Entre procurar e criar há `await`, e o
WhatsApp entrega duas mensagens juntas no primeiro contato (é comum: a saudação e a frase do
anúncio). As duas requisições procuram, nenhuma acha, e as duas criam.

Já havia um remendo de 29/09/2026 no código: "se o contato ficou com 2 conversas (2 webhooks
juntos na 1a mensagem), pega a mais recente em vez de estourar MultipleResultsFound". Trataram
o efeito, e a causa continuou produzindo um par por semana.

POR QUE UM LOCK EM MEMÓRIA RESOLVE DE VERDADE AQUI
O serviço roda com `--workers 1` (um processo só, asyncio). Não há dois processos competindo:
a corrida é entre corrotinas do mesmo processo. Então uma trava por telefone, em memória,
fecha a porta inteira. Se um dia o serviço passar a rodar com mais de um worker, esta trava
deixa de bastar e o caminho passa a ser um índice único no banco; o teste
`test_a_trava_so_vale_dentro_de_um_processo` guarda esse aviso.
"""
import asyncio

# Quantas travas guardar antes de faxinar. Cada uma é um objeto minúsculo; o limite existe só
# para a memória não crescer sem fim num processo que fica meses no ar.
TETO = 5000

_travas = {}


def chave(numero_id, telefone):
    """A identidade do contato para efeito de trava: a linha e os dígitos do telefone.

    Usa os ÚLTIMOS 8 DÍGITOS, igual ao que a busca do webhook faz logo abaixo: o WhatsApp
    manda o mesmo contato ora com o nono dígito, ora sem, e as duas formas têm que cair na
    mesma trava, senão a corrida continua aberta justamente no caso que a causou.
    """
    d = ''.join(c for c in str(telefone or '') if c.isdigit())
    nucleo = d[-8:] if len(d) >= 8 else (d or str(telefone or ''))
    return '%s:%s' % (numero_id, nucleo)


def travar(numero_id, telefone):
    """A trava deste contato nesta linha. Mesmo contato devolve sempre a mesma trava."""
    k = chave(numero_id, telefone)
    lk = _travas.get(k)
    if lk is None:
        if len(_travas) >= TETO:
            faxinar()
        lk = _travas[k] = asyncio.Lock()
    return lk


def faxinar():
    """Joga fora as travas que ninguém está usando. Nunca tira uma trava em uso."""
    livres = [k for k, v in list(_travas.items()) if not v.locked()]
    for k in livres[:max(1, len(livres) // 2)]:
        _travas.pop(k, None)
    return len(livres)


def quantas():
    return len(_travas)
