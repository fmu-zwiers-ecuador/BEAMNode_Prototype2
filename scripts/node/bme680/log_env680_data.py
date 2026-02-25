import json
import os
import time
import board
import adafruit_bme680
from datetime import datetime, timezone

# Absolute path to the config file updated by detect.py
CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

def log_data():
    # 1. Load Configuration
    try:
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
    except Exception:
        exit(1)

    bme_config = config.get("bme680", {})
    global_config = config.get("global", {})
    
    # 2. Check Detect.py status
    # If enabled is False or address is null, exit gracefully
    if not bme_config.get("enabled", False) or bme_config.get("address_hex") is None:
        return

    # 3. Hardware Initialization
    try:
        i2c = board.I2C() 
        
        # Pull address from config (e.g., "0x77") and convert to integer
        addr_hex = bme_config.get("address_hex")
        addr = int(addr_hex, 16)
        
        sensor = adafruit_bme680.Adafruit_BME680_I2C(i2c, address=addr)
        
        # --- GAS SENSOR BURN-IN ---
        # Trigger heater with a dummy read
        _ = sensor.gas 
        # Wait 2 seconds for metal oxide heater stabilization
        time.sleep(2) 
        # -------------------------
        
    except Exception as e:
        # If hardware was detected earlier but is now missing/unplugged
        exit(1)

    # 4. Collect Data
    now_utc = datetime.now(timezone.utc)
    data_point = {
        "timestamp": now_utc.isoformat(),
        "temperature_C": round(sensor.temperature, 2),
        "humidity_percent": round(sensor.relative_humidity, 2),
        "pressure_hPa": round(sensor.pressure, 2),
        "gas_resistance_ohms": float(sensor.gas)
    }

    # 5. File Path & Directory Management
    base_dir = global_config.get("base_dir", "/home/pi/data")
    sensor_dir = os.path.join(base_dir, bme_config.get("directory", "bme680"))
    os.makedirs(sensor_dir, exist_ok=True)
    
    file_path = os.path.join(sensor_dir, bme_config.get("file_name", "bme680_env.json"))

    # 6. Persistent JSON Logging
    try:
        node_id = global_config.get("node_id", "unknown-node")
        
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                try:
                    full_data = json.load(f)
                except:
                    full_data = {"node_id": node_id, "sensor": "bme680", "records": []}
        else:
            full_data = {"node_id": node_id, "sensor": "bme680", "records": []}

        full_data["records"].append(data_point)

        with open(file_path, "w") as f:
            json.dump(full_data, f, indent=4)
            
    except Exception:
        exit(1)

if __name__ == "__main__":
    log_data()
