#!/usr/bin/env python3
# fix_ec_address.py
# Scans I2C bus 2 for a mis-addressed Atlas EZO-EC circuit and resets
# it back to the correct address 0x64 (decimal 100).
# Run with: sudo python3 fix_ec_address.py

import smbus2
from smbus2 import i2c_msg
import time
import sys

BUS_NUM = 2
TARGET_ADDR = 0x64  # correct EZO-EC address

# Atlas EZO circuits typically live in 0x61-0x70; full scan catches anything else
FULL_SCAN_RANGE = range(0x03, 0x78)

def send_cmd(bus, addr, cmd_str):
    """Send a raw command to an Atlas EZO device (no register byte)."""
    msg = i2c_msg.write(addr, [ord(c) for c in cmd_str])
    bus.i2c_rdwr(msg)
    time.sleep(0.9)  # EZO needs up to 900ms for some commands

def read_response(bus, addr, length=20):
    """Read a raw response from an Atlas EZO device (no register byte)."""
    try:
        msg = i2c_msg.read(addr, length)
        bus.i2c_rdwr(msg)
        return list(msg)
    except Exception:
        return None

def probe_ack(bus, addr):
    """Return True if a device ACKs at this address (like i2cdetect)."""
    try:
        # A zero-byte write is enough to test for an ACK
        msg = i2c_msg.write(addr, [])
        bus.i2c_rdwr(msg)
        return True
    except Exception:
        return False

def is_ezo(bus, addr):
    """Return info string if device at addr responds like an Atlas EZO circuit."""
    try:
        send_cmd(bus, addr, "I\r")  # Device Information command
        res = read_response(bus, addr)
        if res and res[0] in (1, 2, 254, 255):
            text = "".join(chr(x) for x in res[1:] if 32 <= x <= 126)
            return text.strip()
    except Exception:
        pass
    return None

def full_bus_scan(bus):
    """Return list of addresses that ACK on the bus."""
    found = []
    for addr in FULL_SCAN_RANGE:
        if probe_ack(bus, addr):
            found.append(addr)
    return found

def main():
    print("=== Atlas EZO-EC Address Fix Utility ===")
    print(f"Opening I2C bus {BUS_NUM}...\n")

    try:
        bus = smbus2.SMBus(BUS_NUM)
    except Exception as e:
        print(f"ERROR: Could not open I2C bus {BUS_NUM}: {e}")
        print("Make sure I2C is enabled (sudo raspi-config → Interface Options → I2C)")
        sys.exit(1)

    # ── Step 1: check correct address first ──────────────────────────────────
    info = is_ezo(bus, TARGET_ADDR)
    if info:
        print(f"EZO-EC already at correct address 0x64. Info: {info}")
        print("No fix needed — run detect.py.")
        bus.close()
        sys.exit(0)

    # ── Step 2: full bus scan to find any responding device ───────────────────
    print("Performing full I2C bus scan (0x03–0x77)...")
    present = full_bus_scan(bus)
    if not present:
        print("\nNo I2C devices found on bus 2 at all.")
        print("Hardware checks:")
        print("  • Confirm the EZO-EC is firmly seated in InterLink slot 3")
        print("  • Check SDA/SCL wiring and pull-up resistors")
        print("  • Verify the board is powered")
        print("  • Run: sudo i2cdetect -y 2")
        bus.close()
        sys.exit(1)

    print(f"Devices found on bus: {[f'0x{a:02X}' for a in present]}\n")

    # ── Step 3: test each responding address for EZO identity ─────────────────
    found_addr = None
    for addr in present:
        if addr == TARGET_ADDR:
            continue
        info = is_ezo(bus, addr)
        if info:
            print(f"EZO circuit found at wrong address 0x{addr:02X}. Info: {info}")
            found_addr = addr
            break
        else:
            print(f"  0x{addr:02X}: device present but not an EZO circuit (or slow to respond)")

    if found_addr is None:
        print("\nNo mis-addressed EZO circuit identified.")
        print("The device(s) on the bus did not respond to the EZO 'I' (info) command.")
        print("Possible causes:")
        print("  • Device is still booting — wait 10 s and retry")
        print("  • Wrong EZO sleep mode — power cycle the Pi and retry")
        print("  • Non-EZO device sharing the bus at that address")
        bus.close()
        sys.exit(1)

    # ── Step 4: send address reset ────────────────────────────────────────────
    print(f"\nSending 'I2C,100' to 0x{found_addr:02X} to reset address to 0x64...")
    try:
        send_cmd(bus, found_addr, "I2C,100\r")
        print("Command sent successfully.")
    except Exception as e:
        print(f"ERROR sending command: {e}")
        bus.close()
        sys.exit(1)

    bus.close()
    print("\nDone. Power cycle the Pi now, then verify with:")
    print("  sudo i2cdetect -y 2      (should show 64)")
    print("  sudo python3 detect.py   (should show atlas_ec found)")

if __name__ == "__main__":
    main()
