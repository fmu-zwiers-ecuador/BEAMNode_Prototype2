#!/usr/bin/env python3

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
    import vedirect
    VEDIRECT_AVAILABLE = True
except ImportError:
    VEDIRECT_AVAILABLE = False


# -----------------------------
# CONFIGURATION
# -----------------------------
SERIAL_PORT = "/dev/ttyUSB0"

ENTER_LOW_POWER_V = 12.0
EXIT_LOW_POWER_V = 12.6
SHUTDOWN_V = 11.8

CHECK_INTERVAL_SEC = 30

SENSOR_POWER_PINS = []

CPU_GOVERNOR_PATH = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
CPU_MAX_FREQ_PATH = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq"

LOW_CPU_FREQ_KHZ = "600000"

log = logging.getLogger("low_power")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

_running = True
_low_power = False
_ve_instance = None


def handle_signal(signum, frame):
    global _running
    log.info("Signal %s received - stopping monitor.", signum)
    _running = False


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def require_root():
    if os.geteuid() != 0:
        print("Must run as root. Use: sudo python3 low_power_mode.py")
        sys.exit(1)


def gpio_setup():
    if not GPIO_AVAILABLE or not SENSOR_POWER_PINS:
        return

    GPIO.setmode(GPIO.BCM)

    for pin in SENSOR_POWER_PINS:
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.HIGH)


def gpio_cleanup():
    if GPIO_AVAILABLE and SENSOR_POWER_PINS:
        GPIO.cleanup()


def set_sensors_power(on):
    if not GPIO_AVAILABLE or not SENSOR_POWER_PINS:
        return

    state = GPIO.HIGH if on else GPIO.LOW

    for pin in SENSOR_POWER_PINS:
        GPIO.output(pin, state)

    log.info("Sensor power -> %s", "ON" if on else "OFF")


def write_sys(path, value):
    try:
        with open(path, "w") as f:
            f.write(str(value))
        return True
    except OSError as exc:
        log.warning("Could not write %s = %s: %s", path, value, exc)
        return False


def set_cpu_governor(governor):
    if os.path.exists(CPU_GOVERNOR_PATH):
        if write_sys(CPU_GOVERNOR_PATH, governor):
            log.info("CPU governor -> %s", governor)


def set_cpu_max_freq(freq_khz):
    if os.path.exists(CPU_MAX_FREQ_PATH):
        if write_sys(CPU_MAX_FREQ_PATH, freq_khz):
            log.info("CPU max freq -> %s kHz", freq_khz)


def _get_ve_instance():
    global _ve_instance

    if _ve_instance is not None:
        return _ve_instance

    try:
        _ve_instance = vedirect.VEDirect(SERIAL_PORT)
        log.info("VE.Direct serial connection opened on %s", SERIAL_PORT)
    except Exception as exc:
        log.error("Failed to open VE.Direct port %s: %s", SERIAL_PORT, exc)
        _ve_instance = None

    return _ve_instance


def get_battery_voltage():
    if not VEDIRECT_AVAILABLE:
        log.error("vedirect library not installed. Run: sudo python3 -m pip install vedirect --break-system-packages")
        return None

    ve = _get_ve_instance()

    if ve is None:
        return None

    try:
        ve.read_serial_data()
        voltage = ve.battery_volts

        if voltage is None:
            log.warning("VE.Direct has no battery voltage yet.")
            return None

        return float(voltage)

    except Exception as exc:
        log.error("VE.Direct read error: %s", exc)
        return None


def enter_low_power():
    global _low_power

    if _low_power:
        return

    log.warning("Entering low-power mode.")
    set_sensors_power(False)
    set_cpu_governor("powersave")
    set_cpu_max_freq(LOW_CPU_FREQ_KHZ)
    _low_power = True


def exit_low_power():
    global _low_power

    if not _low_power:
        return

    log.info("Exiting low-power mode.")
    set_sensors_power(True)
    set_cpu_governor("ondemand")
    _low_power = False


def shutdown_pi():
    log.critical("Battery voltage too low. Shutting down.")
    subprocess.run(["shutdown", "-h", "now"])


def main():
    require_root()
    gpio_setup()

    log.info(
        "Low-power monitor started | port=%s | enter=%.1fV | exit=%.1fV | shutdown=%.1fV",
        SERIAL_PORT,
        ENTER_LOW_POWER_V,
        EXIT_LOW_POWER_V,
        SHUTDOWN_V
    )

    log.info(
        "Sensor control: GPIO pins=%s | USB paths=none",
        SENSOR_POWER_PINS if SENSOR_POWER_PINS else "none"
    )

    try:
        while _running:
            voltage = get_battery_voltage()

            if voltage is None:
                log.warning("Voltage read failed this cycle - no action taken.")
            else:
                log.info("Battery voltage: %.2fV", voltage)

                if voltage <= SHUTDOWN_V:
                    shutdown_pi()
                    break

                elif voltage <= ENTER_LOW_POWER_V:
                    enter_low_power()

                elif voltage >= EXIT_LOW_POWER_V:
                    exit_low_power()

            time.sleep(CHECK_INTERVAL_SEC)

    finally:
        gpio_cleanup()
        log.info("Monitor exited cleanly.")


if __name__ == "__main__":
    main()