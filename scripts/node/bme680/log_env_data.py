import time
import json
import os
import board
import adafruit_bme680
from datetime import datetime

# Path to the shared configuration file
CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

def get_config():
    """Safely load the node configuration."""
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def log_env_data():
    """Reads BME680 sensors and logs to the BEAM data directory."""
    config = get_config()
    bme_cfg = config.get("bme680", {})
    global_cfg = config.get("global", {})
    
    # Initialize I2C bus and the BME680 sensor
    i2c = board.I2C()
    sensor = adafruit_bme680.Adafruit_BME680_I2C(i2c, debug=False)

    # Standard sea level pressure for hPa calculations
    sensor.sea_level_pressure = 1013.25

    # Establish the output directory and filename from config
    base_dir = global_cfg.get("base_dir", "/home/pi/data")
    sensor_dir = bme_cfg.get("directory", "bme680")
    output_dir = os.path.join(base_dir, sensor_dir)
    
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, bme_cfg.get("file_name", "bme680_env.json"))

    if global_cfg.get("print_debug"):
        print(f"[BEAM] Starting BME680 logging to {file_path}")

    try:
        while True:
            # Create a record matching the schema defined in config.json
            data_record = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "temperature_C": round(sensor.temperature, 2),
                "humidity_percent": round(sensor.relative_humidity, 2),
                "pressure_hPa": round(sensor.pressure, 2),
                "gas_resistance_ohms": sensor.gas
            }

            # Append the record to the local JSON log
            with open(file_path, "a") as f:
                f.write(json.dumps(data_record) + "\n")

            if global_cfg.get("print_debug"):
                print(f"Logged: {data_record['temperature_C']}C, {data_record['gas_resistance_ohms']} ohms")

            # Sleep based on the frequency defined in config (default 60s)
            time.sleep(bme_cfg.get("frequency", 60))

    except KeyboardInterrupt:
        print("[BEAM] Logging stopped by user.")
    except Exception as e:
        print(f"[BEAM] Critical error in log_env_data: {e}")

if __name__ == "__main__":
    log_env_data()
