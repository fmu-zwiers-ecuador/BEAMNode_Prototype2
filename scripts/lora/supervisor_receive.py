import time, json, base64, zlib, hashlib
import board, busio, digitalio
import adafruit_rfm9x

# --- LoRa setup ---
spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
cs = digitalio.DigitalInOut(board.CE1)
reset = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, 915.0)
rfm9x.tx_power = 23

# --- STORAGE ---
files = {}
ACK_BACKOFF = 0.12
ACK_REPEATS = 2
ACK_REPEAT_GAP = 0.08

def send_ack(file_id, node_id, chunk_index, received):
    pkt = {
        "type": "ACK",
        "f": file_id,
        "n": node_id,
        "i": chunk_index,
        "received": list(received)
    }
    packet = json.dumps(pkt)
    # Give sender time to switch from TX to RX before ACK is sent.
    time.sleep(ACK_BACKOFF)

    # Send ACK more than once to improve reliability on lossy links.
    for attempt in range(ACK_REPEATS):
        try:
            rfm9x.send(packet.encode(), keep_listening=True)
        except TypeError:
            # Older library versions may not support keep_listening kwarg
            rfm9x.send(packet.encode())
        if attempt < ACK_REPEATS - 1:
            time.sleep(ACK_REPEAT_GAP)

    print(f"ACK sent for {file_id}, chunk {chunk_index}, received chunks: {received}")

def handle_data(pkt):
    print(f"DEBUG: Received DATA packet - file_id: {pkt.get('f')}, chunk: {pkt.get('i')}")
    file_id = pkt["f"]
    node_id = pkt["n"]

    if file_id not in files:
        files[file_id] = {
            "total": pkt["t"],
            "chunks": {},
            "node": node_id
        }

    files[file_id]["chunks"][pkt["i"]] = base64.b64decode(pkt["d"])

    # send selective ACK
    received = set(files[file_id]["chunks"].keys())
    send_ack(file_id, node_id, pkt["i"], received)

def handle_end(pkt):
    file_id = pkt["f"]
    checksum = pkt["checksum"]

    file = files[file_id]
    total = file["total"]

    if len(file["chunks"]) != total:
        print("Incomplete file")
        return

    data = b''.join(file["chunks"][i] for i in range(total))

    if hashlib.md5(data).hexdigest() != checksum:
        print("Checksum failed")
        return

    decompressed = zlib.decompress(data)

    filename = f"{file_id}.json"
    open(filename, "wb").write(decompressed)

    print(f"Saved {filename}")

    del files[file_id]

# --- MAIN LOOP ---
print("Supervisor listening...")

while True:
    pkt = rfm9x.receive(timeout=0.5)
    if not pkt:
        continue

    try:
        msg = json.loads(pkt.decode())
        print(f"DEBUG: Parsed message type: {msg.get('type')}")
    except Exception as e:
        print(f"DEBUG: Failed to decode packet: {e}")
        continue

    if msg["type"] == "DATA":
        handle_data(msg)

    elif msg["type"] == "END":
        handle_end(msg)