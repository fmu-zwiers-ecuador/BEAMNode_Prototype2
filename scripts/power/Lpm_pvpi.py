"""
low_power_mode.py
-----------------
Low-power mode manager for a solar-powered Raspberry Pi Zero (PV Pi).
 
Features:
  - Reads battery voltage via ADC (ADS1115 or MCP3008) or a direct GPIO ADC pin
  - Prints battery voltage + status to the serial console (UART / USB-serial)
  - Logs every activation to a timestamped CSV log file
  - Disables all sensors in a JSON config file by flipping "enabled": true → false
  - Optionally re-enables sensors when called with --restore
 
Usage:
  python3 low_power_mode.py              # Enter low-power mode
  python3 low_power_mode.py --restore    # Restore all sensors to enabled
  python3 low_power_mode.py --status     # Print battery voltage only (no changes)
 
Dependencies:
  pip install adafruit-circuitpython-ads1x15 adafruit-blinka   # for ADS1115
  OR
  pip install spidev                                            # for MCP3008
  OR set VOLTAGE_SOURCE = "mock" for testing without hardware.
"""
 
import argparse
import csv
import json
import logging
import os
import sys
import time
import smbus2
from datetime import datetime
from pathlib import Path
 
# ── Configuration ────────────────────────────────────────────────────────────
 
# Path to your sensor config JSON file
SENSOR_CONFIG_PATH = Path("/home/pi/BEAMNode_Prototype2/scripts/node/config.json")
 
# Log file path (CSV)
LOG_FILE_PATH = Path("/home/pi/logs/pvpi/low_power.log")
 
# Serial port used for human-readable output
# On RPi Zero: /dev/serial0 (GPIO UART) or /dev/ttyUSB0 (USB-serial dongle)
# Set to None to use stdout only
SERIAL_PORT = None
SERIAL_BAUD = 115200
 
# Voltage source backend: "ads1115" | "mcp3008" | "mock"
VOLTAGE_SOURCE = "ads1115"
 
# ADS1115 settings (if using ADS1115)
ADS1115_CHANNEL = 0          # A0
ADS1115_GAIN    = 1          # ±4.096 V full-scale
VOLTAGE_DIVIDER_RATIO = 2.0  # R1=R2=10kΩ → Vbat = Vadc * 2
 
# MCP3008 settings (if using MCP3008)
MCP3008_CHANNEL  = 0
MCP3008_VREF     = 3.3
MCP3008_BITS     = 10
 
# Battery thresholds (volts)
VOLTAGE_CRITICAL = 11.0   # Shut-down warning
VOLTAGE_LOW      = 11.8   # Low — low-power mode engaged
VOLTAGE_OK       = 12.4   # Acceptable
VOLTAGE_GOOD     = 12.8   # Healthy
 
# ── Logging setup ─────────────────────────────────────────────────────────────
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("low_power_mode")
 
 
# ── Battery voltage reading ───────────────────────────────────────────────────
 
def read_voltage_ads1115() -> float:
    try:
        bus = smbus2.SMBus(1)

        # Write config register: single-shot, AIN0, ±4.096V gain, 128SPS
        config = [0xC3, 0x83]
        bus.write_i2c_block_data(0x48, 0x01, config)

        time.sleep(0.01)  # wait for conversion

        # Read conversion register
        data = bus.read_i2c_block_data(0x48, 0x00, 2)
        bus.close()

        raw = (data[0] << 8) | data[1]
        if raw > 0x7FFF:
            raw -= 0x10000

        volts = (raw / 32767.0) * 4.096
        return round(volts * VOLTAGE_DIVIDER_RATIO, 3)
    except Exception as exc:
        logger.error("ADS1115 read failed: %s", exc)
        return -1.0
 
 
