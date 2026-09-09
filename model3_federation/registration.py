"""
model3_federation.registration
---------------------------------
Registers a connected VMS adapter's identity and camera inventory into
the federation tables at startup.

Why this exists instead of a seed file: each adapter (police/rto/muni)
already knows its own system_id, system_name, vendor, and camera list —
that's what get_cameras() and the system_* properties on VMSAdapter are
for. federation_seed.sql used to duplicate all of that as hand-written
SQL literals with the same UUIDs, names, and coordinates. Two sources
of truth for the same data drift apart silently — a camera renamed or
added in an adapter's Python code just would not show up in the DB
until someone remembered to also edit the SQL file, and vice versa.

An adapter is meant to be Model 3's interface to an *external* VMS —
the external system owns its own camera list, we're just reporting
what it told us. So at startup we ask each adapter what it has and
upsert exactly that, instead of pre-loading fabricated inventory (or,
as federation_seed.sql also did, fabricated historical detection
events and correlations) into the database.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Sequence

from model3_federation.adapters.base import VMSAdapter
from model3_federation.schemas.models import FederatedCamera

logger = logging.getLogger("sentinel.federation.registration")


def _upsert_system_and_cameras(
    db_session_factory: Callable,
    adapter: VMSAdapter,
    cameras: Sequence[FederatedCamera],
) -> None:
    """Synchronous DB work — run in a thread executor, same pattern as the correlation engine."""
    from sqlalchemy import text

    session = db_session_factory()
    try:
        session.execute(text(
            """
            INSERT INTO federated_systems
              (id, name, vendor, protocol, status, camera_count, last_heartbeat)
            VALUES
              (:id, :name, :vendor, 'simulated', 'connected', :count, now())
            ON CONFLICT (id) DO UPDATE
            SET status         = 'connected',
                camera_count   = :count,
                last_heartbeat = now()
            """
        ), {
            "id": adapter.system_id,
            "name": adapter.system_name,
            "vendor": adapter.vendor,
            "count": len(cameras),
        })

        for cam in cameras:
            if cam.lat is not None and cam.lng is not None:
                location_sql = "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::GEOGRAPHY"
            else:
                location_sql = "NULL"

            session.execute(text(
                f"""
                INSERT INTO federated_cameras
                  (system_id, external_id, name, location, location_label, is_active)
                VALUES
                  (:sys, :ext, :name, {location_sql}, :label, :active)
                ON CONFLICT (system_id, external_id) DO UPDATE
                SET name           = :name,
                    location       = {location_sql},
                    location_label = :label,
                    is_active      = :active
                """
            ), {
                "sys": adapter.system_id,
                "ext": cam.external_id,
                "name": cam.name,
                "lat": cam.lat,
                "lng": cam.lng,
                "label": cam.location_label,
                "active": cam.is_active,
            })

        session.commit()
        logger.info(
            "Registered %s (%d cameras) into federation tables.",
            adapter.system_name, len(cameras),
        )
    except Exception:
        session.rollback()
        logger.error("Failed to register adapter %s", adapter.system_name, exc_info=True)
        raise
    finally:
        session.close()


async def register_adapter(db_session_factory: Callable, adapter: VMSAdapter) -> None:
    """
    Ask a connected adapter for its camera inventory and upsert its
    system + camera rows into the DB. Call this once per adapter after
    adapter.connect() succeeds and before starting its event stream, so
    federated_events.camera_id lookups in the correlation engine resolve.
    """
    cameras = await adapter.get_cameras()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _upsert_system_and_cameras, db_session_factory, adapter, cameras)
