import board
import digitalio
import busio
import adafruit_rfm9x
import struct
import zlib
import os

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


def parse_node_file_header(packet):
    """Parse node_id and file_id from packet header."""
    pos = 0
    node_len = packet[pos]
    pos += 1
    node_id = packet[pos:pos+node_len].decode()
    pos += node_len
    file_len = packet[pos]
    pos += 1
    file_id = packet[pos:pos+file_len].decode()
    pos += file_len
    return node_id, file_id, pos


def send_ack(node_id, file_id, seq):
    """Send ACK packet with node_id, file_id, and sequence number."""
    node_bytes = node_id.encode()
    file_bytes = file_id.encode()
    ack = struct.pack("B", len(node_bytes)) + node_bytes + struct.pack("B", len(file_bytes)) + file_bytes + struct.pack(">I", seq)
    rfm9x.send(ack)

while True:

    packet = rfm9x.receive(timeout=2)

    if packet is None:
        continue

    try:
        if len(packet) < 3:  # Malformed, skip
            continue

        # Try to parse header
        node_id, file_id, header_end = parse_node_file_header(packet)
        transfer_key = (node_id, file_id)

        # Check if START header
        if packet[header_end:header_end+5] == b"START":
            # Parse: START:file_size:num_chunks
            parts_str = packet[header_end:].decode().split(":")
            file_size = int(parts_str[1])
            num_chunks = int(parts_str[2])

            transfers[transfer_key] = {
                "chunks": {},
                "expected": num_chunks,
                "file_size": file_size
            }

            print(f"\n[{node_id}] Incoming file {file_id}")
            print(f"  Size: {file_size} bytes, {num_chunks} chunks")
            continue

        # Check if END packet
        if packet[header_end:header_end+3] == b"END":
            if transfer_key in transfers:
                transfer = transfers[transfer_key]
                if len(transfer["chunks"]) == transfer["expected"]:
                    # Reconstruct file
                    data = b''.join(transfer["chunks"][i] for i in range(transfer["expected"]))

                    output_file = os.path.join(OUTPUT_DIR, f"{node_id}_{file_id}.bin")
                    with open(output_file, "wb") as f:
                        f.write(data)

                    print(f"[{node_id}] Transfer complete - saved {output_file}")
                    del transfers[transfer_key]
                else:
                    print(f"[{node_id}] END received but missing {transfer['expected'] - len(transfer['chunks'])} chunks")
            continue

        # Otherwise, parse as DATA packet
        if transfer_key not in transfers:
            print(f"[{node_id}] Received chunk before START header, skipping")
            continue

        # Parse sequence and checksum
        seq, checksum = struct.unpack(">I I", packet[header_end:header_end+8])
        data = packet[header_end+8:]

        calc_checksum = zlib.crc32(data)

        if calc_checksum != checksum:
            print(f"[{node_id}] Checksum error on chunk {seq}")
            send_ack(node_id, file_id, seq)  # Still ACK to not stall sender
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