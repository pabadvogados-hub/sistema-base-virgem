# -*- coding: utf-8 -*-
"""Achar o cliente pelo telefone, do jeito que a pessoa digita.

07/11/2026, caso do Galdino: ela fechou com ele, digitou o numero na busca e o
sistema disse que a conversa nao existia. Existia, com 65 mensagens, desde 03/11.

O MOTIVO
O WhatsApp entrega numero brasileiro antigo SEM o nono digito: o card ficou gravado como
`557399812345`, e o numero que esta na agenda de todo mundo e `73 99981-2345`. A busca
procurava o numero inteiro dentro do que estava gravado, e `557399812345` nao contem
`73999812345`. Dava zero.

O que tornava isso um defeito, e nao um acaso: o RECEBIMENTO de mensagem ja era tolerante.
Quando o cliente escreve, `atendimento.py` casa pelos ULTIMOS 8 DIGITOS (linhas 339 e 435) e
acha o card dele certinho. So a busca da tela era mais exigente que o recebimento. A mesma
casa com duas reguas.

POR QUE VARIANTE E NAO "ULTIMOS 8"
Casar so pelos ultimos 8 acharia tambem o cliente de OUTRO DDD com final igual: `11 99981-2345`
apareceria na busca de `73 99981-2345`. Numa busca isso e so ruido, mas ruido numa lista de
cliente e como a equipe aprende a nao confiar na busca. Entao aqui se geram as formas que
AQUELE numero pode ter (com e sem o 9, com e sem o 55) e se procura por elas.

Medido em 07/11/2026: 2.113 cards estao gravados sem o nono digito, e 62 deles sao cliente
fechado.
"""
import re

DDI = '55'


def so_digitos(s):
    return re.sub(r'\D', '', s or '')


def variantes(termo):
    """As formas que este numero pode estar gravado. Devolve lista ordenada, sem repetir.

    >>> variantes('73999812345')
    ['5573999812345', '557399812345', '7399812345', '73999812345']

    O mesmo numero digitado de qualquer jeito gera o mesmo conjunto, entao achar o cliente
    nao depende de a pessoa lembrar se o WhatsApp dele tem o 9 ou nao.
    """
    d = so_digitos(termo)
    if len(d) < 3:
        return []
    saida = {d}

    # o nucleo e DDD + numero, sem o codigo do pais
    nucleo = d[2:] if (d.startswith(DDI) and len(d) >= 12) else d
    if len(nucleo) >= 10:
        ddd, resto = nucleo[:2], nucleo[2:]
        if len(resto) == 9 and resto.startswith('9'):
            outro = ddd + resto[1:]            # tira o nono digito
        elif len(resto) == 8:
            outro = ddd + '9' + resto          # poe o nono digito
        else:
            outro = None
        for n in (nucleo, outro):
            if n:
                saida.add(n)
                saida.add(DDI + n)
    return sorted(saida)


def padroes_like(termo):
    """Os padroes de LIKE para procurar no banco, um por variante."""
    return ['%' + v + '%' for v in variantes(termo)]
