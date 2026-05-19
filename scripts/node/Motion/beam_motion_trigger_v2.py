#!/usr/bin/env python3
"""
beam_motion_trigger.py

Runs 24/7.
Watches PIR motion sensor.

When motion is detected:
  1. Creates one event folder
  2. Takes 3 images
  3. Records 10-second video
  4. Uses matching hourly AudioMoth audio if available
  5. Otherwise records a new 10-second AudioMoth clip
  6. Combines video + audio

Folder layout:
  /home/pi/data/motion_events/event_TIMESTAMP/
      images/
      video/
      audio/
      combined/
"""

import json
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from gpiozero import MotionSensor


BASE_DIR = Path("/home/pi/BEAMNode_Prototype2")
CONFIG_PATH = BASE_DIR / "config.json"
MOTION_DIR = BASE_DIR / "scripts/node/Motion"

CAMERA_SCRIPT = MOTION_DIR / "camera_motion_capture.py"
AUDIO_SCRIPT = MOTION_DIR / "audiomoth_motion_record.py"

DEFAULT_DATA_DIR = Path("/home/pi/data")

VIDEO_SECONDS = 10
PIR_PIN = 24
PIR_WARMUP_SEC = 5
PIR_POLL_INTERVAL_SEC = 0.05
COOLDOWN_SEC = 5

HOURLY_AUDIO_PREFIX = "recording_"
MOTION_AUDIO_PREFIX = "motionaudio_"
FINAL_VIDEO_PREFIX = "motionvid_audio_"


def load_config():
    if not CONFIG_PATH.exists():
        print(f"Config not found: {CONFIG_PATH}")
        return {}

    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not read config.json: {e}")
        return {}


def get_base_data_dir(config):
    global_config = config.get("global", {})
    return Path(global_config.get("base_dir", str(DEFAULT_DATA_DIR)))


def create_event_dirs(config, timestamp_text):
    base_data_dir = get_base_data_dir(config)
    motion_config = config.get("motion_capture", {})

    generic_folder = motion_config.get("directory", "motion_events")

    event_dir = base_data_dir / generic_folder / f"event_{timestamp_text}"
    images_dir = event_dir / "images"
    video_dir = event_dir / "video"
    audio_dir = event_dir / "audio"
    combined_dir = event_dir / "combined"

    for folder in [images_dir, video_dir, audio_dir, combined_dir]:
        folder.mkdir(parents=True, exist_ok=True)

    return event_dir, images_dir, video_dir, audio_dir, combined_dir


def get_hourly_audio_dir(config):
    base_data_dir = get_base_data_dir(config)
    audio_config = config.get("audio", {})

    audio_dir = base_data_dir / audio_config.get("directory", "audio")
    audio_dir.mkdir(parents=True, exist_ok=True)

    return audio_dir


def parse_timestamp_from_name(path):
    stem = path.stem
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
    audio_config = config.get("audio", {})
    motion_audio_config = config.get("motion_audio", {})

    audio_dir = get_hourly_audio_dir(config)

    hourly_prefix = motion_audio_config.get(
        "hourly_audio_prefix",
        audio_config.get("file_prefix", HOURLY_AUDIO_PREFIX)
    )

    hourly_duration = int(audio_config.get("duration_sec", 300))
    motion_end = motion_start + timedelta(seconds=motion_duration)

    possible_files = []
    possible_files.extend(audio_dir.glob(f"{hourly_prefix}*.wav"))
    possible_files.extend(audio_dir.glob(f"{hourly_prefix}*.flac"))

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
        "ffmpeg",
        "-y",
        "-ss", str(offset_sec),
        "-t", str(duration_sec),
        "-i", str(hourly_audio_file),
        "-acodec", "pcm_s16le",
        str(audio_output)
    ]

    print("Cutting motion audio from hourly AudioMoth file...")
    result = subprocess.run(cmd)

    return result.returncode == 0 and audio_output.exists()


