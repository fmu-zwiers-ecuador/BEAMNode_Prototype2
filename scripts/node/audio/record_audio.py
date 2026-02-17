import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parents[1] / "vendor"
sys.path.insert(0, str(VENDOR_DIR))

# record.py — Unified Audio Recorder for BEAM
# Author: Raiz Mohammed / Jaidyn Edwards / Jackson Roberts
# Updated: 2026-02-17

import os
import json
import time
import wave
import pyaudio
from datetime import datetime, timezone
import ctypes
from ctypes.util import find_library

# Suppress ALSA warnings (from PyAudio backend)
try:
    def py_error_handler(filename, line, function, err, fmt):
        pass  # Do nothing (silence ALSA C errors)

    c_error_handler = ctypes.CFUNCTYPE(
        None,
        ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_int, ctypes.c_char_p
    )(py_error_handler)

    asound = ctypes.CDLL(find_library('asound'))
    asound.snd_lib_error_set_handler(c_error_handler)
except Exception:
    pass

# Determine project root dynamically
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Load config
config_path = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"
with open(config_path, "r") as f:
    config = json.load(f)

audio_config = config["audio"]
global_config = config["global"]

# --- UPDATED TIME CALCULATIONS ---
now_utc = datetime.now(timezone.utc)
now_local = now_utc.astimezone()

# Base directory setup
base_dir = global_config.get("base_dir", os.path.join(project_root, "data"))
directory = os.path.join(base_dir, audio_config.get("directory", "audio"))
os.makedirs(directory, exist_ok=True)

# File path setup (Using a clean timestamp for the filename)
file_ts = now_utc.strftime("%Y%m%d_%H%M%SZ")
file_prefix = audio_config.get("file_prefix", "recording_")
wav_filename = os.path.join(directory, f"{file_prefix}{file_ts}.wav")

# Recording parameters
DURATION = audio_config.get("duration_sec", 10)
RATE = audio_config.get("sample_rate", 48000)
CHANNELS = audio_config.get("channels", 1)
FORMAT = pyaudio.paInt16 if audio_config.get("format", "int16") == "int16" else pyaudio.paFloat32
CHUNK = audio_config.get("chunk", 1024)

# Initialize audio interface
audio = pyaudio.PyAudio()
stream = audio.open(format=FORMAT, channels=CHANNELS,
                    rate=RATE, input=True,
                    frames_per_buffer=CHUNK)

if global_config.get("print_debug", True):
    print(f"[BEAM] Recording {DURATION}s of audio to {wav_filename}")

frames = []
for _ in range(0, int(RATE / CHUNK * DURATION)):
    data = stream.read(CHUNK, exception_on_overflow=False)
    frames.append(data)

stream.stop_stream()
stream.close()
audio.terminate()

# Save .wav file
with wave.open(wav_filename, 'wb') as wf:
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(audio.get_sample_size(FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(b''.join(frames))

if global_config.get("print_debug", True):
    print(f"[BEAM] Saved audio file: {wav_filename}")

# Create MASTER.json in same directory
master_json = os.path.join(directory, "MASTER.json")

# --- UPDATED RECORD ENTRY ---
record_entry = {
    "timestamp_utc": now_utc.isoformat(),
    "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
    "timezone": now_local.tzname(),
    "file": wav_filename,
    "duration_sec": DURATION,
    "sample_rate": RATE,
    "channels": CHANNELS,
    "format": "int16"
}

# Append to MASTER.json
if not os.path.exists(master_json):
    with open(master_json, "w") as f:
        json.dump({"node_id": global_config.get("node_id"), "sensor": "audio", "records": []}, f, indent=4)

with open(master_json, "r+") as f:
    try:
        log = json.load(f)
    except Exception:
        log = {"node_id": global_config.get("node_id"), "sensor": "audio", "records": []}
    
    if "records" not in log:
        log["records"] = []
    
    log["records"].append(record_entry)
    f.seek(0)
    json.dump(log, f, indent=4)
    f.truncate()

if global_config.get("print_debug", True):
    print(f"[BEAM] Logged record to {master_json}")
