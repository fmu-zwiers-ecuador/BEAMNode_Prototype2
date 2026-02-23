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

echo "[3/9] Kill any existing dhclient for mesh interface..."
pkill -f "dhclient.*$MESH_IF" || true
sleep 1

echo "[4/9] Create robust boot helper with retries..."
cat >/usr/local/sbin/mesh-boot.sh <<'BOOTSCRIPT'
#!/usr/bin/env bash
set -e

MESH_IF="{{MESH_IF}}"
SUP_IP="{{SUP_IP}}"
MAX_RETRIES=10
RETRY_DELAY=3

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a /var/log/mesh-boot.log
}

log "=== Starting mesh boot sequence ==="

# Step 1: Unblock Wi-Fi
log "Unblocking wireless..."
command -v rfkill >/dev/null 2>&1 && rfkill unblock all || true
sleep 1

# Step 2: Wait for mesh interface to exist
log "Waiting for $MESH_IF to exist..."
for i in $(seq 1 $MAX_RETRIES); do
    if ip link show "$MESH_IF" >/dev/null 2>&1; then
        log "$MESH_IF exists"
        break
    fi
    if [ $i -eq $MAX_RETRIES ]; then
        log "ERROR: $MESH_IF never appeared!"
        exit 1
    fi
    log "Waiting for $MESH_IF... attempt $i/$MAX_RETRIES"
    sleep $RETRY_DELAY
done

# Step 3: Bring up interface
log "Bringing up $MESH_IF..."
ip link set "$MESH_IF" up
sleep 2

# Step 4: Kill any existing dhclient instances for this interface
log "Cleaning up old dhclient processes..."
pkill -f "dhclient.*$MESH_IF" || true
sleep 1

# Step 5: Get IP via DHCP with retries
log "Requesting DHCP lease..."
for i in $(seq 1 $MAX_RETRIES); do
    # Release any existing lease
    dhclient -r "$MESH_IF" 2>/dev/null || true
    sleep 1
    
    # Request new lease
    if dhclient -v "$MESH_IF" 2>&1 | tee -a /var/log/mesh-boot.log; then
        sleep 2
        # Check if we got an IP
        if ip addr show "$MESH_IF" | grep -q "inet "; then
            MESH_IP=$(ip -4 addr show "$MESH_IF" | grep inet | awk '{print $2}')
            log "SUCCESS: Got IP $MESH_IP"
            break
        fi
    fi
    
    if [ $i -eq $MAX_RETRIES ]; then
        log "ERROR: Failed to get DHCP lease after $MAX_RETRIES attempts"
        exit 1
    fi
    log "DHCP attempt $i/$MAX_RETRIES failed, retrying..."
    sleep $RETRY_DELAY
done

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
Description=Batman mesh boot: interface up + DHCP + time sync
After=network.target systemd-networkd.service
Before=network-online.target chrony.service
Wants=network.target

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
