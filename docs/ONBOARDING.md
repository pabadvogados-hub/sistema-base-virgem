# Onboarding — o que falta do escritório para ligar cada parte

Combinado no kickoff de 09/11/2026. Cada item trava só o módulo indicado; o resto do
sistema funciona sem ele. Marque [x] quando chegar e for colocado no `config/.env`.

## Infraestrutura

- [ ] **Domínio** do sistema (placeholder `sistema.escritorio.adv.br`, A CONFIRMAR) e acesso ao DNS
      para apontar o registro A para a VPS → `ESCRITORIO_DOMINIO`
- [ ] **VPS própria** (Ubuntu 22.04/24.04, 2 vCPU, 4 GB RAM, 80 GB) em nome do escritório,
      com acesso root por chave SSH → `VPS_HOST`, `VPS_SSH_KEY`
- [ ] **Acesso remoto** às máquinas do Dr. Titular e da Dra. Advogada (aplicativo a indicar) para a
      configuração e o motor de peças
- [ ] E-mail de login de cada pessoa da equipe → `EQUIPE_EMAIL_TITULAR`, `EQUIPE_EMAIL_ADVOGADA`

## WhatsApp

- [ ] **Linha fixa nova** dedicada aos **reclamantes** (WhatsApp Business valida por ligação),
      conectada no provedor de sessão → `DAPI_TOKEN`, `DAPI_SESSION`, `DAPI_NUMERO_ID`
- [ ] Decisão sobre o **número do Dr. Titular** (empresas): só espelhar na plataforma ou deixar fora
- [ ] **Número do funil de palestra** (rescisão indireta, 09/12) verificado
- [ ] Telefone que recebe **alertas de prazo** (Dra. Advogada) → `CONTROLADORIA_ALERTA_TEL`,
      `ADVOGADA_RESPONSAVEL_TEL`

## Documentos e Google

- [ ] **Decisão Google Drive x OneDrive** (a Dra. Advogada prefere Drive; recomendação: 2 TB).
      O sistema hoje grava no **Google Drive**; OneDrive exige o módulo Microsoft
      (`MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`)
- [ ] Conta Google Workspace do escritório + **projeto Google Cloud próprio** com OAuth
      (Drive, Agenda, Gmail) → `GOOGLE_OAUTH_CREDENTIALS`, `ESC_TOKEN_GOOGLE`
- [ ] ID da **pasta-mãe** de clientes no Drive → `GOOGLE_PASTA_RECLAMANTE`
- [ ] Migração dos arquivos do OneDrive/SharePoint para a pasta-mãe (por cliente e por caso)
- [ ] **Timbrado oficial** em .docx (o enviado em 05/11 está com o monograma VB; confirmar qual
      vai nas peças do Escritório Modelo)

## Jurídico

- [ ] **Sistema jurídico**: assinatura do plano e confirmação de **API** do Astrea com o suporte
      → `ASTREA_API_TOKEN` (sem API, prazos e tarefas ficam só no sistema)
- [ ] **SNC** (publicações/intimações, baixa dos autos, regras de minuta): assinatura
      (R$ 127–150/mês até 300 processos) e token → `SYNC_API_TOKEN`, `SYNC_API_BASE`
- [ ] OAB da sociedade, CNPJ e endereço que saem nas peças e contratos → `ESCRITORIO_*`
- [ ] Peças antigas do Dr. Titular e da Dra. Advogada (para o DNA de escrita de cada um)

## IA

- [ ] **Chave da Anthropic (Claude)** do escritório → `ANTHROPIC_API_KEY`
      (ou assinatura do Claude logada na VPS para a ponte `esc-claude-ponte`)
- [ ] **Transcrição de áudio** (Groq ou OpenAI) → `STT_API_KEY`
- [ ] **ElevenLabs** (voz da IA na ligação) → `ELEVENLABS_API_KEY`
- [ ] Opcional: Gemini como reserva → `GEMINI_API_KEY`

## Comercial, cobrança e assinatura

- [ ] **Asaas** do escritório → `ASAAS_API_TOKEN`
- [ ] **ZapSign** do escritório → `ZAPSIGN_API_TOKEN` (+ `ZAPSIGN_USER_TOKEN` se a Dra. Advogada
      assinar automaticamente)
- [ ] Telefonia fora do WhatsApp (só se o comercial ligar para quem não tem WhatsApp): Nvoip
      → `TELEFONIA_CHAVE`, `NVOIP_CANAIS`

## Marketing (tráfego de presente por 90 dias)

- [ ] Portfólio empresarial do Meta, conta de anúncios, Página e Instagram, com acesso de
      administrador → `META_ADS_TOKEN`, `META_AD_ACCOUNT`, `META_PAGE_ID`, `META_PIXEL_ID`,
      `INSTAGRAM_ID`, `INSTAGRAM_TOKEN`
- [ ] Verba de mídia mensal definida (paga direto à plataforma) → `TETO_MIDIA_MES`
- [ ] Campanhas: acidente de trabalho, fast food / supermercados, geral, palestra de rescisão indireta

## E-mail

- [ ] Caixa de e-mail para a **leitura diária** (peritos, propostas de acordo) e conta de envio
      → `ESC_SMTP_USUARIO`, `ESC_SMTP_SENHA`, `ESC_EMAIL_REMETENTE`

## Painel de metas (TV)

- [ ] TV/computador da sala e as metas do mês (casos novos, protocolos; meta: 30 protocolos/mês
      em 2 meses) e regra da bonificação

## Ordem de entrega combinada

1. Atendimento (linha dos reclamantes + IA) e elaboração de pastas
2. Publicações (SNC) + prazos + tarefas da Dra. Advogada + prazo por áudio
3. Produção de peças no estilo de cada um
4. Painel de metas, leitura de e-mail, funil da palestra (09/12)
