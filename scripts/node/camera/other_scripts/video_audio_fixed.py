"""
BEAM Motion Camera System
Motion trigger -> simultaneously:
  1) record 10 second video
  2) record 10 second audio from Audio Injector
  3) take 3 pictures, 1 second apart
Then merges video + audio into one MP4.
Files are saved inside a date/time folder.
"""

import os
import json
import time
import subprocess
from datetime import datetime, timezone

from gpiozero import Device, MotionSensor, OutputDevice
from gpiozero.exc import BadPinFactory
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput

try:
    from gpiozero.pins.rpigpio import RPiGPIOFactory
    Device.pin_factory = RPiGPIOFactory()
except Exception:
    pass

# ---------------------------------
# CONSTANT PATHS
# ---------------------------------
LUX_LOG_PATH = "/home/pi/data/tsl2591/lux_data.json"
CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

# ---------------------------------
# Load configuration
# ---------------------------------
with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

global_config = config.get("global", {})
cam_config = config.get("camera", {})

if not cam_config.get("enabled", False):
    print("[BEAM] Camera module disabled in config.")
    raise SystemExit

node_id = global_config.get("node_id", "unknown-node")
base_dir = global_config.get("base_dir", "/home/pi/data")

# Main camera directory. Each motion event gets its own timestamp folder inside this.
directory = os.path.join(base_dir, cam_config.get("directory", "camera"))
os.makedirs(directory, exist_ok=True)
log_path = os.path.join(directory, "camera_log.json")

# ---------------------------------
# Motion profiles
# ---------------------------------
DELAY_PROFILES = {
    "instant": {"cooldown_sec": 0.25, "pir_poll_interval_sec": 0.02, "pir_sample_rate": 30, "pir_queue_len": 1, "pir_threshold": 0.3},
    "fast":    {"cooldown_sec": 0.5,  "pir_poll_interval_sec": 0.05, "pir_sample_rate": 20, "pir_queue_len": 1, "pir_threshold": 0.5},
    "normal":  {"cooldown_sec": 1.0,  "pir_poll_interval_sec": 0.1,  "pir_sample_rate": 10, "pir_queue_len": 1, "pir_threshold": 0.5},
    "slow":    {"cooldown_sec": 2.0,  "pir_poll_interval_sec": 0.2,  "pir_sample_rate": 5,  "pir_queue_len": 2, "pir_threshold": 0.5},
}

RANGE_PROFILES = {
    "high":   {"pir_sample_rate": 30, "pir_queue_len": 1, "pir_threshold": 0.3},
    "widest": {"pir_sample_rate": 20, "pir_queue_len": 1, "pir_threshold": 0.4},
    "medium": {"pir_sample_rate": 10, "pir_queue_len": 1, "pir_threshold": 0.5},
    "narrow": {"pir_sample_rate": 8,  "pir_queue_len": 2, "pir_threshold": 0.7},
}

def normalize_delay_profile(camera_config):
    configured = str(camera_config.get("pir_response_profile", camera_config.get("motion_delay_profile", "normal"))).lower()
    aliases = {"highest": "instant", "high": "fast", "medium": "normal", "default": "normal", "low": "slow"}
    return aliases.get(configured, configured)

def normalize_range_profile(camera_config):
    configured = str(camera_config.get("pir_sensitivity_profile", camera_config.get("detection_range_profile", "medium"))).lower()
    aliases = {"highest": "high", "high": "high", "more": "widest", "medium": "medium", "default": "medium", "low": "narrow", "narrowest": "narrow"}
    return aliases.get(configured, configured)

def build_motion_settings(camera_config):
    delay_name = normalize_delay_profile(camera_config)
    range_name = normalize_range_profile(camera_config)
    settings = {}
    settings.update(DELAY_PROFILES.get(delay_name, DELAY_PROFILES["normal"]))
    settings.update(RANGE_PROFILES.get(range_name, RANGE_PROFILES["medium"]))
    for key in ("cooldown_sec", "pir_warmup_sec", "pir_poll_interval_sec", "pir_sample_rate", "pir_queue_len", "pir_threshold"):
        if key in camera_config:
            settings[key] = camera_config[key]
    settings["motion_delay_profile"] = delay_name
    settings["detection_range_profile"] = range_name
    return settings

motion_settings = build_motion_settings(cam_config)

