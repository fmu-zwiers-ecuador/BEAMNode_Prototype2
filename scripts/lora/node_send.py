import time, json, base64, zlib, hashlib, random
import board, busio, digitalio
import adafruit_rfm9x

# --- LoRa setup ---
spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
cs = digitalio.DigitalInOut(board.CE1)
reset = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, 915.0)
rfm9x.tx_power = 23

# --- CONFIG ---
NODE_ID = "node1"
CHUNK_SIZE = 100
WINDOW_SIZE = 4
ACK_TIMEOUT = 3
MAX_RETRIES = 5

# --- Helpers ---
def prepare_file(path):
    data = open(path).read().encode()
    compressed = zlib.compress(data)
    checksum = hashlib.md5(compressed).hexdigest()
    return compressed, checksum

def chunk_data(data):
    return [data[i:i+CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]

def send_packet(obj):
    msg = json.dumps(obj).encode()
    rfm9x.send(msg)

def wait_for_ack(file_id, timeout):
    start = time.time()
    while time.time() - start < timeout:
        pkt = rfm9x.receive(timeout=0.5)
        if pkt:
            msg = json.loads(pkt.decode())
            if msg.get("type") == "ACK" and msg.get("f") == file_id:
                return msg
    return None

# --- MAIN SEND ---
def send_file(path):
    data, checksum = prepare_file(path)
    chunks = chunk_data(data)

    file_id = str(int(time.time()))
    total = len(chunks)

    print(f"Sending {file_id}, {total} chunks")

    # random start delay (collision avoidance)
    time.sleep(random.uniform(0, 10))

    base = 0
    retries = {i: 0 for i in range(total)}

    while base < total:
        # send window
        for i in range(base, min(base + WINDOW_SIZE, total)):
            pkt = {
                "type": "DATA",
                "f": file_id,
                "n": NODE_ID,
                "i": i,
                "t": total,
                "d": base64.b64encode(chunks[i]).decode()
            }
            send_packet(pkt)

        # wait for ACK
        ack = wait_for_ack(file_id, ACK_TIMEOUT)

        if not ack:
            print("Timeout, retransmitting window")
            for i in range(base, min(base + WINDOW_SIZE, total)):
                retries[i] += 1
                if retries[i] > MAX_RETRIES:
                    print("Abort transfer")
                    return
            continue

        # selective ACK handling
        received = set(ack.get("received", []))

        # slide window
        while base in received:
            base += 1

        # retransmit missing
        for i in range(base, min(base + WINDOW_SIZE, total)):
            if i not in received:
                retries[i] += 1

    # send END
    end_pkt = {
        "type": "END",
        "f": file_id,
        "n": NODE_ID,
        "checksum": checksum
    }

    send_packet(end_pkt)
    print("File sent!")

# --- RUN ---
send_file("data.json")