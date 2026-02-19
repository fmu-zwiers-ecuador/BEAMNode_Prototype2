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
  batctl

# Create required data + log roots for the node runtime
sudo mkdir -p /home/pi/data /home/pi/shipping /home/pi/logs /home/pi/BEAMNode_Prototype2/logs
sudo chown -R pi:pi /home/pi/data /home/pi/shipping /home/pi/logs /home/pi/BEAMNode_Prototype2/logs

# Upgrade pip tooling (system-wide). --break-system-packages is for Debian/RPi OS policy.
# sudo python3 -m pip install --upgrade pip setuptools wheel --break-system-packages

# Adafruit + sensors
sudo python3 -m pip install --break-system-packages \
  adafruit-blinka==8.69.0 \
  adafruit-circuitpython-bme280==2.6.30 \
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

# ========================================
# === PART 4: Supervisor Gateway Setup ===
# ========================================

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

read -rp "Mesh interface (batman) [bat0]: " MESH_IF
MESH_IF=${MESH_IF:-bat0}

read -rp "Uplink internet interface [wlan1]: " UPLINK_IF
UPLINK_IF=${UPLINK_IF:-wlan1}

read -rp "Supervisor mesh IP/CIDR [10.42.0.30/16]: " MESH_IPCIDR
MESH_IPCIDR=${MESH_IPCIDR:-10.42.0.30/16}

MESH_IP="${MESH_IPCIDR%/*}"
MESH_PREFIX="${MESH_IPCIDR#*/}"

if [[ "$MESH_PREFIX" == "16" ]]; then
  DHCP_RANGE_START="10.42.0.50"
  DHCP_RANGE_END="10.42.0.200"
  DHCP_MASK="255.255.0.0"
  ALLOW_SUBNET="10.42.0.0/16"
elif [[ "$MESH_PREFIX" == "24" ]]; then
  NET_BASE="$(echo "$MESH_IP" | awk -F. '{print $1"."$2"."$3}')"
  DHCP_RANGE_START="${NET_BASE}.50"
  DHCP_RANGE_END="${NET_BASE}.200"
  DHCP_MASK="255.255.255.0"
  ALLOW_SUBNET="${NET_BASE}.0/24"
else
  DHCP_RANGE_START="$MESH_IP"
  DHCP_RANGE_END="$MESH_IP"
  DHCP_MASK="255.255.255.0"
  ALLOW_SUBNET="$MESH_IPCIDR"
fi

echo
echo "=== Summary ==="
echo "Mesh IF:      $MESH_IF"
echo "Uplink IF:    $UPLINK_IF"
echo "Mesh IP/CIDR: $MESH_IPCIDR"
echo "DHCP range:   $DHCP_RANGE_START - $DHCP_RANGE_END ($DHCP_MASK)"
echo "Chrony allow: $ALLOW_SUBNET"
echo

echo "[1/8] Installing required packages..."
apt-get update -y
apt-get install -y iptables iptables-persistent dnsmasq chrony conntrack || true

echo "[2/8] Writing dnsmasq config for mesh DHCP/DNS..."
cat >/etc/dnsmasq.d/batman-mesh.conf <<EOF
interface=$MESH_IF
bind-interfaces
domain-needed
bogus-priv

dhcp-range=$DHCP_RANGE_START,$DHCP_RANGE_END,$DHCP_MASK,12h
dhcp-option=option:router,$MESH_IP
dhcp-option=option:dns-server,$MESH_IP
EOF

systemctl enable dnsmasq
systemctl restart dnsmasq

echo "[3/8] Configuring chrony to serve time to mesh..."
CHRONY_CONF="/etc/chrony/chrony.conf"
grep -qE '^\s*pool\s+pool\.ntp\.org' "$CHRONY_CONF" || echo "pool pool.ntp.org iburst" >> "$CHRONY_CONF"
grep -qE "^\s*allow\s+$ALLOW_SUBNET" "$CHRONY_CONF" || echo "allow $ALLOW_SUBNET" >> "$CHRONY_CONF"
grep -qE '^\s*local\s+stratum\s+10' "$CHRONY_CONF" || echo "local stratum 10" >> "$CHRONY_CONF"

systemctl enable chrony
systemctl restart chrony

echo "[4/8] Creating a persistent systemd service for gateway setup..."
cat >/etc/systemd/system/batman-gateway.service <<EOF
[Unit]
Description=Batman Mesh Gateway (NAT + bat0 IP)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/batman-gateway-apply

[Install]
WantedBy=multi-user.target
EOF

echo "[5/8] Writing gateway apply script..."
cat >/usr/local/sbin/batman-gateway-apply <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

MESH_IF="{{MESH_IF}}"
UPLINK_IF="{{UPLINK_IF}}"
MESH_IPCIDR="{{MESH_IPCIDR}}"
MESH_IP="${MESH_IPCIDR%/*}"

ip link set "$MESH_IF" up || true
ip addr flush dev "$MESH_IF" || true
ip addr add "$MESH_IPCIDR" dev "$MESH_IF"

sysctl -w net.ipv4.ip_forward=1 >/dev/null

# Ensure FORWARD policy won't block NAT
iptables -P FORWARD ACCEPT || true

iptables -t nat -C POSTROUTING -o "$UPLINK_IF" -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -o "$UPLINK_IF" -j MASQUERADE

iptables -C FORWARD -i "$MESH_IF" -o "$UPLINK_IF" -j ACCEPT 2>/dev/null || \
  iptables -A FORWARD -i "$MESH_IF" -o "$UPLINK_IF" -j ACCEPT

iptables -C FORWARD -i "$UPLINK_IF" -o "$MESH_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  iptables -A FORWARD -i "$UPLINK_IF" -o "$MESH_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

command -v netfilter-persistent >/dev/null 2>&1 && netfilter-persistent save || true
EOF

python3 - <<PY
from pathlib import Path
p = Path("/usr/local/sbin/batman-gateway-apply")
txt = p.read_text()
txt = txt.replace("{{MESH_IF}}", "${MESH_IF}")
txt = txt.replace("{{UPLINK_IF}}", "${UPLINK_IF}")
txt = txt.replace("{{MESH_IPCIDR}}", "${MESH_IPCIDR}")
p.write_text(txt)
PY

chmod +x /usr/local/sbin/batman-gateway-apply

echo "[6/8] Enabling gateway service..."
systemctl daemon-reload
systemctl enable batman-gateway.service

echo "[7/8] Applying gateway config now..."
systemctl start batman-gateway.service

echo "[8/8] Quick checks:"
ip -br a | grep -E "\b$MESH_IF\b" || true
iptables -t nat -S | grep MASQUERADE || true
systemctl is-active dnsmasq || true
systemctl is-active chrony || true
chronyc tracking || true

echo
echo "DONE. Supervisor is now a mesh gateway."
