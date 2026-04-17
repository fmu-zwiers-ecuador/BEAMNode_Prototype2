import RPi.GPIO as GPIO
import time

TRIG = 23
ECHO = 24

GPIO.setmode(GPIO.BCM)
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)

def get_distance():
    GPIO.output(TRIG, False)
    time.sleep(0.05)

    GPIO.output(TRIG, True)
    time.sleep(0.00001)   # 10µs pulse
    GPIO.output(TRIG, False)

    timeout = time.time() + 1
    while GPIO.input(ECHO) == 0:
        pulse_start = time.time()
        if time.time() > timeout:
            return None

    timeout = time.time() + 1
    while GPIO.input(ECHO) == 1:
        pulse_end = time.time()
        if time.time() > timeout:
            return None

    duration = pulse_end - pulsestart
    distance = duration * 17150   # speed of sound / 2, in cm
    return round(distance, 2)

try:
    print("Testing HC-SR04... Press Ctrl+C to stop.\n")
    for  in range(10):
        d = get_distance()
        if d is not None:
            print(f"Distance: {d} cm")
        else:
            print("Timeout — check wiring")
        time.sleep(0.5)
finally:
    GPIO.cleanup()