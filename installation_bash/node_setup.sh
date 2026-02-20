#!/bin/bash

# ============================================================================
# === node_setup.sh: This script sets up a new node, ready to be deployed. ===
# ============================================================================

# =====================================================================
# === PART 1: Install all necessary libraries needed for for set up ===
# =====================================================================

set -euo pipefail

sudo apt update
sudo apt install -y \
  python3-pip python3-venv \
  libportaudio2 libjack0 \
  python3-pyaudio \
  batctl \
  chrony

# Create required data + log roots for the node runtime
sudo mkdir -p /home/pi/data /home/pi/shipping /home/pi/logs /home/pi/BEAMNode_Prototype2/logs
sudo chown -R pi:pi /home/pi/data /home/pi/shipping /home/pi/logs /home/pi/BEAMNode_Prototype2/logs

# Upgrade pip tooling (system-wide). --break-system-packages is for Debian/RPi OS policy.
# sudo python3 -m pip install --upgrade pip setuptools wheel --break-system-packages

# Adafruit + sensors
sudo python3 -m pip install --break-system-packages \
  adafruit-blinka==8.69.0 \
  adafruit-circuitpython-bme280==2.6.30 \
  adafruit-circuitpython-bme680==3.5.0 \
  adafruit-circuitpython-tsl2591==1.4.6 \
  adafruit-circuitpython-ahtx0==1.0.28

# NOTE:
# Do NOT apt install portaudio19-dev here (it can force exact-matching -dev deps and break on some Pi repos).
# If you ever *must* use pip's PyAudio instead, the typical requirement is:
#   sudo apt install libportaudio2 libjack0
#   pip3 install pyaudio
# (ideally inside a venv).  [oai_citation:1‡piwheels.org](https://www.piwheels.org/project/pyaudio/?utm_source=chatgpt.com)

#sudo apt upgrade -y


# ======================================
# === PART 2: Autostart installation ===
# ======================================

# Location: /home/pi/BEAMNode_Prototype2/autostartinstall.sh

# 1. Configuration
PROJECT_ROOT="/home/pi/BEAMNode_Prototype2"
NODE_DIR="$PROJECT_ROOT/scripts/node"
SERVICE_SRC="$PROJECT_ROOT/beamnode.service"
SERVICE_NAME="beamnode.service"
LOG_DIR="$PROJECT_ROOT/logs"

# 2. Create Required Folders
echo "[1/4] Preparing directories..."
mkdir -p "/home/pi/data"
mkdir -p "/home/pi/shipping"
mkdir -p "$LOG_DIR"

# 3. Set Permissions
echo "[2/4] Setting execution permissions..."
chmod +x "$NODE_DIR/launcher.py"
chmod +x "$NODE_DIR/scheduler.py"
chmod +x "$NODE_DIR/sensor_detection/detect.py"
chmod +x "$NODE_DIR/shipping_queuing/shipping.py"

# 4. Install Systemd Service
echo "[3/4] Registering systemd service..."
if [ -f "$SERVICE_SRC" ]; then
    # Copy from project root to system services folder
    sudo cp "$SERVICE_SRC" /etc/systemd/system/
    
    # Reload and Enable
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    
    # Start the service now
    sudo systemctl restart "$SERVICE_NAME"
    echo "Service $SERVICE_NAME installed and started."
else
    echo "ERROR: Could not find $SERVICE_SRC"
    echo "Please ensure beamnode.service is in $PROJECT_ROOT"
    exit 1
fi

# 5. Verification
echo "[4/4] Verifying system status..."
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "------------------------------------------------"
    echo "SUCCESS: Installation Complete!"
    echo "The Launcher is now running in the background."
    echo "------------------------------------------------"
else
    echo "Service installed but failed to start."
    echo "Check logs with: journalctl -u $SERVICE_NAME -f"
fi

# ===================================
# === PART 3: BATMAN Installation ===
# ===================================

# ========================================
# BATMAN-adv Automatic Setup Script (Interactive)
# Tested on Debian / Raspberry Pi OS
# ========================================

set -e

echo "=== BATMAN-adv Setup Script ==="
echo

# --- Ask for configuration with defaults ---
read -p "Enter ad-hoc network name (SSID) [myadhoc]: " NETWORK_NAME
NETWORK_NAME=${NETWORK_NAME:-myadhoc}

read -p "Enter frequency in MHz (e.g. 2412 for channel 1) [2412]: " FREQUENCY
FREQUENCY=${FREQUENCY:-2412}

read -p "Enter static IP for bat0 (e.g. 10.42.0.2/16) [10.42.0.2/16]: " STATIC_IP
STATIC_IP=${STATIC_IP:-10.42.0.2/16}

echo
echo "Using configuration:"
echo "  SSID:       $NETWORK_NAME"
echo "  Frequency:  $FREQUENCY MHz"
echo "  IP Address: $STATIC_IP"
echo
read -p "Press Enter to continue or Ctrl+C to cancel..."

# --- Create BATMAN startup script ---
echo "[1/4] Creating /usr/local/bin/start-batman.sh ..."
cat <<EOF | sudo tee /usr/local/bin/start-batman.sh >/dev/null
#!/bin/bash
# ========================================
# BATMAN-adv Startup Script
# ========================================

echo "Starting BATMAN-adv mesh setup..."

# Stop and disable conflicting services
systemctl stop wpa_supplicant 2>/dev/null || true
systemctl disable wpa_supplicant 2>/dev/null || true
systemctl stop NetworkManager 2>/dev/null || true
systemctl disable NetworkManager 2>/dev/null || true

# Load BATMAN kernel module
modprobe batman-adv

