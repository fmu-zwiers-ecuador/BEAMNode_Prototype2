#!/usr/bin/env python3
"""
Switch Atlas EZO pH from UART mode to I2C mode.

Hardware context:
- pH circuit is connected to a UART-capable serial interface.
- In UART mode, the status LED usually flashes.

This script sweeps common UART ports/baud rates and sends the pH
I2C switch command for address 0x63 (decimal 99).
"""

import os
import time

import serial

TARGET_I2C_ADDR_DEC = 99   # 0x63 (Atlas pH default)
TARGET_I2C_ADDR_HEX = "0x63"

CANDIDATE_PORTS = [
    "/dev/ttyAMA0",   # Pi Zero (no-W) hardware UART
    "/dev/serial0",   # symlink to primary UART
    "/dev/ttyS0",     # Pi Zero W mini-UART fallback
    "/dev/ttyUSB0",   # USB-UART fallback
    "/dev/ttyUSB1",
]


def baud_sweep_switch_ph():
    baud_rates = [9600, 19200, 38400, 57600, 115200]

    # Atlas EZO expects ASCII command ending with CR. We try variants for robustness.
    switch_commands = [
        f"I2C,{TARGET_I2C_ADDR_DEC}\r".encode(),
        f"I2C,{TARGET_I2C_ADDR_DEC}\n".encode(),
        f"I2C,{TARGET_I2C_ADDR_DEC}\r\n".encode(),
    ]
    wake_commands = [b"\r", b"\n", b"\r\n"]

    available_ports = [p for p in CANDIDATE_PORTS if os.path.exists(p)]
    if not available_ports:
        print("ERROR: No UART serial ports found. Check UART is enabled on the Pi.")
        return

    print("=== Atlas pH UART -> I2C Switch ===")
    print("Target I2C address:", TARGET_I2C_ADDR_HEX, f"(decimal {TARGET_I2C_ADDR_DEC})")
    print("Detected serial ports:", available_ports)
    print()

    any_response = False

    for port in available_ports:
        print(f"--- Port: {port} ---")
        for baud in baud_rates:
            try:
                print(f"  Testing {baud} baud...", end=" ")
                ser = serial.Serial(port, baud, timeout=0.8)
                ser.reset_input_buffer()
                ser.reset_output_buffer()

                # Wake the circuit first.
                for wake in wake_commands:
                    ser.write(wake)
                    time.sleep(0.1)
                ser.reset_input_buffer()

                got_response = False
                for _ in range(5):
                    for cmd in switch_commands:
                        ser.write(cmd)
                        ser.flush()
                        time.sleep(0.35)
                        resp = ser.read_all()
                        if resp:
                            got_response = True

                if got_response:
                    any_response = True
                    print("RESPONSE RECEIVED (switch likely accepted).")
                else:
                    print("no response.")

                ser.close()
                time.sleep(0.5)

            except serial.SerialException as e:
                print(f"could not open ({e})")
            except Exception as e:
                print(f"unexpected error ({e})")

    print("\nSweep complete.")
    if any_response:
        print(f"Now check I2C for pH at {TARGET_I2C_ADDR_HEX}:")
    else:
        print("No UART responses were seen. Still check I2C once, then verify wiring/UART config.")

    print("  sudo i2cdetect -y 1")
    print("If not found on bus 1, also check:")
    print("  sudo i2cdetect -y 2")
    print(f"Expected address: {TARGET_I2C_ADDR_HEX}")


if __name__ == "__main__":
    baud_sweep_switch_ph()
