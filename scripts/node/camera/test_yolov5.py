#!/usr/bin/env python3
import cv2
from picamera2 import Picamera2
import time
import os
from datetime import datetime

MODEL_PATH = "models/yolov5n.onnx"
CLASSES_PATH = "models/coco.names"
SAVE_DIR = "./data/camera"

os.makedirs(SAVE_DIR, exist_ok=True)

# Load class names
with open(CLASSES_PATH, "r") as f:
    classes = [line.strip() for line in f.readlines()]

# Load YOLO model
net = cv2.dnn.readNetFromONNX(MODEL_PATH)
net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

# Setup camera
picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(
    main={"format": "RGB888", "size": (320, 240)}  # low res for Pi Zero 2
))
picam2.start()

time.sleep(2)

CONF_THRESHOLD = 0.5
NMS_THRESHOLD = 0.4

print("YOLO offline detection started...")

while True:
    frame = picam2.capture_array()

    blob = cv2.dnn.blobFromImage(frame, 1/255.0, (320, 320), swapRB=True, crop=False)
    net.setInput(blob)
    outputs = net.forward()

    h, w, _ = frame.shape

    boxes = []
    confidences = []
    class_ids = []

    for detection in outputs[0]:
        scores = detection[5:]
        class_id = scores.argmax()
        confidence = scores[class_id]

        if confidence > CONF_THRESHOLD:
            cx, cy, bw, bh = detection[0:4]
            x = int((cx - bw / 2) * w)
            y = int((cy - bh / 2) * h)
            bw = int(bw * w)
            bh = int(bh * h)

            boxes.append([x, y, bw, bh])
            confidences.append(float(confidence))
            class_ids.append(class_id)

    indices = cv2.dnn.NMSBoxes(boxes, confidences, CONF_THRESHOLD, NMS_THRESHOLD)

    if len(indices) > 0:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(SAVE_DIR, f"yolo_{timestamp}.jpg")
        cv2.imwrite(filename, frame)
        print(f"[+] Detection saved: {filename}")

        for i in indices.flatten():
            print(f"Detected: {classes[class_ids[i]]} ({confidences[i]:.2f})")

    time.sleep(1.5)  # slow loop to save CPU
