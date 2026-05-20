#!/usr/bin/env python3
"""
audiomoth_motion_record.py

Records a motion-triggered AudioMoth USB audio clip.

Only used when no hourly recording covers the motion event.
Duration matches VIDEO_SECONDS so the clip lines up with the video.
"""

import argparse
import json
import subprocess
from pathlib import Path

from motion_logging import setup_motion_logger


MOTION_DIR  = Path(__file__).resolve().parent
NODE_DIR    = MOTION_DIR.parent
BASE_DIR    = NODE_DIR.parent.parent
CONFIG_PATH = NODE_DIR / "config.json"

logger = setup_motion_logger("audiomoth_motion_record")


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
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    config = load_config()

    audio_config        = config.get("audio", {})
    motion_audio_config = config.get("motion_audio", {})

    duration_sec = int(motion_audio_config.get("duration_sec", 10))
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
        "-d", str(duration_sec),
        str(output_path),
    ]

    logger.info("Recording AudioMoth audio: %s", output_path)
    logger.info("Running command: %s", " ".join(cmd))

    result = subprocess.run(cmd)

    if result.returncode != 0:
        logger.error("Audio recording failed with code %s", result.returncode)
        raise SystemExit(result.returncode)

    logger.info("Audio recording complete")


if __name__ == "__main__":
    main()
