#!/bin/bash

# ============================================================================
# === node_setup.sh: This script sets up a new node, ready to be deployed. ===
# ============================================================================

# Must be run as root (sudo ./node_setup.sh)
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
    adafruit-circuitpython-ahtx0==1.0.28

  echo "=== PART 1 complete. Continuing to PART 2... ==="
else
  echo "=== Skipping PART 1 (no internet install). Continuing to PART 2... ==="
fi


# ===================================
# === PART 2: BATMAN Installation ===
# ===================================

echo "=== BATMAN-adv Setup Script ==="
echo

read -p "Will you be connecting to supervisor 1 or supervisor 2? [1] or [2]: " SUPERVISOR
if [[ "${SUPERVISOR,,}" == "1" ]]; then
    echo "Configuring for supervisor 1..."
    # --- Ask for configuration with defaults ---
    read -p "Enter ad-hoc network name (SSID) [myadhoc]: " NETWORK_NAME
    NETWORK_NAME=${NETWORK_NAME:-myadhoc}

    read -p "Enter frequency in MHz (e.g. 2412 for channel 1) [2412]: " FREQUENCY
    FREQUENCY=${FREQUENCY:-2412}

    read -p "Enter static IP for bat0 (e.g. 10.42.0.2/16) [10.42.0.2/16]: " STATIC_IP
    STATIC_IP=${STATIC_IP:-10.42.0.2/16}
else
    echo "Configuring for supervisor 2..."
    # --- Ask for configuration with defaults ---
    read -p "Enter ad-hoc network name (SSID) [myadhoc2]: " NETWORK_NAME
    NETWORK_NAME=${NETWORK_NAME:-myadhoc2}

    read -p "Enter frequency in MHz (e.g. 2437 for channel 6) [2437]: " FREQUENCY
    FREQUENCY=${FREQUENCY:-2437}

    read -p "Enter static IP for bat0 (e.g. 10.42.0.2/16) [10.42.0.2/16]: " STATIC_IP
    STATIC_IP=${STATIC_IP:-10.42.0.2/16}
fi


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


# ===================================
# === PART 3: Node Internet Setup ===
# ===================================

set -euo pipefail

read -rp "Mesh interface [bat0]: " MESH_IF
MESH_IF=${MESH_IF:-bat0}

read -rp "Supervisor mesh IP (NTP/DNS gateway) [10.42.0.30](Supervisor 1) or [10.42.0.40](Supervisor 2): " SUP_IP
SUP_IP=${SUP_IP:-10.42.0.30}

echo
echo "=== Summary ==="
echo "Mesh IF:    $MESH_IF"
echo "Supervisor: $SUP_IP"
echo "Static IP:  $STATIC_IP"
echo

echo "[1/9] Checking and installing required packages..."

is_installed() { dpkg -l "$1" 2>/dev/null | grep -q "^ii"; }

PACKAGES_TO_INSTALL=()
for pkg in chrony isc-dhcp-client rfkill; do
  if ! is_installed "$pkg"; then
    echo "  - $pkg not found, will install"
    PACKAGES_TO_INSTALL+=("$pkg")
  else
    echo "  - $pkg already installed ✓"
  fi
done

