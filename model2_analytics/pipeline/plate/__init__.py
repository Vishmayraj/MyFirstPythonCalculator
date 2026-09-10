"""Plate detection subpackage — YOLOv8 detector + full ANPR pipeline."""
from pipeline.plate.plate_detector import PlateDetector, PlateDetection
from pipeline.plate.anpr_pipeline import ANPRPipeline
from pipeline.plate.interface import PlateResult, PlateRecognizerInterface

__all__ = [
    "PlateDetector",
    "PlateDetection",
    "ANPRPipeline",
    "PlateResult",
    "PlateRecognizerInterface",
]
