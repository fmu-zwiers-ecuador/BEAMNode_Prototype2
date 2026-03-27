# Author: Jackson Roberts | Air Quality Sensor Script for PMSA0031 Sensor
#!/usr/bin/env python3

import json
import os
from datetime import datetime, timezone

try:
    import serial
except Exception:
    raise SystemExit(0)

CONFIG_FILE = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"


def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def read_exact(port, size):
    data = bytearray()
    while len(data) < size:
        chunk = port.read(size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def read_pms_frame(port):
    while True:
        first = port.read(1)
        if not first:
            return None
        if first[0] != 0x42:
            continue

        second = port.read(1)
        if not second:
            return None
        if second[0] != 0x4D:
            continue

        rest = read_exact(port, 30)
        if rest is None:
            return None

        frame = bytes([0x42, 0x4D]) + rest
        checksum = sum(frame[0:30]) & 0xFFFF
        expected = (frame[30] << 8) | frame[31]
        if checksum != expected:
            continue

        frame_len = (frame[2] << 8) | frame[3]
        if frame_len != 28:
            continue

        return frame


def parse_pms_frame(frame):
    values = []
    for i in range(4, 30, 2):
        values.append((frame[i] << 8) | frame[i + 1])

    return {
        "pm1_0_cf1_ug_m3": values[0],
        "pm2_5_cf1_ug_m3": values[1],
        "pm10_cf1_ug_m3": values[2],
        "pm1_0_atm_ug_m3": values[3],
        "pm2_5_atm_ug_m3": values[4],
        "pm10_atm_ug_m3": values[5],
        "particles_0_3um_per_0_1L": values[6],
        "particles_0_5um_per_0_1L": values[7],
        "particles_1_0um_per_0_1L": values[8],
        "particles_2_5um_per_0_1L": values[9],
        "particles_5_0um_per_0_1L": values[10],
        "particles_10um_per_0_1L": values[11],
    }


def append_json_record(file_path, node_id, sensor_name, record):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    data = {}
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}

    if not isinstance(data, dict):
        data = {}

    data["node_id"] = node_id
    data["sensor"] = sensor_name
    if "records" not in data or not isinstance(data["records"], list):
        data["records"] = []

    data["records"].append(record)

    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)


def main():
    config = load_config()
    global_cfg = config.get("global", {})
    pm_cfg = config.get("air_quality", {})

    if not pm_cfg.get("enabled", False):
        raise SystemExit(0)

    node_id = global_cfg.get("node_id", "beam-node-01")
    base_dir = global_cfg.get("base_dir", "/home/pi/data")
    out_dir = pm_cfg.get("directory", "air_quality")
    file_name = pm_cfg.get("file_name", "pm_data.json")
    serial_port = pm_cfg.get("serial_port", "/dev/ttyS0")
    baud_rate = int(pm_cfg.get("baud_rate", 9600))
    timeout_sec = float(pm_cfg.get("read_timeout_sec", 3.0))

    file_path = os.path.join(base_dir, out_dir, file_name)

    try:
        with serial.Serial(serial_port, baudrate=baud_rate, timeout=timeout_sec) as port:
            frame = read_pms_frame(port)
            if frame is None:
                raise SystemExit(0)
            parsed = parse_pms_frame(frame)
    except Exception:
        raise SystemExit(0)

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone()

    record = {
        "timestamp_utc": now_utc.isoformat(),
        "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": now_local.tzname(),
        **parsed,
    }

    try:
        append_json_record(file_path, node_id, "air_quality", record)
    except Exception:
        raise SystemExit(0)


if __name__ == "__main__":
    main()
