from gpiozero import MotionSensor
from datetime import datetime
from signal import pause

# Connect the PIR data pin to GPIO 24
pir = MotionSensor(24)

print("PIR Sensor Test (Press Ctrl+C to exit)")
print("-" * 39)

def motion_triggered():
    print(f"{datetime.now().strftime('%H:%M:%S')} - Motion Detected!")

def motion_stopped():
    print(f"{datetime.now().strftime('%H:%M:%S')} - Motion Stopped. Ready.")

# Assign event handlers
pir.when_motion = motion_triggered
pir.when_no_motion = motion_stopped

# Keep the script running
pause()
