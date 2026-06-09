#!/usr/bin/env bash
set -euo pipefail

# BEAMNode Project - Node USB Backup Permission Setup
# Run this on each node that has a local USB backup drive attached.

RUN_USER="${SUDO_USER:-pi}"
if ! id "$RUN_USER" >/dev/null 2>&1; then
  RUN_USER="pi"
fi
RUN_UID="$(id -u "$RUN_USER")"
RUN_GID="$(id -g "$RUN_USER")"

MOUNT_POINT="/home/pi/usbmnt"
USB_DEVICE="/dev/sda1"
SUDOERS_FILE="/etc/sudoers.d/beamnode-node-usb-backup"

MOUNT_CMD="$(command -v mount)"
CHOWN_CMD="$(command -v chown)"
CHMOD_CMD="$(command -v chmod)"
MKDIR_CMD="$(command -v mkdir)"
RSYNC_CMD="$(command -v rsync)"

echo "=== BEAMNode Node USB Backup Permission Setup ==="
echo "Runtime user: $RUN_USER"
echo "Runtime uid:  $RUN_UID"
echo "Runtime gid:  $RUN_GID"
echo "USB device:   $USB_DEVICE"
echo "Mount point:  $MOUNT_POINT"
echo

if [[ -z "$MOUNT_CMD" || -z "$CHOWN_CMD" || -z "$CHMOD_CMD" || -z "$MKDIR_CMD" || -z "$RSYNC_CMD" ]]; then
  echo "ERROR: required command missing. Need mount, chown, chmod, mkdir, and rsync."
  exit 1
fi

echo "[1/4] Preparing node runtime directories..."
mkdir -p /home/pi/data /home/pi/shipping /home/pi/logs "$MOUNT_POINT"
chown -R "$RUN_USER:$RUN_USER" /home/pi/data /home/pi/shipping /home/pi/logs "$MOUNT_POINT" 2>/dev/null || true
chmod -R u+rwX /home/pi/data /home/pi/shipping /home/pi/logs "$MOUNT_POINT" 2>/dev/null || true

echo "[2/4] Installing passwordless sudo rules for node USB backup..."
sudo bash -c "cat > $SUDOERS_FILE <<EOF
$RUN_USER ALL=(root) NOPASSWD: $MOUNT_CMD $USB_DEVICE $MOUNT_POINT
$RUN_USER ALL=(root) NOPASSWD: $MOUNT_CMD -o uid=$RUN_UID\\,gid=$RUN_GID\\,umask=0002 $USB_DEVICE $MOUNT_POINT
$RUN_USER ALL=(root) NOPASSWD: $MOUNT_CMD -o remount\\,uid=$RUN_UID\\,gid=$RUN_GID\\,umask=0002 $MOUNT_POINT
$RUN_USER ALL=(root) NOPASSWD: $CHOWN_CMD -R $RUN_USER\\:$RUN_USER $MOUNT_POINT
$RUN_USER ALL=(root) NOPASSWD: $CHMOD_CMD -R u+rwX $MOUNT_POINT
$RUN_USER ALL=(root) NOPASSWD: $MKDIR_CMD -p $MOUNT_POINT/shipping_archive/*
$RUN_USER ALL=(root) NOPASSWD: $MKDIR_CMD -p /media/pi/BEAMdrive/shipping_archive/*
$RUN_USER ALL=(root) NOPASSWD: $RSYNC_CMD -rt --ignore-existing --no-owner --no-group --no-perms --omit-dir-times /home/pi/shipping/ $MOUNT_POINT/shipping_archive/*/
$RUN_USER ALL=(root) NOPASSWD: $RSYNC_CMD -rt --ignore-existing --no-owner --no-group --no-perms --omit-dir-times /home/pi/shipping/ /media/pi/BEAMdrive/shipping_archive/*/
EOF"

sudo chmod 0440 "$SUDOERS_FILE"

echo "[3/4] Validating sudoers file..."
if ! sudo visudo -cf "$SUDOERS_FILE"; then
  echo "ERROR: invalid sudoers file: $SUDOERS_FILE"
  exit 1
fi

echo "[4/4] Checking USB mount path..."
if mountpoint -q "$MOUNT_POINT"; then
  echo "USB already mounted at $MOUNT_POINT"
else
  echo "USB is not currently mounted at $MOUNT_POINT."
  echo "That is okay; retryqueue.py will mount $USB_DEVICE there when needed."
fi

echo
echo "DONE. Node USB backup permissions are ready."
echo "The retry queue can now back up /home/pi/shipping to the node USB before transfer."
