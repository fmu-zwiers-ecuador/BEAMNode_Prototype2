import serial
import time

def direct_switch():
    # After our config changes, ttyAMA0 is the reliable hardware port
    port = '/dev/ttyAMA0'
    
    try:
        # Most EZO circuits are 9600 baud in UART mode
        ser = serial.Serial(port, 9600, timeout=1)
        print(f"Communicating with {port}...")
        
        # We send the command 'I2C,100' with a carriage return 10 times
        for _ in range(10):
            ser.write(b"I2C,100\r")
            time.sleep(0.2)
            
        ser.close()
        print("Command sequence complete. Watch for the SOLID BLUE LED.")
    except Exception as e:
        print(f"Serial Error: {e}")

if __name__ == "__main__":
    direct_switch()
