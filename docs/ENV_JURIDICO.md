# Variáveis de ambiente: jurídico, controladoria, tarefas e peças (frente B)

Todas ficam em `config/.env`. **Nenhuma tem valor padrão no código.** Quando uma variável fica
vazia, o robô que depende dela registra no log e segue em frente: não manda e-mail nem WhatsApp
para lugar nenhum.

## Sistema jurídico externo e publicações

| Variável | Valor | Para que serve |
|---|---|---|
| `SISTEMA_JURIDICO` | `nenhum` (padrão) ou `astrea` | O banco do sistema é a fonte da verdade. `astrea` ainda é só esqueleto: depende da API do plano. |
| `ESC_FONTE_PUBLICACOES` | `djen` (padrão), `snc` ou `djen,snc` | Origem das intimações. |
| `SNC_API_URL`, `SNC_TOKEN` | — | SNC (publicações e autos). É esqueleto até haver credencial e a documentação da API. |

## Contatos do escritório (`INTEGRACOES/escritorio_config.py`)

| Variável | Exemplo de formato | Para que serve |
|---|---|---|
| `AGENDA_EMAIL_PRINCIPAL` | `agenda@dominio` | Conta Google da agenda central (o token do Calendar é desta conta). |
| `AGENDA_CONVIDADOS` | `a@x,b@y` | Convidados de todo prazo fatal e de toda audiência. |
| `EQUIPE_EMAILS` | `Advogada:email:Revisão:PECA,Titular:email:Titular:` | Equipe que pode ser convidada pela tela. A trilha `PECA`/`MEIO` é opcional. |
| `REVISORA_EMAIL` | e-mail da Dra. Advogada | Convidada da audiência e responsável padrão pela distribuição. |
| `TITULAR_EMAIL` | e-mail do Dr. Titular | — |
| `CAIXA_PECAS_EMAIL` | — | Caixa de Gmail que o assistente jurídico lê e usa para enviar. O token fica em `config/token_gmail_caixa_pecas.json`. |
| `TEL_ALERTA_EQUIPE` | `5511XXXXXXXXX` | WhatsApp que recebe os alertas dos robôs (produção, assinatura, documentos, distribuição, sentinela). |
| `TEL_REVISORA` | `5511XXXXXXXXX` | WhatsApp da Dra. Advogada (relatório semanal de audiências). |
| `VENDEDORES_EMAILS` | `email:id_usuario:Nome,...` | Dono do card no comercial (contratação e assinatura). |
| `SENTINELA_DESTINO`, `ASSINATURA_ALERTA_TEL`, `DISTRIBUICAO_AVISO_TEL`, `DISTRIBUICAO_RESPONSAVEL` | — | Opcionais: sobrescrevem os gerais acima. |

## Google Drive

| Variável | Para que serve |
|---|---|
| `GOOGLE_PASTA_RECLAMANTE` | Id da pasta RECLAMANTE no Drive do escritório. É **obrigatória** para o intake criar a pasta do cliente. |
| `GOOGLE_SHEETS_CONTRATOS_ID` | Planilha de numeração de contratos (opcional). |
| `GOOGLE_TEMPLATE_CONTRATO`, `GOOGLE_TEMPLATE_PROCURACAO`, `GOOGLE_TEMPLATE_DECLARACAO` e `GOOGLE_PASTA_MODELOS_*` | Só para o gerador antigo, que trabalha com Google Docs. O intake novo usa os `.docx` de `app/juridico/modelos/`. |
| `LUA_DRIVE_RAIZES`, `LUA_DRIVE_BLOQUEADOS` | Ids das pastas que o assistente jurídico pode ler (lista fechada) e das que ele não pode. |

`SYNC/kit_boasvindas_config.json` e `SYNC/contratacao_config.json` foram zerados. Preencha neles
os ids de arquivo do Drive do escritório, o site e o WhatsApp de alerta.

## Equipe, peças e intake

| Variável | Padrão | Para que serve |
|---|---|---|
| `ESC_USUARIO_TITULAR_ID`, `ESC_USUARIO_ADVOGADA_ID`, `ESC_USUARIO_AUTOMACAO_ID` | 1, 2, 3 (`app/juridico/equipe.json`) | Ids da tabela `usuarios`. **Crie o usuário "Assistente IA (automacao)"** e confira os ids. |
| `ESC_CARTEIRA_DEDICADA_NOME` | `Advogada` | Quem recebe os processos listados em `controladoria_motor/advogados_processos.json`. |
| `ESC_IA_PECA` | `api` se houver `ANTHROPIC_API_KEY`, senão `cli` | `cli` usa o Claude Code da assinatura (no servidor, o `CLAUDE_CODE_OAUTH_TOKEN`). |
| `ESC_MODELO_PECA` / `ESC_MODELO_PECA_CLI` / `ESC_MODELO_INTAKE` | `claude-opus-5-5` / `opus` / `claude-opus-5-5` | Modelo usado em cada caminho. |
| `ESC_CLAUDE_BIN`, `ESC_TIMEOUT_PECA` | `claude` no PATH, 900 s | — |
| `ESC_TIMBRADO_DOCX` | `app/juridico/modelos/TIMBRADO.docx` | Papel timbrado. |
| `ESC_FONTE_PECA`, `ESC_TAMANHO_PECA`, `ESC_RECUO_PECA_CM` | Century Gothic, 12, 3 | Padrão das peças do escritório. |
| `ESC_CIDADE_ESCRITORIO` | Cidade | Cidade da data no contrato e na procuração. |
| `ESC_RESPONSAVEL_PROCESSO` | nome da Dra. Advogada | Responsável gravado no processo novo. |
| `ESC_SUBPASTAS_CLIENTE` | `ATOS INTERNOS,DOCUMENTOS DO CLIENTE,PASTA DO CLIENTE` | Subpastas criadas para cada cliente. |
| `ESC_INTAKE_ZAPSIGN` | `0` | `1` envia contrato e procuração ao ZapSign quando não há `[PREENCHER]`. Exige o LibreOffice no servidor para gerar o PDF. |
| `ZAPSIGN_ADVOGADA_EMAIL` | — | E-mail da Dra. Advogada no ZapSign (assinatura automática pela API). O nome ficou sem acento. |
| `GEMINI_API_KEY` | — | IA de reserva para quando a Anthropic recusar. |

## Arquivos de configuração (editáveis sem deploy)

- `app/juridico/equipe.json`: pessoas e papéis (revisora, produção, protocolo e dona das tarefas
  são a Advogada; titular e financeiro são o Titular; automação é o Assistente IA).
- `app/juridico/clientes_empresa.json`: cobrança de audiência e consultoria por cliente empresa.
  Começa vazio.
- `app/juridico/DNA_ESCRITORIO.md`: o padrão das peças, que todo prompt de peça lê.
- `app/controladoria_motor/feriados_locais.json`: feriados municipais da região (conferir com o
  TRT-2) e férias coletivas.
- `app/controladoria_motor/advogados_processos.json`: carteira dedicada e processos de parceiro.
