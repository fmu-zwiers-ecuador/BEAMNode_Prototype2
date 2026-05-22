#!/usr/bin/env python3
# launcher.py - Master controller for BEAMNode Prototype 1
# Responsibilities: Startup Detection, Scheduler Maintenance, and 13:00 Shipping.
# Motion capture is intentionally manual-run only.

import subprocess
import os
import time
import sys
import shutil
from datetime import datetime

# --- CONFIGURATION ---
NODE_DIR = "/home/pi/BEAMNode_Prototype2/scripts/node"
DETECT_PATH = os.path.join(NODE_DIR, "sensor_detection/detect.py")
SCHEDULER_PATH = os.path.join(NODE_DIR, "scheduler.py")
SHIPPING_PATH = os.path.join(NODE_DIR, "shipping_queuing/shipping.py")
MOTION_TRIGGER_PATH = os.path.join(NODE_DIR, "Motion/beam_motion_trigger.py")
DATA_DIR = "/home/pi/data"
SHIPPING_DIR = "/home/pi/shipping"
LOG_PATH = "/home/pilogs/launcher.log"
SHIPPING_LOG_PATH = "/home/pi/logs/shipping.log"

def log(msg):
    """Internal launcher logging."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [launcher] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except:
        pass

def log_shipping(msg):
    """Shipping-specific logging."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [shipping] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(SHIPPING_LOG_PATH), exist_ok=True)
        with open(SHIPPING_LOG_PATH, "a") as f:
            f.write(line + "\n")
    except:
        pass

def run_script_sync(path):
    """Runs a script and waits for it to finish (Synchronous)."""
    if os.path.exists(path):
        log(f"Executing: {path}")
        result = subprocess.run(["python3", path])
        return result.returncode
    else:
        log(f"ERROR: File not found at {path}")
        return None

def start_scheduler_async():
    """Starts the original scheduler.py in the background (Asynchronous)."""
    if os.path.exists(SCHEDULER_PATH):
        log(f"Starting Background Scheduler: {SCHEDULER_PATH}")
        return subprocess.Popen(["python3", SCHEDULER_PATH])
    else:
        log(f"CRITICAL ERROR: Scheduler script missing.")
        return None

def start_motion_trigger_async():
    """Starts motion trigger on startup (Asynchronous)."""
    if os.path.exists(MOTION_TRIGGER_PATH):
        log(f"Starting Motion Trigger: {MOTION_TRIGGER_PATH}")
        return subprocess.Popen(["python3", MOTION_TRIGGER_PATH])
    else:
        log(f"ERROR: Motion trigger script missing at {MOTION_TRIGGER_PATH}")
        return None

def move_data_to_shipping():
    """Move everything in /home/pi/data to /home/pi/shipping and clear data folder."""
    if not os.path.exists(DATA_DIR):
        log(f"Data directory not found: {DATA_DIR}")
        return

    os.makedirs(SHIPPING_DIR, exist_ok=True)

    moved_any = False
    for entry in os.listdir(DATA_DIR):
        src = os.path.join(DATA_DIR, entry)
        dest = os.path.join(SHIPPING_DIR, entry)
        try:
            shutil.move(src, dest)
            log(f"Moved {src} -> {dest}")
            moved_any = True
        except Exception as e:
            log(f"ERROR moving {src} -> {dest}: {e}")

    if not moved_any:
        log("No data to move from /home/pi/data")

    # Ensure /home/pi/data exists and is empty
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception as e:
        log(f"ERROR ensuring data dir exists: {e}")

if __name__ == "__main__":
    log("=== BEAMNode System Startup ===")

    # 1. REQUIREMENT: Run detect.py once on startup
    run_script_sync(DETECT_PATH)

    # 1b. REQUIREMENT: Start motion trigger on startup
    motion_proc = start_motion_trigger_async()

    # 2. REQUIREMENT: Start original scheduler and keep it going
    sched_proc = start_scheduler_async()

    if sched_proc is None:
        log("Failed to initialize scheduler. System exiting.")
        sys.exit(1)

    # 3. MONITORING LOOP
    log("Entering master monitoring loop...")
    last_data_move_date = None
    while True:
        try:
            now = datetime.now()
            now_utc = datetime.utcnow()

            # A. Check Scheduler Health (Restart if Pi went down or process crashed)
            if sched_proc is None or sched_proc.poll() is not None:
                log("ALERT: Scheduler process stopped. Restarting...")
                time.sleep(5)
                sched_proc = start_scheduler_async()

            # B. REQUIREMENT: Run Shipping.py at 13:00
            # We use a 30-second window to ensure the trigger catches
            if now.hour == 13 and now.minute == 0 and 0 <= now.second <= 30:
                log("13:00 Target reached. Running Shipping.py...")
                log_shipping("Scheduled shipping trigger fired at 13:00")
                result_code = run_script_sync(SHIPPING_PATH)
                if result_code == 0:
                    log_shipping("Shipping completed successfully (exit code 0)")
                elif result_code is None:
                    log_shipping("Shipping failed to start (script missing)")
                else:
                    log_shipping(f"Shipping failed (exit code {result_code})")
                log("Shipping complete. Resuming monitor.")
                time.sleep(31) # Avoid double-triggering within the same minute

            # C. REQUIREMENT: Move /home/pi/data -> /home/pi/shipping at 18:00 UTC
            if (
                now_utc.hour == 18
                and now_utc.minute == 0
                and 0 <= now_utc.second <= 30
                and last_data_move_date != now_utc.date()
            ):
                log("18:00 UTC reached. Moving /home/pi/data to /home/pi/shipping...")
                move_data_to_shipping()
                last_data_move_date = now_utc.date()
                log("Data move complete. Resuming monitor.")
                time.sleep(31) # Avoid double-triggering within the same minute

            # Sleep to keep CPU usage minimal
            time.sleep(10)

        except KeyboardInterrupt:
            log("Manual shutdown detected. Terminating scheduler...")
            sched_proc.terminate()
            break
        except Exception as e:
            log(f"Unexpected monitor error: {e}")
            time.sleep(10)
