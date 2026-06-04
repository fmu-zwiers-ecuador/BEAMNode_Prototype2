#!/bin/bash

# ========================================================================================
# === supervisor_setup.sh: This script sets up a new supervisor, ready to be deployed. ===
# ========================================================================================

# Must be run as root (sudo ./supervisor_setup.sh)
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: Run as root: sudo $0"
  exit 1
fi

set -euo pipefail

# =====================================================================
# === PART 1: Install all necessary libraries needed for for set up ===
# === NOTE: Internet is required for this section ONLY.             ===
# ===        All packages must be installed here before going       ===
# ===        offline. Parts 2-4 do not require internet.            ===
# =====================================================================

read -rp "Do you want to install/update packages? (requires internet) [y/n]: " DO_INSTALL
if [[ "${DO_INSTALL,,}" == "y" ]]; then
  echo "=== Running PART 1: Package Installation ==="

  echo "Installing required packages..."
  apt-get update -y
  apt-get install -y iptables iptables-persistent dnsmasq chrony conntrack

  apt update
  apt install -y \
    python3-pip python3-venv \
    libportaudio2 libjack0 \
    python3-pyaudio \
    batctl \
    chrony \
    isc-dhcp-client \
    rfkill

  # Create required data + log roots for the node runtime
  mkdir -p /home/pi/data /home/pi/shipping /home/pi/logs /home/pi/BEAMNode_Prototype2/logs
  chown -R pi:pi /home/pi/data /home/pi/shipping /home/pi/logs /home/pi/BEAMNode_Prototype2/logs

  # Adafruit + sensors
  python3 -m pip install --break-system-packages \
    adafruit-blinka==8.69.0 \
    adafruit-circuitpython-bme280==2.6.30 \
    adafruit-circuitpython-bme680==3.5.0 \
    adafruit-circuitpython-tsl2591==1.4.6 \
    adafruit-circuitpython-ahtx0==1.0.28 \
    adafruit-circuitpython-rfm9x==1.0.3 

  echo "=== PART 1 complete. Continuing to PART 2... ==="
else
  echo "=== Skipping PART 1 (no internet install). Continuing to PART 2... ==="
fi


# ===================================
# === PART 2: BATMAN Installation ===
# ===================================

echo "=== BATMAN-adv Setup Script ==="
echo

# --- Ask for configuration with defaults ---
read -p "Enter ad-hoc network name (SSID) [myadhoc]: " NETWORK_NAME
NETWORK_NAME=${NETWORK_NAME:-myadhoc}

read -p "Enter frequency in MHz (e.g. 2412 for channel 1) [2412]: " FREQUENCY
FREQUENCY=${FREQUENCY:-2412}

read -p "Enter static IP for bat0 (e.g. 10.42.0.40/16) [10.42.0.30/16]: " STATIC_IP
STATIC_IP=${STATIC_IP:-10.42.0.30/16}

echo
echo "Using configuration:"
echo "  SSID:       $NETWORK_NAME"
echo "  Frequency:  $FREQUENCY MHz"
echo "  IP Address: $STATIC_IP"
echo
read -p "Press Enter to continue or Ctrl+C to cancel..."

# -----------------------------------------------------------------------
# FIX: Prevent dhcpcd from overwriting the static IP on wlan0 and bat0.
# dhcpcd runs by default on Raspberry Pi OS and will assign a random DHCP
# address on top of whatever IP batman sets, which is why 'ip a' shows the
# wrong address after the script finishes.
# -----------------------------------------------------------------------
echo "[PRE] Configuring dhcpcd to leave wlan0 and bat0 alone..."
DHCPCD_CONF="/etc/dhcpcd.conf"
if [ -f "$DHCPCD_CONF" ]; then
  # Remove any existing denyinterfaces lines for these so we don't duplicate
  sed -i '/^denyinterfaces wlan0/d' "$DHCPCD_CONF"
  sed -i '/^denyinterfaces bat0/d' "$DHCPCD_CONF"
  echo "denyinterfaces wlan0" >> "$DHCPCD_CONF"
  echo "denyinterfaces bat0"  >> "$DHCPCD_CONF"
  echo "  dhcpcd will no longer touch wlan0 or bat0 ✓"
else
  echo "  /etc/dhcpcd.conf not found — skipping (may be using NetworkManager only)"
fi

# Also stop/disable NetworkManager and wpa_supplicant system-wide so they
# cannot come back up after a reboot and tear down the IBSS interface.
echo "[PRE] Disabling NetworkManager and wpa_supplicant permanently..."
systemctl stop    NetworkManager  2>/dev/null || true
systemctl disable NetworkManager  2>/dev/null || true
systemctl stop    wpa_supplicant  2>/dev/null || true
systemctl disable wpa_supplicant  2>/dev/null || true

# --- Create BATMAN startup script ---
echo "[1/4] Creating /usr/local/bin/start-batman.sh ..."
cat <<EOF | tee /usr/local/bin/start-batman.sh >/dev/null
#!/bin/bash
set -e
# ========================================
# BATMAN-adv Startup Script
# ========================================

log() { echo "[start-batman] \$*" | tee -a /var/log/batman-start.log; }

log "Starting BATMAN-adv mesh setup..."

# Unblock wireless radio (must happen before touching wlan0)
log "Unblocking wireless radio..."
rfkill unblock all || true
sleep 1

# Load BATMAN kernel module
log "Loading batman-adv kernel module..."
modprobe batman-adv
sleep 1

# Bring wlan0 down cleanly before changing mode
log "Configuring wlan0 for IBSS (ad-hoc) mode..."
ip link set wlan0 down
sleep 1

# Set IBSS mode (must be done while interface is DOWN)
iw dev wlan0 set type ibss

