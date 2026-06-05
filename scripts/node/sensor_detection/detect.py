#**** BEAM PROJECT - FRANCIS MARION UNIVERSITY - DETECT . PY ****#
# This script is meant to use Python's subprocess module to 
# scan SPI, I2C, Camera, and USB sensors and updates config.json
# It should return text detailing which sensors are currently online.
#
# Collaborators:
# Alex Lance | Jaylen Small | Jackson Roberts | Noel Challa
#********************************************************************#

import spidev
import RPi.GPIO as GPIO
import subprocess
import logging
import logging.handlers
import re
import os
import time
import json
import sys
import atexit
import serial
import serial.tools.list_ports
from picamera2 import Picamera2

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

# ---------------- Config Helper ---------------- #

def set_config_flag(path, section, key, value):
    """Set config.json flag atomically with logging."""
    try:
        # Load current config
        if os.path.exists(path):
            with open(path, "r") as f:
                config = json.load(f)
        else:
            config = {}

        if section not in config or not isinstance(config.get(section), dict):
            config[section] = {}

        current = config[section].get(key)
        if current == value:
            return

        config[section][key] = value

        # Atomic write
        tmp_path = f"{path}.tmp"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w") as f:
            json.dump(config, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)

        print(f"[detect] Updated {section}.{key} -> {value}")
    except Exception as e:
        print(f"[detect] ERROR writing config: {e}")

# ---------------- Logging Setup ---------------- #

LOG_PATH = "/home/pi/logs/detect.log"
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
_log_file = open(LOG_PATH, "a", buffering=1)
sys.stdout = _log_file
sys.stderr = _log_file
atexit.register(_log_file.close)

spi_logger = logging.getLogger("detect_bme280")
spi_logger.setLevel(logging.INFO)
if not spi_logger.handlers:
    try:
        fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=262_144, backupCount=3)
        fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        spi_logger.addHandler(fh)
    except Exception as e:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        spi_logger.addHandler(sh)
        print(f"[detect] Warning: file logging disabled ({e}). Using console handler.")

# ---------------- SPI (BME/BMP280) ---------------- #

CS_PIN_BME = 5

def spi_init(cs_pin):
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(cs_pin, GPIO.OUT, initial=GPIO.HIGH)
    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 1_000_000
    spi.mode = 0
    try:
        spi.no_cs = True
    except AttributeError:
        spi_logger.warning("spidev.no_cs not available")
    return spi

def read_chip_ID(spi, reg, cs_pin):
    GPIO.output(cs_pin, 0)
    response = spi.xfer2([reg | 0x80, 0x00])[1]
    GPIO.output(cs_pin, 1)
    return response

def detect_spi_sensor():
    spi = None
    try:
        spi = spi_init(CS_PIN_BME)
        spi_logger.info("Starting BME/BMP280 detection")
        chip1 = read_chip_ID(spi, 0xD0, CS_PIN_BME)
        time.sleep(0.002)
        chip2 = read_chip_ID(spi, 0xD0, CS_PIN_BME)
        chip = chip1 if chip1 == chip2 else 0x00
        if chip in (0x60, 0x58):
            name = "BME280" if chip == 0x60 else "BMP280"
            print(f"SPI Sensor Found: {name} (ID 0x{chip:02X})")
            spi_logger.info(f"{name} detected (ID 0x{chip:02X})")
            set_config_flag(CONFIG_PATH, "bme280", "enabled", True)
            return name
        else:
            print(f"SPI Sensor: Unknown or not found (ID 0x{chip:02X})")
            spi_logger.warning(f"Unexpected SPI chip ID 0x{chip:02X}")
            set_config_flag(CONFIG_PATH, "bme280", "enabled", False)
            return None
    except Exception as e:
        print("SPI Sensor detection failed")
        spi_logger.exception("SPI detection failed")
        set_config_flag(CONFIG_PATH, "bme280", "enabled", False)
        return None
    finally:
        if spi is not None:
            spi.close()
        GPIO.cleanup()
        spi_logger.info("SPI closed and GPIO cleaned up")

# ---------------- Camera (IMX219) ---------------- #

