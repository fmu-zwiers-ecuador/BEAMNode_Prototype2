import time
import os
import zlib
import random
import json
import base64
import board
import digitalio
import busio
import adafruit_rfm9x

# -------- CONFIG --------
FILE_PATH = "send_file.bin"
CHUNK_SIZE = 100
MAX_RETRIES = 10
ACK_TIMEOUT = 2
FREQ = 915.0
NODE_ID = "node1"  # Unique identifier for this sender node
COLLISION_BACKOFF_MAX = 5  # Max random delay in seconds for collision avoidance
# ------------------------

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

cs = digitalio.DigitalInOut(board.CE0)
reset = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, FREQ)
rfm9x.tx_power = 23


def send_json(message):
    rfm9x.send(json.dumps(message, separators=(",", ":")).encode())


def receive_json(timeout=0.5):
    packet = rfm9x.receive(timeout=timeout)
    if not packet:
        return None
    try:
        return json.loads(packet.decode())
    except Exception:
        return None


def wait_for_ack(node_id, file_id, seq, timeout):
    start = time.time()
    while time.time() - start < timeout:
        msg = receive_json(timeout=0.5)
        if not msg:
            continue

        if (
            msg.get("type") == "ACK"
            and msg.get("node_id") == node_id
            and msg.get("file_id") == file_id
            and msg.get("seq") == seq
        ):
            return True

    return False

file_size = os.path.getsize(FILE_PATH)
file_id = str(int(time.time() * 1000))  # Unique file ID based on timestamp

with open(FILE_PATH, "rb") as f:
    file_data = f.read()

chunks = [file_data[i:i+CHUNK_SIZE] for i in range(0, len(file_data), CHUNK_SIZE)]

print(f"Sending file: {FILE_PATH}")
print(f"Node ID: {NODE_ID}, File ID: {file_id}")
print(f"Chunks: {len(chunks)}")

# Random backoff to reduce collision probability
backoff = random.uniform(0, COLLISION_BACKOFF_MAX)
print(f"Collision avoidance backoff: {backoff:.2f}s")
time.sleep(backoff)

# Send START metadata
send_json(
    {
        "type": "START",
        "node_id": NODE_ID,
        "file_id": file_id,
        "file_name": os.path.basename(FILE_PATH),
        "file_size": file_size,
        "total_chunks": len(chunks),
    }
)
time.sleep(0.5)

for seq, data in enumerate(chunks):

    checksum = zlib.crc32(data) & 0xFFFFFFFF

    retries = 0
    acked = False

    while not acked and retries < MAX_RETRIES:

        print(f"Sending chunk {seq}/{len(chunks)}")

        send_json(
            {
                "type": "DATA",
                "node_id": NODE_ID,
                "file_id": file_id,
                "seq": seq,
                "crc32": checksum,
                "data_b64": base64.b64encode(data).decode(),
            }
        )
        time.sleep(0.1)  # Small delay to allow receiver to process

        acked = wait_for_ack(NODE_ID, file_id, seq, ACK_TIMEOUT)
        if acked:
            print(f"ACK received for chunk {seq}")

        if not acked:
            retries += 1
            print(f"Retry {retries}/{MAX_RETRIES}")

    if not acked:
        print(f"Failed to deliver chunk {seq}")
        exit(1)

# Send END marker
send_json(
    {
        "type": "END",
        "node_id": NODE_ID,
        "file_id": file_id,
    }
)

print("File sent successfully")