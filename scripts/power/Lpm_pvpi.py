
"""
low_power_mode.py
-----------------

Low-power mode manager for a solar-powered Raspberry Pi Zero (PV Pi).

Features:
  - Reads battery voltage using MCP3008 SPI ADC
  - Prints battery voltage + status to console and optional serial
  - Logs events to a plain-text .log file
  - Disables all sensors in a JSON config
  - Restores sensors with --restore
  - Status-only mode with --status

Usage:
  python3 low_power_mode.py
  python3 low_power_mode.py --restore
  python3 low_power_mode.py --status
"""

import argparse
import json
import logging
import time

from datetime import datetime
from pathlib import Path
from typing import List


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

# Sensor config JSON
SENSOR_CONFIG_PATH = Path(
    "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"
)

# Plain text log file
LOG_FILE_PATH = Path(
    "/home/pi/logs/low_power.log"
)

# Serial settings
# Example:
# SERIAL_PORT = "/dev/serial0"
# SERIAL_PORT = "/dev/ttyUSB0"
SERIAL_PORT = None
SERIAL_BAUD = 115200

# MCP3008 settings
MCP3008_CHANNEL = 0
MCP3008_VREF = 3.3
MCP3008_BITS = 10

# Voltage divider ratio
# Example:
# 10k / 10k divider = 2.0
VOLTAGE_DIVIDER_RATIO = 2.0

# Battery thresholds
VOLTAGE_CRITICAL = 11.0
VOLTAGE_LOW = 11.8
VOLTAGE_OK = 12.4
VOLTAGE_GOOD = 12.8


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
# Voltage Reading
# ─────────────────────────────────────────────────────────────────────────────

def read_battery_voltage() -> float:
    """
    Read battery voltage using MCP3008 SPI ADC.
    """

    try:
        import spidev

        spi = spidev.SpiDev()

        spi.open(0, 0)

        spi.max_speed_hz = 1_350_000

        adc = spi.xfer2([
            1,
            (8 + MCP3008_CHANNEL) << 4,
            0
        ])

        spi.close()

        raw = ((adc[1] & 3) << 8) + adc[2]

        voltage = (
            raw / (2 ** MCP3008_BITS - 1)
        ) * MCP3008_VREF * VOLTAGE_DIVIDER_RATIO

        return round(voltage, 3)

    except Exception as exc:

        logger.error(
            "MCP3008 read failed: %s",
            exc
        )

        return -1.0


def voltage_label(v: float) -> str:

    if v < 0:
        return "READ ERROR"

    if v < VOLTAGE_CRITICAL:
        return "CRITICAL"

    if v < VOLTAGE_LOW:
        return "LOW"

    if v < VOLTAGE_OK:
        return "ACCEPTABLE"

    if v < VOLTAGE_GOOD:
        return "OK"

    return "GOOD"


# ─────────────────────────────────────────────────────────────────────────────
# Serial Output
# ─────────────────────────────────────────────────────────────────────────────

def get_serial():
    """
    Return serial.Serial object or None.
    """

    if SERIAL_PORT is None:
        return None

    try:
        import serial

        ser = serial.Serial(
            SERIAL_PORT,
            SERIAL_BAUD,
            timeout=1
        )

        return ser

    except Exception as exc:

        logger.warning(
            "Serial unavailable (%s) — stdout only.",
            exc
        )

        return None


def serial_print(ser, message: str):
    """
    Print to stdout and serial.
    """

    line = message + "\r\n"

    print(message, flush=True)

    if ser:

        try:
            ser.write(line.encode("utf-8"))
            ser.flush()

        except Exception as exc:

            logger.warning(
                "Serial write error: %s",
                exc
            )


# ─────────────────────────────────────────────────────────────────────────────
# Banner
# ─────────────────────────────────────────────────────────────────────────────

def print_banner(
    ser,
    voltage: float,
    mode: str,
    timestamp: str
):
    label = voltage_label(voltage)

    width = 52
    border = "=" * width

    serial_print(ser, "")
    serial_print(ser, border)
    serial_print(ser, "  PV Pi — Low Power Mode Manager")
    serial_print(ser, f"  Mode      : {mode}")
    serial_print(ser, f"  Timestamp : {timestamp}")
    serial_print(
        ser,
        f"  Battery   : {voltage:.3f} V [{label}]"
    )
    serial_print(ser, border)
    serial_print(ser, "")


# ─────────────────────────────────────────────────────────────────────────────
# Sensor Config
# ─────────────────────────────────────────────────────────────────────────────

def load_sensor_config(path: Path) -> dict:

    if not path.exists():

        raise FileNotFoundError(
            f"Sensor config not found: {path}"
        )

    try:

        with path.open("r") as f:
            return json.load(f)

    except json.JSONDecodeError as exc:

        logger.error(
            "Invalid JSON config: %s",
            exc
        )

        return {"sensors": []}


def save_sensor_config(path: Path, config: dict):

    with path.open("w") as f:
        json.dump(config, f, indent=2)


def disable_all_sensors(config: dict) -> List[str]:

    changed = []

    for sensor in config.get("sensors", []):

        if sensor.get("enabled", False):

            sensor["enabled"] = False

            changed.append(
                sensor.get("name", "unnamed")
            )

    return changed


