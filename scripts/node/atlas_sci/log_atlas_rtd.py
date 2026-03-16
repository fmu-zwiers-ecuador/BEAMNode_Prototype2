import json
import os
import time
import sys
import smbus2
from datetime import datetime, timezone

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"


def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Config Load Error: {e}", file=sys.stderr)
        sys.exit(1)


def send_atlas_command(bus, addr, command: str):
    # Atlas EZO over I2C: send ASCII command bytes
    bus.write_i2c_block_data(addr, 0, [ord(c) for c in command])


def read_atlas_response(bus, addr, num_bytes=31):
    # Read raw response block from Atlas EZO circuit
    res = bus.read_i2c_block_data(addr, 0, num_bytes)

    if not res:
        raise Exception("Empty I2C response")

    status = res[0]

    # Keep printable ASCII only, ignore nulls and 0xFF padding
    chars = []
    for b in res[1:]:
        if b in (0x00, 0xFF):
            continue
        if 32 <= b <= 126:
            chars.append(chr(b))

    text = "".join(chars).strip()
    return status, text, res


def parse_float_response(status, text, raw):
    if status != 1:
        raise Exception(f"Atlas EZO Error Code: {status} | Raw: {raw}")

    if not text:
        raise Exception(f"Atlas returned empty text payload | Raw: {raw}")

    try:
        return round(float(text), 2)
    except ValueError:
        raise Exception(f"Could not parse float from response: {repr(text)} | Raw: {raw}")


def read_rtd_temperature(addr):
    try:
        with smbus2.SMBus(1) as bus:
            send_atlas_command(bus, addr, "R")
            time.sleep(1.0)

            status, text, raw = read_atlas_response(bus, addr, num_bytes=31)
            value = parse_float_response(status, text, raw)
            return value

    except Exception as e:
        raise Exception(f"Sensor Read Error: {e}")


def load_existing_json(file_path, node_id):
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {
                    "node_id": node_id,
                    "sensor": "atlas_rtd",
                    "records": []
                }

    return {
        "node_id": node_id,
        "sensor": "atlas_rtd",
        "records": []
    }


def save_data(file_path, payload):
    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=4)
    os.replace(tmp_path, file_path)


def log_data():
    config = load_config()

    rtd_config = config.get("atlas_rtd", {})
    global_config = config.get("global", {})

    if not rtd_config.get("enabled", False):
        print("atlas_rtd is disabled in config.")
        return

    try:
        addr = int(str(rtd_config.get("address_hex", "0x66")), 16)
    except ValueError:
        print("Invalid RTD I2C address in config.", file=sys.stderr)
        sys.exit(1)

    try:
        value = read_rtd_temperature(addr)
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    now_utc = datetime.now(timezone.utc)
    data_point = {
        "timestamp": now_utc.isoformat(),
        "water_temp_C": value
    }

    base_dir = global_config.get("base_dir", "/home/pi/data")
    sensor_dir = os.path.join(base_dir, rtd_config.get("directory", "temperature_water"))
    os.makedirs(sensor_dir, exist_ok=True)

    file_path = os.path.join(
        sensor_dir,
        rtd_config.get("file_name", "water_temp.json")
    )

    try:
        node_id = global_config.get("node_id", "unknown-node")
        full_data = load_existing_json(file_path, node_id)
        full_data["records"].append(data_point)
        save_data(file_path, full_data)
        print(f"Logged RTD reading: {value} C")
    except Exception as e:
        print(f"File Write Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    log_data()
