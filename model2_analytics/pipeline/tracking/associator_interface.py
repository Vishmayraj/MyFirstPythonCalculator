"""
Phase 2 — Pluggable Track Associator Interface
===============================================
Cross-camera Re-ID abstraction.
Future embedding-based models implement TrackAssociatorInterface and swap in.
"""

import uuid
from typing import Optional


class TrackAssociatorInterface:
    """Abstract interface — all cross-camera Re-ID engines must implement this."""

    def associate(
        self,
        camera_id: str,
        timestamp,
        vehicle_crop,     # numpy BGR crop
        plate: Optional[str] = None,
    ) -> Optional[uuid.UUID]:
        """
        Attempt to associate a vehicle sighting with an existing vehicle_track.
        Returns a vehicle_track UUID if matched, else None.
        """
        raise NotImplementedError


class TrackAssociatorStub(TrackAssociatorInterface):
    """
    Pluggable Re-ID Stub: returns consistent global vehicle_track_id based on plate/feature seed.
    Future visual Re-ID / embedding models implement TrackAssociatorInterface and swap in seamlessly.
    """

    def associate(self, camera_id, timestamp, vehicle_crop, plate=None) -> Optional[uuid.UUID]:
        key = plate or f"{camera_id}_{timestamp}"
        return uuid.uuid5(uuid.NAMESPACE_DNS, str(key))
