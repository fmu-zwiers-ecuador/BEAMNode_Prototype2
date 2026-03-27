from picamera2 import Picamera2
import time

picam = Picamera2()

config = picam.create_still_configuration(
    main={"size": (1920,1080)}
)

picam.configure(config)
picam.start()

print("Camera warming up...")
time.sleep(2)

filename = "camera_test.jpg"

picam.capture_file(filename)

print(f"Image saved: {filename}")