def record_new_motion_audio(audio_output):
    cmd = [
        "python3",
        str(AUDIO_SCRIPT),
        "--output",
        str(audio_output)
    ]

    print("Recording new motion audio clip...")
    result = subprocess.run(cmd)

    return result.returncode == 0 and audio_output.exists()


def run_camera_capture(timestamp_text, images_dir, video_dir):
    video_output = video_dir / f"motionvid_{timestamp_text}.mp4"

    cmd = [
        "python3",
        str(CAMERA_SCRIPT),
        "--timestamp",
        timestamp_text,
        "--images-dir",
        str(images_dir),
        "--video-output",
        str(video_output)
    ]

    print("Running camera capture...")
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print("Camera capture failed.")
        return None

    if not video_output.exists():
        print(f"Video output was not created: {video_output}")
        return None

    return video_output


def merge_video_audio(video_file, audio_file, final_output):
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_file),
        "-i", str(audio_file),
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        str(final_output)
    ]

    print("Merging video and audio...")
    result = subprocess.run(cmd)

    return result.returncode == 0 and final_output.exists()


def handle_motion(config):
    motion_start = datetime.now()
    timestamp_text = motion_start.strftime("%Y%m%d_%H%M%S")

    event_dir, images_dir, video_dir, audio_dir, combined_dir = create_event_dirs(config, timestamp_text)

    print(f"Motion detected at {timestamp_text}")
    print(f"Saving event to: {event_dir}")

    video_file = run_camera_capture(timestamp_text, images_dir, video_dir)

    if video_file is None:
        return

    motion_audio_file = audio_dir / f"{MOTION_AUDIO_PREFIX}{timestamp_text}.wav"

    hourly_audio_file = find_hourly_audio_covering_motion(
        config=config,
        motion_start=motion_start,
        motion_duration=VIDEO_SECONDS
    )

    audio_ready = False

    if hourly_audio_file:
        print(f"Found overlapping hourly audio: {hourly_audio_file}")
        audio_ready = cut_audio_from_hourly(
            hourly_audio_file=hourly_audio_file,
            motion_start=motion_start,
            audio_output=motion_audio_file,
            duration_sec=VIDEO_SECONDS
        )
    else:
        print("No overlapping hourly audio found.")
        audio_ready = record_new_motion_audio(motion_audio_file)

    if not audio_ready:
        print("Audio was not available, keeping video-only file.")
        return

    final_video = combined_dir / f"{FINAL_VIDEO_PREFIX}{timestamp_text}.mp4"

    if merge_video_audio(video_file, motion_audio_file, final_video):
        print(f"Final video with audio created: {final_video}")
    else:
        print("Merge failed. Keeping separate video and audio files.")


def main():
    config = load_config()

    camera_config = config.get("camera", {})
    motion_audio_config = config.get("motion_audio", {})

    if not camera_config.get("enabled", True):
        print("Camera is disabled in config.json.")
        return

    if not motion_audio_config.get("enabled", True):
        print("motion_audio is disabled in config.json.")
        return

    pir_pin = int(camera_config.get("gpio_pin", PIR_PIN))
    warmup = float(camera_config.get("pir_warmup_sec", PIR_WARMUP_SEC))
    poll_interval = float(camera_config.get("pir_poll_interval_sec", PIR_POLL_INTERVAL_SEC))
    cooldown = float(camera_config.get("motion_cooldown_sec", camera_config.get("cooldown_sec", COOLDOWN_SEC)))

    print("Starting 24/7 PIR motion trigger...")
    print(f"PIR GPIO pin: {pir_pin}")
    print(f"Warmup seconds: {warmup}")

    pir = MotionSensor(pir_pin)

    time.sleep(warmup)

    print("Ready. Waiting for motion...")

    while True:
        if pir.motion_detected:
            handle_motion(config)

            print(f"Cooling down for {cooldown} seconds...")
            time.sleep(cooldown)

            print("Waiting for motion to clear...")
            while pir.motion_detected:
                time.sleep(0.2)

            print("Ready again.")

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
