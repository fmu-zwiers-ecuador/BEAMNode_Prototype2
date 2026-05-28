#!/bin/bash
# Script to create the low power mode pv pi service file
set -e
echo "=== Low Power Mode Setup Script ==="
echo

read -p "Press Enter to continue or Ctrl+C to cancel..."

# --- Create systemd service ---
echo "[1/3] Creating /etc/systemd/system/lpm_pvpi.service ..."
cat <<SVCEOF | sudo tee /etc/systemd/system/lpm_pvpi.service >/dev/null
[Unit]
Description=Low Power Mode Pv Pi
After=network.target

[Service]
Type=simple
User=pi
ExecStart=/bin/bash /home/pi/BEAMNode_Prototype2/scripts/power/Lpm_pvpi.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF

# --- Start service and enable service ---
echo "[2/3] Reloading systemd and enabling service ..."
sudo systemctl daemon-reload
sudo systemctl enable lpm_pvpi.service

# --- Start service immediately ---
echo "[3/3] Starting lpm pv pi service ..."
sudo systemctl start lpm_pvpi.service

echo
echo "Low power mode setup complete"
echo "To verify, run: sudo systemctl status lpm_pvpi.service"