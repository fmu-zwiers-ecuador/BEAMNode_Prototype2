#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="/home/pi/data"
MOUNT_POINT="/home/pi/usbmnt"

mkdir -p "$MOUNT_POINT"

is_mounted() {
    findmnt -rn "$MOUNT_POINT" >/dev/null 2>&1
}

if is_mounted; then
    echo "$MOUNT_POINT is already mounted."
else
    echo "$MOUNT_POINT is not mounted. Trying to mount it once..."
    if ! sudo -n mount "$MOUNT_POINT"; then
        echo "ERROR: Could not mount $MOUNT_POINT. If sudo needs a password, run the mount manually once or update /etc/fstab/sudoers."
        exit 1
    fi
fi

echo "Copying data..."
if ! sudo -n cp -r "$DATA_DIR" "$MOUNT_POINT/"; then
    echo "ERROR: Could not copy $DATA_DIR to $MOUNT_POINT. If sudo needs a password, fix permissions or run the copy manually once."
    exit 1
fi

echo "Done."
