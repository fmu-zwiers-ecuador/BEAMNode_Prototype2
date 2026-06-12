#!/usr/bin/env python3
"""
retryqueue.py: Requests and queues data from nodes via mDNS.
Path: /home/pi/shipping (on node) ==> /home/pi/data (on supervisor)

Author: Gabriel Gonzalez, Noel Challa, Alex Lance, Jackson Roberts, and Jaylen Small
Last Updated: 2-6-26 
"""

import sys
import subprocess
import os
import json
from datetime import datetime
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo
from dotenv import load_dotenv, dotenv_values 
load_dotenv()

EASTERN_TZ = ZoneInfo("America/New_York")

# ---------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------
JSON_FILEPATH = "/home/pi/BEAMNode_Prototype2/scripts/node/shipping_queuing/node_states.json"
SUPERVISOR_DATA_ROOT = "/home/pi/usbmnt/data"
REMOTE_SHIP_DIR = "/home/pi/shipping"
LOG_FILE = "/home/pi/logs/queue.log"
NAS_PATH = os.getenv("NAS_PATH") + ":/BEAM_test_data/FEC/"
NAS_SSH_CMD = "ssh -p 2222"
MOVE_TO_DRIVE_SCRIPT = "/home/pi/BEAMNode_Prototype2/scripts/node/shipping_queuing/move_supervisor_data_to_beamdrive.sh"
RUN_USER = "pi"
NODE_USB_DEVICE = "/dev/sda1"
NODE_USB_FALLBACK_MOUNT = "/home/pi/usbmnt"
NODE_USB_DRIVE_NAME = "BEAMdrive"
NODE_USB_BACKUP_SUBDIR = "shipping_archive"

MAX_RETRIES = 5
PING_COUNT = 1
ACTIVE_NODE_NAMES = {"node1", "node2", "node3"}

# SSH options to force non-interactive mode and bypass prompts
SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=accept-new"
]

