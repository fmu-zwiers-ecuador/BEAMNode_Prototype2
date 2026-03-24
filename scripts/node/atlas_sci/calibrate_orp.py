#!/usr/bin/env python3
"""Author: Jackson Roberts | Atlas EZO ORP calibration wizard (single-point 225 mV) over I2C."""

import io
import fcntl
import json
import sys
import time

CONFIG_PATH = "/home/pi/BEAMNode_Prototype2/scripts/node/config.json"
CAL_MV = 225


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

    def query(self, command: str, timeout: float = 1.0, num_bytes: int = 31):
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


def parse_orp_i2c_settings(config):
    orp_config = config.get("atlas_orp", {})
    try:
        addr_hex = orp_config.get("address_hex") or "0x62"
        i2c_bus = orp_config.get("i2c_bus")
        bus_num = int(i2c_bus) if i2c_bus is not None else 1
        addr = int(str(addr_hex), 16)
        return bus_num, addr
    except Exception as e:
        print(
            f"Config Parse Error in atlas_orp: "
            f"address_hex={orp_config.get('address_hex')!r}, "
            f"i2c_bus={orp_config.get('i2c_bus')!r} | {e}",
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


def main():
    config = load_config()
    bus_num, addr = parse_orp_i2c_settings(config)

    print("=" * 62)
    print(" Atlas ORP Calibration Wizard")
    print(f" I2C bus: {bus_num} | address: 0x{addr:02X}")
    print(f" Target calibration: {CAL_MV} mV")
    print("=" * 62)

    print("\nBefore continuing:")
    print("- remove storage cap")
    print("- rinse probe with distilled/DI water and blot dry")
    print(f"- place probe in fresh {CAL_MV} mV ORP solution")
    choice = input("\nPress Enter to continue, or type 'q' to quit: ").strip().lower()
    if choice == "q":
        print("Aborted.")
        return

    try:
        with AtlasI2CDevice(addr, bus=bus_num) as dev:
            print("\n1) Identity check")
            status, text, raw = dev.query("I", timeout=0.5)
            print(f"   I -> status={status} ({STATUS_CODES.get(status, 'unknown')}), text='{text}'")
            if status != 1 or "ORP" not in text.upper():
                print("   WARNING: ORP identity check was not clean.")
                print(f"   raw: {raw}")
                keep_going = input("   Continue anyway? [y/N]: ").strip().lower()
                if keep_going not in ("y", "yes"):
                    print("Stopped by user.")
                    return

            print("\n2) Existing calibration status")
            status, text, raw = dev.query("Cal,?", timeout=0.9)
            print(f"   Cal,? -> status={status} ({STATUS_CODES.get(status, 'unknown')}), text='{text}'")
            if status != 1:
                print(f"   raw: {raw}")

            clear_first = input("\n3) Clear previous calibration first? [Y/n]: ").strip().lower()
            if clear_first in ("", "y", "yes"):
                status, text, raw = dev.query("Cal,clear", timeout=1.0)
                print(
                    f"   Cal,clear -> status={status} "
                    f"({STATUS_CODES.get(status, 'unknown')}), text='{text}'"
                )
                if status != 1:
                    print(f"   raw: {raw}")

            print(f"\n4) Stabilize probe in {CAL_MV} mV solution")
            print("   Keep probe submerged and gently swirl once.")
            countdown(90)

            print("\n5) Apply calibration")
            status, text, raw = dev.query(f"Cal,{CAL_MV}", timeout=1.0)
            print(
                f"   Cal,{CAL_MV} -> status={status} "
                f"({STATUS_CODES.get(status, 'unknown')}), text='{text}'"
            )
            if status != 1:
                print(f"   raw: {raw}")

            print("\n6) Verify stored calibration")
            status, text, raw = dev.query("Cal,?", timeout=0.9)
            print(f"   Cal,? -> status={status} ({STATUS_CODES.get(status, 'unknown')}), text='{text}'")
            if status != 1:
                print(f"   raw: {raw}")

            print("\n7) Live ORP reading")
            status, text, raw = dev.query("R", timeout=1.0)
            print(f"   R -> status={status} ({STATUS_CODES.get(status, 'unknown')}), text='{text}'")

            if status == 1 and text:
                try:
                    reading = float(text)
                    delta = reading - CAL_MV
                    print(
                        f"   Reading: {reading:.1f} mV "
                        f"(delta vs {CAL_MV} mV: {delta:+.1f} mV)"
                    )
                except ValueError:
                    print("   Note: could not parse numeric ORP reading.")
            else:
                print(f"   raw: {raw}")

    except Exception as e:
        print(f"Calibration Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("\nCalibration flow complete.")


if __name__ == "__main__":
    main()
