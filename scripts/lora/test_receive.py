import time
import board
import digitalio
import busio
import adafruit_rfm9x

# SPI bus
spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

# Chip select
cs = digitalio.DigitalInOut(board.CE0)

# Reset
reset = digitalio.DigitalInOut(board.D25)

# Initialize radio
rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, 915.0)

print("LoRa receiver ready")

while True:
    packet = rfm9x.receive(timeout=5)

    if packet is None:
        print("Waiting...")
    else:
        message = str(packet, "utf-8")
        print("Received:", message)
        print("RSSI:", rfm9x.last_rssi)

    time.sleep(0.5)