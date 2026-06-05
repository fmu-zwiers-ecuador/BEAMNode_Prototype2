#!/usr/bin/env python3
# launcher.py - Master controller for BEAMNode Prototype 1
# Responsibilities: Startup Detection, Scheduler Maintenance, and 13:00 Shipping.
# Motion capture is intentionally manual-run only.

import subprocess
import os
import time
import sys
import shutil
import json
from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN_TZ = ZoneInfo("America/New_York")

# --- CONFIGURATION ---
NODE_DIR = "/home/pi/BEAMNode_Prototype2/scripts/node"
DETECT_PATH = os.path.join(NODE_DIR, "sensor_detection/detect.py")
CONFIG_PATH = os.path.join(NODE_DIR, "config.json")
SCHEDULER_PATH = os.path.join(NODE_DIR, "scheduler.py")
MOTION_TRIGGER_PATH = os.path.join(NODE_DIR, "Motion/beam_motion_trigger.py")
MOTION_MERGE_WORKER_PATH = os.path.join(NODE_DIR, "Motion/motion_merge_worker.py")
POWER_DIR = "/home/pi/BEAMNode_Prototype2/scripts/power"
LOW_POWER_BACKENDS = {
    "mppt": {
        "config_key": "low_power_mode",
        "path": os.path.join(POWER_DIR, "low_power_mode.py"),
        "log_path": "/home/pi/logs/low_power_mode_output.log",
    },
    "pvpi": {
        "config_key": "lpm_pvpi",
        "path": os.path.join(POWER_DIR, "Lpm_pvpi.py"),
        "log_path": "/home/pi/logs/lpm_pvpi_output.log",
    },
}
DATA_DIR = "/home/pi/data"
SHIPPING_DIR = "/home/pi/shipping"
LOG_PATH = "/home/pi/logs/launcher.log"
SHIPPING_LOG_PATH = "/home/pi/logs/shipping.log"
SHIPPING_PATH = os.path.join(NODE_DIR, "shipping_queuing/shipping.py")
DETECT_LOG_PATH = "/home/pi/logs/detect_output.log"
MOTION_LOG_PATH = "/home/pi/logs/motion_output.log"
MOTION_MERGE_LOG_PATH = "/home/pi/logs/motion_video_processing.log"

_MOTION_LOG_HANDLE = None
_MOTION_MERGE_LOG_HANDLE = None
_LOW_POWER_LOG_HANDLE = None

with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

