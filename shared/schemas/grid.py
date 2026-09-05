"""
Pydantic schemas for Model 2 — Multi-Camera Grid & Stream Ingestion.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StreamProperties(BaseModel):
    codec: Optional[str] = Field(None, description="Video codec, e.g. H.264 or H.265")
    stream_width: Optional[int] = Field(None, description="Stream resolution width")
    stream_height: Optional[int] = Field(None, description="Stream resolution height")
    stream_fps: Optional[float] = Field(None, description="Declared frame rate")
    bitrate_kbps: Optional[int] = Field(None, description="Bitrate in kbps")


class CameraStreamResponse(BaseModel):
    id: UUID
    name: str
    department_name: Optional[str] = None
    district_name: Optional[str] = None
    connectivity_status: str
    is_active: bool
    is_live: bool = False
    source_grid_id: Optional[str] = None
    location_label: Optional[str] = None
    vms_url: Optional[str] = None
    rtsp_url: Optional[str] = None
    whep_url: Optional[str] = None
    hls_url: Optional[str] = None
    properties: Optional[StreamProperties] = None

    model_config = ConfigDict(from_attributes=True)


class CatalogueSyncItem(BaseModel):
    id: str
    location_label: Optional[str] = None
    is_live: bool = True
    codec: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    bitrate_kbps: Optional[int] = None
    rtsp_url: Optional[str] = None
    whep_url: Optional[str] = None
    hls_url: Optional[str] = None


class CatalogueSyncRequest(BaseModel):
    catalogue_url: Optional[str] = "http://127.0.0.1:8000/api/ingest"
    items: Optional[List[CatalogueSyncItem]] = None


class CatalogueSyncResponse(BaseModel):
    synced_count: int
    updated_count: int
    status: str
