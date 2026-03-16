"""
BEAM Motion Camera + Pixel Tracking System
"""

import os
import json
import time
import cv2
from datetime import datetime, timezone
from gpiozero import MotionSensor, Servo, OutputDevice
from picamera2 import Picamera2

# ---------------------------------
# CONSTANT PATHS
# ---------------------------------
LUX_LOG_PATH = "/home/pi/data/tsl2591/lux_data.json"

# ---------------------------------
# Load configuration
# ---------------------------------
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
config_path = os.path.join(project_root, "config.json")

with open(config_path, "r") as f:
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
base_dir = global_config.get("base_dir", os.path.join(project_root, "data"))

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

# ----- Servo Setup -----
servo_enabled = cam_config.get("servo_enabled", False)
servo = None

if servo_enabled:
    servo_pin = cam_config.get("servo_gpio", 18)
    servo = Servo(servo_pin)

# ---------------------------------
# Camera Setup
# ---------------------------------
picam = Picamera2()

main_res = tuple(cam_config.get("resolution", [1920, 1080]))
lores_res = tuple(cam_config.get("lores_resolution", [640, 480]))

camera_config = picam.create_still_configuration(
    main={"size": main_res},
    lores={"size": lores_res},
    display="lores"
)

picam.configure(camera_config)
picam.start()

time.sleep(1)

if global_config.get("print_debug", True):
    print(f"[BEAM] Motion camera armed on GPIO {pir_pin}")
    print(f"[BEAM] Flash enabled: {flash_enabled}")
    print(f"[BEAM] Servo enabled: {servo_enabled}")

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
# Detect motion between frames
# ---------------------------------
def detect_motion(image1, image2):

    img1 = cv2.imread(image1)
    img2 = cv2.imread(image2)

    diff = cv2.absdiff(img1, img2)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)

    _, thresh = cv2.threshold(gray, 25, 255, cv2.THRESH_BINARY)

    contours, _ = cv2.findContours(
        thresh,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    largest_box = None
    max_area = 0

    for c in contours:

        area = cv2.contourArea(c)

        if area > 500 and area > max_area:
            x, y, w, h = cv2.boundingRect(c)
            largest_box = (x, y, w, h)
            max_area = area

    if largest_box is not None:

        x, y, w, h = largest_box

        cv2.rectangle(
            img2,
            (x, y),
            (x + w, y + h),
            (0, 255, 0),
            2
        )

        cv2.imwrite(image2, img2)

        return largest_box, img2.shape[1]

    return None, None

# ---------------------------------
# Servo Tracking
# ---------------------------------
def track_object(box, frame_width):

    if not servo_enabled or servo is None:
        return

    x, y, w, h = box

    center_x = x + w/2
    frame_center = frame_width / 2

    offset = center_x - frame_center

    if offset > 50:
        servo.value = 0.5

    elif offset < -50:
        servo.value = -0.5

    else:
        servo.value = 0

# ---------------------------------
# Motion Detection Loop
# ---------------------------------
while True:

    pir.wait_for_motion()

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone()

    timestamp_iso = now_utc.isoformat()
    file_ts = now_utc.strftime("%Y%m%d_%H%M%SZ")

    frame1 = os.path.join(directory, f"frame1_{file_ts}.jpg")
    frame2 = os.path.join(directory, f"frame2_{file_ts}.jpg")

    final_image = os.path.join(directory, f"motionpic_{file_ts}.jpg")

    if global_config.get("print_debug", True):
        print("[BEAM] Motion detected")

    lux = get_latest_lux()
    flash_threshold = cam_config.get("flash_lux_threshold", 10)

    if flash_enabled and flash is not None:

        if lux is not None and lux < flash_threshold:
            flash.on()

            if global_config.get("print_debug", True):
                print(f"[BEAM] Night detected (lux={lux}) -> Flash ON")
        else:
            flash.off()

    # Capture frames
    picam.capture_file(frame1)
    time.sleep(0.2)
    picam.capture_file(frame2)

    # Turn flash off after capture
    if flash_enabled and flash is not None:
        flash.off()

    box, frame_width = detect_motion(frame1, frame2)

    if box:
        track_object(box, frame_width)
        os.rename(frame2, final_image)
    else:
        final_image = frame2

    record = {
        "timestamp_utc": timestamp_iso,
        "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": now_local.tzname(),
        "file": final_image,
        "lux": lux
    }

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
            print("[ERROR] Failed to save log:", e)

    pir.wait_for_no_motion()
    time.sleep(cooldown)