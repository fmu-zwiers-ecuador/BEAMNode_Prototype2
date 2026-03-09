import serial
import time

def baud_sweep_switch():
    port = '/dev/serial0'
    # All possible standard baud rates for EZO circuits
    baud_rates = [9600, 19200, 38400, 57600, 115200]
    
    print("Starting Comprehensive Baud Sweep...")
    
    for baud in baud_rates:
        try:
            print(f"Testing {baud} baud...")
            ser = serial.Serial(port, baud, timeout=0.5)
            
            # Send the command 10 times rapidly at this specific speed
            for _ in range(10):
                ser.write(b"I2C,100\r")
                time.sleep(0.1)
                
            ser.close()
            # Give the circuit a moment to process before switching speeds
            time.sleep(0.5) 
            
        except Exception as e:
            print(f"Could not test {baud}: {e}")

    print("\nSweep complete. If the LED is still flashing, check /boot/config.txt.")

if __name__ == "__main__":
    baud_sweep_switch()
