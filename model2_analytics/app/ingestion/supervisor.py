"""
IngestionSupervisor — manages one CameraWorker per live camera.

Called by the catalogue poller when the camera list updates.
Starts workers for new cameras, stops workers for removed/offline cameras.

Design constraints:
- sync methods only (workers are threads, supervisor is sync)
- only CataloguePoller uses async/await
- _workers dict keyed by source_grid_id
- one bad camera never blocks the rest
"""

from __future__ import annotations

import asyncio
import logging
import queue

from shared.adapters.factory import AdapterFactory
from shared.adapters.base import FramePacket  # noqa: F401 — re-exported for consumers

logger = logging.getLogger(__name__)


class IngestionSupervisor:
    """
    Manages one CameraWorker per live camera.
    Called by the catalogue poller when the camera list updates.
    Starts workers for new cameras, stops workers for removed cameras.
    """

    def __init__(
        self,
        output_queue: queue.Queue,
        mediamtx_api: str | None = None,
    ) -> None:
        self._output_queue = output_queue
        self._mediamtx_api = mediamtx_api
        self._workers: dict[str, object] = {}  # source_grid_id → CameraWorker

    def sync(self, camera_rows: list[dict]) -> None:
        """
        Reconcile running workers with current camera list.
        camera_rows: list of dicts matching cameras table columns.
        Only cameras with is_live=True get a worker.
        """
        try:
            from ingestion.worker import CameraWorker
        except ImportError:
            from model2_analytics.app.ingestion.worker import CameraWorker

        try:
            from ingestion.catalogue import register_stream_in_mediamtx
        except ImportError:
            from model2_analytics.app.ingestion.catalogue import register_stream_in_mediamtx

        current_ids = {
            row.get("source_grid_id") or str(row.get("id"))
            for row in camera_rows
            if row.get("is_live")
        }
        running_ids = set(self._workers.keys())

        to_start = current_ids - running_ids
        to_stop = running_ids - current_ids

        for grid_id in to_stop:
            logger.info(f"Stopping worker for removed/offline camera {grid_id}")
            worker = self._workers.pop(grid_id)
            if hasattr(worker, "stop"):
                worker.stop()

        for row in camera_rows:
            grid_id = row.get("source_grid_id") or str(row.get("id"))
            if not row.get("is_live"):
                continue
            if grid_id not in to_start:
                continue
            try:
                adapter = AdapterFactory.from_camera_row(row)
                worker = CameraWorker(
                    adapter=adapter,
                    output_queue=self._output_queue,
                    camera_id=str(row.get("id")),
                    source_grid_id=grid_id,
                )
                worker.start()
                self._workers[grid_id] = worker
                logger.info(f"Started worker for camera {grid_id}")

                if self._mediamtx_api and row.get("rtsp_url"):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(
                            register_stream_in_mediamtx(
                                mediamtx_api=self._mediamtx_api,
                                stream_name=grid_id,
                                rtsp_source_url=row["rtsp_url"],
                            )
                        )
                    except RuntimeError:
                        pass
            except ValueError as e:
                logger.error(f"Failed to create adapter for {grid_id}: {e}")
            except Exception as e:
                logger.error(f"Failed to start worker for {grid_id}: {e}")

    def stop_all(self) -> None:
        for worker in self._workers.values():
            if hasattr(worker, "stop"):
                worker.stop()
        self._workers.clear()
        logger.info("All ingestion workers stopped")
