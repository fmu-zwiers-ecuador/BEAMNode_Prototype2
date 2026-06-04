#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="/home/pi/data"
MOUNT_POINT="/home/pi/usbmnt"
MOUNT_CMD="$(command -v mount)"
CP_CMD="$(command -v cp)"
DRIVE="/dev/sda1"
mkdir -p "$MOUNT_POINT"

# Check if the drive is mounted
if mount | grep -q "$MOUNT_POINT"; then
    echo "Drive is already mounted. Proceeding to next step..."
else
    echo "Drive is not mounted. Attempting to mount $DRIVE..."
    
    # Create mount point directory if it doesn't exist
    mkdir -p "$MOUNT_POINT"
    
    # Mount the drive
    if sudo -n mount "$DRIVE" "$MOUNT_POINT"; then
        echo "Mount successful. Proceeding to next step..."
    else
        echo "Error: Failed to mount the drive. Exiting script."
        exit 1
    fi
fi

# --- Your next steps go below this line ---
echo "Doing the next step!"
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
