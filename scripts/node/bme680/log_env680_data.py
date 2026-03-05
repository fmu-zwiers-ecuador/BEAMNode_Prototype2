import json
import os
import time
import board
import busio
import adafruit_bme680
from datetime import datetime, timezone
import sys

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

def log_data():
    # 1. Load Configuration
    try:
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
    except Exception as e:
        print(f"Config Load Error: {e}", file=sys.stderr)
        exit(1)

    bme_config = config.get("bme680", {})
    global_config = config.get("global", {})
    
    if not bme_config.get("enabled", False):
        return

    # 2. Hardware Initialization
    try:
        # Explicitly use I2C Bus 1 (Pins 3 & 5)
        i2c = busio.I2C(board.SCL, board.SDA) 
        
        # Clean the address string: remove extra chars and convert to int
        raw_addr = str(bme_config.get("address_hex", "0x77"))
        addr_clean = "".join(filter(lambda x: x in "0123456789abcdefx", raw_addr.lower()))
        addr = int(addr_clean.split('x')[-1], 16) if 'x' in addr_clean else int(addr_clean, 16)
        
        sensor = adafruit_bme680.Adafruit_BME680_I2C(i2c, address=addr)
        
        # GAS SENSOR BURN-IN
        sensor.gas_heat_temperature = 320
        sensor.gas_heat_duration = 150
        _ = sensor.gas 
        time.sleep(2) 
        
    except Exception as e:
        print(f"Hardware Init Error: {e}", file=sys.stderr)
        exit(1)

    # 3. Collect Data
    try:
        now_utc = datetime.now(timezone.utc)
        data_point = {
            "timestamp": now_utc.isoformat(),
            "temperature_C": round(sensor.temperature, 2),
            "humidity_percent": round(sensor.relative_humidity, 2),
            "pressure_hPa": round(sensor.pressure, 2),
            "gas_resistance_ohms": float(sensor.gas)
        }
    except Exception as e:
        print(f"Sensor Read Error: {e}", file=sys.stderr)
        exit(1)

    # 4. Atomic File Write
    base_dir = global_config.get("base_dir", "/home/pi/data")
    sensor_dir = os.path.join(base_dir, bme_config.get("directory", "bme680"))
    os.makedirs(sensor_dir, exist_ok=True)
    file_path = os.path.join(sensor_dir, bme_config.get("file_name", "bme680_env.json"))

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

        # Atomic replacement to prevent corruption
        tmp_path = f"{file_path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(full_data, f, indent=4)
        os.replace(tmp_path, file_path)
            
    except Exception as e:
        print(f"File Write Error: {e}", file=sys.stderr)
        exit(1)

if __name__ == "__main__":
    log_data()
