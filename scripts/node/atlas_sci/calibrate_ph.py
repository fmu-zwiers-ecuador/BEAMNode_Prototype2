#!/usr/bin/env python3
"""Author: Jackson Roberts | Atlas EZO DO calibration wizard (zero + optional atmospheric) over I2C."""

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

    cfg.setdefault("atlas_do", {})[key] = value

    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def parse_do_i2c_settings(config):
    do_config = config.get("atlas_do", {})
    try:
        addr_hex = do_config.get("address_hex") or "0x61"
        i2c_bus = do_config.get("i2c_bus")
        bus_num = int(i2c_bus) if i2c_bus is not None else 1
        addr = int(str(addr_hex), 16)
        return bus_num, addr
    except Exception as e:
        print(
            f"Config Parse Error in atlas_do: "
            f"address_hex={do_config.get('address_hex')!r}, "
            f"i2c_bus={do_config.get('i2c_bus')!r} | {e}",
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


def main():
    config = load_config()
    bus_num, addr = parse_do_i2c_settings(config)

    print("=" * 70)
    print(" Atlas Dissolved Oxygen Calibration Wizard")
    print(f" I2C bus: {bus_num} | address: 0x{addr:02X}")
    print(" Recommended with your supplies: zero-point calibration using zero DO solution")
    print("=" * 70)

    print("\nBefore continuing:")
    print("- remove storage cap")
    print("- rinse probe with DI/distilled water and blot dry")
    print("- use fresh zero dissolved oxygen calibration solution")
    choice = input("\nPress Enter to continue, or type 'q' to quit: ").strip().lower()
    if choice == "q":
        print("Aborted.")
        return

    try:
        with AtlasI2CDevice(addr, bus=bus_num) as dev:
            print("\n1) Identity check")
            status, text, raw = dev.query("I", timeout=0.5)
            text_norm = "".join(ch for ch in text.upper() if ch.isalnum())
            print(
                f"   I -> status={status} ({STATUS_CODES.get(status, 'unknown')}), "
                f"text='{text}'"
            )
            if status != 1 or "DO" not in text_norm:
                print("   WARNING: DO identity check was not clean.")
                print(f"   raw: {raw}")
                keep_going = input("   Continue anyway? [y/N]: ").strip().lower()
                if keep_going not in ("y", "yes"):
                    print("Stopped by user.")
                    return

            print("\n2) Existing calibration status")
            run_command(dev, "Cal,?", timeout=0.9)

            clear_first = input("\n3) Clear previous calibration first? [Y/n]: ").strip().lower()
            if clear_first in ("", "y", "yes"):
                run_command(dev, "Cal,clear", timeout=1.0)

            print("\n4) Calibration mode")
            print("   1 = Zero-point only (matches your available solution)")
            print("   2 = Zero-point + atmospheric point")
            cal_mode = input("Select [1/2] (default 1): ").strip() or "1"

            if maybe_step("5) Zero-point calibration in zero dissolved oxygen solution"):
                print("- place probe in zero DO solution")
                print("- avoid bubbles and let probe stabilize")
                countdown(90)
                run_command(dev, "Cal,0", timeout=1.5)
                run_command(dev, "Cal,?", timeout=0.9)

            if cal_mode == "2":
                if maybe_step("6) Atmospheric calibration point"):
                    print("- move probe to fully aerated water (or per Atlas procedure)")
                    print("- let the reading stabilize before calibrating")
                    countdown(90)
                    run_command(dev, "Cal", timeout=1.5)
                    run_command(dev, "Cal,?", timeout=0.9)

            print("\n7) Final calibration status")
            run_command(dev, "Cal,?", timeout=0.9)

            print("\n8) Live dissolved oxygen reading")
            status, text, raw = run_command(dev, "R", timeout=1.5)
            if status == 1 and text:
                try:
                    values = parse_float_values(text)
                    mg_l = values[0]
                    if len(values) > 1:
                        print(
                            f"   Live DO: {mg_l:.2f} mg/L "
                            f"| saturation={values[1]:.2f}%"
                        )
                    else:
                        print(f"   Live DO: {mg_l:.2f} mg/L")
                except Exception:
                    print("   Note: could not parse numeric DO reading.")
                    print(f"   raw: {raw}")

    except Exception as e:
        print(f"Calibration Error: {e}", file=sys.stderr)
        sys.exit(1)

    set_config_flag("enabled", True)
    set_config_flag("i2c_bus", bus_num)
    set_config_flag("address_hex", f"0x{addr:02X}")

    print("\nconfig.json updated: atlas_do.enabled = true")
    print("Calibration flow complete.")


if __name__ == "__main__":
    main()
