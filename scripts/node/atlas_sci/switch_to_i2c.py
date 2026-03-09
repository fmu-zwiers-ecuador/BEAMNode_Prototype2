import serial
import time

def force_i2c():
    # serial0 is the most reliable alias for Pi Zero GPIO pins
    port = '/dev/serial0' 
    
    # We will try the two standard Atlas baud rates
    for baud in [9600, 19200]:
        try:
            print(f"Opening {port} at {baud} baud...")
            # We add a 2-second timeout to give it space to breathe
            ser = serial.Serial(port, baud, timeout=2)
            
            # Send the command with a clear carriage return
            print("Sending 'I2C,100'...")
            ser.write(b"I2C,100\r")
            
            time.sleep(1.5)
            ser.close()
            print("Command sent. Check for Solid Blue LED.")
        except Exception as e:
            print(f"Failed on {baud}: {e}")

if __name__ == "__main__":
    force_i2c()
