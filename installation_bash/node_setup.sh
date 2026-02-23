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

  sudo apt update
  sudo apt install -y \
    python3-pip python3-venv \
    libportaudio2 libjack0 \
    python3-pyaudio \
    batctl \
    chrony \
    isc-dhcp-client \
    rfkill

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
  # (ideally inside a venv).

  echo "=== PART 1 complete. Continuing to PART 2... ==="
else
  echo "=== Skipping PART 1 (no internet install). Continuing to PART 2... ==="
fi

#sudo apt upgrade -y


# ===================================
# === PART 2: BATMAN Installation ===
# ===================================

# ========================================
# BATMAN-adv Automatic Setup Script (Interactive)
# Tested on Debian / Raspberry Pi OS
# ========================================

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
set -e
# ========================================
# BATMAN-adv Startup Script
# ========================================

log() { echo "[start-batman] \$*" | tee -a /var/log/batman-start.log; }

# Poll until a condition is true, checking every 0.5s up to max_polls attempts.
# Usage: wait_for "description" "shell command" [max_polls]
wait_for() {
    local desc="\$1" cmd="\$2" max="\${3:-20}" i=0
    while ! eval "\$cmd" >/dev/null 2>&1; do
        i=\$((i+1))
        if [ \$i -ge \$max ]; then
            log "ERROR: Timed out waiting for: \$desc"
            exit 1
        fi
        sleep 0.5
    done
    log "\$desc ready (\${i} x 0.5s polls)"
}

log "Starting BATMAN-adv mesh setup..."

# Stop and disable conflicting services
log "Stopping conflicting services..."
systemctl stop wpa_supplicant 2>/dev/null || true
systemctl disable wpa_supplicant 2>/dev/null || true
systemctl stop NetworkManager 2>/dev/null || true
systemctl disable NetworkManager 2>/dev/null || true

# Unblock wireless radio (must happen before touching wlan0)
log "Unblocking wireless radio..."
rfkill unblock all || true

# Load BATMAN kernel module
log "Loading batman-adv kernel module..."
modprobe batman-adv
wait_for "batman-adv module" "lsmod | grep -q batman_adv"

# Bring wlan0 down cleanly before changing mode
log "Bringing wlan0 down..."
ip link set wlan0 down
wait_for "wlan0 DOWN" "ip link show wlan0 | grep -q 'state DOWN'"

# Set IBSS mode (must be done while interface is DOWN)
iw dev wlan0 set type ibss

# Bring wlan0 back up
log "Bringing wlan0 up..."
ip link set wlan0 up
wait_for "wlan0 UP" "ip link show wlan0 | grep -qE 'state UP|state UNKNOWN'"

# Join the IBSS network and wait until wlan0 is actually in IBSS mode
log "Joining IBSS network: $NETWORK_NAME @ ${FREQUENCY} MHz..."
iw dev wlan0 ibss join $NETWORK_NAME $FREQUENCY
wait_for "wlan0 IBSS mode" "iw dev wlan0 info | grep -q 'type IBSS'" 40

# Add wlan0 to BATMAN-adv (creates bat0)
log "Adding wlan0 to batman-adv..."
batctl if add wlan0
wait_for "bat0 exists" "ip link show bat0"

# Bring bat0 up
log "Bringing up bat0..."
ip link set up dev bat0
wait_for "bat0 UP" "ip link show bat0 | grep -qE 'state UP|state UNKNOWN'"

# Assign static IP (skip if already assigned)
log "Assigning static IP $STATIC_IP to bat0..."
if ip addr show bat0 | grep -q "inet "; then
    log "IP already assigned on bat0, skipping."
else
    ip addr add $STATIC_IP dev bat0
fi

log "bat0 is up: \$(ip addr show bat0 | grep 'inet ')"
log "BATMAN-adv setup complete!"
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
echo "✅ BATMAN-adv setup complete!"
echo "To verify, run: sudo systemctl status batman.service"
echo "Then check mesh neighbors with: sudo batctl n"


# ===================================
# === PART 3: Node Internet Setup ===
# ===================================

#!/usr/bin/env bash
set -euo pipefail

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

echo "[1/9] Checking and installing required packages..."

# Function to check if package is installed
is_installed() {
    dpkg -l "$1" 2>/dev/null | grep -q "^ii"
}

# Track what needs to be installed
PACKAGES_TO_INSTALL=()

if ! is_installed chrony; then
    echo "  - chrony not found, will install"
    PACKAGES_TO_INSTALL+=("chrony")
else
    echo "  - chrony already installed ✓"
fi

if ! is_installed isc-dhcp-client; then
    echo "  - isc-dhcp-client not found, will install"
    PACKAGES_TO_INSTALL+=("isc-dhcp-client")
else
    echo "  - isc-dhcp-client already installed ✓"
fi

if ! is_installed rfkill; then
    echo "  - rfkill not found, will install"
    PACKAGES_TO_INSTALL+=("rfkill")
else
    echo "  - rfkill already installed ✓"
fi

# Only run apt-get if we have packages to install
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
cat >/usr/local/sbin/mesh-boot.sh <<'BOOTSCRIPT'
#!/usr/bin/env bash
set -e

MESH_IF="{{MESH_IF}}"
SUP_IP="{{SUP_IP}}"
MAX_RETRIES=20
RETRY_DELAY=1

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a /var/log/mesh-boot.log
}

