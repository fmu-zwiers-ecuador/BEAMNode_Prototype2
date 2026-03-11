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

        # Allow configurable retries when the probe returns an empty value.
        retries = int(ec_config.get("read_retries", 2))
        retry_sleep = float(ec_config.get("retry_sleep", 1.5))

        STATUS_CODES = {254: "still processing", 255: "no data", 2: "syntax error", 0: "failed"}
        res = None
        raw_val = None
        value = None
        submerged = False

        # Each attempt sends a fresh 'R\r' command then waits for the response.
        # Re-using a single read without re-issuing R causes status 255 on retry
        # because the EZO clears its response slot after the first read.
        for attempt in range(1 + max(0, retries)):
            bus.i2c_rdwr(i2c_msg.write(addr, [ord('R'), 0x0D]))
            time.sleep(2.0)  # EZO EC min ~600ms + i3 InterLink relay overhead
            read_msg = i2c_msg.read(addr, 31)
            bus.i2c_rdwr(read_msg)
            res = list(read_msg)

            if not res:
                # No bytes at all; try again if allowed
                if attempt < retries:
                    continue
                else:
                    raise Exception("No response from Atlas EZO sensor")

            if res[0] == 1:
                char_list = [chr(x) for x in res[1:] if 32 <= x <= 126]
                raw_val = "".join(char_list).strip().split(',')[0].strip()
                if raw_val:
                    value = round(float(raw_val), 2)
                    submerged = True
                    break
                else:
                    # empty textual payload — either probe dry, needs conditioning, or timing issue
                    if attempt < retries:
                        print(f"Warning: Atlas EZO returned empty value (attempt {attempt+1}/{1+retries}) - raw bytes: {res}", file=sys.stderr)
                        continue
                    else:
                        print(f"Warning: Atlas EZO returned empty value after {1+retries} attempt(s). Probe may be dry, need conditioning/calibration solution, or increase read_retries - raw bytes: {res}", file=sys.stderr)
                        value = None
                        submerged = False
                        break
            elif res[0] == 254:
                # sensor still processing
                raise Exception("EZO still processing - increase sleep delay or retry settings")
            else:
                desc = STATUS_CODES.get(res[0], "unknown")
                raise Exception(f"Atlas EZO Error Code {res[0]} ({desc})")

        bus.close()
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
