"""
Model 2 — Evaluate YOLO26m on vehicles-q0x2v test set
Computes accuracy, precision, recall, F1, mAP and generates screenshots.
"""

import logging
from pathlib import Path
from typing import Dict, List

import numpy as np
import cv2

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import (
    HF_DATASET_NAME,
    DATASET_DIR,
    EVAL_SCREENSHOTS_DIR,
    METRICS_DIR,
    VEHICLE_CLASSES,
    CONFIDENCE_THRESHOLD,
    IOU_THRESHOLD,
    IMG_SIZE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("sentinel.evaluation")

# Color palette for visualization
CLASS_COLORS = {
    "car": (0, 255, 0),
    "truck": (255, 0, 0),
    "bus": (0, 165, 255),
    "van": (255, 255, 0),
    "motorcycle": (255, 0, 255),
}


def compute_iou(box1, box2):
    """IoU between two boxes [x1,y1,x2,y2]."""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    a1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    a2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union = a1 + a2 - inter
    return inter / union if union > 0 else 0


def draw_detections(image, detections, color_override=None):
    """Draw bounding boxes and labels on image."""
    img = image.copy() if isinstance(image, np.ndarray) else np.array(image)
    if len(img.shape) == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    for det in detections:
        x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
        cls_name = det.get("class_name", "unknown")
        conf = det.get("confidence", 0)
        color = color_override or CLASS_COLORS.get(cls_name, (0, 255, 0))
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        label = f"{cls_name} {conf:.2%}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(img, (x1, y1 - th - 8), (x1 + tw + 4, y1), color, -1)
        cv2.putText(img, label, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    return img


def evaluate_model():
    """Run evaluation on test split."""
    from ultralytics import YOLO
    from pipeline.config import FINETUNED_WEIGHTS, YOLO26M_PRETRAINED

    if FINETUNED_WEIGHTS.exists():
        logger.info(f"Loading fine-tuned model: {FINETUNED_WEIGHTS}")
        model = YOLO(str(FINETUNED_WEIGHTS))
    else:
        logger.info("No fine-tuned weights. Using pretrained YOLO26m.")
        model = YOLO(YOLO26M_PRETRAINED)

    from datasets import load_dataset
    logger.info(f"Loading test split from {HF_DATASET_NAME}...")
    dataset = load_dataset(HF_DATASET_NAME, split="test")

    per_class_stats = {cls: {"tp": 0, "fp": 0, "fn": 0, "total_gt": 0} for cls in VEHICLE_CLASSES.values()}
    screenshot_indices = np.random.choice(len(dataset), size=min(20, len(dataset)), replace=False)

    logger.info(f"Evaluating on {len(dataset)} test images...")
    for idx in range(len(dataset)):
        sample = dataset[idx]
        img = np.array(sample["image"])
        width = sample["width"]
        height = sample["height"]
        objects = sample["objects"]

        gt_boxes = []
        for i in range(len(objects["id"])):
            bbox = objects["bbox"][i]
            category = objects["category"][i]
            x1, y1 = int(bbox[0]), int(bbox[1])
            x2, y2 = int(bbox[0] + bbox[2]), int(bbox[1] + bbox[3])
            # Dataset uses class IDs 1-12, convert to 0-11
            yolo_cls = category - 1
            if yolo_cls < 0:
                continue
            cls_name = VEHICLE_CLASSES.get(yolo_cls, f"class_{yolo_cls}")
            if cls_name not in per_class_stats:
                per_class_stats[cls_name] = {"tp": 0, "fp": 0, "fn": 0, "total_gt": 0}
            gt_boxes.append({"bbox": [x1, y1, x2, y2], "class_name": cls_name})
            per_class_stats[cls_name]["total_gt"] += 1

        results = model.predict(source=img, conf=CONFIDENCE_THRESHOLD, iou=IOU_THRESHOLD, verbose=False)
        pred_boxes = []
        if results and len(results) > 0 and results[0].boxes is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            confs = results[0].boxes.conf.cpu().numpy()
            cls_ids = results[0].boxes.cls.cpu().numpy().astype(int)
            for i in range(len(boxes)):
                cls_name = VEHICLE_CLASSES.get(int(cls_ids[i]), f"class_{cls_ids[i]}")
                pred_boxes.append({"bbox": boxes[i].tolist(), "confidence": float(confs[i]), "class_name": cls_name})

        matched_gt = set()
        for pred in pred_boxes:
            best_iou = 0
            best_gt_idx = -1
            for gi, gt in enumerate(gt_boxes):
                if gi in matched_gt or gt["class_name"] != pred["class_name"]:
                    continue
                iou = compute_iou(pred["bbox"], gt["bbox"])
                if iou > best_iou:
                    best_iou = iou
                    best_gt_idx = gi
            pred_cls = pred["class_name"]
            if pred_cls not in per_class_stats:
                per_class_stats[pred_cls] = {"tp": 0, "fp": 0, "fn": 0, "total_gt": 0}
            if best_iou >= 0.5 and best_gt_idx >= 0:
                per_class_stats[pred_cls]["tp"] += 1
                matched_gt.add(best_gt_idx)
            else:
                per_class_stats[pred_cls]["fp"] += 1

        for gi, gt in enumerate(gt_boxes):
            if gi not in matched_gt:
                per_class_stats[gt["class_name"]]["fn"] += 1

        if idx in screenshot_indices:
            annotated = draw_detections(img, gt_boxes, color_override=(128, 128, 128))
            annotated = draw_detections(annotated, pred_boxes)
            cv2.putText(annotated, "Gray=GT  Colored=Pred", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.imwrite(str(EVAL_SCREENSHOTS_DIR / f"eval_{idx:04d}.jpg"), cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR))

        if (idx + 1) % 100 == 0:
            logger.info(f"  Processed {idx + 1}/{len(dataset)} images...")


    # Compute metrics
    total_tp = sum(s["tp"] for s in per_class_stats.values())
    total_fp = sum(s["fp"] for s in per_class_stats.values())
    total_fn = sum(s["fn"] for s in per_class_stats.values())

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    accuracy = total_tp / (total_tp + total_fp + total_fn) if (total_tp + total_fp + total_fn) > 0 else 0

    per_class_metrics = {}
    for cls_name, stats in per_class_stats.items():
        tp, fp, fn = stats["tp"], stats["fp"], stats["fn"]
        p = tp / (tp + fp) if (tp + fp) > 0 else 0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0
        f = 2 * p * r / (p + r) if (p + r) > 0 else 0
        per_class_metrics[cls_name] = {"precision": p, "recall": r, "f1": f, "tp": tp, "fp": fp, "fn": fn, "total_gt": stats["total_gt"]}

    # Print report
    logger.info("\n" + "=" * 60)
    logger.info("EVALUATION RESULTS — YOLO26m on vehicles-q0x2v test set")
    logger.info("=" * 60)
    logger.info(f"Total test images: {len(dataset)}")
    logger.info(f"Overall Accuracy:  {accuracy:.4f} ({accuracy*100:.2f}%)")
    logger.info(f"Overall Precision: {precision:.4f} ({precision*100:.2f}%)")
    logger.info(f"Overall Recall:    {recall:.4f} ({recall*100:.2f}%)")
    logger.info(f"Overall F1 Score:  {f1:.4f} ({f1*100:.2f}%)")
    logger.info("-" * 60)
    logger.info("Per-Class Metrics:")
    logger.info(f"{'Class':<15} {'Precision':>10} {'Recall':>10} {'F1':>10} {'TP':>6} {'FP':>6} {'FN':>6}")
    for cls_name, m in sorted(per_class_metrics.items()):
        logger.info(f"{cls_name:<15} {m['precision']:>10.4f} {m['recall']:>10.4f} {m['f1']:>10.4f} {m['tp']:>6} {m['fp']:>6} {m['fn']:>6}")
    logger.info("=" * 60)

    import json
    metrics = {
        "overall": {"accuracy": accuracy, "precision": precision, "recall": recall, "f1": f1},
        "per_class": per_class_metrics,
        "total_images": len(dataset),
        "total_tp": total_tp, "total_fp": total_fp, "total_fn": total_fn,
    }
    (METRICS_DIR / "evaluation_results.json").write_text(json.dumps(metrics, indent=2))
    logger.info(f"Metrics saved to: {METRICS_DIR / 'evaluation_results.json'}")
    logger.info(f"Screenshots saved to: {EVAL_SCREENSHOTS_DIR}")
    return metrics


if __name__ == "__main__":
    evaluate_model()
