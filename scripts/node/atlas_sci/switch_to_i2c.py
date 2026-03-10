import serial
import time

def baud_sweep_switch():
    port = '/dev/serial0'
    # All possible standard baud rates for EZO circuits
    baud_rates = [9600, 19200, 38400, 57600, 115200]
    switch_commands = [b"I2C,100\r", b"I2C,100\n", b"I2C,100\r\n"]
    wake_commands = [b"\r", b"\n", b"\r\n"]
    
    print("Starting Comprehensive Baud Sweep...")
    
    for baud in baud_rates:
        try:
            print(f"Testing {baud} baud...")
            ser = serial.Serial(port, baud, timeout=0.6)
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            # Wake any UART-mode circuit first.
            for wake in wake_commands:
                ser.write(wake)
                time.sleep(0.08)
            
            # Send switch command variants repeatedly at this baud.
            for _ in range(4):
                for cmd in switch_commands:
                    ser.write(cmd)
                    ser.flush()
                    time.sleep(0.12)
                    _ = ser.read_all()
                
            ser.close()
            # Give the circuit a moment to process before switching speeds
            time.sleep(0.6)
            
        except Exception as e:
            print(f"Could not test {baud}: {e}")

    print("\nSweep complete. Re-run detect.py and confirm atlas_ec appears at i2c address 0x64.")

if __name__ == "__main__":
    baud_sweep_switch()
