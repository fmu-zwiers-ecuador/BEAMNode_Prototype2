"""
BEAM Motion Camera System (Minimal Version)
With PIR-triggered audio recording via Audio Injector Zero
"""
import os
import json
import time
import subprocess
from datetime import datetime, timezone
from gpiozero import Device, MotionSensor, OutputDevice
from gpiozero.exc import BadPinFactory
from picamera2 import Picamera2
try:
    from gpiozero.pins.rpigpio import RPiGPIOFactory
    Device.pin_factory = RPiGPIOFactory()
except Exception:
    # Fall back to gpiozero's default pin factory if RPiGPIO is unavailable.
    pass

# ---------------------------------
# CONSTANT PATHS
# ---------------------------------
LUX_LOG_PATH = "/home/pi/data/tsl2591/lux_data.json"

# ---------------------------------
# Load configuration
# ---------------------------------
CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"
with open(CONFIG_PATH, "r") as f:
    config = json.load(f)
global_config = config.get("global", {})
cam_config = config.get("camera", {})

DELAY_PROFILES = {
    "instant": {
        "cooldown_sec": 0.25,
        "pir_poll_interval_sec": 0.02,
        "pir_sample_rate": 30,
        "pir_queue_len": 1,
        "pir_threshold": 0.3,
    },
    "fast": {
        "cooldown_sec": 0.5,
        "pir_poll_interval_sec": 0.05,
        "pir_sample_rate": 20,
        "pir_queue_len": 1,
        "pir_threshold": 0.5,
    },
    "normal": {
        "cooldown_sec": 1.0,
        "pir_poll_interval_sec": 0.1,
        "pir_sample_rate": 10,
        "pir_queue_len": 1,
        "pir_threshold": 0.5,
    },
    "slow": {
        "cooldown_sec": 2.0,
        "pir_poll_interval_sec": 0.2,
        "pir_sample_rate": 5,
        "pir_queue_len": 2,
        "pir_threshold": 0.5,
    },
}

RANGE_PROFILES = {
    "high": {
        "pir_sample_rate": 30,
        "pir_queue_len": 1,
        "pir_threshold": 0.3,
    },
    "widest": {
        "pir_sample_rate": 20,
        "pir_queue_len": 1,
        "pir_threshold": 0.4,
    },
    "medium": {
        "pir_sample_rate": 10,
        "pir_queue_len": 1,
        "pir_threshold": 0.5,
    },
    "narrow": {
        "pir_sample_rate": 8,
        "pir_queue_len": 2,
        "pir_threshold": 0.7,
    },
}

def normalize_delay_profile(camera_config):
    configured = str(
        camera_config.get(
            "pir_response_profile",
            camera_config.get("motion_delay_profile", "normal"),
        )
    ).lower()
    aliases = {
        "highest": "instant",
        "high": "fast",
        "medium": "normal",
        "default": "normal",
        "low": "slow",
    }
    return aliases.get(configured, configured)

def normalize_range_profile(camera_config):
    configured = str(
        camera_config.get(
            "pir_sensitivity_profile",
            camera_config.get("detection_range_profile", "medium"),
        )
    ).lower()
    aliases = {
        "highest": "high",
        "high": "high",
        "more": "widest",
        "medium": "medium",
        "default": "medium",
        "low": "narrow",
        "narrowest": "narrow",
    }
    return aliases.get(configured, configured)

def build_motion_settings(camera_config):
    delay_profile_name = normalize_delay_profile(camera_config)
    range_profile_name = normalize_range_profile(camera_config)
    settings = {}
    settings.update(DELAY_PROFILES.get(delay_profile_name, DELAY_PROFILES["normal"]))
    settings.update(RANGE_PROFILES.get(range_profile_name, RANGE_PROFILES["medium"]))
    for key in (
        "cooldown_sec",
        "pir_warmup_sec",
        "pir_poll_interval_sec",
        "pir_sample_rate",
        "pir_queue_len",
        "pir_threshold",
    ):
        if key in camera_config:
            settings[key] = camera_config[key]
    settings["motion_delay_profile"] = delay_profile_name
    settings["detection_range_profile"] = range_profile_name
    return settings

motion_settings = build_motion_settings(cam_config)

# ---------------------------------
# Check if camera module enabled
# ---------------------------------
if not cam_config.get("enabled", False):
    print("[BEAM] Camera module disabled in config.")
    exit()

