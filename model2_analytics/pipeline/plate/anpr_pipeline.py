"""
Phase 3 — ANPR Pipeline (Plate Detector + OCR)
===============================================
Replaces the PlateRecognizerStub with a real ANPR implementation.
Implements PlateRecognizerInterface so DetectionWriter needs no changes.

Pipeline:
    frame + vehicle_bbox
      → PLATE_DETECTOR (YOLOv8) finds all plates in frame
      → find plate whose bbox is INSIDE the vehicle bbox
      → crop plate region
      → EasyOCR reads plate text
      → returns PlateResult(plate_text, confidence)
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol, Tuple

import numpy as np

from pipeline.plate.interface import PlateRecognizerInterface, PlateResult
from pipeline.plate.plate_detector import PlateDetector
from pipeline.ocr.paddle_ocr_engine import PaddleOCREngine  # Using enhanced PaddleOCR
from pipeline.ocr.ocr_engine import EasyOCREngine, OCRResult  # Fallback + type

logger = logging.getLogger("sentinel.anpr")
logger.setLevel(logging.INFO)


class OCREngineProtocol(Protocol):
    """Protocol for OCR engines that can read plates."""
    def read_plate(self, crop) -> Optional[OCRResult]: ...


def _iou(a, b) -> float:
    """Intersection-over-union between two boxes (x1,y1,x2,y2)."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2); iy2 = min(ay2, by1)
    iw = max(0, ix2 - ix1); ih = max(0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class ANPRPipeline(PlateRecognizerInterface):
    """
    Real ANPR engine — YOLOv8 plate detection + EasyOCR.
    Can be constructed standalone OR shared (one instance per process).
    """

    # Shared instance so DetectionWriter & demos reuse loaded models
    _instance = None

    @classmethod
    def get_instance(cls) -> "ANPRPipeline":
        if cls._instance is None:
            cls._instance = ANPRPipeline()
        return cls._instance

    def __init__(
        self,
        plate_detector: Optional[PlateDetector] = None,
        ocr_engine: Optional[OCREngineProtocol] = None,
    # ── Public API (implements PlateRecognizerInterface) ───────
    def recognize(
        self,
        frame: np.ndarray,
        vehicle_bbox: Tuple[int, int, int, int],
    ) -> Optional[PlateResult]:
        """
        Find + read the plate of the vehicle at vehicle_bbox.
        
        NEW STRATEGY: Try multiple regions to find plates regardless of vehicle orientation:
        - Bottom 60% (rear plates, typical case)
        - Bottom 40% (compact cars, lower plates)
        - Full vehicle bbox (side views, front views)
        Pick the best detection across all regions.

        Args:
            frame:         full BGR frame
            vehicle_bbox:  vehicle box (x1,y1,x2,y2)

        Returns PlateResult(plate_text, confidence) or None.
        """
        if frame is None or frame.size == 0:
            return None

        try:
            import cv2
        except ImportError:
            logger.error("cv2 import failed")
            return None

        x1, y1, x2, y2 = vehicle_bbox
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        # Define regions to search for plates
        regions = [
            {'name': 'bottom_60', 'y1': int(y1 + (y2-y1)*0.4), 'y2': y2, 'x1': x1, 'x2': x2},
            {'name': 'bottom_40', 'y1': int(y1 + (y2-y1)*0.6), 'y2': y2, 'x1': x1, 'x2': x2},
            {'name': 'full_bbox',  'y1': y1, 'y2': y2, 'x1': x1, 'x2': x2},
        ]

        all_results = []
        
        for region in regions:
            ry1, ry2 = region['y1'], region['y2']
            rx1, rx2 = region['x1'], region['x2']
            
            if ry2 <= ry1 or rx2 <= rx1:
                continue
                
            pcrop = frame[ry1:ry2, rx1:rx2]
            if pcrop.size == 0:
                continue

            # Upscale 4x for better detection + OCR
            try:
                big = cv2.resize(pcrop, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_CUBIC)
            except Exception:
                big = pcrop

            # Run plate detection on upscaled crop
            plates = self.detector.detect(big)
            if not plates:
                continue

            # Process each detected plate
            for plate in plates:
                bx1, by1, bx2, by2 = plate.bbox
                bh, bw = big.shape[:2]
                
                # Validate aspect ratio (plates are typically 2:1 to 5:1 width:height)
                plate_w = bx2 - bx1
                plate_h = by2 - by1
                if plate_h > 0:
                    aspect_ratio = plate_w / plate_h
                    if not (1.5 <= aspect_ratio <= 6.0):
                        continue  # Skip non-plate-like shapes
                
                # Crop plate with padding
                pad_x = max(2, int(plate_w * 0.10))
                pad_y = max(2, int(plate_h * 0.10))
                crop = big[
                    max(0, by1 - pad_y):min(bh, by2 + pad_y),
                    max(0, bx1 - pad_x):min(bw, bx2 + pad_x),
                ]
                
                if crop.size == 0:
                    continue

                # OCR
                ocr_result = self.ocr.read_plate(crop)
                if ocr_result and ocr_result.plate_text:
                    # Combined score: plate detection confidence * OCR confidence
                    combined_conf = plate.confidence * ocr_result.confidence
                    all_results.append({
                        'plate_text': ocr_result.plate_text,
                        'confidence': ocr_result.confidence,
                        'combined_score': combined_conf,
                        'region': region['name'],
                        'plate_conf': plate.confidence,
                    })

        # Pick best result across all regions
        if not all_results:
            return None

        best = max(all_results, key=lambda r: r['combined_score'])
        
        logger.info(f"Best plate from region '{best['region']}': {best['plate_text']} "
                   f"(plate_conf={best['plate_conf']:.2f}, ocr_conf={best['confidence']:.2f})")

        return PlateResult(
            plate_text=best['plate_text'],
            confidence=best['confidence'],
        )

    # ── Batch mode for video processing ────────────────────────
    def process_frame(
        self,
        frame: np.ndarray,
        vehicle_bboxes,
    ) -> dict:
        """
        Convenience for video processing: for EVERY vehicle bbox run ANPR and
        return a mapping bbox→PlateResult|None.

        vehicle_bboxes: list of (x1,y1,x2,y2)
        Uses the same crop+upscale strategy as recognize().
        """
        out = {}
        for vb in vehicle_bboxes:
            res = self.recognize(frame, vb)
            out[tuple(vb)] = res
        return out
        conf_threshold: float = 0.20,  # LOWERED from 0.40
    ):
        self.detector = plate_detector or PlateDetector(
            confidence_threshold=conf_threshold
        )
        # Use PaddleOCR by default (with EasyOCR fallback built-in)
        self.ocr = ocr_engine or PaddleOCREngine.get_instance()