# Bring wlan0 back up
ip link set wlan0 up
sleep 2

# Join the IBSS network
log "Joining IBSS network: $NETWORK_NAME @ ${FREQUENCY} MHz..."
iw dev wlan0 ibss join $NETWORK_NAME $FREQUENCY
sleep 3  # critical: ibss join is async — batman needs wlan0 fully in IBSS mode

# Add wlan0 to BATMAN-adv (creates bat0)
log "Adding wlan0 to batman-adv..."
batctl if add wlan0
sleep 2

# Bring bat0 up
log "Bringing up bat0..."
ip link set up dev bat0
sleep 1

# Assign static IP — always force it here so it survives any race conditions
log "Assigning static IP $STATIC_IP to bat0..."
# Remove any existing addresses first so we don't accumulate duplicates
ip addr flush dev bat0 2>/dev/null || true
ip addr add $STATIC_IP dev bat0
log "bat0 address: \$(ip addr show bat0 | grep 'inet ')"
log "BATMAN-adv setup complete!"
EOF

chmod +x /usr/local/bin/start-batman.sh

# --- Create systemd service ---
# FIX: Added Conflicts= so systemd itself prevents NetworkManager/wpa_supplicant
#      from starting after batman and tearing down the IBSS interface on reboot.
echo "[2/4] Creating /etc/systemd/system/batman.service ..."
cat <<EOF | tee /etc/systemd/system/batman.service >/dev/null
[Unit]
Description=BATMAN-adv Mesh Network
After=network.target sys-subsystem-net-devices-wlan0.device
Wants=sys-subsystem-net-devices-wlan0.device
Conflicts=NetworkManager.service wpa_supplicant.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=-/bin/systemctl stop NetworkManager
ExecStartPre=-/bin/systemctl stop wpa_supplicant
ExecStart=/bin/bash /usr/local/bin/start-batman.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# --- Reload systemd and enable service ---
echo "[3/4] Reloading systemd and enabling service ..."
systemctl daemon-reload
systemctl enable batman.service

# --- Start service immediately ---
echo "[4/4] Starting BATMAN service ..."
systemctl start batman.service

echo
echo "BATMAN-adv setup complete!"
echo "To verify, run: sudo systemctl status batman.service"
echo "Then check mesh neighbors with: sudo batctl n"

# =========================================
# === PART 3: Supervisor Internet Setup ===
# =========================================

set -euo pipefail

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

echo "[1/8] Writing dnsmasq config for mesh DHCP/DNS..."
#Ensure the directory exist before creating it
mkdir -p /etc/dnsmasq.d

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

echo "[2/8] Configuring chrony to serve time to mesh..."
CHRONY_CONF="/etc/chrony/chrony.conf"
grep -qE '^\s*pool\s+pool\.ntp\.org' "$CHRONY_CONF" || echo "pool pool.ntp.org iburst" >> "$CHRONY_CONF"
grep -qE "^\s*allow\s+$ALLOW_SUBNET" "$CHRONY_CONF" || echo "allow $ALLOW_SUBNET" >> "$CHRONY_CONF"
grep -qE '^\s*local\s+stratum\s+10' "$CHRONY_CONF" || echo "local stratum 10" >> "$CHRONY_CONF"

systemctl enable chrony
systemctl restart chrony

echo "[3/8] Creating a persistent systemd service for gateway setup..."
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

echo "[4/8] Writing gateway apply script..."
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

echo "[5/8] Enabling gateway service..."
systemctl daemon-reload
systemctl enable batman-gateway.service

echo "[6/8] Applying gateway config now..."
systemctl start batman-gateway.service

echo "[7/8] Seting supervisor services..."
RETRY_SERVICE="/home/pi/BEAMNode_Prototype2/installation_bash/set_retryservice.sh"
INTERNET_SERVICE="home/pi/BEAMNode_Prototype2/installation_bash/set_internetservice.sh"

sudo chmod +x RETRY_SERVICE
sudo chmod +x INTERNET_SERVICE

sudo bash RETRY_SERVICE
sudo bash INTERNET_SERVICE

echo "[8/8] Quick checks:"
ip -br a | grep -E "\b$MESH_IF\b" || true
iptables -t nat -S | grep MASQUERADE || true
systemctl is-active dnsmasq || true
systemctl is-active chrony || true
chronyc tracking || true

echo
echo "DONE. Supervisor is now a mesh gateway."

read -rp "Would you like to set the default boot to terminal mode? [y/n]: " TERM_MODE
if [[ "${TERM_MODE,,}" == "y" ]]; then
    echo "=== Setting default boot to terminal mode ==="
    sudo systemctl set-default multi-user.target
else
    echo "Default boot is still the graphical desktop environment."
fi

echo "LORA Configuration: Enable LoRa? [y/n]: "
read -r LORA_CHOICE
if [[ "${LORA_CHOICE,,}" == "y" ]]; then
    echo "Enabling in config.json..."
    CONFIG_PATH="/home/pi/BEAMNode_Prototype2/scripts/node/config.json"
    python3 - <<PY
import json
config_path = "$CONFIG_PATH"
with open(config_path, "r") as f:
    config = json.load(f)
config.setdefault("global", {})["lora_enabled"] = True
with open(config_path, "w") as f:
    json.dump(config, f, indent=4)
PY
    echo "Enabling LoRa..."
    sudo bash /home/pi/BEAMNode_Prototype2/scripts/lora/install_lora_automation.sh 
fi

echo "------------------------------------------------"
echo "Supervisor installation is complete!"
echo "------------------------------------------------"

read -rp "Would you like to reboot now? [y/n]: " REBOOT
if [[ "${REBOOT,,}" == "y" ]]; then
    echo "Rebooting now..."
    sudo reboot now
fi
