# Author: Jackson Roberts | Dissolved Oxygen Logging Script

import json
import os
import re
import time
import sys
import io
import fcntl
from datetime import datetime
from zoneinfo import ZoneInfo

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"
EASTERN_TZ = ZoneInfo("America/New_York")


class AtlasI2CDevice:
    I2C_SLAVE = 0x0703

    def __init__(self, address: int, bus: int = 1):
        self.address = address
        self.bus = bus
        self.file_read = io.open(f"/dev/i2c-{bus}", "rb", buffering=0)
        self.file_write = io.open(f"/dev/i2c-{bus}", "wb", buffering=0)

        fcntl.ioctl(self.file_read, self.I2C_SLAVE, address)
        fcntl.ioctl(self.file_write, self.I2C_SLAVE, address)

    def write(self, command: str):
        self.file_write.write((command + "\x00").encode("latin-1"))

    def read(self, num_bytes: int = 31):
        raw = self.file_read.read(num_bytes)
        if not raw:
            raise Exception("Empty I2C response")

        status = raw[0]
        chars = [chr(b & ~0x80) for b in raw[1:] if b not in (0x00, 0xFF)]
        text = "".join(chars).strip()
        return status, text, list(raw)

    def query(self, command: str, timeout: float = 1.2, num_bytes: int = 31):
        self.write(command)
        time.sleep(timeout)
        return self.read(num_bytes)

    def close(self):
        try:
            self.file_read.close()
        finally:
            self.file_write.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Config Load Error: {e}", file=sys.stderr)
        sys.exit(1)


def load_existing_json(file_path, node_id):
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                pass

    return {
        "node_id": node_id,
        "sensor": "atlas_do",
        "records": []
    }


def save_json_atomic(file_path, payload):
    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=4)
    os.replace(tmp_path, file_path)


def parse_float_values(text: str):
    numbers = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)
    if not numbers:
        raise Exception(f"Could not parse numeric DO value from response: {repr(text)}")
    return [float(value) for value in numbers]


def log_data():
    config = load_config()

    do_config = config.get("atlas_do", {})
    global_config = config.get("global", {})

    if not do_config.get("enabled", False):
        return

    try:
        addr_hex = do_config.get("address_hex") or "0x61"
        i2c_bus = do_config.get("i2c_bus")
        bus_num = int(i2c_bus) if i2c_bus is not None else 1
        addr = int(str(addr_hex), 16)
    except Exception as e:
        print(
            f"Config Parse Error in atlas_do: "
            f"address_hex={do_config.get('address_hex')!r}, "
            f"i2c_bus={do_config.get('i2c_bus')!r} | {e}",
            file=sys.stderr
        )
        sys.exit(1)

    STATUS_CODES = {
        0: "failed",
        2: "syntax error",
        254: "still processing",
        255: "no data"
    }

    try:
        with AtlasI2CDevice(addr, bus=bus_num) as dev:
            status, info_text, info_raw = dev.query("I", timeout=0.5)
            info_norm = "".join(ch for ch in info_text.upper() if ch.isalnum())
            if status != 1 or "DO" not in info_norm:
                raise Exception(
                    f"Atlas DO identity check failed on bus {bus_num}, addr 0x{addr:02X}. "
                    f"Got {repr(info_text)} | Raw: {info_raw}"
                )

            status, text, raw = dev.query("R", timeout=1.5)

            if status != 1:
                desc = STATUS_CODES.get(status, "unknown")
                raise Exception(f"Atlas DO error code {status} ({desc}) | Raw: {raw}")

            if not text:
                raise Exception(f"Atlas DO returned empty payload | Raw: {raw}")

            values = parse_float_values(text)
            dissolved_oxygen = round(values[0], 2)
            saturation = round(values[1], 2) if len(values) > 1 else None

    except Exception as e:
        print(f"Sensor Read Error: {e}", file=sys.stderr)
        sys.exit(1)

    now_local = datetime.now(EASTERN_TZ)

    data_point = {
        "timestamp_eastern": now_local.isoformat(),
        "dissolved_oxygen_mg_L": dissolved_oxygen
    }
    if saturation is not None:
        data_point["saturation_percent"] = saturation

    base_dir = global_config.get("base_dir", "/home/pi/data")
    sensor_dir = os.path.join(base_dir, do_config.get("directory", "dissolved_oxygen"))
    os.makedirs(sensor_dir, exist_ok=True)
    file_path = os.path.join(sensor_dir, do_config.get("file_name", "do_data.json"))

    try:
        node_id = global_config.get("node_id", "unknown-node")
        full_data = load_existing_json(file_path, node_id)
        full_data["records"].append(data_point)
        save_json_atomic(file_path, full_data)
        if saturation is None:
            print(f"Logged DO reading: {dissolved_oxygen} mg/L")
        else:
            print(
                f"Logged DO reading: {dissolved_oxygen} mg/L "
                f"| saturation={saturation}%"
            )
    except Exception as e:
        print(f"File Write Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    log_data()
