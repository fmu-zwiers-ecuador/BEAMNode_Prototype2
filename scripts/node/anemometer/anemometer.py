import RPi.GPIO as GPIO
import time

#GPIO pin where signal wire is connected
ANEMOMETER_PIN = 17

#Calibration factor for OS-FS01 (anemometer model)
MS_PER_HZ = 0.6667

pulse_count = 0

def count_pulse(channel):
    global pulse_count
    pulse_count += 1

GPIO.setmode(GPIO.BCM)
GPIO.setup(ANEMOMETER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

#Count falling edges from sensor
GPIO.add_event_detect(ANEMOMETER_PIN, GPIO.FALLING, callback=count_pulse)

print("Measuring wind speed ... Press CTRL+C to stop")

try:
    while True:
        pulse_count = 0
        start_time = time.time()

        time.sleep(2) #measurement window (seconds)
        
        duration = time.time() - start_time
        frequency = pulse_count / duration

        wind_speed_ms = frequency * MS_PER_HZ
        wind_speed_kmh = wind_speed_ms * 3.6

        print(f"Pulses: {pulse_count}")
        print(f"Wind Speed: {wind_speed_ms:.2f} m/s | {wind_speed_kmh:.2f}")
        print("")

except KeyboardInterrupt:
    GPIO.cleanup()