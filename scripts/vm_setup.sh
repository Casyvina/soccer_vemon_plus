#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# vm_setup.sh — One-shot setup for soccer_vemon_plus on a fresh Ubuntu 22.04 VM
#
# Run once after uploading the project:
#   bash scripts/vm_setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # project root (one level up from scripts/)
VENV_DIR="$APP_DIR/.venv"
LOG_DIR="$HOME/logs"
SERVICE_NAME="soccer-daemon"

echo "=== App dir  : $APP_DIR"
echo "=== Venv dir : $VENV_DIR"
echo "=== Log dir  : $LOG_DIR"
echo "=== OS       : $(lsb_release -ds 2>/dev/null || cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2)"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/6] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-pip python3-venv python3-dev \
    software-properties-common \
    wget curl ca-certificates gnupg \
    libglib2.0-0 libnss3 libfontconfig1 \
    libdbus-glib-1-2 libxt6

# ── 2. Firefox (from Mozilla's official apt repo — works on 22.04/24.04/26.04) ──
echo "[2/6] Installing Firefox..."
# Remove snap firefox if present (minimal images may not have snap at all)
sudo snap remove firefox 2>/dev/null || true

# Use Mozilla's signed apt repo (supports all Ubuntu versions)
wget -q https://packages.mozilla.org/apt/repo-signing-key.gpg \
    -O /tmp/mozilla-key.gpg
sudo install -D -o root -g root -m 644 \
    /tmp/mozilla-key.gpg /etc/apt/keyrings/mozilla-key.gpg

echo "deb [signed-by=/etc/apt/keyrings/mozilla-key.gpg] https://packages.mozilla.org/apt mozilla main" \
    | sudo tee /etc/apt/sources.list.d/mozilla.list > /dev/null

# Pin Mozilla repo above any snap/distro version
echo '
Package: *
Pin: origin packages.mozilla.org
Pin-Priority: 1001
' | sudo tee /etc/apt/preferences.d/mozilla > /dev/null

sudo apt-get update -qq
sudo apt-get install -y -qq firefox

FIREFOX_BIN=$(which firefox)
echo "    Firefox: $($FIREFOX_BIN --version)"

# ── 3. geckodriver ────────────────────────────────────────────────────────────
echo "[3/6] Installing geckodriver..."
GECKO_VER=$(curl -s "https://api.github.com/repos/mozilla/geckodriver/releases/latest" \
    | grep '"tag_name"' | cut -d'"' -f4)
GECKO_URL="https://github.com/mozilla/geckodriver/releases/download/${GECKO_VER}/geckodriver-${GECKO_VER}-linux64.tar.gz"
TMP=$(mktemp -d)
wget -q "$GECKO_URL" -O "$TMP/gecko.tar.gz"
tar -xzf "$TMP/gecko.tar.gz" -C "$TMP"
sudo mv "$TMP/geckodriver" /usr/local/bin/geckodriver
sudo chmod +x /usr/local/bin/geckodriver
rm -rf "$TMP"
echo "    geckodriver: $(geckodriver --version | head -1)"

# ── 4. Python venv + dependencies ────────────────────────────────────────────
echo "[4/6] Creating Python venv and installing packages..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt" -q
echo "    Packages installed."

# ── 5. Logs directory ────────────────────────────────────────────────────────
echo "[5/6] Creating logs directory..."
mkdir -p "$LOG_DIR"

# ── 6. Systemd service ───────────────────────────────────────────────────────
echo "[6/6] Installing systemd service..."

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Soccer VM Daemon (odds + details + HT scores)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/python src/headless_daemon.py --base-dir $HOME/soccer_data
Restart=always
RestartSec=30
StandardOutput=append:$LOG_DIR/daemon.log
StandardError=append:$LOG_DIR/daemon.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo " Setup complete!"
echo ""
echo " NEXT: make sure your credentials are in:"
echo "   $APP_DIR/src/assets/.env"
echo ""
echo " Then start the daemon:"
echo "   sudo systemctl start $SERVICE_NAME"
echo ""
echo " Useful commands:"
echo "   sudo systemctl status $SERVICE_NAME   # check if running"
echo "   sudo systemctl stop $SERVICE_NAME     # stop"
echo "   sudo systemctl restart $SERVICE_NAME  # restart"
echo "   tail -f $LOG_DIR/daemon.log           # watch live logs"
echo "═══════════════════════════════════════════════════════════"
