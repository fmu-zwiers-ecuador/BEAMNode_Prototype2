#!/bin/bash
# Script to create the low power mode service file
set -e
echo "=== Low Power Mode Setup Script ==="
echo

read -p "Press Enter to continue or Ctrl+C to cancel..."

# --- Create systemd service ---
echo "[1/3] Creating /etc/systemd/system/low_power_mode.service ..."
cat <<SVCEOF | sudo tee /etc/systemd/system/low_power_mode.service >/dev/null
[Unit]
Description=Low Power Mode Manager
After=network.target

[Service]
Type=simple
User=pi
ExecStart=/usr/bin/python3 /home/pi/BEAMNode_Prototype2/scripts/power/low_power_mode.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF

# --- Start service and enable service ---
echo "[2/3] Reloading systemd and enabling service ..."
sudo systemctl daemon-reload
sudo systemctl enable low_power_mode.service

# --- Start service immediately ---
echo "[3/3] Starting low power mode service ..."
sudo systemctl start low_power_mode.service

echo
echo "Low power mode setup complete"
echo "To verify, run: sudo systemctl status low_power_mode.service"
