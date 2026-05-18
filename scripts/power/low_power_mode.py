#!/usr/bin/env python3
"""
low_power_mode.py
-----------------
Monitors a 12.8V battery via the Victron MPPT 75 (VE.Direct) and toggles
low-power mode on a Raspberry Pi Zero based on voltage thresholds.

Sensor shutdown is handled generically via three mechanisms that cover
virtually any sensor regardless of node/type:

  1. GPIO power pins  — a GPIO drives a transistor/MOSFET/relay cutting
                        sensor rail power entirely (most effective).
  2. I2C bus          — the I2C bus is unbound so devices stop being polled
                        and enter their idle/sleep state.
  3. USB ports        — USB power is cut via sysfs (no uhubctl needed).

Configure the SENSOR_* lists below for your node. You only need to use the
methods that match how your sensors are wired — unused lists can stay empty.

Wiring assumption:
  Victron VE.Direct TX  ->  Pi RX  (GPIO 15 / ttyAMA0, or USB-serial -> ttyUSB0)

Dependencies:
  pip install vedirect pyserial RPi.GPIO

Usage:
  sudo python3 low_power_mode.py
"""

import os
import sys
import time
import logging
import subprocess
import signal

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

try:
    from vedirect import Vedirect
    VEDIRECT_AVAILABLE = True
except ImportError:
    VEDIRECT_AVAILABLE = False

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — edit this section for your node
# ──────────────────────────────────────────────────────────────────────────────

# Serial port for VE.Direct
SERIAL_PORT = "/dev/ttyUSB0"   # or /dev/ttyAMA0 for direct GPIO UART
BAUD_RATE   = 19200

# ── Voltage thresholds (volts) ────────────────────────────────────────────────
# LiFePO4 12.8V nominal -> recommended defaults below
# Lead-acid 12V         -> try ENTER=11.8, EXIT=12.4, SHUTDOWN=11.2
LOW_POWER_ENTER_V   = 12.0   # Below this  -> enter low-power mode
LOW_POWER_EXIT_V    = 12.6   # Above this  -> restore normal mode
CRITICAL_SHUTDOWN_V = 11.8   # Below this  -> safe OS shutdown
#                              FIX: raised from 11.5 -> 11.8 to give the OS
#                              more shutdown time before the BMS hard-cuts power

# ── Polling intervals (seconds) ───────────────────────────────────────────────
POLL_INTERVAL_NORMAL = 30
POLL_INTERVAL_LOW    = 60

# ── Threshold debounce ────────────────────────────────────────────────────────
# Number of consecutive readings required before entering/exiting low-power
# mode. Prevents rapid flickering if voltage hovers near a threshold.
THRESHOLD_DEBOUNCE_COUNT = 2

# ── Sensor power GPIO pins ────────────────────────────────────────────────────
# List BCM-numbered GPIO pins that control sensor power rails.
# Each pin should be wired to the gate of a MOSFET or base of a transistor
# so that HIGH = sensors powered, LOW = sensors off.
#
# Example:  SENSOR_POWER_PINS = [17, 27]
#           means GPIO 17 powers one sensor group, GPIO 27 powers another.
#
# Leave empty if you don't use GPIO-switched power: SENSOR_POWER_PINS = []
SENSOR_POWER_PINS = [
    # 17,   # e.g. 3.3V rail for I2C sensors
    # 27,   # e.g. 5V rail for GPS / camera
]

# ── I2C buses to disable ──────────────────────────────────────────────────────
# List I2C bus numbers to unbind during low-power mode.
# Pi Zero exposes bus 1 (/dev/i2c-1) by default.
# Bus 0 is reserved for internal use — don't add it unless you know why.
#
# Leave empty if not applicable: I2C_BUSES = []
I2C_BUSES = [
    1,    # standard Pi I2C bus
]

# ── USB device sysfs paths to power-cycle ─────────────────────────────────────
# Find paths with:  ls /sys/bus/usb/devices/
# Typical Pi Zero USB OTG hub is usb1/1-1  (the single physical port).
# Add sub-device paths like "1-1.1", "1-1.2" for individual ports on a hub.
#
# Leave empty if no USB sensors: USB_POWER_PATHS = []
USB_POWER_PATHS = [
    # "/sys/bus/usb/devices/1-1",      # entire USB OTG port
    # "/sys/bus/usb/devices/1-1.1",    # first downstream hub port
]

