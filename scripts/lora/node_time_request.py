## Script to request time from supervisor and set system time accordingly
import os 
import subprocess
import time
import board, busio, digitalio
import json
import adafruit_rfm9x

SPI = busio.SPI(board.SCK, board.MOSI, board.MISO)
CS = digitalio.DigitalInOut(board.CE1)
RESET = digitalio.DigitalInOut(board.D25)

rfm9x = adafruit_rfm9x.RFM9x(SPI, CS, RESET, 915.0)
# send a time request to the supervisor and wait for a response
def request_time(rfm9x, node_id, timeout=300):
    # Create a time request message
    request_msg = {
        "type": "TIME_REQUEST",
        "node_id": node_id
    }
    rfm9x.send(bytes(json.dumps(request_msg), "utf-8"))
    print(f"Sent time request to supervisor: {request_msg}")

    start_time = time.time()
    time.sleep(1)  # wait a bit before listening for response
    while time.time() - start_time < timeout:
        pkt = rfm9x.receive(timeout=1.0)
        if pkt:
            try:
                response_msg = json.loads(pkt.decode())
                if response_msg.get("type") == "TIME_RESPONSE":
                    print(f"Received time response: {response_msg}")
                    return response_msg.get("timestamp")
            except Exception as e:
                print(f"Failed to decode packet: {e}")
                continue
    print("Timeout waiting for time response from supervisor.")
    return None

if __name__ == "__main__":
    # Initialize LoRa radio (assuming rfm9x is already set up)
    rfm9x = adafruit_rfm9x.RFM9x(SPI, CS, RESET, 915.0)

    # Load node ID from config
    project_root = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"
    config_path = os.path.join(project_root, "config.json")
    with open(config_path, "r") as f:
        config = json.load(f)
    node_id = config["global"].get("node_id", "unknown-node")

    # Request time from supervisor
    timestamp = request_time(rfm9x, node_id)
    if timestamp:
        # Set system time using the received timestamp
        try:
            subprocess.run(["sudo", "date", "-s", timestamp], check=True)
            print(f"System time set to: {timestamp}")
        except subprocess.CalledProcessError as e:
            print(f"Failed to set system time: {e}")
