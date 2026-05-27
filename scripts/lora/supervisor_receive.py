import time, json, base64, zlib, hashlib
import os, re
import sys
import atexit
from datetime import datetime
import board, busio, digitalio
import adafruit_rfm9x

# --- Logging (redirect all output) ---
LOG_PATH = "/home/pi/logs/lora_send.log"

# --- LoRa setup ---
spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
cs = digitalio.DigitalInOut(board.CE1)
reset = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, 915.0)
rfm9x.tx_power = 23
try:
    rfm9x.listen()
except AttributeError:
    pass

# --- STORAGE ---
files = {}
ACK_BACKOFF = 0.12
ACK_REPEATS = 2
ACK_REPEAT_GAP = 0.08
ACK_JITTER_MAX = 0.18

# Where received files are stored on the supervisor.
OUTPUT_DIR = "/home/pi/data"

# Per-node session output directories
NODE_OUTPUT_DIRS = {}

def log(msg):
    """Internal logging."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [supervisor_receive] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except:
        pass


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

# send time string to nodes for system clock
def send_time():
    now = datetime.now()
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    pkt = {
        "type": "TIME_RESPONSE",
        "timestamp": time_str
    }
    packet = json.dumps(pkt)
    try:
        rfm9x.send(packet.encode(), keep_listening=True)
    except TypeError:
        # Older library versions may not support keep_listening kwarg
        rfm9x.send(packet.encode())
    log(f"Sent TIME packet: {time_str}")


def get_node_output_dir(node_id: str, run_id: str | None) -> str:
    node_key = node_num(node_id)
    run_key = run_id or datetime.now().strftime("%Y%m%dT%H%M%SZ")
    dir_key = (node_key, run_key)
    if dir_key not in NODE_OUTPUT_DIRS:
        dir_name = f"node{node_key}_loradata_{run_key}"
        out_dir = os.path.join(OUTPUT_DIR, dir_name)
        os.makedirs(out_dir, exist_ok=True)
        NODE_OUTPUT_DIRS[dir_key] = out_dir
    return NODE_OUTPUT_DIRS[dir_key]

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

    # Ensure we return to RX mode after sending ACKs.
    try:
        rfm9x.listen()
    except AttributeError:
        pass

    log(f"ACK sent for {file_id}, chunk {chunk_index}, received chunks: {received}")

def handle_data(pkt):
    log(f"DEBUG: Received DATA packet - file_id: {pkt.get('f')}, chunk: {pkt.get('i')}")
    file_id = pkt["f"]
    node_id = pkt["n"]
    run_id = pkt.get("r")

    key = (node_id, file_id)

    if key not in files:
        files[key] = {
            "total": pkt["t"],
            "chunks": {},
            "node": node_id,
            "run_id": run_id
        }

    files[key]["chunks"][pkt["i"]] = base64.b64decode(pkt["d"])

    # send selective ACK
    received = set(files[key]["chunks"].keys())
    send_ack(file_id, node_id, pkt["i"], received)

def handle_end(pkt):
    file_id = pkt["f"]
    checksum = pkt["checksum"]
    node_id = pkt.get("n")
    run_id = pkt.get("r")

    key = (node_id, file_id)

    if key not in files:
        log(f"END for unknown transfer: node={node_id}, file_id={file_id}")
        return

    file = files[key]
    total = file["total"]

    if len(file["chunks"]) != total:
        log("Incomplete file")
        return

    data = b''.join(file["chunks"][i] for i in range(total))

    if hashlib.md5(data).hexdigest() != checksum:
        log("Checksum failed")
        return

    decompressed = zlib.decompress(data)

    out_dir = get_node_output_dir(node_id, file.get("run_id") or run_id)
    filename = f"{node_num(node_id)}_{file_id}.json"
    out_path = os.path.join(out_dir, filename)

    with open(out_path, "wb") as f:
        f.write(decompressed)

    log(f"Saved {out_path}")

    del files[key]

# --- MAIN LOOP ---
log("Supervisor listening...")

while True:
    try:
        pkt = rfm9x.receive(timeout=0.5, keep_listening=True)
    except TypeError:
        pkt = rfm9x.receive(timeout=0.5)
    if not pkt:
        continue

    try:
        msg = json.loads(pkt.decode())
        log(f"DEBUG: Parsed message type: {msg.get('type')}")
    except Exception as e:
        log(f"DEBUG: Failed to decode packet: {e}")
        continue

    if msg["type"] == "DATA":
        handle_data(msg)
    
    if msg["type"] == "TIME_REQUEST":
        node_id = msg.get("node_id", "unknown-node")
        log(f"Received TIME_REQUEST from {node_id}")
        send_time()

    elif msg["type"] == "END":
        handle_end(msg)