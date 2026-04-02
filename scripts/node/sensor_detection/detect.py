#**** BEAM PROJECT - FRANCIS MARION UNIVERSITY - DETECT . PY ****#
# This script is meant to use Python's subprocess module to 
# scan SPI, I2C, Camera, and USB sensors and updates config.json
# It should return text detailing which sensors are currently online.
#
# Collaborators:
# Alex Lance | Jaylen Small | Jackson Roberts
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
import serial
import serial.tools.list_ports
from picamera2 import Picamera2

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

# ---------------- Config Helper ---------------- #

def set_config_flag(path, section, key, value):
    """Safely set a flag in config.json """
    try:
        with open(path, "r") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    if section not in cfg or not isinstance(cfg[section], dict):
        cfg[section] = {}
    if cfg[section].get(key) != value:
        cfg[section][key] = value
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp_path, path)

# ---------------- Logging Setup ---------------- #

PRIMARY_LOG_DIR = "/home/pi/BEAMNode_Prototype2/logs"
FALLBACK_LOG_DIR = "/tmp/beam_logs"

def get_log_dir():
    try:
        os.makedirs(PRIMARY_LOG_DIR, exist_ok=True)
        test_file = os.path.join(PRIMARY_LOG_DIR, ".writetest")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        return PRIMARY_LOG_DIR
    except Exception as e:
        print(f"[detect] Log directory not writable ({e}), using fallback /tmp", file=sys.stderr)
        os.makedirs(FALLBACK_LOG_DIR, exist_ok=True)
        return FALLBACK_LOG_DIR

LOG_DIR = get_log_dir()
LOG_PATH = os.path.join(LOG_DIR, "detect_bme280.log")

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
    set_config_flag(CONFIG_PATH, "bme280", "enabled", False)
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
            return None
    except Exception as e:
        print("SPI Sensor detection failed")
        spi_logger.exception("SPI detection failed")
        return None
    finally:
        if spi is not None:
            spi.close()
        GPIO.cleanup()
        spi_logger.info("SPI closed and GPIO cleaned up")

# ---------------- Camera (IMX219) ---------------- #

def detect_camera():
    set_config_flag(CONFIG_PATH, "camera", "enabled", False)
    set_config_flag(CONFIG_PATH, "camera", "model", None)

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
    return False

# ---------------- I2C Sensors ---------------- #

# NEW ADDITIONS: atlas_ec (0x64), atlas_orp (0x62), atlas_rtd (0x66)
I2C_ADDR_TABLE = {
    "tsl2591": [0x29], 
    "aht": [0x38], 
    "bme680": [0x77],
    "atlas_orp": [0x62],
    "atlas_ec": [0x64],
    "atlas_rtd": [0x66]
}

CANDIDATE_I2C_BUSES = (1,)

def scan_i2c(busnum):
    try:
        result = subprocess.run(["sudo", "i2cdetect", "-y", str(busnum)],
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
                set_config_flag(CONFIG_PATH, "audio", "mount_path", None)
                return True
    except Exception as e:
        spi_logger.warning(f"AudioMoth detection failed: {e}")
    print("AudioMoth USB Not Found")
    set_config_flag(CONFIG_PATH, "audio", "enabled", False)
    set_config_flag(CONFIG_PATH, "audio", "mount_path", None)
    return False

# ---------------- Anemometer ---------------- #

def detect_anemometer():
    try:
        ports = serial.tools.list_ports.comports()
        detected = False
        for port in ports:
            if "USB" in port.device or "ACM" in port.device:
                try:
                    ser = serial.Serial(port.device, 9600, timeout=1)
                    ser.close()
                    print(f"Arduino/Anemometer detected on {port.device}")
                    set_config_flag(CONFIG_PATH, "anemometer", "enabled", True)
                    detected = True
                    break
                except Exception:
                    continue
        if not detected:
            print("Arduino/Anemometer not detected")
            set_config_flag(CONFIG_PATH, "anemometer", "enabled", False)
    except Exception as e:
        print(f"Anemometer detection failed: {e}")
        set_config_flag(CONFIG_PATH, "anemometer", "enabled", False)


# ---------------- Air Quality (PMS Frame over SPI/UART) ---------------- #

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


def _build_air_spi_targets(air_cfg):
    targets = []

    candidates = air_cfg.get("spi_candidates", [])
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            try:
                targets.append((int(item.get("bus")), int(item.get("device"))))
            except Exception:
                continue

    try:
        targets.insert(0, (int(air_cfg.get("spi_bus", 0)), int(air_cfg.get("spi_device", 1))))
    except Exception:
        pass

    targets.extend([(0, 0), (0, 1), (1, 0), (1, 1)])

    dedup = []
    seen = set()
    for t in targets:
        if t not in seen:
            seen.add(t)
            dedup.append(t)
    return dedup


def _build_air_uart_ports(air_cfg):
    ports = []

    candidates = air_cfg.get("serial_port_candidates", [])
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, str) and item.strip():
                ports.append(item)

    primary = air_cfg.get("serial_port", "/dev/ttyS0")
    if isinstance(primary, str) and primary.strip():
        ports.insert(0, primary)

    ports.extend([
        "/dev/serial0",
        "/dev/ttyAMA0",
        "/dev/ttyS0",
        "/dev/ttyUSB0",
        "/dev/ttyUSB1",
    ])

    dedup = []
    seen = set()
    for p in ports:
        if p not in seen:
            seen.add(p)
            dedup.append(p)
    return dedup


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


