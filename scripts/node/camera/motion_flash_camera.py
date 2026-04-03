"""
BEAM Motion Camera System (Minimal Version)
"""

import os
import json
import time
from datetime import datetime, timezone
from gpiozero import Device, MotionSensor, OutputDevice
from gpiozero.exc import BadPinFactory
from picamera2 import Picamera2

try:
    from gpiozero.pins.rpigpio import RPiGPIOFactory

    Device.pin_factory = RPiGPIOFactory()
except Exception:
    # Fall back to gpiozero's default pin factory if RPiGPIO is unavailable.
    pass

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
pir_pin = cam_config.get("pir_gpio", cam_config.get("gpio_pin", 4))
try:
    pir = MotionSensor(
        pir_pin,
        # PIR modules drive the signal pin themselves, so don't force an
        # internal pull resistor here.
        pull_up=None,
        active_state=True,
        queue_len=1,
        sample_rate=10,
        threshold=0.5,
    )
except (BadPinFactory, Exception) as e:
    print(f"[BEAM] PIR setup failed on GPIO {pir_pin}: {e}")
    raise

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
    print("[BEAM] Warming up PIR...")

cooldown = cam_config.get("cooldown_sec", 1)
pir_warmup = cam_config.get("pir_warmup_sec", 5)
poll_interval = cam_config.get("pir_poll_interval_sec", 0.1)
time.sleep(pir_warmup)

if global_config.get("print_debug", True):
    print("[BEAM] PIR ready")

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
last_motion_state = pir.motion_detected

while True:
    current_motion_state = pir.motion_detected

    if current_motion_state and not last_motion_state:

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
            last_motion_state = current_motion_state
            time.sleep(poll_interval)
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

        time.sleep(cooldown)

    elif not current_motion_state and last_motion_state and global_config.get("print_debug", True):
        print("[BEAM] Motion ended")

    last_motion_state = current_motion_state
    time.sleep(poll_interval)
