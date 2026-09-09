"""
Phase 3 — Multi-Stream Pipeline Runner
=======================================
Camera 04 (source_grid_id=4)  → rtsp://103.250.160.189:8554/stream/cam04
Camera 22 (source_grid_id=22) → rtsp://103.250.160.189:8554/stream/cam22

Architecture per camera (runs in a daemon thread):
  StreamIngestClient  →  VehicleDetector  →  InFrameTracker  →  DetectionWriter  →  PostgreSQL

No fake/synthetic data. If RTSP is unreachable, the StreamIngestClient retries
with exponential backoff (2s → 30s cap) until the stream comes back.
"""

import logging
import threading
import uuid
from typing import Callable, Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy import text

from pipeline.detection.vehicle_detector import VehicleDetector
from pipeline.detection.writer import DetectionWriter
from pipeline.ingest import StreamIngestClient
from pipeline.tracking.frame_tracker import InFrameTracker

logger = logging.getLogger("sentinel.runner")
logger.setLevel(logging.INFO)

import os

user = os.getenv("GRID_RTSP_USER", "")
passwd = os.getenv("GRID_RTSP_PASS", "")
host = os.getenv("GRID_RTSP_HOST", "103.250.160.189")
auth = f"{user}:{passwd}@" if user and passwd else ""

# ── Camera configuration ───────────────────────────────────────────
CAMERAS = [
    {
        "tag":            "cam04",
        "name":           "Camera 04 — Paldi Circle (Ahmedabad)",
        "source_grid_id": "4",
        "rtsp_url":       f"rtsp://{auth}{host}:8554/stream/cam04",
    },
    {
        "tag":            "cam22",
        "name":           "Camera 22 — BK Mervada (Banaskantha)",
        "source_grid_id": "22",
        "rtsp_url":       f"rtsp://{auth}{host}:8554/stream/cam22",
    },
]

# Run YOLO every 3rd live frame — the InFrameTracker interpolates between
# inferences, so detection quality stays high while the pipeline keeps up
# with the live stream instead of drifting seconds behind on CPU.
INFER_EVERY_N_FRAMES = 3


