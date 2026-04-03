#!/usr/bin/env python3
"""
Ultrasonic sensor detection script for Raspberry Pi 4
HC-SR04 wiring:
  VCC  -> Pin 2  (5V)
  GND  -> Pin 6  (GND)
  TRIG -> Pin 11 (GPIO 17)
  ECHO -> Pin 13 (GPIO 27)  ** use voltage divider on ECHO line **
"""

import RPi.GPIO as GPIO
import time

# --- Pin config ---
TRIG = 17
ECHO = 27

# Detection threshold in cm (anything closer = "detected")
DETECT_DISTANCE_CM = 30.0

# How long between checks (seconds)
CHECK_INTERVAL = 1.0


def setup():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(TRIG, GPIO.OUT)
    GPIO.setup(ECHO, GPIO.IN)
    GPIO.output(TRIG, False)
    print("Ultrasonic sensor ready. Waiting for sensor to settle...")
    time.sleep(2)


def get_distance_cm():
    """Send a pulse and measure the echo return time."""
    # Send 10µs trigger pulse
    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    # Wait for echo to go HIGH
    pulse_start = time.time()
    timeout = pulse_start + 0.04  # 40ms timeout
    while GPIO.input(ECHO) == 0:
        pulse_start = time.time()
        if pulse_start > timeout:
            return None  # Timeout — no echo received

    # Wait for echo to go LOW
    pulse_end = time.time()
    timeout = pulse_end + 0.04
    while GPIO.input(ECHO) == 1:
        pulse_end = time.time()
        if pulse_end > timeout:
            return None  # Timeout — echo stuck HIGH

    # Distance = (time * speed of sound) / 2
    # Speed of sound ~34300 cm/s
    pulse_duration = pulse_end - pulse_start
    distance = pulse_duration * 17150  # 34300 / 2
    return round(distance, 1)


def run():
    print(f"Detection threshold: {DETECT_DISTANCE_CM} cm")
    print(f"Checking every {CHECK_INTERVAL} second(s). Press Ctrl+C to stop.\n")

    try:
        while True:
            distance = get_distance_cm()

            if distance is None:
                print("[ERROR] No echo received — check wiring")
            elif distance < 2 or distance > 400:
                print(f"[OUT OF RANGE] Reading: {distance} cm")
            elif distance <= DETECT_DISTANCE_CM:
                print(f"[DETECTED] Object at {distance} cm — within {DETECT_DISTANCE_CM} cm threshold")
            else:
                print(f"[NOT DETECTED] Distance: {distance} cm — nothing within range")

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        GPIO.cleanup()
        print("GPIO cleaned up.")


if __name__ == "__main__":
    setup()
    run()
