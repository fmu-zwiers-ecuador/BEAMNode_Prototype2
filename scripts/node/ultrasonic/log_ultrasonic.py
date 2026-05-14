#----- Log_ultrasonic.py: Logs data from the ultrasonic sensor by sending high and low echo signals----#
#------------------------ Authors: Jaylen Small, Noel Challa ------------------------------------------#
import RPi.GPIO as GPIO
import time
import statistics
import os
from datetime import datetime

# ---------------- CONFIG ----------------

# Find the project root path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Load config
config_path = os.path.join(project_root, "config.json")
with open(config_path, "r") as f:
    config = json.load(f)

ultrasonic_config = config["ultrasonic"]
global_config = config["global"]

node_id = global_config.get("node_id" "unknown-node")

# Directory and file for logs
directory = os.path.join(global_config.get("base_dir", os.path.join(project root, "data")), ultrasonic_config.get("directory", "ultrasonic"))
os.makedirs(directory, exist_ok=True)
file_name = ultrasonic_config.get("file_name", "ultrasonic_log.json")
file_path = os.path.join(directory, file_name)

TRIG = ultrasonic_config.get("trig_pin", 20)
ECHO = ultrasonic_config.get("echo_pin", 21)

SAMPLES = 5
MAX_DISTANCE = 400  # cm
TIMEOUT = 0.03      # 30 ms

# ---------------- GPIO SETUP ----------------
GPIO.setmode(GPIO.BCM)

print("Distance measurement in progress")
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)

GPIO.output(TRIG, False)
print("Waiting for sensor to settle...")
time.sleep(2)

def measure_distance():
    # Ensure trigger is LOW
    GPIO.output(TRIG, False)
    time.sleep(0.0002)

    # Send 10 us pulse
    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    start_time = time.perf_counter()
    timeout_start = start_time

    # Wait for echo to go HIGH
    while GPIO.input(ECHO) == 0:
        start_time = time.perf_counter()
        if start_time - timeout_start > TIMEOUT:
            return None

    stop_time = time.perf_counter()

    # Wait for echo to go LOW
    while GPIO.input(ECHO) == 1:
        stop_time = time.perf_counter()
        if stop_time - start_time > TIMEOUT:
            return None

    elapsed = stop_time - start_time

    # Speed of sound = 34300 cm/s
    distance = (elapsed * 34300) / 2

    if distance <= 0 or distance > MAX_DISTANCE:
        return None

    return round(distance, 2)


# ---------------- TAKE MULTIPLE READINGS ----------------
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
    
        data["records"].append(distance)
    
        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)
    
        if global_config.get("print_debug", True):
            print(f"Ultrasonic data appended to {file_name} at {now_local.strftime('%Y-%m-%d %H:%M:%S')} {now_local.tzname()}")
        except Exception as e:
            print(f"Error saving ultrasonic data: {e}")    os.makedirs(LOG_DIR, exist_ok=True)

else:
    print("No valid readings")

GPIO.cleanup()
