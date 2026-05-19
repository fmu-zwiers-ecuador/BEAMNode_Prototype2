#!/usr/bin/env bash
set -euo pipefail

# --- CONFIG ---
DATA_DIR="/home/pi/data"                 # <-- change if needed
LOG_FILE="/home/pi/logs/data_move.log"
DRIVE_NAME="BEAMdrive"                       # external drive name/label
DEST_BASE_SUBDIR="data_archive"           # folder on the drive to store moved items
USER="pi"

# Prevent overlapping runs
LOCK_FILE="/tmp/move_shipping.lock"
exec 200>"$LOCK_FILE"
flock -n 200 || exit 0

ts() { date "+%Y-%m-%d %H:%M:%S"; }

# Find BEAMdrive mount point
MOUNT_POINT=""

# Common Raspberry Pi automount location
if [[ -d "/media/$USER/$DRIVE_NAME" ]]; then
  MOUNT_POINT="/media/$USER/$DRIVE_NAME"
fi

# Also try by filesystem label (works even if mounted elsewhere)
if [[ -z "$MOUNT_POINT" ]]; then
  # findmnt returns the target mount path for a source like LABEL=BEAMdrive
  MOUNT_POINT="$(findmnt -rn -S "LABEL=$DRIVE_NAME" -o TARGET 2>/dev/null || true)"
fi

if [[ -z "$MOUNT_POINT" || ! -d "$MOUNT_POINT" ]]; then
  echo "$(ts) | ERROR: BEAMdrive not mounted/found. Expected /media/$USER/$DRIVE_NAME or LABEL=$DRIVE_NAME" >> "$LOG_FILE"
  exit 1
fi

if [[ ! -d "$DATA_DIR" ]]; then
  echo "$(ts) | INFO: data dir not found: $DATA_DIR" >> "$LOG_FILE"
  exit 0
fi

# If data is empty, do nothing
if ! find "$DATA_DIR" -mindepth 1 -print -quit | grep -q .; then
  echo "$(ts) | INFO: data is empty; nothing to move" >> "$LOG_FILE"
  exit 0
fi

# Put each run into a timestamped folder to avoid overwrites
RUN_DIR="$(date "+%Y-%m-%d_%H%M%S")"
DEST_DIR="$MOUNT_POINT/$DEST_BASE_SUBDIR/$RUN_DIR"
mkdir -p "$DEST_DIR"

echo "$(ts) | START | from=$DATA_DIR | to=$DEST_DIR" >> "$LOG_FILE"

# Move everything (files + folders) from data -> destination
# --remove-source-files removes files after successful copy; dirs are cleaned up after.
rsync -a --remove-source-files --info=NAME,STATS2 "$DATA_DIR"/ "$DEST_DIR"/ >> "$LOG_FILE" 2>&1

# Remove now-empty directories from data
find "$DATA_DIR" -mindepth 1 -type d -empty -delete

echo "$(ts) | DONE" >> "$LOG_FILE"
