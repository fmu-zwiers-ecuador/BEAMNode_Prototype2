#!/usr/bin/env python3
"""
beam_motion_trigger.py

Runs 24/7. Watches PIR motion sensor.

When motion is detected:
  1. Creates event folders
  2. Captures high-resolution still photos
  3. Starts raw video and raw audio
  4. Embeds audio into the video

Folder layout:
  /home/pi/data/motion_events/event_TIMESTAMP/
      images/
      video/
      audio/
      combined/
"""

import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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

MERGE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="motion_merge")


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


def record_new_motion_audio(audio_output, start_at_epoch, duration_sec, metadata_output=None):
    cmd = [
        "python3", str(AUDIO_SCRIPT),
        "--output", str(audio_output),
        "--start-at-epoch", str(start_at_epoch),
        "--duration-sec", str(duration_sec),
    ]
    if metadata_output is not None:
        cmd.extend(["--metadata-output", str(metadata_output)])
    logger.info("Recording new motion audio clip: %s", audio_output)
    result = subprocess.run(cmd)
    logger.info("Audio recording command exited with code %s", result.returncode)
    return result.returncode == 0 and audio_output.exists()


def load_audio_metadata(metadata_path):
    try:
        with open(metadata_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Could not read audio metadata %s: %s", metadata_path, e)
        return {}


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


def extract_photos_from_video(
    video_file,
    timestamp_text,
    images_dir,
    photo_prefix,
    photo_count,
    first_photo_sec,
    photo_interval_sec,
    jpeg_quality,
    video_duration_sec,
):
    if photo_count <= 0:
        return []

    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    extracted_photos = []
    for index in range(1, photo_count + 1):
        requested_offset = first_photo_sec + ((index - 1) * photo_interval_sec)
        if video_duration_sec and video_duration_sec > 0:
            photo_offset = min(requested_offset, max(video_duration_sec - 0.1, 0.0))
        else:
            photo_offset = requested_offset

        photo_path = images_dir / f"{photo_prefix}{timestamp_text}_{index}.jpg"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{photo_offset:.3f}",
            "-i", str(video_file),
            "-frames:v", "1",
            "-q:v", str(jpeg_quality),
            str(photo_path),
        ]
        logger.info(
            "Extracting motion photo %s from video at %.3fs: %s",
            index,
            photo_offset,
            photo_path,
        )
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            logger.error("ffmpeg not found; could not extract video photos")
            return extracted_photos

        if result.returncode != 0 or not photo_path.exists():
            logger.error("Video photo extraction failed: %s", result.stderr.strip())
            continue

        extracted_photos.append(photo_path)

    return extracted_photos


def append_merge_log(log_path, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(f"{timestamp} {message}\n")
    except Exception as e:
        logger.warning("Could not write merge log %s: %s", log_path, e)


def merge_video_audio(
    video_file,
    audio_file,
    final_output,
    duration_sec,
    video_fps,
    audio_trim_start_sec=0.0,
    merge_log_path=None,
):
    if audio_trim_start_sec >= 0:
        audio_filter = (
            f"[1:a]asetpts=PTS-STARTPTS,"
            f"atrim=start={audio_trim_start_sec}:duration={duration_sec},"
            f"asetpts=PTS-STARTPTS,"
            f"apad=pad_dur={duration_sec},"
            f"atrim=duration={duration_sec},asetpts=PTS-STARTPTS[a]"
        )
    else:
        delay_ms = int(round(abs(audio_trim_start_sec) * 1000))
        audio_filter = (
            f"[1:a]asetpts=PTS-STARTPTS,"
            f"adelay={delay_ms}:all=1,"
            f"apad=pad_dur={duration_sec},"
            f"atrim=duration={duration_sec},asetpts=PTS-STARTPTS[a]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_file),
        "-i", str(audio_file),
        "-filter_complex",
        (
            f"[0:v]setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop_duration={duration_sec},"
            f"trim=duration={duration_sec},setpts=PTS-STARTPTS[v];"
            f"{audio_filter}"
        ),
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-r", str(video_fps),
        "-c:a", "aac",
        "-t", str(duration_sec),
        str(final_output),
    ]
    if merge_log_path is not None:
        append_merge_log(
            merge_log_path,
            (
                f"START video={video_file} audio={audio_file} output={final_output} "
                f"duration={duration_sec:.3f}s fps={video_fps} "
                f"audio_trim_start={audio_trim_start_sec:.3f}s"
            ),
        )
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            preexec_fn=lambda: os.nice(10),
        )
    except Exception as e:
        if merge_log_path is not None:
            append_merge_log(merge_log_path, f"FAILED_TO_START {e}")
        return False

    merge_ok = result.returncode == 0 and final_output.exists()
    if merge_log_path is not None:
        if merge_ok:
            append_merge_log(merge_log_path, f"COMPLETE output={final_output}")
        else:
            append_merge_log(merge_log_path, f"FAILED returncode={result.returncode}")
            if result.stderr:
                append_merge_log(merge_log_path, f"FFMPEG_STDERR {result.stderr.strip()}")
    return merge_ok


