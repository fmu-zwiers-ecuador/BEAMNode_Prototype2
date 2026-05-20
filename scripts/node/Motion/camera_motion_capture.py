#!/usr/bin/env python3
"""
camera_motion_capture.py

Records video and takes burst photos AT THE SAME TIME using a background thread.

  - Starts a 10-second video recording
  - While recording: takes 3 photos spaced evenly across the clip
  - Photos are grabbed as still frames from the live stream (no mode switch needed)

Uses picamera2. Called by beam_motion_trigger.py.
"""

import argparse
import json
import threading
import time
from pathlib import Path

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder, Quality
from picamera2.outputs import FfmpegOutput
from libcamera import Transform


BASE_DIR    = Path("/home/pi/BEAMNode_Prototype2")
CONFIG_PATH = BASE_DIR / "config.json"

PHOTO_COUNT   = 3
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
    parser.add_argument("--timestamp",    required=True)
    parser.add_argument("--images-dir",   required=True)
    parser.add_argument("--video-output", required=True)
    args = parser.parse_args()

    config        = load_config()
    camera_config = config.get("camera", {})

    images_dir = Path(args.images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    resolution    = camera_config.get("resolution", [1920, 1080])
    width, height = int(resolution[0]), int(resolution[1])

    photo_prefix  = camera_config.get("file_prefix", "motionpic_")
    video_seconds = int(camera_config.get("video_duration_sec", VIDEO_SECONDS))

    video_output = Path(args.video_output)
    video_output.parent.mkdir(parents=True, exist_ok=True)

    picam2 = Picamera2()

    # Single video configuration — main stream used for both recording and
    # still captures, so no reconfiguration (and no mode switch) is needed.
    video_config = picam2.create_video_configuration(
        main={"size": (width, height), "format": "RGB888"},
        transform=Transform(),
    )
    picam2.configure(video_config)

    encoder = H264Encoder()
    output  = FfmpegOutput(str(video_output))

    print("Starting camera...")
    picam2.start()
    time.sleep(2)   # let AE / AWB settle BEFORE the recording clock starts
                    # (this does NOT eat into the 10-second clip)

    # Photo-burst thread — takes 3 shots spread across the recording window.
    # Photos are scheduled at 20 %, 50 %, 80 % of video_seconds so they
    # always land well inside the clip with no risk of running over the end.
    photo_done = threading.Event()

    def burst_photos():
        checkpoints = [0.20, 0.50, 0.80]   # fractions of video_seconds
        recording_start = time.monotonic()
        for i, fraction in enumerate(checkpoints, start=1):
            target_time = fraction * video_seconds
            elapsed = time.monotonic() - recording_start
            wait = target_time - elapsed
            if wait > 0:
                time.sleep(wait)
            photo_path = images_dir / f"{photo_prefix}{args.timestamp}_{i}.jpg"
            print(f"Taking photo {i} at ~{fraction*100:.0f}% mark: {photo_path}")
            picam2.capture_file(str(photo_path))   # grabs JPEG from live stream
        photo_done.set()

    # Start recording and photo thread at the same instant
    print(f"Recording video: {video_output}")
    picam2.start_recording(encoder, output, quality=Quality.HIGH)

    t_photos = threading.Thread(target=burst_photos, daemon=True)
    t_photos.start()

    time.sleep(video_seconds)          # hold for the full 10-second clip

    picam2.stop_recording()
    photo_done.wait(timeout=5)         # let any in-flight capture finish
    picam2.stop()

    print("Camera capture complete.")


if __name__ == "__main__":
    main()