def read_voltage_mcp3008() -> float:
    """Read battery voltage through an MCP3008 SPI ADC."""
    try:
        import spidev
 
        spi = spidev.SpiDev()
        spi.open(0, 0)
        spi.max_speed_hz = 1_350_000
        adc = spi.xfer2([1, (8 + MCP3008_CHANNEL) << 4, 0])
        spi.close()
        raw = ((adc[1] & 3) << 8) + adc[2]
        voltage = (raw / (2 ** MCP3008_BITS - 1)) * MCP3008_VREF * VOLTAGE_DIVIDER_RATIO
        return round(voltage, 3)
    except Exception as exc:
        logger.error("MCP3008 read failed: %s", exc)
        return -1.0
 
 
def read_voltage_mock() -> float:
    """Return a fake voltage for testing without hardware."""
    return 12.1
 
 
VOLTAGE_READERS = {
    "ads1115": read_voltage_ads1115,
    "mcp3008": read_voltage_mcp3008,
    "mock":    read_voltage_mock,
}
 
 
def read_battery_voltage() -> float:
    reader = VOLTAGE_READERS.get(VOLTAGE_SOURCE, read_voltage_mock)
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
 
 
# ── Serial output ─────────────────────────────────────────────────────────────
 
def get_serial():
    """Return an open serial.Serial object or None if unavailable."""
    if SERIAL_PORT is None:
        return None
    try:
        import serial
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
        return ser
    except Exception as exc:
        logger.warning("Serial port unavailable (%s) — using stdout only.", exc)
        return None
 
 
def serial_print(ser, message: str):
    """Write a line to the serial port AND to stdout."""
    line = message + "\r\n"
    print(message)
    if ser:
        try:
            ser.write(line.encode("utf-8"))
        except Exception as exc:
            logger.warning("Serial write error: %s", exc)
 
 
def print_banner(ser, voltage: float, mode: str, timestamp: str):
    label = voltage_label(voltage)
    width = 52
    border = "=" * width
    serial_print(ser, "")
    serial_print(ser, border)
    serial_print(ser, "  PV Pi — Low Power Mode Manager")
    serial_print(ser, f"  Mode      : {mode}")
    serial_print(ser, f"  Timestamp : {timestamp}")
    serial_print(ser, f"  Battery   : {voltage:.3f} V  [{label}]")
    serial_print(ser, border)
    serial_print(ser, "")
 
 
# ── Sensor config management ──────────────────────────────────────────────────
 
def load_sensor_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Sensor config not found: {path}")
    with path.open("r") as f:
        return json.load(f)
 
 
def save_sensor_config(path: Path, config: dict):
    with path.open("w") as f:
        json.dump(config, f, indent=2)
 
 
def disable_all_sensors(config: dict) -> list[str]:
    """Set enabled=False for every sensor. Returns list of names that were changed."""
    changed = []
    for sensor in config.get("sensors", []):
        if sensor.get("enabled", False):
            sensor["enabled"] = False
            changed.append(sensor.get("name", "unnamed"))
    return changed
 
 
def enable_all_sensors(config: dict) -> list[str]:
    """Set enabled=True for every sensor. Returns list of names that were changed."""
    changed = []
    for sensor in config.get("sensors", []):
        if not sensor.get("enabled", True):
            sensor["enabled"] = True
            changed.append(sensor.get("name", "unnamed"))
    return changed
 
 
# ── CSV logging ───────────────────────────────────────────────────────────────
 
CSV_HEADERS = [
    "timestamp", "mode", "battery_voltage_v", "battery_status",
    "sensors_disabled", "sensors_list",
]
 
 
def log_event(timestamp: str, mode: str, voltage: float, disabled: list[str]):
    write_header = not LOG_FILE_PATH.exists()
 
    with LOG_FILE_PATH.open("a", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp":          timestamp,
            "mode":               mode,
            "battery_voltage_v":  f"{voltage:.3f}",
            "battery_status":     voltage_label(voltage),
            "sensors_disabled":   len(disabled),
            "sensors_list":       "|".join(disabled) if disabled else "none",
        })
 
    logger.info("Event logged to %s", LOG_FILE_PATH)
 
 
