import serial
import time
import os

# All ports the Pi may expose the hardware UART on.
# serial0 is a symlink that may point to ttyAMA0 or ttyS0 depending on the Pi model.
CANDIDATE_PORTS = [
    "/dev/ttyAMA0",   # Pi 3/4/5 primary UART (Bluetooth disabled or using mini-UART)
    "/dev/serial0",   # symlink - try explicitly as well
    "/dev/ttyS0",     # Pi 3 mini-UART (lower performance, may still work)
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
