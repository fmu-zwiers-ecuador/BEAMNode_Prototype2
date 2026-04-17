#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo $0 <node|supervisor>"
  exit 1
fi

ROLE="${1:-}"

if [[ -z "$ROLE" ]]; then
  if [[ ! -t 0 ]]; then
    echo "Usage: sudo $0 <node|supervisor>"
    exit 1
  fi

  while true; do
    read -r -p "Choose role [node/supervisor]: " ROLE
    ROLE="$(echo "$ROLE" | tr '[:upper:]' '[:lower:]')"

    if [[ "$ROLE" == "node" || "$ROLE" == "supervisor" ]]; then
      break
    fi

    echo "Invalid choice. Please type 'node' or 'supervisor'."
  done
fi

if [[ "$ROLE" != "node" && "$ROLE" != "supervisor" ]]; then
  echo "Usage: sudo $0 <node|supervisor>"
  exit 1
fi

PROJECT_ROOT="/home/pi/BEAMNode_Prototype2"
LORA_DIR="$PROJECT_ROOT/scripts/lora"
SYSTEMD_DIR="/etc/systemd/system"
LOG_DIR="$PROJECT_ROOT/logs"
CONFIG_PATH="$PROJECT_ROOT/scripts/node/config.json"

get_lora_enabled() {
  /usr/bin/python3 - "$CONFIG_PATH" <<'PY'
import json
import sys

config_path = sys.argv[1]
try:
  with open(config_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

  lora_cfg = cfg.get("lora", False)
  if isinstance(lora_cfg, bool):
    print("true" if lora_cfg else "false")
  elif isinstance(lora_cfg, dict):
    print("true" if bool(lora_cfg.get("enabled", False)) else "false")
  else:
    print("false")
except Exception:
  print("false")
PY
}

LORA_ENABLED="$(get_lora_enabled)"

mkdir -p "$LOG_DIR"

chmod +x "$LORA_DIR/node_send.py" "$LORA_DIR/supervisor_receive.py"

if [[ "$ROLE" == "node" ]]; then
  echo "Config check: lora.enabled=$LORA_ENABLED"
  echo "[1/4] Installing node sender service + timer..."
  cp "$LORA_DIR/lora-node-send.service" "$SYSTEMD_DIR/"
  cp "$LORA_DIR/lora-node-send.timer" "$SYSTEMD_DIR/"

  echo "[2/4] Reloading systemd..."
  systemctl daemon-reload

  if [[ "$LORA_ENABLED" == "true" ]]; then
    echo "[3/4] Enabling sender timer (runs daily at 14:00)..."
    systemctl enable --now lora-node-send.timer
    systemctl restart lora-node-send.timer
    echo "[4/4] Node sender timer active."
    systemctl list-timers lora-node-send.timer --no-pager || true
  else
    echo "[3/4] lora.enabled is false; disabling LoRa sender timer..."
    systemctl disable --now lora-node-send.timer 2>/dev/null || true
    systemctl stop lora-node-send.service 2>/dev/null || true
    echo "[4/4] LoRa sender is installed but not scheduled."
  fi
fi

if [[ "$ROLE" == "supervisor" ]]; then
  echo "[1/3] Installing supervisor receiver service..."
  cp "$LORA_DIR/lora-supervisor-receive.service" "$SYSTEMD_DIR/"

  echo "[2/3] Reloading systemd..."
  systemctl daemon-reload

  echo "[3/3] Enabling receiver service..."
  systemctl enable --now lora-supervisor-receive.service
  systemctl restart lora-supervisor-receive.service

  echo "Supervisor receiver service state:"
  systemctl is-active lora-supervisor-receive.service || true
fi

echo "Done. Logs are in $LOG_DIR"
