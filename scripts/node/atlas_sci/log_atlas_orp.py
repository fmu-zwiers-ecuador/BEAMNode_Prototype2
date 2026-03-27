# Author: Jackson Roberts | ORP Logging Script

import json
import os
import time
import sys
import io
import fcntl
from datetime import datetime, timezone

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"


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

    def query(self, command: str, timeout: float = 1.0, num_bytes: int = 31):
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
        "sensor": "atlas_orp",
        "records": []
    }


def save_json_atomic(file_path, payload):
    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=4)
    os.replace(tmp_path, file_path)


def log_data():
    config = load_config()

    orp_config = config.get("atlas_orp", {})
    global_config = config.get("global", {})

    if not orp_config.get("enabled", False):
        return

    try:
        addr_hex = orp_config.get("address_hex") or "0x62"
        i2c_bus = orp_config.get("i2c_bus")
        bus_num = int(i2c_bus) if i2c_bus is not None else 1
        addr = int(str(addr_hex), 16)
    except Exception as e:
        print(
            f"Config Parse Error in atlas_orp: "
            f"address_hex={orp_config.get('address_hex')!r}, "
            f"i2c_bus={orp_config.get('i2c_bus')!r} | {e}",
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
            if status != 1 or "ORP" not in info_text.upper():
                raise Exception(
                    f"Atlas ORP identity check failed on bus {bus_num}, addr 0x{addr:02X}. "
                    f"Got {repr(info_text)} | Raw: {info_raw}"
                )

            status, text, raw = dev.query("R", timeout=1.0)

            if status != 1:
                desc = STATUS_CODES.get(status, "unknown")
                raise Exception(f"Atlas ORP error code {status} ({desc}) | Raw: {raw}")

            if not text:
                raise Exception(f"Atlas ORP returned empty payload | Raw: {raw}")

            try:
                value = round(float(text), 1)
            except ValueError:
                raise Exception(f"Could not parse ORP float from {repr(text)} | Raw: {raw}")

    except Exception as e:
        print(f"Sensor Read Error: {e}", file=sys.stderr)
        sys.exit(1)

    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now().astimezone()

    data_point = {
        "timestamp": now_utc.isoformat(),
        "local_timestamp": now_local.isoformat(),
        "orp_mV": value
    }

    base_dir = global_config.get("base_dir", "/home/pi/data")
    sensor_dir = os.path.join(base_dir, orp_config.get("directory", "orp"))
    os.makedirs(sensor_dir, exist_ok=True)
    file_path = os.path.join(sensor_dir, orp_config.get("file_name", "orp_data.json"))

    try:
        node_id = global_config.get("node_id", "unknown-node")
        full_data = load_existing_json(file_path, node_id)
        full_data["records"].append(data_point)
        save_json_atomic(file_path, full_data)
        print(f"Logged ORP reading: {value} mV")
    except Exception as e:
        print(f"File Write Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    log_data()
