import board
import digitalio
import busio
import adafruit_rfm9x
import struct
import zlib

# -------- CONFIG --------
OUTPUT_FILE = "received_file.bin"
FREQ = 915.0
# ------------------------

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

cs = digitalio.DigitalInOut(board.CE1)
reset = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, FREQ)

print("Waiting for file...")

expected_chunks = None
received = {}

while True:

    packet = rfm9x.receive(timeout=5)

    if packet is None:
        continue

    # start header
    if packet.startswith(b"START:"):

        parts = packet.decode().split(":")

        file_size = int(parts[1])
        expected_chunks = int(parts[2])

        print("Incoming file")
        print("Size:", file_size)
        print("Chunks:", expected_chunks)

        continue

    if packet == b"END":

        print("Transfer finished")

        with open(OUTPUT_FILE, "wb") as f:

            for i in range(expected_chunks):
                f.write(received[i])

        print("File written:", OUTPUT_FILE)

        break

    # parse packet
    seq, checksum = struct.unpack(">I I", packet[:8])
    data = packet[8:]

    calc_checksum = zlib.crc32(data)

    if calc_checksum != checksum:

        print("Checksum failed for", seq)

        continue

    if seq not in received:

        received[seq] = data

        print("Received", seq)

    # send ACK
    ack = b"ACK" + seq.to_bytes(4, "big")

    rfm9x.send(ack)