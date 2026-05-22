#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="beam-motion-merge.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_SRC="$SCRIPT_DIR/$SERVICE_NAME"
SERVICE_DEST="/etc/systemd/system/$SERVICE_NAME"

echo "[1/4] Installing $SERVICE_NAME"
sudo cp "$SERVICE_SRC" "$SERVICE_DEST"

echo "[2/4] Reloading systemd"
sudo systemctl daemon-reload

echo "[3/4] Enabling $SERVICE_NAME"
sudo systemctl enable "$SERVICE_NAME"

echo "[4/4] Starting $SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "Done. Check status with:"
echo "  sudo systemctl status $SERVICE_NAME"
echo "  journalctl -u $SERVICE_NAME -f"
