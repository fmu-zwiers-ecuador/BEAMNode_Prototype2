#----- Log_ultrasonic.py: Logs data from the ultrasonic sensor by sending high and low echo signals----#
#------------------------ Authors: Jaylen Small, Noel Challa ------------------------------------------#
import RPi.GPIO as GPIO
import time
import statistics
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN_TZ = ZoneInfo("America/New_York")

# --- Config variables ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")) # Find the project root path

# Load config
config_path = os.path.join(project_root, "config.json")
with open(config_path, "r") as f:
    config = json.load(f)

ultrasonic_config = config["ultrasonic"]
global_config = config["global"]

node_id = global_config.get("node_id" "unknown-node")

# Directory and file for logs
directory = os.path.join(global_config.get("base_dir", os.path.join(project_root, "data")), ultrasonic_config.get("directory", "ultrasonic"))
os.makedirs(directory, exist_ok=True)
file_name = ultrasonic_config.get("file_name", "ultrasonic_log.json")
file_path = os.path.join(directory, file_name)

TRIG = ultrasonic_config.get("trig_pin", 20)
ECHO = ultrasonic_config.get("echo_pin", 21)

SAMPLES = 5
MAX_DISTANCE = 400  # Measured in cm
TIMEOUT = 0.03 # Default is 30 ms
# -------------------------


# --- GPIO setup ---
GPIO.setmode(GPIO.BCM)

print("Distance measurement in progress")
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)

GPIO.output(TRIG, False)
print("Waiting for sensor to settle...")
time.sleep(2)
# ------------------


def measure_distance():
    """
    This method measures the distance between the ultrasonic sensor and the surface.

    Only takes one reading at a time.
    """
    # Stableizes the trigger pin to LOW
    GPIO.output(TRIG, False)
    time.sleep(0.0002)

    # Sends the ultrasonic pulse
    GPIO.output(TRIG, True)
    time.sleep(0.00001) # 10 microseconds
    GPIO.output(TRIG, False)

    start_time = time.perf_counter() # Captures when the echo pin goes HIGH, in other words, when the pulse returns
    timeout_start = start_time # A timeout to keep the script from stalling

    # --- Time the echo ---
    while GPIO.input(ECHO) == 0: # Wait for echo to go HIGH
        start_time = time.perf_counter()
        if start_time - timeout_start > TIMEOUT: 
            return None

    stop_time = time.perf_counter() # Captures when the echo pin goes LOW again, in ther words, when the echo ends

    while GPIO.input(ECHO) == 1: # Wait for echo to go LOW
        stop_time = time.perf_counter()
        if stop_time - start_time > TIMEOUT:
            return None

    # Converts time into distance
    elapsed = stop_time - start_time
    distance = (elapsed * 34300) / 2 # Speed of sound = 34300 cm/s

    # Filters out impossible data readings or out-of-range values
    if distance <= 0 or distance > MAX_DISTANCE:
        return None

    return round(distance, 2)


# --- Take multiple readings ---
readings = []
counter = 1 # Keeps track of how many readings have been taken

for _ in range(SAMPLES):
    d = measure_distance()

    if d is not None:
        print(f"Distance {counter}: {d} cm")
        readings.append(d)
    else:
        print(f"Reading {counter} has been skipped because of invalid data")

    counter += 1
    time.sleep(0.05)

if readings:
    distance = round(statistics.median(readings), 2)

    print(f"Average distance: {distance} cm")
    
    
    # -------- LOGGING --------
    # Time calculation variables
    now_local = datetime.now(EASTERN_TZ)
    
    new_ultrasonic_data = {
        "distance": distance,
        "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": now_local.tzname()
    }

    # Append to JSON
    try:
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                try:
                    data = json.load(f)
                    if not isinstance(data, dict) or "records" not in data:
                        data = {"node_id": node_id, "sensor": "ultrasonic", "records": []}
                except Exception:
                    data = {"node_id": node_id, "sensor": "ultrasonic", "records": []}
        else:
            data = {"node_id": node_id, "sensor": "ultrasonic", "records": []}
    
        data["records"].append(new_ultrasonic_data)
    
        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)
    
        if global_config.get("print_debug", True):
            print(f"Ultrasonic data appended to {file_name} at {now_local.strftime('%Y-%m-%d %H:%M:%S')} {now_local.tzname()}")
    except Exception as e:
        print(f"Error saving ultrasonic data: {e}")

else:
    print("No valid readings")

GPIO.cleanup()
