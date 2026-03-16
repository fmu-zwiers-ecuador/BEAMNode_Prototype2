import time
import os
import zlib
import struct
import board
import digitalio
import busio
import adafruit_rfm9x

# -------- CONFIG --------
FILE_PATH = "send_file.bin"
CHUNK_SIZE = 180
MAX_RETRIES = 10
ACK_TIMEOUT = 2
FREQ = 915.0
# ------------------------

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

cs = digitalio.DigitalInOut(board.CE0)
reset = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, FREQ)
rfm9x.tx_power = 23

file_size = os.path.getsize(FILE_PATH)

with open(FILE_PATH, "rb") as f:
    file_data = f.read()

chunks = [file_data[i:i+CHUNK_SIZE] for i in range(0, len(file_data), CHUNK_SIZE)]

print("Sending file:", FILE_PATH)
print("Chunks:", len(chunks))

# send header
header = f"START:{file_size}:{len(chunks)}"
rfm9x.send(header.encode())
time.sleep(1)

for seq, data in enumerate(chunks):

    checksum = zlib.crc32(data)
    packet = struct.pack(">I I", seq, checksum) + data

    retries = 0
    acked = False

    while not acked and retries < MAX_RETRIES:

        print("Sending chunk", seq)

        rfm9x.send(packet)

        start = time.time()

        while time.time() - start < ACK_TIMEOUT:

            ack = rfm9x.receive(timeout=0.5)

            if ack and ack.startswith(b"ACK"):

                ack_seq = int.from_bytes(ack[3:], "big")

                if ack_seq == seq:

                    print("ACK received", seq)
                    acked = True
                    break

        if not acked:

            retries += 1
            print("Retry", retries)

    if not acked:

        print("Failed to deliver chunk", seq)
        exit(1)

rfm9x.send(b"END")

print("File sent successfully")