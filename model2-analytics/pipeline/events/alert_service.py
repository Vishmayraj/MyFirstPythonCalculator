"""
Phase 3 — Alert Service
========================
Creates an alert record when a detected plate matches the watchlist,
and broadcasts it over the existing WebSocket pipeline.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from pipeline.events.watchlist_matcher import WatchlistMatch

logger = logging.getLogger("sentinel.alerts")
logger.setLevel(logging.INFO)


class AlertService:
    """
    Responsible for:
      1. INSERT INTO alerts (detection_id, watchlist_id, severity)
      2. Broadcasting the alert payload over WebSocket via on_detection_event
    """

    def __init__(
        self,
        on_detection_event: Optional[Callable] = None,
    ):
        self.on_detection_event = on_detection_event or self._noop

    @staticmethod
    def _noop(*args, **kwargs):
        pass

    # ── Alert creation ────────────────────────────────────────────
    def create_alert(
        self,
        db: Session,
        detection_id: str,
        match: WatchlistMatch,
        camera_name: Optional[str] = None,
        plate_text: Optional[str] = None,
        vehicle_class: Optional[str] = None,
        crop_path: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> Optional[str]:
        """Persist one alert + broadcast over WS. Returns alert_id or None."""
        alert_id = str(uuid.uuid4())

        try:
            db.execute(
                text("""
                    INSERT INTO alerts
                        (id, detection_id, watchlist_id, alert_type, severity)
                    VALUES
                        (:id, :detection_id, :watchlist_id, 'vehicle_match', :severity)
                """),
                {
                    "id":            alert_id,
                    "detection_id":  detection_id,
                    "watchlist_id":  str(match.watchlist_id),
                    "severity":      match.severity,
                },
            )
            db.commit()
            logger.info(
                f"ALERT created {alert_id}: plate={plate_text} "
                f"category={match.category} severity={match.severity}"
            )
        except Exception as e:
            db.rollback()
            logger.error(f"Alert INSERT failed: {e}")
            return None

        # ── WebSocket broadcast ────────────────────────────────────
        try:
            self.on_detection_event({
                "type": "watchlist_alert",
                "payload": {
                    "alert_id":     alert_id,
                    "detection_id": detection_id,
                    "watchlist_id": str(match.watchlist_id),
                    "plate":        plate_text,
                    "category":     match.category,
                    "description":  match.description,
                    "severity":     match.severity,
                    "vehicle_type": vehicle_class,
                    "camera":       camera_name,
                    "crop_path":    crop_path,
                    "confidence":   confidence,
                },
            })
        except Exception as e:
            logger.warning(f"WS broadcast failed: {e}")

        return alert_id