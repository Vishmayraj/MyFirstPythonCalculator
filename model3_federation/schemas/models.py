"""
model3_federation.schemas.models
---------------------------------
Pydantic models used across adapters, the event bus, the
correlation engine, and the REST/WebSocket API.

Design notes:
  - All timestamps are UTC-aware datetimes.
  - `raw_payload` preserves the original vendor JSON for audit trails.
  - Plate normalisation (uppercase, strip spaces/hyphens) is applied
    centrally in the correlation engine — not here — so adapters can
    store exactly what the vendor sent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid4())


# ---------------------------------------------------------------------------
# Camera descriptor — one per physical camera in a federated VMS
# ---------------------------------------------------------------------------

class FederatedCamera(BaseModel):
    external_id: str                          # camera ID in the source VMS
    name: str
    system_name: str                          # which VMS this camera belongs to
    vendor: str                               # Milestone | Hikvision | Dahua
    department: str                           # Police | RTO | Municipal
    lat: Optional[float] = None
    lng: Optional[float] = None
    location_label: Optional[str] = None
    is_active: bool = True


# ---------------------------------------------------------------------------
# Detection event — normalised form of whatever a VMS adapter receives
# ---------------------------------------------------------------------------

class FederatedEvent(BaseModel):
    id: str = Field(default_factory=_new_id)
    system_id: str                            # UUID of the federated_systems DB row
    system_name: str                          # e.g. "Gujarat Police VMS (Milestone)"
    vendor: str
    camera_external_id: str
    camera_name: str
    event_type: str                           # "vehicle_detection" | "person_detection" | "intrusion"
    detected_plate: Optional[str] = None
    confidence: Optional[float] = None
    vehicle_type: Optional[str] = None
    snapshot_url: Optional[str] = None
    source_timestamp: datetime = Field(default_factory=_now_utc)
    received_at: datetime = Field(default_factory=_now_utc)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    class Config:
        # Allow serialisation of datetime objects
        json_encoders = {datetime: lambda v: v.isoformat()}


# ---------------------------------------------------------------------------
# Cross-system correlation — same plate seen in 2+ VMS systems
# ---------------------------------------------------------------------------

class CorrelationResult(BaseModel):
    id: str = Field(default_factory=_new_id)
    plate_number: str
    systems_involved: list[str]               # names of VMS systems that saw this plate
    first_seen: datetime
    last_seen: datetime
    travel_time_secs: Optional[int] = None
    camera_sequence: list[dict[str, Any]] = Field(default_factory=list)
    # [{camera_name, system_name, timestamp, lat, lng}]
    is_watchlisted: bool = False

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


# ---------------------------------------------------------------------------
# Federated watchlist alert — generated when detected_plate hits watchlist
# ---------------------------------------------------------------------------

class FederatedAlert(BaseModel):
    id: str = Field(default_factory=_new_id)
    event_id: str                             # FederatedEvent.id that triggered this
    plate_number: str
    system_name: str
    camera_name: str
    severity: str = "high"                    # low | medium | high | critical
    created_at: datetime = Field(default_factory=_now_utc)
    acknowledged: bool = False

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


# ---------------------------------------------------------------------------
# VMS system descriptor — one per federated department VMS
# ---------------------------------------------------------------------------

class FederatedSystem(BaseModel):
    id: str                                   # UUID from federated_systems table
    name: str
    vendor: str
    department: str
    status: str = "connected"                 # connected | disconnected | error | degraded
    camera_count: int = 0
    events_per_minute: float = 0.0
    last_heartbeat: Optional[datetime] = None

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


# ---------------------------------------------------------------------------
# WebSocket push envelope — wraps any payload sent to /ws/federation
# ---------------------------------------------------------------------------

class WSMessage(BaseModel):
    type: str                                 # "event" | "alert" | "correlation" | "heartbeat"
    payload: dict[str, Any]

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
