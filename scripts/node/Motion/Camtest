#!/usr/bin/env python3

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
import time
import os
from datetime import datetime

# Folder where videos will be saved
SAVE_DIR = "/home/pi/videos"
os.makedirs(SAVE_DIR, exist_ok=True)

# Make filename with date and time
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
video_file = f"{SAVE_DIR}/video_{timestamp}.h264"

# Camera setup
picam2 = Picamera2()

video_config = picam2.create_video_configuration(
    main={"size": (1280, 720)}
)

picam2.configure(video_config)

encoder = H264Encoder(bitrate=10000000)

print("Starting 10 second video recording...")
print(f"Saving to: {video_file}")

picam2.start_recording(encoder, video_file)

time.sleep(10)

picam2.stop_recording()
picam2.close()

print("Recording finished.")