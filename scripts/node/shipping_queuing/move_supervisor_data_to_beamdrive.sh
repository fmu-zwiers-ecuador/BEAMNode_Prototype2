#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="/home/pi/data"
MOUNT_POINT="/home/pi/usbmnt"

mkdir -p "$MOUNT_POINT"

if findmnt -rn "$MOUNT_POINT" >/dev/null 2>&1; then
  echo "USB already mounted at $MOUNT_POINT"
else
  echo "Mounting USB..."
  sudo mount /dev/sda1 "$MOUNT_POINT"
fi

echo "Copying data..."
sudo cp -r "$DATA_DIR" "$MOUNT_POINT/"

echo "Done."
