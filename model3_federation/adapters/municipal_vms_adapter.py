"""
model3_federation.adapters.municipal_vms_adapter
-------------------------------------------------
Simulated adapter for AMC City Surveillance (Dahua DSS-style).

Dahua DSS uses a WebSocket push. Its ANPR payload is:
  {
    "type":    "ANPR",
    "plate":   "GJ01CD5678",
    "channel": "amc-lal-01",
    "time":    1725878400,       ← UNIX timestamp, not ISO-8601
    "conf":    0.79,
    "class":   "car"
  }

Key differences from Milestone and Hikvision:
  - Uses "plate" (not "licensePlate" or "plateText")
  - Uses "conf" (abbreviated, not "score" or "detectionConfidence")
  - Uses "time" as UNIX integer (not ISO-8601 string)
  - Uses "channel" (not "deviceId" or "cameraIndex")
  - Uses "class" (not "vehicleClass" or "vehicleCategory")

City cameras are busier (2–8 s) — more inner-city traffic density.
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

from model3_federation.adapters.base import VMSAdapter
from model3_federation.schemas.models import FederatedCamera, FederatedEvent

_SYSTEM_ID = "a1000003-0000-0000-0000-000000000003"

_PLATES = [
    "GJ01AB0001", "GJ07PQ4444", "GJ01CD9876", "GJ12EF3344",
    "GJ15GH5566", "GJ19IJ7788", "GJ23KL9900",
]
_WATCHLISTED_PLATE = "GJ01CD5678"

_CAMERAS = [
    FederatedCamera(
        external_id="amc-lal-01",
        name="Lal Darwaja Intersection",
        system_name="AMC City Surveillance (Dahua)",
        vendor="Dahua",
        department="Municipal",
        lat=23.0227, lng=72.5868,
        location_label="Lal Darwaja, Ahmedabad",
    ),
    FederatedCamera(
        external_id="amc-brts-01",
        name="BRTS Kalupur",
        system_name="AMC City Surveillance (Dahua)",
        vendor="Dahua",
        department="Municipal",
        lat=23.0290, lng=72.5987,
        location_label="Kalupur Railway Station BRTS",
    ),
    FederatedCamera(
        external_id="amc-mani-01",
        name="Maninagar Market",
        system_name="AMC City Surveillance (Dahua)",
        vendor="Dahua",
        department="Municipal",
        lat=22.9972, lng=72.6059,
        location_label="Maninagar, Ahmedabad",
    ),
    FederatedCamera(
        external_id="amc-cgrd-01",
        name="CG Road Flyover",
        system_name="AMC City Surveillance (Dahua)",
        vendor="Dahua",
        department="Municipal",
        lat=23.0389, lng=72.5566,
        location_label="CG Road, Ahmedabad",
    ),
]


class MunicipalVMSAdapter(VMSAdapter):
    """Simulated Dahua DSS-style Municipal Corporation VMS adapter."""

    def __init__(self) -> None:
        self._event_counter = 0

    @property
    def system_name(self) -> str:
        return "AMC City Surveillance (Dahua)"

    @property
    def vendor(self) -> str:
        return "Dahua"

    @property
    def system_id(self) -> str:
        return _SYSTEM_ID

    async def connect(self) -> bool:
        self.log_info("Simulated Dahua DSS WebSocket connection established.")
        return True

    async def get_cameras(self) -> list[FederatedCamera]:
        return list(_CAMERAS)

    async def start_event_stream(
        self,
        callback: Callable[[FederatedEvent], Awaitable[None]],
    ) -> None:
        """Emits city intersection events every 2–8 seconds (busiest traffic)."""
        self.log_info("Event stream started.")
        while True:
            try:
                await asyncio.sleep(random.uniform(2.0, 8.0))
                self._event_counter += 1

                camera = random.choice(_CAMERAS)

                if self._event_counter % 20 == 0:
                    plate = _WATCHLISTED_PLATE
                    confidence = round(random.uniform(0.85, 0.99), 2)
                else:
                    plate = random.choice(_PLATES)
                    confidence = round(random.uniform(0.68, 0.95), 2)

                vehicle_cls = random.choice(["car", "car", "car", "motorcycle", "truck"])

                # Raw payload exactly as Dahua DSS WebSocket would push it
                raw: dict = {
                    "type":    "ANPR",
                    "plate":   plate,                     # Dahua: "plate" (not licensePlate/plateText)
                    "channel": camera.external_id,        # Dahua: "channel" (not deviceId/cameraIndex)
                    "time":    int(time.time()),           # Dahua: UNIX integer (not ISO string)
                    "conf":    confidence,                 # Dahua: abbreviated "conf" (not score/detectionConfidence)
                    "class":   vehicle_cls,               # Dahua: "class" (not vehicleClass/vehicleCategory)
                }

                # Translate Dahua fields → FederatedEvent canonical fields
                event = FederatedEvent(
                    system_id=self.system_id,
                    system_name=self.system_name,
                    vendor=self.vendor,
                    camera_external_id=raw["channel"],    # ← Dahua uses "channel"
                    camera_name=camera.name,
                    event_type="vehicle_detection",
                    detected_plate=raw["plate"],          # ← Dahua uses "plate"
                    confidence=raw["conf"],               # ← Dahua uses abbreviated "conf"
                    vehicle_type=raw["class"],            # ← Dahua uses "class"
                    source_timestamp=datetime.fromtimestamp(  # ← Dahua uses UNIX int, not ISO string
                        raw["time"], tz=timezone.utc
                    ),
                    raw_payload=raw,
                )
                await callback(event)

            except asyncio.CancelledError:
                self.log_info("Event stream cancelled.")
                raise
            except Exception as exc:
                self.log_error(f"Unexpected error in event stream: {exc}")
                await asyncio.sleep(2.0)
