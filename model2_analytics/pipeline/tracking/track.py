"""
Model 2 — ByteTrack Multi-Object Vehicle Tracker
Assigns persistent track IDs to detected vehicles across frames.
"""

import logging
import numpy as np
from typing import List, Dict, Any, Optional, Tuple

from pipeline.config import TRACK_BUFFER, MAX_TIME_LOST

logger = logging.getLogger("sentinel.tracking")


class Track:
    """Single vehicle track state."""

    _next_id = 0

    def __init__(self, detection: Dict[str, Any]):
        Track._next_id += 1
        self.track_id = Track._next_id
        self.bbox = detection["bbox"]
        self.class_id = detection["class_id"]
        self.class_name = detection["class_name"]
        self.confidence = detection["confidence"]
        self.hit_streak = 1
        self.time_since_update = 0
        self.history: List[List[float]] = [detection["bbox"]]
        self.color = self._generate_color()

    def _generate_color(self) -> Tuple[int, int, int]:
        """Generate a unique color per track ID for visualization."""
        rng = np.random.default_rng(self.track_id * 42)
        return tuple(int(c) for c in rng.integers(100, 255, size=3))

    def update(self, detection: Dict[str, Any]):
        """Update track with new detection."""
        self.bbox = detection["bbox"]
        self.confidence = detection["confidence"]
        self.hit_streak += 1
        self.time_since_update = 0
        self.history.append(detection["bbox"])
        if len(self.history) > TRACK_BUFFER:
            self.history.pop(0)

    def mark_missed(self):
        """Mark track as not detected in current frame."""
        self.time_since_update += 1
        self.hit_streak = 0

    def is_confirmed(self) -> bool:
        return self.hit_streak >= 3

    def is_deleted(self) -> bool:
        return self.time_since_update > MAX_TIME_LOST
