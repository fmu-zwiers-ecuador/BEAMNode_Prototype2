#!/usr/bin/env python3
"""
Ultrasonic sensor TEST / DIAGNOSTIC script for Raspberry Pi 4
Run this first to confirm your sensor is wired and working correctly.

HC-SR04 wiring:
  VCC  -> Pin 2  (5V)
  GND  -> Pin 6  (GND)
  TRIG -> Pin 11 (GPIO 17)
  ECHO -> Pin 13 (GPIO 27)  ** use voltage divider on ECHO line **

What this script checks:
  1. GPIO can be set up without errors
  2. TRIG pin can be toggled
  3. ECHO pin responds to pulses
  4. Distance readings are in a valid range
  5. Readings are consistent (low variance = good sensor)
"""

import RPi.GPIO as GPIO
import time
import statistics

TRIG = 17
ECHO = 27
TEST_SAMPLES = 10


def setup():
    print("=" * 50)
    print("  HC-SR04 Ultrasonic Sensor — Diagnostic Test")
    print("=" * 50)
    print()

    print("[1/4] Setting up GPIO... ", end="", flush=True)
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(TRIG, GPIO.OUT)
        GPIO.setup(ECHO, GPIO.IN)
        GPIO.output(TRIG, False)
        print("OK")
    except Exception as e:
        print(f"FAILED\n      Error: {e}")
        print("      Check that RPi.GPIO is installed: pip install RPi.GPIO")
        raise

    print("[2/4] Waiting for sensor to stabilise (2s)... ", end="", flush=True)
    time.sleep(2)
    print("OK")


def single_reading():
    """Take one raw distance reading. Returns cm or None on timeout."""
    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    pulse_start = time.time()
    timeout = pulse_start + 0.04
    while GPIO.input(ECHO) == 0:
        pulse_start = time.time()
        if pulse_start > timeout:
            return None

    pulse_end = time.time()
    timeout = pulse_end + 0.04
    while GPIO.input(ECHO) == 1:
        pulse_end = time.time()
        if pulse_end > timeout:
            return None

    distance = (pulse_end - pulse_start) * 17150
    return round(distance, 2)


def test_echo_response():
    print("[3/4] Testing ECHO response... ", end="", flush=True)
    result = single_reading()
    if result is None:
        print("FAILED")
        print()
        print("      ECHO pin did not respond. Possible causes:")
        print("      - TRIG or ECHO wired to wrong pins")
        print("      - Voltage divider missing on ECHO line (5V -> 3.3V)")
        print("      - Sensor not powered (check VCC and GND)")
        print("      - Defective sensor")
        return False
    else:
        print(f"OK (first reading: {result} cm)")
        return True


def test_consistency():
    print(f"[4/4] Taking {TEST_SAMPLES} readings to check consistency...")
    print()

    readings = []
    errors = 0

    for i in range(1, TEST_SAMPLES + 1):
        r = single_reading()
        if r is None:
            errors += 1
            print(f"      Reading {i:2d}: TIMEOUT")
        elif r < 2 or r > 400:
            print(f"      Reading {i:2d}: {r:7.2f} cm  [OUT OF RANGE — sensor range is 2-400 cm]")
        else:
            readings.append(r)
            print(f"      Reading {i:2d}: {r:7.2f} cm")
        time.sleep(0.15)  # HC-SR04 needs ~60ms between readings minimum

    print()
    print("-" * 50)
    print("  Results summary")
    print("-" * 50)

    if errors > 0:
        print(f"  Timeouts:   {errors}/{TEST_SAMPLES}")

    if len(readings) == 0:
        print("  No valid readings — sensor is not working correctly.")
        return

    avg = statistics.mean(readings)
    mn  = min(readings)
    mx  = max(readings)

    print(f"  Valid readings: {len(readings)}/{TEST_SAMPLES}")
    print(f"  Average:        {avg:.2f} cm")
    print(f"  Min:            {mn:.2f} cm")
    print(f"  Max:            {mx:.2f} cm")
    print(f"  Spread (max-min): {mx - mn:.2f} cm")

    if len(readings) >= 2:
        stdev = statistics.stdev(readings)
        print(f"  Std deviation:  {stdev:.2f} cm")

        print()
        if stdev < 1.0:
            print("  [PASS] Sensor readings are stable and consistent.")
        elif stdev < 5.0:
            print("  [WARN] Moderate variance — could be normal if object is moving.")
            print("         Try pointing sensor at a flat wall for a cleaner test.")
        else:
            print("  [WARN] High variance — sensor may be:")
            print("         - Pointed at a curved or angled surface")
            print("         - Too close to or too far from target")
            print("         - Affected by interference or faulty wiring")

    print()
    if len(readings) == TEST_SAMPLES and errors == 0:
        print("  OVERALL: Sensor appears to be working correctly.")
    elif len(readings) >= TEST_SAMPLES // 2:
        print("  OVERALL: Sensor is partially working — check wiring.")
    else:
        print("  OVERALL: Sensor is NOT working reliably — check wiring and power.")

    print()


def run():
    try:
        setup()
        print()

        if not test_echo_response():
            return

        print()
        test_consistency()

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
    finally:
        GPIO.cleanup()
        print("GPIO cleaned up.")
        print("=" * 50)


if __name__ == "__main__":
    run()
