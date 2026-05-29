# Author: Jackson Roberts

import json
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import time

EASTERN_TZ = ZoneInfo("America/New_York")

# --- Hardware Library Imports ---
try:
    import board
    import adafruit_ahtx0
except ImportError:
    pass

# -----------------------------
# Configuration File Path and Loading
# -----------------------------
CONFIG_FILE = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

def get_config_data():
    """Load configuration from CONFIG_FILE, returning {} on error."""
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

# Load Configuration
full_config = get_config_data()
global_cfg = full_config.get("global", {})
aht_cfg = full_config.get("aht", {})

# Skip if disabled in config
if not aht_cfg.get("enabled", False):
    raise SystemExit(0)

NODE_ID = global_cfg.get("node_id", "beam-node-01")
SENSOR_NAME = "aht"

# Determine output path
base_dir = global_cfg.get("base_dir", "/home/pi/data")
sensor_dir = aht_cfg.get("directory", "aht")
file_name = aht_cfg.get("file_name", "aht_env.json")
file_path = os.path.join(base_dir, sensor_dir, file_name)

# --- Initialize variables ---
temperature = None
humidity = None

# -----------------------------
# Initialize the AHTx0 sensor and read values
# -----------------------------
try:
    i2c = board.I2C()
    sensor = adafruit_ahtx0.AHTx0(i2c)

    temperature = float(sensor.temperature)
    humidity = float(sensor.relative_humidity)
except Exception:
    raise SystemExit(0)

# -----------------------------
# MATCHED TIME CALCULATIONS (From BME280 Script)
# -----------------------------
now_local = datetime.now(EASTERN_TZ)

# -----------------------------
# MATCHED RECORD STRUCTURE
# -----------------------------
env_json_data = {
    "timestamp_eastern": now_local.isoformat(),
    "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
    "timezone": now_local.tzname(),                        # The specific zone name
    "temperature_C": temperature,
    "humidity_percent": humidity,
    "pressure_hPa": None                                   # AHTx0 has no pressure sensor
}

# -----------------------------
# Save to JSON file
# -----------------------------
try:
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    data = {}

    if os.path.exists(file_path):
        with open(file_path, "r") as json_file:
            try:
                data = json.load(json_file)
            except Exception:
                data = {}

    if not isinstance(data, dict):
        data = {}

    data["node_id"] = NODE_ID
    data["sensor"] = SENSOR_NAME

    if "records" not in data or not isinstance(data["records"], list):
        data["records"] = []

    data["records"].append(env_json_data)

    with open(file_path, "w") as json_file:
        json.dump(data, json_file, indent=4)

except Exception:
    pass
