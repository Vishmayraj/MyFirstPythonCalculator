"""
CameraWorker — reads frames from one camera in a dedicated thread.

Connects via its adapter, reads frames in a loop, extracts PTS from
CAP_PROP_POS_MSEC (NEVER from time.time() or datetime.now()), and
puts FramePacket objects on a shared output queue non-blocking.

On any failure: disconnects, waits with exponential backoff + jitter, reconnects.
The supervisor creates and manages workers — one instance per camera.

CRITICAL NON-NEGOTIABLE RULES:
1. RTSP MUST use TCP (enforced by adapter).
2. PTS ONLY for timestamps:
   pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
   NEVER use time.time() or datetime.now() on frames.
3. CAP_PROP_FPS is unreliable — do not use it for timing.
4. Reconnect with exponential backoff: 2s base, 30s cap, ±20% jitter.
5. Decoder warnings on join are NOT errors: normal until first IDR.
6. Feeds loop — scene cuts abruptly at loop point.
   VMS does not signal this. Analytics pipeline handles it. VMS does nothing.
7. Queue full → drop frame, never block (put_nowait).
"""

from __future__ import annotations

import logging
import queue
import random
import threading
import time

import cv2

from shared.adapters.base import BaseVMSAdapter, FramePacket

logger = logging.getLogger(__name__)

RECONNECT_BASE_SECONDS = 2.0
RECONNECT_MAX_SECONDS = 30.0


class CameraWorker:
    """
    Runs in its own thread (daemon=True).
    One instance per camera. Supervisor creates and manages these.
    """

    def __init__(
        self,
        adapter: BaseVMSAdapter,
        output_queue: queue.Queue,
        camera_id: str,
        source_grid_id: str,
    ) -> None:
        self._adapter = adapter
        self._output_queue = output_queue
        self._camera_id = camera_id
        self._source_grid_id = source_grid_id
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f"camera-worker-{self._source_grid_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        backoff = RECONNECT_BASE_SECONDS
        while not self._stop_event.is_set():
            try:
                self._connect_and_read()
                backoff = RECONNECT_BASE_SECONDS  # reset on clean return/exit
            except Exception as e:
                logger.warning(
                    f"[{self._source_grid_id}] Worker error: {e}"
                )

            if not self._stop_event.is_set():
                # Reconnect exponential backoff with ±20% jitter
                jitter = random.uniform(0.8, 1.2)
                sleep_seconds = min(backoff * jitter, RECONNECT_MAX_SECONDS)
                logger.info(
                    f"[{self._source_grid_id}] Reconnecting in {sleep_seconds:.1f}s (backoff={backoff:.1f}s)"
                )
                time.sleep(sleep_seconds)
                backoff = min(backoff * 2.0, RECONNECT_MAX_SECONDS)

    def _connect_and_read(self) -> None:
        """
        Connect and read frames until stream fails or stop requested.
        """
        try:
            connected = self._adapter.connect()
            if not connected:
                self._adapter.disconnect()
                raise ConnectionError(f"[{self._source_grid_id}] connect() returned False")

            stream = self._adapter.get_stream()
            cap: cv2.VideoCapture = stream.capture
            logger.info(f"[{self._source_grid_id}] Connected")

            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    logger.warning(
                        f"[{self._source_grid_id}] cap.read() returned False — reconnecting"
                    )
                    break

                # PTS in milliseconds — ONLY valid timestamp source
                pts_ms: float = float(cap.get(cv2.CAP_PROP_POS_MSEC))

                packet = FramePacket(
                    frame=frame,
                    pts_ms=pts_ms,
                    camera_id=self._camera_id,
                    source_grid_id=self._source_grid_id,
                    width=int(frame.shape[1]),
                    height=int(frame.shape[0]),
                )

                try:
                    self._output_queue.put_nowait(packet)
                except queue.Full:
                    # Queue full -> drop frame, throttle to avoid burning CPU when queue has no consumers
                    time.sleep(0.1)

                time.sleep(0.03)  # Yield GIL and pace reading to avoid spinning at 100% CPU

        finally:
            self._adapter.disconnect()
