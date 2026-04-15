import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)

TRIG = 20
ECHO = 21

print("Distance measurement in progress")

GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)

GPIO.output(TRIG, False)
print("Waiting for sensor to settle")
time.sleep(2)

GPIO.output(TRIG, True)
time.sleep(0.00001)
GPIO.output(TRIG, False)

timeout = time.time() + 1

while GPIO.input(ECHO) == 0:
    pulse_start = time.time()
    if time.time() > timeout:
        print("Sensor not detected")
        GPIO.cleanup()
        exit()

while GPIO.input(ECHO) == 1:
    pulse_end = time.time()
    if time.time() > timeout:
        print("Sensor not detected")
        GPIO.cleanup()
        exit()

pulse_duration = pulse_end - pulse_start
distance = pulse_duration * 17150
distance = round(distance, 2)

print("Distance:", distance, "cm")

GPIO.cleanup()