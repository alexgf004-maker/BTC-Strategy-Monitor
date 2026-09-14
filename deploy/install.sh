#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root." >&2
  exit 1
fi

APP_DIR=/opt/btc-strategy-monitor
DATA_DIR=/var/lib/btc-strategy-monitor
ENV_FILE=/etc/btc-strategy-monitor.env
REPO_URL=https://github.com/alexgf004-maker/BTC-Strategy-Monitor.git

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates git openssl python3

if ! id btcmonitor >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin btcmonitor
fi

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --prune origin main
  git -C "$APP_DIR" checkout --detach origin/main
else
  rm -rf "$APP_DIR"
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

install -d -o btcmonitor -g btcmonitor "$DATA_DIR"
chown -R root:root "$APP_DIR"

if [ ! -f "$ENV_FILE" ]; then
  umask 077
  token="$(openssl rand -hex 32)"
  {
    echo "MONITOR_READ_TOKEN=$token"
    echo "MONITOR_ALLOWED_ORIGIN=https://btc-strategy-lab-dg2609.alex-gf004.chatgpt.site"
    echo "BTC_RUNTIME_DIR=$DATA_DIR"
    echo "HOST=127.0.0.1"
    echo "PORT=8080"
  } > "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"

install -m 0644 /dev/stdin /etc/systemd/system/btc-strategy-monitor.service <<'UNIT'
[Unit]
Description=BTC Strategy Monitor (paper only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=btcmonitor
Group=btcmonitor
WorkingDirectory=/opt/btc-strategy-monitor
EnvironmentFile=/etc/btc-strategy-monitor.env
ExecStart=/usr/bin/python3 -m monitor.service
Restart=always
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/btc-strategy-monitor

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now btc-strategy-monitor.service
sleep 2
systemctl --no-pager --full status btc-strategy-monitor.service || true
curl --fail --silent --show-error http://127.0.0.1:8080/healthz
echo
echo "Installed. Detailed endpoints remain private on 127.0.0.1:8080."
