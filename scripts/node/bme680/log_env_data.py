# BME680 Logging Script
# Author: Jackson Roberts
# Date of Completetion: 2/23/2026


import json
import os
import board
import adafruit_bme680
from datetime import datetime, timezone

# 1. Path Setup
# Absolute path ensures the scheduler can run this from any working directory
CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

def log_data():
    # Load Configuration
    try:
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
    except Exception as e:
        print(f"Config Load Error: {e}")
        exit(1)

    bme_config = config.get("bme680", {})
    global_config = config.get("global", {})
    
    if not bme_config.get("enabled", True):
        print("BME680 is disabled in config.")
        return

    # 2. Hardware Initialization
    try:
        i2c = board.I2C()
        # Convert hex string (e.g., "0x77") to integer
        addr = int(bme_config.get("address_hex", "0x77"), 16)
        sensor = adafruit_bme680.Adafruit_BME680_I2C(i2c, address=addr)
        
        # Burn-in: Gas readings require a dummy read to warm the heater
        _ = sensor.gas
    except Exception as e:
        print(f"Hardware Initialization Error: {e}")
        exit(1)

    # 3. Data Collection
    now_utc = datetime.now(timezone.utc)
    # Mapping to your config's record_schema
    data_point = {
        "timestamp": now_utc.isoformat(),
        "temperature_C": round(sensor.temperature, 2),
        "humidity_percent": round(sensor.relative_humidity, 2),
        "pressure_hPa": round(sensor.pressure, 2),
        "gas_resistance_ohms": float(sensor.gas)
    }

    # 4. File Path Management
    # Target: /home/pi/data/bme680/bme680_env.json
    base_dir = global_config.get("base_dir", "/home/pi/data")
    sensor_dir = os.path.join(base_dir, bme_config.get("directory", "bme680"))
    os.makedirs(sensor_dir, exist_ok=True)
    
    file_path = os.path.join(sensor_dir, bme_config.get("file_name", "bme680_env.json"))

    # 5. Persistent Logging (Append to JSON)
    try:
        node_id = global_config.get("node_id", "unknown-node")
        
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                try:
                    full_data = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    # Handle corrupted or empty files
                    full_data = {"node_id": node_id, "sensor": "bme680", "records": []}
        else:
            full_data = {"node_id": node_id, "sensor": "bme680", "records": []}

        # Append new reading to records list
        full_data["records"].append(data_point)

        # Atomic-style write
        with open(file_path, "w") as f:
            json.dump(full_data, f, indent=4)
        
        print(f"Success: Data logged to {file_path}")

    except Exception as e:
        print(f"File Operations Error: {e}")
        exit(1)

if __name__ == "__main__":
    log_data()