# ── Systemd services to stop ──────────────────────────────────────────────────
# Any sensor daemons or high-draw services managed by systemd.
LOW_POWER_STOP_SERVICES = [
    "bluetooth",
    "avahi-daemon",
    "triggerhappy",
    # "gpsd",
    # "your-sensor-daemon",
]

# ── CPU settings ──────────────────────────────────────────────────────────────
CPU_GOVERNOR_PATH     = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
CPU_MAX_FREQ_PATH     = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq"
CPU_FREQ_NORMAL_KHZ   = 1000000   # 1 GHz  (Pi Zero max)
CPU_FREQ_LOW_KHZ      = 600000    # 600 MHz (stable, ~40% less power)

LOG_FILE = "/var/log/low_power_mode.log"

# ──────────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# STATE
# ──────────────────────────────────────────────────────────────────────────────

low_power_active = False
running = True

# FIX: debounce counters to prevent threshold flickering
_low_power_enter_count = 0
_low_power_exit_count  = 0


def handle_signal(sig, frame):
    global running
    log.info("Signal %s received — stopping monitor.", sig)
    running = False


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT,  handle_signal)

# ──────────────────────────────────────────────────────────────────────────────
# GPIO SETUP
# ──────────────────────────────────────────────────────────────────────────────

def gpio_setup() -> None:
    if not GPIO_AVAILABLE or not SENSOR_POWER_PINS:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in SENSOR_POWER_PINS:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)  # default: sensors on
    log.info("GPIO sensor pins initialised (BCM): %s", SENSOR_POWER_PINS)


def gpio_set_sensors(powered: bool) -> None:
    """Drive all sensor power pins HIGH (on) or LOW (off)."""
    if not GPIO_AVAILABLE or not SENSOR_POWER_PINS:
        return
    level = GPIO.HIGH if powered else GPIO.LOW
    for pin in SENSOR_POWER_PINS:
        GPIO.output(pin, level)
    state = "ON" if powered else "OFF"
    log.info("GPIO sensor power pins -> %s  (pins %s)", state, SENSOR_POWER_PINS)


def gpio_cleanup() -> None:
    if GPIO_AVAILABLE and SENSOR_POWER_PINS:
        GPIO.cleanup()

# ──────────────────────────────────────────────────────────────────────────────
# VICTRON VE.DIRECT READER
# ──────────────────────────────────────────────────────────────────────────────

# FIX: persist the Vedirect instance so the serial port is opened once and
#      reused each poll cycle, preventing file-descriptor leaks and ensuring
#      the callback has a live connection when it fires.
_ve_instance: "Vedirect | None" = None
_vedirect_data: dict = {}


def _ve_callback(packet: dict) -> None:
    global _vedirect_data
    _vedirect_data = packet


def _get_ve_instance() -> "Vedirect | None":
    """Return a cached Vedirect instance, creating it if necessary."""
    global _ve_instance
    if _ve_instance is not None:
        return _ve_instance
    try:
        _ve_instance = Vedirect(SERIAL_PORT, timeout=5)
        log.info("VE.Direct serial connection opened on %s", SERIAL_PORT)
    except Exception as exc:
        log.error("Failed to open VE.Direct port %s: %s", SERIAL_PORT, exc)
        _ve_instance = None
    return _ve_instance


def get_battery_voltage() -> "float | None":
    """Read one VE.Direct frame and return battery voltage in volts, or None."""
    # FIX: guard on library availability with clear error message
    if not VEDIRECT_AVAILABLE:
        log.error("vedirect library not installed. Run: pip install vedirect")
        return None

    ve = _get_ve_instance()
    if ve is None:
        return None

    try:
        # FIX: use the persistent instance; callback populates _vedirect_data
        ve.read_data_callback(_ve_callback)
        v_mv = _vedirect_data.get("V")
        if v_mv is None:
            log.warning("VE.Direct frame has no 'V' key: %s", _vedirect_data)
            return None
        voltage = int(v_mv) / 1000.0
        log.debug("VE.Direct battery voltage: %.3f V", voltage)
        return voltage
    except Exception as exc:
        log.error("VE.Direct read error: %s", exc)
        # FIX: invalidate the cached instance so it is re-opened next cycle
        global _ve_instance
        _ve_instance = None
        return None

# ──────────────────────────────────────────────────────────────────────────────
# SYSTEM HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def write_sys(path: str, value: str) -> bool:
    try:
        with open(path, "w") as f:
            f.write(value)
        return True
    except OSError as exc:
        log.warning("write_sys failed (%s = %s): %s", path, value, exc)
        return False


