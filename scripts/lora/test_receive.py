import board
import digitalio
import busio
import adafruit_rfm9x
import zlib
import os
import json
import base64

# -------- CONFIG --------
OUTPUT_DIR = "./received_files/"
FREQ = 915.0
# ------------------------

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

cs = digitalio.DigitalInOut(board.CE0)
reset = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, FREQ)

print("Waiting for files from multiple nodes...")

# Track multiple concurrent transfers: {(node_id, file_id): {chunks, expected_total}}
transfers = {}

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)


def send_ack(node_id, file_id, seq):
    ack = {
        "type": "ACK",
        "node_id": node_id,
        "file_id": file_id,
        "seq": seq,
    }
    rfm9x.send(json.dumps(ack, separators=(",", ":")).encode())


def receive_json(timeout=2):
    packet = rfm9x.receive(timeout=timeout)
    if not packet:
        return None
    try:
        return json.loads(packet.decode())
    except Exception:
        return None

while True:

    msg = receive_json(timeout=2)

    if not msg:
        continue

    try:
        msg_type = msg.get("type")
        node_id = msg.get("node_id")
        file_id = msg.get("file_id")

        if not msg_type or not node_id or not file_id:
            continue

        transfer_key = (node_id, file_id)

        # START metadata
        if msg_type == "START":
            file_size = int(msg.get("file_size", 0))
            num_chunks = int(msg.get("total_chunks", 0))

            if num_chunks <= 0:
                print(f"[{node_id}] Invalid START for file {file_id}")
                continue

            transfers[transfer_key] = {
                "chunks": {},
                "expected": num_chunks,
                "file_size": file_size,
                "file_name": msg.get("file_name", "received.bin"),
            }

            print(f"\n[{node_id}] Incoming file {file_id}")
            print(f"  Size: {file_size} bytes, {num_chunks} chunks")
            continue

        # END marker
        if msg_type == "END":
            if transfer_key in transfers:
                transfer = transfers[transfer_key]
                if len(transfer["chunks"]) == transfer["expected"]:
                    # Reconstruct file
                    data = b''.join(transfer["chunks"][i] for i in range(transfer["expected"]))

                    safe_name = transfer["file_name"].replace("/", "_")
                    output_file = os.path.join(OUTPUT_DIR, f"{node_id}_{file_id}_{safe_name}")
                    with open(output_file, "wb") as f:
                        f.write(data)

                    print(f"[{node_id}] Transfer complete - saved {output_file}")
                    del transfers[transfer_key]
                else:
                    print(f"[{node_id}] END received but missing {transfer['expected'] - len(transfer['chunks'])} chunks")
            continue

        if msg_type != "DATA":
            continue

        # DATA chunks
        if transfer_key not in transfers:
            print(f"[{node_id}] Received chunk before START header, skipping")
            continue

        seq = msg.get("seq")
        checksum = msg.get("crc32")
        data_b64 = msg.get("data_b64")

        if seq is None or checksum is None or not data_b64:
            continue

        data = base64.b64decode(data_b64)

        calc_checksum = zlib.crc32(data) & 0xFFFFFFFF

        if calc_checksum != checksum:
            print(f"[{node_id}] Checksum error on chunk {seq}")
            continue

        # Store chunk if not already received
        if seq not in transfers[transfer_key]["chunks"]:
            transfers[transfer_key]["chunks"][seq] = data
            received_count = len(transfers[transfer_key]["chunks"])
            expected_count = transfers[transfer_key]["expected"]
            print(f"[{node_id}] Chunk {seq} ({received_count}/{expected_count})")

        # Send selective ACK
        send_ack(node_id, file_id, seq)

    except Exception as e:
        print(f"Error processing packet: {e}")
        continue