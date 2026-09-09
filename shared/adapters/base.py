"""
VMS adapter interface — base types and abstract base class.

Every camera adapter implements BaseVMSAdapter.
Every consumer of VMS output depends on FramePacket.
Single definition here; do NOT duplicate in model2_analytics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class CameraMetadata:
    """Properties read from the camera or the grid catalogue."""

    source_grid_id: str
    rtsp_url: str
    codec: Optional[str]          # "h264" | "hevc" | None if unknown
    width: Optional[int]          # None if grid didn't report it
    height: Optional[int]
    fps: Optional[float]          # treat as approximate — CAP_PROP_FPS is unreliable
    bitrate_kbps: Optional[int]
    location_label: str           # raw label from /api/ingest e.g. "06 Timbavadi gate-Junagadh"


@dataclass
class StreamHandle:
    """Wraps whatever the ingestion worker reads frames from."""

    capture: object               # cv2.VideoCapture instance
    camera_id: str                # our DB UUID for this camera
    source_grid_id: str           # grid's id, used for logging and catalogue resync


@dataclass
class FramePacket:
    """
    The output contract from VMS to the ANPR/analytics pipeline.
    Every field here is what the analytics pipeline depends on.
    Do not add fields without updating docs/API_Contract.md §3.
    """

    frame: np.ndarray             # BGR, shape (H, W, 3) — OpenCV default
    pts_ms: float                 # Presentation timestamp in milliseconds — FROM CAP_PROP_POS_MSEC
    camera_id: str                # DB UUID — links detection to cameras table
    source_grid_id: str           # grid id — for logging/debugging
    width: int
    height: int


class BaseVMSAdapter(ABC):
    """
    Every camera adapter implements this interface.
    The ingestion worker calls only these methods — it never knows which adapter it has.
    """

    @abstractmethod
    def connect(self) -> bool:
        """
        Establish connection to the camera stream.
        Returns True if connection succeeded and frames can be read.
        Must force TCP for RTSP — never UDP.
        """
        ...

    @abstractmethod
    def get_stream(self) -> StreamHandle:
        """
        Return a StreamHandle the worker can read frames from.
        Call only after connect() returned True.
        """
        ...

    @abstractmethod
    def get_metadata(self) -> CameraMetadata:
        """
        Return camera properties.
        For RTSPAdapter: read from OpenCV after connect().
        For ONVIFAdapter: read from ONVIF GetVideoSources.
        CAP_PROP_FPS is unreliable — always set fps from the grid catalogue
        value if available, fall back to OpenCV only as a hint.
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Release the connection cleanly. Safe to call even if connect() failed."""
        ...
