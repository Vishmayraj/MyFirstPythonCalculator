"""
model3_federation.adapters.rto_vms_adapter
-------------------------------------------
Simulated adapter for Gujarat RTO Checkpoint System (HikCentral-style).

HikCentral uses a REST event subscription. Its LPR event payload is:
  {
    "eventType":          "vehicleDetection",
    "plateText":          "GJ01XX9999",
    "cameraIndex":        "rto-nh48-01",
    "captureTime":        "2026-09-09T14:22:00Z",
    "detectionConfidence": 0.87,
    "vehicleCategory":    "truck"
  }

Note the intentionally different field names from Milestone:
  - Milestone uses "licensePlate" → HikCentral uses "plateText"
  - Milestone uses "score"        → HikCentral uses "detectionConfidence"
  - Milestone uses "utcTime"      → HikCentral uses "captureTime"
  - Milestone uses "vehicleClass" → HikCentral uses "vehicleCategory"

This is exactly the interoperability problem Model 3 solves.
RTO checkpoints emit less frequently (5–15 s) — toll plazas capture
fewer vehicles than inner-city cameras.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import Awaitable, Callable

from model3_federation.adapters.base import VMSAdapter
from model3_federation.schemas.models import FederatedCamera, FederatedEvent

_SYSTEM_ID = "a1000002-0000-0000-0000-000000000002"

_PLATES = [
    "GJ05AB1234", "GJ01KK7777", "GJ14WX2233", "GJ22LM4455",
    "GJ06NP8899", "GJ11QR6677", "GJ17ST0011",
]
_WATCHLISTED_PLATE = "GJ01CD5678"

_CAMERAS = [
    FederatedCamera(
        external_id="rto-nh48-01",
        name="NH-48 Toll Plaza",
        system_name="Gujarat RTO Checkpoint System (HikCentral)",
        vendor="Hikvision",
        department="RTO",
        lat=22.9930, lng=72.4426,
        location_label="NH-48 Ahmedabad-Mumbai",
    ),
    FederatedCamera(
        external_id="rto-nh8-01",
        name="NH-8 Checkpoint",
        system_name="Gujarat RTO Checkpoint System (HikCentral)",
        vendor="Hikvision",
        department="RTO",
        lat=23.0721, lng=72.5494,
        location_label="NH-8 Gandhinagar Highway",
    ),
    FederatedCamera(
        external_id="rto-sh17-01",
        name="SH-17 Himatnagar Toll",
        system_name="Gujarat RTO Checkpoint System (HikCentral)",
        vendor="Hikvision",
        department="RTO",
        lat=23.5995, lng=72.9638,
        location_label="SH-17 Himatnagar",
    ),
    FederatedCamera(
        external_id="rto-exp-01",
        name="Expressway Navsari Entry",
        system_name="Gujarat RTO Checkpoint System (HikCentral)",
        vendor="Hikvision",
        department="RTO",
        lat=20.9467, lng=72.9520,
        location_label="Navsari Expressway Entry",
    ),
]


class RTOVMSAdapter(VMSAdapter):
    """Simulated HikCentral-style RTO Checkpoint VMS adapter."""

    def __init__(self) -> None:
        self._event_counter = 0

    @property
    def system_name(self) -> str:
        return "Gujarat RTO Checkpoint System (HikCentral)"

    @property
    def vendor(self) -> str:
        return "Hikvision"

    @property
    def system_id(self) -> str:
        return _SYSTEM_ID

    async def connect(self) -> bool:
        self.log_info("Simulated HikCentral REST subscription established.")
        return True

    async def get_cameras(self) -> list[FederatedCamera]:
        return list(_CAMERAS)

    async def start_event_stream(
        self,
        callback: Callable[[FederatedEvent], Awaitable[None]],
    ) -> None:
        """Emits vehicle events every 5–15 seconds (toll checkpoint rate)."""
        self.log_info("Event stream started.")
        while True:
            try:
                await asyncio.sleep(random.uniform(5.0, 15.0))
                self._event_counter += 1

                camera = random.choice(_CAMERAS)

                if self._event_counter % 25 == 0:
                    plate = _WATCHLISTED_PLATE
                    confidence = round(random.uniform(0.88, 0.99), 2)
                else:
                    plate = random.choice(_PLATES)
                    confidence = round(random.uniform(0.72, 0.96), 2)

                vehicle_category = random.choice(["car", "car", "truck", "truck", "motorcycle", "bus"])

                # Raw payload exactly as HikCentral REST would deliver it
                raw: dict = {
                    "eventType":           "vehicleDetection",
                    "plateText":           plate,              # HikCentral: plateText (not licensePlate)
                    "cameraIndex":         camera.external_id, # HikCentral: cameraIndex (not deviceId)
                    "captureTime":         datetime.now(tz=timezone.utc).isoformat(),  # not utcTime
                    "detectionConfidence": confidence,         # HikCentral: detectionConfidence (not score)
                    "vehicleCategory":     vehicle_category,   # HikCentral: vehicleCategory (not vehicleClass)
                }

                # Translate HikCentral fields → FederatedEvent canonical fields
                event = FederatedEvent(
                    system_id=self.system_id,
                    system_name=self.system_name,
                    vendor=self.vendor,
                    camera_external_id=raw["cameraIndex"],           # ← different from Milestone
                    camera_name=camera.name,
                    event_type="vehicle_detection",
                    detected_plate=raw["plateText"],                  # ← different from Milestone
                    confidence=raw["detectionConfidence"],            # ← different from Milestone
                    vehicle_type=raw["vehicleCategory"],              # ← different from Milestone
                    source_timestamp=datetime.fromisoformat(raw["captureTime"]),  # ← different key
                    raw_payload=raw,
                )
                await callback(event)

            except asyncio.CancelledError:
                self.log_info("Event stream cancelled.")
                raise
            except Exception as exc:
                self.log_error(f"Unexpected error in event stream: {exc}")
                await asyncio.sleep(2.0)
