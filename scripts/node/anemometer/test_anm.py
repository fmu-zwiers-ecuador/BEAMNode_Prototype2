import serial

ser = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)

try:
    print("Reading anemometer data... (Ctrl+C to stop)")
    while True:
        line = ser.readline().decode('utf-8').strip()
        if line:
            print(line)

except KeyboardInterrupt:
    print("\nStopped by user.")

except serial.SerialException as e:
    print(f"\nSerial error: {e}")

finally:
    ser.close()
    print("Serial port closed.")
