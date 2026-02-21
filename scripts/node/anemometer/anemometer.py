import RPi.GPIO as GPIO
import time

#GPIO pin where signal wire is connected
ANEMOMETER_PIN = 17

#Calibration factor for OS-FS01 (anemometer model)
MS_PER_HZ = 0.6667

GPIO.setmode(GPIO.BCM)
GPIO.setup(ANEMOMETER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

last_state = GPIO.input(ANEMOMETER_PIN)

print("Measuring wind speed ... Press CTRL+C to stop")

try:
    while True:
        pulse_count = 0
        start_time = time.time()

        while time.time() - start_time < 2:
            state = GPIO.input(ANEMOMETER_PIN)
            if state == 0 and last_state == 1:
                pulse_count += 1
            last_state = state

        frequency = pulse_count / 2
        wind_speed_ms = frequency * MS_PER_HZ
        wind_speed_kmh = wind_speed_ms * 3.6

        print(f"{wind_speed_ms:.2f} m/s | {wind_speed_kmh:.2f} km/h")
        print("")

except KeyboardInterrupt:
    GPIO.cleanup()