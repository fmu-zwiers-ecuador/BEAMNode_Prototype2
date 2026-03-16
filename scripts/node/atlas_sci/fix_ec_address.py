#!/usr/bin/env python3
# fix_ec_address.py
# Scans I2C bus 2 for a mis-addressed Atlas EZO-EC circuit and resets
# it back to the correct address 0x64 (decimal 100).
# Run with: sudo python3 fix_ec_address.py

import smbus2
from smbus2 import i2c_msg
import time
import sys

BUSES_TO_TRY = [1, 2]       # scan bus 1 then bus 2
TARGET_ADDR = 0x64           # correct EZO-EC address

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
    print("=== Atlas EZO-EC Address Fix Utility ===\n")

    found_bus = None
    found_addr = None

    for bus_num in BUSES_TO_TRY:
        print(f"── Trying I2C bus {bus_num} ──────────────────────────────────")
        try:
            bus = smbus2.SMBus(bus_num)
        except Exception as e:
            print(f"  Could not open bus {bus_num}: {e} — skipping\n")
            continue

        # ── Step 1: check correct address first ──────────────────────────────
        info = is_ezo(bus, TARGET_ADDR)
        if info:
            print(f"  EZO-EC already at correct address 0x64 on bus {bus_num}. Info: {info}")
            print("  No fix needed — run detect.py.")
            bus.close()
            sys.exit(0)

        # ── Step 2: full bus scan ─────────────────────────────────────────────
        print(f"  Performing full scan (0x03–0x77)...")
        present = full_bus_scan(bus)
        if not present:
            print(f"  No I2C devices found on bus {bus_num}.\n")
            bus.close()
            continue

        print(f"  Devices found: {[f'0x{a:02X}' for a in present]}")

        # ── Step 3: test each address for EZO identity ───────────────────────
        for addr in present:
            if addr == TARGET_ADDR:
                continue
            info = is_ezo(bus, addr)
            if info:
                print(f"  EZO circuit found at wrong address 0x{addr:02X}. Info: {info}")
                found_bus = bus_num
                found_addr = addr
                break
            else:
                print(f"    0x{addr:02X}: device present but not an EZO circuit (or slow to respond)")

        if found_addr is not None:
            break  # stop scanning buses once found

        print(f"  No EZO circuit identified on bus {bus_num}.\n")
        bus.close()

    # ── Outcome ───────────────────────────────────────────────────────────────
    if found_addr is None:
        print("\nEZO-EC not found on any scanned bus.")
        print("Possible causes:")
        print("  • Device is still booting — wait 10 s and retry")
        print("  • EZO is in sleep mode — power cycle the Pi and retry")
        print("  • Hardware: reseat EZO-EC in InterLink slot 3, check wiring")
        print(f"  • Try: sudo i2cdetect -y 1   and   sudo i2cdetect -y 2")
        sys.exit(1)

    # ── Step 4: send address reset ────────────────────────────────────────────
    print(f"\nSending 'I2C,100' to 0x{found_addr:02X} on bus {found_bus} to reset address to 0x64...")
    try:
        bus = smbus2.SMBus(found_bus)
        send_cmd(bus, found_addr, "I2C,100\r")
        bus.close()
        print("Command sent successfully.")
    except Exception as e:
        print(f"ERROR sending command: {e}")
        sys.exit(1)

    print("\nDone. Power cycle the Pi now, then verify with:")
    print(f"  sudo i2cdetect -y {found_bus}    (should show 64)")
    print("  sudo python3 detect.py          (should show atlas_ec found)")

if __name__ == "__main__":
    main()