def set_cpu_governor(governor: str) -> None:
    if write_sys(CPU_GOVERNOR_PATH, governor):
        log.info("CPU governor -> %s", governor)


def set_cpu_max_freq(freq_khz: int) -> None:
    if write_sys(CPU_MAX_FREQ_PATH, str(freq_khz)):
        log.info("CPU max freq -> %d kHz", freq_khz)


def control_service(name: str, action: str) -> None:
    try:
        subprocess.run(["systemctl", action, name], check=True, capture_output=True)
        # FIX: removed naive "%sed" grammar hack that produced "stoped"
        log.info("Service '%s' -> %s: done.", name, action)
    except subprocess.CalledProcessError as exc:
        log.warning("systemctl %s %s failed: %s", action, name, exc.stderr.decode().strip())


def set_hdmi(enabled: bool) -> None:
    """Enable or disable HDMI output. Silently skips if tvservice is absent."""
    # FIX: extracted into a function so both enter and exit paths can call it,
    #      and missing tvservice is now logged instead of silently ignored.
    flag = "-p" if enabled else "-o"
    result = subprocess.run(
        ["/usr/bin/tvservice", flag],
        capture_output=True,
    )
    if result.returncode == 0:
        state = "enabled" if enabled else "disabled"
        log.info("HDMI %s.", state)
    else:
        log.warning(
            "tvservice not available or failed (rc=%d) — HDMI state unchanged.",
            result.returncode,
        )


def safe_shutdown() -> None:
    log.critical("Battery critically low (<= %.1fV) — safe shutdown initiated.", CRITICAL_SHUTDOWN_V)
    gpio_set_sensors(False)   # cut sensor power before OS halts
    gpio_cleanup()
    subprocess.run(["shutdown", "-h", "now"])

# ──────────────────────────────────────────────────────────────────────────────
# SENSOR CONTROL — I2C BUS
# ──────────────────────────────────────────────────────────────────────────────

def set_i2c_buses(enabled: bool) -> None:
    """
    Bind or unbind I2C bus drivers so devices stop being addressed.
    Unbinding lets I2C sensors idle/sleep; rebinding resumes normal comms.
    """
    if not I2C_BUSES:
        return
    action = "bind" if enabled else "unbind"
    state  = "enabled" if enabled else "disabled"
    for bus in I2C_BUSES:
        # The kernel driver name varies; try both common ones.
        for driver in ["i2c_bcm2835", "i2c_bcm2708"]:
            path = f"/sys/bus/platform/drivers/{driver}/{action}"
            # Bus 1 maps to fe804000.i2c on Pi Zero W / Pi 3+
            device_id = "fe804000.i2c" if bus == 1 else "fe205000.i2c"
            try:
                with open(path, "w") as f:
                    f.write(device_id)
                log.info("I2C bus %d %s (driver: %s).", bus, state, driver)
                break
            except OSError:
                continue   # try next driver name

# ──────────────────────────────────────────────────────────────────────────────
# SENSOR CONTROL — USB POWER
# ──────────────────────────────────────────────────────────────────────────────

def set_usb_power(enabled: bool) -> None:
    """
    Enable or disable USB port power via sysfs authorized / power/control.
    Works without uhubctl on Pi Zero (single USB OTG port).
    """
    if not USB_POWER_PATHS:
        return
    for dev_path in USB_POWER_PATHS:
        auth_path  = f"{dev_path}/authorized"
        power_path = f"{dev_path}/power/control"
        if enabled:
            write_sys(auth_path,  "1")
            write_sys(power_path, "on")
            log.info("USB power restored: %s", dev_path)
        else:
            write_sys(power_path, "auto")   # allow kernel to suspend
            write_sys(auth_path,  "0")      # unauthorize = stop enumeration
            log.info("USB power cut: %s", dev_path)

# ──────────────────────────────────────────────────────────────────────────────
# LOW-POWER MODE TRANSITIONS
# ──────────────────────────────────────────────────────────────────────────────

