"""
Low Power Mode Manager — Raspberry Pi Zero
Reads battery voltage from a Victron MPPT controller via raw VE.Direct serial.
Disables all sensors in config.json when voltage drops to/below LOW_VOLTAGE_THRESHOLD,
and re-enables them once voltage recovers to/above HIGH_VOLTAGE_THRESHOLD.
"""

from __future__ import annotations

import json
import serial
import time
import logging
import os
import sys
from datetime import datetime, timezone

# ─── Configuration ────────────────────────────────────────────────────────────

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

# Constants are set in main() after load_config is defined
SERIAL_PORT = BAUD_RATE = SERIAL_TIMEOUT = None
LOW_VOLTAGE_THRESHOLD = HIGH_VOLTAGE_THRESHOLD = None
POLL_INTERVAL = MAX_PARSE_FAILURES = None
JSON_LOG_PATH = VOLTAGE_LOG_PATH = None

# ─── Logging Setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

# ─── JSON Data Logger ─────────────────────────────────────────────────────────

def append_json_record(path: str, status: str, voltage: float | None) -> None:
    """
    Appends a single record to the JSON log file as a JSON array.
    Creates the file and parent directories if they don't exist.

    Record schema:
        {
            "timestamp": "<ISO8601>",
            "status":    "<string>",
            "voltage":   <float | null>
        }
    """
    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status":    status,
        "voltage":   round(voltage, 3) if voltage is not None else None,
    }

    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Read existing array (or start fresh)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        try:
            with open(path, "r") as f:
                records = json.load(f)
            if not isinstance(records, list):
                log.warning("JSON log file had unexpected format — resetting to empty array.")
                records = []
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Could not read JSON log ({e}) — resetting to empty array.")
            records = []
    else:
        records = []

    records.append(record)

    with open(path, "w") as f:
        json.dump(records, f, indent=4)

def append_voltage_sample(path: str, status: str, voltage: float | None) -> None:
    """Append one voltage sample as JSON-lines for easy tailing."""
    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "voltage_v": round(voltage, 3) if voltage is not None else None,
    }

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")

# ─── VE.Direct Raw Parser ─────────────────────────────────────────────────────

def read_vedirect_voltage(port: str, baud: int, timeout: int) -> float | None:
    """
    Opens the serial port, reads one complete VE.Direct text frame, and returns
    the battery voltage in volts (float), or None if parsing fails.

    VE.Direct text protocol format (each line):
        <Label>\t<Value>\r\n
    The voltage label is 'V' and the value is in millivolts (mV).
    A frame ends with a 'Checksum' label line.
    """
    CHARGE_STATES = {
        "0": "Off", "2": "Fault", "3": "Bulk",
        "4": "Absorption", "5": "Float", "7": "Equalize",
        "245": "Starting", "247": "Auto equalize", "252": "External control"
    }
    try:
        with serial.Serial(port, baud, timeout=timeout) as ser:
            frame = {}
            # Read lines until we get a complete frame (ends at Checksum line)
            # Give up after reading 40 lines to avoid hanging
            for _ in range(40):
                raw = ser.readline()
                if not raw:
                    continue
                try:
                    line = raw.decode("ascii", errors="ignore").strip()
                except Exception:
                    continue

                if "\t" not in line:
                    continue

                label, _, value = line.partition("\t")
                label = label.strip()
                value = value.strip()

                if label == "Checksum":
                    # End of frame — check if we captured voltage
                    if "V" in frame:
                        volts = int(frame["V"]) / 1000.0

                        soc = int(frame.get("SOC", -1)) / 10.0
                        cs_code = frame.get("CS", "?")
                        cs_name = CHARGE_STATES.get(cs_code, f"Unknown ({cs_code})")

                        if soc >= 0:
                            log.info(f"Battery: {volts:.3f} V | {soc:.1f}% | State: {cs_name}")
                        else:
                            log.info(f"Battery: {volts:.3f} V | SOC unavailable | State: {cs_name}")

                        return volts
                    else:
                        # Frame complete but no voltage key — reset and keep reading
                        frame = {}
                else:
                    frame[label] = value

    except serial.SerialException as e:
        log.error(f"Serial error on {port}: {e}")
    except ValueError as e:
        log.error(f"Voltage parse error (expected integer mV): {e}")

    return None

