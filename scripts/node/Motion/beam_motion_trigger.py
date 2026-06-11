#!/usr/bin/env python3
"""
beam_motion_trigger.py

Runs 24/7. Watches PIR motion sensor.

When motion is detected:
  1. Creates event folders
  2. Captures high-resolution still photos
  3. Starts raw video and raw audio
  4. Embeds audio into the video

Folder layout:
  /home/pi/data/motion_events/event_TIMESTAMP/
      images/
      video/
      audio/
      combined/
"""

import json
import math
import os
import fcntl
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from gpiozero import Device, MotionSensor

try:
    from gpiozero.pins.rpigpio import RPiGPIOFactory

    Device.pin_factory = RPiGPIOFactory()
except Exception:
    pass

from camera_motion_capture import MotionCameraCapture
from motion_logging import setup_motion_logger


MOTION_DIR   = Path(__file__).resolve().parent
NODE_DIR     = MOTION_DIR.parent
BASE_DIR     = NODE_DIR.parent.parent
CONFIG_PATH  = NODE_DIR / "config.json"
LUX_LOG_PATH = Path("/home/pi/data/tsl2591/lux_data.json")

DEFAULT_DATA_DIR = Path("/home/pi/data")
LOG_DIR = Path("/home/pi/logs")
VIDEO_PROCESSING_LOG_PATH = LOG_DIR / "motion_video_processing.log"
AUDIO_LOCK_PATH = Path("/tmp/beam_audiomoth.lock")

MOTION_AUDIO_PREFIX = "motionaudio_"
FINAL_VIDEO_PREFIX  = "motionvid_audio_"

logger = setup_motion_logger("beam_motion_trigger")

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


def load_config():
    if not CONFIG_PATH.exists():
        logger.warning("Config not found: %s", CONFIG_PATH)
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Could not read config.json: %s", e)
        return {}


def get_base_data_dir(config):
    return Path(config.get("global", {}).get("base_dir", str(DEFAULT_DATA_DIR)))


def low_power_active(config):
    for section_name in ("low_power_mode", "lpm_pvpi"):
        section = config.get(section_name, {})
        if isinstance(section, dict) and section.get("low_power_active", False):
            return True
    return False


def motion_capture_allowed(config):
    if low_power_active(config):
        logger.info("Low power is active in config.json; stopping motion trigger")
        return False
    if not config.get("motion_capture", {}).get("enabled", False):
        logger.info("motion_capture is disabled in config.json; stopping motion trigger")
        return False
    if not config.get("camera", {}).get("enabled", True):
        logger.info("Camera is disabled in config.json; stopping motion trigger")
        return False
    return True


def get_required_number(config_section, key, label, value_type=float):
    if key not in config_section:
        raise ValueError(f"Missing {label} in config.json")
    value = value_type(config_section[key])
    if value <= 0:
        raise ValueError(f"{label} must be greater than 0")
    return value


def get_nonnegative_number(config_section, key, default, value_type=float):
    value = value_type(config_section.get(key, default))
    if value < 0:
        raise ValueError(f"{key} must be 0 or greater")
    return value


def get_pir_gpio(camera_config):
    if "pir_gpio" in camera_config:
        return get_required_number(camera_config, "pir_gpio", "camera.pir_gpio", int)
    return get_required_number(camera_config, "gpio_pin", "camera.gpio_pin", int)


def validate_camera_gpio(camera_config, pir_pin):
    if not camera_config.get("flash_enabled", False):
        return

    flash_pin = int(camera_config.get("flash_gpio", 26))
    if pir_pin == flash_pin:
        raise ValueError(
            "camera.pir_gpio/gpio_pin and camera.flash_gpio cannot both be "
            f"GPIO{pir_pin}. PIR should use GPIO24 and flash should use GPIO26."
        )


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