def ensure_log_file(path):
    """Create log file and parent directories if missing."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a"):
            pass
    except:
        pass

def log(msg):
    """Internal launcher logging."""
    ts = datetime.now(EASTERN_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] [launcher] {msg}"
    print(line)
    try:
        ensure_log_file(LOG_PATH)
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except:
        pass

def log_shipping(msg):
    """Shipping-specific logging."""
    ts = datetime.now(EASTERN_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] [shipping] {msg}"
    print(line)
    try:
        ensure_log_file(SHIPPING_LOG_PATH)
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

def selected_low_power_backend():
    """Return the configured low-power backend, or None when disabled/invalid."""
    try:
        with open(CONFIG_PATH, "r") as f:
            current_config = json.load(f)
    except Exception as e:
        log(f"ERROR: Could not read low-power config: {e}")
        return None

    enabled_backends = []

    for name, backend in LOW_POWER_BACKENDS.items():
        section = current_config.get(backend["config_key"], {})
        if isinstance(section, dict) and section.get("enabled", False):
            enabled_backends.append(name)

    if len(enabled_backends) == 0:
        log("Low-power monitor disabled: both low_power_mode.enabled and lpm_pvpi.enabled are false")
        return None

    if len(enabled_backends) > 1:
        log(
            "ERROR: Multiple low-power backends enabled "
            f"({', '.join(enabled_backends)}). Enable only one of low_power_mode or lpm_pvpi."
        )
        return None

    return enabled_backends[0]

def start_low_power_monitor_async():
    """Start exactly one low-power monitor based on config.json."""
    backend_name = selected_low_power_backend()
    if backend_name is None:
        return None

    backend = LOW_POWER_BACKENDS[backend_name]
    script_path = backend["path"]
    if not os.path.exists(script_path):
        log(f"ERROR: Low-power script missing for {backend_name}: {script_path}")
        return None

    log(f"Starting {backend_name} low-power monitor: {script_path}")
    global _LOW_POWER_LOG_HANDLE
    ensure_log_file(backend["log_path"])
    _LOW_POWER_LOG_HANDLE = open(backend["log_path"], "a")
    return subprocess.Popen(
        ["python3", script_path],
        stdout=_LOW_POWER_LOG_HANDLE,
        stderr=_LOW_POWER_LOG_HANDLE,
    )

def motion_capture_enabled():
    """Return whether launcher should own motion capture processes."""
    return config.get("motion_capture", {}).get("enabled", True)

def start_motion_trigger_async():
    """Starts motion trigger on startup (Asynchronous)."""
    if not motion_capture_enabled():
        log("Motion trigger disabled because motion_capture.enabled is false in config.json")
        return None

    cam_config = config["camera"]
    enabled = cam_config.get("enabled", False)

    if not enabled:
        log("Motion trigger disabled because camera.enabled is false in config.json")
        return None

    if os.path.exists(MOTION_TRIGGER_PATH) and enabled:
        log(f"Starting Motion Trigger: {MOTION_TRIGGER_PATH}")
        global _MOTION_LOG_HANDLE
        ensure_log_file(MOTION_LOG_PATH)
        _MOTION_LOG_HANDLE = open(MOTION_LOG_PATH, "a")
        return subprocess.Popen(
            ["python3", MOTION_TRIGGER_PATH],
            stdout=_MOTION_LOG_HANDLE,
            stderr=_MOTION_LOG_HANDLE,
        )
    else:
        log(f"ERROR: Motion trigger script missing at {MOTION_TRIGGER_PATH}")
        return None

def warn_if_motion_services_active():
    """Warn when legacy/standalone motion services are active outside launcher."""
    for service in ["beam-motion-merge.service", "motio_camera.service"]:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", service],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log(f"Could not check {service}: {e}")
            continue

        if result.returncode == 0:
            log(
                f"WARNING: {service} is active outside launcher. "
                f"Disable it if launcher should be the only motion owner."
            )

def warn_if_legacy_low_power_services_active():
    """Warn when old standalone low-power services are active outside launcher."""
    for service in ["low_power_mode.service", "lpm_pvpi.service"]:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", service],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log(f"Could not check {service}: {e}")
            continue

        if result.returncode == 0:
            log(
                f"WARNING: {service} is active outside launcher. "
                "Disable it so only the config-selected low-power monitor runs."
            )

def start_motion_merge_worker_async():
    """Starts final-video processing worker in the background."""
    motion_config = config.get("motion_capture", {})

    if not motion_capture_enabled():
        log("Motion merge worker disabled because motion_capture.enabled is false in config.json")
        return None

    enabled = motion_config.get("merge_worker_enabled", True)
    queue_dir = os.path.join(
        config.get("global", {}).get("base_dir", DATA_DIR),
        motion_config.get("directory", "motion_events"),
    )

    if not enabled:
        log("Motion merge worker disabled in config.json")
        return None

    if os.path.exists(MOTION_MERGE_WORKER_PATH):
        log(f"Starting Motion Merge Worker: {MOTION_MERGE_WORKER_PATH}")
        global _MOTION_MERGE_LOG_HANDLE
        ensure_log_file(MOTION_MERGE_LOG_PATH)
        _MOTION_MERGE_LOG_HANDLE = open(MOTION_MERGE_LOG_PATH, "a")
        return subprocess.Popen(
            [
                "python3",
                MOTION_MERGE_WORKER_PATH,
                "--watch",
                "--queue-dir",
                queue_dir,
                "--poll-sec",
                "5",
            ],
            stdout=_MOTION_MERGE_LOG_HANDLE,
            stderr=_MOTION_MERGE_LOG_HANDLE,
        )

    log(f"ERROR: Motion merge worker missing at {MOTION_MERGE_WORKER_PATH}")
    return None

def terminate_process(proc, name):
    """Terminate a child process started by launcher."""
    if proc is None or proc.poll() is not None:
        return
    log(f"Stopping {name}...")
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        log(f"{name} did not stop cleanly; killing it")
        proc.kill()
    except Exception as e:
        log(f"ERROR stopping {name}: {e}")

def run_lora_time_request():
    """Run the lora time request script to sync time with supervisor."""
    TIME_REQUEST_PATH = "/home/pi/BEAMNode_Prototype2/scripts/lora/node_time_request.py"
    if os.path.exists(TIME_REQUEST_PATH):
        log(f"Running LoRa Time Request: {TIME_REQUEST_PATH}")
        result = subprocess.run(["/usr/bin/python3", TIME_REQUEST_PATH])
        if result.returncode == 0:
            log("LoRa Time Request completed successfully.")
        else:
            log(f"LoRa Time Request failed with exit code {result.returncode}.")
    else:
        log(f"ERROR: LoRa Time Request script missing at {TIME_REQUEST_PATH}")


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
    # wait for network - 10.42.0.30 connection to supervisor
    # only if LoRA not enabled, otherwise LoRA will be used for time sync and data transfer
    global_config = config["global"]

    lora_enabled = global_config.get("lora_enabled")

    if not lora_enabled:
        start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log(f"Startup time: {start}")
        log("Waiting for network connection to supervisor... (5 minutes max)")
        while True and (datetime.now() - datetime.strptime(start, "%Y-%m-%d %H:%M:%S")).seconds < 360:  # Only wait for the first 30 seconds of startup
            try:
                result = subprocess.run(
                    ["ping", "-c", "1", "-W", "1", "10.42.0.30"],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    log("Network connection to supervisor established.")
                    break
                else:
                    log("Still waiting for network connection to supervisor...")
            except Exception as e:
                log(f"Error occurred while checking network connection: {e}")
    elif lora_enabled:
        log("LoRa is enabled. Skipping network connection check and proceeding to time sync.")
        run_lora_time_request()


    log("Daily data move scheduled for 18:00 Eastern")

    # 1. REQUIREMENT: Run detect.py once on startup (log output)
    if os.path.exists(DETECT_PATH):
        log(f"Executing: {DETECT_PATH}")
        ensure_log_file(DETECT_LOG_PATH)
        with open(DETECT_LOG_PATH, "a") as detect_log:
            subprocess.run(
                ["/usr/bin/python3", DETECT_PATH],
                stdout=detect_log,
                stderr=detect_log,
                cwd=NODE_DIR,
            )

        # Reload config after detect to keep memory in sync
        try:
            with open(CONFIG_PATH, "r") as f:
                config = json.load(f)
            log("Reloaded config.json after detection.")
        except Exception as e:
            log(f"ERROR reloading config after detection: {e}")
    else:
        log(f"ERROR: File not found at {DETECT_PATH}")

    # 1b. REQUIREMENT: Start motion services on startup
    warn_if_motion_services_active()
    warn_if_legacy_low_power_services_active()
    merge_proc = start_motion_merge_worker_async()
    motion_proc = start_motion_trigger_async()
    low_power_proc = start_low_power_monitor_async()

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
            now = datetime.now(EASTERN_TZ)

            # A. Check Scheduler Health (Restart if Pi went down or process crashed)
            if sched_proc is None or sched_proc.poll() is not None:
                log("ALERT: Scheduler process stopped. Restarting...")
                time.sleep(5)
                sched_proc = start_scheduler_async()

            if motion_proc is not None and motion_proc.poll() is not None:
                log("ALERT: Motion trigger process stopped. Restarting...")
                time.sleep(5)
                motion_proc = start_motion_trigger_async()

            if merge_proc is not None and merge_proc.poll() is not None:
                log("ALERT: Motion merge worker stopped. Restarting...")
                time.sleep(5)
                merge_proc = start_motion_merge_worker_async()

            if low_power_proc is not None and low_power_proc.poll() is not None:
                log("ALERT: Low-power monitor stopped. Restarting...")
                time.sleep(5)
                low_power_proc = start_low_power_monitor_async()

            # B. REQUIREMENT: Run Shipping.py at 13:00 (disabled when LoRa is enabled)
            # We use a 30-second window to ensure the trigger catches
            if (not lora_enabled) and now.hour == 13 and now.minute == 0 and 0 <= now.second <= 30:
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

            # Sleep to keep CPU usage minimal
            time.sleep(10)

        except KeyboardInterrupt:
            log("Manual shutdown detected. Terminating launcher-managed processes...")
            terminate_process(low_power_proc, "low-power monitor")
            terminate_process(motion_proc, "motion trigger")
            terminate_process(merge_proc, "motion merge worker")
            terminate_process(sched_proc, "scheduler")
            break
        except Exception as e:
            log(f"Unexpected monitor error: {e}")
            time.sleep(10)
