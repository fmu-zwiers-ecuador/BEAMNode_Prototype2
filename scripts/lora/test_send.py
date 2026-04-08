import time
import os
import zlib
import struct
import random
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
NODE_ID = "node1"  # Unique identifier for this sender node
COLLISION_BACKOFF_MAX = 5  # Max random delay in seconds for collision avoidance
# ------------------------

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

cs = digitalio.DigitalInOut(board.CE0)
reset = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, FREQ)
rfm9x.tx_power = 23

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

# send header with node_id and file_id
header = f"START:{NODE_ID}:{file_id}:{file_size}:{len(chunks)}"
node_id_bytes = NODE_ID.encode()
file_id_bytes = file_id.encode()
header_packet = struct.pack("B", len(node_id_bytes)) + node_id_bytes + struct.pack("B", len(file_id_bytes)) + file_id_bytes + header.encode()
rfm9x.send(header_packet)
time.sleep(0.5)

for seq, data in enumerate(chunks):

    checksum = zlib.crc32(data)
    # Packet format: node_id_len (1 byte) | node_id | file_id_len (1 byte) | file_id | seq (4 bytes) | checksum (4 bytes) | data
    node_id_bytes = NODE_ID.encode()
    file_id_bytes = file_id.encode()
    packet = struct.pack("B", len(node_id_bytes)) + node_id_bytes + struct.pack("B", len(file_id_bytes)) + file_id_bytes + struct.pack(">I I", seq, checksum) + data

    retries = 0
    acked = False

    while not acked and retries < MAX_RETRIES:

        print(f"Sending chunk {seq}/{len(chunks)}")

        rfm9x.send(packet)
        time.sleep(0.1)  # Small delay to allow receiver to process

        start = time.time()

        while time.time() - start < ACK_TIMEOUT:

            ack = rfm9x.receive(timeout=0.5)

            if ack:
                try:
                    # Parse ACK: node_id_len | node_id | file_id_len | file_id | ack_seq (4 bytes)
                    pos = 0
                    ack_node_len = ack[pos]
                    pos += 1
                    ack_node = ack[pos:pos+ack_node_len].decode()
                    pos += ack_node_len
                    ack_file_len = ack[pos]
                    pos += 1
                    ack_file = ack[pos:pos+ack_file_len].decode()
                    pos += ack_file_len
                    ack_seq = int.from_bytes(ack[pos:pos+4], "big")

                    if ack_node == NODE_ID and ack_file == file_id and ack_seq == seq:
                        print(f"ACK received for chunk {seq}")
                        acked = True
                        break
                except Exception as e:
                    print(f"Failed to parse ACK: {e}")
                    pass

        if not acked:
            retries += 1
            print(f"Retry {retries}/{MAX_RETRIES}")

    if not acked:
        print(f"Failed to deliver chunk {seq}")
        exit(1)

# Send END packet with node_id and file_id
node_id_bytes = NODE_ID.encode()
file_id_bytes = file_id.encode()
end_packet = struct.pack("B", len(node_id_bytes)) + node_id_bytes + struct.pack("B", len(file_id_bytes)) + file_id_bytes + b"END"
rfm9x.send(end_packet)

print("File sent successfully")