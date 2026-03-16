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


def load_existing_json(file_path, node_id):
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                pass

    return {
        "node_id": node_id,
        "sensor": "atlas_ec",
        "records": []
    }


def save_json_atomic(file_path, payload):
    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=4)
    os.replace(tmp_path, file_path)


def parse_first_float(text: str):
    parts = [p.strip() for p in text.split(",")]
    for part in parts:
        if not part:
            continue
        try:
            return round(float(part), 2)
        except ValueError:
            continue
    raise Exception(f"Could not parse numeric EC value from response: {repr(text)}")


def log_data():
    config = load_config()

    ec_config = config.get("atlas_ec", {})
    global_config = config.get("global", {})

    if not ec_config.get("enabled", False):
        return

    try:
        addr = int(str(ec_config.get("address_hex", "0x64")), 16)
        bus_num = int(ec_config.get("i2c_bus", 1))
        retries = int(ec_config.get("read_retries", 2))
        retry_sleep = float(ec_config.get("retry_sleep", 1.5))
    except Exception as e:
        print(f"Config Parse Error: {e}", file=sys.stderr)
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
            if status != 1 or "EC" not in info_text.upper():
                raise Exception(
                    f"Atlas EC identity check failed on bus {bus_num}, addr 0x{addr:02X}. "
                    f"Got {repr(info_text)} | Raw: {info_raw}"
                )

            value = None
            submerged = False
            last_raw = None

            for attempt in range(retries + 1):
                status, text, raw = dev.query("R", timeout=2.0)
                last_raw = raw

                if status == 1:
                    if text:
                        try:
                            value = parse_first_float(text)
                            submerged = True
                            break
                        except Exception:
                            if attempt < retries:
                                time.sleep(retry_sleep)
                                continue
                            raise Exception(f"Invalid EC payload: {repr(text)} | Raw: {raw}")
                    else:
                        if attempt < retries:
                            time.sleep(retry_sleep)
                            continue
                        value = None
                        submerged = False
                        break

                if status == 254:
                    if attempt < retries:
                        time.sleep(retry_sleep)
                        continue
                    raise Exception("EZO EC still processing after retries")

                desc = STATUS_CODES.get(status, "unknown")
                raise Exception(f"Atlas EC error code {status} ({desc}) | Raw: {raw}")

            if last_raw is None:
                raise Exception("No response from Atlas EC sensor")

    except Exception as e:
        print(f"Sensor Read Error: {e}", file=sys.stderr)
        sys.exit(1)

    data_point = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "conductivity_uS": value,
        "submerged": submerged
    }

    base_dir = global_config.get("base_dir", "/home/pi/data")
    sensor_dir = os.path.join(base_dir, ec_config.get("directory", "conductivity"))
    os.makedirs(sensor_dir, exist_ok=True)
    file_path = os.path.join(sensor_dir, ec_config.get("file_name", "ec_data.json"))

    try:
        node_id = global_config.get("node_id", "unknown-node")
        full_data = load_existing_json(file_path, node_id)
        full_data["records"].append(data_point)
        save_json_atomic(file_path, full_data)
        print(f"Logged EC reading: {value} uS/cm | submerged={submerged}")
    except Exception as e:
        print(f"File Write Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    log_data()
