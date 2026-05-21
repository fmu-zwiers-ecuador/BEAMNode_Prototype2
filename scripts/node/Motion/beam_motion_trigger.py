#!/usr/bin/env python3
"""
beam_motion_trigger.py

Runs 24/7. Watches PIR motion sensor.

When motion is detected:
  1. Creates event folders
  2. Starts raw video and raw audio immediately
  3. Takes still photos after the video so photos do not delay the clip
  4. Embeds audio into the video

Folder layout:
  /home/pi/data/motion_events/event_TIMESTAMP/
      images/
      video/
      audio/
      combined/
"""

import json
import subprocess
import threading
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

AUDIO_SCRIPT  = MOTION_DIR / "audiomoth_motion_record.py"

DEFAULT_DATA_DIR = Path("/home/pi/data")

MOTION_AUDIO_PREFIX = "motionaudio_"
FINAL_VIDEO_PREFIX  = "motionvid_audio_"

logger = setup_motion_logger("beam_motion_trigger")


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


def should_use_flash(camera_config, camera_capture, lux_value):
    if not camera_capture.flash_available:
        return False

    flash_threshold = camera_config.get("flash_lux_threshold", 10)
    return lux_value is not None and lux_value < flash_threshold


def record_new_motion_audio(audio_output, start_at_epoch):
    cmd = [
        "python3", str(AUDIO_SCRIPT),
        "--output", str(audio_output),
        "--start-at-epoch", str(start_at_epoch),
    ]
    logger.info("Recording new motion audio clip: %s", audio_output)
    result = subprocess.run(cmd)
    logger.info("Audio recording command exited with code %s", result.returncode)
    return result.returncode == 0 and audio_output.exists()


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


def merge_video_audio(video_file, audio_file, final_output, duration_sec):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_file),
        "-i", str(audio_file),
        "-filter_complex",
        (
            f"[0:v]setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop_duration={duration_sec},"
            f"trim=duration={duration_sec},setpts=PTS-STARTPTS[v];"
            f"[1:a]asetpts=PTS-STARTPTS,"
            f"apad=pad_dur={duration_sec},"
            f"atrim=duration={duration_sec},asetpts=PTS-STARTPTS[a]"
        ),
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-c:a", "aac",
        "-t", str(duration_sec),
        str(final_output),
    ]
    logger.info(
        "Embedding audio into video for exactly %ss: video=%s audio=%s output=%s",
        duration_sec,
        video_file,
        audio_file,
        final_output,
    )
    result = subprocess.run(cmd)
    logger.info("ffmpeg merge exited with code %s", result.returncode)
    return result.returncode == 0 and final_output.exists()


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
            0.25,
        )
    except (KeyError, ValueError) as e:
        logger.error("%s", e)
        return

    event_dir, images_dir, video_dir, audio_dir, combined_dir = \
        create_event_dirs(config, timestamp_text)

    start_at_epoch = time.time() + video_start_delay
    motion_start   = datetime.fromtimestamp(start_at_epoch)

    motion_audio_file = audio_dir / f"{MOTION_AUDIO_PREFIX}{timestamp_text}.wav"

    logger.info("Motion detected at %s", timestamp_text)
    logger.info("Saving event to: %s", event_dir)
    logger.info(
        "Scheduling immediate synchronized video/audio for %s with duration %ss",
        motion_start.strftime("%Y-%m-%d %H:%M:%S.%f"),
        motion_duration,
    )

    camera_config = config.get("camera", {})
    lux = get_latest_lux()
    flash_active = should_use_flash(camera_config, camera_capture, lux)
    if flash_active:
        logger.info("Night detected, lux=%s. Flash sequence armed", lux)
    else:
        logger.info("Flash not used for event; lux=%s", lux)

    results = {"video_file": None, "audio_ready": False}

    def camera_thread():
        try:
            results["video_file"] = run_camera_capture(
                camera_capture,
                timestamp_text,
                images_dir,
                video_dir,
                start_at_epoch,
                flash_active=flash_active,
            )
        except Exception as e:
            logger.exception("Camera video capture failed: %s", e)

    def audio_thread():
        try:
            logger.info("Recording synchronized live audio")
            results["audio_ready"] = record_new_motion_audio(
                motion_audio_file,
                start_at_epoch,
            )
        except Exception as e:
            logger.exception("Audio capture failed: %s", e)

    t_camera = threading.Thread(target=camera_thread, daemon=True)
    t_audio  = threading.Thread(target=audio_thread,  daemon=True)

    t_audio.start()
    t_camera.start()

    t_camera.join()
    t_audio.join()

    video_file  = results["video_file"]
    audio_ready = results["audio_ready"]

    if video_file is None:
        logger.error("Camera capture failed; no video to merge")
        return

    logger.info("Capturing motion photos after video")
    try:
        camera_capture.capture_photos(
            timestamp_text,
            images_dir,
            flash_active=flash_active,
        )
    except Exception as e:
        logger.exception("Photo capture failed after video: %s", e)

    if not audio_ready:
        logger.warning("Audio not available; keeping video-only file")
        return

    # ── Step 3: Embed audio into video ───────────────────────────────────────
    final_video = combined_dir / f"{FINAL_VIDEO_PREFIX}{timestamp_text}.mp4"
    if merge_video_audio(video_file, motion_audio_file, final_video, motion_duration):
        logger.info("Final video with embedded audio: %s", final_video)
    else:
        logger.error("Merge failed; keeping separate video and audio files")


def main():
    config = load_config()

    camera_config       = config.get("camera", {})
    motion_audio_config = config.get("motion_audio", {})

    if not camera_config.get("enabled", True):
        logger.info("Camera is disabled in config.json")
        return
    if not motion_audio_config.get("enabled", True):
        logger.info("motion_audio is disabled in config.json")
        return

    try:
        pir_pin = get_pir_gpio(camera_config)
        validate_camera_gpio(camera_config, pir_pin)
        warmup = get_required_number(camera_config, "pir_warmup_sec", "camera.pir_warmup_sec")
        poll_interval = get_required_number(
            camera_config,
            "pir_poll_interval_sec",
            "camera.pir_poll_interval_sec",
        )
        cooldown = get_required_number(camera_config, "cooldown_sec", "camera.cooldown_sec")
    except ValueError as e:
        logger.error("%s", e)
        return

    logger.info("Starting 24/7 PIR motion trigger")
    logger.info("PIR GPIO pin: %s", pir_pin)
    logger.info("Warmup seconds: %s", warmup)

    try:
        camera_capture = MotionCameraCapture(config)
        camera_capture.start()
        pir = MotionSensor(pir_pin)
        time.sleep(warmup)
    except Exception as e:
        logger.exception("Startup failed: %s", e)
        return

    logger.info("Camera and PIR are armed. Waiting for motion")

    try:
        while True:
            if pir.motion_detected:
                handle_motion(config, camera_capture)
                logger.info("Cooling down for %s seconds", cooldown)
                time.sleep(cooldown)
                logger.info("Waiting for motion to clear")
                while pir.motion_detected:
                    time.sleep(0.2)
                logger.info("Ready again")
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Motion trigger stopped by user")
    finally:
        camera_capture.close()


if __name__ == "__main__":
    main()