log "=== Starting mesh boot sequence ==="

# Step 1: Unblock Wi-Fi
log "Unblocking wireless..."
command -v rfkill >/dev/null 2>&1 && rfkill unblock all || true

# Step 2: Wait for mesh interface to exist
# batman.service must run first and create bat0 before this runs.
log "Waiting for $MESH_IF to exist..."
for i in $(seq 1 $MAX_RETRIES); do
    if ip link show "$MESH_IF" >/dev/null 2>&1; then
        log "$MESH_IF exists"
        break
    fi
    if [ $i -eq $MAX_RETRIES ]; then
        log "ERROR: $MESH_IF never appeared! Is batman.service running?"
        exit 1
    fi
    log "Waiting for $MESH_IF... attempt $i/$MAX_RETRIES"
    sleep $RETRY_DELAY
done

# Step 3: Bring up interface
log "Bringing up $MESH_IF..."
ip link set "$MESH_IF" up

# Step 4: Verify static IP is present (set by batman.service / start-batman.sh)
# NEVER run dhclient here — it overwrites your static IP with a random DHCP address.
log "Verifying static IP on $MESH_IF..."
for i in $(seq 1 $MAX_RETRIES); do
    if ip addr show "$MESH_IF" | grep -q "inet "; then
        MESH_IP=$(ip -4 addr show "$MESH_IF" | grep inet | awk '{print $2}')
        log "Static IP confirmed: $MESH_IP"
        break
    fi
    if [ $i -eq $MAX_RETRIES ]; then
        log "ERROR: No IP on $MESH_IF after $MAX_RETRIES attempts"
        exit 1
    fi
    log "Waiting for IP on $MESH_IF... attempt $i/$MAX_RETRIES"
    sleep $RETRY_DELAY
done

# Step 5: Set default route via supervisor so internet traffic is forwarded
# This is what actually gives the node internet through the supervisor.
log "Setting default route via supervisor $SUP_IP..."
ip route del default 2>/dev/null || true
ip route add default via "$SUP_IP" dev "$MESH_IF"
log "Default route set: $(ip route show default)"

# Step 6: Configure DNS
log "Configuring DNS to use supervisor..."
if command -v resolvectl >/dev/null 2>&1; then
    resolvectl dns "$MESH_IF" "$SUP_IP" || true
    resolvectl domain "$MESH_IF" "~." || true
else
    echo "nameserver $SUP_IP" > /etc/resolv.conf
fi

# Step 7: Wait for supervisor to be reachable
log "Testing connectivity to supervisor at $SUP_IP..."
for i in $(seq 1 $MAX_RETRIES); do
    if ping -c 1 -W 2 "$SUP_IP" >/dev/null 2>&1; then
        log "Supervisor is reachable"
        break
    fi
    if [ $i -eq $MAX_RETRIES ]; then
        log "WARNING: Supervisor not reachable after $MAX_RETRIES attempts"
    else
        log "Waiting for supervisor... attempt $i/$MAX_RETRIES"
        sleep $RETRY_DELAY
    fi
done

# Step 8: Force time sync
log "Forcing time synchronization..."
sleep 2  # Give chrony a moment to start communicating
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
log "Current time: $(date)"
log "IP address: $(ip -4 addr show $MESH_IF | grep inet | awk '{print $2}')"

exit 0
BOOTSCRIPT

# Replace placeholders
python3 - <<PY
from pathlib import Path
p = Path("/usr/local/sbin/mesh-boot.sh")
txt = p.read_text()
txt = txt.replace("{{MESH_IF}}", "${MESH_IF}")
txt = txt.replace("{{SUP_IP}}", "${SUP_IP}")
p.write_text(txt)
PY

chmod +x /usr/local/sbin/mesh-boot.sh

echo "[5/9] Configure chrony for aggressive syncing..."
CHRONY_CONF="/etc/chrony/chrony.conf"

# Backup original
cp "$CHRONY_CONF" "$CHRONY_CONF.backup"

# Remove existing server lines for SUP_IP
grep -vE "^\s*server\s+$SUP_IP\b" "$CHRONY_CONF" > /tmp/chrony.conf.tmp || true
cat /tmp/chrony.conf.tmp > "$CHRONY_CONF"

# Add optimized config at top
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

echo "[6/9] Create systemd service with proper dependencies..."
cat >/etc/systemd/system/mesh-boot.service <<'EOF'
[Unit]
Description=Batman mesh boot: interface up + route + time sync
After=network.target batman.service
Requires=batman.service
Before=network-online.target chrony.service

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

echo "[8/9] Create a manual recovery script (if you need to run manually)..."
cat >/usr/local/bin/mesh-reconnect <<'EOF'
#!/usr/bin/env bash
echo "Manually triggering mesh reconnection..."
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
echo "  1. Wait for $MESH_IF to appear"
echo "  2. Get DHCP from supervisor (with retries)"
echo "  3. Sync time from supervisor"
echo "  4. Connect to internet via supervisor"
echo
echo "Logs: /var/log/mesh-boot.log"
echo "Manual reconnect: mesh-reconnect"
echo
echo "After reboot, everything happens automatically."
echo "No more manual dhclient or chronyc commands needed!"
echo


# ======================================
# === PART 4: Autostart installation ===
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