def queue_video_audio_merge(
    video_file,
    audio_file,
    final_output,
    duration_sec,
    video_fps,
    audio_trim_start_sec,
):
    merge_log_path = final_output.with_suffix(".merge.log")

    def merge_task():
        if merge_video_audio(
            video_file,
            audio_file,
            final_output,
            duration_sec,
            video_fps,
            audio_trim_start_sec,
            merge_log_path,
        ):
            return
        else:
            logger.warning("Background final video processing failed; see %s", merge_log_path)

    append_merge_log(
        merge_log_path,
        f"QUEUED video={video_file} audio={audio_file} output={final_output}",
    )
    MERGE_EXECUTOR.submit(merge_task)
    logger.info("Final video processing sent to background: %s", final_output)


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
            motion_config.get("start_delay_sec", 0.25),
        )
        audio_preroll_sec = get_nonnegative_number(
            motion_config,
            "audio_preroll_sec",
            1.0,
        )
        audio_postroll_sec = get_nonnegative_number(
            motion_config,
            "audio_postroll_sec",
            0.5,
        )
        audio_sync_offset_sec = float(
            motion_config.get("audio_sync_offset_sec", 0.0)
        )
    except (KeyError, ValueError) as e:
        logger.error("%s", e)
        return

    event_dir, images_dir, video_dir, audio_dir, combined_dir = \
        create_event_dirs(config, timestamp_text)

    motion_audio_file = audio_dir / f"{MOTION_AUDIO_PREFIX}{timestamp_text}.wav"
    motion_audio_metadata_file = audio_dir / f"{MOTION_AUDIO_PREFIX}{timestamp_text}.json"

    logger.info("Motion detected at %s", timestamp_text)
    logger.info("Saving event to: %s", event_dir)

    camera_config = config.get("camera", {})
    picture_config = camera_config.get("pictures", {})
    picture_mode = picture_config.get("mode", "before_video")

    lux = get_latest_lux()
    flash_active = should_use_flash(camera_config, camera_capture, lux)
    if flash_active:
        logger.info("Night detected, lux=%s. Flash sequence armed", lux)
    else:
        logger.info("Flash not used for event; lux=%s", lux)

    if picture_mode == "before_video":
        logger.info("Capturing high-resolution motion photos before video")
        try:
            photos = camera_capture.capture_photos(
                timestamp_text,
                images_dir,
                flash_active=flash_active,
            )
            logger.info("Captured %s high-resolution motion photos", len(photos))
        except Exception as e:
            logger.exception("High-resolution photo capture failed before video: %s", e)

    start_at_epoch       = time.time() + video_start_delay
    audio_start_at_epoch = max(time.time(), start_at_epoch - audio_preroll_sec)
    audio_duration       = (
        motion_duration
        + max(start_at_epoch - audio_start_at_epoch, 0.0)
        + audio_postroll_sec
    )
    audio_trim_start_sec = (
        max(start_at_epoch - audio_start_at_epoch, 0.0)
        + audio_sync_offset_sec
    )
    motion_start         = datetime.fromtimestamp(start_at_epoch)

    logger.info(
        "Scheduling synchronized video for %s with duration %ss",
        motion_start.strftime("%Y-%m-%d %H:%M:%S.%f"),
        motion_duration,
    )
    logger.info(
        "Audio pre-roll %.3fs, post-roll %.3fs, sync offset %.3fs, total audio %.3fs",
        max(start_at_epoch - audio_start_at_epoch, 0.0),
        audio_postroll_sec,
        audio_sync_offset_sec,
        audio_duration,
    )

    results = {
        "video_file": None,
        "video_duration_sec": None,
        "video_start_epoch": None,
        "audio_ready": False,
    }

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
            results["video_duration_sec"] = camera_capture.last_video_elapsed_sec
            results["video_start_epoch"] = camera_capture.last_video_start_epoch
        except Exception as e:
            logger.exception("Camera video capture failed: %s", e)

    def audio_thread():
        try:
            logger.info("Recording synchronized live audio")
            results["audio_ready"] = record_new_motion_audio(
                motion_audio_file,
                audio_start_at_epoch,
                audio_duration,
                motion_audio_metadata_file,
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
    video_duration_sec = results["video_duration_sec"] or motion_duration
    video_start_epoch = results["video_start_epoch"]
    audio_ready = results["audio_ready"]

    if video_file is None:
        logger.error("Camera capture failed; no video to merge")
        return

    if picture_mode == "video_frames":
        picture_count = int(picture_config.get("count", camera_capture.picture_count))
        picture_interval_sec = float(
            picture_config.get(
                "video_frame_interval_sec",
                camera_capture.picture_interval_sec,
            )
        )
        first_picture_sec = float(picture_config.get("video_frame_first_sec", 1.0))
        picture_jpeg_quality = int(picture_config.get("video_frame_jpeg_quality", 1))

        logger.info("Extracting motion photos from the recorded video")
        extracted_photos = extract_photos_from_video(
            video_file,
            timestamp_text,
            images_dir,
            camera_capture.photo_prefix,
            picture_count,
            first_picture_sec,
            picture_interval_sec,
            picture_jpeg_quality,
            video_duration_sec,
        )
        logger.info("Extracted %s motion photos from video", len(extracted_photos))

    if not audio_ready:
        logger.warning("Audio not available; keeping video-only file")
        return

    audio_metadata = load_audio_metadata(motion_audio_metadata_file)
    audio_record_start_epoch = audio_metadata.get("record_start_epoch")
    if video_start_epoch is not None and audio_record_start_epoch is not None:
        actual_audio_preroll_sec = max(video_start_epoch - audio_record_start_epoch, 0.0)
        audio_trim_start_sec = actual_audio_preroll_sec + audio_sync_offset_sec
        logger.info(
            "Actual sync timing: audio_pre_video=%.3fs offset=%.3fs trim=%.3fs",
            actual_audio_preroll_sec,
            audio_sync_offset_sec,
            audio_trim_start_sec,
        )
    else:
        logger.warning("Actual sync timing unavailable; using scheduled trim %.3fs", audio_trim_start_sec)

    # ── Step 3: Queue audio/video merge in the background ────────────────────
    final_video = combined_dir / f"{FINAL_VIDEO_PREFIX}{timestamp_text}.mp4"
    queue_video_audio_merge(
        video_file,
        motion_audio_file,
        final_video,
        video_duration_sec,
        camera_capture.video_fps,
        audio_trim_start_sec,
    )


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

    pir = None
    camera_capture = None
    try:
        pir = MotionSensor(
            pir_pin,
            pull_up=None,
            active_state=True,
            queue_len=int(camera_config.get("pir_queue_len", 1)),
            sample_rate=float(camera_config.get("pir_sample_rate", 10)),
            threshold=float(camera_config.get("pir_threshold", 0.5)),
        )
        camera_capture = MotionCameraCapture(config)
        camera_capture.start()
        time.sleep(warmup)
    except Exception as e:
        logger.exception("Startup failed: %s", e)
        if camera_capture is not None:
            camera_capture.close()
        if pir is not None:
            pir.close()
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
        if camera_capture is not None:
            camera_capture.close()
        if pir is not None:
            pir.close()


if __name__ == "__main__":
    main()
