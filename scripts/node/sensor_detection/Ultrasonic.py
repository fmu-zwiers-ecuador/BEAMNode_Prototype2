import RPi.GPIO as GPIO
import time

TRIG = 23
ECHO = 24

GPIO.setmode(GPIO.BCM)
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)

GPIO.output(TRIG, False)
time.sleep(2)

def read_distance():
    # send trigger pulse
    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    timeout = time.time()

    # wait for echo to go high
    while GPIO.input(ECHO) == 0:
        pulse_start = time.time()
        if pulse_start - timeout > 0.05:
            return None

    # wait for echo to go low
    while GPIO.input(ECHO) == 1:
        pulse_end = time.time()
        if pulse_end - pulse_start > 0.05:
            return None

    pulse_duration = pulse_end - pulse_start
    distance_cm = pulse_duration * 17150
    return round(distance_cm, 2)

try:
    print("Testing ultrasonic sensor...")
    for i in range(10):
        distance = read_distance()
        if distance is None:
            print(f"Test {i+1}: No echo detected")
        else:
            print(f"Test {i+1}: {distance} cm")
        time.sleep(1)

except KeyboardInterrupt:
    print("Stopped by user")

finally:
    GPIO.cleanup()