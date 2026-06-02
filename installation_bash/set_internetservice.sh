#!/bin/bash
SERVICE_NAME="restart_internet"
INTERNET_SCRIPT="/home/pi/BEAMNode_Prototype2/restart_internet.sh"

echo "Setting service for restarting the internet..."

sudo bash -c "cat > /etc/systemd/system/$SERVICE_NAME.service <<EOF
[Unit]
Description=Turn on supervisor internet on startup
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/bash $INTERNET_SCRIPT

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME.service"

echo "------------------------------------------------"
if systemctl is-enabled --quiet "$SERVICE_NAME.service"; then
    echo "SUCCESS: $SERVICE_NAME.service is now active!"
else
    echo "FAILURE: $SERVICE_NAME.service could not be activated"
fi
echo "------------------------------------------------"
