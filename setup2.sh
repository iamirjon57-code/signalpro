#!/usr/bin/env bash
# Signal Pro — brauzer orqali sozlashni yoqadi:
#   * HTTPS (Caddy + sslip.io, bepul sertifikat)
#   * /setup sahifasi uchun maxfiy kalit yaratadi
# Ishlatish:  cd /opt/signalpro && git pull && bash setup2.sh
set -euo pipefail

APP_DIR="/opt/signalpro"
SERVICE="signalpro"
ENV_FILE="$APP_DIR/.env"

IP=$(curl -s --max-time 15 https://api.ipify.org || true)
if [ -z "$IP" ]; then
  IP=$(hostname -I | awk '{print $1}')
fi
HOST="${IP//./-}.sslip.io"

echo "==> Server IP: $IP"
echo "==> Domen    : $HOST"

echo "==> Caddy (HTTPS) o'rnatilmoqda..."
export DEBIAN_FRONTEND=noninteractive
if ! command -v caddy >/dev/null 2>&1; then
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy >/dev/null
fi

cat > /etc/caddy/Caddyfile <<CADDY
$HOST {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
CADDY

systemctl restart caddy
systemctl enable caddy >/dev/null 2>&1 || true

echo "==> Firewall..."
ufw allow 80/tcp  >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true
ufw delete allow 8000/tcp >/dev/null 2>&1 || true

echo "==> Sozlash kaliti..."
TOKEN=$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 20)
if grep -q '^SETUP_TOKEN=' "$ENV_FILE" 2>/dev/null; then
  sed -i "s|^SETUP_TOKEN=.*|SETUP_TOKEN=$TOKEN|" "$ENV_FILE"
else
  echo "SETUP_TOKEN=$TOKEN" >> "$ENV_FILE"
fi
grep -q '^ENV_PATH=' "$ENV_FILE" || echo "ENV_PATH=$ENV_FILE" >> "$ENV_FILE"
chmod 600 "$ENV_FILE"

systemctl restart $SERVICE
sleep 6

echo
echo "================================================================"
echo " Tayyor. Brauzerda oching:"
echo
echo "   https://$HOST/setup"
echo
echo " Sozlash kaliti (SETUP_TOKEN):"
echo
echo "   $TOKEN"
echo
echo " Dashboard: https://$HOST"
echo "================================================================"
