import serial
import json
from datetime import datetime

# Match arduino baud rate
ser = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)

# Load existing data or start fresh
try: 
    with open("wind_data.json", "r") as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = []

try:
    print("Logging wind data... (Ctrl+C to stop)")
    while True:
        line = ser.readline().decode('utf-8').strip()
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