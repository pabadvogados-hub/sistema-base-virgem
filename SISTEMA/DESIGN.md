# DESIGN.md — Portal do Cliente Escritório Modelo

Sistema visual do portal em `sistema.escritorio.adv.br/portal`. Referência de estilo: Stripe (estrutura),
adaptada para leitura de celular por público de 45 a 60 anos. Nunca copiar marca, nome ou texto da referência.

Paleta da marca: **bordô e dourado** (identidade enviada pelo escritório). Os valores abaixo são a base herdada do
produto e devem ser conferidos com a tela no ar. **Nada de mudança visual global sem o visto do Dr. Titular e da Dra. Advogada.**

## Cor

As cores são as que já estavam no ar. A única adição é o dourado, que passa a ter três papéis
em vez dos sete tons soltos de antes.

| Token | Hex | Papel |
|---|---|---|
| `--tinta` | `#4e0a1a` | Texto principal e faixa de estado (bordô) |
| `--apoio` | `#5a6478` | Texto secundário e procedência |
| `--papel` | `#f5f3ee` | Fundo de tela |
| `--superficie` | `#ffffff` | Só onde o cliente age |
| `--ouro` | `#A97A12` | Acento único: caminho percorrido, foco, destaque |
| `--ouro-tinta` | `#7A5200` | Dourado quando é texto (6,92:1 no branco) |
| `--verde` | `#128C7E` | Só sinal de canal humano |
| `--fio` | `#e7e3da` | Divisor decorativo |
| `--fio-forte` | `#8f897a` | Contorno de campo e componente (3,48:1 no branco) |
| `--ouro-faixa` | `#D9A441` | Dourado sobre a faixa bordô (6,90:1) |
| `--texto-corpo` | `#3f4859` | Corpo de texto sobre papel (8,30:1) |

Regra fixa no produto inteiro: **ouro é caminho, verde é gente.** Nenhuma outra cor carrega significado.

## Tipografia

Uma família só: **Geist**. Sem segunda fonte e sem monoespaçada — número usa
`font-variant-numeric: tabular-nums` na própria Geist.

| Papel | px | Entrelinha | Peso | Tracking |
|---|---|---|---|---|
| Fato principal da tela | 34 | 1.15 | 600 | −0.02em |
| Nome da outra parte | 27 | 1.2 | 600 | −0.02em |
| Subtítulo de seção | 20 | 1.35 | 500 | 0 |
| Corpo | 17 | 1.55 | 400 | 0 |
| Apoio, procedência | 15 | 1.5 | 400 | 0 |
| Rótulo de dado | 14 | 1.4 | 500 | 0 |

Nada em caixa alta. Rótulo é caixa baixa, diferenciado por cor e peso.

Piso: **nenhum texto abaixo de 14px** dentro do portal; texto que o cliente lê, 15px ou mais.

## Layout

**Faixa de estado.** As telas de estado abrem com uma faixa `--tinta` sangrada de ponta a ponta, de altura fixa, que carrega
a identidade e o único fato que importa ali. Abaixo, papel, coluna única, seções separadas por fio.

- Margem lateral: 16px no celular.
- Ritmo vertical: 4px de base. Passos 8 / 12 / 16 / 22 / 32.
- Raio: 14px nas superfícies de ação, 0 no resto.
- Trilho da jornada: 6px, `#c9c1ae` no que falta e `--ouro-tinta` no percorrido (3,86:1 entre os dois).
- Marca: "Escritório Modelo Advogados" escrito (sem monograma) na faixa e no login, em dourado sobre bordô.
- Alinhamento: tudo à esquerda. Centralizado só o código de 6 dígitos do login.
- Alvo de toque: 48px na barra fixa, 44px no resto.
- Tudo que se toca é `<button>` ou `<a>`, nunca `span` ou `div`. Foco visível: anel dourado de 2px.

## Princípios

1. **Uma pergunta por tela.** Cada tela responde uma coisa e aponta o próximo passo.
2. **Caixa só onde se age.** A caixa da Cláudia e o dock têm superfície. O resto é fio e espaço.
3. **Número é número.** Data, valor e etapa sempre tabulares, alinhados em coluna.
4. **Ouro é caminho, verde é gente.**
5. **A faixa navy é a assinatura** das três telas de estado (lista, processo, painel), sempre com a mesma altura. Entrar e código abrem com a marca em navy: ali não existe estado para a faixa carregar.

## Proibido neste projeto

Rótulo em caixa alta espaçada · monoespaçada em rótulo pequeno · metadados colados com ponto do meio ·
seta "→" no fim de link ou botão · numeração 01/02/03 onde não há sequência · cartão arredondado com
borda e sombra repetido para tudo · botão cheio ao lado de botão fantasma · gradiente decorativo ·
emoji na interface.

## Telas cobertas

Login e código · Lista de processos (PF) · Processo (PF e PJ) · Processo por inteiro e documentos ·
Painel da empresa (PJ).
