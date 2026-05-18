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


# CONFIGURATION
SERIAL_PORT = "/dev/ttyUSB0"
BAUD_RATE = 19200

LOW_POWER_ENTER_V = 12.0
LOW_POWER_EXIT_V = 12.6
CRITICAL_SHUTDOWN_V = 11.8

POLL_INTERVAL_NORMAL = 30
POLL_INTERVAL_LOW = 60
THRESHOLD_DEBOUNCE_COUNT = 2

SENSOR_POWER_PINS = []

I2C_BUSES = [
    1,
]

USB_POWER_PATHS = []

LOW_POWER_STOP_SERVICES = [
    "bluetooth",
    "avahi-daemon",
    "triggerhappy",
]

CPU_GOVERNOR_PATH = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
CPU_MAX_FREQ_PATH = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq"
CPU_FREQ_NORMAL_KHZ = 1000000
CPU_FREQ_LOW_KHZ = 600000

LOG_FILE = "/home/pi/logs/low_power_mode.log"


os.makedirs("/home/pi/logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)

log = logging.getLogger(__name__)

low_power_active = False
running = True

_low_power_enter_count = 0
_low_power_exit_count = 0

_ve_instance = None
_vedirect_data = {}


def handle_signal(sig, frame):
    global running
    log.info("Signal %s received - stopping monitor.", sig)
    running = False


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


def gpio_setup():
    if not GPIO_AVAILABLE or not SENSOR_POWER_PINS:
        return

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    for pin in SENSOR_POWER_PINS:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)

    log.info("GPIO sensor pins initialized: %s", SENSOR_POWER_PINS)


def gpio_set_sensors(powered):
    if not GPIO_AVAILABLE or not SENSOR_POWER_PINS:
        return

    level = GPIO.HIGH if powered else GPIO.LOW

    for pin in SENSOR_POWER_PINS:
        GPIO.output(pin, level)

    state = "ON" if powered else "OFF"
    log.info("GPIO sensor power -> %s", state)


def gpio_cleanup():
    if GPIO_AVAILABLE and SENSOR_POWER_PINS:
        GPIO.cleanup()


def _ve_callback(packet):
    global _vedirect_data
    _vedirect_data = packet


def _get_ve_instance():
    global _ve_instance

    if _ve_instance is not None:
        return _ve_instance

    try:
        _ve_instance = vedirect.Vedirect(SERIAL_PORT, timeout=5)
        log.info("VE.Direct serial connection opened on %s", SERIAL_PORT)
    except Exception as exc:
        log.error("Failed to open VE.Direct port %s: %s", SERIAL_PORT, exc)
        _ve_instance = None

    return _ve_instance


def get_battery_voltage():
    global _ve_instance

    if not VEDIRECT_AVAILABLE:
        log.error("vedirect library not installed. Run: sudo python3 -m pip install vedirect --break-system-packages")
        return None

    ve = _get_ve_instance()

    if ve is None:
        return None

    try:
        ve.read_data_callback(_ve_callback)

        v_mv = _vedirect_data.get("V")

        if v_mv is None:
            log.warning("VE.Direct frame has no V key: %s", _vedirect_data)
            return None

        voltage = int(v_mv) / 1000.0
        return voltage

    except Exception as exc:
        log.error("VE.Direct read error: %s", exc)
        _ve_instance = None
        return None


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


def control_service(name, action):
    try:
        subprocess.run(
            ["systemctl", action, name],
            check=True,
            capture_output=True,
            text=True,
        )
        log.info("Service %s -> %s done", name, action)

    except subprocess.CalledProcessError as exc:
        log.warning("systemctl %s %s failed: %s", action, name, exc.stderr.strip())


def safe_shutdown():
    log.critical("Battery critically low. Shutting down now.")
    gpio_set_sensors(False)
    gpio_cleanup()
    subprocess.run(["shutdown", "-h", "now"])


