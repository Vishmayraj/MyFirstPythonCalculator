"""
Phase 3 — OCR Engine (EasyOCR)
================================
Reads license-plate text from a cropped plate image.
Includes:
  * CLAHE + threshold preprocessing (better small/blurry text)
  * EasyOCR (en) inference
  * Indian plate format validation
  * aggressive character mapping (O→0, I→1, …) based on position
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger("sentinel.ocr")
logger.setLevel(logging.INFO)


@dataclass
class OCRResult:
    plate_text: str      # normalized, uppercase, no spaces/hyphens
    confidence: float    # 0–1


# ── Character correction maps (Indian plate context) ──────────────
# Letters confused with digits (used in letter positions)
_LC = {"O": "0", "D": "0", "Q": "0", "I": "1", "J": "1", "Z": "2",
       "A": "4", "S": "5", "G": "6", "T": "7", "B": "8"}
# Digits confused with letters (used in letter positions)
_CL = {"0": "O", "1": "I", "2": "Z", "3": "J", "4": "A",
       "5": "S", "6": "G", "7": "T", "8": "B"}
def _clean_text(raw: str) -> str:
    """Keep only alphanumerics, uppercase."""
    return "".join(c for c in raw.upper() if c in "0123456789ABCDEFGHJKLMNPRSTUVWXYZ")


def _format_plate(text: str) -> str:
    """
    Normalize OCR output to standard Indian plate shape.

    Handles two formats:
      * Classic:  GJ 01 AB 1234  →  GJ01AB1234 (10 chars)
      * Bharat:   22 BH 1234 AA   →  22BH1234AA  (10 chars)

    Returns empty string if the text is unusable, keeps partial reads.
    """
    t = _clean_text(text)
    if len(t) < 8:
        return ""

    out = list(t)

    # decide classic vs bharat
    first_two_digits = out[0].isdigit() and out[1].isdigit()

    if first_two_digits and len(out) == 10:
        # Bharat: DD LL DDDD LL
        pos = {
            0: _LC, 1: _LC, 2: _CL, 3: _CL,
            4: _LC, 5: _LC, 6: _LC, 7: _LC,
            8: _CL, 9: _CL,
        }
    else:
        # Classic: LL DD LL DDDD
        pos = {
            0: _CL, 1: _CL,          # letters
            2: _LC, 3: _LC,          # digits
            4: _CL, 5: _CL,          # letters
            6: _LC, 7: _LC, 8: _LC, 9: _LC,   # digits
        }

    for i, ch in enumerate(out):
        if i >= len(pos):
            break
        if ch in pos[i]:
            out[i] = pos[i][ch]

    return "".join(out)


def _validate_plate(text: str) -> bool:
    """
    Basic sanity check for an Indian plate read.
    Accepts classic or Bharat style, length >= 9.
    """
    if len(text) < 9 or len(text) > 10:
        return False
    digits = sum(1 for c in text if c.isdigit())
    letters = sum(1 for c in text if c.isalpha())
    return digits >= 4 and letters >= 3


class EasyOCREngine:
    """EasyOCR-based plate reader with Indian formatting."""

    _instance = None

    @classmethod
    def get_instance(cls, gpu: bool = False) -> "EasyOCREngine":
        if cls._instance is None:
            cls._instance = EasyOCREngine(gpu=gpu)
        return cls._instance

    def __init__(self, gpu: bool = False):
        self.gpu = gpu
        self._reader = None

    def _lazy_load(self):
        if self._reader is None:
            import easyocr
            self._reader = easyocr.Reader(["en"], gpu=self.gpu, verbose=False)
            logger.info("EasyOCR reader initialised.")

    # ── Preprocessing ─────────────────────────────────────────────
    @staticmethod
    def _preprocess(crop):
        """CLAHE + upscale for small/blurry plates."""
        import cv2
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        h, w = gray.shape
        if max(h, w) < 200:
            scale = 200.0 / max(h, w)
            gray = cv2.resize(gray, (int(w * scale), int(h * scale)),
                              interpolation=cv2.INTER_CUBIC)
        return gray

    # ── Main entry ────────────────────────────────────────────────
    def read_plate(self, crop) -> Optional[OCRResult]:
        """
        Read plate text from a crop image (BGR ndarray).
        Returns OCRResult (formatted text + confidence) or None.
        """
        if crop is None or crop.size == 0:
            return None

        try:
            self._lazy_load()
        except Exception as e:
            logger.error(f"EasyOCR init failed: {e}")
            return None

        try:
            gray = self._preprocess(crop)
        except Exception:
            gray = crop

        try:
            detections = self._reader.readtext(gray)
        except Exception as e:
            logger.warning(f"readtext failed: {e}")
            return None

        for _, text, conf in detections:
            formatted = _format_plate(text)
            if not formatted:
                continue
            if not _validate_plate(formatted):
                continue
            return OCRResult(plate_text=formatted, confidence=float(conf))

        # Fallback: raw text with weak constraints
        for _, text, conf in detections:
            ct = _clean_text(text)
            if len(ct) >= 9 and conf > 0.5:
                formatted = _format_plate(ct)
                if formatted:
                    return OCRResult(plate_text=formatted, confidence=float(conf))
        return None