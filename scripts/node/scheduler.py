############## SCHEDULER.PY - BEAM PROJECT - GABRIEL GONZALEZ - Jackson Roberts - OCT 2025 ##################
## This script should schedule times for data collection from all sensors: AHT, Audio,   ##
## BME280, and TSL2591.                                                                  ##
###########################################################################################


import json
import os
import subprocess
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

EASTERN_TZ = ZoneInfo("America/New_York")

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"
NODE_DIR = "/home/pi/BEAMNode_Prototype2/scripts/node/"
LOG_FILE = "/home/pi/logs/scheduler.log"

FILE_NAMES = {

}

# Atlas sensor scripts are grouped under a shared folder.
SCRIPT_DIR_OVERRIDES = {
    "atlas_ec": "atlas_sci",
    "atlas_orp": "atlas_sci",
    "atlas_rtd": "atlas_sci",
    "atlas_do": "atlas_sci",
    "atlas_ph": "atlas_sci"
}

SUDO_SENSORS = {"atlas_ec", "atlas_orp", "atlas_rtd", "atlas_do", "atlas_ph", "audio"}

# log funciton
def log(msg):
    ts = datetime.now(EASTERN_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] {msg}"
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    print(line)

# create /home/pi/data if it doesn't exist
data_dir = "/home/pi/data"
os.makedirs(data_dir, exist_ok=True)

# create /home/pi/shipping if it doesn't exist
shipping_dir = "/home/pi/shipping"
os.makedirs(shipping_dir, exist_ok=True)

# Track last run times
last_run_times = {}

# current time
current_time = datetime.now(EASTERN_TZ)

#beginning of next hour
start_time = (current_time + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

def load_config():
    """Load frequency configuration for all sensors."""
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def find_sensor_script(sensor):
    """Find the Python script inside each sensor’s directory."""
    config = load_config()
    params = config.get(sensor)
    if not isinstance(params, dict):
        log(f"[WARN] Invalid config for sensor '{sensor}'")
        return None

    script_name = params.get("script_name")
    if not script_name:
        log(f"[WARN] Missing script_name for sensor '{sensor}'")
        return None

    dir_name = SCRIPT_DIR_OVERRIDES.get(sensor, sensor)
    sensor_dir = os.path.join(NODE_DIR, dir_name)
    if not os.path.isdir(sensor_dir):
        log(f"[WARN] Directory not found for sensor '{sensor}' ({sensor_dir})")
        return None

    full_path = os.path.join(sensor_dir, script_name)

    if os.path.isfile(full_path):
        return full_path
    
    log(f"[WARN] No .py script found in '{sensor_dir}'")
    return None

def run_sensor_once(sensor):
    """Run the sensor’s Python script once."""
    script_path = find_sensor_script(sensor)
    if not script_path:
        return

    log(f"[INFO] Running {sensor} at {datetime.now(EASTERN_TZ).strftime('%H:%M:%S %Z')}")
    cmd = ["python3", script_path]
    if sensor in SUDO_SENSORS:
        cmd = ["sudo", "python3", script_path]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    # You can log result.stdout/result.stderr here if needed
    if result.returncode == 0:
        log(f"[INFO] {sensor} finished successfully.")
    else:
        log(f"[ERROR] {sensor} exited with code {result.returncode}")
        log(result.stderr)



def scheduler_loop():
    log("[INFO] Frequency-based Scheduler started")
    config = load_config()

    # Initialize last run times to "now" so they don't all start instantly
    now = datetime.now(EASTERN_TZ)
    for sensor in config.keys():
        last_run_times[sensor] = now

    while True:
        now = datetime.now(EASTERN_TZ)
        config = load_config()  # reload in case user updates config.json

        for sensor, params in config.items():
            freq_min = params.get("frequency")
            enabled = params.get("enabled", True)
            if freq_min is None or not enabled:
                continue  # skip if no frequency defined
            last_run = last_run_times.get(sensor)
            next_run = last_run + timedelta(minutes=freq_min)

            if now >= next_run:
                run_sensor_once(sensor)
                last_run_times[sensor] = datetime.now(EASTERN_TZ)
            time.sleep(1)  # brief sleep to avoid tight loop
    
                        
        time.sleep(5)  # check every 5 seconds

if __name__ == "__main__":
    try:
        # wait until start time
        log(f"[INFO] Scheduler will start at {start_time.strftime('%H:%M:%S')}")
        while datetime.now(EASTERN_TZ) < start_time:
            time.sleep(1)
        scheduler_loop()
    except KeyboardInterrupt:
        log("\n[INFO] Scheduler shutting down gracefully.")
