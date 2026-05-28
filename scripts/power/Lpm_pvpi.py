"""
Low-power mode manager for a solar-powered Raspberry Pi Zero (PV Pi).

The PV Pi HAT communicates over UART using simple ASCII commands.
Battery voltage is read by sending GET_BAT_V and parsing the response —
there is no SPI/I2C ADC to read directly.

In raspi-config, go to Interface Options
Select Serial Port
When asked "Would you like a login shell to be accessible over the serial port?" → No
When asked "Would you like the serial port hardware to be enabled?" → Yes
Finish and reboot
"""

import argparse
import json
import logging
import serial
import time

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

# Sensor config JSON (also contains the lpm_pvpi section)
SENSOR_CONFIG_PATH = Path(
    "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"
)

# Plain text log file
LOG_FILE_PATH = Path(
    "/home/pi/logs/low_power.log"
)


@dataclass
class LpmConfig:
    """Runtime settings loaded from the lpm_pvpi config section."""
    serial_port:      str   = "/dev/serial0"
    serial_baud:      int   = 115200
    serial_timeout:   int   = 2
    voltage_critical: float = 11.0
    voltage_low:      float = 11.8
    voltage_ok:       float = 12.4
    voltage_good:     float = 12.8


def load_lpm_config(path: Path) -> LpmConfig:
    """
    Read the lpm_pvpi section from config.json and return an LpmConfig.
    Falls back to dataclass defaults for any missing keys, so the script
    stays runnable even if the config file is absent or incomplete.
    """
    defaults = LpmConfig()

    if not path.exists():
        logging.getLogger("low_power_mode").warning(
            "Config not found at %s — using built-in defaults.", path
        )
        return defaults

    try:
        with path.open("r") as f:
            raw = json.load(f)
    except json.JSONDecodeError as exc:
        logging.getLogger("low_power_mode").error(
            "Could not parse config JSON: %s — using built-in defaults.", exc
        )
        return defaults

    section = raw.get("lpm_pvpi", {})

    return LpmConfig(
        serial_port      = section.get("serial_port",      defaults.serial_port),
        serial_baud      = section.get("baud_rate",        defaults.serial_baud),
        serial_timeout   = section.get("serial_timeout",   defaults.serial_timeout),
        voltage_critical = section.get("voltage_critical", defaults.voltage_critical),
        voltage_low      = section.get("voltage_low",      defaults.voltage_low),
        voltage_ok       = section.get("voltage_ok",       defaults.voltage_ok),
        voltage_good     = section.get("voltage_good",     defaults.voltage_good),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("low_power_mode")


# ─────────────────────────────────────────────────────────────────────────────
# UART helpers
# ─────────────────────────────────────────────────────────────────────────────

def open_serial(cfg: LpmConfig) -> Optional[serial.Serial]:
    """Open the PV Pi UART port. Returns None on failure."""
    try:
        ser = serial.Serial(
            cfg.serial_port,
            cfg.serial_baud,
            timeout=cfg.serial_timeout
        )
        return ser
    except Exception as exc:
        logger.error("Failed to open serial port %s: %s", cfg.serial_port, exc)
        return None


def send_command(ser: serial.Serial, command: str) -> str:
    """
    Send a newline-terminated ASCII command to the PV Pi and return
    the stripped response line. Returns empty string on failure.
    """
    try:
        ser.reset_input_buffer()
        ser.write((command + "\n").encode("ascii"))
        response = ser.readline().decode("ascii").strip()
        return response
    except Exception as exc:
        logger.error("UART command '%s' failed: %s", command, exc)
        return ""


def serial_print(ser: Optional[serial.Serial], message: str):
    """Print to stdout. If a separate debug serial is configured, send there too."""
    print(message, flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Voltage Reading
# ─────────────────────────────────────────────────────────────────────────────

def read_battery_voltage(ser: serial.Serial) -> float:
    """
    Read battery voltage from the PV Pi over UART.

    Sends GET_BAT_V, expects response: MILLIVOLTS,<int>
    Returns voltage in volts, or -1.0 on any error.
    """
    response = send_command(ser, "GET_BAT_V")

    if not response:
        logger.error("No response to GET_BAT_V")
        return -1.0

    if "ERROR" in response:
        logger.error("PV Pi reported error: %s", response)
        return -1.0

    # Expected format: MILLIVOLTS,12500
    parts = response.split(",")
    if len(parts) != 2 or parts[0] != "MILLIVOLTS":
        logger.error("Unexpected GET_BAT_V response: %s", response)
        return -1.0

    try:
        millivolts = int(parts[1])
        return round(millivolts / 1000.0, 3)
    except ValueError:
        logger.error("Could not parse millivolt value from: %s", response)
        return -1.0


def voltage_label(v: float, cfg: LpmConfig) -> str:
    if v < 0:
        return "READ ERROR"
    if v < cfg.voltage_critical:
        return "CRITICAL"
    if v < cfg.voltage_low:
        return "LOW"
    if v < cfg.voltage_ok:
        return "ACCEPTABLE"
    if v < cfg.voltage_good:
        return "OK"
    return "GOOD"


# ─────────────────────────────────────────────────────────────────────────────
# Banner
# ─────────────────────────────────────────────────────────────────────────────

def print_banner(voltage: float, mode: str, timestamp: str, cfg: LpmConfig):
    label = voltage_label(voltage, cfg)
    width = 52
    border = "=" * width

    print("")
    print(border)
    print("  PV Pi — Low Power Mode Manager")
    print(f"  Mode      : {mode}")
    print(f"  Timestamp : {timestamp}")
    print(f"  Battery   : {voltage:.3f} V [{label}]")
    print(border)
    print("")


# ─────────────────────────────────────────────────────────────────────────────
# Sensor Config
# ─────────────────────────────────────────────────────────────────────────────

def load_sensor_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Sensor config not found: {path}")
    try:
        with path.open("r") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON config: %s", exc)
        return {"sensors": []}


def save_sensor_config(path: Path, config: dict):
    with path.open("w") as f:
        json.dump(config, f, indent=2)


def disable_all_sensors(config: dict) -> List[str]:
    changed = []
    for sensor in config.get("sensors", []):
        if sensor.get("enabled", False):
            sensor["enabled"] = False
            changed.append(sensor.get("name", "unnamed"))
    return changed


def enable_all_sensors(config: dict) -> List[str]:
    changed = []
    for sensor in config.get("sensors", []):
        # Default False: sensors missing "enabled" are treated as disabled
        # and will be restored, consistent with disable_all_sensors.
        if not sensor.get("enabled", False):
            sensor["enabled"] = True
            changed.append(sensor.get("name", "unnamed"))
    return changed


# ─────────────────────────────────────────────────────────────────────────────
# Log
# ─────────────────────────────────────────────────────────────────────────────

def log_event(
    timestamp: str,
    mode: str,
    voltage: float,
    affected: List[str],
    cfg: LpmConfig,
):
    try:
        LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE_PATH.open("a") as logfile:
            logfile.write(
                f"[{timestamp}] "
                f"MODE={mode} "
                f"VOLTAGE={voltage:.3f}V "
                f"STATUS={voltage_label(voltage, cfg)} "
                f"AFFECTED={len(affected)} "
                f"LIST={','.join(affected) if affected else 'none'}\n"
            )
        logger.info("Event logged to %s", LOG_FILE_PATH)
    except Exception as exc:
        logger.error("Failed to write log file: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Actions
# ─────────────────────────────────────────────────────────────────────────────

def run_low_power_mode(timestamp: str, voltage: float, cfg: LpmConfig):
    if voltage > cfg.voltage_low:
        print(
            f"[SKIP] Battery at {voltage:.3f}V "
            f"(above {cfg.voltage_low}V) — no action taken."
        )
        log_event(timestamp, "SKIP", voltage, [], cfg)
        return

    print("[ACTION] Entering low-power mode...")

    config = load_sensor_config(SENSOR_CONFIG_PATH)
    disabled = disable_all_sensors(config)
    save_sensor_config(SENSOR_CONFIG_PATH, config)

    if disabled:
        print(f"[SENSORS] Disabled {len(disabled)} sensor(s):")
        for name in disabled:
            print(f"  - {name}")
    else:
        print("[SENSORS] All sensors already disabled.")

    log_event(timestamp, "LOW_POWER", voltage, disabled, cfg)
    print("[DONE] Low-power mode active.")


def run_restore(timestamp: str, voltage: float, cfg: LpmConfig):
    if voltage < 0:
        print("[ERROR] Battery read failed.")
        print("[ABORT] Restore cancelled.")
        return

    if voltage < cfg.voltage_critical:
        print(f"[ABORT] Battery critically low ({voltage:.3f}V) — restore cancelled.")
        return

    print("[ACTION] Restoring sensors...")

    config = load_sensor_config(SENSOR_CONFIG_PATH)
    restored = enable_all_sensors(config)
    save_sensor_config(SENSOR_CONFIG_PATH, config)

    if restored:
        print(f"[SENSORS] Restored {len(restored)} sensor(s):")
        for name in restored:
            print(f"  - {name}")
    else:
        print("[SENSORS] All sensors already enabled.")

    log_event(timestamp, "RESTORE", voltage, restored, cfg)
    print("[DONE] Sensors restored.")


def run_status(voltage: float):
    if voltage < 0:
        print("[WARN] Battery voltage unavailable — UART read failed.")

    config = load_sensor_config(SENSOR_CONFIG_PATH)
    sensors = config.get("sensors", [])

    enabled  = [s["name"] for s in sensors if s.get("enabled")]
    disabled = [s["name"] for s in sensors if not s.get("enabled")]

    print("[STATUS] No changes made.")
    print(
        f"[SENSORS] Enabled  ({len(enabled)}): "
        f"{', '.join(enabled) if enabled else 'none'}"
    )
    print(
        f"[SENSORS] Disabled ({len(disabled)}): "
        f"{', '.join(disabled) if disabled else 'none'}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="PV Pi low-power mode manager"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--restore",
        action="store_true",
        help="Restore all sensors"
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Print status only"
    )
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Load runtime settings (serial + voltage thresholds) from config
    cfg = load_lpm_config(SENSOR_CONFIG_PATH)

    # Open the single UART connection used for everything
    ser = open_serial(cfg)
    if ser is None:
        print("")
        print("=" * 52)
        print("ERROR: Could not open UART port", cfg.serial_port)
        print("Make sure serial is enabled (raspi-config)")
        print("=" * 52)
        print("")
        return

    try:
        voltage = read_battery_voltage(ser)

        # Hard-exit for modes that need a valid voltage reading
        if voltage < 0 and not args.status:
            print("")
            print("=" * 52)
            print("ERROR: Failed to read battery voltage from PV Pi")
            print("=" * 52)
            print("")
            return

        if args.restore:
            mode = "RESTORE"
        elif args.status:
            mode = "STATUS"
        else:
            mode = "LOW POWER"

        print_banner(voltage, mode, timestamp, cfg)

        # Warn on critically low battery in low-power mode only;
        # restore and status have their own handling for this.
        if (
            not args.restore and
            not args.status and
            0 < voltage < cfg.voltage_critical
        ):
            print(f"[WARN] Battery critically low ({voltage:.3f}V)")

        if args.restore:
            run_restore(timestamp, voltage, cfg)
        elif args.status:
            run_status(voltage)
        else:
            run_low_power_mode(timestamp, voltage, cfg)

    finally:
        ser.close()


if __name__ == "__main__":
    main()