#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="/home/pi/data"
MOUNT_POINT="/home/pi/usbmnt"

mkdir -p "$MOUNT_POINT"

echo "Copying data..."
sudo cp -r "$DATA_DIR" "$MOUNT_POINT/"

echo "Done."
