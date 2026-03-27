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

def send_ack(file_id, node_id, received):
    pkt = {
        "type": "ACK",
        "f": file_id,
        "n": node_id,
        "received": list(received)
    }
    rfm9x.send(json.dumps(pkt).encode())
    print(f"ACK sent for {file_id}, received chunks: {received}")

def handle_data(pkt):
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
    send_ack(file_id, node_id, received)

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
    pkt = rfm9x.receive(timeout=1.0)
    if not pkt:
        continue

    try:
        msg = json.loads(pkt.decode())
    except:
        
        continue

    if msg["type"] == "DATA":
        handle_data(msg)

    elif msg["type"] == "END":
        handle_end(msg)