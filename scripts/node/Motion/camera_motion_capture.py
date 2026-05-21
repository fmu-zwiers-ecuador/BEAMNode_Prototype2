#!/usr/bin/env python3
"""
camera_motion_capture.py

Keeps the camera initialized in video mode, captures high-resolution photos,
then records a timed video clip.

    - Starts and settles the camera once
    - Takes photos immediately when motion is detected
    - Records the video clip after the photos

Uses picamera2. Called by beam_motion_trigger.py.
"""

import argparse
import json
import subprocess
import time
from pathlib import Path

from motion_logging import setup_motion_logger
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput
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


def remux_h264_to_mp4(raw_video_path, mp4_output_path, video_fps):
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(video_fps),
        "-i", str(raw_video_path),
        "-c:v", "copy",
        str(mp4_output_path),
    ]
    logger.info("Remuxing raw H.264 to MP4: %s", mp4_output_path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        logger.error("ffmpeg not found; raw video kept at %s", raw_video_path)
        return False

    if result.returncode != 0:
        logger.error("ffmpeg remux failed: %s", result.stderr.strip())
        return False
    return mp4_output_path.exists()


class MotionCameraCapture:
    def __init__(self, config):
        self.config = config
        self.camera_config = config.get("camera", {})
        self.picam2 = Picamera2()
        self.started = False

        motion_config = config["motion_capture"]
        video_settings = self.camera_config["video"]
        picture_settings = self.camera_config["pictures"]

        self.video_width, self.video_height = get_required_resolution(
            self.camera_config,
            "video",
        )
        self.picture_width, self.picture_height = get_required_resolution(
            self.camera_config,
            "pictures",
        )
        self.video_seconds = get_required_number(
            motion_config,
            "duration_sec",
            "motion_capture.duration_sec",
            int,
        )
        self.video_fps = get_required_number(
            video_settings,
            "fps",
            "camera.video.fps",
            int,
        )
        self.picture_count = get_required_number(
            picture_settings,
            "count",
            "camera.pictures.count",
            int,
        )
        self.settle_sec = get_required_number(
            self.camera_config,
            "settle_sec",
            "camera.settle_sec",
        )
        self.picture_interval_sec = float(
            self.camera_config.get("picture_interval_sec", 1.0)
        )
        self.photo_prefix = self.camera_config.get("file_prefix", "motionpic_")

        frame_us = int(1_000_000 / self.video_fps)
        exposure_us = int(self.camera_config.get("video_exposure_us", frame_us))
        analogue_gain = float(self.camera_config.get("video_gain", 2.0))
        self.video_warmup_sec = float(self.camera_config.get("video_warmup_sec", 1.0))
        self.video_bitrate = int(self.camera_config.get("video_bitrate", 2_000_000))
        self.fixed_fps_controls = {
            "FrameRate": self.video_fps,
            "FrameDurationLimits": (frame_us, frame_us),
            "AeEnable": False,
            "AwbEnable": False,
            "ExposureTime": exposure_us,
            "AnalogueGain": analogue_gain,
        }

        self.video_config = self.picam2.create_video_configuration(
            main={"size": (self.video_width, self.video_height), "format": "YUV420"},
            controls=self.fixed_fps_controls,
            transform=Transform(),
        )
        self.still_config = self.picam2.create_still_configuration(
            main={"size": (self.picture_width, self.picture_height)}
        )

        logger.info(
            "Camera initialized: duration=%ss, video=%sx%s at %s fps, pictures=%sx%s count=%s",
            self.video_seconds,
            self.video_width,
            self.video_height,
            self.video_fps,
            self.picture_width,
            self.picture_height,
            self.picture_count,
        )
        logger.info(
            "Fixed video controls: exposure_us=%s gain=%s warmup_sec=%s bitrate=%s",
            self.fixed_fps_controls["ExposureTime"],
            self.fixed_fps_controls["AnalogueGain"],
            self.video_warmup_sec,
            self.video_bitrate,
        )

    def start(self):
        if self.started:
            return
        logger.info("Starting and settling camera at process startup")
        self.picam2.configure(self.video_config)
        self.picam2.start()
        self.picam2.set_controls(self.fixed_fps_controls)
        time.sleep(self.settle_sec)
        self.started = True
        logger.info("Camera is armed and ready")

    def close(self):
        try:
            self.picam2.stop_recording()
        except Exception:
            pass
        try:
            self.picam2.stop()
        except Exception:
            pass
        try:
            self.picam2.close()
        except Exception:
            pass
        self.started = False

    def capture_photos(self, timestamp_text, images_dir):
        if self.picture_count <= 0:
            return []

        self.start()
        images_dir = Path(images_dir)
        images_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Switching camera to still-photo mode")
        self.picam2.stop()
        self.picam2.configure(self.still_config)
        self.picam2.start()

        photos = []
        for i in range(1, self.picture_count + 1):
            photo_path = images_dir / f"{self.photo_prefix}{timestamp_text}_{i}.jpg"
            logger.info("Taking motion photo %s: %s", i, photo_path)
            self.picam2.capture_file(str(photo_path))
            photos.append(photo_path)
            if i < self.picture_count and self.picture_interval_sec > 0:
                time.sleep(self.picture_interval_sec)

        logger.info("Returning camera to video standby")
        self.picam2.stop()
        self.picam2.configure(self.video_config)
        self.picam2.start()
        self.picam2.set_controls(self.fixed_fps_controls)
        if self.video_warmup_sec > 0:
            time.sleep(self.video_warmup_sec)
        return photos

    def record_video(self, video_output, start_at_epoch=None):
        self.start()
        video_output = Path(video_output)
        video_output.parent.mkdir(parents=True, exist_ok=True)
        raw_video_output = video_output.with_suffix(".h264")

        wait_until_epoch(start_at_epoch)

        encoder = H264Encoder(bitrate=self.video_bitrate)
        output = FileOutput(str(raw_video_output))

        logger.info(
            "Recording raw H.264 for %ss at %s fps: %s",
            self.video_seconds,
            self.video_fps,
            raw_video_output,
        )
        record_start = time.monotonic()
        self.picam2.start_recording(encoder, output)
        logger.info("Video recording started")

        fps_warned = False
        last_frame = None
        last_time = time.monotonic()
        start_frame = None
        target_frames = int(self.video_fps * self.video_seconds)
        max_wall_time = self.video_seconds * 2

        while True:
            elapsed = time.monotonic() - record_start
            if elapsed >= max_wall_time:
                logger.warning("Frame-based stop timed out; stopping at %.2fs", elapsed)
                break
            time.sleep(0.25)
            try:
                metadata = self.picam2.capture_metadata()
                frame_number = metadata.get("FrameNumber")
                if frame_number is None:
                    if not fps_warned:
                        logger.warning("FPS logging unavailable: FrameNumber missing in metadata")
                        fps_warned = True
                    if elapsed >= self.video_seconds:
                        break
                    continue
                if start_frame is None:
                    start_frame = frame_number
                if start_frame is not None and (frame_number - start_frame) >= target_frames:
                    break

                now = time.monotonic()
                if last_frame is not None:
                    dt = now - last_time
                    if dt > 0:
                        fps = (frame_number - last_frame) / dt
                        logger.info("Recording FPS: %.2f", fps)
                last_frame = frame_number
                last_time = now
            except Exception as exc:
                if not fps_warned:
                    logger.warning("FPS logging unavailable: %s", exc)
                    fps_warned = True
                if elapsed >= self.video_seconds:
                    break

        self.picam2.stop_recording()
        elapsed = time.monotonic() - record_start
        logger.info("Video recording complete; wall-clock recording time %.3fs", elapsed)

        if not raw_video_output.exists():
            return None

        if remux_h264_to_mp4(raw_video_output, video_output, self.video_fps):
            return video_output

        return raw_video_output


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
        camera_capture = MotionCameraCapture(config)
    except (KeyError, ValueError) as e:
        logger.error("%s", e)
        raise SystemExit(1)

    video_output = Path(args.video_output)
    try:
        camera_capture.start()
        if args.pre_settled:
            logger.info("Camera was requested as pre-settled; using startup warm camera")
        camera_capture.capture_photos(args.timestamp, images_dir)
        if camera_capture.record_video(video_output, args.start_at_epoch) is None:
            raise SystemExit(1)
    finally:
        camera_capture.close()


if __name__ == "__main__":
    main()
