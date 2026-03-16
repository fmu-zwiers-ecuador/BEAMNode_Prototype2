import time
import board
import digitalio
import busio
import adafruit_rfm9x
import os

# -------- CONFIG --------
FILE_PATH = "send_file.bin"
CHUNK_SIZE = 180
FREQ = 915.0
# ------------------------

# Setup SPI
spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

cs = digitalio.DigitalInOut(board.CE1)
reset = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, FREQ)

rfm9x.tx_power = 23

file_size = os.path.getsize(FILE_PATH)

print("Sending file:", FILE_PATH)
print("File size:", file_size)

# Send file header
header = f"START:{file_size}"
rfm9x.send(header.encode())
time.sleep(1)

with open(FILE_PATH, "rb") as f:

    seq = 0

    while True:
        chunk = f.read(CHUNK_SIZE)

        if not chunk:
            break

        packet = seq.to_bytes(4, "big") + chunk
        rfm9x.send(packet)

        print("Sent chunk", seq)

        seq += 1
        time.sleep(0.25)

rfm9x.send(b"END")

print("File transmission complete")