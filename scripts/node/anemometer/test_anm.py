import time
from gpiozero import Button

pin = 17  # BCM GPIO17 (physical pin 11)
sensor = Button(pin, pull_up=True)

count = 0
def pulse():
    global count
    count += 1

sensor.when_pressed = pulse

print("Anemometer test on GPIO17. Ctrl+C to stop.")
try:
    while True:
        count = 0
        time.sleep(1)
        print("pulses/sec:", count)
except KeyboardInterrupt:
    pass
