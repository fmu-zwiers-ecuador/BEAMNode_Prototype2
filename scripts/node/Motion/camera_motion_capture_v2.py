#!/usr/bin/env python3
"""
camera_motion_capture.py

Takes:
  - 3 pictures, 1 second apart
  - 10 second video

Uses picamera2.
Does not handle PIR directly. It only runs when beam_motion_trigger.py calls it.
"""

import argparse
import json
import time
from pathlib import Path

from gpiozero import Device, OutputDevice
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FfmpegOutput

try:
    from gpiozero.pins.rpigpio import RPiGPIOFactory

    Device.pin_factory = RPiGPIOFactory()
except Exception:
    # Fall back to gpiozero's default pin factory if RPiGPIO is unavailable.
    pass


BASE_DIR = Path("/home/pi/BEAMNode_Prototype2")
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_DATA_DIR = Path("/home/pi/data")
PHOTO_COUNT = 3
PHOTO_GAP_SEC = 1
VIDEO_SECONDS = 10


def load_config():
    if not CONFIG_PATH.exists():
        return {}

    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not read config.json: {e}")
        return {}


def get_latest_lux(lux_log_path: Path):
    try:
        with open(lux_log_path, "r") as f:
            data = json.load(f)

        if "records" in data and len(data["records"]) > 0:
            return data["records"][-1]["lux"]

    except Exception as e:
        print(f"[BEAM] Lux read error: {e}")

    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--images-dir", required=True)
    parser.add_argument("--video-output", required=True)
    args = parser.parse_args()

    config = load_config()
    global_config = config.get("global", {})
    camera_config = config.get("camera", {})

    images_dir = Path(args.images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    resolution = camera_config.get("resolution", [1920, 1080])
    width = int(resolution[0])
    height = int(resolution[1])

    photo_prefix = camera_config.get("file_prefix", "motionpic_")
    video_seconds = VIDEO_SECONDS
    video_bitrate = int(camera_config.get("video_bitrate", 10000000))

    base_dir = Path(global_config.get("base_dir", str(DEFAULT_DATA_DIR)))
    lux_log_path = base_dir / "tsl2591" / "lux_data.json"

    flash_enabled = camera_config.get("flash_enabled", False)
    flash = None
    if flash_enabled:
        flash_pin = camera_config.get("flash_gpio", 17)
        flash = OutputDevice(flash_pin)

    photo_flash_warmup = float(camera_config.get("motion_photo_flash_warmup_sec", 0.15))
    photo_flash_cooldown = float(camera_config.get("motion_photo_flash_cooldown_sec", 0.1))
    video_flash_duration = float(camera_config.get("motion_video_flash_duration_sec", 10.0))

    picam2 = Picamera2()

    still_config = picam2.create_still_configuration(
        main={"size": (width, height)}
    )

    video_config = picam2.create_video_configuration(
        main={"size": (width, height)}
    )

    def configure_camera(mode):
        try:
            picam2.stop()
        except Exception:
            pass
        picam2.configure(video_config if mode == "video" else still_config)
        picam2.start()
        time.sleep(1)

    def should_use_flash(lux_value):
        flash_threshold = camera_config.get("flash_lux_threshold", 10)
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
            print(f"[BEAM] Flash pulse for photo: {photo_path}")
            if photo_flash_warmup > 0:
                time.sleep(photo_flash_warmup)

        picam2.capture_file(str(photo_path))

        if flash_active:
            if photo_flash_cooldown > 0:
                time.sleep(photo_flash_cooldown)
            set_flash_state(False)

    def record_video_with_flash(video_path, encoder, flash_active):
        configure_camera("video")
        picam2.start_recording(encoder, FfmpegOutput(str(video_path)))
        print(f"Recording video: {video_path}")

        end_time = time.monotonic() + max(video_seconds, 0)

        if flash_active:
            active_flash_time = min(max(video_flash_duration, 0.0), max(video_seconds, 0))
            if active_flash_time > 0:
                set_flash_state(True)
                print(f"[BEAM] Flash ON for video ({active_flash_time:.1f}s)")
                time.sleep(active_flash_time)
                set_flash_state(False)

            remaining_video_time = max(end_time - time.monotonic(), 0.0)
            if remaining_video_time > 0:
                time.sleep(remaining_video_time)
        else:
            remaining_video_time = max(end_time - time.monotonic(), 0.0)
            if remaining_video_time > 0:
                time.sleep(remaining_video_time)

        picam2.stop_recording()

    print("Starting camera...")

    configure_camera("still")

    lux = get_latest_lux(lux_log_path)
    flash_active = should_use_flash(lux)
    if flash_active:
        print(f"[BEAM] Night detected (lux={lux}) -> Flash sequence armed")
    else:
        set_flash_state(False)

    for i in range(1, PHOTO_COUNT + 1):
        photo_path = images_dir / f"{photo_prefix}{args.timestamp}_{i}.jpg"
        print(f"Taking photo {i}: {photo_path}")
        capture_photo_with_flash(photo_path, flash_active)
        time.sleep(PHOTO_GAP_SEC)

    video_output = Path(args.video_output)
    video_output.parent.mkdir(parents=True, exist_ok=True)

    encoder = H264Encoder(bitrate=video_bitrate)

    record_video_with_flash(video_output, encoder, flash_active)
    set_flash_state(False)

    print("Camera capture complete.")


if __name__ == "__main__":
    main()