# ---------------------------------
# Audio/video settings
# ---------------------------------
AUDIO_ENABLED = cam_config.get("audio_enabled", True)
AUDIO_DEVICE = cam_config.get("audio_device", "plughw:1,0")
AUDIO_RATE = int(cam_config.get("audio_rate", 48000))
AUDIO_CHANNELS = int(cam_config.get("audio_channels", 2))

VIDEO_DURATION = int(cam_config.get("video_duration_sec", 10))
VIDEO_FPS = int(cam_config.get("video_fps", 30))
VIDEO_BITRATE = int(cam_config.get("video_bitrate", 8_000_000))
PICTURE_COUNT = int(cam_config.get("picture_count", 3))
PICTURE_INTERVAL = float(cam_config.get("picture_interval_sec", 1.0))

# ---------------------------------
# GPIO setup
# ---------------------------------
pir_pin = cam_config.get("pir_gpio", cam_config.get("gpio_pin", 4))
try:
    pir = MotionSensor(
        pir_pin,
        pull_up=None,
        active_state=True,
        queue_len=motion_settings.get("pir_queue_len", 1),
        sample_rate=motion_settings.get("pir_sample_rate", 10),
        threshold=motion_settings.get("pir_threshold", 0.5),
    )
except (BadPinFactory, Exception) as e:
    print(f"[BEAM] PIR setup failed on GPIO {pir_pin}: {e}")
    raise

flash_enabled = cam_config.get("flash_enabled", False)
flash = None
if flash_enabled:
    flash = OutputDevice(cam_config.get("flash_gpio", 17))

# ---------------------------------
# Camera setup
# ---------------------------------
picam = Picamera2()
main_res = tuple(cam_config.get("resolution", [1920, 1080]))
video_config = picam.create_video_configuration(
    main={"size": main_res},
    controls={"FrameRate": VIDEO_FPS},
)
picam.configure(video_config)
picam.start()
time.sleep(1)

# ---------------------------------
# Helpers
# ---------------------------------
def get_latest_lux():
    try:
        with open(LUX_LOG_PATH, "r") as f:
            data = json.load(f)
        records = data.get("records", [])
        if records:
            return records[-1].get("lux")
    except Exception as e:
        print("[BEAM] Lux read error:", e)
    return None

def start_audio_recording(audio_path):
    cmd = [
        "arecord",
        "-D", AUDIO_DEVICE,
        "-f", "S16_LE",
        "-r", str(AUDIO_RATE),
        "-c", str(AUDIO_CHANNELS),
        "-d", str(VIDEO_DURATION),
        audio_path,
    ]
    try:
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except FileNotFoundError:
        print("[BEAM] arecord not found. Install it with: sudo apt install alsa-utils")
    except Exception as e:
        print(f"[BEAM] Audio failed to start: {e}")
    return None

def merge_video_audio(video_h264, audio_wav, final_mp4):
    # ffmpeg turns the raw camera .h264 and wav audio into one playable mp4.
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(VIDEO_FPS),
        "-i", video_h264,
        "-i", audio_wav,
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        final_mp4,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and os.path.exists(final_mp4):
            print(f"[BEAM] Final video with audio saved: {final_mp4}")
            return True
        print("[BEAM] ffmpeg merge failed:")
        print(result.stderr.strip())
    except FileNotFoundError:
        print("[BEAM] ffmpeg not found. Install it with: sudo apt install ffmpeg")
    except Exception as e:
        print(f"[BEAM] Merge error: {e}")
    return False

