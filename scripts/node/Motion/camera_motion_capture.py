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

from motion_logging import setup_motion_logger
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder, Quality
from picamera2.outputs import FfmpegOutput
from libcamera import Transform


MOTION_DIR  = Path(__file__).resolve().parent
NODE_DIR    = MOTION_DIR.parent
BASE_DIR    = NODE_DIR.parent.parent
CONFIG_PATH = NODE_DIR / "config.json"

PHOTO_COUNT   = 3
VIDEO_SECONDS = 10
VIDEO_FPS     = 30

logger = setup_motion_logger("camera_motion_capture")


def wait_until_epoch(start_at_epoch):
    if start_at_epoch is None:
        return
    wait_seconds = start_at_epoch - time.time()
    if wait_seconds > 0:
        logger.info("Waiting %.3fs for synchronized capture start", wait_seconds)
        time.sleep(wait_seconds)
    else:
        logger.warning("Synchronized capture start is %.3fs late", abs(wait_seconds))


def load_config():
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Could not read config.json: %s", e)
        return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timestamp",    required=True)
    parser.add_argument("--images-dir",   required=True)
    parser.add_argument("--video-output", required=True)
    parser.add_argument("--start-at-epoch", type=float, default=None)
    parser.add_argument(
        "--pre-settled",
        action="store_true",
        help="Skip the internal AE/AWB settle delay because the caller already warmed the camera.",
    )
    args = parser.parse_args()

    config        = load_config()
    camera_config = config.get("camera", {})

    images_dir = Path(args.images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    picture_resolution = camera_config.get(
        "picture_resolution",
        camera_config.get("resolution", [1920, 1080]),
    )
    video_resolution = camera_config.get(
        "video_resolution",
        camera_config.get("lores_resolution", [640, 480]),
    )
    picture_width, picture_height = int(picture_resolution[0]), int(picture_resolution[1])
    video_width, video_height = int(video_resolution[0]), int(video_resolution[1])

    photo_prefix  = camera_config.get("file_prefix", "motionpic_")
    video_seconds = int(camera_config.get("video_duration_sec", VIDEO_SECONDS))
    video_fps     = int(camera_config.get("video_fps", VIDEO_FPS))
    frame_us      = int(1_000_000 / video_fps)
    logger.info(
        "Camera capture settings: video=%sx%s at %s fps, pictures=%sx%s",
        video_width,
        video_height,
        video_fps,
        picture_width,
        picture_height,
    )

    video_output = Path(args.video_output)
    video_output.parent.mkdir(parents=True, exist_ok=True)

    picam2 = Picamera2()

    # Encode the low-res stream for video while keeping the main stream high-res
    # for photos. This avoids mode switching during the 10-second clip.
    video_config = picam2.create_video_configuration(
        main={"size": (picture_width, picture_height), "format": "RGB888"},
        lores={"size": (video_width, video_height), "format": "YUV420"},
        controls={
            "FrameRate": video_fps,
            "FrameDurationLimits": (frame_us, frame_us),
        },
        transform=Transform(),
    )
    picam2.configure(video_config)

    encoder = H264Encoder()
    output  = FfmpegOutput(str(video_output))

    logger.info("Starting camera")
    picam2.start()
    if not args.pre_settled:
        logger.info("Settling camera for 2 seconds")
        time.sleep(2)   # let AE / AWB settle BEFORE the recording clock starts
                        # (this does NOT eat into the 10-second clip)

    wait_until_epoch(args.start_at_epoch)

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
            logger.info("Taking photo %s at ~%.0f%% mark: %s", i, fraction * 100, photo_path)
            picam2.capture_file(str(photo_path), name="main")   # grabs JPEG from high-res stream
        photo_done.set()

    # Start recording and photo thread at the same instant
    logger.info(
        "Recording video for %ss at %s fps: %s",
        video_seconds,
        video_fps,
        video_output,
    )
    record_start = time.monotonic()
    picam2.start_recording(encoder, output, quality=Quality.HIGH, name="lores")

    t_photos = threading.Thread(target=burst_photos, daemon=True)
    t_photos.start()

    time.sleep(video_seconds)          # hold for the full 10-second clip

    picam2.stop_recording()
    elapsed = time.monotonic() - record_start
    photo_done.wait(timeout=5)         # let any in-flight capture finish
    picam2.stop()

    logger.info("Camera capture complete; wall-clock recording time %.3fs", elapsed)


if __name__ == "__main__":
    main()
