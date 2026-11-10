---
name: embargos-declaracao
description: Redige embargos de declaração (omissão, contradição, obscuridade) contra sentença ou acórdão.
---

# embargos-declaracao

Fundamento: art. 897-A da CLT e art. 1.022 do CPC. Abra um tópico por vício ("DA OMISSÃO QUANTO
AO ..."), transcrevendo o pedido da inicial que ficou sem resposta e o trecho da decisão. Feche em
DOS REQUERIMENTOS, pedindo que o vício seja sanado e conste do dispositivo para fins de liquidação.
ED por omissão evidente é petição simples e pode sair direto.

## Antes de escrever
1. Leia INTEIRO o `SISTEMA/backend/app/juridico/DNA_ESCRITORIO.md` (na raiz do repositório do sistema; no servidor, `$ESC_RAIZ/SISTEMA/backend/app/juridico/DNA_ESCRITORIO.md`).
   Ele define a formatação, a abertura ("por suas advogadas"), os títulos DA/DO, o fecho
   ("Termos em que, pede deferimento."), a cidade e data e o bloco de assinatura.
2. Leia todos os insumos da pasta do caso: autos, decisão, defesa e documentos. Imagem e PDF
   escaneado se leem por visão.

## Regras inegociáveis
- Nunca invente jurisprudência. Precedente que não esteja no DNA nem nos autos vira `[CONFERIR PRECEDENTE]`.
- Nunca invente dado do cliente ou do processo. O que faltar vira `[PREENCHER: campo]`.
- Valores sempre com "R$" e conferidos (a soma dos pedidos tem de bater com o valor indicado).
- Rebata a peça contrária ponto a ponto: o que ela diz e por que não se sustenta.
- Regra de ouro: caso simples e padronizado e petição de andamento podem sair direto. O resto
  termina com `STATUS: AGUARDA REVISÃO DA DRA. ADVOGADA`.

## Entrega
- Monte o .docx no timbrado com a skill `timbrado`:
  `python .claude/skills/timbrado/montar_peca.py --texto saida/PECA.txt --saida "saida/<NOME>.docx"`
- Registre em `saida/RESUMO.txt`, em até 5 linhas, a peça, as teses usadas e os pontos que pedem
  conferência.