def enter_low_power() -> None:
    global low_power_active
    if low_power_active:
        return
    log.info("Entering LOW-POWER mode  (battery below %.1fV)", LOW_POWER_ENTER_V)

    # 1. Cut GPIO sensor power rails first (immediate, no software delay)
    gpio_set_sensors(False)

    # 2. Disable I2C bus (stops polling; devices enter idle state)
    set_i2c_buses(False)

    # 3. Cut USB sensor power
    set_usb_power(False)

    # 4. Stop sensor + system services
    for svc in LOW_POWER_STOP_SERVICES:
        control_service(svc, "stop")

    # 5. Throttle CPU
    set_cpu_governor("powersave")
    set_cpu_max_freq(CPU_FREQ_LOW_KHZ)

    # 6. Write flag file — external scripts can check os.path.exists("/tmp/low_power_active")
    # FIX: use context manager instead of unclosed open() call
    with open("/tmp/low_power_active", "w") as f:
        pass

    low_power_active = True
    log.info("LOW-POWER mode active. All configured sensors powered off.")


def exit_low_power() -> None:
    global low_power_active
    if not low_power_active:
        return
    log.info("Exiting LOW-POWER mode  (battery above %.1fV)", LOW_POWER_EXIT_V)

    # 1. Restore CPU first so subsequent steps run at full speed
    set_cpu_governor("ondemand")
    set_cpu_max_freq(CPU_FREQ_NORMAL_KHZ)

    # 2. Re-enable I2C bus
    set_i2c_buses(True)

    # 3. Restore USB sensor power
    set_usb_power(True)

    # 4. Power GPIO sensor rails back on
    gpio_set_sensors(True)

    # 5. Restart services
    for svc in LOW_POWER_STOP_SERVICES:
        control_service(svc, "start")

    # 6. Remove flag file
    try:
        os.remove("/tmp/low_power_active")
    except FileNotFoundError:
        pass

    low_power_active = False
    log.info("Normal mode restored. All configured sensors powered on.")

# ──────────────────────────────────────────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    if os.geteuid() != 0:
        log.error("Must run as root (sudo) — required for sysfs, GPIO, and systemctl.")
        sys.exit(1)

    gpio_setup()

    log.info(
        "Low-power monitor started | port=%s | enter=%.1fV | exit=%.1fV | shutdown=%.1fV",
        SERIAL_PORT, LOW_POWER_ENTER_V, LOW_POWER_EXIT_V, CRITICAL_SHUTDOWN_V,
    )
    log.info(
        "Sensor control: GPIO pins=%s | I2C buses=%s | USB paths=%s",
        SENSOR_POWER_PINS or "none",
        I2C_BUSES or "none",
        USB_POWER_PATHS or "none",
    )

    # FIX: debounce counters declared at module level; reset here for clarity
    global _low_power_enter_count, _low_power_exit_count
    _low_power_enter_count = 0
    _low_power_exit_count  = 0

    try:
        while running:
            voltage = get_battery_voltage()

            if voltage is None:
                log.warning("Voltage read failed this cycle — no action taken.")
                # Reset debounce counters on a failed read to avoid acting on
                # incomplete data sequences.
                _low_power_enter_count = 0
                _low_power_exit_count  = 0
            else:
                log.info("Battery: %.3f V  |  Low-power: %s", voltage, low_power_active)

                if voltage <= CRITICAL_SHUTDOWN_V:
                    safe_shutdown()
                    break

                # FIX: debounced threshold transitions
                elif voltage <= LOW_POWER_ENTER_V and not low_power_active:
                    _low_power_enter_count += 1
                    _low_power_exit_count   = 0
                    log.debug(
                        "Low-voltage reading %d/%d before entering low-power mode.",
                        _low_power_enter_count, THRESHOLD_DEBOUNCE_COUNT,
                    )
                    if _low_power_enter_count >= THRESHOLD_DEBOUNCE_COUNT:
                        enter_low_power()
                        _low_power_enter_count = 0

                elif voltage >= LOW_POWER_EXIT_V and low_power_active:
                    _low_power_exit_count  += 1
                    _low_power_enter_count  = 0
                    log.debug(
                        "Recovery reading %d/%d before exiting low-power mode.",
                        _low_power_exit_count, THRESHOLD_DEBOUNCE_COUNT,
                    )
                    if _low_power_exit_count >= THRESHOLD_DEBOUNCE_COUNT:
                        exit_low_power()
                        _low_power_exit_count = 0

                else:
                    # Voltage is in the normal operating band — reset counters
                    _low_power_enter_count = 0
                    _low_power_exit_count  = 0

            interval = POLL_INTERVAL_LOW if low_power_active else POLL_INTERVAL_NORMAL
            time.sleep(interval)

    finally:
        gpio_cleanup()
        log.info("Monitor exited cleanly.")


if __name__ == "__main__":
    main()
