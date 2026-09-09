"""
model3_federation.adapters.police_vms_adapter
----------------------------------------------
Simulated adapter for Gujarat Police VMS (Milestone-style).

Milestone XProtect uses an XML/SDK event push model. The raw payload
schema it sends for LPR events looks like:
  {
    "alarmType":    "LPR",
    "licensePlate": "GJ05AB1234",
    "deviceId":     "cam-pol-01",
    "utcTime":      "2026-09-09T14:00:00Z",
    "score":        0.92,
    "vehicleClass": "car"
  }

This adapter:
  1. Translates Milestone field names → FederatedEvent canonical fields.
  2. Simulates an asyncio event loop with 3–10s random intervals.
  3. Every ~30 events emits a watchlisted plate (GJ01CD5678) to trigger
     a live alert demo for the judges.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import Awaitable, Callable

from model3_federation.adapters.base import VMSAdapter
from model3_federation.schemas.models import FederatedCamera, FederatedEvent

# Stable UUID matching federation_seed.sql
_SYSTEM_ID = "a1000001-0000-0000-0000-000000000001"

# Plates pool — GJ01CD5678 is the watchlisted one (also in shared/db/seed.sql)
_PLATES = [
    "GJ05AB1234", "GJ01XX9999", "GJ04ZZ3210", "GJ03KL5566",
    "GJ18MN7788", "GJ21PQ9900", "GJ07RS1122", "GJ09TU3344",
]
_WATCHLISTED_PLATE = "GJ01CD5678"

_CAMERAS = [
    FederatedCamera(
        external_id="cam-pol-01",
        name="Ahmedabad Police HQ",
        system_name="Gujarat Police VMS (Milestone)",
        vendor="Milestone",
        department="Police",
        lat=23.0225, lng=72.5714,
        location_label="Shahibaug, Ahmedabad",
    ),
    FederatedCamera(
        external_id="cam-pol-02",
        name="Surat Control Room",
        system_name="Gujarat Police VMS (Milestone)",
        vendor="Milestone",
        department="Police",
        lat=21.1702, lng=72.8311,
        location_label="Athwalines, Surat",
    ),
    FederatedCamera(
        external_id="cam-pol-03",
        name="Vadodara Junction",
        system_name="Gujarat Police VMS (Milestone)",
        vendor="Milestone",
        department="Police",
        lat=22.3072, lng=73.2090,
        location_label="Sayajiganj, Vadodara",
    ),
    FederatedCamera(
        external_id="cam-pol-04",
        name="Rajkot Ring Road",
        system_name="Gujarat Police VMS (Milestone)",
        vendor="Milestone",
        department="Police",
        lat=22.3039, lng=70.8022,
        location_label="Race Course Road, Rajkot",
    ),
    FederatedCamera(
        external_id="cam-pol-05",
        name="Gandhinagar Secretariat",
        system_name="Gujarat Police VMS (Milestone)",
        vendor="Milestone",
        department="Police",
        lat=23.2156, lng=72.6849,
        location_label="Sector 10, Gandhinagar",
    ),
]


class PoliceVMSAdapter(VMSAdapter):
    """Simulated Milestone-style Police VMS adapter."""

    def __init__(self) -> None:
        self._event_counter = 0

    @property
    def system_name(self) -> str:
        return "Gujarat Police VMS (Milestone)"

    @property
    def vendor(self) -> str:
        return "Milestone"

    @property
    def system_id(self) -> str:
        return _SYSTEM_ID

    async def connect(self) -> bool:
        self.log_info("Simulated Milestone SDK connection established.")
        return True

    async def get_cameras(self) -> list[FederatedCamera]:
        return list(_CAMERAS)

    async def start_event_stream(
        self,
        callback: Callable[[FederatedEvent], Awaitable[None]],
    ) -> None:
        """
        Emits vehicle detection events every 3–10 seconds.
        Every 30th event uses the watchlisted plate to trigger alert demo.
        """
        self.log_info("Event stream started.")
        while True:
            try:
                await asyncio.sleep(random.uniform(3.0, 10.0))
                self._event_counter += 1

                camera = random.choice(_CAMERAS)

                # Every 30th event: use the watchlisted plate
                if self._event_counter % 30 == 0:
                    plate = _WATCHLISTED_PLATE
                    confidence = round(random.uniform(0.90, 0.99), 2)
                else:
                    plate = random.choice(_PLATES)
                    confidence = round(random.uniform(0.70, 0.98), 2)

                vehicle_class = random.choice(["car", "car", "car", "truck", "motorcycle", "bus"])

                # Raw payload exactly as Milestone would send it
                raw: dict = {
                    "alarmType":    "LPR",
                    "licensePlate": plate,
                    "deviceId":     camera.external_id,
                    "utcTime":      datetime.now(tz=timezone.utc).isoformat(),
                    "score":        confidence,
                    "vehicleClass": vehicle_class,
                }

                # Translate Milestone fields → FederatedEvent canonical fields
                event = FederatedEvent(
                    system_id=self.system_id,
                    system_name=self.system_name,
                    vendor=self.vendor,
                    camera_external_id=camera.external_id,    # Milestone: deviceId
                    camera_name=camera.name,
                    event_type="vehicle_detection",
                    detected_plate=raw["licensePlate"],        # Milestone: licensePlate
                    confidence=raw["score"],                   # Milestone: score
                    vehicle_type=raw["vehicleClass"],          # Milestone: vehicleClass
                    source_timestamp=datetime.fromisoformat(raw["utcTime"]),  # Milestone: utcTime
                    raw_payload=raw,
                )
                await callback(event)

            except asyncio.CancelledError:
                self.log_info("Event stream cancelled.")
                raise
            except Exception as exc:
                self.log_error(f"Unexpected error in event stream: {exc}")
                await asyncio.sleep(2.0)
