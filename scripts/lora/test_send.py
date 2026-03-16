import time
import board
import digitalio
import busio
import adafruit_rfm9x

# Setup SPI
spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

# Chip select
cs = digitalio.DigitalInOut(board.CE1)

# Reset pin
reset = digitalio.DigitalInOut(board.D25)

# Initialize radio
rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, 915.0)

rfm9x.tx_power = 23

print("LoRa transmitter ready")

counter = 0

while True:
    input("Press ENTER to send packet...")

    message = f"Test packet {counter}"
    rfm9x.send(bytes(message, "utf-8"))

    print("Sent:", message)

    counter += 1
    time.sleep(0.2)