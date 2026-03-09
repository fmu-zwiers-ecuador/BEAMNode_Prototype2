import serial
import time

# For Raspberry Pi, /dev/serial0 is the primary UART alias
USB_PORT = '/dev/serial0' 

def force_i2c_mode():
    try:
        # EZO circuits default to 9600 baud in UART mode
        ser = serial.Serial(USB_PORT, 9600, timeout=2)
        print(f"Opening {USB_PORT}...")
        
        # Command: I2C,[decimal_address] followed by a carriage return
        # 100 decimal = 0x64 hex
        print("Sending 'I2C,100' command...")
        ser.write(b"I2C,100\r")
        
        time.sleep(1.5)
        print("Switch command sent. Check the LED—it should now be SOLID BLUE.")
        ser.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    force_i2c_mode()