# Configure wlan0 for ad-hoc mode
ip link set wlan0 down
iw dev wlan0 set type ibss
ip link set wlan0 up
iw dev wlan0 ibss join $NETWORK_NAME $FREQUENCY

# Add wlan0 to BATMAN
batctl if add wlan0
ip link set up dev bat0

# Assign static IP
ip addr add $STATIC_IP dev bat0

echo "BATMAN-adv setup complete!"
EOF

sudo chmod +x /usr/local/bin/start-batman.sh

# --- Create systemd service ---
echo "[2/4] Creating /etc/systemd/system/batman.service ..."
cat <<EOF | sudo tee /etc/systemd/system/batman.service >/dev/null
[Unit]
Description=BATMAN-adv Mesh Network
After=network.target sys-subsystem-net-devices-wlan0.device
Wants=sys-subsystem-net-devices-wlan0.device

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash /usr/local/bin/start-batman.sh

[Install]
WantedBy=multi-user.target
EOF

# --- Reload systemd and enable service ---
echo "[3/4] Reloading systemd and enabling service ..."
sudo systemctl daemon-reload
sudo systemctl enable batman.service

# --- Start service immediately ---
echo "[4/4] Starting BATMAN service ..."
sudo systemctl start batman.service

echo
echo "BATMAN-adv setup complete!"
echo "To verify, run: sudo systemctl status batman.service"
echo "Then check mesh neighbors with: sudo batctl n"

# ===================================
# === PART 4: Node Internet Setup ===
# ===================================

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

read -rp "Mesh interface [bat0]: " MESH_IF
MESH_IF=${MESH_IF:-bat0}

read -rp "Supervisor mesh IP (NTP/DNS gateway) [10.42.0.30]: " SUP_IP
SUP_IP=${SUP_IP:-10.42.0.30}

echo
echo "=== Summary ==="
echo "Mesh IF:   $MESH_IF"
echo "Supervisor:$SUP_IP"
echo

echo "[1/9] Installing required packages..."
apt-get update -y
apt-get install -y chrony isc-dhcp-client rfkill || true

echo "[2/9] Prevent dhclient DNS write issues (resolv.conf protected)..."
mkdir -p /etc/dhcp/dhclient-enter-hooks.d
cat >/etc/dhcp/dhclient-enter-hooks.d/nodns <<'EOF'
make_resolv_conf() { :; }
EOF
chmod +x /etc/dhcp/dhclient-enter-hooks.d/nodns

echo "[3/9] Create boot helper script (rfkill -> mesh -> DHCP -> time)..."
cat >/usr/local/sbin/mesh-boot.sh <<EOF
#!/usr/bin/env bash
set -e

MESH_IF="$MESH_IF"
SUP_IP="$SUP_IP"

# Unblock Wi-Fi if rfkill is on
command -v rfkill >/dev/null 2>&1 && rfkill unblock all || true

# Bring up mesh iface if it exists
ip link set "\$MESH_IF" up 2>/dev/null || true

# DHCP (this is what you were doing manually)
dhclient -r "\$MESH_IF" 2>/dev/null || true
dhclient "\$MESH_IF" 2>/dev/null || true

# If resolvectl exists, pin DNS to supervisor (best effort)
if command -v resolvectl >/dev/null 2>&1; then
  resolvectl dns "\$MESH_IF" "\$SUP_IP" || true
  resolvectl domain "\$MESH_IF" "~." || true
else
  echo "nameserver \$SUP_IP" > /etc/resolv.conf || true
fi

# Time sync step (best effort)
command -v chronyc >/dev/null 2>&1 && chronyc -a makestep || true
EOF
chmod +x /usr/local/sbin/mesh-boot.sh

echo "[4/9] Ensure chrony uses supervisor and steps quickly..."
CHRONY_CONF="/etc/chrony/chrony.conf"

# Create the chrony config dir/file if they don't exist yet
mkdir -p /etc/chrony
touch "$CHRONY_CONF"

# Remove existing server line for this SUP_IP duplicates (safe)
grep -vE "^\s*server\s+$SUP_IP\b" "$CHRONY_CONF" > /tmp/chrony.conf.tmp || true
cat /tmp/chrony.conf.tmp > "$CHRONY_CONF"

tmpfile="$(mktemp)"
{
  echo "server $SUP_IP iburst prefer"
  echo "makestep 1.0 3"
  cat "$CHRONY_CONF"
} > "$tmpfile"
cat "$tmpfile" > "$CHRONY_CONF"
rm -f "$tmpfile"

systemctl enable chrony
systemctl restart chrony

echo "[5/9] Create systemd service to run mesh-boot on every startup..."
cat >/etc/systemd/system/mesh-boot.service <<'EOF'
[Unit]
Description=Batman mesh boot: rfkill unblock + DHCP on bat0 + time sync
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/mesh-boot.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable mesh-boot.service
systemctl start mesh-boot.service || true

echo "[6/9] (Optional) Add boot-time force sync service (kept from your original)..."
cat >/etc/systemd/system/mesh-timesync.service <<EOF
[Unit]
Description=Force time sync over mesh after network is up
After=network-online.target chrony.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/chronyc -a makestep
ExecStart=/usr/bin/chronyc -a 'burst 4/4'
ExecStart=/usr/bin/chronyc -a tracking

[Install]
WantedBy=multi-user.target
EOF

systemctl enable mesh-timesync.service
systemctl start mesh-timesync.service || true

echo
echo "Quick checks:"
ip -br a | grep -E "\b$MESH_IF\b" || true
ip route | head -n 5 || true
ping -c 2 "$SUP_IP" || true
ping -c 2 8.8.8.8 || true
ping -c 2 google.com || true
date
chronyc tracking || true

echo
echo "DONE. After reboot, DHCP on bat0 will run automatically (no more manual dhclient)."