# ---------------------------------------------------
# LOGGING
# ---------------------------------------------------
def log(msg):
    ts = datetime.now(EASTERN_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] {msg}"
    log_dir = os.path.dirname(LOG_FILE)
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except PermissionError:
        subprocess.run(["sudo", "-n", "chown", "-R", f"{RUN_USER}:{RUN_USER}", log_dir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["sudo", "-n", "chmod", "-R", "u+rwX", log_dir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with open(LOG_FILE, "a") as f:
                f.write(line + "\n")
        except PermissionError:
            print(f"{line} | WARNING: Could not write to {LOG_FILE}")
    print(line)

# ---------------------------------------------------
# LOAD/SAVE NODE STATE
# ---------------------------------------------------
def load_nodes():
    if not os.path.exists(JSON_FILEPATH):
        log(f"ERROR: node state file missing: {JSON_FILEPATH}")
        return {}
    try:
        with open(JSON_FILEPATH, "r") as f:
            return json.load(f)
    except Exception as e:
        log(f"ERROR: Could not read JSON: {e}")
        return {}

def save_nodes(nodes):
    try:
        with open(JSON_FILEPATH, "w") as f:
            json.dump(nodes, f, indent=4)
    except Exception as e:
        log(f"ERROR: Could not save JSON: {e}")

# ---------------------------------------------------
# NETWORK & DATA OPERATIONS
# ---------------------------------------------------
def get_full_host(name, info):
    raw_host = info.get("hostname", name)
    return raw_host if raw_host.endswith(".local") else f"{raw_host}.local"

def should_process_node(name):
    return name in ACTIVE_NODE_NAMES

def ping_node(full_hostname):
    try:
        subprocess.run(
            ["ping", "-c", str(PING_COUNT), "-W", "2", full_hostname],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
        return True
    except:
        return False

def run_cmd(cmd, label, **kwargs):
    """Run a command and log stderr if it fails."""
    try:
        subprocess.run(cmd, check=True, **kwargs)
        return True
    except FileNotFoundError as e:
        log(f"{label}: command not found: {e}")
        return False
    except subprocess.CalledProcessError as e:
        stderr = ""
        if getattr(e, "stderr", None):
            stderr = f": {e.stderr.strip()}"
        log(f"{label}: command failed with exit code {e.returncode}{stderr}")
        return False

def fix_local_permissions(path):
    """Make a local data path writable by the pi user when sudo is available."""
    try:
        os.makedirs(path, exist_ok=True)
    except PermissionError:
        parent = os.path.dirname(path)
        log(f"WARNING: Permission denied creating {path}; trying sudo repair for {parent}.")
        run_cmd(["sudo", "-n", "chown", "-R", f"{RUN_USER}:{RUN_USER}", parent], f"Fixing permissions for {parent}", stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        run_cmd(["sudo", "-n", "chmod", "-R", "u+rwX", parent], f"Fixing permissions for {parent}", stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            os.makedirs(path, exist_ok=True)
        except PermissionError:
            log(f"WARNING: Could not create {path}. Run installation_bash/set_script_permissions.sh or installation_bash/set_retryservice.sh once with sudo.")
            return False
    if os.access(path, os.R_OK | os.W_OK | os.X_OK):
        return True

    commands = [
        ["sudo", "-n", "chown", "-R", f"{RUN_USER}:{RUN_USER}", path],
        ["sudo", "-n", "chmod", "-R", "u+rwX", path],
    ]
    for cmd in commands:
        if not run_cmd(cmd, f"Fixing permissions for {path}", stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True):
            log(f"WARNING: Could not auto-fix permissions for {path}. If sudo needs a password, run the permission fix once manually.")
            return False
    return True

def fix_remote_permissions(full_hostname, path):
    """Make a remote node path writable by pi before cleanup."""
    remote_cmd = (
        f"sudo -n chown -R {RUN_USER}:{RUN_USER} {path} && "
        f"sudo -n chmod -R u+rwX {path}"
    )
    cmd = ["ssh"] + SSH_OPTS + [f"pi@{full_hostname}", remote_cmd]
    if not run_cmd(cmd, f"{full_hostname}: Fixing permissions for {path}", stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True):
        log(f"{full_hostname}: WARNING - Could not auto-fix permissions for {path}. If sudo needs a password on the node, fix it once manually.")
        return False
    return True

def has_remote_data(full_hostname):
    """Lists remote files to verify presence of data."""
    remote_path = f"pi@{full_hostname}:{REMOTE_SHIP_DIR}/"
    ssh_cmd = "ssh " + " ".join(SSH_OPTS)
    cmd = ["rsync", "--list-only", "-e", ssh_cmd, remote_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        entries = []
        for line in result.stdout.splitlines():
            cleaned = line.rstrip() #This takes the original output and removes trailing whitespaces
            if cleaned.endswith(" ."): #This ignores the actually directory (which is " .")
                continue
            entries.append(cleaned)

        if entries: 
            log(f"{full_hostname}: SUCCESS checking remote shipping folder")
            return True
        else:
            log(f"{full_hostname}: The shipping folder is empty")
            return False
    except subprocess.CalledProcessError as e:
        log(f"{full_hostname}: rsync list failed (exit {e.returncode}): {e.stderr.strip()}")
        return False
    except Exception as e:
        log(f"{full_hostname}: Exception {e}")
        return False

def backup_remote_shipping_to_usb(full_hostname):
    """Copies the node shipping folder to that node's USB drive before transfer."""
    remote_script = f"""
set -euo pipefail

SHIP_DIR={REMOTE_SHIP_DIR!r}
USB_DEVICE={NODE_USB_DEVICE!r}
FALLBACK_MOUNT={NODE_USB_FALLBACK_MOUNT!r}
DRIVE_NAME={NODE_USB_DRIVE_NAME!r}
BACKUP_SUBDIR={NODE_USB_BACKUP_SUBDIR!r}
LOG_FILE=/home/pi/logs/node_usb_backup.log

mkdir -p "$(dirname "$LOG_FILE")"

log() {{
  printf '[%s] %s\\n' "$(date -Iseconds)" "$*" >> "$LOG_FILE"
}}

if [ ! -d "$SHIP_DIR" ]; then
  log "INFO: shipping dir not found: $SHIP_DIR"
  exit 0
fi

if ! find "$SHIP_DIR" -mindepth 1 -print -quit | grep -q .; then
  log "INFO: shipping is empty; nothing to back up"
  exit 0
fi

MOUNT_POINT="$(findmnt -rn -S "LABEL=$DRIVE_NAME" -o TARGET 2>/dev/null || true)"

if [ -z "$MOUNT_POINT" ] && mountpoint -q "/media/pi/$DRIVE_NAME"; then
  MOUNT_POINT="/media/pi/$DRIVE_NAME"
fi

if [ -z "$MOUNT_POINT" ] && mountpoint -q "$FALLBACK_MOUNT"; then
  MOUNT_POINT="$FALLBACK_MOUNT"
fi

if [ -z "$MOUNT_POINT" ]; then
  mkdir -p "$FALLBACK_MOUNT"
  RUN_UID="$(id -u pi)"
  RUN_GID="$(id -g pi)"
  if sudo -n mount -o "uid=$RUN_UID,gid=$RUN_GID,umask=0002" "$USB_DEVICE" "$FALLBACK_MOUNT" 2>>"$LOG_FILE"; then
    MOUNT_POINT="$FALLBACK_MOUNT"
  elif sudo -n mount "$USB_DEVICE" "$FALLBACK_MOUNT" 2>>"$LOG_FILE"; then
    MOUNT_POINT="$FALLBACK_MOUNT"
  fi
fi

if [ -z "$MOUNT_POINT" ] || [ ! -d "$MOUNT_POINT" ]; then
  log "ERROR: USB drive not mounted/found. Expected LABEL=$DRIVE_NAME, /media/pi/$DRIVE_NAME, or $USB_DEVICE mounted at $FALLBACK_MOUNT"
  echo "USB_BACKUP_ERROR reason=usb_not_mounted"
  exit 1
fi

echo "USB_MOUNT_READY mount=$MOUNT_POINT"

if [ ! -w "$MOUNT_POINT" ] && [ "$MOUNT_POINT" = "$FALLBACK_MOUNT" ]; then
  RUN_UID="$(id -u pi)"
  RUN_GID="$(id -g pi)"
  sudo -n mount -o "remount,uid=$RUN_UID,gid=$RUN_GID,umask=0002" "$MOUNT_POINT" 2>>"$LOG_FILE" || true
fi

HOST="$(hostname)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
DEST_DIR="$MOUNT_POINT/$BACKUP_SUBDIR/$HOST-$RUN_ID"

mkdir_as="pi"
if mkdir -p "$DEST_DIR" 2>>"$LOG_FILE"; then
  mkdir_as="pi"
elif sudo -n mkdir -p "$DEST_DIR" 2>>"$LOG_FILE"; then
  mkdir_as="root"
else
  log "ERROR: cannot create USB backup directory: $DEST_DIR"
  echo "USB_BACKUP_ERROR reason=mkdir_failed dest=$DEST_DIR"
  exit 1
fi

log "START: backing up $SHIP_DIR to $DEST_DIR"
copy_as="pi"
RSYNC_OPTS="-rt --ignore-existing --no-owner --no-group --no-perms --omit-dir-times"
if rsync $RSYNC_OPTS "$SHIP_DIR"/ "$DEST_DIR"/ >> "$LOG_FILE" 2>&1; then
  copy_as="pi"
elif sudo -n rsync $RSYNC_OPTS "$SHIP_DIR"/ "$DEST_DIR"/ >> "$LOG_FILE" 2>&1; then
  copy_as="root"
else
  log "ERROR: rsync backup failed: $SHIP_DIR -> $DEST_DIR"
  echo "USB_BACKUP_ERROR reason=rsync_failed dest=$DEST_DIR"
  exit 1
fi

file_count="$(find "$DEST_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')"
byte_count="$(du -sk "$DEST_DIR" 2>/dev/null | awk '{{print $1 * 1024}}' || echo unknown)"
log "DONE: backup complete dest=$DEST_DIR files=$file_count bytes=$byte_count mkdir_as=$mkdir_as copy_as=$copy_as"
echo "USB_BACKUP_SUCCESS dest=$DEST_DIR files=$file_count bytes=$byte_count mkdir_as=$mkdir_as copy_as=$copy_as"
"""
    cmd = ["ssh"] + SSH_OPTS + [f"pi@{full_hostname}", "bash", "-s"]
    try:
        result = subprocess.run(
            cmd,
            input=remote_script,
            capture_output=True,
            check=True,
            text=True
        )
        for line in result.stdout.splitlines():
            if line.strip():
                log(f"{full_hostname}: {line.strip()}")
        log(f"{full_hostname}: USB BACKUP SUCCESS")
        return True
    except FileNotFoundError as e:
        log(f"{full_hostname}: USB backup before transfer: command not found: {e}")
        return False
    except subprocess.CalledProcessError as e:
        detail = ""
        for output in (e.stdout, e.stderr):
            if output:
                lines = [line.strip() for line in output.splitlines() if line.strip()]
                if lines:
                    detail = ": " + " | ".join(lines[-3:])
        log(f"{full_hostname}: USB backup before transfer failed with exit code {e.returncode}{detail}")
        return False

def rsync_pull(full_hostname):
    """Pulls data from node to supervisor data root."""
    if not fix_local_permissions(SUPERVISOR_DATA_ROOT):
        log(f"{full_hostname}: WARNING - continuing transfer; {SUPERVISOR_DATA_ROOT} may already be writable.")

    # The trailing slash on remote_source is critical to pull CONTENTS, not the folder
    remote_source = f"pi@{full_hostname}:{REMOTE_SHIP_DIR}/"
    ssh_cmd = "ssh " + " ".join(SSH_OPTS)
    cmd = [
        "rsync", "-avz", "--partial", "--ignore-existing",
        "-e", ssh_cmd,
        remote_source,
        SUPERVISOR_DATA_ROOT
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        fix_local_permissions(SUPERVISOR_DATA_ROOT)
        return True
    except subprocess.CalledProcessError:
        return False

def delete_shipping_data(full_hostname):
    """Removes data from node shipping folder after successful pull."""
    cmd = ["ssh"] + SSH_OPTS + [f"pi@{full_hostname}", f"rm -rf {REMOTE_SHIP_DIR}/*"]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        log(f"{full_hostname}: Remote folder cleared.")
        return True
    except subprocess.CalledProcessError as e:
        log(f"{full_hostname}: Normal cleanup failed (exit code {e.returncode}); trying permission repair once.")
        if not fix_remote_permissions(full_hostname, REMOTE_SHIP_DIR):
            return False
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
            log(f"{full_hostname}: Remote folder cleared after permission repair.")
            return True
        except subprocess.CalledProcessError as retry_error:
            log(f"{full_hostname}: Deleting shipping folder: WARNING - SSH command failed (exit code {retry_error.returncode}): {retry_error.cmd}")
            return False
    except FileNotFoundError as e:
        log(f"{full_hostname}: Deleting shipping folder: WARNING - 'ssh' binary not found: {e}")
        return False
    except PermissionError as e:
        log(f"{full_hostname}: Deleting shipping folder: WARNING - Permission denied running SSH: {e}")
        return False
    except Exception as e:
        log(f"{full_hostname}: Deleting shipping folder: WARNING - Unexpected error ({type(e).__name__}): {e}")
        return False
    
def move_to_nas():
    """
    Pushes data from supervisor root to Synology NAS using scp.
    """
    log("=== STARTING NAS BACKUP ===")
    
    # Check if there is data to send before running scp
    if not os.path.exists(SUPERVISOR_DATA_ROOT) or not os.listdir(SUPERVISOR_DATA_ROOT):
        log("No local data found in supervisor root directory. Skipping NAS backup.")
        return True

    # The "/." suffix copies the contents of data, not a nested data folder.
    source_dir = os.path.join(SUPERVISOR_DATA_ROOT, ".")
    cmd = [
        "scp", "-r", 
        source_dir,
        NAS_PATH
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if result.stdout.strip():
            log(f"SCP stdout: {result.stdout.strip()}")
        log("=== NAS BACKUP COMPLETED SUCCESSFULLY ===")
        return True
    except subprocess.CalledProcessError as e:
        log(f"=== NAS BACKUP FAILED (exit code {e.returncode}) ===")
        if e.stdout:
            log(f"SCP stdout: {e.stdout.strip()}")
        if e.stderr:
            log(f"SCP stderr: {e.stderr.strip()}")
        return False

# ---------------------------------------------------
# LOCAL BEAMDRIVE BACKUP
# ---------------------------------------------------
def move_to_beamdrive():
    """Copies supervisor data to the local BEAM Drive."""
    cmd = ["bash", MOVE_TO_DRIVE_SCRIPT]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, text=True)
        if result.stdout.strip():
            log(f"Move To BEAMDrive output: {result.stdout.strip()}")
        return True
    except FileNotFoundError:
        log(f"ERROR: The script {MOVE_TO_DRIVE_SCRIPT} was not found.")
        return False
    except subprocess.CalledProcessError as e:
        log(f"Move To BEAMDrive ERROR: Command failed with exit code {e.returncode}")
        if e.stdout:
            log(f"BEAMDrive stdout: {e.stdout.strip()}")
        if e.stderr:
            log(f"BEAMDrive stderr: {e.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired as e:
        log(f"Move To BEAMDrive: Command timed out after {e.timeout} seconds")
        return False
    except subprocess.SubprocessError as e:
        log(f"Move To BEAMDrive: A general subprocess error occurred: {e}")
        return False

def clear_supervisor_data():
    """Deletes supervisor data after successful NAS and BEAMDrive backups."""
    fix_local_permissions(SUPERVISOR_DATA_ROOT)
    try:
        for item in os.scandir(SUPERVISOR_DATA_ROOT):
            if item.is_dir():
                shutil.rmtree(item.path)
            else:
                os.remove(item.path)
        return True
    except FileNotFoundError:
        log(f"ERROR: {SUPERVISOR_DATA_ROOT} not found.")
        return False
    except PermissionError:
        log(f"ERROR: Permission denied clearing {SUPERVISOR_DATA_ROOT} - check ownership.")
        return False
    except Exception as e:
        log(f"ERROR: Failed to clear supervisor data: {e}")
        return False

# ---------------------------------------------------
# MAIN PROCESS
# ---------------------------------------------------
def main():
    log("=== STARTING DATA TRANSFER: NODES TO SUPERVISOR ===")
    nodes = load_nodes()
    if not nodes:
        return

    # STEP 1: Verify Node Health
    for name, info in nodes.items():
        if not should_process_node(name):
            log(f"{name}: SKIPPED - not in active node list")
            continue
        full_host = get_full_host(name, info)
        nodes[name]["node_state"] = "alive" if ping_node(full_host) else "dead"
        if nodes[name]["node_state"] == "dead":
            log(f"{full_host}: OFFLINE")
    save_nodes(nodes)

    # STEP 2: Initial Transfer Attempt
    failed_nodes = []
    usb_backed_up_nodes = set()

    def try_usb_backup(name, full_host):
        if name in usb_backed_up_nodes:
            log(f"{full_host}: USB backup already completed this run")
            return True
        if backup_remote_shipping_to_usb(full_host):
            usb_backed_up_nodes.add(name)
            return True
        log(f"{full_host}: WARNING - USB backup failed; continuing supervisor pull anyway")
        return False

    for name, info in nodes.items():
        if not should_process_node(name):
            continue
        full_host = get_full_host(name, info)

        if info["node_state"] == "dead":
            failed_nodes.append(name)
            continue

        if not has_remote_data(full_host):
            log(f"{full_host}: No files found in {REMOTE_SHIP_DIR}/")
            nodes[name]["transfer_fail"] = False
            continue

        try_usb_backup(name, full_host)

        log(f"{full_host}: Pulling data...")
        if rsync_pull(full_host):
            log(f"{full_host}: TRANSFER SUCCESS")
            nodes[name]["transfer_fail"] = False
            delete_shipping_data(full_host)
        else:
            log(f"{full_host}: TRANSFER FAILURE")
            nodes[name]["transfer_fail"] = True
            failed_nodes.append(name)
    save_nodes(nodes)

    # STEP 3: Retries for Offline or Failed Nodes
    if failed_nodes:
        log(f"=== RETRYING FAILED NODES (Max {MAX_RETRIES}) ===")
        for attempt in range(1, MAX_RETRIES + 1):
            if not failed_nodes:
                break
            log(f"--- Retry Round {attempt} ---")
            still_failing = []
            for name in failed_nodes:
                full_host = get_full_host(name, nodes[name])
                if ping_node(full_host) and has_remote_data(full_host):
                    try_usb_backup(name, full_host)
                    if rsync_pull(full_host):
                        log(f"{full_host}: SUCCESS on retry")
                        nodes[name]["transfer_fail"] = False
                        delete_shipping_data(full_host)
                        continue
                still_failing.append(name)
            failed_nodes = still_failing
            save_nodes(nodes)

    log("=== Moving data to the remote NAS unit ===")
    nas = move_to_nas()
    if nas:
        log("=== SUCCESS moving NAS data ===")
    else:
        log("=== FAILURE moving NAS data ===")

    log("=== Moving data to the local BEAM Drive ===")
    beamdrive = move_to_beamdrive()
    if beamdrive:
        log("=== SUCCESS moving BEAM Drive data ===")
    else:
        log("=== FAILURE moving BEAM Drive data ===")

    if nas and beamdrive:
        if clear_supervisor_data():
            log("=== SUCCESS clearing supervisor data folder ===")
        else:
            log("=== FAILURE clearing supervisor data folder ===")
    else:
        log("=== WARNING: Did not clear supervisor data as either NAS unit or BEAMDrive backup failed")

    log("=== FINAL STATUS: COMPLETED ===")

if __name__ == "__main__":
    main()