node_id = global_config.get("node_id", "unknown-node")
base_dir = global_config.get("base_dir", "/home/pi/data")

# ---------------------------------
# Directory setup
# ---------------------------------
directory = os.path.join(base_dir, cam_config.get("directory", "camera"))
os.makedirs(directory, exist_ok=True)
log_path = os.path.join(directory, "images_log.json")

# ---------------------------------
# Audio Setup
# ---------------------------------
# Config keys (add these to your config.json under "camera"):
#   "audio_enabled": true
#   "audio_duration_sec": 10       <- how long to record per trigger
#   "audio_device": "plughw:1,0"   <- Audio Injector Zero ALSA device
#                                     run `arecord -l` to confirm device index
AUDIO_ENABLED = cam_config.get("audio_enabled", False)
AUDIO_DURATION = cam_config.get("audio_duration_sec", 10)
AUDIO_DEVICE   = cam_config.get("audio_device", "plughw:1,0")

def start_audio_recording(audio_path):
    """
    Start arecord as a non-blocking subprocess.
    Returns the Popen process handle so the caller can wait on it or kill it.

    arecord flags used:
      -D  : ALSA device (Audio Injector Zero)
      -f  : sample format  (S16_LE = 16-bit signed little-endian)
      -r  : sample rate    (44100 Hz — covers full human hearing range)
      -c  : channels       (1 = mono; change to 2 for stereo if needed)
      -d  : duration in seconds
    """
    cmd = [
        "arecord",
        "-D", AUDIO_DEVICE,
        "-f", "S16_LE",
        "-r", "44100",
        "-c", "1",
        "-d", str(AUDIO_DURATION),
        audio_path,
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return proc
    except FileNotFoundError:
        print("[BEAM] arecord not found. Install with: sudo apt install alsa-utils")
        return None
    except Exception as e:
        print(f"[BEAM] Audio recording failed to start: {e}")
        return None

# ---------------------------------
# GPIO Setup
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

# ----- Flash Setup -----
flash_enabled = cam_config.get("flash_enabled", False)
flash = None
if flash_enabled:
    flash_pin = cam_config.get("flash_gpio", 17)
    flash = OutputDevice(flash_pin)

# ---------------------------------
# Camera Setup
# ---------------------------------
picam = Picamera2()
main_res = tuple(cam_config.get("resolution", [1920, 1080]))
camera_config = picam.create_still_configuration(
    main={"size": main_res}
)
picam.configure(camera_config)
picam.start()
time.sleep(1)

if global_config.get("print_debug", True):
    print(f"[BEAM] Motion camera armed on GPIO {pir_pin}")
    print(f"[BEAM] Flash enabled: {flash_enabled}")
    print(f"[BEAM] Image directory: {directory}")
    print(f"[BEAM] Image log: {log_path}")
    print(f"[BEAM] Audio enabled: {AUDIO_ENABLED}")
    if AUDIO_ENABLED:
        print(f"[BEAM] Audio device: {AUDIO_DEVICE} | Duration: {AUDIO_DURATION}s | Format: S16_LE 44100Hz mono")
    print(
        "[BEAM] Motion tuning: "
        f"response_profile={motion_settings['motion_delay_profile']}, "
        f"sensitivity_profile={motion_settings['detection_range_profile']}, "
        f"sample_rate={motion_settings.get('pir_sample_rate', 10)}, "
        f"queue_len={motion_settings.get('pir_queue_len', 1)}, "
        f"threshold={motion_settings.get('pir_threshold', 0.5)}, "
        f"poll_interval={motion_settings.get('pir_poll_interval_sec', 0.1)}, "
        f"cooldown={motion_settings.get('cooldown_sec', 1)}"
    )
    print("[BEAM] Warming up PIR...")

cooldown     = motion_settings.get("cooldown_sec", 1)
pir_warmup   = motion_settings.get("pir_warmup_sec", cam_config.get("pir_warmup_sec", 5))
poll_interval = motion_settings.get("pir_poll_interval_sec", 0.1)

time.sleep(pir_warmup)
if global_config.get("print_debug", True):
    print("[BEAM] PIR ready")

# ---------------------------------
# Read latest lux value
# ---------------------------------
def get_latest_lux():
    try:
        with open(LUX_LOG_PATH, "r") as f:
            data = json.load(f)
        if "records" in data and len(data["records"]) > 0:
            return data["records"][-1]["lux"]
    except Exception as e:
        print("[BEAM] Lux read error:", e)
    return None

# ---------------------------------
# Motion Detection Loop
# ---------------------------------
last_motion_state = pir.motion_detected

while True:
    current_motion_state = pir.motion_detected

    if current_motion_state and not last_motion_state:
        now_utc   = datetime.now(timezone.utc)
        now_local = now_utc.astimezone()
        timestamp_iso = now_utc.isoformat()
        file_ts       = now_utc.strftime("%Y%m%d_%H%M%SZ")
        file_prefix   = cam_config.get("file_prefix", "motionpic_")

        image_path = os.path.join(directory, f"{file_prefix}{file_ts}.jpg")
        # Audio file sits next to the image with the same timestamp
        audio_path = os.path.join(directory, f"audio_{file_ts}.wav")

        if global_config.get("print_debug", True):
            print("[BEAM] Motion detected")

        # ---------------------------------
        # Start audio recording FIRST
        # so it captures the moment of trigger
        # ---------------------------------
        audio_proc = None
        if AUDIO_ENABLED:
            audio_proc = start_audio_recording(audio_path)
            if audio_proc:
                print(f"[BEAM] Audio recording started: {audio_path}")

        # ---------------------------------
        # Flash logic
        # ---------------------------------
        lux = get_latest_lux()
        flash_threshold = cam_config.get("flash_lux_threshold", 10)
        if flash_enabled and flash is not None:
            if lux is not None and lux < flash_threshold:
                flash.on()
                print(f"[BEAM] Night detected (lux={lux}) -> Flash ON")
            else:
                flash.off()

        # ---------------------------------
        # Capture image
        # ---------------------------------
        try:
            picam.capture_file(image_path)
            if os.path.exists(image_path):
                print(f"[BEAM] Picture saved: {image_path}")
            else:
                print(f"[BEAM] Capture finished but file not found: {image_path}")
        except Exception as e:
            print(f"[BEAM] Picture capture failed: {e}")
            # Kill audio if image failed so we don't leave a dangling process
            if audio_proc and audio_proc.poll() is None:
                audio_proc.terminate()
            last_motion_state = current_motion_state
            time.sleep(poll_interval)
            continue

        # Turn flash off after capture
        if flash_enabled and flash is not None:
            flash.off()

        # ---------------------------------
        # Wait for audio to finish
        # (non-blocking during cooldown)
        # ---------------------------------
        if audio_proc:
            try:
                audio_proc.wait(timeout=AUDIO_DURATION + 5)
                _, stderr_output = audio_proc.communicate(timeout=1)
                if audio_proc.returncode != 0 and stderr_output:
                    print(f"[BEAM] Audio warning: {stderr_output.decode().strip()}")
                if os.path.exists(audio_path):
                    print(f"[BEAM] Audio saved: {audio_path}")
                else:
                    print(f"[BEAM] Audio process finished but file not found: {audio_path}")
                    audio_path = None
            except subprocess.TimeoutExpired:
                print("[BEAM] Audio recording timed out, terminating.")
                audio_proc.terminate()
                audio_path = None
            except Exception as e:
                print(f"[BEAM] Audio wait error: {e}")
                audio_path = None

        # ---------------------------------
        # Save log
        # ---------------------------------
        record = {
            "timestamp_utc": timestamp_iso,
            "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": now_local.tzname(),
            "file": image_path,
            "lux": lux,
            # Audio field is always present; None if audio disabled or failed
            "audio_file": audio_path if AUDIO_ENABLED else None,
        }

        try:
            if os.path.exists(log_path):
                with open(log_path, "r") as f:
                    try:
                        data = json.load(f)
                        if not isinstance(data, dict) or "records" not in data:
                            data = {"node_id": node_id, "sensor": "camera", "records": []}
                    except Exception:
                        data = {"node_id": node_id, "sensor": "camera", "records": []}
            else:
                data = {"node_id": node_id, "sensor": "camera", "records": []}

            data["records"].append(record)
            with open(log_path, "w") as f:
                json.dump(data, f, indent=4)
            print(f"[BEAM] Capture logged to: {log_path}")
        except Exception as e:
            print("[ERROR] Failed to save log:", e)

        time.sleep(cooldown)

    elif not current_motion_state and last_motion_state and global_config.get("print_debug", True):
        print("[BEAM] Motion ended")

    last_motion_state = current_motion_state
    time.sleep(poll_interval)
