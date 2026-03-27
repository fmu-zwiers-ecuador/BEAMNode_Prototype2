"""
BEAM Motion Camera System (Minimal Version)
"""

import os
import json
import time
from datetime import datetime, timezone
from gpiozero import MotionSensor, OutputDevice
from picamera2 import Picamera2

# ---------------------------------
# CONSTANT PATHS
# ---------------------------------
LUX_LOG_PATH = "/home/pi/data/tsl2591/lux_data.json"

# ---------------------------------
# Load configuration
# ---------------------------------
CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

global_config = config.get("global", {})
cam_config = config.get("camera", {})

# ---------------------------------
# Check if camera module enabled
# ---------------------------------
if not cam_config.get("enabled", False):
    print("[BEAM] Camera module disabled in config.")
    exit()

node_id = global_config.get("node_id", "unknown-node")
base_dir = global_config.get("base_dir", "/home/pi/data")

# ---------------------------------
# Directory setup
# ---------------------------------
directory = os.path.join(base_dir, cam_config.get("directory", "camera"))
os.makedirs(directory, exist_ok=True)

log_path = os.path.join(directory, "images_log.json")

# ---------------------------------
# GPIO Setup
# ---------------------------------
pir_pin = cam_config.get("gpio_pin", 4)
pir = MotionSensor(pir_pin)

# ----- Flash Setup -----
flash_enabled = cam_config.get("flash_enabled", False)
flash = None

if flash_enabled:
    flash_pin = cam_config.get("flash_gpio", 17)
    flash = OutputDevice(flash_pin)

# ---------------------------------
# Camera Setup
# ---------------------------------
picam = Picamera2()

main_res = tuple(cam_config.get("resolution", [1920, 1080]))

camera_config = picam.create_still_configuration(
    main={"size": main_res}
)

picam.configure(camera_config)
picam.start()

time.sleep(1)

if global_config.get("print_debug", True):
    print(f"[BEAM] Motion camera armed on GPIO {pir_pin}")
    print(f"[BEAM] Flash enabled: {flash_enabled}")
    print(f"[BEAM] Image directory: {directory}")
    print(f"[BEAM] Image log: {log_path}")

cooldown = cam_config.get("cooldown_sec", 1)

# ---------------------------------
# Read latest lux value
# ---------------------------------
def get_latest_lux():
    try:
        with open(LUX_LOG_PATH, "r") as f:
            data = json.load(f)

        if "records" in data and len(data["records"]) > 0:
            return data["records"][-1]["lux"]

    except Exception as e:
        print("[BEAM] Lux read error:", e)

    return None

# ---------------------------------
# Motion Detection Loop
# ---------------------------------
while True:

    # Wait for PIR motion
    pir.wait_for_motion()

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone()

    timestamp_iso = now_utc.isoformat()
    file_ts = now_utc.strftime("%Y%m%d_%H%M%SZ")
    file_prefix = cam_config.get("file_prefix", "motionpic_")

    image_path = os.path.join(directory, f"{file_prefix}{file_ts}.jpg")

    if global_config.get("print_debug", True):
        print("[BEAM] Motion detected")

    # ---------------------------------
    # Flash logic
    # ---------------------------------
    lux = get_latest_lux()
    flash_threshold = cam_config.get("flash_lux_threshold", 10)

    if flash_enabled and flash is not None:
        if lux is not None and lux < flash_threshold:
            flash.on()
            print(f"[BEAM] Night detected (lux={lux}) -> Flash ON")
        else:
            flash.off()

    # ---------------------------------
    # Capture image
    # ---------------------------------
    try:
        picam.capture_file(image_path)
        if os.path.exists(image_path):
            print(f"[BEAM] Picture saved: {image_path}")
        else:
            print(f"[BEAM] Capture finished but file not found: {image_path}")
    except Exception as e:
        print(f"[BEAM] Picture capture failed: {e}")
        continue

    # Turn flash off after capture
    if flash_enabled and flash is not None:
        flash.off()

    # ---------------------------------
    # Save log
    # ---------------------------------
    record = {
        "timestamp_utc": timestamp_iso,
        "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": now_local.tzname(),
        "file": image_path,
        "lux": lux
    }

    try:
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                try:
                    data = json.load(f)
                    if not isinstance(data, dict) or "records" not in data:
                        data = {"node_id": node_id, "sensor": "camera", "records": []}
                except:
                    data = {"node_id": node_id, "sensor": "camera", "records": []}
        else:
            data = {"node_id": node_id, "sensor": "camera", "records": []}

        data["records"].append(record)

        with open(log_path, "w") as f:
            json.dump(data, f, indent=4)

        print(f"[BEAM] Capture logged to: {log_path}")

    except Exception as e:
        print("[ERROR] Failed to save log:", e)

    # Wait for motion to stop + cooldown
    pir.wait_for_no_motion()
    time.sleep(cooldown)