def detect_camera():
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        cam_cfg = cfg.get("camera", {})
        device_id = int(cam_cfg.get("device_id", 0))
        expected_model = str(cam_cfg.get("expected_model", "")).strip().lower()
    except Exception:
        device_id = 0
        expected_model = ""

    def _camera_info_matches_device(camera_info, desired_id):
        for key in ("Num", "num", "Id", "id", "Index", "index"):
            value = camera_info.get(key)
            try:
                if value is not None and int(value) == desired_id:
                    return True
            except Exception:
                continue
        return False

    def _extract_camera_model(camera_info):
        for key in (
            "Model",
            "model",
            "SensorModel",
            "sensor_model",
            "Sensor",
            "sensor",
        ):
            value = camera_info.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "unknown"

    try:
        from picamera2 import Picamera2
        cams = Picamera2.global_camera_info()

        selected_camera = None
        if isinstance(cams, list):
            for c in cams:
                if isinstance(c, dict) and _camera_info_matches_device(c, device_id):
                    selected_camera = c
                    break

            if selected_camera is None and len(cams) > device_id:
                selected_camera = cams[device_id]

            if selected_camera is None and cams:
                selected_camera = cams[0]

        if selected_camera is None:
            print("Camera Not Found: picamera2 reported no available cameras")
            set_config_flag(CONFIG_PATH, "motion_capture", "enabled", False)
            set_config_flag(CONFIG_PATH, "camera", "enabled", False)
            set_config_flag(CONFIG_PATH, "camera", "model", None)
            return False

        model = _extract_camera_model(selected_camera)

        picam = None
        last_error = None
        try:
            try:
                picam = Picamera2(device_id)
            except Exception as e:
                last_error = e
                # Fall back to the default constructor, which matches the
                # working camera test scripts in this repo.
                picam = Picamera2()
        finally:
            if picam is not None:
                try:
                    picam.close()
                except Exception:
                    pass
            elif last_error is not None:
                raise last_error

        print(f"Camera Found: {model}")
        set_config_flag(CONFIG_PATH, "motion_capture", "enabled", True)
        set_config_flag(CONFIG_PATH, "camera", "enabled", True)
        set_config_flag(CONFIG_PATH, "camera", "model", model.lower())
        if expected_model and expected_model not in model.lower():
            spi_logger.warning(
                f"Camera model mismatch: expected '{expected_model}', detected '{model.lower()}'"
            )
        return True
    except Exception as e:
        print(f"Camera Not Found: {e}")
        spi_logger.warning(f"Camera detection failed: {e}")
    print("Camera Not Found")
    set_config_flag(CONFIG_PATH, "motion_capture", "enabled", False)
    set_config_flag(CONFIG_PATH, "camera", "enabled", False)
    set_config_flag(CONFIG_PATH, "camera", "model", None)
    return False

# ---------------- I2C Sensors ---------------- #
# NEW ADDITIONS: atlas_ec (0x64), atlas_orp (0x62), atlas_rtd (0x66), atlas_ph (0x63), atlas_do (0x61) - all share the same I2C protocol and can be detected together. See config.json for details.
I2C_ADDR_TABLE = {
    "tsl2591": [0x29], 
    "ahtx0": [0x38], 
    "bme680": [0x77],
    "atlas_orp": [0x62],
    "atlas_ec": [0x64],
    "atlas_rtd": [0x66],
    "atlas_ph": [0x63],
    "atlas_do": [0x61]
}

CANDIDATE_I2C_BUSES = (1,)

def scan_i2c(busnum):
    try:
        result = subprocess.run(["i2cdetect", "-y", str(busnum)],
                                capture_output=True, text=True, check=True)
        return result.stdout
    except Exception as e:
        spi_logger.warning(f"I2C scan failed on bus {busnum}: {e}")
        return ""

def detect_i2c_sensors():
    detected = []  # sensors found on any bus
    # ── Pass 1: scan all buses and record every sensor that is found ──────────
    for bus in CANDIDATE_I2C_BUSES:
        if not os.path.exists(f"/dev/i2c-{bus}"):
            continue
        output = scan_i2c(bus)
        found_addrs = set(int(m, 16) for m in re.findall(r"\b[0-9a-f]{2}\b", output, re.IGNORECASE))

        for name, addrs in I2C_ADDR_TABLE.items():
            if name in detected:
                continue  # already found on an earlier bus — don't overwrite
            for addr in addrs:
                if addr in found_addrs:
                    print(f"I2C Sensor Found: {name} (Bus {bus}, Addr 0x{addr:02X})")
                    set_config_flag(CONFIG_PATH, name, "enabled", True)
                    set_config_flag(CONFIG_PATH, name, "i2c_bus", bus)
                    set_config_flag(CONFIG_PATH, name, "address_hex", f"0x{addr:02X}")
                    detected.append(name)
                    break

    # ── Pass 2: disable anything not found on any bus ─────────────────────────
    for name in I2C_ADDR_TABLE:
        if name not in detected:
            set_config_flag(CONFIG_PATH, name, "enabled", False)
            set_config_flag(CONFIG_PATH, name, "i2c_bus", None)
            set_config_flag(CONFIG_PATH, name, "address_hex", None)

    if not detected:
        print("No I2C sensors detected")
    return detected

