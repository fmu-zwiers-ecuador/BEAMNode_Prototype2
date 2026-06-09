#!/bin/bash
# beam_setup_permissions.sh
# Sets up correct permissions for BEAM audio recording without sudo at runtime
# Run this ONCE with sudo: sudo bash beam_setup_permissions.sh
 
set -e
 
# --- Config ---
BEAM_USER="pi"  # change if your BEAM user is different
BEAM_DIR="/home/$BEAM_USER/BEAMNode_Prototype2"
DATA_DIR="$BEAM_DIR/data"
 
# --- Helpers ---
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color
 
ok()   { echo -e "${GREEN}[OK]${NC}  $1"; }
fail() { echo -e "${RED}[ERR]${NC} $1"; exit 1; }
 
# --- Must be run as root ---
if [ "$EUID" -ne 0 ]; then
    fail "Please run with sudo: sudo bash audio_permissions.sh"
fi
 
echo "Setting up BEAM permissions for user: $BEAM_USER"
echo "BEAM directory: $BEAM_DIR"
echo ""
 
# --- 1. Add user to audio group ---
if groups "$BEAM_USER" | grep -q '\baudio\b'; then
    ok "$BEAM_USER is already in the audio group"
else
    usermod -aG audio "$BEAM_USER"
    ok "Added $BEAM_USER to the audio group"
fi
 
# --- 2. Verify audio devices exist and are accessible ---
if [ -d /dev/snd ]; then
    ok "Audio devices found at /dev/snd"
    # Ensure audio group owns the devices
    chown -R root:audio /dev/snd
    chmod -R g+rw /dev/snd
    ok "Set group ownership on /dev/snd"
else
    fail "/dev/snd not found — is a microphone connected?"
fi
 
# --- 3. Ensure BEAM directory exists and is owned by user ---
if [ -d "$BEAM_DIR" ]; then
    chown -R "$BEAM_USER":"$BEAM_USER" "$BEAM_DIR"
    ok "Set ownership of $BEAM_DIR to $BEAM_USER"
else
    fail "BEAM directory not found at $BEAM_DIR — check your install path"
fi
 
# --- 4. Create data/audio directory if missing and set permissions ---
mkdir -p "$DATA_DIR/audio"
chown -R "$BEAM_USER":"$BEAM_USER" "$DATA_DIR"
chmod -R 755 "$DATA_DIR"
ok "Created and set permissions on $DATA_DIR/audio"
 
# --- 5. Set script itself as executable (non-root) ---
RECORD_SCRIPT="$BEAM_DIR/scripts/node/record.py"
if [ -f "$RECORD_SCRIPT" ]; then
    chown "$BEAM_USER":"$BEAM_USER" "$RECORD_SCRIPT"
    chmod 744 "$RECORD_SCRIPT"
    ok "Set record.py as executable by $BEAM_USER"
else
    echo "  [WARN] record.py not found at $RECORD_SCRIPT — skipping"
fi
 
# --- 6. Set config.json as readable by user only ---
CONFIG_FILE="$BEAM_DIR/scripts/node/config.json"
if [ -f "$CONFIG_FILE" ]; then
    chown "$BEAM_USER":"$BEAM_USER" "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
    ok "Locked down config.json (owner read/write only)"
else
    echo "  [WARN] config.json not found at $CONFIG_FILE — skipping"
fi
 
# --- 7. If systemd service exists, update it ---
SERVICE_FILE="/etc/systemd/system/beam-record.service"
if [ -f "$SERVICE_FILE" ]; then
    # Ensure User and Group are set correctly in the service
    sed -i "s/^User=.*/User=$BEAM_USER/" "$SERVICE_FILE"
    sed -i "s/^Group=.*/Group=audio/" "$SERVICE_FILE"
    systemctl daemon-reload
    ok "Updated beam-record.service to run as $BEAM_USER:audio"
else
    echo "  [INFO] No systemd service found at $SERVICE_FILE — skipping"
fi
 
echo ""
echo "============================================"
ok "All permissions set successfully."
echo "  >> Please log out and back in (or reboot)"
echo "     for group changes to take effect."
echo "============================================"