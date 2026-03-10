import json
import os
import time
import sys
import smbus2
from smbus2 import i2c_msg
from datetime import datetime, timezone

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

def log_data():
    # 1. Load Configuration
    try:
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
    except Exception as e:
        print(f"Config Load Error: {e}", file=sys.stderr)
        sys.exit(1)

    ec_config = config.get("atlas_ec", {})
    global_config = config.get("global", {})
    
    if not ec_config.get("enabled", False):
        return

    # 2. Hardware Initialization & Read
    try:
        addr = int(str(ec_config.get("address_hex", "0x64")), 16)
        bus_num = ec_config.get("i2c_bus") or 1
        bus = smbus2.SMBus(bus_num)

        # Write 'R\r' — raw I2C, no register byte (Atlas EZO protocol)
        bus.i2c_rdwr(i2c_msg.write(addr, [ord('R'), 0x0D]))
        time.sleep(0.9)

        # Read 31-byte response — raw I2C, no register byte
        read_msg = i2c_msg.read(addr, 31)
        bus.i2c_rdwr(read_msg)
        res = list(read_msg)
        bus.close()

        STATUS_CODES = {254: "still processing", 255: "no data", 2: "syntax error", 0: "failed"}
        if res[0] == 1:
            char_list = [chr(x) for x in res[1:] if 32 <= x <= 126]
            raw_val = "".join(char_list).strip().split(',')[0].strip()
            if not raw_val:
                print("Warning: EZO returned empty value (probe likely dry/not submerged) — logging null", file=sys.stderr)
                value = None
                submerged = False
            else:
                value = round(float(raw_val), 2)
                submerged = True
        elif res[0] == 254:
            raise Exception("EZO still processing — increase sleep delay")
        else:
            desc = STATUS_CODES.get(res[0], "unknown")
            raise Exception(f"Atlas EZO Error Code {res[0]} ({desc})")
    except Exception as e:
        print(f"Sensor Read Error: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Collect Data
    now_utc = datetime.now(timezone.utc)
    data_point = {
        "timestamp": now_utc.isoformat(),
        "conductivity_uS": value,
        "submerged": submerged
    }

    # 4. Atomic File Write
    base_dir = global_config.get("base_dir", "/home/pi/data")
    sensor_dir = os.path.join(base_dir, ec_config.get("directory", "conductivity"))
    os.makedirs(sensor_dir, exist_ok=True)
    file_path = os.path.join(sensor_dir, ec_config.get("file_name", "ec_data.json"))

    try:
        node_id = global_config.get("node_id", "unknown-node")
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                try:
                    full_data = json.load(f)
                except json.JSONDecodeError:
                    full_data = {"node_id": node_id, "sensor": "atlas_ec", "records": []}
        else:
            full_data = {"node_id": node_id, "sensor": "atlas_ec", "records": []}

        full_data["records"].append(data_point)
        tmp_path = f"{file_path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(full_data, f, indent=4)
        os.replace(tmp_path, file_path)
    except Exception as e:
        print(f"File Write Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    log_data()
