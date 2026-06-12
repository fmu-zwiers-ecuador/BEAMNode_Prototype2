#!/usr/bin/env bash
set -euo pipefail

# Run from anywhere. Defaults to the deployed Pi path, but also works from
# a checked-out repo if this script is still inside installation_bash/.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -d /home/pi/BEAMNode_Prototype2 ]]; then
  PROJECT_ROOT="/home/pi/BEAMNode_Prototype2"
fi

RUN_USER="${SUDO_USER:-pi}"
if ! id "$RUN_USER" >/dev/null 2>&1; then
  RUN_USER="pi"
fi

echo "=== BEAMNode Permission Setup ==="
echo "Project root: $PROJECT_ROOT"
echo "Runtime user: $RUN_USER"
echo

ensure_dir() {
  local dir="$1"
  mkdir -p "$dir"
  if id "$RUN_USER" >/dev/null 2>&1; then
    chown "$RUN_USER:$RUN_USER" "$dir" 2>/dev/null || true
  fi
  chmod u+rwx "$dir" 2>/dev/null || true
}

make_executable() {
  local path="$1"
  if [[ -f "$path" ]]; then
    chmod +x "$path"
    echo "OK executable: $path"
  else
    echo "SKIP missing:  $path"
  fi
}

echo "[1/4] Preparing runtime directories..."
ensure_dir /home/pi/data
ensure_dir /home/pi/shipping
ensure_dir /home/pi/logs
ensure_dir /home/pi/usbmnt
ensure_dir /home/pi/usbmnt/data

echo
echo "[2/4] Setting setup script permissions..."
make_executable "$PROJECT_ROOT/installation_bash/supervisor_setup.sh"
make_executable "$PROJECT_ROOT/installation_bash/node_setup.sh"
make_executable "$PROJECT_ROOT/installation_bash/set_retryservice.sh"
make_executable "$PROJECT_ROOT/installation_bash/set_internetservice.sh"
make_executable "$PROJECT_ROOT/installation_bash/wlan0.sh"
make_executable "$PROJECT_ROOT/installation_bash/set_script_permissions.sh"
make_executable "$PROJECT_ROOT/installation_bash/set_node_usb_backup_permissions.sh"

echo
echo "[3/4] Setting retry queue and shipping script permissions..."
make_executable "$PROJECT_ROOT/scripts/node/shipping_queuing/retryqueue.py"
make_executable "$PROJECT_ROOT/scripts/node/shipping_queuing/retrylogic.sh"
make_executable "$PROJECT_ROOT/scripts/node/shipping_queuing/ping_nodes_10min.py"
make_executable "$PROJECT_ROOT/scripts/node/shipping_queuing/shipping.py"
make_executable "$PROJECT_ROOT/scripts/node/shipping_queuing/move_shipping_to_beamdrive.sh"
make_executable "$PROJECT_ROOT/scripts/node/shipping_queuing/move_supervisor_data_to_beamdrive.sh"

echo
echo "[4/4] Setting Wi-Fi/internet helper permissions..."
make_executable "$PROJECT_ROOT/scripts/node/node_gateway/NodeInternet_Setup.sh"
make_executable "$PROJECT_ROOT/wlan1.sh"
make_executable "$PROJECT_ROOT/monitor_internet.sh"
make_executable "$PROJECT_ROOT/restart_internet.sh"

echo
echo "Fixing ownership on project logs/data helper folders..."
if id "$RUN_USER" >/dev/null 2>&1; then
  chown -R "$RUN_USER:$RUN_USER" \
    "$PROJECT_ROOT/scripts/node/shipping_queuing" \
    "$PROJECT_ROOT/scripts/node/node_gateway" \
    "$PROJECT_ROOT/installation_bash" \
    2>/dev/null || true
fi
chmod -R u+rwX \
  "$PROJECT_ROOT/scripts/node/shipping_queuing" \
  "$PROJECT_ROOT/scripts/node/node_gateway" \
  "$PROJECT_ROOT/installation_bash" \
  2>/dev/null || true

echo
echo "DONE. Core supervisor, retry queue, and Wi-Fi/ping scripts are executable."
