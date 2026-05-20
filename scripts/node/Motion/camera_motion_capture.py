#!/usr/bin/env python3
"""
camera_motion_capture.py

Records video and takes burst photos AT THE SAME TIME using a background thread.

  - Starts a timed video recording using config.json
  - While recording: takes photos spaced evenly across the clip
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


def get_required_resolution(camera_config, section_name):
    section = camera_config.get(section_name, {})
    resolution = section.get("resolution")
    if not resolution or len(resolution) != 2:
        raise ValueError(f"Missing camera.{section_name}.resolution in config.json")
    return int(resolution[0]), int(resolution[1])


def get_required_number(config_section, key, label, value_type=float):
    if key not in config_section:
        raise ValueError(f"Missing {label} in config.json")
    value = value_type(config_section[key])
    if value <= 0:
        raise ValueError(f"{label} must be greater than 0")
    return value


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

    try:
        motion_config = config["motion_capture"]
        video_settings = camera_config["video"]
        picture_settings = camera_config["pictures"]
        video_width, video_height = get_required_resolution(camera_config, "video")
        picture_width, picture_height = get_required_resolution(camera_config, "pictures")
        video_seconds = get_required_number(
            motion_config,
            "duration_sec",
            "motion_capture.duration_sec",
            int,
        )
        video_fps = get_required_number(video_settings, "fps", "camera.video.fps", int)
        picture_count = get_required_number(
            picture_settings,
            "count",
            "camera.pictures.count",
            int,
        )
        picture_finish_timeout = get_required_number(
            picture_settings,
            "finish_timeout_sec",
            "camera.pictures.finish_timeout_sec",
        )
        settle_sec = get_required_number(camera_config, "settle_sec", "camera.settle_sec")
    except (KeyError, ValueError) as e:
        logger.error("%s", e)
        raise SystemExit(1)

    photo_prefix  = camera_config.get("file_prefix", "motionpic_")
    frame_us      = int(1_000_000 / video_fps)
    logger.info(
        "Camera capture settings: duration=%ss, video=%sx%s at %s fps, pictures=%sx%s count=%s",
        video_seconds,
        video_width,
        video_height,
        video_fps,
        picture_width,
        picture_height,
        picture_count,
    )

    video_output = Path(args.video_output)
    video_output.parent.mkdir(parents=True, exist_ok=True)

    picam2 = Picamera2()

    # Encode the low-res stream for video while keeping the main stream high-res
    # for photos. This avoids mode switching during the clip.
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
        logger.info("Settling camera for %s seconds", settle_sec)
        time.sleep(settle_sec)   # let AE / AWB settle before the recording clock starts

    wait_until_epoch(args.start_at_epoch)

    photo_done = threading.Event()

    def burst_photos():
        checkpoints = [
            photo_number / (picture_count + 1)
            for photo_number in range(1, picture_count + 1)
        ]
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
    logger.info("Video recording started")

    t_photos = threading.Thread(target=burst_photos, daemon=True)
    t_photos.start()

    time.sleep(video_seconds)

    picam2.stop_recording()
    elapsed = time.monotonic() - record_start
    photo_done.wait(timeout=picture_finish_timeout)
    picam2.stop()

    logger.info("Camera capture complete; wall-clock recording time %.3fs", elapsed)


if __name__ == "__main__":
    main()
