from gpiozero import OutputDevice
from time import sleep

FLASH_GPIO = 17

flash = OutputDevice(FLASH_GPIO)

print("Flash test starting...")

while True:

    print("Flash ON")
    flash.on()
    sleep(2)

    print("Flash OFF")
    flash.off()
    sleep(2)