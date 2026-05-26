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

from gpiozero import Device, OutputDevice
from motion_logging import setup_motion_logger
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput
from libcamera import Transform

try:
    from gpiozero.pins.rpigpio import RPiGPIOFactory

    Device.pin_factory = RPiGPIOFactory()
except Exception:
    pass


MOTION_DIR  = Path(__file__).resolve().parent
NODE_DIR    = MOTION_DIR.parent
BASE_DIR    = NODE_DIR.parent.parent
CONFIG_PATH = NODE_DIR / "config.json"
DEFAULT_FLASH_GPIO = 26

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


def count_h264_frames(raw_video_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-count_frames",
        "-select_streams", "v:0",
        "-show_entries", "stream=nb_read_frames",
        "-of", "default=nokey=1:noprint_wrappers=1",
        str(raw_video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        logger.warning("ffprobe not found; using configured video fps")
        return None

    if result.returncode != 0:
        logger.warning("ffprobe frame count failed: %s", result.stderr.strip())
        return None

    try:
        frame_count = int(result.stdout.strip())
    except ValueError:
        logger.warning("ffprobe returned invalid frame count: %s", result.stdout.strip())
        return None

    if frame_count <= 0:
        logger.warning("ffprobe returned no video frames")
        return None

    return frame_count


def remux_h264_to_mp4(raw_video_path, mp4_output_path, video_fps, wall_duration_sec=None):
    remux_fps = float(video_fps)
    frame_count = None
    if wall_duration_sec is not None and wall_duration_sec > 0:
        frame_count = count_h264_frames(raw_video_path)
        if frame_count is not None:
            measured_fps = frame_count / wall_duration_sec
            if measured_fps > 0:
                remux_fps = measured_fps
                logger.info(
                    "Measured video fps %.3f from %s frames over %.3fs",
                    remux_fps,
                    frame_count,
                    wall_duration_sec,
                )

    cmd = [
        "ffmpeg", "-y",
        "-fflags", "+genpts",
        "-r", f"{remux_fps:.6f}",
        "-i", str(raw_video_path),
        "-c:v", "copy",
        "-video_track_timescale", "90000",
        str(mp4_output_path),
    ]
    logger.info("Remuxing raw H.264 to MP4 at %.3f fps: %s", remux_fps, mp4_output_path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        logger.error("ffmpeg not found; raw video kept at %s", raw_video_path)
        return False, remux_fps

    if result.returncode != 0:
        logger.error("ffmpeg remux failed: %s", result.stderr.strip())
        return False, remux_fps
    return mp4_output_path.exists(), remux_fps


class MotionCameraCapture:
    def __init__(self, config):
        self.config = config
        self.camera_config = config.get("camera", {})
        self.picam2 = Picamera2()
        self.started = False
        self.last_video_elapsed_sec = None
        self.last_video_start_epoch = None
        self.last_video_fps = None

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
        self.flash_enabled = self.camera_config.get("flash_enabled", False)
        self.flash_pin = int(self.camera_config.get("flash_gpio", DEFAULT_FLASH_GPIO))
        self.flash = None
        self.photo_flash_warmup_sec = float(
            self.camera_config.get("motion_photo_flash_warmup_sec", 0.15)
        )
        self.photo_flash_cooldown_sec = float(
            self.camera_config.get("motion_photo_flash_cooldown_sec", 0.1)
        )
        self.photo_awb_settle_sec = float(
            self.camera_config.get("photo_awb_settle_sec", 1.0)
        )
        self.video_flash_duration_sec = float(
            self.camera_config.get(
                "motion_video_flash_duration_sec",
                self.video_seconds,
            )
        )
        if self.flash_enabled:
            self.flash = OutputDevice(self.flash_pin)
            self.set_flash_state(False)

        frame_us = int(1_000_000 / self.video_fps)
        exposure_us = int(self.camera_config.get("video_exposure_us", frame_us))
        analogue_gain = float(self.camera_config.get("video_gain", 1.0))
        self.video_warmup_sec = float(self.camera_config.get("video_warmup_sec", 1.0))
        self.video_bitrate = int(self.camera_config.get("video_bitrate", 2_000_000))
        self.fixed_fps_controls = {
            "FrameRate": self.video_fps,
            "FrameDurationLimits": (frame_us, frame_us),
            "AeEnable": False,
            "AwbEnable": False,
            "ExposureTime": exposure_us,
            "AnalogueGain": analogue_gain,
            "ColourGains": (1.4, 2.2),
        }
        self.photo_controls = {
            "AeEnable": True,
            "AwbEnable": False,
            "ColourGains": (1.4, 2.2),
        }

        self.video_config = self.picam2.create_video_configuration(
            main={"size": (self.video_width, self.video_height), "format": "YUV420"},
            controls=self.fixed_fps_controls,
            transform=Transform(),
        )
        self.still_config = self.picam2.create_still_configuration(
            main={"size": (self.picture_width, self.picture_height)},
            controls=self.photo_controls,
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
            "Fixed video controls: exposure_us=%s gain=%s awb_enabled=%s warmup_sec=%s bitrate=%s",
            self.fixed_fps_controls["ExposureTime"],
            self.fixed_fps_controls["AnalogueGain"],
            self.fixed_fps_controls["AwbEnable"],
            self.video_warmup_sec,
            self.video_bitrate,
        )
        logger.info(
            "Flash enabled=%s gpio=%s",
            self.flash_enabled,
            self.flash_pin,
        )
        logger.info(
            "Photo controls: awb_enabled=%s settle_sec=%s flash_warmup_sec=%s",
            self.photo_controls["AwbEnable"],
            self.photo_awb_settle_sec,
            self.photo_flash_warmup_sec,
        )

    @property
    def flash_available(self):
        return self.flash_enabled and self.flash is not None

    def set_flash_state(self, enabled):
        if not self.flash_available:
            return
        if enabled:
            self.flash.on()
        else:
            self.flash.off()

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
        self.set_flash_state(False)
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
        try:
            if self.flash is not None:
                self.flash.close()
        except Exception:
            pass
        self.started = False

    def capture_photos(self, timestamp_text, images_dir, flash_active=False):
        if self.picture_count <= 0:
            return []

        self.start()
        images_dir = Path(images_dir)
        images_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Switching camera to still-photo mode")
        self.picam2.stop()
        self.picam2.configure(self.still_config)
        self.picam2.start()
        self.picam2.set_controls(self.photo_controls)
        if self.photo_awb_settle_sec > 0:
            logger.info(
                "Settling still-photo exposure and white balance for %.2fs",
                self.photo_awb_settle_sec,
            )
            time.sleep(self.photo_awb_settle_sec)

        photos = []
        for i in range(1, self.picture_count + 1):
            photo_path = images_dir / f"{self.photo_prefix}{timestamp_text}_{i}.jpg"
            logger.info("Taking motion photo %s: %s", i, photo_path)
            if flash_active:
                self.set_flash_state(True)
                logger.info("Flash pulse for photo: %s", photo_path.name)
                self.picam2.set_controls(self.photo_controls)
                if self.photo_flash_warmup_sec > 0:
                    time.sleep(self.photo_flash_warmup_sec)
            try:
                self.picam2.capture_file(str(photo_path))
            finally:
                if flash_active:
                    if self.photo_flash_cooldown_sec > 0:
                        time.sleep(self.photo_flash_cooldown_sec)
                    self.set_flash_state(False)
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

    def record_video(self, video_output, start_at_epoch=None, flash_active=False):
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
        self.last_video_start_epoch = time.time()
        logger.info("Video recording started")

        try:
            fps_warned = False
            last_frame = None
            last_time  = time.monotonic()
            flash_off_at = None

            if flash_active:
                active_flash_time = min(
                    max(self.video_flash_duration_sec, 0.0),
                    max(float(self.video_seconds), 0.0),
                )
                if active_flash_time > 0:
                    self.set_flash_state(True)
                    logger.info("Flash ON for video (%.1fs)", active_flash_time)
                    flash_off_at = record_start + active_flash_time

            while True:
                elapsed = time.monotonic() - record_start

                if flash_off_at is not None and time.monotonic() >= flash_off_at:
                    self.set_flash_state(False)
                    flash_off_at = None

                if elapsed >= self.video_seconds:
                    break

                time.sleep(0.25)

                try:
                    metadata     = self.picam2.capture_metadata()
                    frame_number = metadata.get("FrameNumber")
                    if frame_number is not None and last_frame is not None:
                        dt = time.monotonic() - last_time
                        if dt > 0:
                            logger.info("Recording FPS: %.2f", (frame_number - last_frame) / dt)
                    last_frame = frame_number
                    last_time  = time.monotonic()
                except Exception as exc:
                    if not fps_warned:
                        logger.warning("FPS logging unavailable: %s", exc)
                        fps_warned = True
        finally:
            self.set_flash_state(False)
            self.picam2.stop_recording()

        elapsed = time.monotonic() - record_start
        self.last_video_elapsed_sec = elapsed
        logger.info("Video recording complete; wall-clock recording time %.3fs", elapsed)

        if not raw_video_output.exists():
            return None

        remuxed, measured_fps = remux_h264_to_mp4(
            raw_video_output,
            video_output,
            self.video_fps,
            elapsed,
        )
        self.last_video_fps = measured_fps
        if remuxed:
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