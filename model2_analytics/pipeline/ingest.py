"""
Stream Ingestion Client — Model 2 Analytics Pipeline

Complies strictly with Hackathon Portal ingestion rules:
 1. Forces RTSP over TCP (`rtsp_transport=tcp`).
 2. Drives frame timing from Presentation Timestamps (PTS `CAP_PROP_POS_MSEC`), never wall-clock or declared FPS.
 3. Automatic reconnect with exponential backoff (~2s start, capped at ~30s).
 4. Tolerates decoder warnings and mid-stream IDR frame waits.
 5. Recovers from scene discontinuities / stream loop points.
 6. Reads dynamically from `/api/ingest` camera catalogue contract.
"""

import logging
import os
import threading
import time
from typing import Callable, Generator, Optional, Tuple

import cv2
import requests

# Enforce RTSP over TCP and disable buffering for real-time WebRTC sync
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|"
    "fflags;nobuffer|"
    "flags;low_delay|"
    "max_delay;50000"
)

logger = logging.getLogger("sentinel.ingest")
logger.setLevel(logging.INFO)


class StreamIngestClient:
    """
    Zero-latency RTSP ingestion client.
    Runs a dedicated background reader thread that constantly reads from RTSP,
    dropping stale buffered frames and exposing only the newest live frame.
    """

    def __init__(
        self,
        camera_id: str,
        rtsp_url: str,
        initial_backoff: float = 2.0,
        max_backoff: float = 30.0,
    ):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.is_running = False
        self._current_backoff = initial_backoff

        self._latest_frame = None
        self._latest_pts = 0.0
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _reader_loop(self):
        """Continuously pulls frames from RTSP, keeping only the latest frame."""
        reconnect_count = 0

        while self.is_running:
            logger.info(f"Connecting to RTSP stream [{self.camera_id}]: {self.rtsp_url}")
            cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                logger.warning(
                    f"Failed to open stream [{self.camera_id}]. Retrying in {self._current_backoff:.1f}s..."
                )
                time.sleep(self._current_backoff)
                self._current_backoff = min(self._current_backoff * 2, self.max_backoff)
                continue

            self._current_backoff = self.initial_backoff
            logger.info(f"Stream connected successfully [{self.camera_id}]")

            try:
                while self.is_running and cap.isOpened():
                    ok, frame = cap.read()
                    if not ok:
                        logger.warning(f"Stream frame drop detected on [{self.camera_id}]. Reconnecting...")
                        break

                    pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                    with self._lock:
                        self._latest_frame = frame
                        self._latest_pts = pts_ms
                    self._event.set()
            except Exception as e:
                logger.error(f"Error reading RTSP frames [{self.camera_id}]: {e}")
            finally:
                cap.release()

            if self.is_running:
                time.sleep(self._current_backoff)
                self._current_backoff = min(self._current_backoff * 2, self.max_backoff)

    def read_frames(
        self,
        max_reconnect_attempts: Optional[int] = None,
    ) -> Generator[Tuple[any, float], None, None]:
        """
        Yields always-fresh (frame, pts_ms) tuples without buffering lag.
        """
        import threading
        self.is_running = True
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

        while self.is_running:
            # 50 ms timeout: wake up quickly on every new frame signal.
            # We do NOT null out _latest_frame — the reader thread continuously
            # overwrites it with the newest frame (drop-frame, not buffer).
            # This prevents the old 1-second stall that occurred when YOLO
            # inference was slower than one frame interval.
            if self._event.wait(timeout=0.05):
                self._event.clear()
                with self._lock:
                    frame = self._latest_frame
                    pts = self._latest_pts

                if frame is not None:
                    yield frame, pts

        self.is_running = False


def fetch_ingest_catalogue(catalogue_url: str = "http://127.0.0.1:8000/api/ingest") -> list:
    """
    Fetches available camera feeds dynamically from the hackathon `/api/ingest` catalogue.
    """
    try:
        resp = requests.get(catalogue_url, timeout=5.0)
        if resp.status_code == 200:
            return resp.json().get("cameras", [])
        logger.warning(f"Catalogue returned HTTP {resp.status_code}")
    except Exception as err:
        logger.warning(f"Could not reach ingestion catalogue at {catalogue_url}: {err}")
    return []
