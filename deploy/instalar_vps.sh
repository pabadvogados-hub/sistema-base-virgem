#!/usr/bin/env bash
# =============================================================================
# Instalador do Sistema Escritório Modelo Advogados numa VPS Ubuntu 22.04/24.04 NOVA
# (VPS do proprio escritorio; nada aqui depende de outro servidor).
#
# Uso (como root, na VPS):
#   git clone <repo> /opt/escritorio          (ou copie a pasta para la)
#   cd /opt/escritorio
#   cp config/.env.example config/.env && nano config/.env     (preencha o essencial)
#   bash deploy/instalar_vps.sh sistema.escritorio.adv.br email@do-escritorio
#
# O que faz: pacotes, usuario de servico, venv, dependencias, pastas de dados,
# systemd, nginx, firewall e HTTPS (certbot). NAO liga as rotinas automaticas:
# isso e feito depois, rotina por rotina (ver deploy/crontab.txt).
# Pode rodar de novo sem estragar nada (idempotente).
# =============================================================================
set -euo pipefail

DOMINIO="${1:-}"
EMAIL_CERT="${2:-}"
RAIZ=/opt/escritorio
USUARIO=escsistema

if [[ $EUID -ne 0 ]]; then echo "Rode como root."; exit 1; fi
if [[ -z "$DOMINIO" ]]; then
  echo "Uso: bash deploy/instalar_vps.sh <dominio> [email-para-o-certificado]"; exit 1
fi
if [[ ! -d "$RAIZ/SISTEMA/backend" ]]; then
  echo "Nao achei $RAIZ/SISTEMA/backend. O projeto precisa estar em $RAIZ."; exit 1
fi
if [[ ! -f "$RAIZ/config/.env" ]]; then
  echo "Falta $RAIZ/config/.env (copie de config/.env.example e preencha)."; exit 1
fi
if ! grep -qE '^JWT_SECRET=.{20,}' "$RAIZ/config/.env"; then
  echo "Gerando JWT_SECRET e CRON_TOKEN no config/.env ..."
  JWT=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')
  CRON=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')
  sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$JWT|" "$RAIZ/config/.env"
  grep -q '^JWT_SECRET=' "$RAIZ/config/.env" || echo "JWT_SECRET=$JWT" >> "$RAIZ/config/.env"
  sed -i "s|^CRON_TOKEN=.*|CRON_TOKEN=$CRON|" "$RAIZ/config/.env"
  grep -q '^CRON_TOKEN=' "$RAIZ/config/.env" || echo "CRON_TOKEN=$CRON" >> "$RAIZ/config/.env"
fi
grep -q '^AMBIENTE=' "$RAIZ/config/.env" || echo "AMBIENTE=producao" >> "$RAIZ/config/.env"
sed -i "s|^ESCRITORIO_DOMINIO=.*|ESCRITORIO_DOMINIO=$DOMINIO|" "$RAIZ/config/.env"

echo "==> Pacotes do sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx \
  ufw ffmpeg tesseract-ocr tesseract-ocr-por sqlite3 curl git
timedatectl set-timezone America/Sao_Paulo || true

echo "==> Usuario de servico ($USUARIO)"
id "$USUARIO" >/dev/null 2>&1 || useradd --system --home "$RAIZ" --shell /usr/sbin/nologin "$USUARIO"

echo "==> Ambiente Python"
[[ -d "$RAIZ/venv" ]] || python3 -m venv "$RAIZ/venv"
"$RAIZ/venv/bin/pip" install --upgrade pip wheel
"$RAIZ/venv/bin/pip" install -r "$RAIZ/SISTEMA/backend/requirements.txt"

echo "==> Pastas de dados"
mkdir -p "$RAIZ/media/avatars" "$RAIZ/producao/ponte" "$RAIZ/backups" "$RAIZ/cerebro" \
         "$RAIZ/SISTEMA/backend/uploads" /var/log/escritorio
chown -R "$USUARIO:$USUARIO" "$RAIZ/media" "$RAIZ/producao" "$RAIZ/backups" "$RAIZ/cerebro" \
         "$RAIZ/SISTEMA/backend" /var/log/escritorio
chown root:"$USUARIO" "$RAIZ/config" "$RAIZ/config/.env"
chmod 750 "$RAIZ/config"; chmod 640 "$RAIZ/config/.env"
chmod +x "$RAIZ/backup_db.sh"

echo "==> Banco e funis iniciais"
sudo -u "$USUARIO" bash -c "cd $RAIZ/SISTEMA/backend && ESC_CONFIG_ENV=$RAIZ/config/.env $RAIZ/venv/bin/python -c 'import asyncio; from app.database import init_db; asyncio.run(init_db())'"

echo "==> Servico (systemd)"
cp "$RAIZ/deploy/systemd/esc-sistema.service" /etc/systemd/system/
cp "$RAIZ/deploy/systemd/esc-claude-ponte.service" /etc/systemd/system/   # copiado, NAO ligado
systemctl daemon-reload
systemctl enable --now esc-sistema.service
sleep 3
systemctl is-active esc-sistema.service

echo "==> Nginx"
sed "s|__DOMINIO__|$DOMINIO|g" "$RAIZ/deploy/nginx/esc-sistema.conf" > /etc/nginx/sites-available/esc-sistema.conf
ln -sf /etc/nginx/sites-available/esc-sistema.conf /etc/nginx/sites-enabled/esc-sistema.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
# o nginx precisa ler o frontend
chmod o+rx /opt "$RAIZ" "$RAIZ/SISTEMA" "$RAIZ/SISTEMA/frontend"
chmod -R o+rX "$RAIZ/SISTEMA/frontend" "$RAIZ/media"

echo "==> Firewall"
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

if [[ -n "$EMAIL_CERT" ]]; then
  echo "==> HTTPS (certbot)"
  certbot --nginx -d "$DOMINIO" --non-interactive --agree-tos -m "$EMAIL_CERT" --redirect || \
    echo "AVISO: certbot falhou. Confira se o DNS de $DOMINIO aponta para esta VPS e rode de novo."
fi

echo
echo "Pronto. Teste:  curl -s http://127.0.0.1:8000/health"
echo "Primeiro admin: cd $RAIZ/SISTEMA/backend && sudo -u $USUARIO ESC_CONFIG_ENV=$RAIZ/config/.env $RAIZ/venv/bin/python seed_admin.py \"Nome\" email"
echo "Equipe inicial: sudo -u $USUARIO ... seed_equipe.py   (gera e mostra as senhas uma unica vez)"
echo "Rotinas:        revise deploy/crontab.txt e instale com: crontab -u $USUARIO $RAIZ/deploy/crontab.txt"
