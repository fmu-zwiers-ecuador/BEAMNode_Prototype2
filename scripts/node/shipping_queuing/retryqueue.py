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

# ---------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------
JSON_FILEPATH = "/home/pi/BEAMNode_Prototype2/scripts/node/shipping_queuing/node_states.json"
SUPERVISOR_DATA_ROOT = "/home/pi/data"
REMOTE_SHIP_DIR = "/home/pi/shipping"
LOG_FILE = "/home/pi/logs/queue.log"
NAS_PATH = "PiSync@100.115.5.12:/BEAM_test_data/FEC/"
MOVE_TO_DRIVE_SCRIPT = "move_supervisor_data_to_beamdrive.sh"

MAX_RETRIES = 5
PING_COUNT = 1

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
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
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
    os.makedirs(SUPERVISOR_DATA_ROOT, exist_ok=True)
    # The trailing slash on remote_source is critical to pull CONTENTS, not the folder
    remote_source = f"pi@{full_hostname}:{REMOTE_SHIP_DIR}/"
    cmd = ["scp", "-r"] + SSH_OPTS + [SUPERVISOR_DATA_ROOT, NAS_PATH]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
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
        "rsync", "-avz", "--timeout=30",
        "--partial", "--ignore-existing",
        "-e", "ssh",
        SUPERVISOR_DATA_ROOT + "/",
        NAS_PATH
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, text=True)
        return True
    except FileNotFoundError:
        log("ERROR: The command was not found.")
        return False
    except subprocess.CalledProcessError as e:
        log(f"Moving to NAS ERROR: Command failed with exit code {e.returncode}")
        log(f"Error detail: {e.stderr}")
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
    cmd = ["sudo", "rm", "-rf", f"{SUPERVISOR_DATA_ROOT}/*"]
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
        full_host = get_full_host(name, info)
        nodes[name]["node_state"] = "alive" if ping_node(full_host) else "dead"
        if nodes[name]["node_state"] == "dead":
            log(f"{full_host}: OFFLINE")
    save_nodes(nodes)

    # STEP 2: Initial Transfer Attempt
    failed_nodes = []
    for name, info in nodes.items():
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