# ─── Config.json Helpers ──────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)

NON_SENSOR_KEYS = {"global", "low_power_mode", "lpm_pvpi"}
STATE_DISABLED_KEY = "low_power_disabled_sensors"
STATE_ACTIVE_KEY = "low_power_active"

def save_config(path: str, config: dict) -> None:
    with open(path, "w") as f:
        json.dump(config, f, indent=2)

def sensor_sections(config: dict):
    for sensor_name, sensor_cfg in config.items():
        if sensor_name in NON_SENSOR_KEYS:
            continue
        if not isinstance(sensor_cfg, dict):
            continue
        if isinstance(sensor_cfg.get("enabled"), bool):
            yield sensor_name, sensor_cfg

def enter_low_power(path: str) -> list[str]:
    """
    Disable currently enabled sensors and remember only the sensors changed by
    this low-power transition. User-disabled sensors stay out of the restore set.
    """
    config = load_config(path)
    lpm_section = config.setdefault("low_power_mode", {})
    previously_managed = set(lpm_section.get(STATE_DISABLED_KEY, []))
    changed = []

    for sensor_name, sensor_cfg in sensor_sections(config):
        if sensor_cfg["enabled"]:
            sensor_cfg["enabled"] = False
            changed.append(sensor_name)

    managed = sorted(previously_managed.union(changed))
    lpm_section[STATE_DISABLED_KEY] = managed
    lpm_section[STATE_ACTIVE_KEY] = True
    save_config(path, config)

    if changed:
        log.info(f"Sensors DISABLED: {', '.join(changed)}")
    else:
        log.info("No additional sensors needed disabling.")
    return changed

def restore_low_power_sensors(path: str) -> list[str]:
    """Restore only sensors previously disabled by this low-power manager."""
    config = load_config(path)
    lpm_section = config.setdefault("low_power_mode", {})
    managed = lpm_section.get(STATE_DISABLED_KEY, [])
    if not isinstance(managed, list):
        managed = []

    restored = []
    for sensor_name in managed:
        sensor_cfg = config.get(sensor_name)
        if isinstance(sensor_cfg, dict) and sensor_cfg.get("enabled") is False:
            sensor_cfg["enabled"] = True
            restored.append(sensor_name)

    lpm_section[STATE_DISABLED_KEY] = []
    lpm_section[STATE_ACTIVE_KEY] = False
    save_config(path, config)

    if restored:
        log.info(f"Sensors RESTORED: {', '.join(restored)}")
    else:
        log.info("No low-power-managed sensors needed restoring.")
    return restored

# ─── Main Loop ────────────────────────────────────────────────────────────────

