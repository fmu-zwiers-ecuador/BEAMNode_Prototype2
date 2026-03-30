# Author: Jackson Roberts | Air Quality Sensor Script for PMSA0031 Sensor
#!/usr/bin/env python3

import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    import serial
except Exception:
    raise SystemExit(0)

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


def read_exact(port, size):
    data = bytearray()
    while len(data) < size:
        chunk = port.read(size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def read_pms_frame(port, max_wait_sec):
    deadline = time.monotonic() + max_wait_sec

    while time.monotonic() < deadline:
        first = port.read(1)
        if not first:
            continue
        if first[0] != 0x42:
            continue

        second = port.read(1)
        if not second:
            continue
        if second[0] != 0x4D:
            continue

        rest = read_exact(port, 30)
        if rest is None:
            continue

        frame = bytes([0x42, 0x4D]) + rest
        checksum = sum(frame[0:30]) & 0xFFFF
        expected = (frame[30] << 8) | frame[31]
        if checksum != expected:
            continue

        frame_len = (frame[2] << 8) | frame[3]
        if frame_len != 28:
            continue

        return frame

    return None


def build_candidate_ports(configured_port, configured_candidates):
    candidates = []

    if isinstance(configured_candidates, list):
        candidates.extend([p for p in configured_candidates if isinstance(p, str) and p.strip()])

    if isinstance(configured_port, str) and configured_port.strip():
        candidates.insert(0, configured_port)

    # Keep a small, practical fallback set for Raspberry Pi UART naming differences.
    candidates.extend([
        "/dev/serial0",
        "/dev/ttyAMA0",
        "/dev/ttyS0",
        "/dev/ttyUSB0",
        "/dev/ttyUSB1",
    ])

    seen = set()
    ordered = []
    for port in candidates:
        if port not in seen:
            seen.add(port)
            ordered.append(port)
    return ordered


def read_first_valid_frame(candidate_ports, baud_rate, timeout_sec, frame_search_sec, debug):
    errors = []

    for port_name in candidate_ports:
        if not os.path.exists(port_name):
            errors.append(f"{port_name}: device does not exist")
            continue

        try:
            with serial.Serial(port_name, baudrate=baud_rate, timeout=timeout_sec) as port:
                # Drop stale bytes before trying to parse a fresh frame.
                port.reset_input_buffer()
                frame = read_pms_frame(port, frame_search_sec)
                if frame is None:
                    errors.append(f"{port_name}: no valid PMS frame within {frame_search_sec}s")
                    continue
                return port_name, frame, errors
        except Exception as e:
            errors.append(f"{port_name}: {e}")

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
    serial_port = pm_cfg.get("serial_port", "/dev/ttyS0")
    serial_port_candidates = pm_cfg.get("serial_port_candidates", [])
    baud_rate = int(pm_cfg.get("baud_rate", 9600))
    timeout_sec = float(pm_cfg.get("read_timeout_sec", 3.0))
    frame_search_sec = float(pm_cfg.get("frame_search_sec", max(6.0, timeout_sec * 2.0)))

    file_path = os.path.join(base_dir, out_dir, file_name)
    candidate_ports = build_candidate_ports(serial_port, serial_port_candidates)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    debug(f"Config loaded from: {CONFIG_FILE}")
    debug(
        f"Serial ports: {candidate_ports} @ {baud_rate} timeout={timeout_sec}s frame_search={frame_search_sec}s"
    )
    debug(f"Output file: {file_path}")

    try:
        selected_port, frame, read_errors = read_first_valid_frame(
            candidate_ports, baud_rate, timeout_sec, frame_search_sec, debug
        )
    except KeyboardInterrupt:
        print("[air_quality] Interrupted by user.", file=sys.stderr)
        raise SystemExit(130)
    if frame is None:
        print(
            "[air_quality] No PMS frame received on any candidate serial port.",
            file=sys.stderr,
        )
        for err in read_errors:
            print(f"[air_quality]   - {err}", file=sys.stderr)
        print(
            "[air_quality] Check TX/RX wiring, disable serial console, and verify enable_uart=1.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    debug(f"Using serial port: {selected_port}")
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
