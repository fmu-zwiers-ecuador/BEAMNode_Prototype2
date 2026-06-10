"""
log_lux_data.py: A simple script to log lux data to json.

This script logs the lux value along with the current timestamp into a json file, stored on the home/pi/data directory.

Author: Jaylen Small
Last Updated: 2-17-26 
"""

import board
import adafruit_tsl2591
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import time

EASTERN_TZ = ZoneInfo("America/New_York")

# Determine project root dynamically
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Load config
config_path = os.path.join(project_root, "config.json")
with open(config_path, "r") as f:
    config = json.load(f)

tsl_config = config["tsl2591"]
global_config = config["global"]

node_id = global_config.get("node_id", "unknown-node")

# Directory and file for logs
directory = os.path.join(global_config.get("base_dir", os.path.join(project_root, "data")), tsl_config.get("directory", "tsl2591"))
os.makedirs(directory, exist_ok=True)
file_name = tsl_config.get("file_name", "lux_data.json")
file_path = os.path.join(directory, file_name)

# Initialize sensor
i2c = board.I2C()
sensor = adafruit_tsl2591.TSL2591(i2c)

GAIN_OPTIONS = {
    "low": "GAIN_LOW",
    "medium": "GAIN_MED",
    "high": "GAIN_HIGH",
    "max": "GAIN_MAX",
}

INTEGRATION_OPTIONS = {
    "100ms": "INTEGRATIONTIME_100MS",
    "200ms": "INTEGRATIONTIME_200MS",
    "300ms": "INTEGRATIONTIME_300MS",
    "400ms": "INTEGRATIONTIME_400MS",
    "500ms": "INTEGRATIONTIME_500MS",
    "600ms": "INTEGRATIONTIME_600MS",
}

INTEGRATION_WAIT_SEC = {
    "100ms": 0.12,
    "200ms": 0.22,
    "300ms": 0.32,
    "400ms": 0.42,
    "500ms": 0.52,
    "600ms": 0.62,
}


def set_sensor_option(attribute, configured_value, options, default_key):
    configured_key = str(configured_value or default_key).lower()
    constant_name = options.get(configured_key, options[default_key])
    constant_value = getattr(adafruit_tsl2591, constant_name)
    setattr(sensor, attribute, constant_value)
    return configured_key if configured_key in options else default_key


def collect_raw_channels():
    channels = {}
    for output_name, attribute_name in (
        ("visible", "visible"),
        ("infrared", "infrared"),
        ("full_spectrum", "full_spectrum"),
    ):
        try:
            channels[output_name] = getattr(sensor, attribute_name)
        except Exception:
            pass
    return channels


def read_lux_with_overflow_retry():
    gain_name = set_sensor_option(
        "gain",
        tsl_config.get("gain", "low"),
        GAIN_OPTIONS,
        "low",
    )
    integration_name = set_sensor_option(
        "integration_time",
        tsl_config.get("integration_time", "100ms"),
        INTEGRATION_OPTIONS,
        "100ms",
    )
    time.sleep(INTEGRATION_WAIT_SEC.get(integration_name, 0.12))

    try:
        return sensor.lux, "ok", gain_name, integration_name, collect_raw_channels()
    except RuntimeError as e:
        if "Overflow reading light channels" not in str(e):
            raise

        sensor.gain = adafruit_tsl2591.GAIN_LOW
        sensor.integration_time = adafruit_tsl2591.INTEGRATIONTIME_100MS
        time.sleep(INTEGRATION_WAIT_SEC["100ms"])
        try:
            return sensor.lux, "ok_after_overflow_retry", "low", "100ms", collect_raw_channels()
        except RuntimeError as retry_error:
            if "Overflow reading light channels" not in str(retry_error):
                raise
            return None, "overflow", "low", "100ms", collect_raw_channels()


# Read lux
lux, read_status, gain_name, integration_name, raw_channels = read_lux_with_overflow_retry()

# --- TIME CALCULATIONS ---
now_local = datetime.now(EASTERN_TZ)

# New record with synchronized time fields
new_lux_data = {
    "timestamp_eastern": now_local.isoformat(),
    "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
    "timezone": now_local.tzname(),
    "lux": lux,
    "status": read_status,
    "gain": gain_name,
    "integration_time": integration_name,
    "raw_channels": raw_channels
}

# Append to JSON
try:
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            try:
                data = json.load(f)
                if not isinstance(data, dict) or "records" not in data:
                    data = {"node_id": node_id, "sensor": "tsl2591", "records": []}
            except Exception:
                data = {"node_id": node_id, "sensor": "tsl2591", "records": []}
    else:
        data = {"node_id": node_id, "sensor": "tsl2591", "records": []}

    data["records"].append(new_lux_data)

    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)

    if global_config.get("print_debug", True):
        print(
            f"Lux data appended to {file_name} at "
            f"{now_local.strftime('%Y-%m-%d %H:%M:%S')} {now_local.tzname()} "
            f"(status={read_status}, lux={lux})"
        )
except Exception as e:
    print(f"Error saving lux data: {e}")
