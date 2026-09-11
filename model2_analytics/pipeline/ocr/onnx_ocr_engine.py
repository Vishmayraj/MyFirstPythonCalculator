"""
OnnxOCR Engine (uses PaddleOCR PP-OCRv5 models via ONNXRuntime)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger("sentinel.onnx_ocr")
logger.setLevel(logging.INFO)


@dataclass
class OCRResult:
    plate_text: str
    confidence: float


INDIAN_PLATE_RE = re.compile(r"[A-Z]{2}\d{2}[A-Z]{1,2}\d{3,4}")
NON_PLATE_WORDS = ["IND", "INIDA", "INDIA", "SUZUKI", "MARUTI", "HYUNDAI", "TATA", "MAHINDRA",
                  "TOYOTA", "HONDA", "FORD", "BMW", "POLICE", "NORTH", "EAST", "WEST", "SOUTH",
                  "GOVT", "GOVERNMENT"]


def extract_indian_plate(text: str) -> str:
    """Extract Indian plate-formatted text from OCR output."""
    text = text.upper()
    text = "".join(c for c in text if c.isalnum())
    for word in NON_PLATE_WORDS:
        text = text.replace(word, "")
    m = INDIAN_PLATE_RE.search(text)
    if m:
        return m.group(0)
    return text


class OnnxOCREngine:
    """
    PaddleOCR PP-OCRv5 models running via ONNXRuntime (OnnxOCR).
    No PaddlePaddle dependency - works on CPU without oneDNN bugs.
    """

    _instance = None

    @classmethod
    def get_instance(cls, use_gpu: bool = False) -> "OnnxOCREngine":
        if cls._instance is None:
            cls._instance = OnnxOCREngine(use_gpu=use_gpu)
        return cls._instance

    def __init__(self, use_gpu: bool = False):
        self.use_gpu = use_gpu
        self._model = None

    def _lazy_load(self):
        if self._model is None:
            try:
                from onnxocr.onnx_paddleocr import ONNXPaddleOcr
                self._model = ONNXPaddleOcr(use_angle_cls=True, use_gpu=self.use_gpu)
                logger.info("OnnxOCR (PP-OCRv5) initialized")
            except Exception as e:
                logger.error(f"OnnxOCR init failed: {e}")
                raise

    @staticmethod
    def _preprocess(crop):
        import cv2
        h, w = crop.shape[:2]
        if h < 48:
            scale = 48.0 / h
            crop = cv2.resize(crop, (int(w * scale), 48), interpolation=cv2.INTER_CUBIC)
        return crop

    def read_plate(self, crop) -> Optional[OCRResult]:
        """Read plate text from a cropped image."""
        self._lazy_load()
        try:
            processed = self._preprocess(crop)
            result = self._model.ocr(processed)
            if not result or not result[0]:
                return None

            texts = [line[1][0] for line in result[0]]
            confs = [float(line[1][1]) for line in result[0]]
            raw = "".join(texts).upper()
            raw = "".join(c for c in raw if c.isalnum())
            clean = extract_indian_plate(raw)

            if len(clean) < 6:
                return None

            return OCRResult(plate_text=clean, confidence=sum(confs) / len(confs))
        except Exception as e:
            logger.warning(f"OnnxOCR read failed: {e}")
            return None

    # Alias for compatibility
    read = read_plate
    recognize = read_plate