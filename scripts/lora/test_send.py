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
WINDOW_SIZE = 5
MAX_RETRIES = 5
FREQ = 915.0
# ------------------------

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

cs = digitalio.DigitalInOut(board.CE1)
reset = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, FREQ)

rfm9x.tx_power = 23

file_size = os.path.getsize(FILE_PATH)

with open(FILE_PATH, "rb") as f:
    file_data = f.read()

chunks = [file_data[i:i+CHUNK_SIZE] for i in range(0, len(file_data), CHUNK_SIZE)]
total_chunks = len(chunks)

print("Sending file:", FILE_PATH)
print("Chunks:", total_chunks)

# send header
header = f"START:{file_size}:{total_chunks}"
rfm9x.send(header.encode())
time.sleep(1)

acked = set()
window_start = 0

while window_start < total_chunks:

    window_end = min(window_start + WINDOW_SIZE, total_chunks)

    # send packets in window
    for seq in range(window_start, window_end):

        if seq in acked:
            continue

        data = chunks[seq]
        checksum = zlib.crc32(data)

        packet = struct.pack(">I I", seq, checksum) + data

        rfm9x.send(packet)

        print("Sent", seq)

        time.sleep(0.1)

    # listen for ACKs
    start_wait = time.time()

    while time.time() - start_wait < 2:

        ack = rfm9x.receive(timeout=0.5)

        if ack and ack.startswith(b"ACK"):

            seq = int.from_bytes(ack[3:], "big")

            acked.add(seq)

            print("ACK", seq)

    # slide window
    while window_start in acked:
        window_start += 1

# send completion
rfm9x.send(b"END")

print("File sent successfully")