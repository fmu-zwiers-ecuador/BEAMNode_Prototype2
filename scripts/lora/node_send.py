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
ACK_TIMEOUT = 20 
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
    print(f"DEBUG: Sending packet of {len(msg)} bytes")
    rfm9x.send(msg)

def wait_for_ack(file_id, timeout):
    start = time.time()
    while time.time() - start < timeout:
        pkt = rfm9x.receive(timeout=0.5)
        if pkt:
            try:
                msg = json.loads(pkt.decode())
                print(f"DEBUG: Received message: {msg}")
                if msg.get("type") == "ACK" and msg.get("f") == file_id:
                    print(f"DEBUG: ACK matched for file_id {file_id}")
                    return msg
                else:
                    print(f"DEBUG: Message doesn't match - type: {msg.get('type')}, f: {msg.get('f')}")
            except Exception as e:
                print(f"DEBUG: Failed to decode packet: {e}")
                print(f"DEBUG: Raw packet: {pkt}")
                pass
    print(f"DEBUG: ACK timeout for file_id {file_id}")
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

    retries = {i: 0 for i in range(total)}

    # Send each chunk with retry logic
    for i in range(total):
        acked = False

        while not acked and retries[i] < MAX_RETRIES:
            # Send packet
            pkt = {
                "type": "DATA",
                "f": file_id,
                "n": NODE_ID,
                "i": i,
                "t": total,
                "d": base64.b64encode(chunks[i]).decode()
            }
            print(f"Sending chunk {i}/{total}")
            send_packet(pkt)

            # Allow radio to finish TX and switch to RX mode
            time.sleep(0.5)

            # Wait for ACK
            ack = wait_for_ack(file_id, ACK_TIMEOUT)

            if ack:
                print(f"ACK received for chunk {i}")
                acked = True
            else:
                retries[i] += 1
                print(f"Timeout for chunk {i}, retry {retries[i]}/{MAX_RETRIES}")

        if not acked:
            print(f"Failed to deliver chunk {i}")
            return

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
send_file("ec_data.json")