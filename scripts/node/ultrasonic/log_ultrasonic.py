#----- Log_ultrasonic.py: Logs data from the ultrasonic sensor by sending high and low echo signals----#
#------------------------ Authors: Jaylen Small, Noel Challa ------------------------------------------#
import RPi.GPIO as GPIO
import time
import statistics
import os
from datetime import datetime

# ---------------- CONFIG ----------------
TRIG = 20
ECHO = 21

LOG_DIR = "/home/pi/logs"
LOG_FILE = os.path.join(LOG_DIR, "ultrasonic_log.csv")

SAMPLES = 5
MAX_DISTANCE = 400  # cm
TIMEOUT = 0.03      # 30 ms

# ---------------- GPIO SETUP ----------------
GPIO.setmode(GPIO.BCM)

print("Distance measurement in progress")
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)

GPIO.output(TRIG, False)
print("Waiting for sensor to settle...")
time.sleep(2)

def measure_distance():
    # Ensure trigger is LOW
    GPIO.output(TRIG, False)
    time.sleep(0.0002)

    # Send 10 us pulse
    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    start_time = time.perf_counter()
    timeout_start = start_time

    # Wait for echo to go HIGH
    while GPIO.input(ECHO) == 0:
        start_time = time.perf_counter()
        if start_time - timeout_start > TIMEOUT:
            return None

    stop_time = time.perf_counter()

    # Wait for echo to go LOW
    while GPIO.input(ECHO) == 1:
        stop_time = time.perf_counter()
        if stop_time - start_time > TIMEOUT:
            return None

    elapsed = stop_time - start_time

    # Speed of sound = 34300 cm/s
    distance = (elapsed * 34300) / 2

    if distance <= 0 or distance > MAX_DISTANCE:
        return None

    return round(distance, 2)


# ---------------- TAKE MULTIPLE READINGS ----------------
readings = []
counter = 1 # Keeps track of how many readings have been taken

for _ in range(SAMPLES):
    d = measure_distance()

    if d is not None:
        print(f"Distance {counter}: {d} cm")
        readings.append(d)
    else:
        print(f"Reading {counter} has been skipped because of invalid data")

    counter += 1
    time.sleep(0.05)

if readings:
    distance = round(statistics.median(readings), 2)

    print(f"Average distance: {distance} cm")

    # -------- LOGGING --------
    os.makedirs(LOG_DIR, exist_ok=True)

    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write("timestamp,distance_cm\n")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(LOG_FILE, "a") as f:
        f.write(f"{now},{distance}\n")

    print("Result saved to:", LOG_FILE)

else:
    print("No valid readings")

GPIO.cleanup()
