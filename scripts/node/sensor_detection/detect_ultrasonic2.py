import RPi.GPIO as GPIO
import time

TRIG = 23
ECHO = 24
THRESHOLD_CM = 30    # alert if object is closer than this
INTERVAL     = 0.2   # seconds between readings

GPIO.setmode(GPIO.BCM)
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)

def get_distance():
    GPIO.output(TRIG, False)
    time.sleep(0.05)

    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    t = time.time()
    while GPIO.input(ECHO) == 0:
        pulse_start = time.time()
        if time.time() - t > 1:
            return None

    t = time.time()
    while GPIO.input(ECHO) == 1:
        pulse_end = time.time()
        if time.time() - t > 1:
            return None

    return round((pulse_end - pulse_start) * 17150, 2)

print(f"Detection running — alert threshold: {THRESHOLD_CM} cm\n")

try:
    while True:
        dist = get_distance()
        if dist is None:
            print("[WARN] No reading — check wiring")
        elif dist < THRESHOLD_CM:
            print(f"[ALERT] Object detected at {dist} cm!")
        else:
            print(f"  Clear — {dist} cm")
        time.sleep(INTERVAL)
except KeyboardInterrupt:
    print("\nStopped.")
finally:
    GPIO.cleanup()