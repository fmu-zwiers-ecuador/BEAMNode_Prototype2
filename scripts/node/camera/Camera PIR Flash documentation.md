# **Camera, PIR, and Flash description, detection, motion capture, JSON output/logging, and testing**

---
# Basic Sensor Description

**System Description**

This BEAM node camera system uses three main hardware parts together: a Raspberry Pi CSI camera, a PIR motion sensor, and an LED flash connected to a GPIO pin. The camera captures still images when motion is detected. The PIR sensor is used to sense movement near the node, and the flash is used to improve nighttime image capture when the light level is low.

The camera itself connects to the Raspberry Pi through the CSI ribbon connector, not through the 40-pin GPIO header. The PIR sensor and flash do use the GPIO header, so the wiring for those two devices must match the configuration exactly.

The motion camera script reads `config.json` to determine whether the camera system is enabled, which GPIO pin is used for the PIR, whether flash is enabled, which GPIO controls the flash, image resolution, cooldown timing, and where images should be saved.

**Hardware Connections**

**1. Camera connection**

The CSI camera connects to the Raspberry Pi CSI camera port using the ribbon cable.

* Camera ribbon cable -> Raspberry Pi CSI port

This is not connected to GPIO pins.

**2. PIR sensor to Raspberry Pi pin assignments**

The PIR signal pin must match the `camera.pir_gpio` setting in `config.json`.

Current configured value:

* `camera.pir_gpio = 24`

Exact Raspberry Pi header wiring:

* PIR VCC -> Pin 2 or Pin 4 (5V Power)
* PIR GND -> Pin 6 (Ground)
* PIR OUT -> Pin 18 (GPIO24)

![PIR Sensor Pins](https://qqtrading.com.my/image/contents/pir_iso_botm_annot.jpg)

**3. Flash to Raspberry Pi pin assignments**

The flash GPIO must match the `camera.flash_gpio` setting in `config.json`.

Current configured value:

* `camera.flash_gpio = 17`

Exact Raspberry Pi header wiring:

* Flash control signal -> Pin 11 (GPIO17)
* Flash ground -> Any Ground pin such as Pin 9 or Pin 14

If an LED or higher-power light is being used, it should not be driven directly from the Raspberry Pi unless the hardware is designed for that. Use the proper driver circuit, transistor, or relay module if needed.

![GPIO Pin Diagram](https://i0.wp.com/randomnerdtutorials.com/wp-content/uploads/2023/03/Raspberry-Pi-Pinout-Random-Nerd-Tutorials.png?quality=100&strip=all&ssl=1)

**Detection Script Integration**

The `detect.py` script checks whether a camera is present and whether the PIR pin sees a motion signal during the sampling window.

For the camera:

* If a camera is detected, `config.json` is updated so `camera.enabled = true`
* If no camera is detected, the camera section is left disabled
* The detected camera model is stored in `camera.model`

For the PIR:

* The detector reads the configured `camera.pir_gpio`
* If that GPIO goes high during sampling, the script reports that the PIR was found
* If no high signal is seen, it prints `PIR Sensor Not Detected or Idle`

This allows the node to decide whether the motion camera script should run.

**Motion Capture Script Integration**

The `motion_flash_camera.py` script uses the camera section of `config.json`.

It follows this basic flow:

* Check if `camera.enabled` is true
* Load the PIR GPIO from `camera.pir_gpio`
* Set up the flash using `camera.flash_gpio` if `camera.flash_enabled` is true
* Start the camera using `Picamera2`
* Wait for motion from the PIR sensor
* Read the most recent lux value from the TSL2591 JSON log
* Turn the flash on if lux is below the configured threshold
* Capture and save a still image
* Append an image record to `images_log.json`

If `camera.enabled` is false, the motion script exits immediately and does not attempt to arm the PIR or start the camera.

**Data Logging Script Integration**

When motion is detected and an image is captured, the motion camera script appends a JSON record to the camera image log.

By default, image output goes into the camera data directory defined by:

* `global.base_dir`
* `camera.directory`

The current default path is typically:

`/home/pi/data/camera/`

The script also writes a JSON log file named:

`images_log.json`

Each record includes:

* UTC timestamp
* Local time
* Time zone
* Image file path
* Most recent lux value

This allows the supervisor and shipping flow to later collect the most recent image records from the node.

---

# Script explanation

**Basic Info**

* **Primary Tasks For Camera System:** Camera detection to config, PIR-triggered motion capture, flash-assisted nighttime capture, JSON image logging, and verification
* **Student(s):** Alexander Lance
* **Mentor/Reviewer:** Dr. Paul Zwiers / Raiz Mohammed
* **Date Started / Completed for this documentation:** April 1st, 2026
* **Status:** Done
* **GitHub Link:** [https://github.com/fmu-zwiers-ecuador/BEAMNode_Prototype2/tree/main/scripts/node/camera](https://github.com/fmu-zwiers-ecuador/BEAMNode_Prototype2/tree/main/scripts/node/camera)

---

**1) Summary**

The camera system is designed to arm a PIR motion sensor, detect movement, and capture an image using the Raspberry Pi camera. The flash can be enabled for low-light situations, and each capture is stored on disk and logged to JSON. The detection script updates the config so the node knows whether the camera system should be active.

---

**2) Goals**

* Main goal(s): Ensure the camera system is correctly wired, detected, triggered by PIR motion, and able to save images plus JSON log records
* How do we know it works? The detection script marks the camera as enabled, the motion camera script starts without error, motion causes an image to be saved, and `images_log.json` receives a new record with the correct timestamp and image path

---

**3) Setup**

