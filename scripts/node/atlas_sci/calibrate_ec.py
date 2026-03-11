#!/usr/bin/env python3
"""
Author: Jackson Roberts
Atlas EZO EC Calibration Script
================================
Walks through dry → single-point (or two-point) calibration
for the Atlas Scientific EZO EC circuit over I2C.

Usage:
    sudo python3 calibrate_ec.py

Config is read from config.json (address_hex, i2c_bus).
All calibration points are stored on the EZO circuit itself — no
re-run is needed after a reboot.
"""

import json
import os
import sys
import time
import smbus2
from smbus2 import i2c_msg

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"

# ── helpers ──────────────────────────────────────────────────────────────────

def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"ERROR: Could not load config.json: {e}")
        sys.exit(1)

def ezo_cmd(bus, addr, cmd: str, delay: float = 1.3, read_len: int = 20) -> str:
    """Send a command to the EZO circuit and return the decoded response string."""
    bus.i2c_rdwr(i2c_msg.write(addr, list(cmd.encode()) + [0x0D]))
    time.sleep(delay)
    r = i2c_msg.read(addr, read_len)
    bus.i2c_rdwr(r)
    res = list(r)
    if not res:
        return ""
    status = res[0]
    text = "".join(chr(x) for x in res[1:] if 32 <= x <= 126).strip()
    if status == 1:
        return text if text else "OK"
    elif status == 254:
        return "STILL_PROCESSING"
    elif status == 255:
        return "NO_DATA"
    else:
        return f"ERROR_{status}"

def set_config_flag(key, value):
    """Atomically update a single field under atlas_ec in config.json."""
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg.setdefault("atlas_ec", {})[key] = value
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)

def prompt(msg: str) -> str:
    return input(f"\n{msg}\nPress Enter when ready (or type 'skip' to skip): ").strip().lower()

def separator():
    print("\n" + "─" * 60)

# ── calibration steps ────────────────────────────────────────────────────────

def check_existing_cal(bus, addr):
    separator()
    print("Checking existing calibration status...")
    resp = ezo_cmd(bus, addr, "Cal,?", delay=0.6)
    print(f"  EZO reports: {resp}")
    # ?Cal,0 = none, ?Cal,1 = one-point, ?Cal,2 = two-point, ?Cal,d = dry only
    if resp.endswith(",0"):
        print("  No calibration stored.")
    elif resp.endswith(",d"):
        print("  Dry calibration only — still needs solution calibration.")
    elif resp.endswith(",1"):
        print("  One-point calibration already stored.")
    elif resp.endswith(",2"):
        print("  Two-point calibration already stored.")
    return resp

def step_clear(bus, addr):
    separator()
    print("STEP 0  — Clear existing calibration")
    ans = input("  Clear all stored calibration data? (y/N): ").strip().lower()
    if ans == "y":
        resp = ezo_cmd(bus, addr, "Cal,clear", delay=0.6)
        print(f"  Response: {resp}")
        print("  Calibration cleared.")
    else:
        print("  Skipped.")

def step_dry(bus, addr):
    separator()
    print("STEP 1  — Dry calibration")
    print("  • Remove probe from water")
    print("  • Rinse with DI water and pat completely dry")
    p = prompt("Confirm probe is dry and in open air")
    if p == "skip":
        print("  Skipped.")
        return
    resp = ezo_cmd(bus, addr, "Cal,dry", delay=1.3)
    if resp and "ERROR" not in resp and resp not in ("STILL_PROCESSING", "NO_DATA"):
        confirm = ezo_cmd(bus, addr, "Cal,?", delay=0.6)
        print(f"  Dry calibration successful. Cal status: {confirm}")
    else:
        print(f"  WARNING: Unexpected response: {resp}")

def step_single_point(bus, addr):
    separator()
    print("STEP 2  — Single-point calibration (solution)")
    print("  • Rinse probe with DI water, shake off excess")
    print("  • Submerge fully in your calibration solution")
    print("  • Common values:  84 µS/cm  |  1413 µS/cm  |  12880 µS/cm")
    print("  • Check the label on your solution bottle for the exact value")

    sol_val = input("\n  Enter solution value in µS/cm (e.g. 1413): ").strip()
    if not sol_val.isdigit():
        print("  Invalid value — skipping single-point calibration.")
        return

    print(f"\n  Waiting 60 s for probe to stabilise in {sol_val} µS/cm solution...")
    for i in range(60, 0, -10):
        print(f"    {i}s remaining...")
        time.sleep(10)

    resp = ezo_cmd(bus, addr, f"Cal,one,{sol_val}", delay=1.3)
    if resp and "ERROR" not in resp and resp not in ("STILL_PROCESSING", "NO_DATA"):
        confirm = ezo_cmd(bus, addr, "Cal,?", delay=0.6)
        print(f"  Single-point calibration successful. Cal status: {confirm}")
    else:
        print(f"  WARNING: Unexpected response: {resp}")

