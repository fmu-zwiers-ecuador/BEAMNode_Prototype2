#!/usr/bin/env python3
# fix_ec_address.py
# Scans I2C bus 2 for a mis-addressed Atlas EZO-EC circuit and resets
# it back to the correct address 0x64 (decimal 100).
# Run with: sudo python3 fix_ec_address.py

import smbus2
import time
import sys

BUS_NUM = 2
TARGET_ADDR = 0x64  # correct EZO-EC address
SCAN_ADDRS = [0x30, 0x3a, 0x50, 0x61, 0x62, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68]

def send_cmd(bus, addr, cmd_str):
    cmd = [ord(c) for c in cmd_str]
    bus.write_i2c_block_data(addr, cmd[0], cmd[1:])
    time.sleep(0.5)

def read_response(bus, addr, length=20):
    try:
        res = bus.read_i2c_block_data(addr, 0, length)
        return res
    except Exception:
        return None

def is_ezo(bus, addr):
    """Returns response string if device at addr responds like an EZO circuit."""
    try:
        send_cmd(bus, addr, "I\r")  # Device Information command
        res = read_response(bus, addr)
        if res and res[0] in (1, 2, 254, 255):
            text = "".join(chr(x) for x in res[1:] if 32 <= x <= 126)
            return text.strip()
    except Exception:
        pass
    return None

def main():
    print("=== Atlas EZO-EC Address Fix Utility ===")
    print(f"Scanning I2C bus {BUS_NUM}...\n")

    try:
        bus = smbus2.SMBus(BUS_NUM)
    except Exception as e:
        print(f"ERROR: Could not open I2C bus {BUS_NUM}: {e}")
        print("Make sure I2C is enabled (sudo raspi-config → Interface Options → I2C)")
        sys.exit(1)

    # Check if already at correct address
    info = is_ezo(bus, TARGET_ADDR)
    if info:
        print(f"EZO-EC already at correct address 0x64. Info: {info}")
        print("No fix needed — run detect.py.")
        bus.close()
        sys.exit(0)

    # Scan for mis-addressed EZO circuit
    found_addr = None
    for addr in SCAN_ADDRS:
        if addr == TARGET_ADDR:
            continue
        try:
            info = is_ezo(bus, addr)
            if info:
                print(f"EZO circuit found at wrong address 0x{addr:02X}. Info: {info}")
                found_addr = addr
                break
            else:
                print(f"  0x{addr:02X}: not an EZO circuit")
        except Exception:
            print(f"  0x{addr:02X}: no response")

    if found_addr is None:
        print("\nNo mis-addressed EZO circuit found on bus 2.")
        print("Check that the EZO-EC is firmly seated in the InterLink slot 3.")
        bus.close()
        sys.exit(1)

    # Send address reset command
    print(f"\nSending I2C,100 to 0x{found_addr:02X} to reset address to 0x64...")
    try:
        send_cmd(bus, found_addr, "I2C,100\r")
        print("Command sent successfully.")
    except Exception as e:
        print(f"ERROR sending command: {e}")
        bus.close()
        sys.exit(1)

    bus.close()
    print("\nDone. Power cycle the Pi now, then run:")
    print("  sudo i2cdetect -y 2      (should show 64)")
    print("  sudo python3 detect.py   (should show atlas_ec found)")

if __name__ == "__main__":
    main()
