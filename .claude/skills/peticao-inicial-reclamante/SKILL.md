---
name: peticao-inicial-reclamante
description: Redige a petição inicial trabalhista do reclamante (TRT-2) a partir da ficha, da transcrição e dos documentos do cliente.
---

# peticao-inicial-reclamante

A estrutura segue a inicial-modelo descrita no DNA:
1. qualificação completa do reclamante;
2. "por suas advogadas ... com fundamento no art. 840, §1º da CLT, propor a presente";
3. `RECLAMAÇÃO TRABALHISTA`;
4. "Em face de", com cada reclamada;
5. tópicos preliminares cabíveis: grupo econômico, responsabilidade solidária ou subsidiária,
   JUÍZO 100% DIGITAL, segredo de justiça, justiça gratuita;
6. mérito, um tópico por direito, cada um com fato, fundamento e pedido;
7. NÃO LIMITAÇÃO DOS VALORES DA INICIAL;
8. juros e correção;
9. honorários de 15% (art. 791-A);
10. DOS PEDIDOS em alíneas, com valor;
11. valor da causa por extenso.

Os valores saem da skill `liquidacao-previa`.

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
