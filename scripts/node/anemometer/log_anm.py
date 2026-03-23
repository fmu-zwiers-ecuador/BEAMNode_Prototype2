import serial
import json
from datetime import datetime

# Match arduino baud rate
ser = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)

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

        # Read existing data
        try:
            with open("wind_data.json", "r") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = []

        # Append new entry and save
        data.append(entry)
        with open("wind_data.json", "w") as f:
            json.dump(data, f, indent=2)
        
        print(entry)
    
    except:
        pass

ser.close()