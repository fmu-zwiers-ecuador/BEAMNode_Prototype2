import datetime
import time, json, base64, zlib, hashlib, random
import sys
import argparse
import shutil
import os
import re
from pathlib import Path
import socket
import atexit
import board, busio, digitalio
import adafruit_rfm9x
from zoneinfo import ZoneInfo

EASTERN_TZ = ZoneInfo("America/New_York")

# --- Logging (redirect all output) ---
LOG_PATH = "/home/pi/logs/lora_send.log"

# --- LoRa setup ---
spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
cs = digitalio.DigitalInOut(board.CE1)
reset = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, 915.0)
rfm9x.tx_power = 23

# --- CONFIG ---
NODE_ID = socket.gethostname()
CHUNK_SIZE = 100
MAX_PACKET_BYTES = 200
ACK_TIMEOUT = 20 
MAX_RETRIES = 5
TX_RX_TURNAROUND = 0.1
DEFAULT_DATA_DIR = "/home/pi/data"
DEFAULT_STORAGE_DIR = "/home/pi/storage"
RUN_ID = datetime.datetime.now(EASTERN_TZ).strftime("%Y%m%dT%H%M%S%Z")

# --- Multi-node airtime coordination (best-effort) ---
# LoRa is a shared medium; with multiple nodes, packets/ACKs will occasionally collide.
# These settings add jitter and a simple “listen-before-talk” sampling to reduce collisions.
CHANNEL_CLEAR_MAX_WAIT = 6.0
CHANNEL_BUSY_SAMPLE_TIMEOUT = 0.12
CHANNEL_CLEAR_SAMPLES = 2
RETRY_BACKOFF_BASE = 0.25
RETRY_BACKOFF_MAX = 4.0
INTER_CHUNK_JITTER_MAX = 0.35

# --- Helpers ---

def log(msg):
    """Internal logging."""
    ts = datetime.datetime.now(EASTERN_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] [lora_send] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except:
        pass

def prepare_file(path):
    with open(path, "rb") as f:
        data = f.read()
    compressed = zlib.compress(data)
    checksum = hashlib.md5(compressed).hexdigest()
    return compressed, checksum

def make_file_id(path):
    stem = Path(path).stem
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    safe_stem = safe_stem[:48] if safe_stem else "data"
    # Use ns timestamp to avoid collisions when sending many files quickly.
    # Include NODE_ID so file_id is globally unique across nodes.
    return f"{NODE_ID}_{time.time_ns()}_{safe_stem}"

