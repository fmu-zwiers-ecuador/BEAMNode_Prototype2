#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="/home/pi/data"
MOUNT_POINT="/home/pi/usbmnt"
MOUNT_CMD="$(command -v mount)"
CP_CMD="$(command -v cp)"

mkdir -p "$MOUNT_POINT"

is_mounted() {
    findmnt -rn "$MOUNT_POINT" >/dev/null 2>&1
}

if is_mounted; then
    echo "$MOUNT_POINT is already mounted."
else
    echo "$MOUNT_POINT is not mounted. Trying to mount it once..."
    if ! sudo -n "$MOUNT_CMD" "$MOUNT_POINT"; then
        echo "ERROR: Could not mount $MOUNT_POINT without a password. Run installation_bash/set_retryservice.sh once to install the passwordless mount rule."
        exit 1
    fi
fi

echo "Copying data..."
if cp -r "$DATA_DIR" "$MOUNT_POINT/"; then
    echo "Copy completed as pi user."
elif sudo -n "$CP_CMD" -r "$DATA_DIR" "$MOUNT_POINT/"; then
    echo "Copy completed with passwordless sudo."
else
    echo "ERROR: Could not copy $DATA_DIR to $MOUNT_POINT without a password. Run installation_bash/set_retryservice.sh once to install the passwordless copy rule."
    exit 1
fi

echo "Done."
