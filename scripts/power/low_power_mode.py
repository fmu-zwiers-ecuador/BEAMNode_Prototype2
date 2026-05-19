
"""
Low Power Mode Manager — Raspberry Pi Zero
Reads battery voltage from a Victron MPPT controller via raw VE.Direct serial.
Disables all sensors in config.json when voltage drops to/below LOW_VOLTAGE_THRESHOLD,
and re-enables them once voltage recovers to/above HIGH_VOLTAGE_THRESHOLD.
"""
 
import json
import serial
import time
import logging
import os
import sys
 
# ─── Configuration ────────────────────────────────────────────────────────────
 
SERIAL_PORT        = "/dev/ttyUSB0"   # Change if your Victron shows up on a different port
BAUD_RATE          = 19200            # VE.Direct standard baud rate
SERIAL_TIMEOUT     = 5               # seconds to wait for a full frame
 
CONFIG_PATH        = "/home/pi//BEAMNode_Prototype2/scripts/node/config.json"   # Path to your sensor config file
 
LOW_VOLTAGE_THRESHOLD  = 11.8        # V — turn sensors OFF at or below this
HIGH_VOLTAGE_THRESHOLD = 13.1        # V — turn sensors back ON at or above this
 
POLL_INTERVAL      = 30              # seconds between voltage checks
MAX_PARSE_FAILURES = 5               # consecutive failures before alerting
 
# ─── Logging Setup ────────────────────────────────────────────────────────────
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/home/pi/logs/low_power_mode.log"),
    ]
)
log = logging.getLogger(__name__)
 
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
                        millivolts = int(frame["V"])
                        volts = millivolts / 1000.0
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
 
def save_config(path: str, config: dict) -> None:
    with open(path, "w") as f:
        json.dump(config, f, indent=4)
 
def set_all_sensors(path: str, enabled: bool) -> None:
    """
    Iterates every top-level key in config.json. If the value is a dict
    containing an 'enabled' boolean field, that field is set to `enabled`.
    All other fields (directory, script_name, frequency, etc.) are untouched.
 
    Expects config.json structure like:
    {
        "ahtx0": {
            "enabled": false,
            "directory": "ahtx0",
            "script_name": "log_ahtx0_paramdata.py",
            ...
        },
        "gps": {
            "enabled": true,
            ...
        }
    }
    """
    config = load_config(path)
 
    changed = []
    skipped = []
 
    for sensor_name, sensor_cfg in config.items():
        if not isinstance(sensor_cfg, dict):
            # Top-level key is not a sensor block (e.g. a plain string/int) — skip
            skipped.append(sensor_name)
            continue
 
        if "enabled" not in sensor_cfg:
            # Sensor block exists but has no 'enabled' field — leave it alone
            skipped.append(sensor_name)
            continue
 
        if not isinstance(sensor_cfg["enabled"], bool):
            log.warning(
                f"'{sensor_name}.enabled' is not a boolean "
                f"(got {type(sensor_cfg['enabled']).__name__}) — skipping."
            )
            continue
 
        if sensor_cfg["enabled"] != enabled:
            sensor_cfg["enabled"] = enabled
            changed.append(sensor_name)
 
    if skipped:
        log.debug(f"Non-sensor keys ignored: {', '.join(skipped)}")
 
    if changed:
        save_config(path, config)
        state_str = "ENABLED" if enabled else "DISABLED"
        log.info(f"Sensors {state_str}: {', '.join(changed)}")
    else:
        log.info("No sensor states needed changing.")
 
# ─── Main Loop ────────────────────────────────────────────────────────────────
 
def main():
    if not os.path.exists(CONFIG_PATH):
        log.error(f"config.json not found at: {os.path.abspath(CONFIG_PATH)}")
        sys.exit(1)
 
    log.info("═" * 50)
    log.info("Low Power Mode Manager started")
    log.info(f"  Serial port : {SERIAL_PORT} @ {BAUD_RATE} baud")
    log.info(f"  Config file : {CONFIG_PATH}")
    log.info(f"  OFF below   : {LOW_VOLTAGE_THRESHOLD} V")
    log.info(f"  ON above    : {HIGH_VOLTAGE_THRESHOLD} V")
    log.info(f"  Poll every  : {POLL_INTERVAL}s")
    log.info("═" * 50)
 
    # Track current power state so we only write config.json on transitions
    # Start as None (unknown) so we always apply correct state on first read
    low_power_active = None
    consecutive_failures = 0
 
    while True:
        voltage = read_vedirect_voltage(SERIAL_PORT, BAUD_RATE, SERIAL_TIMEOUT)
 
        if voltage is None:
            consecutive_failures += 1
            log.warning(
                f"Could not read voltage from {SERIAL_PORT} "
                f"(failure {consecutive_failures}/{MAX_PARSE_FAILURES})"
            )
            if consecutive_failures >= MAX_PARSE_FAILURES:
                log.error(
                    "Too many consecutive read failures. "
                    "Check your serial port and Victron connection."
                )
                # Don't change sensor state — fail safe (leave as-is)
            time.sleep(POLL_INTERVAL)
            continue
 
        consecutive_failures = 0
        log.info(f"Battery voltage: {voltage:.3f} V")
 
        # ── Hysteresis logic ─────────────────────────────────────────────────
        # Only switch states at the thresholds, not in the middle band.
        # This prevents rapid toggling when voltage sits near a threshold.
 
        if voltage <= LOW_VOLTAGE_THRESHOLD and low_power_active is not True:
            log.warning(
                f"Voltage {voltage:.3f} V ≤ {LOW_VOLTAGE_THRESHOLD} V "
                f"— entering LOW POWER MODE"
            )
            set_all_sensors(CONFIG_PATH, enabled=False)
            low_power_active = True
 
        elif voltage >= HIGH_VOLTAGE_THRESHOLD and low_power_active is not False:
            log.info(
                f"Voltage {voltage:.3f} V ≥ {HIGH_VOLTAGE_THRESHOLD} V "
                f"— exiting low power mode, RESTORING SENSORS"
            )
            set_all_sensors(CONFIG_PATH, enabled=True)
            low_power_active = False
 
        else:
            # Voltage is in the middle band — hold current state
            state_str = "low power" if low_power_active else "normal"
            log.info(f"Voltage in hold band — maintaining {state_str} state.")
 
        time.sleep(POLL_INTERVAL)
 
# ─── Entry Point ──────────────────────────────────────────────────────────────
 
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    except Exception as e:
        log.exception(f"Unexpected error: {e}")
        sys.exit(1)
 