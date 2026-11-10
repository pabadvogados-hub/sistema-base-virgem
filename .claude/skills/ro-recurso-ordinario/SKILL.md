---
name: ro-recurso-ordinario
description: Redige o recurso ordinário do reclamante ao TRT da 2ª Região.
---

# ro-recurso-ordinario

São duas partes.

1. Interposição, endereçada à vara: "com fundamento no art. 895, inciso I, da CLT, interpor" →
   `RECURSO ORDINÁRIO` → tempestividade, justiça gratuita, pedido de remessa ao TRT-2, fecho e
   assinatura.
2. Razões, com o cabeçalho EGRÉGIO TRIBUNAL ... / RECORRENTE / RECORRIDA / PROCESSO / ORIGEM /
   RAZÕES DE RECURSO ORDINÁRIO. Abra um tópico por capítulo da sentença que se ataca (majoração do
   dano moral, adicional convencional, honorários de 10% para 15% etc.). Feche com DOS PEDIDOS,
   pedindo a reforma de cada capítulo e a readequação da condenação.

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
