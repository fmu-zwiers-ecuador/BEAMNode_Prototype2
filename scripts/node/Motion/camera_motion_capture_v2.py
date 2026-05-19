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

from picamera2 import Picamera2
from libcamera import Transform


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--images-dir", required=True)
    parser.add_argument("--video-output", required=True)
    args = parser.parse_args()

    config = load_config()
    camera_config = config.get("camera", {})

    images_dir = Path(args.images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    resolution = camera_config.get("resolution", [1920, 1080])
    width = int(resolution[0])
    height = int(resolution[1])

    photo_prefix = camera_config.get("file_prefix", "motionpic_")
    video_seconds = int(camera_config.get("video_duration_sec", VIDEO_SECONDS))

    picam2 = Picamera2()

    still_config = picam2.create_still_configuration(
        main={"size": (width, height)},
        transform=Transform()
    )

    video_config = picam2.create_video_configuration(
        main={"size": (width, height)},
        transform=Transform()
    )

    print("Starting camera...")

    picam2.configure(still_config)
    picam2.start()
    time.sleep(1)

    for i in range(1, PHOTO_COUNT + 1):
        photo_path = images_dir / f"{photo_prefix}{args.timestamp}_{i}.jpg"
        print(f"Taking photo {i}: {photo_path}")
        picam2.capture_file(str(photo_path))
        time.sleep(PHOTO_GAP_SEC)

    picam2.stop()

    video_output = Path(args.video_output)
    video_output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Recording video: {video_output}")

    picam2.configure(video_config)
    picam2.start_and_record_video(str(video_output), duration=video_seconds)

    print("Camera capture complete.")


if __name__ == "__main__":
    main()
