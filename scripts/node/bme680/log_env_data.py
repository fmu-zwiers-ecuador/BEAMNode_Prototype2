import json
from datetime import datetime, timezone
import os
import board
import busio
import adafruit_bme680

# Determine project root dynamically
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Load config
config_path = os.path.join(project_root, "config.json")
with open(config_path, "r") as f:
    config = json.load(f)

# Update config keys to bme680
bme_config = config.get("bme680", {})
global_config = config.get("global", {})

# Check if sensor is enabled
if not bme_config.get("enabled", True):
    exit(0)

node_id = global_config.get("node_id", "unknown-node")

# --- I2C INITIALIZATION ---
# Using I2C instead of SPI as requested
i2c = board.I2C()  # uses board.SCL and board.SDA

try:
    # Initialize BME680 at address 0x77
    sensor = adafruit_bme680.Adafruit_BME680_I2C(i2c, address=0x77)
    
    # Optional: Set sea level pressure for altitude accuracy
    sensor.sea_level_pressure = bme_config.get("sea_level_pressure", 1013.25)
except Exception as e:
    print(f"Failed to initialize BME680: {e}")
    exit(1)

# Read values
temperature = float(sensor.temperature)
humidity = float(sensor.relative_humidity)
pressure = float(sensor.pressure)
gas = float(sensor.gas)  # Resistance in Ohms (Air Quality indicator)
altitude = float(sensor.altitude)

# Directory and file for logs
base_dir = global_config.get("base_dir", os.path.join(project_root, "data"))
directory = os.path.join(base_dir, bme_config.get("directory", "bme680"))
os.makedirs(directory, exist_ok=True)

file_name = bme_config.get("file_name", "env_data.json")
file_path = os.path.join(directory, file_name)

# --- TIME CALCULATIONS ---
now_utc = datetime.now(timezone.utc)
now_local = now_utc.astimezone() 

# New record with BME680 specific data
env_json_data = {
    "timestamp_utc": now_utc.isoformat(),
    "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
    "timezone": str(now_local.tzname()),
    "temperature_C": round(temperature, 2),
    "humidity_percent": round(humidity, 2),
    "pressure_hPa": round(pressure, 2),
    "gas_resistance_ohm": gas,
    "altitude_m": round(altitude, 2)
}

# Append to JSON
try:
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            try:
                data = json.load(f)
                if not isinstance(data, dict) or "records" not in data:
                    data = {"node_id": node_id, "sensor": "bme680", "records": []}
            except Exception:
                data = {"node_id": node_id, "sensor": "bme680", "records": []}
    else:
        data = {"node_id": node_id, "sensor": "bme680", "records": []}

    data["records"].append(env_json_data)

    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)

except Exception as e:
    print(f"Error writing to file: {e}")
