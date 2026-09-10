"""
Phase 2 — Detection Writer
===========================
Persists one detection row to PostgreSQL per confirmed vehicle sighting.
Also saves the crop image to static/crops/{detection_id}.jpg.
"""

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import cv2
import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from pipeline.plate.interface import PlateRecognizerInterface, PlateRecognizerStub
from pipeline.tracking.associator_interface import (
    TrackAssociatorInterface,
    TrackAssociatorStub,
)
from pipeline.tracking.frame_tracker import TrackedEvent

logger = logging.getLogger("sentinel.writer")
logger.setLevel(logging.INFO)

# Crops saved in model2_analytics/detection-image
_CROPS_CANDIDATES = [
    Path("/model2-analytics/detection-image"),
    Path("/app/model2_analytics/detection-image"),
    Path(__file__).resolve().parents[2] / "detection-image",
]
CROPS_BASE = next((p for p in _CROPS_CANDIDATES if p.is_dir()), _CROPS_CANDIDATES[0])
CROPS_BASE.mkdir(parents=True, exist_ok=True)


class DetectionWriter:
    """
    Writes exactly one detection record per TrackedEvent to PostgreSQL.
    Uses stub plate recognizer and stub track associator by default.
    Swap them out with real implementations without changing the writer.
    """

    def __init__(
        self,
        plate_recognizer: Optional[PlateRecognizerInterface] = None,
        track_associator: Optional[TrackAssociatorInterface] = None,
    ):
        self.plate_recognizer = plate_recognizer or PlateRecognizerStub()
        self.track_associator = track_associator or TrackAssociatorStub()

    def persist_sighting(
        self,
        db: Session,
        camera_uuid: uuid.UUID,
        event: TrackedEvent,
    ) -> Dict:
        """
        1. Run plate OCR (generates formatted Indian plate).
        2. Run track association (links global vehicle_track).
        3. Save crop image to disk.
        4. INSERT one row into detections table.

        Returns a dict with detection_id and other metadata for the WS broadcast.
        """
        detection_id = str(uuid.uuid4())

        # ── Plate recognition ──────────────────────────────────────
        plate_text: Optional[str] = None
        if event.crop is not None:
            plate_result = self.plate_recognizer.recognize(
                frame=event.crop,
                vehicle_bbox=event.bbox,
            )
            if plate_result:
                plate_text = plate_result.plate_text

        # ── Cross-camera track association ─────────────────────────
        vehicle_track_id: Optional[str] = None
        assoc = self.track_associator.associate(
            camera_id    = event.camera_id,
            timestamp    = event.timestamp,
            vehicle_crop = event.crop,
            plate        = plate_text,
        )
        if assoc:
            vehicle_track_id = str(assoc)
            try:
                db.execute(
                    text("""
                        INSERT INTO vehicle_tracks (id, plate_number, vehicle_type, first_seen, last_seen)
                        VALUES (:id, :plate, :vtype, :first_seen, :last_seen)
                        ON CONFLICT (id) DO UPDATE
                        SET last_seen = EXCLUDED.last_seen,
                            plate_number = COALESCE(vehicle_tracks.plate_number, EXCLUDED.plate_number)
                    """),
                    {
                        "id": vehicle_track_id,
                        "plate": plate_text,
                        "vtype": event.class_name,
                        "first_seen": event.timestamp,
                        "last_seen": event.timestamp,
                    }
                )
            except Exception as e:
                logger.warning(f"Could not upsert vehicle_tracks: {e}")

        # ── Save crop image ────────────────────────────────────────
        crop_path: Optional[str] = None
        if event.crop is not None and event.crop.size > 0:
            try:
                CROPS_BASE.mkdir(parents=True, exist_ok=True)
                crop_file = CROPS_BASE / f"{detection_id}.jpg"
                cv2.imwrite(str(crop_file), event.crop, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                crop_path = f"/detection-image/{detection_id}.jpg"
            except Exception as e:
                logger.warning(f"Could not save crop image: {e}")

        # ── DB INSERT ──────────────────────────────────────────────
        try:
            db.execute(
                text("""
                    INSERT INTO detections
                        (id, camera_id, "timestamp", detected_plate,
                         confidence, cropped_image_path, vehicle_track_id)
                    VALUES
                        (:id, :camera_id, :ts, :plate,
                         :conf, :crop_path, :track_id)
                """),
                {
                    "id":        detection_id,
                    "camera_id": str(camera_uuid),
                    "ts":        event.timestamp,
                    "plate":     plate_text,
                    "conf":      round(event.confidence, 4),
                    "crop_path": crop_path,
                    "track_id":  vehicle_track_id,
                },
            )
            db.commit()
            logger.info(
                f"[{event.camera_id}] Persisted detection {detection_id} "
                f"({event.class_name} conf={event.confidence:.2f})"
            )
        except Exception as e:
            db.rollback()
            logger.error(f"[{event.camera_id}] DB insert failed: {e}")

        return {
            "detection_id":    detection_id,
            "detected_plate":  plate_text,
            "vehicle_track_id": vehicle_track_id,
            "crop_path":       crop_path,
        }
