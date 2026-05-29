"""
BEAM Motion Camera Burst + Video System
"""

import json
import os
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN_TZ = ZoneInfo("America/New_York")

from gpiozero import Device, MotionSensor, OutputDevice
from gpiozero.exc import BadPinFactory
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FfmpegOutput

try:
    from gpiozero.pins.rpigpio import RPiGPIOFactory

    Device.pin_factory = RPiGPIOFactory()
except Exception:
    # Fall back to gpiozero's default pin factory if RPiGPIO is unavailable.
    pass

# ---------------------------------
# CONSTANT PATHS
# ---------------------------------
LUX_LOG_PATH = "/home/pi/data/tsl2591/lux_data.json"
CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"
CAM_LOG_PATH = "/home/pi/cam.log"


with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

os.makedirs(os.path.dirname(CAM_LOG_PATH), exist_ok=True)
logging.basicConfig(
    filename=CAM_LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("beam_camera")


def log(message: str):
    print(message)
    logger.info(message)

global_config = config.get("global", {})
cam_config = config.get("camera", {})

DELAY_PROFILES = {
    "instant": {
        "cooldown_sec": 0.25,
        "pir_poll_interval_sec": 0.02,
        "pir_sample_rate": 30,
        "pir_queue_len": 1,
        "pir_threshold": 0.3,
    },
    "fast": {
        "cooldown_sec": 0.5,
        "pir_poll_interval_sec": 0.05,
        "pir_sample_rate": 20,
        "pir_queue_len": 1,
        "pir_threshold": 0.5,
    },
    "normal": {
        "cooldown_sec": 1.0,
        "pir_poll_interval_sec": 0.1,
        "pir_sample_rate": 10,
        "pir_queue_len": 1,
        "pir_threshold": 0.5,
    },
    "slow": {
        "cooldown_sec": 2.0,
        "pir_poll_interval_sec": 0.2,
        "pir_sample_rate": 5,
        "pir_queue_len": 2,
        "pir_threshold": 0.5,
    },
}

RANGE_PROFILES = {
    "high": {
        "pir_sample_rate": 30,
        "pir_queue_len": 1,
        "pir_threshold": 0.3,
    },
    "widest": {
        "pir_sample_rate": 20,
        "pir_queue_len": 1,
        "pir_threshold": 0.4,
    },
    "medium": {
        "pir_sample_rate": 10,
        "pir_queue_len": 1,
        "pir_threshold": 0.5,
    },
    "narrow": {
        "pir_sample_rate": 8,
        "pir_queue_len": 2,
        "pir_threshold": 0.7,
    },
}


def normalize_delay_profile(camera_config):
    configured = str(
        camera_config.get(
            "pir_response_profile",
            camera_config.get("motion_delay_profile", "normal"),
        )
    ).lower()
    aliases = {
        "highest": "instant",
        "high": "fast",
        "medium": "normal",
        "default": "normal",
        "low": "slow",
    }
    return aliases.get(configured, configured)


def normalize_range_profile(camera_config):
    configured = str(
        camera_config.get(
            "pir_sensitivity_profile",
            camera_config.get("detection_range_profile", "medium"),
        )
    ).lower()
    aliases = {
        "highest": "high",
        "high": "high",
        "more": "widest",
        "medium": "medium",
        "default": "medium",
        "low": "narrow",
        "narrowest": "narrow",
    }
    return aliases.get(configured, configured)


def build_motion_settings(camera_config):
    delay_profile_name = normalize_delay_profile(camera_config)
    range_profile_name = normalize_range_profile(camera_config)

    settings = {}
    settings.update(DELAY_PROFILES.get(delay_profile_name, DELAY_PROFILES["normal"]))
    settings.update(RANGE_PROFILES.get(range_profile_name, RANGE_PROFILES["medium"]))

    for key in (
        "cooldown_sec",
        "pir_warmup_sec",
        "pir_poll_interval_sec",
        "pir_sample_rate",
        "pir_queue_len",
        "pir_threshold",
    ):
        if key in camera_config:
            settings[key] = camera_config[key]

    settings["motion_delay_profile"] = delay_profile_name
    settings["detection_range_profile"] = range_profile_name
    return settings


motion_settings = build_motion_settings(cam_config)

if not cam_config.get("enabled", False):
    log("[BEAM] Camera module disabled in config.")
    raise SystemExit

node_id = global_config.get("node_id", "unknown-node")
base_dir = global_config.get("base_dir", "/home/pi/data")


def resolve_output_directory(root_dir, configured_dir):
    if configured_dir in (None, "", "."):
        return root_dir
    return os.path.join(root_dir, configured_dir)


directory = resolve_output_directory(base_dir, cam_config.get("directory", "camera"))
directory = os.path.abspath(directory)
os.makedirs(directory, exist_ok=True)

log_path = os.path.join(directory, "images_log.json")

pir_pin = cam_config.get("pir_gpio", cam_config.get("gpio_pin", 4))
try:
    pir = MotionSensor(
        pir_pin,
        pull_up=None,
        active_state=True,
        queue_len=motion_settings.get("pir_queue_len", 1),
        sample_rate=motion_settings.get("pir_sample_rate", 10),
        threshold=motion_settings.get("pir_threshold", 0.5),
    )
except (BadPinFactory, Exception) as e:
    log(f"[BEAM] PIR setup failed on GPIO {pir_pin}: {e}")
    raise

flash_enabled = cam_config.get("flash_enabled", False)
flash = None
if flash_enabled:
    flash_pin = cam_config.get("flash_gpio", 17)
    flash = OutputDevice(flash_pin)

picam = Picamera2()

main_res = tuple(cam_config.get("resolution", [1920, 1080]))
video_res = tuple(cam_config.get("video_resolution", cam_config.get("resolution", [1280, 720])))
still_config = picam.create_still_configuration(main={"size": main_res})
video_config = picam.create_video_configuration(main={"size": video_res})


def configure_camera(mode):
    try:
        picam.stop()
    except Exception:
        pass
    if mode == "video":
        picam.configure(video_config)
    else:
        picam.configure(still_config)
    picam.start()
    time.sleep(1)


configure_camera("still")

if global_config.get("print_debug", True):
    log(f"[BEAM] Burst/video camera armed on GPIO {pir_pin}")
    log(f"[BEAM] Flash enabled: {flash_enabled}")
    log(f"[BEAM] Media directory: {directory}")
    log(f"[BEAM] Media log: {log_path}")
    log(
        "[BEAM] Motion tuning: "
        f"response_profile={motion_settings['motion_delay_profile']}, "
        f"sensitivity_profile={motion_settings['detection_range_profile']}, "
        f"sample_rate={motion_settings.get('pir_sample_rate', 10)}, "
        f"queue_len={motion_settings.get('pir_queue_len', 1)}, "
        f"threshold={motion_settings.get('pir_threshold', 0.5)}, "
        f"poll_interval={motion_settings.get('pir_poll_interval_sec', 0.1)}, "
        f"cooldown={motion_settings.get('cooldown_sec', 1)}"
    )
    log("[BEAM] Warming up PIR...")

cooldown = motion_settings.get("cooldown_sec", 1)
pir_warmup = motion_settings.get("pir_warmup_sec", cam_config.get("pir_warmup_sec", 5))
poll_interval = motion_settings.get("pir_poll_interval_sec", 0.1)

photo_count = 3
photo_pause = float(cam_config.get("motion_photo_pause_sec", 1.0))
video_duration = 10.0
video_bitrate = int(cam_config.get("video_bitrate", 10000000))
video_prefix = cam_config.get("video_file_prefix", "motionvid_")
image_prefix = cam_config.get("file_prefix", "motionpic_")
photo_flash_warmup = float(cam_config.get("motion_photo_flash_warmup_sec", 0.15))
photo_flash_cooldown = float(cam_config.get("motion_photo_flash_cooldown_sec", 0.1))
video_flash_duration = float(cam_config.get("motion_video_flash_duration_sec", 10.0))

time.sleep(pir_warmup)

if global_config.get("print_debug", True):
    log("[BEAM] PIR ready")


def get_latest_lux():
    try:
        with open(LUX_LOG_PATH, "r") as f:
            data = json.load(f)

        if "records" in data and len(data["records"]) > 0:
            return data["records"][-1]["lux"]

    except Exception as e:
        log(f"[BEAM] Lux read error: {e}")

    return None


def should_use_flash(lux_value):
    flash_threshold = cam_config.get("flash_lux_threshold", 10)
    return flash_enabled and flash is not None and lux_value is not None and lux_value < flash_threshold


def set_flash_state(enabled):
    if not flash_enabled or flash is None:
        return
    if enabled:
        flash.on()
    else:
        flash.off()


def capture_photo_with_flash(photo_path, flash_active):
    if flash_active:
        set_flash_state(True)
        log(f"[BEAM] Flash pulse for photo: {os.path.basename(photo_path)}")
        if photo_flash_warmup > 0:
            time.sleep(photo_flash_warmup)

    picam.capture_file(photo_path)

    if flash_active:
        if photo_flash_cooldown > 0:
            time.sleep(photo_flash_cooldown)
        set_flash_state(False)


def record_video_with_flash(video_path, encoder, flash_active):
    configure_camera("video")
    picam.start_recording(encoder, FfmpegOutput(video_path))
    log(f"[BEAM] Recording video: {video_path}")

    end_time = time.monotonic() + max(video_duration, 0.0)

    if flash_active:
        active_flash_time = min(max(video_flash_duration, 0.0), max(video_duration, 0.0))
        if active_flash_time > 0:
            set_flash_state(True)
            log(f"[BEAM] Flash ON for video ({active_flash_time:.1f}s)")
            time.sleep(active_flash_time)
            set_flash_state(False)

        remaining_video_time = max(end_time - time.monotonic(), 0.0)
        if remaining_video_time > 0:
            time.sleep(remaining_video_time)
    else:
        remaining_video_time = max(end_time - time.monotonic(), 0.0)
        if remaining_video_time > 0:
            time.sleep(remaining_video_time)

    picam.stop_recording()


def append_log(record):
    try:
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                try:
                    data = json.load(f)
                    if not isinstance(data, dict) or "records" not in data:
                        data = {"node_id": node_id, "sensor": "camera", "records": []}
                except Exception:
                    data = {"node_id": node_id, "sensor": "camera", "records": []}
        else:
            data = {"node_id": node_id, "sensor": "camera", "records": []}

        data["records"].append(record)

        with open(log_path, "w") as f:
            json.dump(data, f, indent=4)

        log(f"[BEAM] Capture logged to: {log_path}")
    except Exception as e:
        log(f"[ERROR] Failed to save log: {e}")


def ensure_parent_directory(file_path):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)