def validate_motion_settings(settings):
    for key in (
        "cooldown_sec",
        "pir_warmup_sec",
        "pir_poll_interval_sec",
        "pir_sample_rate",
        "pir_queue_len",
        "pir_threshold",
    ):
        if key not in settings:
            raise ValueError(f"Missing camera.{key} in config.json")

    for key in ("cooldown_sec", "pir_warmup_sec", "pir_poll_interval_sec", "pir_sample_rate"):
        if float(settings[key]) <= 0:
            raise ValueError(f"camera.{key} must be greater than 0")

    if int(settings["pir_queue_len"]) <= 0:
        raise ValueError("camera.pir_queue_len must be greater than 0")

    threshold = float(settings["pir_threshold"])
    if threshold <= 0 or threshold > 1:
        raise ValueError("camera.pir_threshold must be greater than 0 and no more than 1")


def create_event_dirs(config, timestamp_text):
    base_data_dir  = get_base_data_dir(config)
    generic_folder = config.get("motion_capture", {}).get("directory", "motion_events")

    event_dir    = base_data_dir / generic_folder / f"event_{timestamp_text}"
    images_dir   = event_dir / "images"
    video_dir    = event_dir / "video"
    audio_dir    = event_dir / "audio"
    combined_dir = event_dir / "combined"

    for folder in [images_dir, video_dir, audio_dir, combined_dir]:
        folder.mkdir(parents=True, exist_ok=True)

    return event_dir, images_dir, video_dir, audio_dir, combined_dir


def get_latest_lux():
    try:
        with open(LUX_LOG_PATH, "r") as f:
            data = json.load(f)

        records = data.get("records", [])
        if records:
            return records[-1].get("lux")
    except Exception as e:
        logger.warning("Lux read error: %s", e)

    return None


def set_tsl_option(sensor, tsl_module, attribute, configured_value, options, default_key):
    configured_key = str(configured_value or default_key).lower()
    constant_name = options.get(configured_key, options[default_key])
    setattr(sensor, attribute, getattr(tsl_module, constant_name))
    return configured_key if configured_key in options else default_key


def read_live_lux(config):
    """Read TSL2591 directly for camera exposure without writing a lux log."""
    try:
        import board
        import adafruit_tsl2591
    except Exception as e:
        logger.warning("Live lux read unavailable; TSL library import failed: %s", e)
        return None

    tsl_config = config.get("tsl2591", {})
    gain_options = {
        "low": "GAIN_LOW",
        "medium": "GAIN_MED",
        "high": "GAIN_HIGH",
        "max": "GAIN_MAX",
    }
    integration_options = {
        "100ms": "INTEGRATIONTIME_100MS",
        "200ms": "INTEGRATIONTIME_200MS",
        "300ms": "INTEGRATIONTIME_300MS",
        "400ms": "INTEGRATIONTIME_400MS",
        "500ms": "INTEGRATIONTIME_500MS",
        "600ms": "INTEGRATIONTIME_600MS",
    }
    integration_wait_sec = {
        "100ms": 0.12,
        "200ms": 0.22,
        "300ms": 0.32,
        "400ms": 0.42,
        "500ms": 0.52,
        "600ms": 0.62,
    }

    try:
        sensor = adafruit_tsl2591.TSL2591(board.I2C())
        gain_name = set_tsl_option(
            sensor,
            adafruit_tsl2591,
            "gain",
            tsl_config.get("gain", "low"),
            gain_options,
            "low",
        )
        integration_name = set_tsl_option(
            sensor,
            adafruit_tsl2591,
            "integration_time",
            tsl_config.get("integration_time", "100ms"),
            integration_options,
            "100ms",
        )
        time.sleep(integration_wait_sec.get(integration_name, 0.12))
        lux = sensor.lux
        logger.info(
            "Live TSL2591 lux for motion exposure: %s (gain=%s integration=%s)",
            lux,
            gain_name,
            integration_name,
        )
        return lux
    except RuntimeError as e:
        if "Overflow reading light channels" in str(e):
            logger.warning("Live TSL2591 lux overflow in current light")
            return None
        logger.warning("Live TSL2591 lux read failed: %s", e)
        return None
    except Exception as e:
        logger.warning("Live TSL2591 lux read failed: %s", e)
        return None