def set_i2c_buses(enabled):
    if not I2C_BUSES:
        return

    action = "bind" if enabled else "unbind"

    for bus in I2C_BUSES:
        for driver in ["i2c_bcm2835", "i2c_bcm2708"]:
            path = f"/sys/bus/platform/drivers/{driver}/{action}"
            device_id = "fe804000.i2c" if bus == 1 else "fe205000.i2c"

            try:
                with open(path, "w") as f:
                    f.write(device_id)

                log.info("I2C bus %s %s using %s", bus, action, driver)
                break

            except OSError:
                continue


def set_usb_power(enabled):
    if not USB_POWER_PATHS:
        return

    for dev_path in USB_POWER_PATHS:
        auth_path = f"{dev_path}/authorized"
        power_path = f"{dev_path}/power/control"

        if enabled:
            write_sys(auth_path, "1")
            write_sys(power_path, "on")
            log.info("USB power restored: %s", dev_path)
        else:
            write_sys(power_path, "auto")
            write_sys(auth_path, "0")
            log.info("USB power cut: %s", dev_path)


def enter_low_power():
    global low_power_active

    if low_power_active:
        return

    log.info("Entering LOW-POWER mode")

    gpio_set_sensors(False)
    set_i2c_buses(False)
    set_usb_power(False)

    for svc in LOW_POWER_STOP_SERVICES:
        control_service(svc, "stop")

    set_cpu_governor("powersave")
    set_cpu_max_freq(CPU_FREQ_LOW_KHZ)

    with open("/tmp/low_power_active", "w") as f:
        f.write("1")

    low_power_active = True
    log.info("LOW-POWER mode active")


def exit_low_power():
    global low_power_active

    if not low_power_active:
        return

    log.info("Exiting LOW-POWER mode")

    set_cpu_governor("ondemand")
    set_cpu_max_freq(CPU_FREQ_NORMAL_KHZ)

    set_i2c_buses(True)
    set_usb_power(True)
    gpio_set_sensors(True)

    for svc in LOW_POWER_STOP_SERVICES:
        control_service(svc, "start")

    try:
        os.remove("/tmp/low_power_active")
    except FileNotFoundError:
        pass

    low_power_active = False
    log.info("Normal mode restored")


def main():
    global _low_power_enter_count
    global _low_power_exit_count

    if os.geteuid() != 0:
        log.error("Must run as root. Use: sudo python3 low_power_mode.py")
        sys.exit(1)

    gpio_setup()

    log.info(
        "Low-power monitor started | port=%s | enter=%.1fV | exit=%.1fV | shutdown=%.1fV",
        SERIAL_PORT,
        LOW_POWER_ENTER_V,
        LOW_POWER_EXIT_V,
        CRITICAL_SHUTDOWN_V,
    )

    log.info(
        "Sensor control: GPIO pins=%s | I2C buses=%s | USB paths=%s",
        SENSOR_POWER_PINS or "none",
        I2C_BUSES or "none",
        USB_POWER_PATHS or "none",
    )

    try:
        while running:
            voltage = get_battery_voltage()

            if voltage is None:
                log.warning("Voltage read failed this cycle - no action taken.")
                _low_power_enter_count = 0
                _low_power_exit_count = 0

            else:
                log.info("Battery: %.3f V | Low-power: %s", voltage, low_power_active)

                if voltage <= CRITICAL_SHUTDOWN_V:
                    safe_shutdown()
                    break

                elif voltage <= LOW_POWER_ENTER_V and not low_power_active:
                    _low_power_enter_count += 1
                    _low_power_exit_count = 0

                    if _low_power_enter_count >= THRESHOLD_DEBOUNCE_COUNT:
                        enter_low_power()
                        _low_power_enter_count = 0

                elif voltage >= LOW_POWER_EXIT_V and low_power_active:
                    _low_power_exit_count += 1
                    _low_power_enter_count = 0

                    if _low_power_exit_count >= THRESHOLD_DEBOUNCE_COUNT:
                        exit_low_power()
                        _low_power_exit_count = 0

                else:
                    _low_power_enter_count = 0
                    _low_power_exit_count = 0

            interval = POLL_INTERVAL_LOW if low_power_active else POLL_INTERVAL_NORMAL
            time.sleep(interval)

    finally:
        gpio_cleanup()
        log.info("Monitor exited cleanly")


if __name__ == "__main__":
    main()
