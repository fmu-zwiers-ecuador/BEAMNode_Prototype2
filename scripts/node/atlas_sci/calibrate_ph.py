#!/usr/bin/env python3
"""Author: Jackson Roberts | Atlas EZO pH calibration wizard (mid + optional low and high) over I2C."""

import io
import fcntl
import json
import os
import re
import sys
import time

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"


class AtlasI2CDevice:
    """Minimal raw-I2C interface for Atlas EZO circuits."""

    I2C_SLAVE = 0x0703

    def __init__(self, address: int, bus: int = 1):
        self.address = address
        self.bus = bus
        self.file_read = io.open(f"/dev/i2c-{bus}", "rb", buffering=0)
        self.file_write = io.open(f"/dev/i2c-{bus}", "wb", buffering=0)

        fcntl.ioctl(self.file_read, self.I2C_SLAVE, address)
        fcntl.ioctl(self.file_write, self.I2C_SLAVE, address)

    def write(self, command: str):
        self.file_write.write((command + "\x00").encode("latin-1"))

    def read(self, num_bytes: int = 31):
        raw = self.file_read.read(num_bytes)
        if not raw:
            raise Exception("Empty I2C response")

        status = raw[0]
        chars = [chr(b & ~0x80) for b in raw[1:] if b not in (0x00, 0xFF)]
        text = "".join(chars).strip()
        return status, text, list(raw)

    def query(self, command: str, timeout: float = 1.2, num_bytes: int = 31):
        self.write(command)
        time.sleep(timeout)
        return self.read(num_bytes)

    def close(self):
        try:
            self.file_read.close()
        finally:
            self.file_write.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


STATUS_CODES = {
    0: "failed",
    1: "success",
    2: "syntax error",
    254: "still processing",
    255: "no data",
}


def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Config Load Error: {e}", file=sys.stderr)
        sys.exit(1)


def set_config_flag(key, value):
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    cfg.setdefault("atlas_ph", {})[key] = value

    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def parse_ph_i2c_settings(config):
    ph_config = config.get("atlas_ph", {})
    try:
        addr_hex = ph_config.get("address_hex") or "0x63"
        i2c_bus = ph_config.get("i2c_bus")
        bus_num = int(i2c_bus) if i2c_bus is not None else 1
        addr = int(str(addr_hex), 16)
        return bus_num, addr
    except Exception as e:
        print(
            f"Config Parse Error in atlas_ph: "
            f"address_hex={ph_config.get('address_hex')!r}, "
            f"i2c_bus={ph_config.get('i2c_bus')!r} | {e}",
            file=sys.stderr,
        )
        sys.exit(1)


def countdown(seconds: int):
    remaining = int(seconds)
    while remaining > 0:
        print(f"  waiting... {remaining}s")
        sleep_for = 10 if remaining >= 10 else remaining
        time.sleep(sleep_for)
        remaining -= sleep_for


def parse_float_values(text: str):
    numbers = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)
    if not numbers:
        raise ValueError(f"Could not parse numeric value from: {repr(text)}")
    return [float(value) for value in numbers]


def run_command(dev, command: str, timeout: float = 1.2):
    status, text, raw = dev.query(command, timeout=timeout)
    print(
        f"   {command} -> status={status} "
        f"({STATUS_CODES.get(status, 'unknown')}), text='{text}'"
    )
    if status != 1:
        print(f"   raw: {raw}")
    return status, text, raw


def maybe_step(label: str):
    answer = input(
        f"\n{label}\n"
        "Press Enter to continue, or type 'skip' to skip this step: "
    ).strip().lower()
    return answer != "skip"


def is_ph_circuit(identity: str):
    """True when an ``I`` response such as ``?I,pH,2.16`` names a pH circuit."""
    fields = identity.split(",")
    return len(fields) >= 2 and fields[1].strip().upper() == "PH"


def parse_cal_points(text: str):
    """Return the point count from a ``Cal,?`` response such as ``?Cal,3``."""
    match = re.search(r"cal,\s*(\d+)", text, re.IGNORECASE)
    if not match:
        raise ValueError(f"Could not parse calibration status from: {repr(text)}")
    return int(match.group(1))


