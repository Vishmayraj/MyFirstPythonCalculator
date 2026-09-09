"""
AdapterFactory — picks the correct VMS adapter for a camera row.

Accepts either a Camera ORM instance (from a DB query) or a plain dict
(used in unit tests / catalogue-sync path). The `get` closure handles
both transparently so callers never need to branch on the type.

Week 1: always returns RTSPAdapter.
Week 2: add ONVIFAdapter selection when onvif_url is present and rtsp_url is not.
"""

from __future__ import annotations

from typing import Union

from shared.adapters.base import BaseVMSAdapter, CameraMetadata
from shared.adapters.rtsp import RTSPAdapter

# Deferred import — Camera ORM class; imported here to avoid circular
# deps at module load time for tests that don't need DB at all.
# If import fails (e.g. geoalchemy2 not installed in test env), factory
# still works with dict rows — ORM rows are only used in production.
try:
    from shared.db.models import Camera as _Camera
    _CameraType = Union[_Camera, dict]
except ImportError:
    _CameraType = dict  # type: ignore[misc,assignment]


class AdapterFactory:

    @staticmethod
    def from_camera_row(row: _CameraType) -> BaseVMSAdapter:  # type: ignore[valid-type]
        """
        Given a camera record — either the SQLAlchemy `Camera` ORM instance
        (the normal case, read from a DB query) or a plain dict (used only
        in unit tests / the catalogue-sync path before a row is persisted) —
        return the appropriate adapter instance.

        Accepts both so tests can pass a dict without touching the DB.
        In real ingestion code, this always receives a `Camera` ORM row.

        Required fields (by attribute or key): rtsp_url, source_grid_id, id.
        Optional: codec, stream_width, stream_height, stream_fps,
        bitrate_kbps, location_label.

        Raises ValueError if rtsp_url is absent or falsy.

        Week 1: always returns RTSPAdapter.
        # Week 2: if row.get("onvif_url"): return ONVIFAdapter(...)
        """
        if isinstance(row, dict):
            get = lambda k, default=None: row.get(k, default)  # noqa: E731
        else:
            get = lambda k, default=None: getattr(row, k, default)  # noqa: E731

        rtsp_url = get("rtsp_url")
        source_grid_id = get("source_grid_id") or str(get("id") or "")
        if not source_grid_id:
            raise ValueError("Camera row missing both source_grid_id and id")

        camera_id = str(get("id") or "")
        if not camera_id:
            raise ValueError("Camera row missing id")

        if not rtsp_url:
            raise ValueError(
                f"Camera {source_grid_id!r} has no rtsp_url — cannot create RTSPAdapter"
            )

        # Inject credentials from environment if needed (embed user:pass into rtsp_url in memory)
        if "103.250.160.189" in rtsp_url and "@" not in rtsp_url:
            import os
            from urllib.parse import quote
            user = os.environ.get("GRID_RTSP_USER", "")
            password = os.environ.get("GRID_RTSP_PASS", "")
            if user and password:
                user_enc = quote(user, safe="")
                pass_enc = quote(password, safe="")
                rtsp_url = rtsp_url.replace("rtsp://", f"rtsp://{user_enc}:{pass_enc}@")

        meta = CameraMetadata(
            source_grid_id=source_grid_id,
            rtsp_url=rtsp_url,
            codec=get("codec"),
            # Field names verified against shared/db/models.py in Task 0:
            # stream_width, stream_height, stream_fps, bitrate_kbps, location_label — all exist.
            width=get("stream_width"),
            height=get("stream_height"),
            fps=get("stream_fps"),
            bitrate_kbps=get("bitrate_kbps"),
            location_label=get("location_label", ""),
        )

        return RTSPAdapter(
            rtsp_url=rtsp_url,
            source_grid_id=source_grid_id,
            camera_id=camera_id,
            catalogue_metadata=meta,
        )
