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
    """Moves data in the supervisor data folder to the remove NAS unit"""
    cmd = [
        "rsync", "-rzv",
        "--no-perms", "--no-owner", "--no-group",
        "--no-times", "--omit-dir-times",
        "-e", NAS_SSH_CMD,
        SUPERVISOR_DATA_ROOT + "/",
        NAS_PATH
    ]
    log(f"Moving to NAS with command: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, text=True)
        if result.stdout.strip():
            log(f"Moving to NAS output: {result.stdout.strip()}")
        return True
    except FileNotFoundError:
        log("ERROR: The command was not found.")
        return False
    except subprocess.CalledProcessError as e:
        log(f"Moving to NAS ERROR: Command failed with exit code {e.returncode}")
        if e.stdout:
            log(f"NAS stdout: {e.stdout.strip()}")
        if e.stderr:
            log(f"NAS stderr: {e.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired as e:
        log(f"Moving to NAS: Command timed out after {e.timeout} seconds")
        return False
    except subprocess.SubprocessError as e:
        log(f"Moving to NAS: A general subprocess error occurred: {e}")
        return False
        
def move_to_beamdrive():
    """Moves data in the supervisor data folder to the local BEAM Drive"""
    cmd = ["bash", MOVE_TO_DRIVE_SCRIPT]
    try:
        subprocess.run(cmd, capture_output=True, check=True, text=True)
        return True
    except FileNotFoundError:
        log(f"ERROR: The script {MOVE_TO_DRIVE_SCRIPT} was not found.")
        return False
    except subprocess.CalledProcessError as e:
        log(f"Move To BEAMDrive ERROR: Command failed with exit code {e.returncode}")
        log(f"Error detail: {e.stderr}")
        return False
    except subprocess.TimeoutExpired as e:
        log(f"Move To BEAMDrive: Command timed out after {e.timeout} seconds")
        return False
    except subprocess.SubprocessError as e:
        log(f"Move To BEAMDrive: A general subprocess error occurred: {e}")
        return False
        
def clear_supervisor_data():
    """Deletes data from the data folder on the supervisor after successfully backing up the tohe NAS Unit and BEAMDrive"""
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
        log(f"ERROR: Permission denied clearing {SUPERVISOR_DATA_ROOT} — check ownership.")
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
            if not failed_nodes: break
            log(f"--- Retry Round {attempt} ---")
            still_failing = []
            for name in failed_nodes:
                full_host = get_full_host(name, nodes[name])
                if ping_node(full_host) and has_remote_data(full_host):
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
        
    if nas & beamdrive:
        clear_sup_data = clear_supervisor_data()
        if clear_sup_data:
            log("=== SUCCESS clearing supervisor data folder ===")
        else:
            log("=== FAILURE clearing suoervisor data folder ===")
    else:
        log("=== WARNING: Did not clear supervisor data as either NAS unit or BEAMDrive backup failed")

    log("=== FINAL STATUS: COMPLETED ===")

if __name__ == "__main__":
    main()
