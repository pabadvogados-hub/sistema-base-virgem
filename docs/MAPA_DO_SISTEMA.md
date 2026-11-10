# Mapa do sistema

Entrada de produção: `SISTEMA/backend/app/main_vps.py`. Cada módulo carrega isolado; o que
subiu e o que falhou aparece no log do serviço e em `GET /api/sistema/modulos` (admin).
Saúde: `GET /health` ou `/api/health`.

## Módulos (backend → `SISTEMA/backend/app/routers/`)

| Prefixo | Arquivo | O que faz |
|---|---|---|
| `/api/auth` | auth.py | Login (e-mail/senha, JWT próprio), usuários, papéis, troca de senha |
| `/api/atendimento` | atendimento.py, video_gravado.py | WhatsApp (sessão D-API/Z-API), inbox, CRM por funis, IA de primeiro atendimento, motor de fluxos (`/fluxos/processar`, protegido por `CRON_TOKEN`) |
| `/api/telefonia` | telefonia.py | Ligação por linha telefônica (Nvoip), opcional |
| `/api/voz` | voz.py | Ligação pelo WhatsApp no navegador, gravação e transcrição |
| `/api/dashboard` | dashboard.py | Resumo do atendimento e da produção |
| `/api/cadastro` | cadastro.py | Clientes e processos (cadastro próprio, quadro por fases) |
| `/api/casos`, `/api/taxonomias` | casos.py, taxonomias.py | Casos antes do processo; listas fechadas do escritório |
| `/api/processos` | processos.py, processos_carteira.py | Carteira e ficha do processo |
| `/api/ficha-entrevista` | ficha_entrevista.py | Entrevista do cliente |
| `/api/andamentos` | andamentos.py | Linha do tempo do processo |
| `/api/audiencias` | audiencias.py | Audiências, que vão para a agenda |
| `/api/controladoria` | controladoria.py | Publicações/intimações (SNC/SYNC), prazos, alertas no WhatsApp |
| `/api/tarefas`, `/api/roteiros`, `/api/assistente` | tarefas.py, tarefa_roteiros.py, assistente.py | Tarefas com prazo e responsável, checklist por tipo, "Meu dia" |
| `/api/peca` | peca.py | Gerar peça no timbrado (IA + DNA de escrita) |
| `/api/intake` | intake.py | Contratação: contrato, procuração, declaração, ZapSign |
| `/api/financeiro` | financeiro.py | Cobranças Asaas |
| `/api/fin` | fin.py, fin_rotinas.py | Financeiro próprio: lançamentos, fechamento, comissões, conciliação |
| `/api/campanhas` | campanhas.py, campanhas_produto.py, base_fluxos.py | Meta Ads, marketing por produto, reaquecimento da base |
| `/api/comercial` | comercial_kpi.py | Metas, vendedores, funil, ticket |
| `/api/estudio` | estudio.py | Pauta de conteúdo |
| `/api/agentes` | agentes.py | Agentes de IA da equipe |
| `/api/rh` | rh.py, rh_pagamentos.py, rh_slcoin.py | Ficha do colaborador, ponto, férias, demonstrativo, moeda de metas |
| `/api/portal` | portal.py + app/portal/ | Portal do Cliente (CPF + código no WhatsApp) |
| `/api/portal-resultado` | portal_resultado.py | Números do portal |

Módulos novos desta implantação (painel de metas da TV, leitura diária de e-mail, prazo por
áudio) entram pela lista `MODULOS` do `main_vps.py` assim que o arquivo existir em `routers/`.

## Telas (`SISTEMA/frontend/`)

- `dist/` — a tela no ar: `assets/index-*.js` (bundle) + blocos separados (`chat-amplo.js`,
  `chat-extras.js`, `crm-meus.js`, `video-gravado.js`, `telefonia-linha.js`, `ruido.js`,
  `whatsapp-ligar.js`, `visual-novo*.js`) carregados pelo `index.html`.
- `portal/` — Portal do Cliente. `colaborador/` e `equipe/` — áreas internas.
- `src_referencia/` — fonte React antiga, só referência (a tela no ar é o `dist`).

## Robôs (`SYNC/` + raiz) — horários em `deploy/crontab.txt`

| Grupo | Robôs |
|---|---|
| Básico | `backup_db.sh`, `transcrever_ligacoes.py`, motor de fluxos (curl) |
| Atendimento/comercial | `lead_quente_tarefa`, `alarme_qualificado`, `fluxo_quentes_esc`, `fluxo_fechamento_esc`, `fluxo_saiu_esc`, `aquecimento_watcher`, `termometro_leads`, `termometro_tarefa`, `comercial`, `crm_dona`, `pos_ligacao`, `card_apos_ligacao`, `fila_ligacao`, `fotos_contatos`, `relatorio_fluxos` |
| Marketing | `capi_meta`, `estudio_conteudo`, `render_carrossel` |
| Contratação | `contratacao_watcher`, `cobranca_assinatura`, `vigia_assinatura`, `cobranca_documentos`, `sync_assinados`, `quadro_contratacao`, `contratacao_documentos`, `checklist_documentos` |
| Produção/operacional | `pecas_agente`, `operador_agente`, `producao_watcher`, `aviso_producao`, `vigia_distribuicao`, `tarefas_conferir`, `avisos_agendados`, `juntada_pje`, `juntada_fila` |
| Agenda/audiências | `audiencias_vigia.py`, `relatorio_audiencias_semana.py`, `lembretes_compromisso.py` |
| Portal | `vigia_portal`, `regua_portal`, `convite_nps_portal`, `nps_coletor`, `relatorio_portal` |
| IA | `claude_ponte.py` (serviço `esc-claude-ponte`, opcional), `voz_ia/` (ligação por IA) |

Todos leem o `config/.env` apontado por `ESC_CONFIG_ENV` e o banco em `SISTEMA_DB`
(padrão `/opt/escritorio/SISTEMA/backend/sistema_esc.db`).

## Serviços na VPS

| Serviço | O que é |
|---|---|
| `esc-sistema.service` | backend (uvicorn, 127.0.0.1:8000), usuário `escsistema` |
| `esc-claude-ponte.service` | ponte do Claude pela assinatura (opcional, 127.0.0.1:8791) |
| nginx `esc-sistema.conf` | tela, portal, `/api`, `/media`, HTTPS |
