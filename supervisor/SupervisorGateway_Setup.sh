#!/usr/bin/env bash
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
