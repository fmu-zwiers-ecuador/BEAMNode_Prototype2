"""
Motion-Triggered Camera Logger for BEAM
Updated: Added local timestamp and timezone logging for consistency.
Authors: Gabriel Gonzalez, Raiz Mohammed, and Jackson Roberts
"""

import os
import json
import time
from datetime import datetime, timezone
from gpiozero import MotionSensor
from picamera2 import Picamera2

# -----------------------------
# Load configuration
# -----------------------------
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
config_path = os.path.join(project_root, "config.json")

with open(config_path, "r") as f:
    config = json.load(f)

global_config = config.get("global", {})
cam_config = config.get("camera", {})

node_id = global_config.get("node_id", "unknown-node")
base_dir = global_config.get("base_dir", os.path.join(project_root, "data"))

# -----------------------------
# Directory setup
# -----------------------------
directory = os.path.join(base_dir, cam_config.get("directory", "camera"))
os.makedirs(directory, exist_ok=True)
log_path = os.path.join(directory, "images_log.json")

# -----------------------------
# Motion sensor and camera setup
# -----------------------------
gpio_pin = cam_config.get("gpio_pin", 4)
pir = MotionSensor(gpio_pin)

picam = Picamera2()
main_res = tuple(cam_config.get("resolution", [1920, 1080]))
lores_res = tuple(cam_config.get("lores_resolution", [640, 480]))
camera_config = picam.create_still_configuration(
    main={"size": main_res}, lores={"size": lores_res}, display="lores"
)
picam.configure(camera_config)
picam.start()
time.sleep(1)  # allow sensor to warm up

if global_config.get("print_debug", True):
    print(f"[BEAM] Motion camera armed on GPIO {gpio_pin}, waiting for movement...")

cooldown = cam_config.get("cooldown_sec", 1)

# -----------------------------
# Motion detection loop
# -----------------------------
while True:
    pir.wait_for_motion()
    
    # --- UPDATED TIME CALCULATIONS ---
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone()
    
    timestamp_iso = now_utc.isoformat()
    # Sanitize timestamp for filename (remove colons/dots for better OS compatibility)
    file_ts = now_utc.strftime("%Y%m%d_%H%M%SZ")

    filename = f"{cam_config.get('file_prefix', 'motionpic_')}{file_ts}.jpg"
    file_path = os.path.join(directory, filename)

    if global_config.get("print_debug", True):
        print(f"[BEAM] Motion detected — capturing {filename}")

    # Capture image
    picam.capture_file(file_path)

    # --- UPDATED RECORD STRUCTURE ---
    record = {
        "timestamp_utc": timestamp_iso,
        "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": now_local.tzname(),
        "file": file_path
    }

    # Append to log JSON
    try:
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                try:
                    data = json.load(f)
                    if not isinstance(data, dict) or "records" not in data:
                        data = {"node_id": node_id, "sensor": "camera", "records": []}
                except Exception:
                    data = {"node_id": node_id, "sensor": "camera", "records": []}
        else:
            data = {"node_id": node_id, "sensor": "camera", "records": []}

        data["records"].append(record)
        with open(log_path, "w") as f:
            json.dump(data, f, indent=4)

    except Exception as e:
        if global_config.get("print_debug", True):
            print(f"[ERROR] Failed to save image log: {e}")

    time.sleep(cooldown)
