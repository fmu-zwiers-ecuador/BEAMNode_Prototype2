import serial
import time
import os

# ============================================================
# IMPORTANT — i3 InterLink users:
#
# The Atlas Scientific i3 InterLink board speaks I2C only.
# UART commands from this script CANNOT reach an EZO circuit
# seated inside the InterLink. You MUST temporarily remove the
# EC circuit from the InterLink and wire it directly to the
# Pi GPIO UART pins before running this script:
#
#   Pi GPIO 14 (TXD, pin 8)  →  EZO TX
#   Pi GPIO 15 (RXD, pin 10) →  EZO RX
#   Pi 3.3V (pin 1 or 17)    →  EZO VCC
#   Pi GND   (pin 6 or 14)   →  EZO GND
#
# Pi Zero (no W): /dev/ttyAMA0 is the hardware UART — use this.
# Pi Zero W:      /dev/ttyAMA0 is taken by Bluetooth.
#                 Either use /dev/ttyS0 (mini-UART, less reliable)
#                 or add  dtoverlay=miniuart-bt  to /boot/config.txt
#                 and reboot so ttyAMA0 is free.
#
# Also make sure the serial console is disabled:
#   sudo raspi-config → Interface Options → Serial Port
#     "login shell over serial" = NO
#     "serial port hardware enabled" = YES
#
# After the switch succeeds re-seat the circuit in the InterLink.
# ============================================================

CANDIDATE_PORTS = [
    "/dev/ttyAMA0",   # Pi Zero (no-W) hardware UART / Pi 3+4 when BT moved off
    "/dev/serial0",   # symlink — resolves to ttyAMA0 or ttyS0 depending on model
    "/dev/ttyS0",     # Pi Zero W mini-UART fallback
    "/dev/ttyUSB0",   # USB-UART adapter fallback
    "/dev/ttyUSB1",
]

def baud_sweep_switch():
    # All possible standard baud rates for EZO circuits
    baud_rates = [9600, 19200, 38400, 57600, 115200]
    switch_commands = [b"I2C,100\r", b"I2C,100\n", b"I2C,100\r\n"]
    wake_commands = [b"\r", b"\n", b"\r\n"]

    # Resolve which ports actually exist on this Pi.
    available_ports = [p for p in CANDIDATE_PORTS if os.path.exists(p)]
    if not available_ports:
        print("ERROR: No UART serial ports found. Check /boot/config.txt has enable_uart=1.")
        return

    print(f"Found ports: {available_ports}")
    print("Starting Comprehensive Baud Sweep across all ports...\n")

    for port in available_ports:
        print(f"--- Port: {port} ---")
        for baud in baud_rates:
            try:
                print(f"  Testing {baud} baud...", end=" ")
                ser = serial.Serial(port, baud, timeout=0.8)
                ser.reset_input_buffer()
                ser.reset_output_buffer()

                # Wake the circuit - it needs a CR to come out of sleep.
                for wake in wake_commands:
                    ser.write(wake)
                    time.sleep(0.1)
                ser.reset_input_buffer()

                # Send switch command variants; read response after each attempt.
                got_response = False
                for _ in range(5):
                    for cmd in switch_commands:
                        ser.write(cmd)
                        ser.flush()
                        time.sleep(0.3)
                        resp = ser.read_all()
                        if resp:
                            got_response = True

                if got_response:
                    print(f"RESPONSE RECEIVED at {baud} baud on {port} — circuit likely switched.")
                else:
                    print("no response.")

                ser.close()
                time.sleep(0.6)

            except serial.SerialException as e:
                print(f"  Could not open {port} at {baud}: {e}")
            except Exception as e:
                print(f"  Unexpected error on {port} at {baud}: {e}")

    print("\nSweep complete.")
    print("Run: sudo i2cdetect -y 1")
    print("Address 0x64 should now appear. If not, check:")
    print("  1. sudo raspi-config > Interface Options > Serial Port")
    print("     - 'login shell over serial' = NO")
    print("     - 'serial port hardware enabled' = YES")
    print("  2. /boot/config.txt has: enable_uart=1")
    print("  3. Wiring: TX->RX, RX->TX, GND->GND, 3.3V->VCC")

if __name__ == "__main__":
    baud_sweep_switch()
