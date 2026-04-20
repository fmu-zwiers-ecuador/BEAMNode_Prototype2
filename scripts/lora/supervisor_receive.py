import time, json, base64, zlib, hashlib
import os, re
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
ACK_JITTER_MAX = 0.18

# Where received files are stored on the supervisor.
OUTPUT_DIR = "/home/pi/data"
SILENCE_SHUTDOWN_SEC = 15 * 60
PATIENCE_LOG_EVERY_SEC = 10


def node_num(node_id: str) -> str:
    """Extract a stable node number string from a node id.

    Examples:
      - "node7" -> "7"
      - "07" -> "07"
      - "NODE_12" -> "12"

    If no digits exist, falls back to a sanitized node_id.
    """
    if not node_id:
        return "unknown"

    m = re.search(r"(\d+)", str(node_id))
    if m:
        return m.group(1)

    # fallback: keep only safe characters
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(node_id)).strip("_")
    return safe or "unknown"

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
    # Add jitter so the supervisor's ACKs don't consistently collide with other nodes.
    time.sleep(ACK_BACKOFF + (ACK_JITTER_MAX * (hash((node_id, file_id, chunk_index)) & 0xFFFF) / 0xFFFF))

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

    key = (node_id, file_id)

    if key not in files:
        files[key] = {
            "total": pkt["t"],
            "chunks": {},
            "node": node_id
        }

    files[key]["chunks"][pkt["i"]] = base64.b64decode(pkt["d"])

    # send selective ACK
    received = set(files[key]["chunks"].keys())
    send_ack(file_id, node_id, pkt["i"], received)

def handle_end(pkt):
    file_id = pkt["f"]
    checksum = pkt["checksum"]
    node_id = pkt.get("n")

    key = (node_id, file_id)

    if key not in files:
        print(f"END for unknown transfer: node={node_id}, file_id={file_id}")
        return

    file = files[key]
    total = file["total"]

    if len(file["chunks"]) != total:
        print("Incomplete file")
        return

    data = b''.join(file["chunks"][i] for i in range(total))

    if hashlib.md5(data).hexdigest() != checksum:
        print("Checksum failed")
        return

    decompressed = zlib.decompress(data)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = f"{node_num(node_id)}_{file_id}.json"
    out_path = os.path.join(OUTPUT_DIR, filename)

    with open(out_path, "wb") as f:
        f.write(decompressed)

    print(f"Saved {out_path}")

    del files[key]

# --- MAIN LOOP ---
print("Supervisor listening...")

last_packet_at = time.monotonic()
last_patience_log_at = 0.0

while True:
    now = time.monotonic()
    silence_sec = now - last_packet_at

    if silence_sec >= SILENCE_SHUTDOWN_SEC:
        print(
            f"No LoRa traffic for {SILENCE_SHUTDOWN_SEC // 60} minutes. "
            "Patience exhausted; shutting down receiver."
        )
        break

    if now - last_patience_log_at >= PATIENCE_LOG_EVERY_SEC:
        remaining_sec = max(0.0, SILENCE_SHUTDOWN_SEC - silence_sec)
        patience_pct = (remaining_sec / SILENCE_SHUTDOWN_SEC) * 100.0
        print(
            f"Listening patience: {patience_pct:.1f}% "
            f"({int(remaining_sec)}s remaining before shutdown)"
        )
        last_patience_log_at = now

    pkt = rfm9x.receive(timeout=0.5)
    if not pkt:
        continue

    last_packet_at = time.monotonic()

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