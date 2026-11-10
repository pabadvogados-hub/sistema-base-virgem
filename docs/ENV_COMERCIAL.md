# Variáveis de ambiente: Comercial e Atendimento (frente A)

Tudo fica em `config/.env`. **Nenhum telefone, e-mail, ID de pasta, voz ou token tem valor padrão no código.**
Variável vazia = o recurso fica desligado em silêncio (o robô registra e segue, não quebra e não manda nada para endereço inventado).

Onde houver equivalente da frente B (`ENV_FRENTE_B.md`), os robôs daqui usam o dela como reserva:
`TEL_ALERTA_EQUIPE`, `REVISORA_EMAIL`, `SITE_URL`, `VENDEDORES_EMAILS`.

## 1. As duas linhas de WhatsApp

O `seed_pipelines` cria as linhas na primeira subida, a partir destas variáveis (só se ainda não existirem).

| Variável | Para quê |
|---|---|
| `WA_RECLAMANTES_TELEFONE` | Linha fixa do escritório, só para reclamantes (55DDDNUMERO) |
| `WA_RECLAMANTES_INSTANCIA` | Instância Z-API, ou `dapi:<sessao>` para a D-API |
| `WA_RECLAMANTES_TOKEN` / `WA_RECLAMANTES_CLIENT_TOKEN` | Credenciais da instância |
| `WA_RECLAMANTES_NOME` | Nome da linha na tela (padrão "Reclamantes") |
| `WA_EMPRESAS_TELEFONE` / `_INSTANCIA` / `_TOKEN` / `_CLIENT_TOKEN` / `_NOME` | Número do Dr. Titular (empresas) |
| `WA_EMPRESAS_IA` | `1` liga a recepção de IA na linha de empresas. **Padrão: desligada** (é o WhatsApp pessoal dele) |
| `DAPI_TOKEN`, `DAPI_SESSION`, `DAPI_NUMERO_ID` | Saída dos robôs (SYNC/wa_dapi.py) e do voz_ia; `DAPI_NUMERO_ID` = id da linha em `wa_numeros` (padrão 1 = Reclamantes) |
| `SAUDACAO_WHATSAPP_BUSINESS` | Início da saudação automática do WhatsApp Business do celular (para não desligar a IA) |
| `ASSINATURA_AUTOMACAO` | Nome que assina as mensagens automáticas (padrão "Equipe Escritório Modelo") |

## 2. IA de atendimento

| Variável | Padrão | Para quê |
|---|---|---|
| `ANTHROPIC_API_KEY` | (obrigatória) | SDR, briefing, termômetro, mensagens |
| `SDR_RECLAMANTE_NOME` | Helena | Nome da SDR da linha de reclamantes |
| `SDR_ADVOGADA_RESPONSAVEL` | Dra. Advogada | Como a SDR e os fluxos citam a advogada |
| `RESPONSAVEL_EMPRESAS` | Dr. Titular | Para quem a recepção de empresas encaminha |
| `RECEPCAO_EMPRESAS_NOME` | Recepção | |
| `SDR_MODELO`, `SDR_MODELO_RESERVA`, `SDR_MODELO_BRIEFING`, `RECEPCAO_EMPRESAS_MODELO` | modelos Claude | Troca de modelo sem mexer no código |
| `FLUXO_MODELO`, `FLUXO_MODELO_LEITURA` | | Mensagem personalizada do fluxo "já saiu?" |
| `ESC_CLAUDE_BIN` | `~/.local/bin/claude` | Claude Code do servidor (opcional; sem ele, os robôs caem em palavra-chave/texto padrão) |
| `ESCRITORIO_NOME` | Escritório Modelo Advogados | |

## 3. Equipe comercial e avisos

| Variável | Formato | Para quê |
|---|---|---|
| `VENDEDORES` | `Nome:email:telefone:meta:9-18, ...` | Equipe de vendas (só o nome é obrigatório). Rodízio do LIGAR AGORA, dono do card, fila diária, painel comercial. Vazio = sem vendedor: as tarefas vão para `TAREFA_RESPONSAVEL_EMAIL` |
| `TEL_ALERTA_EQUIPE` | 55DDD... | Reserva de todos os avisos de WhatsApp abaixo |
| `QUENTES_AVISO_TEL` / `ALARME_DESTINO` | lista com vírgula | Quem recebe o LIGAR AGORA além do vendedor |
| `FECHAMENTO_AVISO_TEL` | | Avisos da régua de fechamento |
| `FLUXO_SAIU_RESUMO` | | Resumo diário do fluxo "já saiu?" |
| `FILA_LIGACAO_DESTINO` | lista | Quem recebe a fila de ligação |
| `COMERCIAL_PAINEL_TEL` | | Quem recebe o painel do comercial |
| `TAREFA_RESPONSAVEL_EMAIL` | e-mail de usuário do sistema | Dono das tarefas de lead quente sem vendedor (reserva: `REVISORA_EMAIL`) |
| `REVISORA_EMAIL` / `RESPONSAVEL_REVISAO_EMAIL` | | Entra na tarefa quando a troca de produto envolve contrato |
| `PRODUTO_CARD_VENDEDORAS` | e-mails | Quem, além do admin, troca o produto do card |
| `COMERCIAL_VENDEDOR_1/2`, `COMERCIAL_VENDEDOR_1/2_EMAIL` | | Reserva do painel comercial se `VENDEDORES` estiver vazio |
| `COMERCIAL_BLOCO` | 30 | Leads por dia na fila do comercial |
| `COMERCIAL_INICIO`, `MOEDAS_INICIO` | AAAA-MM-DD | Início da série do painel e do programa de moedas |
| `COMERCIAL_TAREFA_PROTOCOLO`, `ADVBOX_TOKEN` | | Integração financeira opcional do painel (vazio = painel sem a parte financeira) |
| `STT_NOMES_EQUIPE` | Titular, Advogada | Nomes que ajudam a transcrição de voz |