# ── Main ──────────────────────────────────────────────────────────────────────
 
def parse_args():
    parser = argparse.ArgumentParser(
        description="PV Pi low-power mode manager"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--restore",
        action="store_true",
        help="Re-enable all sensors in the config and exit",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Print battery voltage and sensor status without making changes",
    )
    return parser.parse_args()
 
 
def run_low_power_mode(ser, timestamp: str, voltage: float):
    if voltage > VOLTAGE_LOW:
        serial_print(ser, f"[SKIP] Battery at {voltage:.3f} V — above threshold "
                         f"({VOLTAGE_LOW} V). No changes made.")
        return
 
    serial_print(ser, f"[ACTION] Battery at {voltage:.3f} V — at or below threshold "
                      f"({VOLTAGE_LOW} V). Entering low-power mode ...")
 
    config = load_sensor_config(SENSOR_CONFIG_PATH)
    disabled = disable_all_sensors(config)
    save_sensor_config(SENSOR_CONFIG_PATH, config)
 
    if disabled:
        serial_print(ser, f"[SENSORS] Disabled {len(disabled)} sensor(s):")
        for name in disabled:
            serial_print(ser, f"          - {name}")
    else:
        serial_print(ser, "[SENSORS] All sensors were already disabled.")
 
    log_event(timestamp, "low_power", voltage, disabled)
    serial_print(ser, f"[LOG]     Event written to {LOG_FILE_PATH}")
    serial_print(ser, "")
    serial_print(ser, "[DONE]    Low-power mode active.")
 
 
def run_restore(ser, timestamp: str, voltage: float):
    serial_print(ser, "[ACTION] Restoring all sensors ...")
 
    config = load_sensor_config(SENSOR_CONFIG_PATH)
    restored = enable_all_sensors(config)
    save_sensor_config(SENSOR_CONFIG_PATH, config)
 
    if restored:
        serial_print(ser, f"[SENSORS] Re-enabled {len(restored)} sensor(s):")
        for name in restored:
            serial_print(ser, f"          - {name}")
    else:
        serial_print(ser, "[SENSORS] All sensors were already enabled.")
 
    log_event(timestamp, "restore", voltage, restored)
    serial_print(ser, f"[LOG]     Event written to {LOG_FILE_PATH}")
    serial_print(ser, "")
    serial_print(ser, "[DONE]    Sensors restored.")
 
 
def run_status(ser, timestamp: str, voltage: float):
    config = load_sensor_config(SENSOR_CONFIG_PATH)
    sensors = config.get("sensors", [])
    enabled  = [s["name"] for s in sensors if s.get("enabled")]
    disabled = [s["name"] for s in sensors if not s.get("enabled")]
 
    serial_print(ser, "[STATUS]  No changes made.")
    serial_print(ser, f"[SENSORS] Enabled  ({len(enabled)}): {', '.join(enabled) or 'none'}")
    serial_print(ser, f"[SENSORS] Disabled ({len(disabled)}): {', '.join(disabled) or 'none'}")
 
 
def main():
    args = parse_args()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
 
    voltage = read_battery_voltage()
 
    if args.restore:
        mode = "RESTORE"
    elif args.status:
        mode = "STATUS"
    else:
        mode = "LOW POWER"
 
    ser = get_serial()
 
    try:
        print_banner(ser, voltage, mode, timestamp)
 
        if voltage > 0 and voltage < VOLTAGE_CRITICAL:
            serial_print(ser, f"[WARN] Battery critically low ({voltage:.3f} V)! "
                              "Consider immediate shutdown.")
 
        if args.restore:
            run_restore(ser, timestamp, voltage)
        elif args.status:
            run_status(ser, timestamp, voltage)
        else:
            run_low_power_mode(ser, timestamp, voltage)
 
    finally:
        if ser:
            ser.close()
 
 
if __name__ == "__main__":
    main()
 