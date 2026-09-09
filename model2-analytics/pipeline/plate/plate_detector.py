"""
Phase 3 — License Plate Detector (YOLOv8)
=========================================
Downloads license_plate_detector.pt (from Muhammad-Zeerak-Khan's
Automatic-License-Plate-Recognition-using-YOLOv8) if not present.

Pipeline:
  raw frame → YOLO plate detect → (x1,y1,x2,y2,confidence) boxes
"""

from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger("sentinel.plate_detector")
logger.setLevel(logging.INFO)

def _workspace_root() -> Path:
    """Walk up to the workspace root robustly (path depth differs between
    local checkout and Docker: /model2-analytics/pipeline/plate/...)."""
    p = Path(__file__).resolve()
    for ancestor in p.parents:
        if ancestor.name in ("HEHE", "model2-analytics") or ancestor == Path(ancestor.anchor):
            return ancestor.parent if ancestor.name == "model2-analytics" else ancestor
    return p.parents[-1]

_WORKSPACE = _workspace_root()
_WEIGHTS = Path(__file__).resolve().parent / "license_plate_detector.pt"

# Repo 2's trained YOLOv8 plate detector
PLATE_WEIGHTS_URL = (
    "https://raw.githubusercontent.com/Muhammad-Zeerak-Khan/"
    "Automatic-License-Plate-Recognition-using-YOLOv8/main/"
    "license_plate_detector.pt"
)

# Fallback location in shared /weights dir (same repo copy)
_WEIGHTS_SHARED = _WORKSPACE / "weights" / "license_plate_detector.pt"


@dataclass
class PlateDetection:
    """One license plate detected in a frame."""
    bbox:        Tuple[int, int, int, int]   # (x1, y1, x2, y2)
    confidence:  float


class PlateDetector:
    """
    YOLOv8 license-plate detector.
    Lazy-loads the model on first call; auto-downloads weights if missing.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.20,  # LOWERED from 0.40 to detect more plates
        imgsz: int = 640,
        device: Optional[str] = None,
        model_path: Optional[str] = None,  # NEW: custom model path
    ):
        self.confidence_threshold = confidence_threshold
        self.imgsz                = imgsz
        self.device               = device
        self._model               = None
        self._custom_model_path   = model_path  # NEW

    # ── Weight resolution ──────────────────────────────────────────
    def _resolve_weights(self) -> Optional[str]:
        """Return a path to plate detector weights, downloading if needed."""
        # If custom model path provided, use it first
        if self._custom_model_path:
            p = Path(self._custom_model_path)
            if p.exists() and p.stat().st_size > 100_000:
                logger.info(f"Using custom plate model: {p}")
                return str(p)
            else:
                logger.warning(f"Custom model not found: {p}, falling back to default")

        candidates = [
            _WEIGHTS,
            _WEIGHTS_SHARED,
            _WORKSPACE / "weights" / "license_plate_detector.pt",
        ]
        for p in candidates:
            if p.exists() and p.stat().st_size > 100_000:   # 6.2 MB real file
                logger.info(f"Plate weights found: {p}")
                return str(p)

        # Download to the pipeline/plate directory
        logger.info("Plate weights missing — downloading from GitHub…")
        _WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(PLATE_WEIGHTS_URL, str(_WEIGHTS))
            if _WEIGHTS.exists() and _WEIGHTS.stat().st_size > 100_000:
                logger.info(f"Downloaded plate weights → {_WEIGHTS}")
                return str(_WEIGHTS)
        except Exception as e:
            logger.error(f"Plate weight download failed: {e}")
        return None

    # ── Model load ────────────────────────────────────────────────
    def _load(self) -> None:
        if self._model is not None:
            return
        weights = self._resolve_weights()
        if weights is None:
            raise RuntimeError(
                "license_plate_detector.pt unavailable — "
                "cannot run plate detection."
            )
        from ultralytics import YOLO
        self._model = YOLO(weights)
        logger.info(f"Loaded plate detector: {Path(weights).name}")

    # ── Inference ─────────────────────────────────────────────────
    def detect(self, frame: np.ndarray) -> List[PlateDetection]:
        """
        Run plate detection on a full BGR frame.
        Returns up to N plate boxes; never raises.
        """
        if frame is None or frame.size == 0:
            return []

        try:
            self._load()
        except Exception as e:
            logger.error(f"Plate model load failed: {e}")
            return []

        h, w = frame.shape[:2]
        try:
            results = self._model.predict(
                source=frame,
                imgsz=self.imgsz,
                conf=self.confidence_threshold,
                iou=0.45,
                device=self.device,
                verbose=False,
            )
        except Exception as e:
            logger.warning(f"Plate predict() failed: {e}")
            return []

        plates: List[PlateDetection] = []
        if not results:
            return plates

        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return plates

        for box in boxes:
            conf = float(box.conf[0].item())
            xyxy = box.xyxy[0].tolist()
            x1 = max(0, int(xyxy[0]));  y1 = max(0, int(xyxy[1]))
            x2 = min(w, int(xyxy[2]));  y2 = min(h, int(xyxy[3]))

            # Plates should be wider than tall and reasonably sized
            if (x2 - x1) < 12 or (y2 - y1) < 8:
                continue

            plates.append(PlateDetection(
                bbox=(x1, y1, x2, y2),
                confidence=conf,
            ))

        return plates