## 4. Links e pastas (todos opcionais)

| Variável | Para quê |
|---|---|
| `PUBLIC_BASE_URL` | Endereço público do sistema (mídias, avatares, carrossel, link do portal). Padrão provisório `https://sistema.escritorio.adv.br` |
| `MEDIA_DIR`, `ESC_MEDIA_DIR` | Pasta das mídias servida em `/media` |
| `GOOGLE_PASTA_RECLAMANTE` | ID da pasta-mãe RECLAMANTE no Drive do escritório (vazio = "subir no Drive" não acha cliente) |
| `GOOGLE_PASTA_LEADS_DOCS_NOME` | Nome da pasta de documentos de leads |
| `FILA_LIGACAO_PASTA_DRIVE` | Pasta onde a fila de ligação em HTML é salva |
| `DEPOIMENTOS_LINK` | Pasta pública de depoimentos (toque 3 da régua) |
| `SITE_URL` | Site do escritório, citado na régua de fechamento |
| `PLANILHA_MESA_LINK` | Planilha da mesa comercial, citada no painel |
| `ESC_CARROSSEL_TPL` | Modelo HTML do carrossel (identidade bordô e dourado) |

## 5. Meta Ads e campanhas

`META_ADS_TOKEN`, `META_ADS_ACCOUNT`, `META_PIXEL_ID`, `META_PAGE_ID`, `META_VALOR_CONTRATO`, `CAMPANHAS_NOSSAS` (prefixos das campanhas
criadas pela gestão, padrão `[ESC],[TESTE]`), `TETO_MIDIA_MES`, `TETO_TRABALHISTA`, `TETO_EMPRESARIAL`, `PRODUTO_VALOR_TRABALHISTA`,
`PRODUTO_VALOR_EMPRESARIAL`.

As frases de cada anúncio (fast-food, supermercado, acidente, geral, palestra) ficam em `app/whatsapp/funis.py` (`FRASES_CAMPANHA`).

## 6. Telefonia e voz

| Variável | Para quê |
|---|---|
| `NVOIP_NUMERO_SAIDA` | Linha Nvoip do escritório (bina) |
| `NVOIP_TRONCO_PADRAO`, `NVOIP_TRONCO_<RAMAL>` | Tronco PJSIP por ramal (padrão `nvoip`) |
| `NVOIP_CANAIS`, `AMI_DISCADOR_USER`, `AMI_DISCADOR_SECRET`, `TELEFONIA_CHAVE` | Discador (Asterisk) |
| `SIP_DOMAIN`, `SIP_WSS_URL` | Softphone do navegador |
| `ELEVENLABS_API_KEY`, `GEMINI_API_KEY` | Voz dos agentes e da IA que liga |
| `ELEVENLABS_VOZ_ID` | **Sem padrão.** Voz da IA que liga (voz_ia). Clonar voz só com autorização da pessoa |
| `ELEVENLABS_AGENTE_NOME`, `VOZ_IA_QUEM_FALA` | Nome do agente e em nome de quem ele fala |

## 7. Outros

`ALARME_JANELA_DIAS`, `ALARME_MAX`, `FLUXO_REVISITA_DIAS`, `QUENTES_REVISAO_MANUAL`, `ESC_CONFIG_ENV`, `ESC_DB`, `SISTEMA_DB`,
`PRODUCAO_DIR`, `ESC_AVISOS_FILA`.

## Quadros (IDs fixos, `app/whatsapp/funis.py`)

| ID | Quadro |
|---|---|
| 10 | Reclamantes (entrada: Caixa de Entrada + colunas Campanha Fast-food / Supermercado / Acidente de Trabalho / Geral) |
| 20 | Contratação |
| 18 | Produção |
| 11 | Clientes |
| 30 | Empresas |
| 31 | Palestra Rescisão Indireta |
