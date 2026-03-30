import serial
import json
from datetime import datetime
import os

# Match arduino baud rate
ser = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)

# Determine project root dynamically
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Load config
config_path = os.path.join(project_root, "config.json")
with open(config_path, "r") as f:
    config = json.load(f)

tsl_config = config["anemometer"]
global_config = config["global"]

node_id = global_config.get("node_id", "unknown-node")

# Directory and file for logs
directory = os.path.join(global_config.get("base_dir", os.path.join(project_root, "data")), tsl_config.get("directory", "anemometer"))
os.makedirs(directory, exist_ok=True)
file_name = tsl_config.get("file_name", "wind_data.json")
file_path = os.path.join(directory, file_name)

# Load existing data or start fresh
try: 
    with open("wind_data.json", "r") as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = []

try:
    print("Logging wind data... (Ctrl+C to stop)")
    while True:
        line = ser.readline().decode('utf-8').strip().replace('/r', '')
        if not line:
            continue
        try:
            parts = dict(p.split("=") for p in line.split())
            entry = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "raw": int(parts["raw"]),
                "voltage": float(parts["V"]),
                "wind_mph": float(parts["wind_mph"])
            }
            data.append(entry)
            print(entry)
        except Exception as e:
            print(f"Parse error: {e} | line: {line}")
except KeyboardInterrupt:
    print("\nStopped by user.")

finally:
    # Only write file once on exit
    with open("wind_data.json", "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {len(data)} entries to wind_data.json")
    ser.close()
    print("Serial port closed")