def step_two_point_low(bus, addr):
    separator()
    print("STEP 2a — Two-point calibration: LOW solution")
    print("  • Rinse probe with DI water, shake off excess")
    print("  • Submerge fully in your LOW calibration solution")

    sol_val = input("\n  Enter LOW solution value in µS/cm (e.g. 84): ").strip()
    if not sol_val.isdigit():
        print("  Invalid value — skipping.")
        return

    print(f"\n  Waiting 60 s for probe to stabilise...")
    for i in range(60, 0, -10):
        print(f"    {i}s remaining...")
        time.sleep(10)

    resp = ezo_cmd(bus, addr, f"Cal,low,{sol_val}", delay=1.3)
    if resp and "ERROR" not in resp and resp not in ("STILL_PROCESSING", "NO_DATA"):
        confirm = ezo_cmd(bus, addr, "Cal,?", delay=0.6)
        print(f"  Low-point calibration successful. Cal status: {confirm}")
    else:
        print(f"  WARNING: Unexpected response: {resp}")

def step_two_point_high(bus, addr):
    separator()
    print("STEP 2b — Two-point calibration: HIGH solution")
    print("  • Rinse probe with DI water, shake off excess")
    print("  • Submerge fully in your HIGH calibration solution")

    sol_val = input("\n  Enter HIGH solution value in µS/cm (e.g. 1413): ").strip()
    if not sol_val.isdigit():
        print("  Invalid value — skipping.")
        return

    print(f"\n  Waiting 60 s for probe to stabilise...")
    for i in range(60, 0, -10):
        print(f"    {i}s remaining...")
        time.sleep(10)

    resp = ezo_cmd(bus, addr, f"Cal,high,{sol_val}", delay=1.3)
    if resp and "ERROR" not in resp and resp not in ("STILL_PROCESSING", "NO_DATA"):
        confirm = ezo_cmd(bus, addr, "Cal,?", delay=0.6)
        print(f"  High-point calibration successful. Cal status: {confirm}")
    else:
        print(f"  WARNING: Unexpected response: {resp}")

def step_verify(bus, addr):
    separator()
    print("STEP 3  — Verify calibration & take a live reading")
    cal_resp = ezo_cmd(bus, addr, "Cal,?", delay=0.6)
    print(f"  Calibration stored: {cal_resp}")

    print("\n  Taking live reading (probe should be submerged in water)...")
    live = ezo_cmd(bus, addr, "R", delay=1.0, read_len=31)
    print(f"  Live conductivity: {live} µS/cm")

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    config = load_config()
    ec_config = config.get("atlas_ec", {})

    addr = int(str(ec_config.get("address_hex", "0x64")), 16)
    bus_num = ec_config.get("i2c_bus") or 1

    print("=" * 60)
    print("  Atlas EZO EC Calibration Wizard")
    print(f"  I2C bus: {bus_num}  |  Address: 0x{addr:02X}")
    print("=" * 60)

    try:
        bus = smbus2.SMBus(bus_num)
    except Exception as e:
        print(f"ERROR: Cannot open I2C bus {bus_num}: {e}")
        sys.exit(1)

    try:
        existing = check_existing_cal(bus, addr)

        # Offer to clear if already calibrated
        if not existing.endswith(",0"):
            step_clear(bus, addr)

        # Calibration type
        separator()
        print("Calibration type:")
        print("  1 = Single-point  (one solution bottle)")
        print("  2 = Two-point     (low + high solution bottles — recommended)")
        cal_type = input("\nSelect [1/2] (default 2): ").strip() or "2"

        step_dry(bus, addr)

        if cal_type == "2":
            step_two_point_low(bus, addr)
            step_two_point_high(bus, addr)
        else:
            step_single_point(bus, addr)

        step_verify(bus, addr)

        # Enable the sensor in config.json so log_atlas_ec.py will run
        set_config_flag("enabled", True)
        set_config_flag("i2c_bus", bus_num)
        set_config_flag("address_hex", f"0x{addr:02X}")
        print("\n  config.json updated: atlas_ec.enabled = true")

    finally:
        bus.close()

    separator()
    print("Calibration complete. You can now run log_atlas_ec.py normally.")
    print()

if __name__ == "__main__":
    main()