if [ ${#PACKAGES_TO_INSTALL[@]} -gt 0 ]; then
  echo "  Installing: ${PACKAGES_TO_INSTALL[*]}"
  apt-get update -y
  apt-get install -y "${PACKAGES_TO_INSTALL[@]}" || true
else
  echo "  All required packages already installed, skipping apt-get"
fi

echo "[2/9] Prevent dhclient DNS write issues..."
mkdir -p /etc/dhcp/dhclient-enter-hooks.d
cat >/etc/dhcp/dhclient-enter-hooks.d/nodns <<'EOF'
make_resolv_conf() { :; }
EOF
chmod +x /etc/dhcp/dhclient-enter-hooks.d/nodns

echo "[3/9] Create robust boot helper..."
cat >/usr/local/sbin/mesh-boot.sh <<BOOTSCRIPT
#!/usr/bin/env bash
set -e

MESH_IF="{{MESH_IF}}"
SUP_IP="{{SUP_IP}}"
STATIC_IP="{{STATIC_IP}}"
MAX_RETRIES=10
RETRY_DELAY=3

log() {
    echo "[\$(date '+%Y-%m-%d %H:%M:%S')] \$*" | tee -a /var/log/mesh-boot.log
}

log "=== Starting mesh boot sequence ==="

# Step 1: Unblock Wi-Fi
log "Unblocking wireless..."
command -v rfkill >/dev/null 2>&1 && rfkill unblock all || true
sleep 1

# Step 2: Wait for mesh interface to exist
log "Waiting for \$MESH_IF to exist..."
for i in \$(seq 1 \$MAX_RETRIES); do
    if ip link show "\$MESH_IF" >/dev/null 2>&1; then
        log "\$MESH_IF exists"
        break
    fi
    if [ \$i -eq \$MAX_RETRIES ]; then
        log "ERROR: \$MESH_IF never appeared! Is batman.service running?"
        exit 1
    fi
    log "Waiting for \$MESH_IF... attempt \$i/\$MAX_RETRIES"
    sleep \$RETRY_DELAY
done

# Step 3: Bring up interface
log "Bringing up \$MESH_IF..."
ip link set "\$MESH_IF" up
sleep 2

# Step 4: Verify static IP — if missing (e.g. batman.service was slow), assign it now.
# FIX: Previously this just waited and gave up. Now it actively re-assigns the IP
#      so the node always ends up with the correct address even if batman.service
#      lost a race condition on boot.
log "Verifying static IP on \$MESH_IF..."
for i in \$(seq 1 \$MAX_RETRIES); do
    if ip addr show "\$MESH_IF" | grep -q "inet "; then
        MESH_IP=\$(ip -4 addr show "\$MESH_IF" | grep inet | awk '{print \$2}')
        log "IP confirmed: \$MESH_IP"
        # FIX: Even if an IP exists it might be the wrong one (assigned by dhcpcd
        #      before our denyinterfaces takes effect on first boot). Replace it.
        if [ "\$MESH_IP" != "\$STATIC_IP" ]; then
            log "WARNING: IP \$MESH_IP does not match desired \$STATIC_IP — correcting..."
            ip addr flush dev "\$MESH_IF" 2>/dev/null || true
            ip addr add "\$STATIC_IP" dev "\$MESH_IF"
            log "IP corrected to \$STATIC_IP"
        fi
        break
    fi
    if [ \$i -eq \$MAX_RETRIES ]; then
        log "No IP found after \$MAX_RETRIES attempts — force-assigning \$STATIC_IP..."
        ip addr flush dev "\$MESH_IF" 2>/dev/null || true
        ip addr add "\$STATIC_IP" dev "\$MESH_IF" || true
    fi
    log "Waiting for IP on \$MESH_IF... attempt \$i/\$MAX_RETRIES"
    sleep \$RETRY_DELAY
done

# Step 5: Set default route via supervisor
log "Setting default route via supervisor \$SUP_IP..."
ip route del default 2>/dev/null || true
ip route add default via "\$SUP_IP" dev "\$MESH_IF"
log "Default route: \$(ip route show default)"

# Step 6: Configure DNS
log "Configuring DNS to use supervisor..."
if command -v resolvectl >/dev/null 2>&1; then
    resolvectl dns "\$MESH_IF" "\$SUP_IP" || true
    resolvectl domain "\$MESH_IF" "~." || true
else
    echo "nameserver \$SUP_IP" > /etc/resolv.conf
fi

# Step 7: Wait for supervisor to be reachable
log "Testing connectivity to supervisor at \$SUP_IP..."
for i in \$(seq 1 \$MAX_RETRIES); do
    if ping -c 1 -W 2 "\$SUP_IP" >/dev/null 2>&1; then
        log "Supervisor is reachable"
        break
    fi
    if [ \$i -eq \$MAX_RETRIES ]; then
        log "WARNING: Supervisor not reachable after \$MAX_RETRIES attempts"
    else
        log "Waiting for supervisor... attempt \$i/\$MAX_RETRIES"
        sleep \$RETRY_DELAY
    fi
done

# Step 8: Force time sync
log "Forcing time synchronization..."
sleep 2
if command -v chronyc >/dev/null 2>&1; then
    chronyc -a makestep 2>&1 | tee -a /var/log/mesh-boot.log || true
    sleep 1
    chronyc -a burst 4/4 2>&1 | tee -a /var/log/mesh-boot.log || true
    sleep 2
    chronyc tracking 2>&1 | tee -a /var/log/mesh-boot.log || true
fi

# Step 9: Test internet connectivity
log "Testing internet connectivity..."
if ping -c 2 8.8.8.8 >/dev/null 2>&1; then
    log "Internet connectivity: OK"
else
    log "WARNING: No internet connectivity"
fi

log "=== Mesh boot sequence complete ==="
log "Current time: \$(date)"
log "IP address:   \$(ip -4 addr show \$MESH_IF | grep inet | awk '{print \$2}')"

exit 0
BOOTSCRIPT

# Replace placeholders (now includes STATIC_IP)
python3 - <<PY
from pathlib import Path
p = Path("/usr/local/sbin/mesh-boot.sh")
txt = p.read_text()
txt = txt.replace("{{MESH_IF}}",   "${MESH_IF}")
txt = txt.replace("{{SUP_IP}}",    "${SUP_IP}")
txt = txt.replace("{{STATIC_IP}}", "${STATIC_IP}")
p.write_text(txt)
PY

chmod +x /usr/local/sbin/mesh-boot.sh

echo "[5/9] Configure chrony for aggressive syncing..."
CHRONY_CONF="/etc/chrony/chrony.conf"
cp "$CHRONY_CONF" "$CHRONY_CONF.backup"
grep -vE "^\s*server\s+$SUP_IP\b" "$CHRONY_CONF" > /tmp/chrony.conf.tmp || true
cat /tmp/chrony.conf.tmp > "$CHRONY_CONF"

tmpfile="$(mktemp)"
{
  echo "# Supervisor NTP server (primary time source)"
  echo "server $SUP_IP iburst prefer minpoll 0 maxpoll 4"
  echo ""
  echo "# Allow large time steps (important for initial sync)"
  echo "makestep 1.0 -1"
  echo ""
  echo "# More aggressive polling"
  echo "maxupdateskew 100.0"
  echo ""
  cat "$CHRONY_CONF"
} > "$tmpfile"
cat "$tmpfile" > "$CHRONY_CONF"
rm -f "$tmpfile"

systemctl enable chrony
systemctl restart chrony

# FIX: Removed "Before=network-online.target chrony.service" — that line was
#      causing chrony to start before the mesh interface was ready, so it could
#      never reach the supervisor NTP server on reboot.
echo "[6/9] Create systemd service with proper dependencies..."
cat >/etc/systemd/system/mesh-boot.service <<'EOF'
[Unit]
Description=Batman mesh boot: interface up + route + time sync
After=network.target batman.service
Requires=batman.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/mesh-boot.sh
RemainAfterExit=yes
TimeoutStartSec=120
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable mesh-boot.service

# FIX: mesh-timesync now starts After=chrony.service (not before it) so chrony
#      is already running and able to receive the makestep/burst commands.
echo "[7/9] Create delayed time sync service (runs after mesh-boot)..."
cat >/etc/systemd/system/mesh-timesync.service <<EOF
[Unit]
Description=Force time sync over mesh (delayed)
After=mesh-boot.service chrony.service
Requires=mesh-boot.service
BindsTo=mesh-boot.service

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/chronyc -a makestep
ExecStart=/usr/bin/chronyc -a burst 4/4
ExecStartPost=/bin/sleep 2
ExecStartPost=/usr/bin/chronyc tracking

[Install]
WantedBy=multi-user.target
EOF

systemctl enable mesh-timesync.service

echo "[8/9] Create a manual recovery script..."
cat >/usr/local/bin/mesh-reconnect <<'EOF'
#!/usr/bin/env bash
echo "Manually triggering mesh reconnection..."
sudo systemctl restart batman.service
sleep 5
sudo systemctl restart mesh-boot.service
sleep 5
sudo systemctl restart mesh-timesync.service
echo "Done. Check status with: systemctl status mesh-boot.service"
EOF
chmod +x /usr/local/bin/mesh-reconnect

echo "[9/9] Testing the setup now..."
/usr/local/sbin/mesh-boot.sh || true

echo
echo "============================================"
echo "SETUP COMPLETE!"
echo "============================================"
echo
echo "The node will now automatically:"
echo "  1. Wait for $MESH_IF to appear (batman.service runs first)"
echo "  2. Verify/correct the static IP $STATIC_IP on $MESH_IF"
echo "  3. Set default route via supervisor $SUP_IP"
echo "  4. Sync time from supervisor"
echo "  5. Connect to internet via supervisor"
echo
echo "Logs: /var/log/mesh-boot.log  /var/log/batman-start.log"
echo "Manual reconnect: mesh-reconnect"
echo
echo "After reboot, everything happens automatically."
echo

# --- Register crontab for NodeInternet_Setup.sh ---
echo "Registering crontab for NodeInternet_Setup.sh..."
echo "@reboot /bin/bash /home/pi/BEAMNode_Prototype2/scripts/node/NodeInternet_Setup.sh >> /var/log/nodeinternet-setup.log 2>&1" > /etc/cron.d/nodeinternet_setup
chmod 644 /etc/cron.d/nodeinternet_setup
echo "Crontab registered"

# ======================================
# === PART 4: Autostart installation ===
# ======================================

PROJECT_ROOT="/home/pi/BEAMNode_Prototype2"
NODE_DIR="$PROJECT_ROOT/scripts/node"
SERVICE_SRC="$PROJECT_ROOT/beamnode.service"
SERVICE_NAME="beamnode.service"
LOG_DIR="$PROJECT_ROOT/logs"

echo "[1/4] Preparing directories..."
mkdir -p "/home/pi/data"
mkdir -p "/home/pi/shipping"
mkdir -p "$LOG_DIR"

echo "[2/4] Setting execution permissions..."
chmod +x "$NODE_DIR/launcher.py"
chmod +x "$NODE_DIR/scheduler.py"
chmod +x "$NODE_DIR/sensor_detection/detect.py"
chmod +x "$NODE_DIR/shipping_queuing/shipping.py"

echo "[3/4] Registering systemd service..."
if [ -f "$SERVICE_SRC" ]; then
    cp "$SERVICE_SRC" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    echo "Service $SERVICE_NAME installed and started."
else
    echo "ERROR: Could not find $SERVICE_SRC"
    echo "Please ensure beamnode.service is in $PROJECT_ROOT"
    exit 1
fi

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

# Enable I2C and SPI
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_spi 0

read -rp "Would you like to set the default boot to terminal mode? [y/n]: " TERM_MODE
if [[ "${TERM_MODE,,}" == "y" ]]; then
    echo "=== Setting default boot to terminal mode ==="
    sudo systemctl set-default multi-user.target
else
    echo "Default boot is still the graphical desktop environment."
fi

# LoRa configuration: run ../scripts/lora/install_lora_automation.sh to set up the config file
LORA_DIR="$PROJECT_ROOT/scripts/lora"
sudo chown -R pi:pi "$LORA_DIR"
sudo bash $LORA_DIR/install_lora_automation.sh


echo "------------------------------------------------"
echo "Node installation is complete!"
echo "------------------------------------------------"

read -rp "Would you like to reboot now? [y/n]: " REBOOT
if [[ "${REBOOT,,}" == "y" ]]; then
    echo "Rebooting now..."
    sudo reboot now
fi

