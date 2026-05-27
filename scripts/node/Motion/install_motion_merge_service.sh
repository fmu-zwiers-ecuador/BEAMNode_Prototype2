#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="beam-motion-merge.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_SRC="$SCRIPT_DIR/$SERVICE_NAME"
SERVICE_DEST="/etc/systemd/system/$SERVICE_NAME"

if [[ "${1:-}" != "--standalone" ]]; then
  echo "This standalone service is optional and is NOT used by the default node setup."
  echo "By default, launcher.py starts and monitors motion_merge_worker.py."
  echo
  echo "To keep launcher.py as the only motion owner, do not install this service."
  echo "If you intentionally want standalone merge processing, run:"
  echo "  $0 --standalone"
  exit 1
fi

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
