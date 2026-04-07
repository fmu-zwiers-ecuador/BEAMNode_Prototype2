# Author: Jackson Roberts | Air Quality Sensor Script for PMSA0031 Sensor
#!/usr/bin/env python3

import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    from smbus2 import SMBus, i2c_msg
except Exception:
    SMBus = None
    i2c_msg = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NODE_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DEFAULT_CONFIG_FILE = os.path.join(NODE_DIR, "config.json")
CONFIG_FILE = os.environ.get("BEAM_CONFIG_PATH", DEFAULT_CONFIG_FILE)


def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[air_quality] Failed to load config at {CONFIG_FILE}: {e}", file=sys.stderr)
        return {}


def is_valid_pms_frame(frame):
    if len(frame) != 32:
        return False
    if frame[0] != 0x42 or frame[1] != 0x4D:
        return False

    frame_len = (frame[2] << 8) | frame[3]
    if frame_len != 28:
        return False

    checksum = sum(frame[0:30]) & 0xFFFF
    expected = (frame[30] << 8) | frame[31]
    return checksum == expected


def _parse_i2c_addr(value, default):
    try:
        if isinstance(value, str):
            return int(value, 0)
        return int(value)
    except Exception:
        return default


def build_candidate_i2c_targets(configured_bus, configured_address, configured_candidates):
    candidates = []

    if isinstance(configured_candidates, list):
        for item in configured_candidates:
            if not isinstance(item, dict):
                continue
            bus = _parse_i2c_addr(item.get("bus", 1), 1)
            addr = _parse_i2c_addr(item.get("address", item.get("address_hex", "0x12")), 0x12)
            candidates.append((bus, addr))

    try:
        primary_bus = _parse_i2c_addr(configured_bus, 1)
        primary_addr = _parse_i2c_addr(
            configured_address if configured_address is not None else "0x12",
            0x12,
        )
        candidates.insert(0, (primary_bus, primary_addr))
    except Exception:
        pass

    candidates.append((1, 0x12))

    seen = set()
    ordered = []
    for bus, addr in candidates:
        if (bus, addr) not in seen:
            seen.add((bus, addr))
            ordered.append((bus, addr))
    return ordered


def read_first_valid_frame_i2c(candidate_targets, timeout_sec):
    errors = []
    if SMBus is None or i2c_msg is None:
        return None, None, ["smbus2 library is not installed"]

    for bus, addr in candidate_targets:
        try:
            with SMBus(int(bus)) as i2c_bus:
                read = i2c_msg.read(addr, 32)
                i2c_bus.i2c_rdwr(read)
                frame = bytes(read)

            if frame and is_valid_pms_frame(frame):
                return f"/dev/i2c-{bus}@0x{addr:02X}", frame, errors
            errors.append(f"/dev/i2c-{bus} addr 0x{addr:02X}: invalid PMS frame")
        except Exception as e:
            errors.append(f"/dev/i2c-{bus} addr 0x{addr:02X}: {e}")

    return None, None, errors


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
    print_debug = bool(global_cfg.get("print_debug", False))

    def debug(msg):
        if print_debug:
            print(f"[air_quality] {msg}")

    if not pm_cfg.get("enabled", False):
        debug("air_quality.enabled is false; exiting")
        raise SystemExit(0)

    node_id = global_cfg.get("node_id", "beam-node-01")
    base_dir = global_cfg.get("base_dir", "/home/pi/data")
    out_dir = pm_cfg.get("directory", "air_quality")
    file_name = pm_cfg.get("file_name", "pm_data.json")
    interface = str(pm_cfg.get("interface", "i2c")).strip().lower()
    timeout_sec = float(pm_cfg.get("read_timeout_sec", 3.0))

    file_path = os.path.join(base_dir, out_dir, file_name)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    debug(f"Config loaded from: {CONFIG_FILE}")
    debug(f"Interface: {interface}")
    debug(f"I2C timeout: {timeout_sec}s")
    debug(f"Output file: {file_path}")

    try:
        if interface == "i2c":
            candidate_i2c_targets = build_candidate_i2c_targets(
                pm_cfg.get("i2c_bus", 1),
                pm_cfg.get("i2c_address", pm_cfg.get("address_hex", "0x12")),
                pm_cfg.get("i2c_candidates", []),
            )
            debug(f"I2C targets: {candidate_i2c_targets}")
            selected_port, frame, read_errors = read_first_valid_frame_i2c(
                candidate_i2c_targets,
                timeout_sec,
            )
        else:
            print(f"[air_quality] Unsupported interface '{interface}'. Use 'i2c'.", file=sys.stderr)
            raise SystemExit(1)
    except KeyboardInterrupt:
        print("[air_quality] Interrupted by user.", file=sys.stderr)
        raise SystemExit(130)
    if frame is None:
        print(
            "[air_quality] No PMS frame received on the configured I2C address.",
            file=sys.stderr,
        )
        for err in read_errors:
            print(f"[air_quality]   - {err}", file=sys.stderr)
        print(
            "[air_quality] Check SDA/SCL wiring, I2C bus enablement, and that address 0x12 is present.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    debug(f"Using I2C target: {selected_port}")
    parsed = parse_pms_frame(frame)

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
        debug("Record appended successfully")
    except Exception as e:
        print(f"[air_quality] Failed to write JSON file {file_path}: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
