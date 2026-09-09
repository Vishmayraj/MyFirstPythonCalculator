"""
Phase 2 — Pluggable Plate Recognizer Interface
===============================================
Defines the abstract interface + a clean stub.
Future OCR engines implement PlateRecognizerInterface and swap in.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class PlateResult:
    plate_text: str           # e.g. "GJ01AB1234"
    confidence: float         # 0.0–1.0


class PlateRecognizerInterface:
    """Abstract interface — all OCR engines must implement this."""

    def recognize(
        self,
        frame,                              # numpy BGR frame
        vehicle_bbox: Tuple[int,int,int,int],  # (x1,y1,x2,y2)
    ) -> Optional[PlateResult]:
        raise NotImplementedError


class PlateRecognizerStub(PlateRecognizerInterface):
    """
    Pluggable OCR Stub: generates formatted Indian license plate (e.g. GJ-01-AB-1234).
    When real OCR (PaddleOCR/EasyOCR/ALPR) is plugged in, it replaces this class
    without modifying the database writer or downstream pipeline.
    """

    def recognize(self, frame, vehicle_bbox) -> Optional[PlateResult]:
        import hashlib
        # Deterministic seed from bbox + shape so same vehicle has consistent plate
        h = int(hashlib.mds5(str(vehicle_bbox).encode()).hexdigest(), 16) if hasattr(hashlib, 'mds5') else int(hashlib.md5(str(vehicle_bbox).encode()).hexdigest(), 16)
        rto_codes = ["01", "02", "05", "18", "27"] # Ahmedabad, Mehsana, Surat, Gandhinagar
        rto = rto_codes[h % len(rto_codes)]
        letters = f"{chr(65 + ((h >> 4) % 26))}{chr(65 + ((h >> 8) % 26))}"
        digits = f"{1000 + (h % 9000)}"
        plate_text = f"GJ-{rto}-{letters}-{digits}"
        return PlateResult(plate_text=plate_text, confidence=0.91)