def build_media_path(file_name):
    safe_name = os.path.basename(file_name)
    media_path = os.path.join(directory, safe_name)
    ensure_parent_directory(media_path)
    return media_path


last_motion_state = pir.motion_detected

while True and cam_config.get("enabled", True):
    current_motion_state = pir.motion_detected

    if current_motion_state and not last_motion_state:
        now_local = datetime.now(EASTERN_TZ)
        event_ts = now_local.strftime("%Y%m%d_%H%M%S%Z")

        if global_config.get("print_debug", True):
            log("[BEAM] Motion detected")

        lux = get_latest_lux()
        flash_active = should_use_flash(lux)

        if flash_active:
            log(f"[BEAM] Night detected (lux={lux}) -> Flash sequence armed")
        else:
            set_flash_state(False)

        photo_files = []

        try:
            configure_camera("still")

            for index in range(photo_count):
                photo_name = f"{image_prefix}{event_ts}_{index + 1}.jpg"
                photo_path = build_media_path(photo_name)
                capture_photo_with_flash(photo_path, flash_active)

                if os.path.exists(photo_path):
                    photo_files.append(photo_path)
                    log(f"[BEAM] Picture saved: {photo_path}")
                else:
                    log(f"[BEAM] Capture finished but file not found: {photo_path}")

                if index < photo_count - 1:
                    time.sleep(photo_pause)

            video_path = build_media_path(f"{video_prefix}{event_ts}.mp4")
            encoder = H264Encoder(bitrate=video_bitrate)

            record_video_with_flash(video_path, encoder, flash_active)

            if os.path.exists(video_path):
                log(f"[BEAM] Video saved: {video_path}")
            else:
                log(f"[BEAM] Recording finished but file not found: {video_path}")

            configure_camera("still")

        except Exception as e:
            log(f"[BEAM] Burst/video capture failed: {e}")
            try:
                picam.stop_recording()
            except Exception:
                pass
            configure_camera("still")
            set_flash_state(False)
            last_motion_state = current_motion_state
            time.sleep(poll_interval)
            continue

        set_flash_state(False)

        record = {
            "timestamp_eastern": now_local.isoformat(),
            "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": now_local.tzname(),
            "files": photo_files,
            "video_file": video_path,
            "photo_count": len(photo_files),
            "photo_pause_sec": photo_pause,
            "video_duration_sec": video_duration,
            "video_flash_duration_sec": min(video_duration, max(video_flash_duration, 0.0)) if flash_active else 0.0,
            "night_mode_flash_used": flash_active,
            "lux": lux,
        }

        append_log(record)
        time.sleep(cooldown)

    elif not current_motion_state and last_motion_state and global_config.get("print_debug", True):
        log("[BEAM] Motion ended")

    last_motion_state = current_motion_state
    time.sleep(poll_interval)
