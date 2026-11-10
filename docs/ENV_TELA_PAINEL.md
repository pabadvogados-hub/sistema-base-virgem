# Variáveis de ambiente — Frente C (tela, painel de metas, portal, financeiro e RH)

Todas vão no `config/.env` do servidor. Nenhuma é obrigatória para o sistema subir: sem elas, cada recurso fica desligado ou usa o padrão indicado.

## Painel de metas da TV (`/painel-metas.html?token=...`)
| Variável | Padrão | Para que serve |
|---|---|---|
| `PAINEL_METAS_TOKEN` | vazio = painel desligado (503) | Token de leitura que vai na URL da TV. Use um texto longo e aleatório. |
| `PAINEL_META_PROTOCOLOS` | `30` | Meta de protocolos por mês. |
| `PAINEL_META_CASOS_NOVOS` | `100` | Meta de casos novos (contratos assinados) por mês. |
| `PAINEL_ETAPA_PROTOCOLO` | `DISTRIBU\|PROTOCOL` | Regex das colunas do CRM que contam como protocolo. |
| `PAINEL_ETAPA_CONTRATO` | `ASSINAD\|FECHOU\|FECHADO\|CONTRATO` | Regex das colunas do CRM que contam como contrato assinado. |
| `PAINEL_BONUS_POR_CONTRATO` | `0` | R$ de bonificação por contrato (ranking e total). |
| `PAINEL_BONUS_POR_PROTOCOLO` | `0` | R$ de bonificação por protocolo (total). |

## Financeiro — comissões e fechamento
| Variável | Padrão | Para que serve |
|---|---|---|
| `FIN_COM_A_NOME` / `FIN_COM_B_NOME` | `Comissionado A` / `Comissionado B` | Nome de exibição de cada comissionado. |
| `FIN_COM_A_PCT` | `0` (desligado) | % do comissionado A sobre COM_A, COM_A+B e PARCEIRO+A. |
| `FIN_COM_A_EXTRA_PCT` | `0` | % extra do A, só no COM_A puro. |
| `FIN_COM_B_PCT` | `0` (desligado) | % do comissionado B sobre COM_B, COM_A+B e PARCEIRO. |
| `FIN_COM_A_EXCLUIR` / `FIN_COM_B_EXCLUIR` | vazio | Clientes (lista por vírgula) que nunca geram comissão para A / B. |
| `FIN_EXCLUIR_FATURAMENTO` | vazio | Nomes (lista por vírgula) cujas receitas não entram no faturamento. |
| `FIN_COM_VENCIMENTO_DIA` | `5` | Dia do mês seguinte em que vence a despesa de comissão. |
| `FIN_HIST_PLANILHA_ID` | vazio | ID no Drive de planilha opcional com histórico (abas `A MES.AA` / `B MES.AA`). |

Sufixo na descrição da cobrança do Asaas: `_A`, `_B`, `_B_A`, `_P`, `_P_A`.

## Portal do cliente e robôs do portal
| Variável | Padrão | Para que serve |
|---|---|---|
| `PORTAL_RESULTADO_EMAILS` | e-mails do Dr. Titular e da Dra. Advogada | Quem vê a tela "Resultado do Portal" (lista por vírgula). |
| `PORTAL_VIDEOS_URL` | vazio = régua manda só texto | Pasta pública com os vídeos de ajuda do portal. |
| `WHATSAPP_ESCRITORIO` | vazio | Número do próprio escritório (a régua não manda mensagem para ele). |
| `AVISO_WHATSAPP_EQUIPE` | vazio | Número que recebe os testes e alertas da régua. |
| `RELATORIO_PORTAL_DESTINO` | vazio | Números que recebem o relatório diário do portal (lista por vírgula). |
| `IP_DA_VPS` | vazio | IP do servidor, excluído das estatísticas de acesso. |

## RH
| Variável | Padrão | Para que serve |
|---|---|---|
| `RH_DOMINIO_EQUIPE` | vazio = qualquer domínio | Ex.: `@escritorio.adv.br`: só esses e-mails entram como equipe. |
| `RH_CONTAS_ROBO` | `robo@,automacao@` | Prefixos de logins de automação que não entram no RH. |
| `RH_PAGAMENTO_DIVIDIDO_IDS` | vazio | IDs de colaboradores com remuneração dividida com outra empresa. |

## Na tela (sem .env)
Admins, vendedores, apelidos e ramais da Telefonia ficam em `SISTEMA/frontend/dist/escritorio-config.js`.
