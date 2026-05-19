"""
BEAM Burst Camera: capture 3 images (1s apart), then record 10s video.
No PIR sensor required.
"""

import json
import os
import time
from datetime import datetime, timezone

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FfmpegOutput

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

    main_res = tuple(cam_config.get("resolution", [1920, 1080]))
    video_res = tuple(cam_config.get("video_resolution", cam_config.get("resolution", [1920, 1080])))

    picam = Picamera2()
    still_config = picam.create_still_configuration(main={"size": main_res})
    video_config = picam.create_video_configuration(main={"size": video_res})

    def configure_camera(mode):
        try:
            picam.stop()
        except Exception:
            pass
        picam.configure(video_config if mode == "video" else still_config)
        picam.start()
        time.sleep(1)

    now_utc = datetime.now(timezone.utc)
    event_ts = now_utc.strftime("%Y%m%d_%H%M%SZ")

    configure_camera("still")

    photo_paths = []
    for index in range(3):
        photo_name = f"{image_prefix}{event_ts}_{index + 1}.jpg"
        photo_path = build_media_path(directory, photo_name)
        picam.capture_file(photo_path)
        photo_paths.append(photo_path)
        if index < 2:
            time.sleep(1.0)

    video_path = build_media_path(directory, f"{video_prefix}{event_ts}.mp4")
    encoder = H264Encoder(bitrate=video_bitrate)

    configure_camera("video")
    picam.start_recording(encoder, FfmpegOutput(video_path))
    time.sleep(10.0)
    picam.stop_recording()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
