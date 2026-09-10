"""
PaddleOCR Engine for License Plate Recognition
===============================================
Superior OCR for license plates - handles distorted/angled text better than EasyOCR.
Falls back to EasyOCR if PaddleOCR unavailable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger("sentinel.paddle_ocr")
logger.setLevel(logging.INFO)


@dataclass
class OCRResult:
    plate_text: str      # normalized, uppercase, no spaces/hyphens
    confidence: float    # 0–1


# Indian plate validation
def _clean_text(raw: str) -> str:
    """Keep only alphanumerics, uppercase."""
    return "".join(c for c in raw.upper() if c.isalnum())


def _validate_plate(text: str) -> bool:
    """Basic sanity check for plate read."""
    if len(text) < 6 or len(text) > 12:
        return False
    digits = sum(1 for c in text if c.isdigit())
    letters = sum(1 for c in text if c.isalpha())
    return digits >= 3 and letters >= 2


class PaddleOCREngine:
    """
    PaddleOCR-based plate reader with fallback to EasyOCR.
    Better for distorted/angled plates than EasyOCR alone.
    """

    _instance = None

    @classmethod
    def get_instance(cls, use_gpu: bool = False) -> "PaddleOCREngine":
        if cls._instance is None:
            cls._instance = PaddleOCREngine(use_gpu=use_gpu)
        return cls._instance

    def __init__(self, use_gpu: bool = False):
        self.use_gpu = use_gpu  # Keep for compatibility but don't pass to PaddleOCR
        self._paddle_reader = None
        self._easy_reader = None
        self._use_paddle = True  # Try PaddleOCR first

    def _lazy_load_paddle(self):
        """Load PaddleOCR engine - disabled due to API issues in v3.7.0."""
        # PaddleOCR 3.7.0 has API compatibility issues - use EasyOCR instead
        logger.info("PaddleOCR 3.7.0 has API issues, using EasyOCR")
        self._use_paddle = False
        self._lazy_load_easy()

    def _lazy_load_easy(self):
        """Fallback to EasyOCR."""
        if self._easy_reader is None:
            try:
                import easyocr
                self._easy_reader = easyocr.Reader(["en"], gpu=self.use_gpu, verbose=False)
                logger.info("✓ EasyOCR initialized as fallback")
            except Exception as e:
                logger.error(f"✗ Both PaddleOCR and EasyOCR failed: {e}")

    @staticmethod
    def _preprocess(crop):
        """Enhanced preprocessing for license plates."""
        import cv2
        
        if len(crop.shape) == 3:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        else:
            gray = crop.copy()

        # Denoise
        denoised = cv2.fastNlMeansDenoising(gray, None, h=10)
        
        # Adaptive thresholding
        thresh = cv2.adaptiveThreshold(
            denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
            cv2.THRESH_BINARY, 11, 2
        )
        
        # CLAHE for contrast
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(thresh)
        
        # Upscale if small
        h, w = enhanced.shape
        if max(h, w) < 200:
            scale = 250.0 / max(h, w)
            enhanced = cv2.resize(
                enhanced, (int(w * scale), int(h * scale)),
                interpolation=cv2.INTER_CUBIC
            )
        return enhanced

    def read_plate(self, crop) -> Optional[OCRResult]:
        """Read plate text from a cropped image."""
        if crop is None or crop.size == 0:
            return None

        # Try PaddleOCR first (if available)
        if self._use_paddle:
            try:
                return self._read_with_paddle(crop)
            except Exception as e:
                logger.warning(f"PaddleOCR failed, falling back to EasyOCR: {e}")
                self._use_paddle = False
                self._lazy_load_easy()

        # Fallback to EasyOCR
        if self._easy_reader is None:
            self._lazy_load_easy()
        if self._easy_reader:
    def _read_with_paddle(self, crop) -> Optional[OCRResult]:
        """Read using PaddleOCR."""
        try:
            processed = self._preprocess(crop)
            
            # Try new API first (PaddleOCR 3.x+)
            try:
                result = self._paddle_reader.predict(processed)
                if result and len(result) > 0:
                    texts = []
                    confidences = []
                    for res in result:
                        if hasattr(res, 'rec_text') and hasattr(res, 'rec_score'):
                            texts.append(res.rec_text)
                            confidences.append(float(res.rec_score))
                    
                    if texts:
                        raw_text = " ".join(texts)
                        clean_text = _clean_text(raw_text)
                        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
                        
                        if len(clean_text) >= 6:
                            return OCRResult(plate_text=clean_text, confidence=avg_conf)
            except (AttributeError, TypeError):
                pass
            
            # Fallback to old API (PaddleOCR 2.x)
            try:
                result = self._paddle_reader.ocr(processed, cls=True)
                
                if not result or not result[0]:
                    return None
                
                texts = []
                confidences = []
                for line in result[0]:
                    if line and len(line) >= 2:
                        text = line[1][0]
                        conf = float(line[1][1])
                        texts.append(text)
                        confidences.append(conf)
                
                if not texts:
                    return None
                
                raw_text = " ".join(texts)
                clean_text = _clean_text(raw_text)
                avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
                
                if len(clean_text) < 6:
                    return None
                
                return OCRResult(plate_text=clean_text, confidence=avg_conf)
            except Exception:
                pass
            
            return None
            
        except Exception as e:
            logger.warning(f"PaddleOCR read failed: {e}")
            return None

    def _read_with_easy(self, crop) -> Optional[OCRResult]:
        """Fallback to EasyOCR."""
        try:
            processed = self._preprocess(crop)
            results = self._easy_reader.readtext(processed)
            
            if not results:
                return None

            texts = []
            confidences = []
            for detection in results:
                text = detection[1]
                conf = float(detection[2])
                texts.append(text)
                confidences.append(conf)

            raw_text = " ".join(texts)
            clean_text = _clean_text(raw_text)
            avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

            if len(clean_text) < 6:
                return None

            return OCRResult(plate_text=clean_text, confidence=avg_conf)

        except Exception as e:
            logger.warning(f"EasyOCR read failed: {e}")
            return None
            return self._read_with_easy(crop)

        return None