def chunk_data(data):
    return [data[i:i+CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]

def build_chunks_for_limit(data, file_id, node_id, run_id, limit_bytes=MAX_PACKET_BYTES):
    """Split data into chunks so every DATA packet stays <= limit_bytes."""
    data_len = len(data)
    if data_len == 0:
        return [b""]

    def packet_size_for(chunk_len: int, idx: int, total: int) -> int:
        dummy = b"x" * chunk_len
        pkt = {
            "type": "DATA",
            "f": file_id,
            "n": node_id,
            "r": run_id,
            "i": idx,
            "t": total,
            "d": base64.b64encode(dummy).decode(),
        }
        return len(json.dumps(pkt).encode())

    def is_ok(chunk_len: int) -> bool:
        total = (data_len + chunk_len - 1) // chunk_len
        return packet_size_for(chunk_len, total - 1, total) <= limit_bytes

    low, high = 1, 512
    best = 0
    while low <= high:
        mid = (low + high) // 2
        if is_ok(mid):
            best = mid
            low = mid + 1
        else:
            high = mid - 1

    if best <= 0:
        raise ValueError("MAX_PACKET_BYTES too small for DATA packet headers")

    return [data[i:i+best] for i in range(0, data_len, best)]

def send_packet(obj):
    msg = json.dumps(obj).encode()
    if len(msg) > MAX_PACKET_BYTES:
        raise ValueError(f"Packet size {len(msg)} exceeds limit {MAX_PACKET_BYTES}")
    log(f"DEBUG: Sending packet of {len(msg)} bytes")
    try:
        rfm9x.send(msg, keep_listening=True)
    except TypeError:
        # Older library versions may not support keep_listening kwarg
        rfm9x.send(msg)


def wait_for_channel_clear(max_wait_s: float = CHANNEL_CLEAR_MAX_WAIT) -> bool:
    """Best-effort carrier sense.

    LoRa isn't CSMA like Wi-Fi, but sampling for any packet activity before TX
    reduces the chance we transmit right over someone else's packet.
    """
    start = time.time()
    consecutive_clear = 0

    while time.time() - start < max_wait_s:
        pkt = rfm9x.receive(timeout=CHANNEL_BUSY_SAMPLE_TIMEOUT)
        if pkt:
            consecutive_clear = 0
            # Random short backoff before re-checking.
            time.sleep(random.uniform(0.05, 0.25))
            continue

        consecutive_clear += 1
        if consecutive_clear >= CHANNEL_CLEAR_SAMPLES:
            return True

    return False

def wait_for_ack(file_id, chunk_index, timeout):
    start = time.time()
    while time.time() - start < timeout:
        pkt = rfm9x.receive(timeout=0.25)
        if pkt:
            try:
                msg = json.loads(pkt.decode())
                log(f"DEBUG: Received message: {msg}")
                if (
                    msg.get("type") == "ACK"
                    and msg.get("f") == file_id
                    and msg.get("n") == NODE_ID
                    and msg.get("i") == chunk_index
                ):
                    log(f"DEBUG: ACK matched for file_id {file_id}, chunk {chunk_index}")
                    return msg
                else:
                    log(
                        "DEBUG: Message doesn't match - "
                        f"type: {msg.get('type')}, "
                        f"f: {msg.get('f')}, "
                        f"n: {msg.get('n')} (expected {NODE_ID}), "
                        f"i: {msg.get('i')} (expected {chunk_index})"
                    )
            except Exception as e:
                log(f"DEBUG: Failed to decode packet: {e}")
                log(f"DEBUG: Raw packet: {pkt}")
                pass
    log(f"DEBUG: ACK timeout for file_id {file_id}, chunk {chunk_index}")
    return None

# --- MAIN SEND ---
def send_file(path):
    file_id = make_file_id(path)

    data, checksum = prepare_file(path)
    chunks = build_chunks_for_limit(data, file_id, NODE_ID, RUN_ID, MAX_PACKET_BYTES)
    total = len(chunks)

    log(f"Sending {path} as {file_id}, {total} chunks")

    # Random start delay (collision avoidance across multiple nodes).
    time.sleep(random.uniform(0, 10))

    retries = {i: 0 for i in range(total)}

    # Send each chunk with retry logic
    for i in range(total):
        acked = False

        while not acked and retries[i] < MAX_RETRIES:
            # Best-effort: don't transmit right into detected activity.
            wait_for_channel_clear()

            # Send packet
            pkt = {
                "type": "DATA",
                "f": file_id,
                "n": NODE_ID,
                "r": RUN_ID,
                "i": i,
                "t": total,
                "d": base64.b64encode(chunks[i]).decode()
            }
            log(f"Sending chunk {i}/{total}")
            send_packet(pkt)

            # Allow radio to finish TX and switch to RX mode
            time.sleep(TX_RX_TURNAROUND)

            # Wait for ACK
            ack = wait_for_ack(file_id, i, ACK_TIMEOUT)

            if ack:
                log(f"ACK received for chunk {i}")
                acked = True
                # Small jitter so multiple nodes don't stay phase-locked.
                time.sleep(random.uniform(0.0, INTER_CHUNK_JITTER_MAX))
            else:
                retries[i] += 1
                log(f"Timeout for chunk {i}, retry {retries[i]}/{MAX_RETRIES}")
                backoff = min(RETRY_BACKOFF_MAX, RETRY_BACKOFF_BASE * (2 ** (retries[i] - 1)))
                time.sleep(random.uniform(0.5 * backoff, 1.5 * backoff))

        if not acked:
            log(f"Failed to deliver chunk {i}")
            return False

    # send END
    end_pkt = {
        "type": "END",
        "f": file_id,
        "n": NODE_ID,
        "r": RUN_ID,
        "checksum": checksum
    }

    wait_for_channel_clear()
    send_packet(end_pkt)
    log("File sent!")
    return True

def iter_json_files(root_dir, recursive=True):
    root = Path(root_dir)
    if not root.exists() or not root.is_dir():
        return []

    if recursive:
        candidates = [p for p in root.rglob("*.json") if p.is_file()]
    else:
        candidates = [p for p in root.glob("*.json") if p.is_file()]

    def sort_key(p: Path):
        try:
            st = p.stat()
            return (st.st_mtime, str(p))
        except OSError:
            return (float("inf"), str(p))

    candidates.sort(key=sort_key)
    return [str(p) for p in candidates]

def main():
    parser = argparse.ArgumentParser(description="Send all JSON files over LoRa")
    parser.add_argument(
        "--dir",
        default=DEFAULT_DATA_DIR,
        help=f"Directory to scan for JSON files (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--non-recursive",
        action="store_true",
        help="Only send JSON files directly in --dir (no subdirectories)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of files to send (0 = no limit)",
    )
    parser.add_argument(
        "--storage",
        default=DEFAULT_STORAGE_DIR,
        help=f"Directory to move failed files (default: {DEFAULT_STORAGE_DIR})",
    )

    args = parser.parse_args()

    paths = iter_json_files(args.dir, recursive=not args.non_recursive)
    if args.limit and args.limit > 0:
        paths = paths[: args.limit]

    if not paths:
        log(f"No JSON files found in {args.dir}")
        return 0

    log(f"Found {len(paths)} JSON file(s) under {args.dir}")

    failures = 0
    os.makedirs(args.storage, exist_ok=True)
    for idx, path in enumerate(paths, start=1):
        log(f"\n[{idx}/{len(paths)}] Sending {path}")
        try:
            ok = send_file(path)
            if not ok:
                raise RuntimeError("Send failed")
            try:
                os.remove(path)
                log(f"Deleted sent file {path}")
            except Exception as del_err:
                log(f"ERROR: Failed to delete sent file {path}: {del_err}")
        except Exception as e:
            failures += 1
            log(f"ERROR: Failed sending {path}: {e}")
            try:
                dest_path = os.path.join(args.storage, os.path.basename(path))
                shutil.move(path, dest_path)
                log(f"Moved failed file to {dest_path}")
            except Exception as move_err:
                log(f"ERROR: Failed to move {path} to storage: {move_err}")

    if failures:
        log(f"\nDone with {failures} failure(s) out of {len(paths)} file(s)")
        return 2

    log(f"\nDone: sent {len(paths)} file(s) successfully")
    return 0

# --- RUN ---
if __name__ == "__main__":
    raise SystemExit(main())
