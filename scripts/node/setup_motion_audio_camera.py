#!/usr/bin/env python3
"""
setup_motion_audio_camera_v3.py

Run this one time on the Raspberry Pi.

It creates:
  /home/pi/BEAMNode_Prototype2/scripts/node/Motion/
      beam_motion_trigger.py
      camera_motion_capture.py
      audiomoth_motion_record.py

Changes from v2:
  - Photos are taken DURING video recording (not before)
  - Audio is recorded SIMULTANEOUSLY with video+photos
  - Audio is embedded (muxed) into the final video file

It does NOT edit config.json.
"""

from pathlib import Path
import stat

MOTION_DIR = Path("/home/pi/BEAMNode_Prototype2/scripts/node/Motion")

FILES = {

# ─────────────────────────────────────────────────────────────────────────────
"beam_motion_trigger.py": '''\
#!/usr/bin/env python3
"""
beam_motion_trigger.py

Runs 24/7. Watches PIR motion sensor.

When motion is detected:
  1. Creates one event folder
  2. Starts video recording, photo bursts, and audio capture ALL AT THE SAME TIME
  3. Uses matching hourly AudioMoth audio if available; otherwise records a new clip
  4. Embeds audio into the video file

Folder layout:
  /home/pi/data/motion_events/event_TIMESTAMP/
      images/
      video/
      audio/
      combined/
"""

import json
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from gpiozero import MotionSensor


BASE_DIR     = Path("/home/pi/BEAMNode_Prototype2")
CONFIG_PATH  = BASE_DIR / "config.json"
MOTION_DIR   = BASE_DIR / "scripts/node/Motion"

CAMERA_SCRIPT = MOTION_DIR / "camera_motion_capture.py"
AUDIO_SCRIPT  = MOTION_DIR / "audiomoth_motion_record.py"

DEFAULT_DATA_DIR = Path("/home/pi/data")

VIDEO_SECONDS         = 10
PIR_PIN               = 24
PIR_WARMUP_SEC        = 5
PIR_POLL_INTERVAL_SEC = 0.05
COOLDOWN_SEC          = 5

HOURLY_AUDIO_PREFIX = "recording_"
MOTION_AUDIO_PREFIX = "motionaudio_"
FINAL_VIDEO_PREFIX  = "motionvid_audio_"


def load_config():
    if not CONFIG_PATH.exists():
        print(f"Config not found: {CONFIG_PATH}")
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not read config.json: {e}")
        return {}


def get_base_data_dir(config):
    return Path(config.get("global", {}).get("base_dir", str(DEFAULT_DATA_DIR)))


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


def get_hourly_audio_dir(config):
    base_data_dir = get_base_data_dir(config)
    audio_dir = base_data_dir / config.get("audio", {}).get("directory", "audio")
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir


def parse_timestamp_from_name(path):
    stem  = path.stem
    parts = stem.split("_")
    candidates = []
    if len(parts) >= 3:
        candidates.append(parts[-2] + "_" + parts[-1])
    if len(parts) >= 2:
        candidates.append(parts[-1])
    for text in candidates:
        for fmt in ("%Y%m%d_%H%M%S", "%Y-%m-%d_%H-%M-%S", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
    return None


def find_hourly_audio_covering_motion(config, motion_start, motion_duration):
    audio_config        = config.get("audio", {})
    motion_audio_config = config.get("motion_audio", {})
    audio_dir           = get_hourly_audio_dir(config)

    hourly_prefix   = motion_audio_config.get(
        "hourly_audio_prefix",
        audio_config.get("file_prefix", HOURLY_AUDIO_PREFIX),
    )
    hourly_duration = int(audio_config.get("duration_sec", 300))
    motion_end      = motion_start + timedelta(seconds=motion_duration)

    possible_files = (
        list(audio_dir.glob(f"{hourly_prefix}*.wav")) +
        list(audio_dir.glob(f"{hourly_prefix}*.flac"))
    )

    newest_match = None
    for audio_file in possible_files:
        audio_start = parse_timestamp_from_name(audio_file)
        if audio_start is None:
            continue
        audio_end = audio_start + timedelta(seconds=hourly_duration)
        if audio_start <= motion_start and motion_end <= audio_end:
            if newest_match is None or audio_file.stat().st_mtime > newest_match.stat().st_mtime:
                newest_match = audio_file

    return newest_match


def cut_audio_from_hourly(hourly_audio_file, motion_start, audio_output, duration_sec):
    audio_start = parse_timestamp_from_name(hourly_audio_file)
    if audio_start is None:
        return False
    offset_sec = (motion_start - audio_start).total_seconds()
    if offset_sec < 0:
        return False
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(offset_sec),
        "-t",  str(duration_sec),
        "-i",  str(hourly_audio_file),
        "-acodec", "pcm_s16le",
        str(audio_output),
    ]
    print("Cutting motion audio from hourly AudioMoth file...")
    result = subprocess.run(cmd)
    return result.returncode == 0 and audio_output.exists()


def record_new_motion_audio(audio_output):
    """Run audiomoth_motion_record.py and block until it finishes."""
    cmd = ["python3", str(AUDIO_SCRIPT), "--output", str(audio_output)]
    print("Recording new motion audio clip...")
    result = subprocess.run(cmd)
    return result.returncode == 0 and audio_output.exists()


def run_camera_capture(timestamp_text, images_dir, video_dir):
    """Run camera_motion_capture.py (video + simultaneous photos)."""
    video_output = video_dir / f"motionvid_{timestamp_text}.mp4"
    cmd = [
        "python3", str(CAMERA_SCRIPT),
        "--timestamp",    timestamp_text,
        "--images-dir",   str(images_dir),
        "--video-output", str(video_output),
    ]
    print("Running camera capture (video + photos simultaneously)...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("Camera capture failed.")
        return None
    if not video_output.exists():
        print(f"Video output was not created: {video_output}")
        return None
    return video_output


def merge_video_audio(video_file, audio_file, final_output):
    """Mux audio into video; audio is re-encoded to AAC and embedded."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_file),
        "-i", str(audio_file),
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        str(final_output),
    ]
    print("Embedding audio into video...")
    result = subprocess.run(cmd)
    return result.returncode == 0 and final_output.exists()


def handle_motion(config):
    motion_start   = datetime.now()
    timestamp_text = motion_start.strftime("%Y%m%d_%H%M%S")

    event_dir, images_dir, video_dir, audio_dir, combined_dir = \
        create_event_dirs(config, timestamp_text)

    print(f"Motion detected at {timestamp_text}")
    print(f"Saving event to: {event_dir}")

    # Decide audio source before launching threads
    motion_audio_file = audio_dir / f"{MOTION_AUDIO_PREFIX}{timestamp_text}.wav"
    hourly_audio_file = find_hourly_audio_covering_motion(
        config=config,
        motion_start=motion_start,
        motion_duration=VIDEO_SECONDS,
    )

    # Results shared between threads
    results = {"video_file": None, "audio_ready": False}

    def camera_thread():
        results["video_file"] = run_camera_capture(
            timestamp_text, images_dir, video_dir
        )

    def audio_thread():
        if hourly_audio_file:
            print(f"Found overlapping hourly audio: {hourly_audio_file}")
            results["audio_ready"] = cut_audio_from_hourly(
                hourly_audio_file=hourly_audio_file,
                motion_start=motion_start,
                audio_output=motion_audio_file,
                duration_sec=VIDEO_SECONDS,
            )
        else:
            print("No overlapping hourly audio — recording live audio...")
            results["audio_ready"] = record_new_motion_audio(motion_audio_file)

    # Launch camera and audio at exactly the same time
    t_camera = threading.Thread(target=camera_thread, daemon=True)
    t_audio  = threading.Thread(target=audio_thread,  daemon=True)

    t_camera.start()
    t_audio.start()

    # Wait for both to finish before merging
    t_camera.join()
    t_audio.join()

    video_file  = results["video_file"]
    audio_ready = results["audio_ready"]

    if video_file is None:
        print("Camera capture failed — no video to merge.")
        return

    if not audio_ready:
        print("Audio was not available — keeping video-only file.")
        return

    # Embed audio into video
    final_video = combined_dir / f"{FINAL_VIDEO_PREFIX}{timestamp_text}.mp4"
    if merge_video_audio(video_file, motion_audio_file, final_video):
        print(f"Final video with embedded audio: {final_video}")
    else:
        print("Merge failed — keeping separate video and audio files.")


def main():
    config = load_config()

    camera_config       = config.get("camera", {})
    motion_audio_config = config.get("motion_audio", {})

    if not camera_config.get("enabled", True):
        print("Camera is disabled in config.json.")
        return
    if not motion_audio_config.get("enabled", True):
        print("motion_audio is disabled in config.json.")
        return

    pir_pin       = int(camera_config.get("gpio_pin",               PIR_PIN))
    warmup        = float(camera_config.get("pir_warmup_sec",       PIR_WARMUP_SEC))
    poll_interval = float(camera_config.get("pir_poll_interval_sec", PIR_POLL_INTERVAL_SEC))
    cooldown      = float(camera_config.get("motion_cooldown_sec",
                          camera_config.get("cooldown_sec", COOLDOWN_SEC)))

    print("Starting 24/7 PIR motion trigger...")
    print(f"PIR GPIO pin: {pir_pin}")
    print(f"Warmup seconds: {warmup}")

    pir = MotionSensor(pir_pin)
    time.sleep(warmup)
    print("Ready. Waiting for motion...")

    while True:
        if pir.motion_detected:
            handle_motion(config)
            print(f"Cooling down for {cooldown} seconds...")
            time.sleep(cooldown)
            print("Waiting for motion to clear...")
            while pir.motion_detected:
                time.sleep(0.2)
            print("Ready again.")
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
''',

# ─────────────────────────────────────────────────────────────────────────────
"camera_motion_capture.py": '''\
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
    time.sleep(1)   # let AE / AWB settle before recording

    # Photo-burst thread — fires during the recording window
    photo_done = threading.Event()

    def burst_photos():
        # Space shots evenly: 20 %, 50 %, 80 % through the clip
        gap = video_seconds / (PHOTO_COUNT + 1)
        for i in range(1, PHOTO_COUNT + 1):
            time.sleep(gap)
            photo_path = images_dir / f"{photo_prefix}{args.timestamp}_{i}.jpg"
            print(f"Taking photo {i}: {photo_path}")
            picam2.capture_file(str(photo_path))   # grabs JPEG from live stream
        photo_done.set()

    # Start recording, then immediately kick off the photo thread
    print(f"Recording video: {video_output}")
    picam2.start_recording(encoder, output, quality=Quality.HIGH)

    t_photos = threading.Thread(target=burst_photos, daemon=True)
    t_photos.start()

    time.sleep(video_seconds)          # hold for full clip duration

    picam2.stop_recording()
    photo_done.wait(timeout=5)         # let any in-flight capture finish
    picam2.stop()

    print("Camera capture complete.")


if __name__ == "__main__":
    main()
''',

# ─────────────────────────────────────────────────────────────────────────────
"audiomoth_motion_record.py": '''\
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


BASE_DIR    = Path("/home/pi/BEAMNode_Prototype2")
CONFIG_PATH = BASE_DIR / "config.json"


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

    print(f"Recording AudioMoth audio: {output_path}")
    print(" ".join(cmd))

    result = subprocess.run(cmd)

    if result.returncode != 0:
        print("Audio recording failed.")
        raise SystemExit(result.returncode)

    print("Audio recording complete.")


if __name__ == "__main__":
    main()
''',

}   # end FILES


