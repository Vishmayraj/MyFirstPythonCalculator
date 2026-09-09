"""
Model 2 — Pre-Recorded Video AI Detection Worker
=================================================
Runs an isolated, on-demand video detection and tracking pipeline for uploaded
video files (.mp4, .avi, .mov, .mkv).

Reuses existing core components:
  - VehicleDetector: YOLOv8 Indian Traffic detection
  - InFrameTracker: Single-camera IoU + centroid tracker
  - DetectionWriter: Database persistence to `detections` & `vehicle_tracks` + crop saving
"""

import base64
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, Optional

import cv2
from sqlalchemy.orm import Session

from pipeline.detection.vehicle_detector import VehicleDetector
from pipeline.detection.writer import DetectionWriter
from pipeline.tracking.frame_tracker import InFrameTracker

logger = logging.getLogger("sentinel.video_worker")
logger.setLevel(logging.INFO)

SPEED_TO_INFER_EVERY = {
    "1x": 2,
    "2x": 4,
    "max": 6,
}


class PreRecordedVideoWorker:
    """Processes uploaded video files asynchronously in an independent daemon thread."""

    def __init__(
        self,
        job_id: str,
        file_path: str,
        camera_uuid: uuid.UUID,
        camera_name: str = "Recorded Video Source",
        speed: str = "1x",
        event_callback: Optional[Callable[[Dict], None]] = None,
        db_session_factory: Optional[Callable[[], Session]] = None,
    ):
        self.job_id = job_id
        self.file_path = file_path
        self.camera_uuid = camera_uuid
        self.camera_name = camera_name
        self.speed = speed if speed in SPEED_TO_INFER_EVERY else "1x"
        self.event_callback = event_callback
        self.db_session_factory = db_session_factory

        self.tracker = InFrameTracker(camera_id=f"rec-{job_id[:8]}", min_confirmed_frames=2, iou_threshold=0.25)
        self.detector = VehicleDetector()
        self.writer = DetectionWriter()

        self._thread: Optional[threading.Thread] = None
        self._pause_event = threading.Event()
        self._pause_event.set()  # Initialized in unpaused (running) state
        self._stop_event = threading.Event()

        self.state: str = "idle"  # idle, running, paused, completed, stopped, error
        self.total_detections: int = 0
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
        logger.info(f"[{self.job_id}] PreRecordedVideoWorker started for file: {self.file_path}")
        cap = cv2.VideoCapture(self.file_path)

        if not cap.isOpened():
            self.state = "error"
            self._emit("JOB_ERROR", {"job_id": self.job_id, "error": f"Failed to open video file: {self.file_path}"})
            logger.error(f"[{self.job_id}] Cannot open video: {self.file_path}")
            return

        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        self.fps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
        infer_every = SPEED_TO_INFER_EVERY.get(self.speed, 2)
        frame_delay = 0.0 if self.speed == "max" else (1.0 / (self.fps * (2.0 if self.speed == "2x" else 1.0)))

        self.state = "running"
        frame_idx = 0
        t_start = time.time()
        fps_frames_count = 0
        t_fps_checkpoint = time.time()

        last_boxes_payload = []
        last_active_tracks_len = 0

        try:
            while not self._stop_event.is_set():
                # Pause barrier
                self._pause_event.wait()
                if self._stop_event.is_set():
                    break

                loop_t0 = time.time()
                ok, frame = cap.read()
                if not ok:
                    # Video completed
                    break

                frame_idx += 1
                self.current_frame = frame_idx
                fps_frames_count += 1
                pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC) or (frame_idx * (1000.0 / self.fps))

                # Processing speed calculation
                now = time.time()
                elapsed_chk = now - t_fps_checkpoint
                if elapsed_chk >= 1.0:
                    self.processing_fps = round(fps_frames_count / elapsed_chk, 1)
                    fps_frames_count = 0
                    t_fps_checkpoint = now

                # Run inference periodically based on speed setting
                should_infer = (frame_idx % infer_every == 0)
                if should_infer:
                    raw_dets = self.detector.detect(frame, pts_ms=pts_ms)
                    new_events, active_tracks = self.tracker.update(raw_dets, pts_ms=pts_ms)
                    h, w = frame.shape[:2]

                    last_boxes_payload = [
                        {
                            "track_id": trk.track_id,
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
                    last_active_tracks_len = len(active_tracks)

                    # Persist confirmed sightings into PostgreSQL
                    if new_events and self.db_session_factory:
                        db = self.db_session_factory()
                        if db is not None:
                            try:
                                for event in new_events:
                                    meta = self.writer.persist_sighting(
                                        db=db,
                                        camera_uuid=self.camera_uuid,
                                        event=event,
                                    )
                                    self.total_detections += 1
                                    self._emit(
                                        "NEW_DETECTION",
                                        {
                                            "job_id": self.job_id,
                                            "detection_id": meta["detection_id"],
                                            "track_id": event.track_id,
                                            "camera_name": self.camera_name,
                                            "class_name": event.class_name,
                                            "confidence": round(event.confidence * 100, 1),
                                            "timestamp": event.timestamp.strftime("%H:%M:%S"),
                                            "detected_plate": meta.get("detected_plate"),
                                            "crop_path": meta.get("crop_path"),
                                            "vehicle_track_id": meta.get("vehicle_track_id"),
                                        },
                                    )
                            finally:
                                db.close()

                # Emit bounding boxes
                self._emit(
                    "FRAME_BOXES",
                    {
                        "job_id": self.job_id,
                        "frame_n": frame_idx,
                        "boxes": last_boxes_payload,
                        "active_tracks": last_active_tracks_len,
                    },
                )

                # JPEG encode and transmit video frame
                # Resize if frame is large to keep WebSocket responsive and smooth
                h, w = frame.shape[:2]
                target_w = min(w, 960)
                if target_w < w:
                    target_h = int(h * (target_w / w))
                    disp_frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
                else:
                    disp_frame = frame

                encode_ok, buffer = cv2.imencode(".jpg", disp_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
                if encode_ok:
                    b64_frame = base64.b64encode(buffer).decode("ascii")
                    self._emit(
                        "VIDEO_FRAME",
                        {
                            "job_id": self.job_id,
                            "frame_n": frame_idx,
                            "total_frames": self.total_frames,
                            "pts_ms": round(pts_ms, 1),
                            "jpeg_b64": b64_frame,
                        },
                    )

                # Periodic progress emission (every 5 frames or final)
                if frame_idx % 5 == 0 or frame_idx == self.total_frames:
                    pct = round((frame_idx / self.total_frames) * 100, 1)
                    self._emit(
                        "JOB_PROGRESS",
                        {
                            "job_id": self.job_id,
                            "frame_n": frame_idx,
                            "total_frames": self.total_frames,
                            "pct": min(100.0, pct),
                            "processing_fps": self.processing_fps,
                            "state": self.state,
                            "total_detections": self.total_detections,
                        },
                    )

                # Playback pacing delay if not in max speed
                if frame_delay > 0:
                    elapsed = time.time() - loop_t0
                    sleep_time = frame_delay - elapsed
                    if sleep_time > 0.002:
                        time.sleep(sleep_time)

            # End of loop
            if self._stop_event.is_set():
                self.state = "stopped"
                logger.info(f"[{self.job_id}] Video processing stopped by user.")
            else:
                self.state = "completed"
                logger.info(f"[{self.job_id}] Video processing completed. Processed {frame_idx}/{self.total_frames} frames.")
                self._emit(
                    "JOB_DONE",
                    {
                        "job_id": self.job_id,
                        "total_frames": frame_idx,
                        "total_detections": self.total_detections,
                    },
                )

        except Exception as err:
            self.state = "error"
            logger.exception(f"[{self.job_id}] Worker execution exception: {err}")
            self._emit("JOB_ERROR", {"job_id": self.job_id, "error": str(err)})
        finally:
            cap.release()

    def start(self):
        if self.state in ("running", "paused"):
            return
        self._stop_event.clear()
        self._pause_event.set()
        self._thread = threading.Thread(target=self._run, name=f"RecordedWorker-{self.job_id[:8]}", daemon=True)
        self._thread.start()

    def pause(self):
        if self.state == "running":
            self._pause_event.clear()
            self.state = "paused"
            self._emit("JOB_PROGRESS", {"job_id": self.job_id, "state": "paused", "pct": round((self.current_frame / self.total_frames) * 100, 1)})
            logger.info(f"[{self.job_id}] Video worker paused.")

    def resume(self):
        if self.state == "paused":
            self._pause_event.set()
            self.state = "running"
            self._emit("JOB_PROGRESS", {"job_id": self.job_id, "state": "running", "pct": round((self.current_frame / self.total_frames) * 100, 1)})
            logger.info(f"[{self.job_id}] Video worker resumed.")

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()  # Unblock thread if paused so it can cleanly exit
        self.state = "stopped"
        logger.info(f"[{self.job_id}] Video worker stop requested.")
