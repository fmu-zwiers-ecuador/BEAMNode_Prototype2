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
LOG_DIR="/home/pi/logs"
CONFIG_PATH="$PROJECT_ROOT/scripts/node/config.json"

read_lora_config() {
  /usr/bin/python3 - "$CONFIG_PATH" <<'PY'
import json
import re
import sys

config_path = sys.argv[1]


def parse_enabled(cfg):
  global_cfg = cfg.get("global", {}) if isinstance(cfg, dict) else {}
  val = global_cfg.get("lora_enabled", None)
  if isinstance(val, bool):
    return val

  # Backward compatibility with older config style: {"lora": true|{"enabled": true}}
  lora_cfg = cfg.get("lora", False) if isinstance(cfg, dict) else False
  if isinstance(lora_cfg, bool):
    return lora_cfg
  if isinstance(lora_cfg, dict):
    return bool(lora_cfg.get("enabled", False))
  return False


def parse_hhmm(value, default):
  if not isinstance(value, str):
    return default

  s = value.strip()
  m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", s)
  if not m:
    return default

  hh = int(m.group(1))
  mm = int(m.group(2))
  return f"{hh:02d}:{mm:02d}"


def parse_receive_window_minutes(cfg):
  global_cfg = cfg.get("global", {}) if isinstance(cfg, dict) else {}
  raw = global_cfg.get("lora_receive_window_minutes", 60)
  try:
    val = int(raw)
  except Exception:
    return 60

  # Keep sensible bounds: 1 minute to 24 hours
  if val < 1:
    return 1
  if val > 1440:
    return 1440
  return val


try:
  with open(config_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

  global_cfg = cfg.get("global", {}) if isinstance(cfg, dict) else {}

  enabled = parse_enabled(cfg)
  send_time = parse_hhmm(global_cfg.get("lora_send_time", "14:00"), "14:00")
  recv_time = parse_hhmm(global_cfg.get("lora_receive_time", "14:00"), "14:00")
  recv_window = parse_receive_window_minutes(cfg)

  print(f"LORA_ENABLED={'true' if enabled else 'false'}")
  print(f"LORA_SEND_TIME={send_time}")
  print(f"LORA_RECEIVE_TIME={recv_time}")
  print(f"LORA_RECEIVE_WINDOW_MINUTES={recv_window}")
except Exception:
  print("LORA_ENABLED=false")
  print("LORA_SEND_TIME=14:00")
  print("LORA_RECEIVE_TIME=14:00")
  print("LORA_RECEIVE_WINDOW_MINUTES=60")
PY
}

eval "$(read_lora_config)"

to_calendar_time() {
  local hhmm="$1"
  if [[ "$hhmm" =~ ^([01][0-9]|2[0-3]):([0-5][0-9])$ ]]; then
    echo "${hhmm}:00"
  else
    echo "14:00:00"
  fi
}

NODE_ONCALENDAR="*-*-* $(to_calendar_time "$LORA_SEND_TIME")"
SUPERVISOR_ONCALENDAR="*-*-* $(to_calendar_time "$LORA_RECEIVE_TIME")"

mkdir -p "$LOG_DIR"

chmod +x "$LORA_DIR/node_send.py" "$LORA_DIR/supervisor_receive.py"

if [[ "$ROLE" == "node" ]]; then
  echo "Config check: lora.enabled=$LORA_ENABLED, send_time=$LORA_SEND_TIME"
  echo "[1/5] Installing node sender service + timer..."
  cp "$LORA_DIR/lora-node-send.service" "$SYSTEMD_DIR/"
  cat >"$SYSTEMD_DIR/lora-node-send.timer" <<EOF
[Unit]
Description=Run LoRa Node Sender daily at $LORA_SEND_TIME

[Timer]
OnCalendar=$NODE_ONCALENDAR
Persistent=true
Unit=lora-node-send.service

[Install]
WantedBy=timers.target
EOF

  echo "[2/5] Reloading systemd..."
  systemctl daemon-reload

  if [[ "$LORA_ENABLED" == "true" ]]; then
    echo "[3/5] Enabling sender timer (runs daily at $LORA_SEND_TIME)..."
    systemctl enable --now lora-node-send.timer
    systemctl restart lora-node-send.timer
    echo "[4/5] Node sender timer active."
    systemctl list-timers lora-node-send.timer --no-pager || true
    echo "[5/5] Node LoRa scheduling setup complete."
  else
    echo "[3/5] lora.enabled is false; disabling LoRa sender timer..."
    systemctl disable --now lora-node-send.timer 2>/dev/null || true
    systemctl stop lora-node-send.service 2>/dev/null || true
    echo "[4/5] LoRa sender is installed but not scheduled."
    echo "[5/5] Node LoRa scheduling setup complete."
  fi
fi

if [[ "$ROLE" == "supervisor" ]]; then
  echo "Config check: lora.enabled=$LORA_ENABLED, receive_time=$LORA_RECEIVE_TIME, receive_window_minutes=$LORA_RECEIVE_WINDOW_MINUTES"
  echo "[1/6] Installing supervisor receiver service + timer..."
  cp "$LORA_DIR/lora-supervisor-receive.service" "$SYSTEMD_DIR/"

  mkdir -p "$SYSTEMD_DIR/lora-supervisor-receive.service.d"
  cat >"$SYSTEMD_DIR/lora-supervisor-receive.service.d/schedule.conf" <<EOF
[Service]
Restart=no
RuntimeMaxSec=${LORA_RECEIVE_WINDOW_MINUTES}m
EOF

  cat >"$SYSTEMD_DIR/lora-supervisor-receive.timer" <<EOF
[Unit]
Description=Run LoRa Supervisor Receiver daily at $LORA_RECEIVE_TIME

[Timer]
OnCalendar=$SUPERVISOR_ONCALENDAR
Persistent=true
Unit=lora-supervisor-receive.service

[Install]
WantedBy=timers.target
EOF

  echo "[2/6] Reloading systemd..."
  systemctl daemon-reload

  echo "[3/6] Disabling direct boot service (schedule-driven mode)..."
  systemctl disable --now lora-supervisor-receive.service 2>/dev/null || true

  if [[ "$LORA_ENABLED" == "true" ]]; then
    echo "[4/6] Enabling receiver timer (runs daily at $LORA_RECEIVE_TIME)..."
    systemctl enable --now lora-supervisor-receive.timer
    systemctl restart lora-supervisor-receive.timer
    echo "[5/6] Supervisor receiver timer active."
    systemctl list-timers lora-supervisor-receive.timer --no-pager || true
  else
    echo "[4/6] lora.enabled is false; disabling LoRa receiver timer..."
    systemctl disable --now lora-supervisor-receive.timer 2>/dev/null || true
    systemctl stop lora-supervisor-receive.service 2>/dev/null || true
    echo "[5/6] LoRa receiver is installed but not scheduled."
  fi

  echo "[6/6] Supervisor LoRa scheduling setup complete."
fi

echo "Done. Logs are in $LOG_DIR"
