"""
low_power_mode.py
-----------------
Low-power mode manager for a solar-powered Raspberry Pi Zero (PV Pi).
"""

import argparse
import json
import logging
import time

from datetime import datetime
from pathlib import Path
from typing import List


# ── Configuration ────────────────────────────────────────────────────────────

SENSOR_CONFIG_PATH = Path(
    "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"
)

# Plain text log file
LOG_FILE_PATH = Path("/home/pi/logs/low_power.log")

SERIAL_PORT = None
SERIAL_BAUD = 115200

# "ads1115" | "mcp3008" | "mock"
VOLTAGE_SOURCE = "ads1115"

# ADS1115
ADS1115_ADDRESS = 0x48
VOLTAGE_DIVIDER_RATIO = 2.0

# MCP3008
MCP3008_CHANNEL = 0
MCP3008_VREF = 3.3
MCP3008_BITS = 10

# Battery thresholds
VOLTAGE_CRITICAL = 11.0
VOLTAGE_LOW = 11.8
VOLTAGE_OK = 12.4
VOLTAGE_GOOD = 12.8


# ── Logging setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("low_power_mode")


# ── Battery voltage reading ─────────────────────────────────────────────────

def read_voltage_ads1115() -> float:
    try:
        import smbus2

        bus = smbus2.SMBus(1)

        # ADS1115 config:
        # Single-shot
        # AIN0
        # ±4.096V
        # 128 SPS
        config = [0xC2, 0x83]

        bus.write_i2c_block_data(
            ADS1115_ADDRESS,
            0x01,
            config
        )

        time.sleep(0.01)

        data = bus.read_i2c_block_data(
            ADS1115_ADDRESS,
            0x00,
            2
        )

        bus.close()

        raw = (data[0] << 8) | data[1]

        if raw > 0x7FFF:
            raw -= 0x10000

        volts = raw * 4.096 / 32768.0

        return round(
            volts * VOLTAGE_DIVIDER_RATIO,
            3
        )

    except Exception as exc:
        logger.error("ADS1115 read failed: %s", exc)
        return -1.0


def read_voltage_mcp3008() -> float:
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
        logger.error("MCP3008 read failed: %s", exc)
        return -1.0


def read_voltage_mock() -> float:
    return 12.1


VOLTAGE_READERS = {
    "ads1115": read_voltage_ads1115,
    "mcp3008": read_voltage_mcp3008,
    "mock": read_voltage_mock,
}


def read_battery_voltage() -> float:
    reader = VOLTAGE_READERS.get(
        VOLTAGE_SOURCE,
        read_voltage_mock
    )
    return reader()


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


# ── Serial output ────────────────────────────────────────────────────────────

def get_serial():
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
    line = message + "\r\n"

    print(message)

    if ser:
        try:
            ser.write(line.encode("utf-8"))
            ser.flush()

        except Exception as exc:
            logger.warning(
                "Serial write error: %s",
                exc
            )


# ── Sensor config management ────────────────────────────────────────────────

def load_sensor_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Sensor config not found: {path}"
        )

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


# ── Plain text logging ──────────────────────────────────────────────────────

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


# ── Main operations ─────────────────────────────────────────────────────────

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

    log_event(
        timestamp,
        "LOW_POWER",
        voltage,
        disabled
    )

    serial_print(
        ser,
        f"[DONE] Disabled {len(disabled)} sensor(s)."
    )


def run_restore(
    ser,
    timestamp: str,
    voltage: float
):
    if voltage < VOLTAGE_CRITICAL:

        serial_print(
            ser,
            "[ABORT] Battery critically low. "
            "Restore cancelled."
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

    log_event(
        timestamp,
        "RESTORE",
        voltage,
        restored
    )

    serial_print(
        ser,
        f"[DONE] Restored {len(restored)} sensor(s)."
    )