class CameraWorker:
    """Single camera ingestion + detection + persistence worker (runs in daemon thread)."""

    def __init__(
        self,
        tag: str,
        name: str,
        source_grid_id: str,
        rtsp_url: str,
        event_callback: Optional[Callable[[Dict], None]],
        db_session_factory: Optional[Callable[[], Session]],
    ):
        self.tag               = tag
        self.name              = name
        self.source_grid_id    = source_grid_id
        self.rtsp_url          = rtsp_url
        self.event_callback    = event_callback
        self.db_session_factory = db_session_factory

        self.tracker  = InFrameTracker(camera_id=tag, min_confirmed_frames=3, iou_threshold=0.30)
        self.detector = VehicleDetector()
        self.writer   = DetectionWriter()

        self._running        = False
        self._thread: Optional[threading.Thread] = None
        self._camera_uuid: Optional[uuid.UUID]   = None

    # ── Resolve camera UUID from DB (cached after first call) ──────
    def _resolve_uuid(self, db: Session) -> Optional[uuid.UUID]:
        if self._camera_uuid:
            return self._camera_uuid
        row = db.execute(
            text("SELECT id FROM cameras WHERE source_grid_id = :s LIMIT 1"),
            {"s": self.source_grid_id},
        ).fetchone()
        if row:
            self._camera_uuid = row[0]
        else:
            logger.warning(f"[{self.tag}] No DB row for source_grid_id={self.source_grid_id}")
        return self._camera_uuid

    # ── Thread entry point ─────────────────────────────────────────
    def _run(self):
        logger.info(f"[{self.tag}] Worker started → {self.rtsp_url}")
        ingest = StreamIngestClient(camera_id=self.tag, rtsp_url=self.rtsp_url)

        frame_n = 0
        for frame, pts_ms in ingest.read_frames():
            if not self._running:
                break

            frame_n += 1
            if frame_n % INFER_EVERY_N_FRAMES != 0:
                continue

            # ── Detect ───────────────────────────────────────────
            raw_dets = self.detector.detect(frame, pts_ms=pts_ms)

            # ── Track ────────────────────────────────────────────
            new_events, active_tracks = self.tracker.update(raw_dets, pts_ms=pts_ms)

            # ── Broadcast live bounding boxes for in-browser canvas ──
            if self.event_callback:
                h, w = frame.shape[:2]
                boxes_payload = [
                    {
                        "track_id":   trk.track_id,
                        "bbox": [
                            round(trk.bbox[0] / w, 4),
                            round(trk.bbox[1] / h, 4),
                            round(trk.bbox[2] / w, 4),
                            round(trk.bbox[3] / h, 4),
                        ],
                        "class_name": trk.class_name,
                        "confidence": round(trk.best_conf * 100, 1),
                    }
                    for trk in active_tracks
                    if trk.missed_frames == 0
                ]
                self.event_callback({
                    "type":          "FRAME_BOXES",
                    "camera_tag":    self.tag,
                    "boxes":         boxes_payload,
                    "active_tracks": len(active_tracks),
                })

            if not new_events:
                continue

            # ── Persist & broadcast ───────────────────────────────
            if not self.db_session_factory:
                continue

            db = self.db_session_factory()
            if db is None:
                continue

            try:
                cam_uuid = self._resolve_uuid(db)
                if cam_uuid is None:
                    continue

                for event in new_events:
                    meta = self.writer.persist_sighting(
                        db=db,
                        camera_uuid=cam_uuid,
                        event=event,
                    )
                    if self.event_callback:
                        self.event_callback({
                            "type":             "NEW_DETECTION",
                            "detection_id":     meta["detection_id"],
                            "track_id":         event.track_id,
                            "camera_tag":       self.tag,
                            "camera_name":      self.name,
                            "class_name":       event.class_name,
                            "confidence":       round(event.confidence * 100, 1),
                            "timestamp":        event.timestamp.strftime("%H:%M:%S"),
                            "detected_plate":   meta.get("detected_plate"),
                            "crop_path":        meta.get("crop_path"),
                            "vehicle_track_id": meta.get("vehicle_track_id"),
                        })
            finally:
                db.close()

        ingest.is_running = False
        logger.info(f"[{self.tag}] Worker stopped.")

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name=f"Worker-{self.tag}", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False


class MultiStreamPipelineRunner:
    """Manages CameraWorker threads for all configured cameras on-demand."""

    def __init__(
        self,
        event_callback: Optional[Callable[[Dict], None]] = None,
        db_session_factory: Optional[Callable[[], Session]] = None,
    ):
        self.event_callback = event_callback
        self.db_session_factory = db_session_factory
        self.workers: Dict[str, CameraWorker] = {}

    @property
    def is_running(self) -> bool:
        return any(w._running for w in self.workers.values())

    def start_all(self):
        """Starts workers for cameras if not already active."""
        for cfg in CAMERAS:
            tag = cfg["tag"]
            if tag not in self.workers or not self.workers[tag]._running:
                w = CameraWorker(
                    tag               = cfg["tag"],
                    name              = cfg["name"],
                    source_grid_id    = cfg["source_grid_id"],
                    rtsp_url          = cfg["rtsp_url"],
                    event_callback    = self.event_callback,
                    db_session_factory = self.db_session_factory,
                )
                self.workers[tag] = w
                w.start()
        logger.info("Pipeline runner active: camera workers started on-demand.")

    def stop_all(self):
        """Stops all active camera workers to conserve CPU and stream bandwidth."""
        for w in list(self.workers.values()):
            w.stop()
        self.workers.clear()
        logger.info("Pipeline runner paused: all camera workers stopped.")
