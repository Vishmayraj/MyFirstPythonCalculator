"""
model3_federation.adapters.base
---------------------------------
Abstract base class for all VMS vendor adapters.

Each concrete adapter:
  - Knows how to connect to its VMS (or simulate one for the demo).
  - Enumerates its camera inventory.
  - Streams normalised FederatedEvent objects by calling a callback.
  - Translates vendor-specific field names into the canonical schema
    so the federation layer sees a uniform event regardless of vendor.

For real production use a concrete adapter would open an SDK socket,
REST polling loop, or ONVIF subscription. For the hackathon demo the
adapters use asyncio.sleep + random generation to produce realistic
event traffic without needing access to real government VMS endpoints.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from model3_federation.schemas.models import FederatedCamera, FederatedEvent

logger = logging.getLogger("sentinel.federation.adapter")


class VMSAdapter(ABC):
    """
    Abstract base for all VMS vendor adapters.

    Subclasses must implement:
      - connect()          → establish (or simulate) a VMS connection
      - get_cameras()      → return the list of cameras in this VMS
      - start_event_stream(callback) → run indefinitely, calling callback per event
      - system_name        → human label for this VMS
      - vendor             → vendor name (Milestone | Hikvision | Dahua | …)
      - system_id          → the UUID string of the corresponding federated_systems row
    """

    # ── Abstract interface ─────────────────────────────────────

    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to the VMS.
        Returns True on success, False on failure.
        For simulated adapters always returns True.
        """
        ...

    @abstractmethod
    async def get_cameras(self) -> list[FederatedCamera]:
        """Return the list of cameras managed by this VMS."""
        ...

    @abstractmethod
    async def start_event_stream(
        self,
        callback: Callable[[FederatedEvent], Awaitable[None]],
    ) -> None:
        """
        Run indefinitely, calling callback() each time a detection event arrives.

        For demo adapters: asyncio.sleep loop with randomised intervals.
        For real adapters: open SDK/REST/WebSocket connection to VMS.

        Must not raise on transient errors — log and retry instead.
        """
        ...

    @property
    @abstractmethod
    def system_name(self) -> str:
        """Human-readable label for this VMS, e.g. 'Gujarat Police VMS (Milestone)'."""
        ...

    @property
    @abstractmethod
    def vendor(self) -> str:
        """Vendor name as stored in federated_systems.vendor."""
        ...

    @property
    @abstractmethod
    def system_id(self) -> str:
        """
        The UUID string of the federated_systems DB row for this adapter.
        Must match the hardcoded UUIDs in federation_seed.sql so that events
        written to federated_events FK correctly.
        """
        ...

    # ── Shared helpers ─────────────────────────────────────────

    def log_info(self, msg: str) -> None:
        logger.info("[%s] %s", self.system_name, msg)

    def log_warning(self, msg: str) -> None:
        logger.warning("[%s] %s", self.system_name, msg)

    def log_error(self, msg: str) -> None:
        logger.error("[%s] %s", self.system_name, msg)
