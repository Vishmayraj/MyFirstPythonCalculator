"""
Model 2 — Pre-Recorded Video Face Detection & Watchlist Worker.
==============================================================
Runs an isolated, on-demand face detection, biometric feature extraction,
and watchlist matching worker on uploaded video files (.mp4, .avi, .mov, etc.).

Streams processed video frames with glowing face boxes and live suspect
match alerts over WebSockets to the frontend dashboard.
"""

import base64
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import cv2
from sqlalchemy.orm import Session

from pipeline.face_detection.matcher import FaceDetectionResult, FaceMatchEngine

logger = logging.getLogger("sentinel.face_detection.worker")
logger.setLevel(logging.INFO)

SPEED_TO_INFER_EVERY = {
    "1x": 2,
    "2x": 4,
    "max": 6,
}


class FaceVideoWorker:
    """Processes uploaded video files for face detection & watchlist matching."""

    def __init__(
        self,
        job_id: str,
        file_path: str,
        camera_id: Optional[str] = "prerecorded",
        camera_name: str = "Pre-recorded Video Feed",
        speed: str = "1x",
        event_callback: Optional[Callable[[Dict], None]] = None,
        db_session_factory: Optional[Callable[[], Session]] = None,
        matcher: Optional[FaceMatchEngine] = None,
    ):
        self.job_id = job_id
        self.file_path = file_path
        self.camera_id = str(camera_id) if camera_id else "prerecorded"
        self.camera_name = camera_name
        self.speed = speed if speed in SPEED_TO_INFER_EVERY else "1x"
        self.event_callback = event_callback
        self.db_session_factory = db_session_factory

        self.matcher = matcher or FaceMatchEngine(db_session_factory=db_session_factory)

        self._thread: Optional[threading.Thread] = None
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_event = threading.Event()

        self.state: str = "idle"  # idle, running, paused, completed, stopped, error
        self.total_faces_detected: int = 0
        self.total_matches: int = 0
        self.current_frame: int = 0
        self.total_frames: int = 0
        self.fps: float = 25.0
        self.processing_fps: float = 0.0

    @property
    def is_running(self) -> bool:
        return self.state in ("running", "paused")

    def _emit(self, event_type: str, data: Dict):
        if self.event_callback:
            try:
                self.event_callback({"type": event_type, "data": data})
            except Exception as e:
                logger.warning(f"[{self.job_id}] event_callback error: {e}")

    def _run(self):
        logger.info(f"[{self.job_id}] FaceVideoWorker started on file: {self.file_path}")
        cap = cv2.VideoCapture(self.file_path)

        if not cap.isOpened():
            self.state = "error"
            self._emit("JOB_ERROR", {"job_id": self.job_id, "error": f"Failed to open video: {self.file_path}"})
            return

        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        self.fps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
        infer_every = SPEED_TO_INFER_EVERY.get(self.speed, 2)
        frame_delay = 0.0 if self.speed == "max" else (1.0 / (self.fps * (2.0 if self.speed == "2x" else 1.0)))

        self.state = "running"
        self._emit("JOB_STARTED", {
            "job_id": self.job_id,
            "total_frames": self.total_frames,
            "fps": self.fps,
            "camera_name": self.camera_name,
        })

        frame_idx = 0
        t_start = time.time()
        last_detections: List[FaceDetectionResult] = []

        # Maintain a short cooldown to avoid emitting duplicate alerts for the same person in rapid succession
        recent_alert_cooldowns: Dict[str, float] = {}

        try:
            while not self._stop_event.is_set():
                self._pause_event.wait()
                if self._stop_event.is_set():
                    break

                t_frame_start = time.time()
                ret, frame = cap.read()
                if not ret:
                    logger.info(f"[{self.job_id}] End of video stream reached.")
                    break

                frame_idx += 1
                self.current_frame = frame_idx

                # Inference on sampled frames
                if frame_idx % infer_every == 0:
                    now_utc = datetime.now(timezone.utc)
                    last_detections = self.matcher.process_frame(
                        frame=frame,
                        camera_id=self.camera_id,
                        frame_timestamp=now_utc,
                    )

                    if last_detections:
                        self.total_faces_detected += len(last_detections)

                    # Check for person matches and emit alerts
                    for det in last_detections:
                        if det.is_match and det.person_id:
                            now_sec = time.time()
                            last_seen_sec = recent_alert_cooldowns.get(det.person_id, 0.0)
                            # Alert once every 5 seconds per unique person
                            if now_sec - last_seen_sec > 5.0:
                                recent_alert_cooldowns[det.person_id] = now_sec
                                self.total_matches += 1
                                self._emit("PERSON_MATCH", {
                                    "job_id": self.job_id,
                                    "alert_id": det.alert_id,
                                    "person_id": det.person_id,
                                    "name": det.person_name,
                                    "category": det.category,
                                    "similarity": det.similarity_score,
                                    "distance": det.distance,
                                    "crop_url": det.face_crop_path,
                                    "photo_path": det.photo_path,
                                    "timestamp": now_utc.isoformat(),
                                    "camera_name": self.camera_name,
                                    "frame_index": frame_idx,
                                })

                # Draw bounding box overlays
                rendered_frame = self.matcher.draw_overlays(frame, last_detections)

                # Resize frame for efficient WebSocket delivery (max 720p)
                max_w = 960
                if rendered_frame.shape[1] > max_w:
                    scale = max_w / float(rendered_frame.shape[1])
                    rendered_frame = cv2.resize(
                        rendered_frame,
                        (max_w, int(rendered_frame.shape[0] * scale)),
                        interpolation=cv2.INTER_AREA,
                    )

                # Encode to JPEG
                ok, buffer = cv2.imencode(".jpg", rendered_frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok:
                    b64_image = base64.b64encode(buffer).decode("utf-8")
                    elapsed = time.time() - t_start
                    self.processing_fps = frame_idx / elapsed if elapsed > 0 else 0.0

                    self._emit("VIDEO_FRAME", {
                        "job_id": self.job_id,
                        "frame_index": frame_idx,
                        "image": b64_image,
                        "fps": round(self.processing_fps, 1),
                        "progress": round((frame_idx / self.total_frames) * 100, 1),
                    })

                # Periodic progress stats event
                if frame_idx % 10 == 0:
                    self._emit("JOB_PROGRESS", {
                        "job_id": self.job_id,
                        "current_frame": frame_idx,
                        "total_frames": self.total_frames,
                        "progress": round((frame_idx / self.total_frames) * 100, 1),
                        "processing_fps": round(self.processing_fps, 1),
                        "total_faces": self.total_faces_detected,
                        "total_matches": self.total_matches,
                    })

                # Frame rate throttling
                dt = time.time() - t_frame_start
                if frame_delay > dt:
                    time.sleep(frame_delay - dt)

            if self._stop_event.is_set():
                self.state = "stopped"
                self._emit("JOB_STOPPED", {"job_id": self.job_id, "current_frame": frame_idx})
            else:
                self.state = "completed"
                self._emit("JOB_COMPLETED", {
                    "job_id": self.job_id,
                    "total_frames": self.total_frames,
                    "total_faces": self.total_faces_detected,
                    "total_matches": self.total_matches,
                })

        except Exception as e:
            logger.error(f"[{self.job_id}] FaceVideoWorker exception: {e}", exc_info=True)
            self.state = "error"
            self._emit("JOB_ERROR", {"job_id": self.job_id, "error": str(e)})
        finally:
            cap.release()

    def start(self):
        if self.state in ("running", "paused"):
            return
        self._stop_event.clear()
        self._pause_event.set()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"FaceWorker-{self.job_id[:8]}")
        self._thread.start()

    def pause(self):
        if self.state == "running":
            self._pause_event.clear()
            self.state = "paused"
            self._emit("JOB_PAUSED", {"job_id": self.job_id, "current_frame": self.current_frame})

    def resume(self):
        if self.state == "paused":
            self._pause_event.set()
            self.state = "running"
            self._emit("JOB_RESUMED", {"job_id": self.job_id, "current_frame": self.current_frame})

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()  # Unpause to exit loop
        self.state = "stopped"
