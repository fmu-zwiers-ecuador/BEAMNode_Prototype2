"""
BEAM Burst Camera: capture 3 images (1s apart), then record 10s video.
No PIR sensor required.
"""

import json
import os
import time
from datetime import datetime, timezone

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

LUX_LOG_PATH = "/home/pi/data/tsl2591/lux_data.json"
CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def resolve_output_directory(root_dir, configured_dir):
    if configured_dir in (None, "", "."):
        return root_dir
    return os.path.join(root_dir, configured_dir)


def ensure_parent_directory(file_path):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)


def build_media_path(directory, file_name):
    safe_name = os.path.basename(file_name)
    media_path = os.path.join(directory, safe_name)
    ensure_parent_directory(media_path)
    return media_path


def get_latest_lux():
    try:
        with open(LUX_LOG_PATH, "r") as f:
            data = json.load(f)

        if "records" in data and len(data["records"]) > 0:
            return data["records"][-1]["lux"]

    except Exception as e:
        print(f"[BEAM] Lux read error: {e}")

    return None


def main():
    config = load_config()
    global_config = config.get("global", {})
    cam_config = config.get("camera", {})

    base_dir = global_config.get("base_dir", "/home/pi/data")
    directory = resolve_output_directory(base_dir, cam_config.get("directory", "camera"))
    directory = os.path.abspath(directory)
    os.makedirs(directory, exist_ok=True)

    image_prefix = cam_config.get("file_prefix", "motionpic_")
    video_prefix = cam_config.get("video_file_prefix", "motionvid_")
    video_bitrate = int(cam_config.get("video_bitrate", 10000000))
    photo_flash_warmup = float(cam_config.get("motion_photo_flash_warmup_sec", 0.15))
    photo_flash_cooldown = float(cam_config.get("motion_photo_flash_cooldown_sec", 0.1))
    video_flash_duration = float(cam_config.get("motion_video_flash_duration_sec", 10.0))

    main_res = tuple(cam_config.get("resolution", [1920, 1080]))
    video_res = tuple(cam_config.get("video_resolution", cam_config.get("resolution", [1920, 1080])))

    picam = Picamera2()
    still_config = picam.create_still_configuration(main={"size": main_res})
    video_config = picam.create_video_configuration(main={"size": video_res})

    flash_enabled = cam_config.get("flash_enabled", False)
    flash = None
    if flash_enabled:
        flash_pin = cam_config.get("flash_gpio", 17)
        flash = OutputDevice(flash_pin)

    def configure_camera(mode):
        try:
            picam.stop()
        except Exception:
            pass
        picam.configure(video_config if mode == "video" else still_config)
        picam.start()
        time.sleep(1)

    def should_use_flash(lux_value):
        flash_threshold = cam_config.get("flash_lux_threshold", 10)
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
            print(f"[BEAM] Flash pulse for photo: {os.path.basename(photo_path)}")
            if photo_flash_warmup > 0:
                time.sleep(photo_flash_warmup)

        picam.capture_file(photo_path)

        if flash_active:
            if photo_flash_cooldown > 0:
                time.sleep(photo_flash_cooldown)
            set_flash_state(False)

    def record_video_with_flash(video_path, encoder, flash_active):
        configure_camera("video")
        picam.start_recording(encoder, FfmpegOutput(video_path))
        print(f"[BEAM] Recording video: {video_path}")

        end_time = time.monotonic() + 10.0

        if flash_active:
            active_flash_time = min(max(video_flash_duration, 0.0), 10.0)
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

        picam.stop_recording()

    now_utc = datetime.now(timezone.utc)
    event_ts = now_utc.strftime("%Y%m%d_%H%M%SZ")

    lux = get_latest_lux()
    flash_active = should_use_flash(lux)
    if flash_active:
        print(f"[BEAM] Night detected (lux={lux}) -> Flash sequence armed")
    else:
        set_flash_state(False)

    configure_camera("still")

    photo_paths = []
    for index in range(3):
        photo_name = f"{image_prefix}{event_ts}_{index + 1}.jpg"
        photo_path = build_media_path(directory, photo_name)
        capture_photo_with_flash(photo_path, flash_active)
        photo_paths.append(photo_path)
        if index < 2:
            time.sleep(1.0)

    video_path = build_media_path(directory, f"{video_prefix}{event_ts}.mp4")
    encoder = H264Encoder(bitrate=video_bitrate)

    record_video_with_flash(video_path, encoder, flash_active)
    set_flash_state(False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
