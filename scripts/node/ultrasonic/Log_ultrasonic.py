import RPi.GPIO as GPIO
import time
import os
from datetime import datetime

TRIG = 20
ECHO = 21
LOG_DIR = "/home/pi/logs"
LOG_FILE = os.path.join(LOG_DIR, "ultrasonic_log.csv")

GPIO.setmode(GPIO.BCM)
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)

def get_distance():
    GPIO.output(TRIG, False)
    time.sleep(0.2)
    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    timeout = time.time() + 1
    pulse_start = time.time()
    while GPIO.input(ECHO) == 0:
        pulse_start = time.time()
        if time.time() > timeout:
            return None

    pulse_end = time.time()
    while GPIO.input(ECHO) == 1:
        pulse_end = time.time()
        if time.time() > timeout:
            return None

    pulse_duration = pulse_end - pulse_start
    distance = pulse_duration * 17150
    return round(distance, 2)

try:
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write("timestamp,distance_cm\n")

    while True:
        distance = get_distance()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a") as f:
            if distance is None:
                f.write(f"{now},ERROR\n")
            else:
                f.write(f"{now},{distance}\n")
        time.sleep(2)

except KeyboardInterrupt:
    pass

finally:
    GPIO.cleanup()