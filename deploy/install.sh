#!/usr/bin/env bash
# One-shot bootstrap for Ubuntu (22.04+). Run as root.
# Installs python3.11, Caddy, creates a system user, sets up venv + systemd.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)." >&2; exit 1
fi

REPO_DIR="${REPO_DIR:-/opt/boniforce-mcp}"
SVC_USER="${SVC_USER:-boniforce}"
DOMAIN="${DOMAIN:-}"

if [[ -z "$DOMAIN" ]]; then
  read -r -p "Public domain (e.g. mcp.example.com): " DOMAIN
fi

echo "==> apt deps"
apt-get update
apt-get install -y python3.11 python3.11-venv python3.11-dev \
    debian-keyring debian-archive-keyring apt-transport-https curl gnupg

if ! command -v caddy >/dev/null; then
  echo "==> install Caddy"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
fi

if ! id -u "$SVC_USER" >/dev/null 2>&1; then
  echo "==> create user $SVC_USER"
  useradd -r -m -d "$REPO_DIR" -s /usr/sbin/nologin "$SVC_USER"
fi

mkdir -p "$REPO_DIR" /var/lib/boniforce-mcp
chown -R "$SVC_USER:$SVC_USER" "$REPO_DIR" /var/lib/boniforce-mcp

echo "==> sync source code into $REPO_DIR"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
    "$SRC_DIR/" "$REPO_DIR/"
chown -R "$SVC_USER:$SVC_USER" "$REPO_DIR"

echo "==> create venv"
sudo -u "$SVC_USER" python3.11 -m venv "$REPO_DIR/.venv"
sudo -u "$SVC_USER" "$REPO_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$SVC_USER" "$REPO_DIR/.venv/bin/pip" install "$REPO_DIR"

echo "==> generate keys + .env (only if missing)"
if [[ ! -f "$REPO_DIR/.env" ]]; then
  ENC_KEY="$(sudo -u "$SVC_USER" "$REPO_DIR/.venv/bin/boniforce-mcp" genkey)"
  SIGN_KEY="$(sudo -u "$SVC_USER" "$REPO_DIR/.venv/bin/boniforce-mcp" gensigning)"
  cat > "$REPO_DIR/.env" <<EOF
BF_ISSUER_URL=https://$DOMAIN
BF_DB_PATH=/var/lib/boniforce-mcp/db.sqlite
BF_ENCRYPTION_KEY=$ENC_KEY
BF_OAUTH_SIGNING_KEY="$(printf '%s' "$SIGN_KEY" | sed ':a;N;$!ba;s/\n/\\n/g')"
BF_API_BASE=https://api.boniforce.de
BF_HOST=127.0.0.1
BF_PORT=8000
BF_JWT_AUDIENCE=boniforce-mcp
EOF
  chown "$SVC_USER:$SVC_USER" "$REPO_DIR/.env"
  chmod 600 "$REPO_DIR/.env"
fi

echo "==> install systemd unit"
install -m 0644 "$REPO_DIR/deploy/boniforce-mcp.service" /etc/systemd/system/boniforce-mcp.service
systemctl daemon-reload
systemctl enable --now boniforce-mcp

echo "==> install Caddyfile"
sed "s/mcp\.example\.com/$DOMAIN/" "$REPO_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

echo
echo "==> firewall hint: ufw allow 80/tcp && ufw allow 443/tcp"
echo
echo "Users self-provision by pasting their Boniforce API key on first connect."
echo "No 'adduser' step is required."
echo
echo "Connector URL to add in Claude/ChatGPT:"
echo "   https://$DOMAIN/mcp"