def get_motion_lux(config):
    camera_config = config.get("camera", {})
    if camera_config.get("live_lux_on_motion", True):
        live_lux = read_live_lux(config)
        if live_lux is not None:
            return live_lux

    fallback_lux = get_latest_lux()
    logger.info("Using latest logged lux fallback for motion exposure: %s", fallback_lux)
    return fallback_lux


def should_use_flash(camera_config, camera_capture, lux_value):
    if not camera_capture.flash_available:
        return False

    flash_threshold = camera_config.get("flash_lux_threshold", 10)
    return lux_value is not None and lux_value < flash_threshold


def list_alsa_capture_devices():
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        logger.warning("arecord not found; install alsa-utils for motion audio")
        return []
    except Exception as e:
        logger.warning("Could not list ALSA capture devices: %s", e)
        return []

    if result.returncode != 0:
        logger.warning("arecord -l failed: %s", result.stderr.strip())
        return []

    devices = []
    pattern = re.compile(r"card\s+(\d+):\s+(.+?),\s+device\s+(\d+):\s+(.+)")
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        card_index, card_name, device_index, device_name = match.groups()
        devices.append({
            "card": card_index,
            "device": device_index,
            "card_name": card_name,
            "device_name": device_name,
            "alsa_device": f"plughw:{card_index},{device_index}",
        })

    return devices


def resolve_alsa_device(configured_device):
    if str(configured_device).lower() != "auto":
        return configured_device

    devices = list_alsa_capture_devices()
    if not devices:
        logger.warning("No ALSA capture devices found; falling back to default")
        return "default"

    preferred_terms = ("audiomoth", "usb", "audio")
    for device in devices:
        label = f"{device['card_name']} {device['device_name']}".lower()
        if any(term in label for term in preferred_terms):
            logger.info(
                "Auto-selected ALSA capture device %s (%s / %s)",
                device["alsa_device"],
                device["card_name"],
                device["device_name"],
            )
            return device["alsa_device"]

    selected = devices[0]
    logger.info(
        "Auto-selected first ALSA capture device %s (%s / %s)",
        selected["alsa_device"],
        selected["card_name"],
        selected["device_name"],
    )
    return selected["alsa_device"]


def get_motion_audio_settings(config):
    audio_config = config.get("audio", {})
    motion_audio_config = config.get("motion_audio", {})
    configured_alsa_device = motion_audio_config.get("alsa_device", "auto")
    return {
        "sample_rate": int(motion_audio_config.get(
            "sample_rate", audio_config.get("sample_rate", 48000)
        )),
        "channels": int(motion_audio_config.get(
            "channels", audio_config.get("channels", 1)
        )),
        "alsa_device": resolve_alsa_device(configured_alsa_device),
        "alsa_format": motion_audio_config.get("alsa_format", "S16_LE"),
    }


def write_audio_metadata(metadata_output, metadata):
    if metadata_output is None:
        return
    try:
        metadata_output = Path(metadata_output)
        metadata_output.parent.mkdir(parents=True, exist_ok=True)
        with open(metadata_output, "w") as f:
            json.dump(metadata, f, indent=2)
    except Exception as e:
        logger.warning("Could not write audio metadata %s: %s", metadata_output, e)


