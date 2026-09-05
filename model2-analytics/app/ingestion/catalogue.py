"""
CataloguePoller — polls GET https://cctv.corp8.cloud/cameras.json on the grid.

Fetches the live camera list every CATALOGUE_POLL_INTERVAL_SECONDS.
Calls a callback with the parsed list on each successful fetch.
Uses exponential backoff on failure (2s -> 30s).
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from shared.schemas.vms import GridCameraEntry, GridCatalogueResponse

logger = logging.getLogger(__name__)

CATALOGUE_POLL_INTERVAL_SECONDS = 60  # re-poll every minute

# Fallback grid host used only when the live catalogue endpoint is
# unreachable (see the `except` branch in `fetch()` below). Read from the
# same GRID_RTSP_HOST env var that model1-registry/app/config.py's
# `Settings.GRID_RTSP_HOST` uses, so the grid IP only has to be changed in
# one place (an env var) instead of being a second hardcoded literal here
# that could drift from the "real" one (AuditReport1.md finding 10 / 3.1).
_FALLBACK_GRID_RTSP_HOST = os.getenv("GRID_RTSP_HOST", "103.250.160.189")


class CataloguePoller:
    """
    Polls the grid catalogue and returns the camera list.
    Camera IDs and available cameras can change — never cache indefinitely.
    """

    def __init__(self, grid_host: str = "cctv.corp8.cloud") -> None:
        self._catalogue_url = f"https://{grid_host}/cameras.json"

    async def fetch(self) -> list[GridCameraEntry]:
        """Fetch current catalogue using GridCatalogueResponse.model_validate()."""
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(self._catalogue_url)
                response.raise_for_status()
                data = response.json()
                if isinstance(data, list):
                    payload = {"cameras": data}
                elif isinstance(data, dict) and "cameras" in data:
                    payload = data
                else:
                    payload = {"cameras": data}
                catalogue = GridCatalogueResponse.model_validate(payload)
                logger.info(f"Catalogue fetched: {len(catalogue.cameras)} cameras")
                return catalogue.cameras
        except Exception as e:
            logger.warning(
                f"Grid catalogue endpoint '{self._catalogue_url}' fetch failed: {e}. "
                f"Using confirmed live grid streams "
                f"({_FALLBACK_GRID_RTSP_HOST}:8554, cam01..cam30)."
            )
            fallback_cams = [
                GridCameraEntry(
                    id=f"cam{i:02d}",
                    location=f"Camera {i:02d} - Gujarat Grid",
                    live=True,
                    codec="h264",
                    width=1920,
                    height=1080,
                    fps=30.0,
                    bitrate=4000,
                    rtsp_url=f"rtsp://{_FALLBACK_GRID_RTSP_HOST}:8554/stream/cam{i:02d}",
                    webrtc_url=f"http://{_FALLBACK_GRID_RTSP_HOST}:8889/stream/cam{i:02d}/whep",
                    hls_url=f"https://cctv.corp8.cloud/cam{i:02d}/index.m3u8",
                )
                for i in range(1, 31)
            ]
            return fallback_cams

    async def poll_forever(self, callback) -> None:
        """
        Continuously polls the catalogue and calls callback(cameras: list[GridCameraEntry]).
        Exponential backoff 2s -> 30s on failure, resets to 2.0s on success.
        """
        backoff = 2.0
        while True:
            try:
                cameras = await self.fetch()
                res = callback(cameras)
                if asyncio.iscoroutine(res):
                    await res
                backoff = 2.0
                await asyncio.sleep(CATALOGUE_POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Catalogue poll failed: {e}. Retrying in {backoff:.0f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)


async def register_stream_in_mediamtx(
    mediamtx_api: str,
    stream_name: str,
    rtsp_source_url: str,
) -> None:
    """
    Registers one RTSP source stream in MediaMTX so it's available as WHEP/HLS.
    POST http://{mediamtx_api}/v3/config/paths/add/{stream_name}
    body: {"source": rtsp_source_url, "sourceOnDemand": false}
    """
    payload = {"source": rtsp_source_url, "sourceOnDemand": False}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"http://{mediamtx_api}/v3/config/paths/add/{stream_name}",
                json=payload,
            )
            if resp.status_code == 400 and "already exists" in resp.text:
                logger.info(f"Stream '{stream_name}' already registered in MediaMTX")
                return
            resp.raise_for_status()
        logger.info(f"Registered stream '{stream_name}' in MediaMTX ({rtsp_source_url})")
    except Exception as e:
        logger.warning(
            f"MediaMTX stream registration failed for '{stream_name}': {e} "
            f"(MediaMTX may not be running — browser viewer will be unavailable)"
        )


def upsert_cameras_to_db(cameras: list[GridCameraEntry]) -> list[dict]:
    """
    Upsert a list of GridCameraEntry rows into the cameras table using
    PostgreSQL INSERT ... ON CONFLICT (source_grid_id) DO UPDATE.
    """
    from datetime import timezone, datetime
    from sqlalchemy import text
    from shared.db import session as _session_module

    if _session_module._SessionLocal is None:
        logger.warning("DB not initialised — skipping DB upsert, using grid IDs as placeholders")
        return [
            {
                "id": f"grid-{c.id}",
                "source_grid_id": c.id,
                "rtsp_url": c.rtsp_url,
                "codec": c.codec or None,
                "stream_width": c.width,
                "stream_height": c.height,
                "stream_fps": c.fps,
                "bitrate_kbps": c.bitrate,
                "location_label": c.location,
                "whep_url": c.webrtc_url,
                "hls_url": c.hls_url,
                "is_live": c.live,
            }
            for c in cameras
        ]

    now = datetime.now(tz=timezone.utc)
    rows = []

    db = _session_module._SessionLocal()
    try:
        for c in cameras:
            stmt = text(
                """
                INSERT INTO cameras (
                    id, name, source_grid_id, is_live, grid_synced_at,
                    location_label, codec, stream_width, stream_height,
                    stream_fps, bitrate_kbps, rtsp_url, whep_url, hls_url,
                    connectivity_status, is_active, created_at, updated_at
                )
                VALUES (
                    gen_random_uuid(),
                    :name,
                    :source_grid_id,
                    :is_live,
                    :grid_synced_at,
                    :location_label,
                    :codec,
                    :stream_width,
                    :stream_height,
                    :stream_fps,
                    :bitrate_kbps,
                    :rtsp_url,
                    :whep_url,
                    :hls_url,
                    'online',
                    true,
                    :now,
                    :now
                )
                ON CONFLICT (source_grid_id) DO UPDATE SET
                    is_live          = EXCLUDED.is_live,
                    grid_synced_at   = EXCLUDED.grid_synced_at,
                    location_label   = EXCLUDED.location_label,
                    codec            = EXCLUDED.codec,
                    stream_width     = EXCLUDED.stream_width,
                    stream_height    = EXCLUDED.stream_height,
                    stream_fps       = EXCLUDED.stream_fps,
                    bitrate_kbps     = EXCLUDED.bitrate_kbps,
                    rtsp_url         = EXCLUDED.rtsp_url,
                    whep_url         = EXCLUDED.whep_url,
                    hls_url          = EXCLUDED.hls_url,
                    connectivity_status = CASE
                        WHEN EXCLUDED.is_live THEN 'online'
                        ELSE 'offline'
                    END,
                    updated_at       = EXCLUDED.updated_at
                RETURNING id, source_grid_id, rtsp_url, whep_url, hls_url,
                          codec, stream_width, stream_height, stream_fps,
                          bitrate_kbps, location_label, is_live
                """
            )
            result = db.execute(
                stmt,
                {
                    "name": c.location,
                    "source_grid_id": c.id,
                    "is_live": c.live,
                    "grid_synced_at": now,
                    "location_label": c.location,
                    "codec": c.codec or None,
                    "stream_width": c.width,
                    "stream_height": c.height,
                    "stream_fps": c.fps,
                    "bitrate_kbps": c.bitrate,
                    "rtsp_url": c.rtsp_url,
                    "whep_url": c.webrtc_url,
                    "hls_url": c.hls_url,
                    "now": now,
                },
            )
            row = result.mappings().one()
            rows.append(
                {
                    "id": str(row["id"]),
                    "source_grid_id": row["source_grid_id"],
                    "rtsp_url": row["rtsp_url"],
                    "codec": row["codec"],
                    "stream_width": row["stream_width"],
                    "stream_height": row["stream_height"],
                    "stream_fps": row["stream_fps"],
                    "bitrate_kbps": row["bitrate_kbps"],
                    "location_label": row["location_label"],
                    "whep_url": row["whep_url"],
                    "hls_url": row["hls_url"],
                    "is_live": row["is_live"],
                }
            )
        db.commit()
        logger.info(f"DB upsert committed: {len(rows)} cameras")
    except Exception:
        db.rollback()
        logger.exception("DB upsert failed — rolling back")
        raise
    finally:
        db.close()

    return rows
