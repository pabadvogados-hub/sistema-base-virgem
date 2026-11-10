# Sistema Escritório Modelo Advogados

> **BASE VIRGEM.** Esta é a versão em branco do sistema: sem marca, sem equipe, sem cliente.
> Para montar o sistema de um escritório novo:
>
> 1. Copie esta pasta inteira para `C:\AUTOMAÇÕES CLIENTES\<NOME_DO_ESCRITORIO>` (nunca mexa na base).
> 2. `git init` na cópia e rode `python deploy/personalizar.py --nome "X Advogados" --curto "X" --cidade "Cidade" --uf UF`.
> 3. Preencha `SISTEMA/backend/app/juridico/equipe.json` (pessoas e papéis), `config/.env`
>    e `SISTEMA/frontend/dist/escritorio-config.js` (admins e vendedores).
> 4. Reescreva `SISTEMA/backend/app/juridico/DNA_ESCRITORIO.md` com 5 a 10 peças reais do escritório.
> 5. Coloque timbrado, contrato e procuração em `SISTEMA/backend/app/juridico/modelos/` (ficam fora do git).
> 6. Troque as logos neutras "EM" (`SISTEMA/frontend/dist/logo-esc-*`, `favicon.svg`, `SISTEMA/frontend/portal/logo-esc*`, `icone*`).
> 7. Antes de subir para o GitHub, rode as duas linhas anti-vazamento.

Central de operação do escritório: atendimento por WhatsApp com IA, CRM de leads,
contratação com assinatura digital, cadastro de clientes e processos, controladoria
(publicações e prazos), tarefas, produção de peças no timbrado, financeiro, marketing,
RH, painel de metas e Portal do Cliente.

> Domínio de produção: `sistema.escritorio.adv.br` (**A CONFIRMAR**).

## Estrutura

```
SISTEMA/
  backend/            API FastAPI (app/main_vps.py = entrada de produção)
  frontend/dist/      tela que vai para o ar (bundle + blocos .js)
  frontend/portal/    Portal do Cliente
  frontend/src_referencia/  código-fonte React antigo, só referência
  deploy/deploy.py    envio de mudanças para a VPS com trava (--puxar / --simular)
INTEGRACOES/          Google, ZapSign, Asaas, Meta, e-mail, agenda
SYNC/                 rotinas automáticas (fluxos do CRM, cobranças, vigias)
voz_ia/               ligação feita pela IA (ElevenLabs)
deploy/               instalador da VPS, systemd, nginx, crontab, Windows
config/.env.example   todas as chaves e dados do escritório (copiar para config/.env)
docs/                 COMECE_AQUI, MAPA_DO_SISTEMA, ONBOARDING
```

## Subir numa VPS nova (Ubuntu)

```bash
git clone <repositorio> /opt/escritorio
cd /opt/escritorio
cp config/.env.example config/.env && nano config/.env
bash deploy/instalar_vps.sh sistema.escritorio.adv.br email@do-escritorio
```

O instalador cria o usuário de serviço, o ambiente Python, o banco vazio, o serviço
`esc-sistema`, o nginx, o firewall e o HTTPS. As rotinas automáticas **não** são ligadas
sozinhas: veja `deploy/crontab.txt`.

Primeiro acesso:

```bash
cd /opt/escritorio/SISTEMA/backend
sudo -u escsistema ESC_CONFIG_ENV=/opt/escritorio/config/.env \
  /opt/escritorio/venv/bin/python seed_equipe.py titular@... advogada@...
```

As senhas são geradas na hora e aparecem uma única vez.

## Rodar no Windows (teste)

Duplo clique em `deploy\windows\iniciar.bat`. Na primeira vez ele copia o
`config\.env.example`, abre para preencher e instala tudo. A tela abre em
`http://localhost:8000`.

## Mudanças depois de no ar

Sempre: `python SISTEMA/deploy/deploy.py --puxar` → mudança → `--simular` → deploy.
O script sobe só o que mudou, guarda backup na VPS e desfaz sozinho se o backend não subir.

## Segurança

- `config/.env`, banco, mídia e documentos de cliente **nunca** vão para o git (`.gitignore`).
- Em produção (`AMBIENTE=producao`) o sistema não sobe sem `JWT_SECRET`.
- A documentação da API (`/docs`) fica desligada em produção.
