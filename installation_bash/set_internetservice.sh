#!/bin/bash
INTERNET_SERVICE="restart_internet"
MONITOR_SERVICE="monitor_internet"
INTERNET_SCRIPT="/home/pi/BEAMNode_Prototype2/restart_internet.sh"
MONITOR_SCRIPT="/home/pi/BEAMNode_Prototype2/monitor_internet.sh"

# Restart internet service
echo "Setting service for restarting the internet..."

sudo bash -c "cat > /etc/systemd/system/$INTERNET_SERVICE.service <<EOF
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
sudo systemctl enable "$INTERNET_SERVICE.service"

echo "------------------------------------------------"
if systemctl is-enabled --quiet "$INTERNET_SERVICE.service"; then
    echo "SUCCESS: $INTERNET_SERVICE.service is enabled at startup!"
else
    echo "FAILURE: $INTERNET_SERVICE.service could not be activated"
fi
echo "------------------------------------------------"

# Monitor internet service
echo "Setting service for monitoring the internet..."

sudo bash -c "cat > /etc/systemd/system/$MONITOR_SERVICE.service <<EOF
[Unit]
Description=Turn on supervisor internet on startup
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/bash /home/pi/BEAMNode_Prototype2/monitor_internet.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable "$MONITOR_SERVICE.service"

echo "------------------------------------------------"
if systemctl is-enabled --quiet "$MONITOR_SERVICE.service"; then
    echo "SUCCESS: $MONITOR_SERVICE.service is now active!"
else
    echo "FAILURE: $MONITOR_SERVICE.service could not be activated"
fi
echo "------------------------------------------------"

