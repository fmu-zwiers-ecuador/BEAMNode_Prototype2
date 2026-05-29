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

EASTERN_TZ = ZoneInfo("America/New_York")

# ---------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------
JSON_FILEPATH = "/home/pi/BEAMNode_Prototype2/scripts/node/shipping_queuing/node_states.json"
SUPERVISOR_DATA_ROOT = "/home/pi/data"
REMOTE_SHIP_DIR = "/home/pi/shipping"
LOG_FILE = "/home/pi/logs/queue.log"
NAS_PATH = "PiSync@100.115.5.12:/volume1/BEAM_test_data/FEC/"
NAS_SSH_CMD = "ssh -p 2222"
MOVE_TO_DRIVE_SCRIPT = "move_supervisor_data_to_beamdrive.sh"
RUN_USER = "pi"

MAX_RETRIES = 5
PING_COUNT = 1
ACTIVE_NODE_NAMES = {"node1", "node2", "node3", "node4", "node5"}

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
    os.makedirs(path, exist_ok=True)
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

def rsync_pull(full_hostname):
    """Pulls data from node to supervisor data root."""
    fix_local_permissions(SUPERVISOR_DATA_ROOT)
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
    fix_remote_permissions(full_hostname, REMOTE_SHIP_DIR)
    cmd = ["ssh"] + SSH_OPTS + [f"pi@{full_hostname}", f"rm -rf {REMOTE_SHIP_DIR}/*"]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        log(f"{full_hostname}: Remote folder cleared.")
        return True
    except subprocess.CalledProcessError as e:
        log(f"{full_hostname}: Deleting shipping folder: WARNING - SSH command failed (exit code {e.returncode}): {e.cmd}")
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
    Pushes data from supervisor root to Synology NAS using optimized flags 
    to prevent permission and timestamp operation-denied errors on NAS.
    """
    log("=== STARTING NAS BACKUP ===")
    
    # Check if there is data to send before running rsync
    if not os.path.exists(SUPERVISOR_DATA_ROOT) or not os.listdir(SUPERVISOR_DATA_ROOT):
        log("No local data found in supervisor root directory. Skipping NAS backup.")
        return True

    # Source trailing slash is critical to move contents, not the folder itself
    source_dir = f"{SUPERVISOR_DATA_ROOT}/"
    
    # Replaced '-avz' to bypass target permission and owner modifications on Synology DSM:
    # -r: recursive copy
    # -l: copy symlinks as symlinks
    # -t: copy times (Synology allowed if write privilege is valid)
    # -v: verbose logging
    # --no-perms --no-owner --no-group: prevents metadata synchronization drops
    # --omit-dir-times: skips top-level folder attribute manipulation updates
    cmd = [
        "rsync", "-rltv", "--no-perms", "--no-owner", "--no-group", "--omit-dir-times",
        "-e", NAS_SSH_CMD,
        source_dir,
        NAS_PATH
    ]
    
    try:
        # Run rsync and capture error output to surface to log infrastructure
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        log("=== NAS BACKUP COMPLETED SUCCESSFULLY ===")
        return True
    except subprocess.CalledProcessError as e:
        log(f"=== NAS BACKUP FAILED (exit code {e.returncode}) ===")
        if e.stderr:
            log(f"Rsync stderr: {e.stderr.strip()}")
        return False

# ---------------------------------------------------
# MAIN EXECUTION (Placeholder block)
# ---------------------------------------------------