def start_motion_audio_recording(config, audio_output, duration_sec, metadata_output=None):
    settings = get_motion_audio_settings(config)
    audio_output = Path(audio_output)
    audio_output.parent.mkdir(parents=True, exist_ok=True)

    lock_fd = os.open(AUDIO_LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        os.chmod(AUDIO_LOCK_PATH, 0o666)
    except OSError:
        pass

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        raise RuntimeError("AudioMoth is already recording; skipping motion audio")

    cmd = [
        "arecord",
        "-D", settings["alsa_device"],
        "-f", settings["alsa_format"],
        "-r", str(settings["sample_rate"]),
        "-c", str(settings["channels"]),
        "-d", str(int(math.ceil(duration_sec))),
        str(audio_output),
    ]
    logger.info("Starting live audio recording before video: %s", audio_output)
    logger.info("Running command: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd)
    except Exception:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        raise

    start_epoch = time.time()
    start_monotonic = time.monotonic()
    write_audio_metadata(metadata_output, {
        "record_start_epoch": start_epoch,
        "requested_duration_sec": duration_sec,
        "output": str(audio_output),
        "command": cmd,
    })
    return proc, start_epoch, start_monotonic, lock_fd


def finish_motion_audio_recording(
    proc,
    audio_output,
    metadata_output,
    start_epoch,
    start_monotonic,
    timeout_sec,
    lock_fd,
):
    try:
        try:
            result = proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            logger.warning("Audio recording timed out; terminating arecord")
            proc.terminate()
            try:
                result = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                result = proc.wait()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    elapsed = time.monotonic() - start_monotonic
    end_epoch = time.time()
    write_audio_metadata(metadata_output, {
        "record_start_epoch": start_epoch,
        "record_end_epoch": end_epoch,
        "elapsed_sec": elapsed,
        "returncode": result,
        "output": str(audio_output),
    })
    logger.info("Audio recording command exited with code %s", result)
    logger.info("Audio recording complete; wall-clock recording time %.3fs", elapsed)
    return result == 0 and Path(audio_output).exists()


def load_audio_metadata(metadata_path):
    try:
        with open(metadata_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Could not read audio metadata %s: %s", metadata_path, e)
        return {}


def run_camera_capture(
    camera_capture,
    timestamp_text,
    images_dir,
    video_dir,
    start_at_epoch,
    flash_active=False,
):
    video_output = video_dir / f"motionvid_{timestamp_text}.mp4"
    logger.info("Running warm-camera video capture: %s", video_output)
    captured_video = camera_capture.record_video(
        video_output,
        start_at_epoch,
        flash_active=flash_active,
    )
    if captured_video is None:
        logger.error("Camera capture failed")
        return None
    if not captured_video.exists():
        logger.error("Video output not created: %s", captured_video)
        return None
    logger.info("Camera capture complete: %s", captured_video)
    return captured_video


def extract_photos_from_video(
    video_file,
    timestamp_text,
    images_dir,
    photo_prefix,
    photo_count,
    first_photo_sec,
    photo_interval_sec,
    jpeg_quality,
    video_duration_sec,
):
    if photo_count <= 0:
        return []

    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    extracted_photos = []
    for index in range(1, photo_count + 1):
        requested_offset = first_photo_sec + ((index - 1) * photo_interval_sec)
        if video_duration_sec and video_duration_sec > 0:
            photo_offset = min(requested_offset, max(video_duration_sec - 0.1, 0.0))
        else:
            photo_offset = requested_offset

        photo_path = images_dir / f"{photo_prefix}{timestamp_text}_{index}.jpg"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{photo_offset:.3f}",
            "-i", str(video_file),
            "-frames:v", "1",
            "-q:v", str(jpeg_quality),
            str(photo_path),
        ]
        logger.info(
            "Extracting motion photo %s from video at %.3fs: %s",
            index,
            photo_offset,
            photo_path,
        )
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            logger.error("ffmpeg not found; could not extract video photos")
            return extracted_photos

        if result.returncode != 0 or not photo_path.exists():
            logger.error("Video photo extraction failed: %s", result.stderr.strip())
            continue

        extracted_photos.append(photo_path)

    return extracted_photos


def append_merge_log(log_path, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(f"{timestamp} {message}\n")
    except Exception as e:
        logger.warning("Could not write merge log %s: %s", log_path, e)


def queue_video_audio_merge(
    video_file,
    audio_file,
    final_output,
    duration_sec,
    video_fps,
    audio_trim_start_sec,
):
    merge_log_path = VIDEO_PROCESSING_LOG_PATH
    merge_job_path = final_output.with_suffix(".merge.json")
    merge_job = {
        "video_file": str(video_file),
        "audio_file": str(audio_file),
        "final_output": str(final_output),
        "duration_sec": float(duration_sec),
        "video_fps": float(video_fps),
        "audio_trim_start_sec": float(audio_trim_start_sec),
        "merge_log_path": str(merge_log_path),
    }

    append_merge_log(
        merge_log_path,
        f"QUEUED video={video_file} audio={audio_file} output={final_output}",
    )
    try:
        with open(merge_job_path, "w") as f:
            json.dump(merge_job, f, indent=2)
    except Exception as e:
        append_merge_log(merge_log_path, f"FAILED_TO_QUEUE {e}")
        logger.warning("Could not queue final video processing; see %s", merge_log_path)
        return

    logger.info("Final video job queued for background processing: %s", final_output)


def handle_motion(config, camera_capture):
    detected_at    = datetime.now()
    timestamp_text = detected_at.strftime("%Y%m%d_%H%M%S")

    try:
        motion_config = config["motion_capture"]
        motion_duration = get_required_number(
            motion_config,
            "duration_sec",
            "motion_capture.duration_sec",
            int,
        )
        video_start_delay = get_nonnegative_number(
            motion_config,
            "video_start_delay_sec",
            motion_config.get("start_delay_sec", 0.25),
        )
        audio_preroll_sec = get_nonnegative_number(
            motion_config,
            "audio_preroll_sec",
            1.0,
        )
        audio_postroll_sec = get_nonnegative_number(
            motion_config,
            "audio_postroll_sec",
            0.5,
        )
        audio_sync_offset_sec = float(
            motion_config.get("audio_sync_offset_sec", 0.0)
        )
    except (KeyError, ValueError) as e:
        logger.error("%s", e)
        return

    event_dir, images_dir, video_dir, audio_dir, combined_dir = \
        create_event_dirs(config, timestamp_text)

    motion_audio_file = audio_dir / f"{MOTION_AUDIO_PREFIX}{timestamp_text}.wav"
    motion_audio_metadata_file = audio_dir / f"{MOTION_AUDIO_PREFIX}{timestamp_text}.json"

    logger.info("Motion detected at %s", timestamp_text)
    logger.info("Saving event to: %s", event_dir)

    camera_config = config.get("camera", {})
    motion_audio_config = config.get("motion_audio", {})
    audio_enabled = motion_audio_config.get("enabled", True)
    picture_config = camera_config.get("pictures", {})
    picture_mode = picture_config.get("mode", "before_video")

    lux = get_motion_lux(config)
    camera_capture.set_lux_exposure(lux)
    flash_active = should_use_flash(camera_config, camera_capture, lux)
    if flash_active:
        logger.info("Night detected, lux=%s. Flash sequence armed", lux)
    else:
        logger.info("Flash not used for event; lux=%s", lux)

    if picture_mode == "before_video":
        logger.info("Capturing high-resolution motion photos before video")
        try:
            photos = camera_capture.capture_photos(
                timestamp_text,
                images_dir,
                flash_active=flash_active,
            )
            logger.info("Captured %s high-resolution motion photos", len(photos))
        except Exception as e:
            logger.exception("High-resolution photo capture failed before video: %s", e)

    target_audio_preroll_sec = max(video_start_delay, audio_preroll_sec)
    audio_duration = (
        motion_duration
        + target_audio_preroll_sec
        + audio_postroll_sec
        + max(audio_sync_offset_sec, 0.0)
    )

    if audio_enabled:
        try:
            audio_proc, audio_start_epoch, audio_start_monotonic, audio_lock_fd = start_motion_audio_recording(
                config,
                motion_audio_file,
                audio_duration,
                motion_audio_metadata_file,
            )
        except Exception as e:
            logger.exception("Audio capture failed to start: %s", e)
            audio_proc = None
            audio_start_epoch = None
            audio_start_monotonic = None
            audio_lock_fd = None
    else:
        logger.info("motion_audio is disabled in config.json; proceeding with photos and video only")
        audio_proc = None
        audio_start_epoch = None
        audio_start_monotonic = None
        audio_lock_fd = None

    if audio_start_epoch is not None:
        start_at_epoch = audio_start_epoch + target_audio_preroll_sec
    else:
        start_at_epoch = time.time() + video_start_delay

    audio_trim_start_sec = target_audio_preroll_sec + audio_sync_offset_sec
    motion_start         = datetime.fromtimestamp(start_at_epoch)

    logger.info(
        "Scheduling synchronized video for %s with duration %ss",
        motion_start.strftime("%Y-%m-%d %H:%M:%S.%f"),
        motion_duration,
    )
    if audio_enabled:
        logger.info(
            "Audio pre-roll %.3fs, post-roll %.3fs, sync offset %.3fs, total audio %.3fs",
            target_audio_preroll_sec,
            audio_postroll_sec,
            audio_sync_offset_sec,
            audio_duration,
        )
    else:
        logger.info("Audio disabled; video will start after %.3fs delay", video_start_delay)

    try:
        video_file = run_camera_capture(
            camera_capture,
            timestamp_text,
            images_dir,
            video_dir,
            start_at_epoch,
            flash_active=flash_active,
        )
        video_duration_sec = camera_capture.last_video_elapsed_sec or motion_duration
        video_start_epoch = camera_capture.last_video_start_epoch
    except Exception as e:
        logger.exception("Camera video capture failed: %s", e)
        video_file = None
        video_duration_sec = motion_duration
        video_start_epoch = None

    if audio_proc is not None:
        audio_ready = finish_motion_audio_recording(
            audio_proc,
            motion_audio_file,
            motion_audio_metadata_file,
            audio_start_epoch,
            audio_start_monotonic,
            audio_duration + 5,
            audio_lock_fd,
        )
    else:
        audio_ready = False

    if video_file is None:
        logger.error("Camera capture failed; no video to merge")
        return

    if picture_mode == "video_frames":
        picture_count = int(picture_config.get("count", camera_capture.picture_count))
        picture_interval_sec = float(
            picture_config.get(
                "video_frame_interval_sec",
                camera_capture.picture_interval_sec,
            )
        )
        first_picture_sec = float(picture_config.get("video_frame_first_sec", 1.0))
        picture_jpeg_quality = int(picture_config.get("video_frame_jpeg_quality", 1))

        logger.info("Extracting motion photos from the recorded video")
        extracted_photos = extract_photos_from_video(
            video_file,
            timestamp_text,
            images_dir,
            camera_capture.photo_prefix,
            picture_count,
            first_picture_sec,
            picture_interval_sec,
            picture_jpeg_quality,
            video_duration_sec,
        )
        logger.info("Extracted %s motion photos from video", len(extracted_photos))

    if not audio_ready:
        if audio_enabled:
            logger.warning("Audio not available; keeping video-only file")
        else:
            logger.info("Audio disabled in config; keeping video-only file")
        return

    audio_metadata = load_audio_metadata(motion_audio_metadata_file)
    audio_record_start_epoch = audio_metadata.get("record_start_epoch")
    if video_start_epoch is not None and audio_record_start_epoch is not None:
        actual_audio_preroll_sec = max(video_start_epoch - audio_record_start_epoch, 0.0)
        audio_trim_start_sec = actual_audio_preroll_sec + audio_sync_offset_sec
        logger.info(
            "Actual sync timing: audio_pre_video=%.3fs offset=%.3fs trim=%.3fs",
            actual_audio_preroll_sec,
            audio_sync_offset_sec,
            audio_trim_start_sec,
        )
    else:
        logger.warning("Actual sync timing unavailable; using scheduled trim %.3fs", audio_trim_start_sec)

    # ── Step 3: Queue audio/video merge in the background ────────────────────
    final_video = combined_dir / f"{FINAL_VIDEO_PREFIX}{timestamp_text}.mp4"
    queue_video_audio_merge(
        video_file,
        motion_audio_file,
        final_video,
        video_duration_sec,
        camera_capture.last_video_fps or camera_capture.video_fps,
        audio_trim_start_sec,
    )


def main():
    config = load_config()

    if not motion_capture_allowed(config):
        return

    camera_config = config.get("camera", {})

    try:
        pir_pin = get_pir_gpio(camera_config)
        validate_camera_gpio(camera_config, pir_pin)
        motion_settings = build_motion_settings(camera_config)
        validate_motion_settings(motion_settings)
        warmup = float(motion_settings["pir_warmup_sec"])
        poll_interval = float(motion_settings["pir_poll_interval_sec"])
        cooldown = float(motion_settings["cooldown_sec"])
        clear_timeout = float(camera_config.get("pir_clear_timeout_sec", 20.0))
        if clear_timeout <= 0:
            clear_timeout = None
    except ValueError as e:
        logger.error("%s", e)
        return

    logger.info("Starting 24/7 PIR motion trigger")
    logger.info("PIR GPIO pin: %s", pir_pin)
    logger.info("Warmup seconds: %s", warmup)
    logger.info("PIR clear timeout: %s", clear_timeout if clear_timeout is not None else "disabled")
    logger.info(
        "Motion tuning: response_profile=%s, sensitivity_profile=%s, "
        "sample_rate=%s, queue_len=%s, threshold=%s, poll_interval=%s, cooldown=%s",
        motion_settings["motion_delay_profile"],
        motion_settings["detection_range_profile"],
        motion_settings["pir_sample_rate"],
        motion_settings["pir_queue_len"],
        motion_settings["pir_threshold"],
        poll_interval,
        cooldown,
    )

    pir = None
    camera_capture = None
    try:
        pir = MotionSensor(
            pir_pin,
            pull_up=None,
            active_state=True,
            queue_len=int(motion_settings["pir_queue_len"]),
            sample_rate=float(motion_settings["pir_sample_rate"]),
            threshold=float(motion_settings["pir_threshold"]),
        )
        camera_capture = MotionCameraCapture(config)
        camera_capture.start()
        time.sleep(warmup)
    except Exception as e:
        logger.exception("Startup failed: %s", e)
        if camera_capture is not None:
            camera_capture.close()
        if pir is not None:
            pir.close()
        return

    logger.info("Camera and PIR are armed. Waiting for motion")

    try:
        while True:
            current_config = load_config()
            if not motion_capture_allowed(current_config):
                break

            if pir.motion_detected:
                handle_motion(current_config, camera_capture)
                logger.info("Cooling down for %s seconds", cooldown)
                time.sleep(cooldown)
                logger.info("Waiting for motion to clear")
                clear_wait_start = time.monotonic()
                while pir.motion_detected:
                    if (
                        clear_timeout is not None
                        and time.monotonic() - clear_wait_start >= clear_timeout
                    ):
                        logger.warning(
                            "PIR still active after %.1fs; re-arming anyway",
                            clear_timeout,
                        )
                        break
                    time.sleep(0.2)
                logger.info("Ready again")
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Motion trigger stopped by user")
    finally:
        if camera_capture is not None:
            camera_capture.close()
        if pir is not None:
            pir.close()


if __name__ == "__main__":
    main()
