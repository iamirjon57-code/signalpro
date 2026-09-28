#!/usr/bin/env bash
# Signal Pro — bitta buyruq bilan to'liq o'rnatish (Ubuntu 22.04/24.04, Debian 12).
#
#   curl -fsSL https://raw.githubusercontent.com/iamirjon57-code/signalpro/main/install.sh | sudo bash
#
# Nima qiladi: Python + bot + sayt + HTTPS (Caddy, sslip.io) + systemd xizmati.
# Oxirida brauzer uchun sozlash manzili va kaliti chiqadi — qolgan hamma narsa brauzerda.
# Qayta ishga tushirsangiz: kodni yangilaydi, .env va baza saqlanadi, yangi sozlash kaliti beradi.
set -euo pipefail

REPO="${REPO:-https://github.com/iamirjon57-code/signalpro.git}"
APP_DIR="/opt/signalpro"
SERVICE="signalpro"
ENV_FILE="$APP_DIR/.env"

if [ "$(id -u)" -ne 0 ]; then echo "Root sifatida ishga tushiring:  sudo bash install.sh"; exit 1; fi
export DEBIAN_FRONTEND=noninteractive

echo "==> [1/6] Paketlar..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ufw \
  debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null

echo "==> [2/6] Kod: $REPO"
if [ -d "$APP_DIR/.git" ]; then
  git -c safe.directory="$APP_DIR" -C "$APP_DIR" pull --ff-only
else
  rm -rf "$APP_DIR"
  git clone --depth 1 "$REPO" "$APP_DIR"
fi

echo "==> [3/6] Python muhiti (avtomatik)..."
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$ENV_FILE" ]; then
  cp "$APP_DIR/.env.example" "$ENV_FILE"
  echo "DB_PATH=$APP_DIR/signals.db" >> "$ENV_FILE"
fi
TOKEN=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 20)
sed -i '/^SETUP_TOKEN=/d' "$ENV_FILE"
echo "SETUP_TOKEN=$TOKEN" >> "$ENV_FILE"
grep -q '^ENV_PATH=' "$ENV_FILE" || echo "ENV_PATH=$ENV_FILE" >> "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "==> [4/6] Xizmat (24/7, qulasa o'zi qayta yonadi)..."
cat > /etc/systemd/system/$SERVICE.service <<UNIT
[Unit]
Description=Signal Pro trading bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable $SERVICE >/dev/null 2>&1
systemctl restart $SERVICE

echo "==> [5/6] HTTPS (Caddy + sslip.io)..."
IP=$(curl -s --max-time 15 https://api.ipify.org || true)
[ -z "$IP" ] && IP=$(hostname -I | awk '{print $1}')
HOST="${IP//./-}.sslip.io"
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
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
systemctl enable caddy >/dev/null 2>&1 || true
systemctl restart caddy

echo "==> [6/6] Firewall..."
ufw allow 22/tcp  >/dev/null 2>&1 || true
ufw allow 80/tcp  >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true
ufw delete allow 8000/tcp >/dev/null 2>&1 || true
yes | ufw enable >/dev/null 2>&1 || true

sleep 5
STATE=$(systemctl is-active $SERVICE || true)
echo
echo "================================================================"
echo " Tayyor. Bot holati: $STATE"
echo
echo " 1) Brauzerda oching:   https://$HOST/setup"
echo " 2) Sozlash kaliti:     $TOKEN"
echo
echo " Binance API kalitini shu IP ga bog'lang:  $IP"
echo " Sayt (dashboard):      https://$HOST"
echo "================================================================"
