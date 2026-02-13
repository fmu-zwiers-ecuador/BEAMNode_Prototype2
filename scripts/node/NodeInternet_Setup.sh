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