def write_file(path, content):
    path.write_text(content)
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main():
    MOTION_DIR.mkdir(parents=True, exist_ok=True)

    for name, content in FILES.items():
        path = MOTION_DIR / name
        write_file(path, content)
        print(f"Created: {path}")

    print()
    print("Done.")
    print()
    print("Motion scripts were created in:")
    print(f"  {MOTION_DIR}")
    print()
    print("Run the 24/7 motion script with:")
    print(f"  python3 {MOTION_DIR / 'beam_motion_trigger.py'}")
    print()
    print("On every motion event all three things now happen at the same time:")
    print("  - 10-second video is recorded")
    print("  - 3 photos are captured during the recording")
    print("  - Audio is recorded (or cut from hourly file) in parallel")
    print("  - Audio is embedded into the final .mp4 in combined/")
    print()
    print("This setup script did NOT change config.json.")


if __name__ == "__main__":
    main()
    for name, content in FILES.items():
        path = MOTION_DIR / name
        write_file(path, content)
        print(f"Created: {path}")

    print()
    print("Done.")
    print()
    print("Motion scripts were created in:")
    print(f"  {MOTION_DIR}")
    print()
    print("Run the 24/7 motion script with:")
    print(f"  python3 {MOTION_DIR / 'beam_motion_trigger.py'}")
    print()
    print("Motion event files will save like:")
    print("  /home/pi/data/motion_events/event_TIMESTAMP/images/")
    print("  /home/pi/data/motion_events/event_TIMESTAMP/video/")
    print("  /home/pi/data/motion_events/event_TIMESTAMP/audio/")
    print("  /home/pi/data/motion_events/event_TIMESTAMP/combined/")
    print()
    print("This setup script did NOT change config.json.")


if __name__ == "__main__":
    main()
    for name, content in FILES.items():
        path = MOTION_DIR / name
        write_file(path, content)
        print(f"Created: {path}")

    print()
    print("Done.")
    print()
    print("Motion scripts were created in:")
    print(f"  {MOTION_DIR}")
    print()
    print("Run the 24/7 motion script with:")
    print(f"  python3 {MOTION_DIR / 'beam_motion_trigger.py'}")
    print()
    print("This setup script did NOT change config.json.")


if __name__ == "__main__":
    main()