def main():
    global SERIAL_PORT, BAUD_RATE, SERIAL_TIMEOUT
    global LOW_VOLTAGE_THRESHOLD, HIGH_VOLTAGE_THRESHOLD
    global POLL_INTERVAL, MAX_PARSE_FAILURES
    global JSON_LOG_PATH, VOLTAGE_LOG_PATH

    if not os.path.exists(CONFIG_PATH):
        log.error(f"config.json not found at: {os.path.abspath(CONFIG_PATH)}")
        sys.exit(1)
    _config = load_config(CONFIG_PATH)
    _lpm = _config["low_power_mode"]

    SERIAL_PORT            = _lpm["serial_port"]
    BAUD_RATE              = _lpm["baud_rate"]
    SERIAL_TIMEOUT         = _lpm["serial_timeout"]
    LOW_VOLTAGE_THRESHOLD  = _lpm["voltage_off_threshold"]
    HIGH_VOLTAGE_THRESHOLD = _lpm["voltage_on_threshold"]
    POLL_INTERVAL          = _lpm["poll_interval"]
    MAX_PARSE_FAILURES     = _lpm["max_parse_failures"]

    # Build JSON log path from config: ~/data/<directory>/<file_name>
    log_dir  = _lpm.get("directory", "low_power_mode")
    log_file = _lpm.get("file_name",  "low_power_log.json")
    voltage_log_file = _lpm.get("voltage_log_file", "voltage_log.jsonl")
    JSON_LOG_PATH = os.path.join("/home/pi/data", log_dir, log_file)
    VOLTAGE_LOG_PATH = os.path.join("/home/pi/data", log_dir, voltage_log_file)

    log.info("═" * 50)
    log.info("Low Power Mode Manager started")
    log.info(f"  Serial port : {SERIAL_PORT} @ {BAUD_RATE} baud")
    log.info(f"  Config file : {CONFIG_PATH}")
    log.info(f"  JSON log    : {JSON_LOG_PATH}")
    log.info(f"  Voltage log : {VOLTAGE_LOG_PATH}")
    log.info(f"  OFF below   : {LOW_VOLTAGE_THRESHOLD} V")
    log.info(f"  ON above    : {HIGH_VOLTAGE_THRESHOLD} V")
    log.info(f"  Poll every  : {POLL_INTERVAL}s")
    log.info("═" * 50)

    append_json_record(JSON_LOG_PATH, "manager_started", None)

    # Track current power state so we only write config.json on transitions
    # Start as None (unknown) so we always apply correct state on first read
    low_power_active = None
    consecutive_failures = 0

    while True:
        voltage = read_vedirect_voltage(SERIAL_PORT, BAUD_RATE, SERIAL_TIMEOUT)

        if voltage is None:
            consecutive_failures += 1
            append_voltage_sample(VOLTAGE_LOG_PATH, "read_failure", None)
            log.warning(
                f"Could not read voltage from {SERIAL_PORT} "
                f"(failure {consecutive_failures}/{MAX_PARSE_FAILURES})"
            )
            append_json_record(
                JSON_LOG_PATH,
                f"read_failure_{consecutive_failures}_of_{MAX_PARSE_FAILURES}",
                None
            )
            if consecutive_failures >= MAX_PARSE_FAILURES:
                log.error(
                    "Too many consecutive read failures. "
                    "Check your serial port and Victron connection."
                )
                append_json_record(JSON_LOG_PATH, "max_failures_reached", None)
                # Don't change sensor state — fail safe (leave as-is)
            time.sleep(POLL_INTERVAL)
            continue

        consecutive_failures = 0

        # ── Hysteresis logic ─────────────────────────────────────────────────
        # Only switch states at the thresholds, not in the middle band.
        # This prevents rapid toggling when voltage sits near a threshold.

        if voltage <= LOW_VOLTAGE_THRESHOLD and low_power_active is not True:
            append_voltage_sample(VOLTAGE_LOG_PATH, "low_power_activated", voltage)
            log.warning(
                f"Voltage {voltage:.3f} V ≤ {LOW_VOLTAGE_THRESHOLD} V "
                f"— entering LOW POWER MODE"
            )
            enter_low_power(CONFIG_PATH)
            append_json_record(JSON_LOG_PATH, "low_power_activated", voltage)
            low_power_active = True

        elif voltage >= HIGH_VOLTAGE_THRESHOLD and low_power_active is not False:
            append_voltage_sample(VOLTAGE_LOG_PATH, "sensors_restored", voltage)
            log.info(
                f"Voltage {voltage:.3f} V ≥ {HIGH_VOLTAGE_THRESHOLD} V "
                f"— exiting low power mode, RESTORING SENSORS"
            )
            restore_low_power_sensors(CONFIG_PATH)
            append_json_record(JSON_LOG_PATH, "sensors_restored", voltage)
            low_power_active = False

        else:
            # Voltage is in the middle band — hold current state
            state_str = "low_power" if low_power_active else "normal"
            append_voltage_sample(VOLTAGE_LOG_PATH, f"hold_{state_str}", voltage)
            log.info(f"Voltage in hold band — maintaining {state_str} state.")
            append_json_record(JSON_LOG_PATH, f"hold_{state_str}", voltage)

        time.sleep(POLL_INTERVAL)

# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user.")
        append_json_record(JSON_LOG_PATH, "stopped_by_user", None)
    except Exception as e:
        log.exception(f"Unexpected error: {e}")
        append_json_record(JSON_LOG_PATH, f"unexpected_error: {e}", None)
        sys.exit(1)
