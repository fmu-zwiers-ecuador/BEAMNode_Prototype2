import RPi.GPIO as GPIO
import time
import threading

ANEMOMETER_PIN = 17 # GPIO pin connected to anemometer (BCM numbering = physical pin)

# OS-FS01: 1 pulse/rev, 1 Hz = 2.4 km/h = 0.6667 m/s
MS_PER_HZ = 0.6667
SAMPLE_WINDOW = 2  # seconds to measure per reading

pulse_count = 0
lock = threading.Lock()

def count_pulse(channel):
    global pulse_count
    with lock:
        pulse_count += 1
    print("Pulse!") # remove once confirmed working

GPIO.setmode(GPIO.BCM)
GPIO.setup(ANEMOMETER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(ANEMOMETER_PIN, GPIO.FALLING, callback=count_pulse, bouncetime=5)

print("Measuring wind speed... Press CTRL+C to stop")

try:
    while True:
        # Reset count at start of window
        with lock:
            pulse_count = 0

        time.sleep(SAMPLE_WINDOW)

        with lock:
            count = pulse_count
            pulse_count = 0
        
        frequency = count / SAMPLE_WINDOW
        wind_speed_ms = frequency * MS_PER_HZ
        wind_speed_kmh = wind_speed_ms * 3.6

        print(f"{wind_speed_ms:.2f} m/s | {wind_speed_kmh:.2f} km/h")

except KeyboardInterrupt:
    print("\nCLeaning up...")
    GPIO.remove_event_detect(ANEMOMETER_PIN)
    GPIO.cleanup()