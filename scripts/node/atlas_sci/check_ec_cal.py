#!/usr/bin/env python3
"""
Quick diagnostic: checks Atlas EZO EC calibration status and takes a live reading.
Run on the Pi:  sudo python3 check_ec_cal.py
"""

import json
import sys
import time
import smbus2
from smbus2 import i2c_msg

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

def ezo_cmd(bus, addr, cmd, delay=1.0, read_len=31):
    bus.i2c_rdwr(i2c_msg.write(addr, list(cmd.encode()) + [0x0D]))
    time.sleep(delay)
    r = i2c_msg.read(addr, read_len)
    bus.i2c_rdwr(r)
    res = list(r)
    status = res[0] if res else -1
    text = "".join(chr(x) for x in res[1:] if 32 <= x <= 126).strip()
    return status, text, res

def main():
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"Config error: {e}")
        sys.exit(1)

    ec = cfg.get("atlas_ec", {})
    addr    = int(str(ec.get("address_hex", "0x64")), 16)
    bus_num = ec.get("i2c_bus") or 1

    print(f"I2C bus: {bus_num}  |  Address: 0x{addr:02X}")
    print("─" * 50)

    bus = smbus2.SMBus(bus_num)

    # 1. Calibration status
    status, text, raw = ezo_cmd(bus, addr, "Cal,?", delay=0.8)
    print(f"Cal,?  →  status={status}  text='{text}'")
    print(f"         raw bytes: {raw}")

    if   text.endswith(",0"): print("  → No calibration stored")
    elif text.endswith(",d"): print("  → Dry only — needs solution calibration")
    elif text.endswith(",1"): print("  → One-point calibration stored")
    elif text.endswith(",2"): print("  → Two-point calibration stored ✓")
    else:                     print("  → Unknown / empty — calibration may not have saved")

    print()

    # 2. Live reading
    status, text, raw = ezo_cmd(bus, addr, "R", delay=1.5)
    print(f"R      →  status={status}  text='{text}'")
    print(f"         raw bytes: {raw}")

    if status == 1 and text:
        print(f"\n  Live conductivity: {text} µS/cm")
    elif status == 1 and not text:
        print("\n  Status OK but no value returned — calibration required")
    elif status == 254:
        print("\n  Still processing — increase delay")
    elif status == 255:
        print("\n  No data — re-issue R command")
    else:
        print(f"\n  Error status {status}")

    bus.close()

if __name__ == "__main__":
    main()
