# Model 2 — YOLO26m Fine-Tuning on vehicles-q0x2v
# Google Colab script — Free GPU (T4)
# Run in Google Colab: upload this file and execute, or paste cells individually.

# ============================================================
# CELL 1: Setup
# ============================================================
# !pip install ultralytics opencv-python-headless datasets huggingface-hub scikit-learn

import os, sys, numpy as np, cv2, torch, json, shutil
from pathlib import Path
from collections import Counter
from datasets import load_dataset
from ultralytics import YOLO

print(f"PyTorch: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")

# ============================================================
# CELL 2: Download dataset and convert to YOLO format
# ============================================================
HF_DATASET = "Francesco/vehicles-q0x2v"
DATASET_DIR = Path("/content/dataset")
WEIGHTS_DIR = Path("/content/weights")
OUTPUT_DIR = Path("/content/output")

for d in [DATASET_DIR, WEIGHTS_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

print("Downloading dataset...")
dataset = load_dataset(HF_DATASET)

# Class mapping: dataset uses IDs 1-12, YOLO uses 0-11
CLASS_NAMES = {
    0: "big bus", 1: "big truck", 2: "bus-l-", 3: "bus-s-",
    4: "car", 5: "mid truck", 6: "small bus", 7: "small truck",
    8: "truck-l-", 9: "truck-m-", 10: "truck-s-", 11: "truck-xl-",
}
NUM_CLASSES = 12

for split in ["train", "validation", "test"]:
    (DATASET_DIR / split / "images").mkdir(parents=True, exist_ok=True)
    (DATASET_DIR / split / "labels").mkdir(parents=True, exist_ok=True)

split_map = {"train": "train", "validation": "validation", "test": "test"}
for split_name, yolo_split in split_map.items():
    if split_name not in dataset:
        continue
    split_data = dataset[split_name]
    print(f"Converting {split_name}: {len(split_data)} images...")
    for idx, sample in enumerate(split_data):
        img = sample["image"]
        width = sample["width"]
        height = sample["height"]
        objects = sample["objects"]
        img_filename = f"{split_name}_{idx:06d}.jpg"
        img.save(str(DATASET_DIR / yolo_split / "images" / img_filename))
        label_filename = f"{split_name}_{idx:06d}.txt"
        label_path = DATASET_DIR / yolo_split / "labels" / label_filename
        lines = []
        for i in range(len(objects["id"])):
            bbox = objects["bbox"][i]
            category = objects["category"][i]
            yolo_class = category - 1
            if yolo_class < 0:
                continue
            x_center = (bbox[0] + bbox[2] / 2) / width
            y_center = (bbox[1] + bbox[3] / 2) / height
            norm_w = bbox[2] / width
            norm_h = bbox[3] / height
            lines.append(f"{yolo_class} {x_center:.6f} {y_center:.6f} {norm_w:.6f} {norm_h:.6f}")
        label_path.write_text("\n".join(lines))

yaml_content = f"""path: {DATASET_DIR.absolute()}
train: train/images
val: validation/images
test: test/images
names:
"""
for cls_id, cls_name in sorted(CLASS_NAMES.items()):
    yaml_content += f"  {cls_id}: {cls_name}\n"

DATASET_YAML = DATASET_DIR / "dataset.yaml"
DATASET_YAML.write_text(yaml_content)
print(f"Dataset ready! YAML: {DATASET_YAML}")

# ============================================================
# CELL 3: Train YOLO26m
# ============================================================
model = YOLO("yolo26m.pt")
print("YOLO26m loaded!")

results = model.train(
    data=str(DATASET_YAML),
    epochs=30,
    batch=16,
    imgsz=640,
    lr0=0.001,
    patience=10,
    device=0,
    project=str(WEIGHTS_DIR),
    name="yolo26m_vehicles",
    exist_ok=True,
    pretrained=True,
    optimizer="AdamW",
    cos_lr=True,
    augment=True,
    mosaic=1.0,
    mixup=0.1,
    verbose=True,
)

best_weights = WEIGHTS_DIR / "yolo26m_vehicles" / "weights" / "best.pt"
if best_weights.exists():
    shutil.copy2(best_weights, WEIGHTS_DIR / "yolo26m_vehicles_best.pt")
    print(f"Best model saved: {WEIGHTS_DIR / 'yolo26m_vehicles_best.pt'}")

# ============================================================
# CELL 4: Evaluate
# ============================================================
MODEL_PATH = WEIGHTS_DIR / "yolo26m_vehicles_best.pt"
model = YOLO(str(MODEL_PATH))

test_dataset = load_dataset(HF_DATASET, split="test")
per_class = {name: {"tp": 0, "fp": 0, "fn": 0, "total_gt": 0} for name in CLASS_NAMES.values()}

print(f"Evaluating on {len(test_dataset)} test images...")
for idx in range(len(test_dataset)):
    sample = test_dataset[idx]
    img = np.array(sample["image"])
    width = sample["width"]
    height = sample["height"]
    objects = sample["objects"]

    gt_boxes = []
    for i in range(len(objects["id"])):
        bbox = objects["bbox"][i]
        cat = objects["category"][i]
        yolo_cls = cat - 1
        if yolo_cls < 0:
            continue
        cls_name = CLASS_NAMES.get(yolo_cls, f"class_{yolo_cls}")
        x1, y1 = int(bbox[0]), int(bbox[1])
        x2, y2 = int(bbox[0] + bbox[2]), int(bbox[1] + bbox[3])
        gt_boxes.append({"bbox": [x1, y1, x2, y2], "class_name": cls_name})
        per_class[cls_name]["total_gt"] += 1

    results = model.predict(source=img, conf=0.25, iou=0.45, verbose=False)
    pred_boxes = []
    if results and len(results) > 0 and results[0].boxes is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        confs = results[0].boxes.conf.cpu().numpy()
        cls_ids = results[0].boxes.cls.cpu().numpy().astype(int)
        for i in range(len(boxes)):
            cls_name = CLASS_NAMES.get(int(cls_ids[i]), f"class_{cls_ids[i]}")
            pred_boxes.append({"bbox": boxes[i].tolist(), "confidence": float(confs[i]), "class_name": cls_name})

    matched_gt = set()
    for pred in pred_boxes:
        best_iou = 0
        best_gt = -1
        for gi, gt in enumerate(gt_boxes):
            if gi in matched_gt or gt["class_name"] != pred["class_name"]:
                continue
            x1 = max(pred["bbox"][0], gt["bbox"][0])
            y1 = max(pred["bbox"][1], gt["bbox"][1])
            x2 = min(pred["bbox"][2], gt["bbox"][2])
            y2 = min(pred["bbox"][3], gt["bbox"][3])
            inter = max(0, x2 - x1) * max(0, y2 - y1)
            a1 = (pred["bbox"][2] - pred["bbox"][0]) * (pred["bbox"][3] - pred["bbox"][1])
            a2 = (gt["bbox"][2] - gt["bbox"][0]) * (gt["bbox"][3] - gt["bbox"][1])
            union = a1 + a2 - inter
            iou = inter / union if union > 0 else 0
            if iou > best_iou:
                best_iou = iou
                best_gt = gi
        pred_cls = pred["class_name"]
        if pred_cls not in per_class:
            per_class[pred_cls] = {"tp": 0, "fp": 0, "fn": 0, "total_gt": 0}
        if best_iou >= 0.5 and best_gt >= 0:
            per_class[pred_cls]["tp"] += 1
            matched_gt.add(best_gt)
        else:
            per_class[pred_cls]["fp"] += 1

    for gi, gt in enumerate(gt_boxes):
        if gi not in matched_gt:
            per_class[gt["class_name"]]["fn"] += 1

total_tp = sum(s["tp"] for s in per_class.values())
total_fp = sum(s["fp"] for s in per_class.values())
total_fn = sum(s["fn"] for s in per_class.values())

precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
accuracy = total_tp / (total_tp + total_fp + total_fn) if (total_tp + total_fp + total_fn) > 0 else 0

print("\n" + "=" * 60)
print("EVALUATION RESULTS — YOLO26m on vehicles-q0x2v test set")
print("=" * 60)
print(f"Total test images: {len(test_dataset)}")
print(f"Overall Accuracy:  {accuracy:.4f} ({accuracy*100:.2f}%)")
print(f"Overall Precision: {precision:.4f} ({precision*100:.2f}%)")
print(f"Overall Recall:    {recall:.4f} ({recall*100:.2f}%)")
print(f"Overall F1 Score:  {f1:.4f} ({f1*100:.2f}%)")
print("-" * 60)
print("Per-Class Metrics:")
print(f"{'Class':<15} {'Precision':>10} {'Recall':>10} {'F1':>10} {'TP':>6} {'FP':>6} {'FN':>6}")
for cls_name, m in sorted(per_class.items()):
    tp, fp, fn = m["tp"], m["fp"], m["fn"]
    p = tp / (tp + fp) if (tp + fp) > 0 else 0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0
    f = 2 * p * r / (p + r) if (p + r) > 0 else 0
    print(f"{cls_name:<15} {p:>10.4f} {r:>10.4f} {f:>10.4f} {tp:>6} {fp:>6} {fn:>6}")
print("=" * 60)

metrics = {
    "overall": {"accuracy": accuracy, "precision": precision, "recall": recall, "f1": f1},
    "per_class": {k: v for k, v in per_class.items()},
    "total_images": len(test_dataset),
}
(OUTPUT_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2))
print(f"Metrics saved to: {OUTPUT_DIR / 'metrics.json'}")

# ============================================================
# CELL 5: Save model to Google Drive (optional)
# ============================================================
# from google.colab import drive
# drive.mount('/content/drive')
# shutil.copy2(WEIGHTS_DIR / "yolo26m_vehicles_best.pt", "/content/drive/MyDrive/yolo26m_vehicles_best.pt")
# print("Model saved to Google Drive!")