def _read_exact_serial(port, size):
    data = bytearray()
    while len(data) < size:
        chunk = port.read(size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def _build_pms_command(cmd, data=0):
    packet = [0x42, 0x4D, cmd, (data >> 8) & 0xFF, data & 0xFF]
    checksum = sum(packet) & 0xFFFF
    packet.extend([(checksum >> 8) & 0xFF, checksum & 0xFF])
    return bytes(packet)


def _prime_pms_uart(port):
    # Wake, force active mode, then request a frame in case the sensor is passive.
    commands = [
        _build_pms_command(0xE4, 0x0001),
        _build_pms_command(0xE1, 0x0001),
        _build_pms_command(0xE2, 0x0000),
    ]
    for command in commands:
        try:
            port.write(command)
            port.flush()
            time.sleep(0.12)
        except Exception:
            pass


def _probe_pm_frame_uart(port_name, baud_rate, timeout_sec, probe_sec):
    if not os.path.exists(port_name):
        return False, f"{port_name}@{baud_rate} missing"

    try:
        with serial.Serial(port_name, baudrate=baud_rate, timeout=timeout_sec) as port:
            try:
                port.reset_input_buffer()
                port.reset_output_buffer()
            except Exception:
                pass

            _prime_pms_uart(port)

            deadline = time.monotonic() + probe_sec
            while time.monotonic() < deadline:
                first = port.read(1)
                if not first or first[0] != 0x42:
                    continue

                second = port.read(1)
                if not second or second[0] != 0x4D:
                    continue

                rest = _read_exact_serial(port, 30)
                if rest is None:
                    continue

                frame = bytes([0x42, 0x4D]) + rest
                if _is_valid_pm_frame(frame):
                    return True, f"valid PM frame on {port_name}@{baud_rate}"

                return False, f"no valid PM frame on {port_name}@{baud_rate} within {probe_sec}s"

            return False, f"no PM header found on {port_name}@{baud_rate} within {probe_sec}s"
    except Exception as e:
            return False, f"{port_name}@{baud_rate} error: {e}"


def _debug_air_quality_uart_candidates():
    # Run an explicit quick check for the most common ports and baud rates.
    candidates = ["/dev/serial0", "/dev/ttyAMA0", "/dev/ttyS0", "/dev/ttyUSB0", "/dev/ttyUSB1"]
    baud_rates = [9600, 115200]
    print("[detect] Air quality UART diagnostic scan...")
    for port_name in candidates:
        for baud in baud_rates:
            ok, detail = _probe_pm_frame_uart(port_name, baud, 2.0, 4.0)
            print(f"[detect] {port_name}@{baud}: {'OK' if ok else 'FAIL'} ({detail})")
    print("[detect] Air quality UART diagnostic complete")


def _probe_pm_frame_spi(bus, dev, mode, speed_hz, probe_bytes, probe_sec, poll_interval_sec):
    dev_path = f"/dev/spidev{bus}.{dev}"
    if not os.path.exists(dev_path):
        return False, f"{dev_path} missing"

    spi = None
    try:
        spi = spidev.SpiDev()
        spi.open(bus, dev)
        spi.mode = mode
        spi.max_speed_hz = speed_hz

        deadline = time.monotonic() + probe_sec
        buf = bytearray()

        while time.monotonic() < deadline:
            rx = spi.xfer2([0x00] * probe_bytes)
            if rx:
                buf.extend(rx)
                if len(buf) > 2048:
                    del buf[:-512]

                max_start = len(buf) - 32
                i = 0
                while i <= max_start:
                    if buf[i] == 0x42 and buf[i + 1] == 0x4D:
                        frame = bytes(buf[i : i + 32])
                        if _is_valid_pm_frame(frame):
                            return True, f"valid PM frame on {dev_path}"
                    i += 1

            if poll_interval_sec > 0:
                time.sleep(poll_interval_sec)

        return False, f"no valid PM frame on {dev_path} within {probe_sec}s"
    except Exception as e:
        return False, f"{dev_path} error: {e}"
    finally:
        if spi is not None:
            try:
                spi.close()
            except Exception:
                pass


def detect_air_quality():
    if os.environ.get("DETECT_AIR_QUALITY_DIAGNOSTIC", "0") == "1":
        _debug_air_quality_uart_candidates()

    air_cfg = _load_air_quality_cfg()
    interface = str(air_cfg.get("interface", "uart")).strip().lower()

    set_config_flag(CONFIG_PATH, "air_quality", "enabled", False)

    if interface == "spi":
        try:
            mode = int(air_cfg.get("spi_mode", 0))
            speed_hz = int(air_cfg.get("spi_max_speed_hz", 500000))
            probe_bytes = int(air_cfg.get("spi_probe_bytes", 32))
            probe_sec = float(air_cfg.get("spi_probe_sec", 6.0))
            poll_interval_sec = float(air_cfg.get("spi_poll_interval_sec", 0.02))
        except Exception:
            mode = 0
            speed_hz = 500000
            probe_bytes = 32
            probe_sec = 6.0
            poll_interval_sec = 0.02

        targets = _build_air_spi_targets(air_cfg)
        misses = []
        for bus, dev in targets:
            ok, detail = _probe_pm_frame_spi(bus, dev, mode, speed_hz, probe_bytes, probe_sec, poll_interval_sec)
            if ok:
                print(f"Air Quality Sensor Found over SPI: /dev/spidev{bus}.{dev}")
                set_config_flag(CONFIG_PATH, "air_quality", "enabled", True)
                set_config_flag(CONFIG_PATH, "air_quality", "spi_bus", bus)
                set_config_flag(CONFIG_PATH, "air_quality", "spi_device", dev)
                return True
            misses.append(detail)
            spi_logger.info(f"Air quality SPI probe miss: {detail}")

        print("Air Quality Sensor Not Found over SPI")
        for miss in misses:
            print(f"  - {miss}")
        return False

    if interface == "uart":
        try:
            baud_rate = int(air_cfg.get("baud_rate", 9600))
            timeout_sec = float(air_cfg.get("read_timeout_sec", 3.0))
            probe_sec = float(air_cfg.get("frame_search_sec", max(6.0, timeout_sec * 2.0)))
            baud_rate_candidates_cfg = air_cfg.get("baud_rate_candidates", [baud_rate, 9600, 115200])
        except Exception:
            baud_rate = 9600
            timeout_sec = 3.0
            probe_sec = 8.0
            baud_rate_candidates_cfg = [9600, 115200]

        baud_rate_candidates = []
        if isinstance(baud_rate_candidates_cfg, list):
            for candidate in baud_rate_candidates_cfg:
                try:
                    baud_rate_candidates.append(int(candidate))
                except Exception:
                    continue

        if not baud_rate_candidates:
            baud_rate_candidates = [baud_rate, 9600, 115200]

        dedup_baud = []
        seen_baud = set()
        for b in baud_rate_candidates:
            if b not in seen_baud:
                seen_baud.add(b)
                dedup_baud.append(b)
        baud_rate_candidates = dedup_baud

        ports = _build_air_uart_ports(air_cfg)
        misses = []
        for baud in baud_rate_candidates:
            for port_name in ports:
                ok, detail = _probe_pm_frame_uart(port_name, baud, timeout_sec, probe_sec)
                if ok:
                    print(f"Air Quality Sensor Found over UART: {port_name} @ {baud}")
                    set_config_flag(CONFIG_PATH, "air_quality", "enabled", True)
                    set_config_flag(CONFIG_PATH, "air_quality", "serial_port", port_name)
                    set_config_flag(CONFIG_PATH, "air_quality", "baud_rate", baud)
                    return True
                misses.append(detail)
                spi_logger.info(f"Air quality UART probe miss: {detail}")

        print("Air Quality Sensor Not Found over UART")
        for miss in misses:
            print(f"  - {miss}")
        return False

    print(f"Air Quality detection skipped (unsupported interface '{interface}')")
    return False
    
# ---------------- Main ---------------- #

print("=== Sensor Detection Summary ===")
detect_spi_sensor()
detect_air_quality()
detect_camera()
detect_i2c_sensors()
detect_audiomoth()
detect_anemometer()
print("=== Detection Complete ===")
