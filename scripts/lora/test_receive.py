import board
import digitalio
import busio
import adafruit_rfm9x

# -------- CONFIG --------
OUTPUT_FILE = "received_file.bin"
FREQ = 915.0
# ------------------------

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

cs = digitalio.DigitalInOut(board.CE0)
reset = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, FREQ)

print("Waiting for file...")

file = None
expected_size = None
bytes_received = 0

while True:

    packet = rfm9x.receive(timeout=5)

    if packet is None:
        continue

    # Header
    if packet.startswith(b"START:"):
        expected_size = int(packet.decode().split(":")[1])

        print("Incoming file size:", expected_size)

        file = open(OUTPUT_FILE, "wb")
        bytes_received = 0
        continue

    # End signal
    if packet == b"END":
        if file:
            file.close()
        print("File transfer complete")
        break

    # Data packet
    seq = int.from_bytes(packet[:4], "big")
    data = packet[4:]

    if file:
        file.write(data)
        bytes_received += len(data)

    print("Received chunk", seq, "Total bytes:", bytes_received)