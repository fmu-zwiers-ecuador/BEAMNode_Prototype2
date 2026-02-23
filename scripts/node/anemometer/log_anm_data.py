import RPi.GPIO as GPIO
import time
import threading
import json
import os
from datetime import datetime, timezone

# --- CONFIG & INITIALIZATION ---
# Determine project root dynamically to match BME280 script style
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Load config
config_path = os.path.join(project_root, "config.json")
try:
    with open(config_path, "r") as f:
        config = json.load(f)
except Exception as e:
    print(f"Error loading config: {e}")
    exit(1)

anm_config = config.get("anemometer", {})
global_config = config.get("global", {})

# Check if sensor is enabled
if not anm_config.get("enabled", True):
    exit(0)

node_id = global_config.get("node_id", "unknown-node")
ANEMOMETER_PIN = anm_config.get("pin", 17)
MS_PER_HZ = anm_config.get("ms_per_hz", 0.6667)
SAMPLE_WINDOW = anm_config.get("sample_window", 2)

# Global for interrupt handling
pulse_count = 0
lock = threading.Lock()

def count_pulse(channel):
    global pulse_count
    with lock:
        pulse_count += 1

# --- GPIO SETUP ---
GPIO.setmode(GPIO.BCM)
GPIO.setup(ANEMOMETER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(ANEMOMETER_PIN, GPIO.FALLING, callback=count_pulse, bouncetime=5)

# --- DIRECTORY & FILE SETUP ---
base_dir = global_config.get("base_dir", os.path.join(project_root, "data"))
directory = os.path.join(base_dir, anm_config.get("directory", "anemometer"))
os.makedirs(directory, exist_ok=True)

file_name = anm_config.get("file_name", "wind_data.json")
file_path = os.path.join(directory, file_name)

if global_config.get("print_debug"):
    print(f"[{node_id}] Measuring wind speed... Logging to {file_path}")

# --- MAIN LOOP ---
try:
    while True:
        # Reset count for the start of the sample window
        with lock:
            pulse_count = 0

        time.sleep(SAMPLE_WINDOW)

        # Grab count from the window
        with lock:
            count = pulse_count
        
        # Physics Calculations
        frequency = count / SAMPLE_WINDOW
        wind_speed_ms = round(frequency * MS_PER_HZ, 4)
        wind_speed_kmh = round(wind_speed_ms * 3.6, 4)

        # Time Calculations (Matches BME280 script)
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone() 

        anm_json_data = {
            "timestamp_utc": now_utc.isoformat(),
            "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": str(now_local.tzname()),
            "wind_speed_ms": wind_speed_ms,
            "wind_speed_kmh": wind_speed_kmh,
            "pulses": count,
            "sample_window_s": SAMPLE_WINDOW
        }

        # --- ATOMIC DATA PERSISTENCE ---
        try:
            # Prepare data structure
            data = {"node_id": node_id, "sensor": "anemometer", "records": []}
            
            # Load existing records if file exists
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    try:
                        existing_content = json.load(f)
                        if isinstance(existing_content, dict) and "records" in existing_content:
                            data = existing_content
                    except json.JSONDecodeError:
                        pass # Start fresh if file is corrupted

            # Append new reading
            data["records"].append(anm_json_data)

            # Atomic write: Save to temp file then rename (prevents corruption)
            tmp_path = file_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=4)
            os.replace(tmp_path, file_path)

            if global_config.get("print_debug"):
                print(f"Logged: {wind_speed_ms} m/s | {wind_speed_kmh} km/h")

        except Exception as e:
            if global_config.get("print_debug"):
                print(f"File write error: {e}")
            pass

except KeyboardInterrupt:
    if global_config.get("print_debug"):
        print("\nShutting down anemometer logger...")
    GPIO.remove_event_detect(ANEMOMETER_PIN)
    GPIO.cleanup()
