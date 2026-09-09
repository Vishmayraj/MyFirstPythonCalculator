"""
Phase 1 — Vehicle Detector (Indian Traffic Edition)
====================================================
Uses indian_traffic_yolov8.pt — fine-tuned for Indian roads.
Classes: Auto-Rickshaw (CNG/Tuk-Tuk), Motorcycle/Scooter, Car, Bus, Truck, Mini-Truck, Bicycle.

Auto-downloads weights from HuggingFace if not found locally.
Falls back to standard yolov8n.pt (COCO) if download fails.
"""

import logging
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("sentinel.detector")
logger.setLevel(logging.INFO)

# ── Weights path (inside Docker: /app/model2_analytics/pipeline/detection/) ────
_HERE = Path(__file__).resolve().parent
INDIAN_WEIGHTS = _HERE / "indian_traffic_yolov8.pt"
FALLBACK_MODEL = "yolov8n.pt"   # standard COCO — auto-downloaded by ultralytics

# HuggingFace mirror for Indian traffic weights
HF_URL = "https://huggingface.co/abrarhameem398/traffice-detection-best/resolve/main/best.pt"

# ── Indian traffic class normalization ─────────────────────────────
#    Maps raw model class names (lower) → clean display name
INDIAN_CLASS_MAP: Dict[str, str] = {
    "cng":        "Auto Rickshaw",
    "rickshaw":   "Auto Rickshaw",
    "auto":       "Auto Rickshaw",
    "bike":       "Motorcycle",
    "motorcycle": "Motorcycle",
    "scooter":    "Motorcycle",
    "car":        "Car",
    "bus":        "Bus",
    "truck":      "Truck",
    "mini-truck": "Mini-Truck",
    "minitruck":  "Mini-Truck",
    "van":        "Van",
    "cycle":      "Bicycle",
    "bicycle":    "Bicycle",
}

# ── COCO fallback class IDs (used when Indian model unavailable) ───
COCO_VEHICLE: Dict[int, str] = {
    2: "Car",
    3: "Motorcycle",
    5: "Bus",
    7: "Truck",
}


@dataclass
class RawDetection:
    """Single vehicle detection from one frame."""
    bbox:       Tuple[int, int, int, int]   # (x1, y1, x2, y2) absolute pixels
    confidence: float
    class_id:   int
    class_name: str
    crop:       Optional[np.ndarray] = None  # cropped BGR region


class VehicleDetector:
    """
    Indian Traffic YOLO detector.
    Uses indian_traffic_yolov8.pt when available; falls back to yolov8n.pt.
    Lazy-loads on first detect() call.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.25,  # LOWERED from 0.40 to detect more vehicles
        iou_threshold: float = 0.45,
        device: Optional[str] = None,
    ):
        self.confidence_threshold = confidence_threshold
        self.iou_threshold        = iou_threshold
        self.device               = device

        self._model        = None
        self._is_indian    = False     # True when Indian model loaded
        self._target_cls:  Optional[List[int]] = None   # None = all classes

    # ── Weight resolution ──────────────────────────────────────────
    def _resolve_weights(self) -> str:
        if INDIAN_WEIGHTS.exists():
            logger.info(f"Using Indian traffic weights: {INDIAN_WEIGHTS}")
            return str(INDIAN_WEIGHTS)

        # Try downloading via huggingface_hub or urllib
        logger.info("indian_traffic_yolov8.pt not found — downloading from HuggingFace…")
        try:
            from huggingface_hub import hf_hub_download
            import shutil
            downloaded = hf_hub_download(repo_id="abrarhameem398/traffice-detection-best", filename="best.pt")
            shutil.copy2(downloaded, str(INDIAN_WEIGHTS))
            logger.info(f"Downloaded Indian traffic weights via HF Hub → {INDIAN_WEIGHTS}")
            return str(INDIAN_WEIGHTS)
        except Exception as hf_err:
            logger.warning(f"HF Hub download failed ({hf_err}), trying direct URL…")
            try:
                urllib.request.urlretrieve(HF_URL, str(INDIAN_WEIGHTS))
                logger.info(f"Downloaded Indian traffic weights → {INDIAN_WEIGHTS}")
                return str(INDIAN_WEIGHTS)
            except Exception as e:
                logger.warning(f"Download failed ({e}). Falling back to {FALLBACK_MODEL}.")
                return FALLBACK_MODEL

    # ── Model load (lazy) ──────────────────────────────────────────
    def _load(self):
        if self._model is not None:
            return
        from ultralytics import YOLO

        weights = self._resolve_weights()
        logger.info(f"Loading YOLO model: {weights}")
        self._model = YOLO(weights)

        # Inspect class names
        names = self._model.names            # {id: name}
        lower_names = {k: str(v).lower() for k, v in names.items()}
        logger.info(f"Model classes: {names}")

        # Detect whether this is the Indian traffic model
        indian_keys = {"cng", "rickshaw", "auto", "mini-truck", "minitruck"}
        if any(n in indian_keys for n in lower_names.values()):
            self._is_indian   = True
            # Only detect vehicles (filter out people/pedestrians)
            self._target_cls  = [
                idx for idx, name in names.items()
                if str(name).lower() not in {"people", "person", "human"}
            ]
            logger.info(f"✅ Indian Traffic model active — vehicle classes: {self._target_cls}")
        else:
            self._is_indian   = False
            self._target_cls  = sorted(COCO_VEHICLE.keys())
            logger.info(f"⚠️  COCO fallback model active — filtering classes: {self._target_cls}")

    # ── Inference ──────────────────────────────────────────────────
    def detect(self, frame: np.ndarray, pts_ms: float = 0.0) -> List[RawDetection]:
        """
        Run vehicle detection on a single BGR frame.
        Always returns a list — never raises.
        """
        if frame is None or frame.size == 0:
            return []

        try:
            self._load()
        except Exception as e:
            logger.error(f"Model load failed: {e}")
            return []

        try:
            results = self._model.predict(
                source=frame,
                imgsz=1280,  # INCREASED from 480 to preserve more detail (2.7x larger objects)
                conf=self.confidence_threshold,
                iou=self.iou_threshold,
                classes=self._target_cls,   # None = all (Indian model); list = COCO subset
                device=self.device,
                verbose=False,
            )
        except Exception as e:
            logger.warning(f"predict() failed: {e}")
            return []

        detections: List[RawDetection] = []
        if not results:
            return detections

        h, w = frame.shape[:2]
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return detections

        for box in boxes:
            cls_id = int(box.cls[0].item())
            conf   = float(box.conf[0].item())
            xyxy   = box.xyxy[0].tolist()

            x1 = max(0, int(xyxy[0]));  y1 = max(0, int(xyxy[1]))
            x2 = min(w, int(xyxy[2]));  y2 = min(h, int(xyxy[3]))

            if (x2 - x1) < 20 or (y2 - y1) < 20:   # skip tiny artifacts
                continue

            crop = frame[y1:y2, x1:x2].copy()

            # ── Class name resolution ──────────────────────────────
            if self._is_indian:
                raw = str(self._model.names.get(cls_id, "vehicle")).lower()
                class_name = INDIAN_CLASS_MAP.get(raw, raw.title())
            else:
                class_name = COCO_VEHICLE.get(cls_id, "Vehicle")

            detections.append(RawDetection(
                bbox=(x1, y1, x2, y2),
                confidence=conf,
                class_id=cls_id,
                class_name=class_name,
                crop=crop,
            ))

        return detections
