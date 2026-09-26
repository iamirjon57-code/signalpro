#!/usr/bin/env bash
# Signal Pro — VPS'ga o'rnatish skripti (Ubuntu 22.04 / 24.04 / Debian 12)
# Ishlatish:  bash setup.sh <github-repo-url>
set -euo pipefail

REPO="${1:-https://github.com/iamirjon57-code/signalpro.git}"
APP_DIR="/opt/signalpro"
SERVICE="signalpro"

echo "==> Paketlar o'rnatilmoqda..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ufw >/dev/null

echo "==> Kod yuklanmoqda: $REPO"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  rm -rf "$APP_DIR"
  git clone --depth 1 "$REPO" "$APP_DIR"
fi

echo "==> Python muhiti..."
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "DB_PATH=$APP_DIR/signals.db" >> "$APP_DIR/.env"
  echo "!! $APP_DIR/.env faylini to'ldiring (nano $APP_DIR/.env), keyin: systemctl restart $SERVICE"
fi
chmod 600 "$APP_DIR/.env"

echo "==> systemd xizmati..."
cat > /etc/systemd/system/$SERVICE.service <<UNIT
[Unit]
Description=Signal Pro trading bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable $SERVICE >/dev/null 2>&1
systemctl restart $SERVICE

echo "==> Firewall (SSH + 8000)..."
ufw allow 22/tcp >/dev/null 2>&1 || true
ufw allow 8000/tcp >/dev/null 2>&1 || true
yes | ufw enable >/dev/null 2>&1 || true

IP=$(curl -s --max-time 10 https://api.ipify.org || echo "?")
echo
echo "================================================"
echo " Tayyor."
echo " Serverning IP manzili : $IP"
echo " Shu IP'ni Binance API oq ro'yxatiga kiriting."
echo " Sayt                  : http://$IP:8000"
echo " Sozlamalar            : nano $APP_DIR/.env"
echo " Qayta ishga tushirish : systemctl restart $SERVICE"
echo " Loglar                : journalctl -u $SERVICE -f"
echo "================================================"
