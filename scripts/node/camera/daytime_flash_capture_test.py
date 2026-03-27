import json
import os
import time
from datetime import datetime, timezone

from gpiozero import OutputDevice
from picamera2 import Picamera2

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"
LUX_LOG_PATH = "/home/pi/data/tsl2591/lux_data.json"

# Set this high so the flash can trigger during daytime testing.
TEST_LUX_THRESHOLD = 100000


def get_latest_lux():
    try:
        with open(LUX_LOG_PATH, "r") as f:
            data = json.load(f)
        records = data.get("records", [])
        if records:
            return records[-1].get("lux")
    except Exception as e:
        print(f"[BEAM TEST] Lux read error: {e}")
    return None


with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

global_config = config.get("global", {})
cam_config = config.get("camera", {})

base_dir = global_config.get("base_dir", "/home/pi/data")
directory = os.path.join(base_dir, cam_config.get("directory", "camera"))
os.makedirs(directory, exist_ok=True)

flash_pin = cam_config.get("flash_gpio", 17)
flash = OutputDevice(flash_pin)

picam = Picamera2()
main_res = tuple(cam_config.get("resolution", [1920, 1080]))
camera_config = picam.create_still_configuration(main={"size": main_res})
picam.configure(camera_config)
picam.start()

print(f"[BEAM TEST] Flash GPIO: {flash_pin}")
print(f"[BEAM TEST] Output directory: {directory}")
print(f"[BEAM TEST] Daytime flash test threshold: {TEST_LUX_THRESHOLD}")
print("[BEAM TEST] Camera warming up...")
time.sleep(2)

lux = get_latest_lux()
print(f"[BEAM TEST] Latest lux: {lux}")

flash_was_used = False
if lux is None or lux < TEST_LUX_THRESHOLD:
    print("[BEAM TEST] Flash ON for test capture")
    flash.on()
    flash_was_used = True
    time.sleep(1)
else:
    print("[BEAM TEST] Flash skipped because lux is above test threshold")

timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
image_path = os.path.join(directory, f"daytime_flash_test_{timestamp}.jpg")

try:
    picam.capture_file(image_path)
    if os.path.exists(image_path):
        print(f"[BEAM TEST] Picture saved: {image_path}")
    else:
        print(f"[BEAM TEST] Capture finished but file not found: {image_path}")
finally:
    flash.off()
    picam.stop()

print(f"[BEAM TEST] Flash used: {flash_was_used}")