def calibrate_point(dev, command: str):
    """Send one calibration command and return True if the circuit accepted it."""
    status, _, _ = run_command(dev, command, timeout=1.5)
    run_command(dev, "Cal,?", timeout=0.9)
    return status == 1


def main():
    config = load_config()
    bus_num, addr = parse_ph_i2c_settings(config)

    print("=" * 70)
    print(" Atlas pH Calibration Wizard")
    print(f" I2C bus: {bus_num} | address: 0x{addr:02X}")
    print(" Needs pH 7.00 buffer; pH 4.00 and pH 10.00 buffers are optional")
    print("=" * 70)

    print("\nBefore continuing:")
    print("- remove storage cap")
    print("- rinse probe with DI/distilled water and blot dry")
    print("- have fresh pH 7.00, 4.00 and 10.00 buffer solutions ready")
    choice = input("\nPress Enter to continue, or type 'q' to quit: ").strip().lower()
    if choice == "q":
        print("Aborted.")
        return

    try:
        with AtlasI2CDevice(addr, bus=bus_num) as dev:
            print("\n1) Identity check")
            status, text, raw = dev.query("I", timeout=0.5)
            print(
                f"   I -> status={status} ({STATUS_CODES.get(status, 'unknown')}), "
                f"text='{text}'"
            )
            if status != 1 or not is_ph_circuit(text):
                print("   ERROR: this circuit did not identify as pH. Nothing was changed.")
                print(f"   raw: {raw}")
                sys.exit(1)

            print("\n2) Existing calibration status")
            run_command(dev, "Cal,?", timeout=0.9)

            print("\n3) Mid-point calibration in pH 7.00 buffer (required)")
            print("   This must come first: it clears any earlier low and high points.")
            input("   Press Enter when the probe is in pH 7.00 buffer: ")
            print("- avoid bubbles and let the reading stabilize")
            countdown(90)
            if not calibrate_point(dev, "Cal,mid,7.00"):
                print("Mid-point calibration failed. atlas_ph was not enabled.", file=sys.stderr)
                sys.exit(1)
            expected_points = 1

            if maybe_step("4) Low-point calibration in pH 4.00 buffer (optional)"):
                print("- rinse the probe, then place it in pH 4.00 buffer")
                print("- avoid bubbles and let the reading stabilize")
                countdown(90)
                if not calibrate_point(dev, "Cal,low,4.00"):
                    print(
                        "Low-point calibration failed. atlas_ph was not enabled.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                expected_points += 1

            if maybe_step("5) High-point calibration in pH 10.00 buffer (optional)"):
                print("- rinse the probe, then place it in pH 10.00 buffer")
                print("- avoid bubbles and let the reading stabilize")
                countdown(90)
                if not calibrate_point(dev, "Cal,high,10.00"):
                    print(
                        "High-point calibration failed. atlas_ph was not enabled.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                expected_points += 1

            print("\n6) Verify calibration")
            status, text, raw = run_command(dev, "Cal,?", timeout=0.9)
            points = parse_cal_points(text) if status == 1 else None
            if points != expected_points:
                print(
                    f"Calibration check failed: expected {expected_points} point(s), "
                    f"circuit reports {points}. atlas_ph was not enabled.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"   Circuit reports {points} calibration point(s).")

            print("\n7) Live pH reading")
            status, text, raw = run_command(dev, "R", timeout=1.5)
            if status == 1 and text:
                try:
                    print(f"   Live pH: {parse_float_values(text)[0]:.3f}")
                except Exception:
                    print("   Note: could not parse numeric pH reading.")
                    print(f"   raw: {raw}")

    except Exception as e:
        print(f"Calibration Error: {e}", file=sys.stderr)
        sys.exit(1)

    set_config_flag("enabled", True)
    set_config_flag("i2c_bus", bus_num)
    set_config_flag("address_hex", f"0x{addr:02X}")

    print("\nconfig.json updated: atlas_ph.enabled = true")
    print("Calibration flow complete.")


if __name__ == "__main__":
    main()