def enable_all_sensors(config: dict) -> List[str]:

    changed = []

    for sensor in config.get("sensors", []):

        if not sensor.get("enabled", True):

            sensor["enabled"] = True

            changed.append(
                sensor.get("name", "unnamed")
            )

    return changed


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def log_event(
    timestamp: str,
    mode: str,
    voltage: float,
    affected: List[str]
):
    try:

        LOG_FILE_PATH.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        with LOG_FILE_PATH.open("a") as logfile:

            logfile.write(
                f"[{timestamp}] "
                f"MODE={mode} "
                f"VOLTAGE={voltage:.3f}V "
                f"STATUS={voltage_label(voltage)} "
                f"AFFECTED={len(affected)} "
                f"LIST={','.join(affected) if affected else 'none'}\n"
            )

        logger.info(
            "Event logged to %s",
            LOG_FILE_PATH
        )

    except Exception as exc:

        logger.error(
            "Failed to write log file: %s",
            exc
        )


# ─────────────────────────────────────────────────────────────────────────────
# Actions
# ─────────────────────────────────────────────────────────────────────────────

def run_low_power_mode(
    ser,
    timestamp: str,
    voltage: float
):
    if voltage > VOLTAGE_LOW:

        serial_print(
            ser,
            f"[SKIP] Battery at {voltage:.3f}V "
            f"(above {VOLTAGE_LOW}V)"
        )

        return

    serial_print(
        ser,
        "[ACTION] Entering low-power mode..."
    )

    config = load_sensor_config(
        SENSOR_CONFIG_PATH
    )

    disabled = disable_all_sensors(config)

    save_sensor_config(
        SENSOR_CONFIG_PATH,
        config
    )

    if disabled:

        serial_print(
            ser,
            f"[SENSORS] Disabled "
            f"{len(disabled)} sensor(s):"
        )

        for name in disabled:

            serial_print(
                ser,
                f"  - {name}"
            )

    else:

        serial_print(
            ser,
            "[SENSORS] All sensors already disabled."
        )

    log_event(
        timestamp,
        "LOW_POWER",
        voltage,
        disabled
    )

    serial_print(
        ser,
        "[DONE] Low-power mode active."
    )


def run_restore(
    ser,
    timestamp: str,
    voltage: float
):
    if voltage < 0:

        serial_print(
            ser,
            "[ERROR] Battery read failed."
        )

        serial_print(
            ser,
            "[ABORT] Restore cancelled."
        )

        return

    if voltage < VOLTAGE_CRITICAL:

        serial_print(
            ser,
            "[ABORT] Battery critically low."
        )

        return

    serial_print(
        ser,
        "[ACTION] Restoring sensors..."
    )

    config = load_sensor_config(
        SENSOR_CONFIG_PATH
    )

    restored = enable_all_sensors(config)

    save_sensor_config(
        SENSOR_CONFIG_PATH,
        config
    )

    if restored:

        serial_print(
            ser,
            f"[SENSORS] Restored "
            f"{len(restored)} sensor(s):"
        )

        for name in restored:

            serial_print(
                ser,
                f"  - {name}"
            )

    else:

        serial_print(
            ser,
            "[SENSORS] All sensors already enabled."
        )

    log_event(
        timestamp,
        "RESTORE",
        voltage,
        restored
    )

    serial_print(
        ser,
        "[DONE] Sensors restored."
    )


def run_status(
    ser,
    timestamp: str,
    voltage: float
):
    config = load_sensor_config(
        SENSOR_CONFIG_PATH
    )

    sensors = config.get("sensors", [])

    enabled = [
        s["name"]
        for s in sensors
        if s.get("enabled")
    ]

    disabled = [
        s["name"]
        for s in sensors
        if not s.get("enabled")
    ]

    serial_print(
        ser,
        "[STATUS] No changes made."
    )

    serial_print(
        ser,
        f"[SENSORS] Enabled ({len(enabled)}): "
        f"{', '.join(enabled) if enabled else 'none'}"
    )

    serial_print(
        ser,
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

    timestamp = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    voltage = read_battery_voltage()

    if voltage < 0:

        print("")
        print("====================================================")
        print("ERROR: Failed to read battery voltage from MCP3008")
        print("====================================================")
        print("")

        return

    if args.restore:
        mode = "RESTORE"

    elif args.status:
        mode = "STATUS"

    else:
        mode = "LOW POWER"

    ser = get_serial()

    try:

        print_banner(
            ser,
            voltage,
            mode,
            timestamp
        )

        if (
            voltage > 0 and
            voltage < VOLTAGE_CRITICAL
        ):

            serial_print(
                ser,
                f"[WARN] Battery critically low "
                f"({voltage:.3f}V)"
            )

        if args.restore:

            run_restore(
                ser,
                timestamp,
                voltage
            )

        elif args.status:

            run_status(
                ser,
                timestamp,
                voltage
            )

        else:

            run_low_power_mode(
                ser,
                timestamp,
                voltage
            )

    finally:

        if ser:
            ser.close()


if __name__ == "__main__":
    main()