# ---------------- AudioMoth USB ---------------- #

def detect_audiomoth():
    try:
        result = subprocess.run(["lsusb"], capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            if "audiomoth" in line.lower():
                print(f"AudioMoth USB Found: {line.strip()}")
                set_config_flag(CONFIG_PATH, "audio", "enabled", True)
                set_config_flag(CONFIG_PATH, "motion_audio", "enabled", True)
                set_config_flag(CONFIG_PATH, "audio", "mount_path", None)
                return True
    except Exception as e:
        spi_logger.warning(f"AudioMoth detection failed: {e}")
    print("AudioMoth USB Not Found")
    set_config_flag(CONFIG_PATH, "audio", "enabled", False)
    set_config_flag(CONFIG_PATH, "motion_audio", "enabled", False)
    set_config_flag(CONFIG_PATH, "audio", "mount_path", None)
    return False

# ---------------- Anemometer ---------------- #

def detect_anemometer():
    try:
        ports = serial.tools.list_ports.comports()
        detected = False
        other_usb_present = False
        for port in ports:
            device = port.device
            if device == "/dev/ttyUSB1":
                try:
                    ser = serial.Serial(device, 9600, timeout=1)
                    ser.close()
                    print(f"Arduino/Anemometer detected on {device}")
                    set_config_flag(CONFIG_PATH, "anemometer", "enabled", True)
                    detected = True
                except Exception:
                    detected = False
            elif device.startswith("/dev/ttyUSB") or device.startswith("/dev/ttyACM"):
                other_usb_present = True

        if detected and not other_usb_present:
            return

        if other_usb_present and not detected:
            print("Arduino/Anemometer not detected: other USB/ACM ports present and /dev/ttyUSB1 not detected")
        else:
            print("Arduino/Anemometer not detected")
        set_config_flag(CONFIG_PATH, "anemometer", "enabled", False)
    except Exception as e:
        print(f"Anemometer detection failed: {e}")
        set_config_flag(CONFIG_PATH, "anemometer", "enabled", False)


# ---------------- Air Quality (PMS Frame over I2C) ---------------- #

def _load_air_quality_cfg():
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        section = cfg.get("air_quality", {})
        if isinstance(section, dict):
            return section
    except Exception:
        pass
    return {}


def _parse_i2c_addr(value, default):
    try:
        if isinstance(value, str):
            return int(value, 0)
        return int(value)
    except Exception:
        return default


def _parse_i2cdetect_addresses(output):
    """Parse i2cdetect output into a set of detected integer addresses."""
    found = set()
    for raw in output.splitlines():
        line = raw.strip()
        m = re.match(r"^([0-9a-f]{2}):\s+(.*)$", line, re.IGNORECASE)
        if not m:
            continue
        cells = m.group(2).split()
        for cell in cells:
            if re.fullmatch(r"[0-9a-f]{2}", cell, re.IGNORECASE):
                found.add(int(cell, 16))
    return found


def _build_air_i2c_targets(air_cfg):
    targets = []

    candidates = air_cfg.get("i2c_candidates", [])
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            bus = _parse_i2c_addr(item.get("bus", 1), 1)
            addr = _parse_i2c_addr(item.get("address", item.get("address_hex", "0x12")), 0x12)
            targets.append((bus, addr))

    primary_bus = _parse_i2c_addr(air_cfg.get("i2c_bus", 1), 1)
    primary_addr = _parse_i2c_addr(
        air_cfg.get("i2c_address", air_cfg.get("address_hex", "0x12")),
        0x12,
    )
    targets.insert(0, (primary_bus, primary_addr))

    # Common PMSA003I default target.
    targets.append((1, 0x12))

    dedup = []
    seen = set()
    for t in targets:
        if t not in seen:
            seen.add(t)
            dedup.append(t)
    return dedup


def _probe_pm_frame_i2c(bus, addr):
    dev_path = f"/dev/i2c-{bus}"
    if not os.path.exists(dev_path):
        return False, f"{dev_path} missing"

    output = scan_i2c(bus)
    if not output:
        return False, f"{dev_path} scan failed"

    found_addrs = _parse_i2cdetect_addresses(output)
    if addr not in found_addrs:
        if found_addrs:
            seen = ", ".join(f"0x{x:02X}" for x in sorted(found_addrs))
            return False, f"0x{addr:02X} not found on {dev_path} (found: {seen})"
        return False, f"0x{addr:02X} not found on {dev_path}"

    return True, f"air quality I2C device present at {dev_path} addr 0x{addr:02X}"


def _is_valid_pm_frame(frame):
    if len(frame) != 32:
        return False
    if frame[0] != 0x42 or frame[1] != 0x4D:
        return False
    if (((frame[2] << 8) | frame[3]) != 28):
        return False

    checksum = sum(frame[0:30]) & 0xFFFF
    expected = (frame[30] << 8) | frame[31]
    return checksum == expected


def _detect_air_quality_i2c(air_cfg):
    targets = _build_air_i2c_targets(air_cfg)
    misses = []
    for bus, addr in targets:
        ok, detail = _probe_pm_frame_i2c(bus, addr)
        if ok:
            print(f"Air Quality Sensor Found over I2C: /dev/i2c-{bus} addr 0x{addr:02X}")
            set_config_flag(CONFIG_PATH, "air_quality", "enabled", True)
            set_config_flag(CONFIG_PATH, "air_quality", "i2c_bus", bus)
            set_config_flag(CONFIG_PATH, "air_quality", "i2c_address", f"0x{addr:02X}")
            set_config_flag(CONFIG_PATH, "air_quality", "address_hex", f"0x{addr:02X}")
            return True
        misses.append(detail)
        spi_logger.info(f"Air quality I2C probe miss: {detail}")

    print("Air Quality Sensor Not Found over I2C")
    for miss in misses:
        print(f"  - {miss}")
    return False


def detect_air_quality():
    air_cfg = _load_air_quality_cfg()
    interface = str(air_cfg.get("interface", "i2c")).strip().lower()

    if interface == "i2c":
        detected = _detect_air_quality_i2c(air_cfg)
        if not detected:
            set_config_flag(CONFIG_PATH, "air_quality", "enabled", False)
        return detected

    print(f"Air Quality detection skipped (unsupported interface '{interface}')")
    set_config_flag(CONFIG_PATH, "air_quality", "enabled", False)
    return False

# ---------------- Ultrasonic (HC-SR04) ---------------- #

TRIG_PIN = 20
ECHO_PIN = 21

def detect_ultrasonic():
    try:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(TRIG_PIN, GPIO.OUT)
        GPIO.setup(ECHO_PIN, GPIO.IN)
        GPIO.output(TRIG_PIN, False)
        time.sleep(2)

        detected = False
        try:
            # Send trigger pulse
            GPIO.output(TRIG_PIN, True)
            time.sleep(0.00001)
            GPIO.output(TRIG_PIN, False)

            timeout = time.time() + 1
            while GPIO.input(ECHO_PIN) == 0:
                pulse_start = time.time()
                if time.time() > timeout:
                    break
            while GPIO.input(ECHO_PIN) == 1:
                pulse_end = time.time()
                detected = True
                if time.time() > timeout:
                    break
        except Exception as e:
            print(f"Ultrasonic Sensor: Error during pulse ({e})")
            spi_logger.warning(f"Ultrasonic pulse error: {e}")

        if detected:
            print(f"Ultrasonic Sensor Found: HC-SR04 (TRIG GPIO{TRIG_PIN}, ECHO GPIO{ECHO_PIN})")
            spi_logger.info(f"HC-SR04 detected on TRIG GPIO{TRIG_PIN} / ECHO GPIO{ECHO_PIN}")
            set_config_flag(CONFIG_PATH, "ultrasonic", "enabled", True)
            set_config_flag(CONFIG_PATH, "ultrasonic", "trig_pin", TRIG_PIN)
            set_config_flag(CONFIG_PATH, "ultrasonic", "echo_pin", ECHO_PIN)
        else:
            print("Ultrasonic Sensor Not Found")
            spi_logger.info("HC-SR04 not detected")
            set_config_flag(CONFIG_PATH, "ultrasonic", "enabled", False)

        return detected
    except Exception as e:
        print(f"Ultrasonic Sensor detection failed: {e}")
        spi_logger.exception("Ultrasonic detection failed")
        set_config_flag(CONFIG_PATH, "ultrasonic", "enabled", False)
        return False
    finally:
        GPIO.cleanup()
        spi_logger.info("GPIO cleaned up after ultrasonic detection")

# ---------------- Main ---------------- #

print("=== Sensor Detection Summary ===")
detect_spi_sensor()
detect_air_quality()
detect_camera()
detect_i2c_sensors()
detect_audiomoth()
detect_anemometer()
detect_ultrasonic()
print("=== Detection Complete ===")
