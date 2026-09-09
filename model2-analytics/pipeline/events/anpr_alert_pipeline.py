"""
ANPR Alert Pipeline
===================
Connects OCR plate output to watchlist matching to alert generation.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from pipeline.events.watchlist_matcher import WatchlistMatch, normalize_plate
from pipeline.events.alert_service import AlertService

logger = logging.getLogger("sentinel.anpr_pipeline")
logger.setLevel(logging.INFO)


@dataclass
class AnprResult:
    """Result of processing a plate through the ANPR alert pipeline."""
    plate_text: str
    detection_id: Optional[str] = None
    alert_id: Optional[str] = None
    watchlist_match: Optional[WatchlistMatch] = None
    is_watchlisted: bool = False


class AnprAlertPipeline:
    """End-to-end ANPR alert pipeline."""

    def __init__(
        self,
        db_session_factory: Callable[[], Session],
        ws_callback: Optional[Callable[[dict], None]] = None,
    ):
        self.db_session_factory = db_session_factory
        self.alert_service = AlertService(on_detection_event=ws_callback)

    def process_plate(
        self,
        plate_text: str,
        camera_id: str,
        camera_name: str = "Unknown",
        vehicle_class: Optional[str] = None,
        crop_path: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> AnprResult:
        """Process a plate through the full ANPR alert pipeline."""
        result = AnprResult(plate_text=plate_text)
        plate = normalize_plate(plate_text)

        if len(plate) < 4:
            logger.warning(f"Plate too short, skipping: {plate_text}")
            return result

        db = self.db_session_factory()
        try:
            detection_id = self._create_detection(db, plate, camera_id, crop_path, confidence)
            result.detection_id = detection_id

            from pipeline.events.watchlist_matcher import WatchlistMatcher
            matcher = WatchlistMatcher()
            match = matcher.check_plate(db, plate)
            result.watchlist_match = match
            result.is_watchlisted = match is not None

            if match:
                alert_id = self.alert_service.create_alert(
                    db=db,
                    detection_id=detection_id,
                    match=match,
                    camera_name=camera_name,
                    plate_text=plate,
                    vehicle_class=vehicle_class,
                    crop_path=crop_path,
                    confidence=confidence,
                )
                result.alert_id = alert_id
                logger.info(f"WATCHLIST ALERT: plate={plate} category={match.category} severity={match.severity}")
            else:
                logger.debug(f"Plate {plate} not in watchlist")

        except Exception as e:
            logger.error(f"ANPR pipeline error: {e}")
            db.rollback()
        finally:
            db.close()

        return result

    def _create_detection(
        self,
        db: Session,
        plate: str,
        camera_id: str,
        crop_path: Optional[str],
        confidence: Optional[float],
    ) -> Optional[str]:
        """Insert a detection record into the database."""
        detection_id = str(uuid.uuid4())
        try:
            db.execute(
                text("""
                    INSERT INTO detections
                        (id, camera_id, "timestamp", detected_plate, confidence, cropped_image_path)
                    VALUES
                        (:id, :camera_id, :timestamp, :plate, :confidence, :crop_path)
                """),
                {
                    "id": detection_id,
                    "camera_id": str(camera_id),
                    "timestamp": datetime.now(timezone.utc),
                    "plate": plate,
                    "confidence": confidence,
                    "crop_path": crop_path,
                },
            )
            db.commit()
            logger.info(f"Detection created: {detection_id} plate={plate}")
        except Exception as e:
            db.rollback()
            logger.error(f"Detection INSERT failed: {e}")
            return None
        return detection_id
