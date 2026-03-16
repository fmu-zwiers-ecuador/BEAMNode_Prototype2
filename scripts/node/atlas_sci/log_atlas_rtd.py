import json
import os
import time
import sys
import io
import fcntl
from datetime import datetime, timezone

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"


class AtlasI2CDevice:
    I2C_SLAVE = 0x703

    def __init__(self, address: int, bus: int = 1):
        self.address = address
        self.bus = bus
        self.file_read = io.open(f"/dev/i2c-{bus}", "rb", buffering=0)
        self.file_write = io.open(f"/dev/i2c-{bus}", "wb", buffering=0)

        fcntl.ioctl(self.file_read, self.I2C_SLAVE, address)
        fcntl.ioctl(self.file_write, self.I2C_SLAVE, address)

    def write(self, command: str):
        # Atlas sample appends a null byte for I2C commands
        command += "\x00"
        self.file_write.write(command.encode("latin-1"))

    def read(self, num_bytes: int = 31):
        raw = self.file_read.read(num_bytes)
        if not raw:
            raise Exception("Empty I2C response")

        status = raw[0]

        # Atlas sample masks off the MSB on returned chars
        chars = [chr(b & ~0x80) for b in raw[1:] if b != 0x00]
        text = "".join(chars).strip()

        return status, text, list(raw)

    def query(self, command: str, timeout: float = 1.5, num_bytes: int = 31):
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


def read_rtd_temperature(addr: int) -> float:
    with AtlasI2CDevice(addr, bus=1) as dev:
        status, text, raw = dev.query("R", timeout=1.5, num_bytes=31)

        print(f"DEBUG status={status}, text={repr(text)}, raw={raw}")

        if status != 1:
            raise Exception(f"Atlas EZO error code: {status} | Raw: {raw}")

        if not text:
            raise Exception(f"Atlas returned empty payload | Raw: {raw}")

        try:
            return round(float(text), 2)
        except ValueError:
            raise Exception(f"Could not parse float from {repr(text)} | Raw: {raw}")


def load_existing_json(file_path, node_id):
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                pass

    return {
        "node_id": node_id,
        "sensor": "atlas_rtd",
        "records": []
    }


def save_json_atomic(file_path, payload):
    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=4)
    os.replace(tmp_path, file_path)


def log_data():
    config = load_config()

    rtd_config = config.get("atlas_rtd", {})
    global_config = config.get("global", {})

    if not rtd_config.get("enabled", False):
        return

    try:
        addr = int(str(rtd_config.get("address_hex", "0x66")), 16)
    except ValueError:
        print("Invalid atlas_rtd address_hex in config", file=sys.stderr)
        sys.exit(1)

    try:
        value = read_rtd_temperature(addr)
    except Exception as e:
        print(f"Sensor Read Error: {e}", file=sys.stderr)
        sys.exit(1)

    data_point = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
        save_json_atomic(file_path, full_data)
        print(f"Logged RTD reading: {value} C")
    except Exception as e:
        print(f"File Write Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    log_data()
