#!/usr/bin/env python3
"""
beam_motion_trigger.py

Runs 24/7. Watches PIR motion sensor.

When motion is detected:
  1. Creates event folders
  2. Pre-warms the camera (2s AE/AWB settle) -- audio does NOT start yet
  3. Fires video+photos and audio in two threads at the SAME instant
  4. Waits for both to finish, then embeds audio into the video

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
from datetime import datetime, timedelta
from pathlib import Path
from gpiozero import MotionSensor

from motion_logging import setup_motion_logger


MOTION_DIR   = Path(__file__).resolve().parent
NODE_DIR     = MOTION_DIR.parent
BASE_DIR     = NODE_DIR.parent.parent
CONFIG_PATH  = NODE_DIR / "config.json"

CAMERA_SCRIPT = MOTION_DIR / "camera_motion_capture.py"
AUDIO_SCRIPT  = MOTION_DIR / "audiomoth_motion_record.py"

DEFAULT_DATA_DIR = Path("/home/pi/data")

VIDEO_SECONDS         = 10
PIR_PIN               = 24
PIR_WARMUP_SEC        = 5
PIR_POLL_INTERVAL_SEC = 0.05
COOLDOWN_SEC          = 5

HOURLY_AUDIO_PREFIX = "recording_"
MOTION_AUDIO_PREFIX = "motionaudio_"
FINAL_VIDEO_PREFIX  = "motionvid_audio_"

CAPTURE_START_DELAY_SEC = 4

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


def get_hourly_audio_dir(config):
    base_data_dir = get_base_data_dir(config)
    audio_dir = base_data_dir / config.get("audio", {}).get("directory", "audio")
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir


def parse_timestamp_from_name(path):
    stem  = path.stem
    parts = stem.split("_")
    candidates = []
    if len(parts) >= 3:
        candidates.append(parts[-2] + "_" + parts[-1])
    if len(parts) >= 2:
        candidates.append(parts[-1])
    for text in candidates:
        for fmt in ("%Y%m%d_%H%M%S", "%Y-%m-%d_%H-%M-%S", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
    return None


def find_hourly_audio_covering_motion(config, motion_start, motion_duration):
    audio_config        = config.get("audio", {})
    motion_audio_config = config.get("motion_audio", {})
    audio_dir           = get_hourly_audio_dir(config)

    hourly_prefix   = motion_audio_config.get(
        "hourly_audio_prefix",
        audio_config.get("file_prefix", HOURLY_AUDIO_PREFIX),
    )
    hourly_duration = int(audio_config.get("duration_sec", 300))
    motion_end      = motion_start + timedelta(seconds=motion_duration)

    possible_files = (
        list(audio_dir.glob(f"{hourly_prefix}*.wav")) +
        list(audio_dir.glob(f"{hourly_prefix}*.flac"))
    )

    newest_match = None
    for audio_file in possible_files:
        audio_start = parse_timestamp_from_name(audio_file)
        if audio_start is None:
            continue
        audio_end = audio_start + timedelta(seconds=hourly_duration)
        if audio_start <= motion_start and motion_end <= audio_end:
            if newest_match is None or audio_file.stat().st_mtime > newest_match.stat().st_mtime:
                newest_match = audio_file

    return newest_match


def cut_audio_from_hourly(hourly_audio_file, motion_start, audio_output, duration_sec):
    audio_start = parse_timestamp_from_name(hourly_audio_file)
    if audio_start is None:
        return False
    offset_sec = (motion_start - audio_start).total_seconds()
    if offset_sec < 0:
        return False
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(offset_sec),
        "-t",  str(duration_sec),
        "-i",  str(hourly_audio_file),
        "-acodec", "pcm_s16le",
        str(audio_output),
    ]
    logger.info("Cutting motion audio from hourly AudioMoth file: %s", hourly_audio_file)
    result = subprocess.run(cmd)
    logger.info("ffmpeg audio cut exited with code %s", result.returncode)
    return result.returncode == 0 and audio_output.exists()


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


def run_camera_capture(timestamp_text, images_dir, video_dir, start_at_epoch):
    video_output = video_dir / f"motionvid_{timestamp_text}.mp4"
    cmd = [
        "python3", str(CAMERA_SCRIPT),
        "--timestamp",    timestamp_text,
        "--images-dir",   str(images_dir),
        "--video-output", str(video_output),
        "--start-at-epoch", str(start_at_epoch),
    ]
    logger.info("Running camera capture: %s", video_output)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        logger.error("Camera capture failed with code %s", result.returncode)
        return None
    if not video_output.exists():
        logger.error("Video output not created: %s", video_output)
        return None
    logger.info("Camera capture complete: %s", video_output)
    return video_output


def merge_video_audio(video_file, audio_file, final_output):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_file),
        "-i", str(audio_file),
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        str(final_output),
    ]
    logger.info("Embedding audio into video: video=%s audio=%s output=%s", video_file, audio_file, final_output)
    result = subprocess.run(cmd)
    logger.info("ffmpeg merge exited with code %s", result.returncode)
    return result.returncode == 0 and final_output.exists()


def handle_motion(config):
    detected_at    = datetime.now()
    timestamp_text = detected_at.strftime("%Y%m%d_%H%M%S")
    start_at_epoch = time.time() + CAPTURE_START_DELAY_SEC
    motion_start   = datetime.fromtimestamp(start_at_epoch)

    event_dir, images_dir, video_dir, audio_dir, combined_dir = \
        create_event_dirs(config, timestamp_text)

    logger.info("Motion detected at %s", timestamp_text)
    logger.info("Saving event to: %s", event_dir)

    motion_audio_file = audio_dir / f"{MOTION_AUDIO_PREFIX}{timestamp_text}.wav"
    hourly_audio_file = find_hourly_audio_covering_motion(
        config=config,
        motion_start=motion_start,
        motion_duration=VIDEO_SECONDS,
    )

    logger.info(
        "Scheduling synchronized raw capture for %s",
        motion_start.strftime("%Y-%m-%d %H:%M:%S.%f"),
    )

    results = {"video_file": None, "audio_ready": False}

    def camera_thread():
        results["video_file"] = run_camera_capture(
            timestamp_text,
            images_dir,
            video_dir,
            start_at_epoch,
        )

    def audio_thread():
        if hourly_audio_file:
            logger.info("Found overlapping hourly audio: %s", hourly_audio_file)
            results["audio_ready"] = cut_audio_from_hourly(
                hourly_audio_file=hourly_audio_file,
                motion_start=motion_start,
                audio_output=motion_audio_file,
                duration_sec=VIDEO_SECONDS,
            )
        else:
            logger.info("No overlapping hourly audio; recording live audio")
            results["audio_ready"] = record_new_motion_audio(
                motion_audio_file,
                start_at_epoch,
            )

    t_camera = threading.Thread(target=camera_thread, daemon=True)
    t_audio  = threading.Thread(target=audio_thread,  daemon=True)

    t_camera.start()
    t_audio.start()

    t_camera.join()
    t_audio.join()

    video_file  = results["video_file"]
    audio_ready = results["audio_ready"]

    if video_file is None:
        logger.error("Camera capture failed; no video to merge")
        return

    if not audio_ready:
        logger.warning("Audio not available; keeping video-only file")
        return

    # ── Step 3: Embed audio into video ───────────────────────────────────────
    final_video = combined_dir / f"{FINAL_VIDEO_PREFIX}{timestamp_text}.mp4"
    if merge_video_audio(video_file, motion_audio_file, final_video):
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

    pir_pin       = int(camera_config.get("gpio_pin",               PIR_PIN))
    warmup        = float(camera_config.get("pir_warmup_sec",       PIR_WARMUP_SEC))
    poll_interval = float(camera_config.get("pir_poll_interval_sec", PIR_POLL_INTERVAL_SEC))
    cooldown      = float(camera_config.get("motion_cooldown_sec",
                          camera_config.get("cooldown_sec", COOLDOWN_SEC)))

    logger.info("Starting 24/7 PIR motion trigger")
    logger.info("PIR GPIO pin: %s", pir_pin)
    logger.info("Warmup seconds: %s", warmup)

    pir = MotionSensor(pir_pin)
    time.sleep(warmup)
    logger.info("Ready. Waiting for motion")

    while True:
        if pir.motion_detected:
            handle_motion(config)
            logger.info("Cooling down for %s seconds", cooldown)
            time.sleep(cooldown)
            logger.info("Waiting for motion to clear")
            while pir.motion_detected:
                time.sleep(0.2)
            logger.info("Ready again")
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
