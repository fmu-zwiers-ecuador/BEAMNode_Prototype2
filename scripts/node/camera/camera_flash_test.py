from gpiozero import OutputDevice
from picamera2 import Picamera2
import time

flash = OutputDevice(17)
picam = Picamera2()

config = picam.create_still_configuration(
    main={"size": (1920,1080)}
)

picam.configure(config)
picam.start()

time.sleep(2)

print("Flash ON")
flash.on()

time.sleep(1)

picam.capture_file("flash_test.jpg")

flash.off()

print("Flash OFF")
print("Image saved: flash_test.jpg")