* Hardware: Raspberry Pi, CSI camera module, PIR motion sensor, GPIO-controlled flash or LED flash driver
* Software: Python3, `picamera2`, `gpiozero`
* Install/run steps:

```bash
python3 /home/pi/BEAMNode_Prototype2/scripts/node/sensor_detection/detect.py
python3 /home/pi/BEAMNode_Prototype2/scripts/node/camera/motion_flash_camera.py
```

Important config values in `scripts/node/config.json`:

```json
"camera": {
  "enabled": true,
  "pir_gpio": 24,
  "flash_enabled": true,
  "flash_gpio": 17,
  "resolution": [1920, 1080],
  "cooldown_sec": 1,
  "flash_lux_threshold": 10
}
```

---

**4) Method**

* Connect the CSI camera ribbon cable to the Raspberry Pi camera port
* Wire the PIR output to GPIO24, which is physical Pin 18
* Wire the flash control input to GPIO17, which is physical Pin 11
* Confirm `camera.pir_gpio` and `camera.flash_gpio` match the hardware wiring
* Run `detect.py` to update the camera configuration state
* Run `motion_flash_camera.py` to arm the system
* Trigger motion in front of the PIR to force an image capture
* Check that the image file is saved in `/home/pi/data/camera/`
* Check that `images_log.json` contains a new record

---

**5) Code**

* Main script(s):
  * `scripts/node/sensor_detection/detect.py`
  * `scripts/node/camera/motion_flash_camera.py`
  * `scripts/node/camera/flash_test.py`
  * `scripts/node/camera/daytime_flash_capture_test.py`

* Example run:

```bash
python3 /home/pi/BEAMNode_Prototype2/scripts/node/sensor_detection/detect.py
```

Example expected output:

```text
=== Sensor Detection Summary ===
Camera Found: imx219
PIR Sensor Found: GPIO 24
=== Detection Complete ===
```

Motion script run:

```bash
python3 /home/pi/BEAMNode_Prototype2/scripts/node/camera/motion_flash_camera.py
```

Example runtime messages:

```text
[BEAM] Motion camera armed on GPIO 24
[BEAM] Flash enabled: True
[BEAM] Warming up PIR...
[BEAM] PIR ready
[BEAM] Motion detected
[BEAM] Picture saved: /home/pi/data/camera/motionpic_20260401_120000Z.jpg
[BEAM] Capture logged to: /home/pi/data/camera/images_log.json
```

Important snippet:

```python
if not cam_config.get("enabled", False):
    print("[BEAM] Camera module disabled in config.")
    exit()

pir_pin = cam_config.get("pir_gpio", cam_config.get("gpio_pin", 4))
pir = MotionSensor(pir_pin)

if flash_enabled:
    flash_pin = cam_config.get("flash_gpio", 17)
    flash = OutputDevice(flash_pin)
```

---

**6) Testing**

* Run `detect.py` and verify the camera is reported when attached
* Verify the PIR message matches the configured GPIO and responds when motion is present
* Run `flash_test.py` to confirm the flash turns on and off correctly
* Run `daytime_flash_capture_test.py` to confirm the camera can capture an image while using the flash path
* Run `motion_flash_camera.py` and trigger motion in front of the PIR
* Confirm that:
  * an image file is created in `/home/pi/data/camera/`
  * `images_log.json` is updated
  * flash behavior matches low-light conditions

---

**7) References**

* [BEAMNode Prototype 2 Repository](https://github.com/fmu-zwiers-ecuador/BEAMNode_Prototype2)
* [Picamera2 Documentation](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
* [gpiozero Documentation](https://gpiozero.readthedocs.io/)
* [Raspberry Pi GPIO Pinout Reference](https://randomnerdtutorials.com/raspberry-pi-pinout-gpios/)
