# detect_ultrasonic.py

import RPi.GPIO as GPIO
import time

TRIG = 20
ECHO = 21

GPIO.setmode(GPIO.BCM)
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)

GPIO.output(TRIG, False)
time.sleep(2)

detected = False

try:
    # send trigger pulse
    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    timeout = time.time() + 1

    while GPIO.input(ECHO) == 0:
        pulse_start = time.time()
        if time.time() > timeout:
            break

    while GPIO.input(ECHO) == 1:
        pulse_end = time.time()
        detected = True
        if time.time() > timeout:
            break

    if detected:
        print("Ultrasonic sensor detected")
    else:
        print("Ultrasonic sensor not detected")

except Exception as e:
    print("Error:", e)

finally:
    GPIO.cleanup()