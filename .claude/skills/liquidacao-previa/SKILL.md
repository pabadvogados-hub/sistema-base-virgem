---
name: liquidacao-previa
description: Calcula a liquidação prévia dos pedidos (memória de cálculo) para inicial, acordo ou execução.
---

# liquidacao-previa

Entregue `saida/MEMORIA_CALCULO.md` (ou .xlsx) com, para cada pedido, a base, o período, a
fórmula, os reflexos e o total, além do somatório. Use o último salário, a jornada e as datas da
ficha; o que faltar vira [PREENCHER]. Não invente índice: correção e juros seguem o critério
vigente, indicado por escrito. Na execução provisória, a planilha acompanha o requerimento (arts.
876 e 878 da CLT).

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
