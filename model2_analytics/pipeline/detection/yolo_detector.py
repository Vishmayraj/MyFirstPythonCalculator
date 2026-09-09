"""
Model 2 — YOLO26m Vehicle Detector
Loads the fine-tuned YOLO26m model and runs inference on frames.
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

import numpy as np

from pipeline.config import (
    YOLO26M_PRETRAINED,
    FINETUNED_WEIGHTS,
    CONFIDENCE_THRESHOLD,
    IOU_THRESHOLD,
    IMG_SIZE,
    MAX_DETECTIONS,
    VEHICLE_CLASSES,
)

logger = logging.getLogger("sentinel.detection")


class VehicleDetector:
    """YOLO26m-based vehicle detector fine-tuned on vehicles-q0x2v dataset."""

    def __init__(
        self,
        weights_path: Optional[str] = None,
        confidence: float = CONFIDENCE_THRESHOLD,
        iou: float = IOU_THRESHOLD,
        img_size: int = IMG_SIZE,
        device: str = "auto",
    ):
        """
        Initialize the YOLO26m vehicle detector.

        Args:
            weights_path: Path to fine-tuned .pt weights. If None, uses pretrained yolo26m.pt.
            confidence: Minimum confidence threshold for detections.
            iou: IoU threshold for NMS.
            img_size: Inference image size.
            device: 'cpu', 'cuda', or 'auto'.
        """
        # Deferred import — ultralytics is heavy, only load when detector is instantiated
        from ultralytics import YOLO

        self.confidence = confidence
        self.iou = iou
        self.img_size = img_size

        # Resolve device
        if device == "auto":
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"

        self.device = device
        logger.info(f"YOLO26m detector initializing on device: {device}")

        # Load model — fine-tuned weights if available, else pretrained
        if weights_path and Path(weights_path).exists():
            logger.info(f"Loading fine-tuned weights: {weights_path}")
            self.model = YOLO(str(weights_path))
        elif FINETUNED_WEIGHTS.exists():
            logger.info(f"Loading fine-tuned weights: {FINETUNED_WEIGHTS}")
            self.model = YOLO(str(FINETUNED_WEIGHTS))
        else:
            logger.info("No fine-tuned weights found. Loading pretrained yolo26m.pt")
            self.model = YOLO(YOLO26M_PRETRAINED)

        self.model.to(device)
        logger.info("YOLO26m detector ready.")

    def detect(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """
        Run vehicle detection on a single frame.

        Args:
            frame: BGR image (numpy array, OpenCV format).

        Returns:
            List of detection dicts:
            {
                "bbox": [x1, y1, x2, y2],
                "confidence": float,
                "class_id": int,
                "class_name": str,
                "area": float,
            }
        """
        results = self.model.predict(
            source=frame,
            conf=self.confidence,
            iou=self.iou,
            imgsz=self.img_size,
            max_det=MAX_DETECTIONS,
            device=self.device,
            verbose=False,
        )

        detections = []

        if results and len(results) > 0:
            result = results[0]
            if result.boxes is not None and len(result.boxes) > 0:
                boxes = result.boxes
                xyxy = boxes.xyxy.cpu().numpy()
                confs = boxes.conf.cpu().numpy()
                cls_ids = boxes.cls.cpu().numpy().astype(int)

                for i in range(len(xyxy)):
                    x1, y1, x2, y2 = xyxy[i]
                    cls_id = int(cls_ids[i])
                    conf = float(confs[i])

                    # Map class ID to name
                    class_name = VEHICLE_CLASSES.get(cls_id, f"class_{cls_id}")

                    detections.append({
                        "bbox": [float(x1), float(y1), float(x2), float(y2)],
                        "confidence": conf,
                        "class_id": cls_id,
                        "class_name": class_name,
                        "area": float((x2 - x1) * (y2 - y1)),
                    })

        return detections

    def detect_batch(self, frames: List[np.ndarray]) -> List[List[Dict[str, Any]]]:
        """Run detection on a batch of frames."""
        return [self.detect(frame) for frame in frames]

    def get_model_info(self) -> Dict[str, Any]:
        """Return model information."""
        return {
            "model": "YOLO26m",
            "device": self.device,
            "confidence_threshold": self.confidence,
            "iou_threshold": self.iou,
            "img_size": self.img_size,
            "num_classes": len(VEHICLE_CLASSES),
            "classes": VEHICLE_CLASSES,
        }
