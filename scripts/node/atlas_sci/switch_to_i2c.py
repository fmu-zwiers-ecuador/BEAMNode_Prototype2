import serial
import time

def force_conversion():
    port = '/dev/serial0'
    # Try both standard speeds back-to-back
    for baud in [9600, 19200]:
        try:
            ser = serial.Serial(port, baud, timeout=1)
            print(f"Blasting {baud} baud for 3 seconds...")
            end_time = time.time() + 3
            while time.time() < end_time:
                ser.write(b"I2C,100\r")
                time.sleep(0.05) # Faster blast
            ser.close()
        except Exception as e:
            print(f"Error on {baud}: {e}")

    print("\nCheck the LED now. If it's Blue, run 'sudo i2cdetect -y 1'.")

if __name__ == "__main__":
    force_conversion()