def load_log():
    if os.path.exists(log_path):
        try:
            with open(log_path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and "records" in data:
                return data
        except Exception:
            pass
    return {"node_id": node_id, "sensor": "camera", "records": []}

def save_log(record):
    data = load_log()
    data["records"].append(record)
    with open(log_path, "w") as f:
        json.dump(data, f, indent=4)
    print(f"[BEAM] Event logged to: {log_path}")

def set_flash_for_lux(lux):
    if not flash_enabled or flash is None:
        return
    threshold = cam_config.get("flash_lux_threshold", 10)
    if lux is not None and lux < threshold:
        flash.on()
        print(f"[BEAM] Night detected, lux={lux}. Flash ON")
    else:
        flash.off()

def capture_motion_event():
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone()
    timestamp_iso = now_utc.isoformat()
    folder_ts = now_utc.strftime("%Y-%m-%d_%H-%M-%S")

    event_dir = os.path.join(directory, folder_ts)
    os.makedirs(event_dir, exist_ok=True)

    raw_video_path = os.path.join(event_dir, "video_raw.h264")
    audio_path = os.path.join(event_dir, "audio.wav")
    final_video_path = os.path.join(event_dir, "video_with_audio.mp4")
    image_paths = [os.path.join(event_dir, f"photo_{i + 1}.jpg") for i in range(PICTURE_COUNT)]

    print(f"[BEAM] Motion event folder: {event_dir}")

    lux = get_latest_lux()
    set_flash_for_lux(lux)

    audio_proc = None
    if AUDIO_ENABLED:
        audio_proc = start_audio_recording(audio_path)
        if audio_proc:
            print(f"[BEAM] Audio started: {audio_path}")

    encoder = H264Encoder(bitrate=VIDEO_BITRATE)
    picam.start_recording(encoder, FileOutput(raw_video_path))
    print(f"[BEAM] Video started: {raw_video_path}")

    start_time = time.monotonic()

    # Take 3 photos while the video/audio are still recording.
    for i, image_path in enumerate(image_paths):
        target_time = start_time + (i * PICTURE_INTERVAL)
        wait_time = target_time - time.monotonic()
        if wait_time > 0:
            time.sleep(wait_time)

        try:
            picam.capture_file(image_path)
            print(f"[BEAM] Picture {i + 1} saved: {image_path}")
        except Exception as e:
            print(f"[BEAM] Picture {i + 1} failed: {e}")
            image_paths[i] = None

    # Keep recording until the full 10 seconds is done.
    remaining = VIDEO_DURATION - (time.monotonic() - start_time)
    if remaining > 0:
        time.sleep(remaining)

    try:
        picam.stop_recording()
        print(f"[BEAM] Video saved: {raw_video_path}")
    except Exception as e:
        print(f"[BEAM] Video stop failed: {e}")

    if flash_enabled and flash is not None:
        flash.off()

    audio_ok = False
    if audio_proc:
        try:
            _, stderr_output = audio_proc.communicate(timeout=5)
            audio_ok = audio_proc.returncode == 0 and os.path.exists(audio_path)
            if not audio_ok and stderr_output:
                print(f"[BEAM] Audio warning: {stderr_output.decode(errors='ignore').strip()}")
            elif audio_ok:
                print(f"[BEAM] Audio saved: {audio_path}")
        except subprocess.TimeoutExpired:
            print("[BEAM] Audio still running, terminating.")
            audio_proc.terminate()

    merged_ok = False
    if AUDIO_ENABLED and audio_ok and os.path.exists(raw_video_path):
        merged_ok = merge_video_audio(raw_video_path, audio_path, final_video_path)

    record = {
        "timestamp_utc": timestamp_iso,
        "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": now_local.tzname(),
        "event_folder": event_dir,
        "pictures": [p for p in image_paths if p],
        "raw_video_file": raw_video_path if os.path.exists(raw_video_path) else None,
        "audio_file": audio_path if audio_ok else None,
        "final_video_file": final_video_path if merged_ok else None,
        "lux": lux,
    }
    save_log(record)

# ---------------------------------
# Main loop
# ---------------------------------
if global_config.get("print_debug", True):
    print(f"[BEAM] Motion camera armed on GPIO {pir_pin}")
    print(f"[BEAM] Save directory: {directory}")
    print(f"[BEAM] Audio enabled: {AUDIO_ENABLED}")
    print(f"[BEAM] Audio device: {AUDIO_DEVICE}")
    print(f"[BEAM] Video duration: {VIDEO_DURATION}s")
    print(f"[BEAM] Pictures: {PICTURE_COUNT}, {PICTURE_INTERVAL}s apart")
    print("[BEAM] Warming up PIR...")

pir_warmup = motion_settings.get("pir_warmup_sec", cam_config.get("pir_warmup_sec", 5))
time.sleep(pir_warmup)
print("[BEAM] PIR ready")

cooldown = motion_settings.get("cooldown_sec", 1)
poll_interval = motion_settings.get("pir_poll_interval_sec", 0.1)
last_motion_state = pir.motion_detected

try:
    while True:
        current_motion_state = pir.motion_detected

        if current_motion_state and not last_motion_state:
            print("[BEAM] Motion detected")
            capture_motion_event()
            time.sleep(cooldown)

        elif not current_motion_state and last_motion_state and global_config.get("print_debug", True):
            print("[BEAM] Motion ended")

        last_motion_state = current_motion_state
        time.sleep(poll_interval)

except KeyboardInterrupt:
    print("\n[BEAM] Stopped by user")
finally:
    if flash_enabled and flash is not None:
        flash.off()
    picam.stop()
