"""Validate the fine-tuned model loads and runs inference."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from datasets import load_dataset

print("Loading fine-tuned YOLO26m...")
from ultralytics import YOLO
model = YOLO(r"C:\Users\katha\Hackathons\CCTV Hackathon\weights\yolo26m_vehicles_best.pt")
print(f"Model loaded: {model.task}")
print(f"Class names: {model.names}")

# Load one test image
ds = load_dataset("Francesco/vehicles-q0x2v", split="test")
sample = ds[0]
img = np.array(sample["image"])
print(f"Test image: {img.shape}")

# Run detection
results = model.predict(source=img, conf=0.25, verbose=False)
boxes = results[0].boxes
print(f"Detections: {len(boxes)}")
for i in range(len(boxes)):
    cls_id = int(boxes.cls[i])
    conf = float(boxes.conf[i])
    print(f"  -> {model.names[cls_id]} conf={conf:.3f}")

print("\nFine-tuned model works locally!")