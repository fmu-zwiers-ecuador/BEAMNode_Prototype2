#!/usr/bin/env python3
"""
audiomoth_motion_record.py

Records a motion-triggered AudioMoth USB audio clip.

Only used when no hourly recording covers the motion event.
Duration comes from motion_capture.duration_sec so it lines up with the video.
"""

import argparse
import json
import math
import subprocess
import time
from pathlib import Path

from motion_logging import setup_motion_logger


MOTION_DIR  = Path(__file__).resolve().parent
NODE_DIR    = MOTION_DIR.parent
BASE_DIR    = NODE_DIR.parent.parent
CONFIG_PATH = NODE_DIR / "config.json"

logger = setup_motion_logger("audiomoth_motion_record")


def wait_until_epoch(start_at_epoch):
    if start_at_epoch is None:
        return
    wait_seconds = start_at_epoch - time.time()
    if wait_seconds > 0:
        logger.info("Waiting %.3fs for synchronized audio start", wait_seconds)
        time.sleep(wait_seconds)
    else:
        logger.warning("Synchronized audio start is %.3fs late", abs(wait_seconds))


def load_config():
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Could not read config.json: %s", e)
        return {}


def get_required_number(config_section, key, label, value_type=float):
    if key not in config_section:
        raise ValueError(f"Missing {label} in config.json")
    value = value_type(config_section[key])
    if value <= 0:
        raise ValueError(f"{label} must be greater than 0")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--start-at-epoch", type=float, default=None)
    parser.add_argument(
        "--duration-sec",
        type=float,
        default=None,
        help="Override the motion audio recording duration.",
    )
    args = parser.parse_args()

    config = load_config()

    audio_config        = config.get("audio", {})
    motion_audio_config = config.get("motion_audio", {})

    try:
        config_duration_sec = get_required_number(
            config["motion_capture"],
            "duration_sec",
            "motion_capture.duration_sec",
            int,
        )
    except (KeyError, ValueError) as e:
        logger.error("%s", e)
        raise SystemExit(1)
    duration_sec = args.duration_sec if args.duration_sec is not None else config_duration_sec
    if duration_sec <= 0:
        logger.error("--duration-sec must be greater than 0")
        raise SystemExit(1)

    sample_rate  = int(motion_audio_config.get(
        "sample_rate", audio_config.get("sample_rate", 48000)
    ))
    channels     = int(motion_audio_config.get(
        "channels", audio_config.get("channels", 1)
    ))
    alsa_device  = motion_audio_config.get("alsa_device", "plughw:1,0")
    audio_format = motion_audio_config.get("alsa_format", "S16_LE")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "arecord",
        "-D", alsa_device,
        "-f", audio_format,
        "-r", str(sample_rate),
        "-c", str(channels),
        "-d", str(int(math.ceil(duration_sec))),
        str(output_path),
    ]

    logger.info("Recording AudioMoth audio: %s", output_path)
    logger.info("Running command: %s", " ".join(cmd))

    wait_until_epoch(args.start_at_epoch)
    record_start = time.monotonic()
    proc = subprocess.Popen(cmd)
    logger.info("Audio recording started")
    result = proc.wait()
    elapsed = time.monotonic() - record_start

    if result != 0:
        logger.error("Audio recording failed with code %s", result)
        raise SystemExit(result)

    logger.info("Audio recording complete; wall-clock recording time %.3fs", elapsed)


if __name__ == "__main__":
    main()
