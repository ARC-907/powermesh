#!/usr/bin/env bash
# PowerMesh Collector Installer — Linux (systemd)
set -euo pipefail

INSTALL_DIR="${1:-/opt/powermesh-collector}"
CONFIG_SOURCE="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== PowerMesh Collector Installer ==="

# 1. Create install dir
sudo mkdir -p "$INSTALL_DIR"
sudo chown "$(whoami):$(whoami)" "$INSTALL_DIR"

# 2. Copy project files
echo "Copying from $PROJECT_ROOT..."
cp -r "$PROJECT_ROOT/src" "$INSTALL_DIR/src"
cp "$PROJECT_ROOT/pyproject.toml" "$INSTALL_DIR/"
cp "$PROJECT_ROOT/requirements.txt" "$INSTALL_DIR/"

# 3. Create venv
echo "Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
echo "Dependencies installed"

# 4. Config
mkdir -p "$INSTALL_DIR/config"
if [ -n "$CONFIG_SOURCE" ] && [ -f "$CONFIG_SOURCE" ]; then
    cp "$CONFIG_SOURCE" "$INSTALL_DIR/config/mesh.yaml"
    echo "Config copied from $CONFIG_SOURCE"
elif [ ! -f "$INSTALL_DIR/config/mesh.yaml" ]; then
    cp "$PROJECT_ROOT/config/mesh.yaml" "$INSTALL_DIR/config/mesh.yaml"
    echo "Default config copied — edit $INSTALL_DIR/config/mesh.yaml before starting"
fi

# 5. Data dir
mkdir -p "$INSTALL_DIR/data"

# 6. Firewall — allow collector port
if command -v ufw &>/dev/null; then
    echo "Opening port 8430/tcp via ufw..."
    sudo ufw allow 8430/tcp comment "PowerMesh Collector" || true
elif command -v firewall-cmd &>/dev/null; then
    echo "Opening port 8430/tcp via firewalld..."
    sudo firewall-cmd --permanent --add-port=8430/tcp || true
    sudo firewall-cmd --reload || true
fi

# 7. systemd unit file
UNIT_FILE="/etc/systemd/system/powermesh-collector.service"
echo "Creating systemd unit at $UNIT_FILE..."
sudo tee "$UNIT_FILE" > /dev/null <<EOF
[Unit]
Description=PowerMesh Power Monitoring Collector
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python -m src.collector $INSTALL_DIR/config/mesh.yaml
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=powermesh-collector

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable powermesh-collector

echo ""
echo "Installed to: $INSTALL_DIR"
echo "Service: powermesh-collector (enabled at boot)"
echo "Port: 8430/tcp"
echo ""
echo "To start now:  sudo systemctl start powermesh-collector"
echo "To check logs: journalctl -u powermesh-collector -f"
echo "To uninstall:  sudo systemctl disable --now powermesh-collector && sudo rm $UNIT_FILE"
