#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="/home/pi/data"
MOUNT_POINT="/home/pi/usbmnt"

mkdir -p "$MOUNT_POINT"

echo "Mounting USB..."
sudo mount /dev/sda1 "$MOUNT_POINT"
trap 'echo "Unmounting USB..."; sudo umount "$MOUNT_POINT"' EXIT

echo "Copying data..."
sudo cp -r "$DATA_DIR" "$MOUNT_POINT/"

echo "Done."