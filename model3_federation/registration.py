"""
model3_federation.registration
---------------------------------
Registers a connected VMS adapter's identity and camera inventory into
the shared cameras/vms_systems tables at startup.

Why this writes to the *shared* tables (shared/db/schema.sql) instead
of a model3-private schema: a department isn't guaranteed to run
exactly one VMS, and a VMS isn't guaranteed to be government-owned —
so "one VMS system" needed its own row (vms_systems), but the cameras
it reports are still just cameras. Model 1's own camera grid already
has a `cameras` table with department/location/status columns; giving
model3 a second, parallel `federated_cameras` table would mean the
same concept (a camera) lived in two places depending on which part
of the system happened to write it. Every camera model3 reports now
lands in that same `cameras` table, tagged with `vms_system_id` so
it's traceable to whichever VMS reported it — NULL vms_system_id
means "from the main grid or manually onboarded", not "federated".

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
from typing import Callable, Optional, Sequence

from model3_federation.adapters.base import VMSAdapter
from model3_federation.schemas.models import FederatedCamera

logger = logging.getLogger("sentinel.federation.registration")


def _resolve_department_id(session, department_hint: Optional[str]) -> Optional[str]:
    """
    Match an adapter's plain-English department hint (e.g. "Police") against
    departments.name/category. Returns None on no match rather than guessing —
    cameras.department_id has no NOT NULL constraint, so leaving it unset is
    safe and honest; picking an arbitrary department would not be.
    """
    if not department_hint:
        return None
    from sqlalchemy import text

    row = session.execute(text(
        "SELECT id FROM departments WHERE name ILIKE :pat OR category ILIKE :pat LIMIT 1"
    ), {"pat": f"%{department_hint}%"}).fetchone()
    return str(row[0]) if row else None


def _upsert_system_and_cameras(
    db_session_factory: Callable,
    adapter: VMSAdapter,
    cameras: Sequence[FederatedCamera],
    ownership: str,
) -> None:
    """Synchronous DB work — run in a thread executor, same pattern as the correlation engine."""
    from sqlalchemy import text

    session = db_session_factory()
    try:
        department_id = _resolve_department_id(
            session, cameras[0].department if cameras else None
        )

        session.execute(text(
            """
            INSERT INTO vms_systems
              (id, name, vendor, protocol, ownership, department_id, status, camera_count, last_heartbeat)
            VALUES
              (:id, :name, :vendor, 'simulated', :ownership, :dept, 'connected', :count, now())
            ON CONFLICT (id) DO UPDATE
            SET status         = 'connected',
                department_id  = COALESCE(vms_systems.department_id, EXCLUDED.department_id),
                camera_count   = :count,
                last_heartbeat = now()
            """
        ), {
            "id": adapter.system_id,
            "name": adapter.system_name,
            "vendor": adapter.vendor,
            "ownership": ownership,
            "dept": department_id,
            "count": len(cameras),
        })

        for cam in cameras:
            if cam.lat is not None and cam.lng is not None:
                location_sql = "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::GEOGRAPHY"
            else:
                location_sql = "NULL"

            session.execute(text(
                f"""
                INSERT INTO cameras
                  (name, source_grid_id, vms_system_id, department_id,
                   location, location_label, ownership, connectivity_status, is_active)
                VALUES
                  (:name, :ext, :sys, :dept,
                   {location_sql}, :label, :ownership, 'online', :active)
                ON CONFLICT (vms_system_id, source_grid_id) DO UPDATE
                SET name                = :name,
                    location            = {location_sql},
                    location_label      = :label,
                    connectivity_status = 'online',
                    is_active           = :active
                """
            ), {
                "sys": adapter.system_id,
                "ext": cam.external_id,
                "name": cam.name,
                "dept": department_id,
                "lat": cam.lat,
                "lng": cam.lng,
                "label": cam.location_label,
                "ownership": ownership,
                "active": cam.is_active,
            })

        session.commit()
        logger.info(
            "Registered %s (%d cameras) into cameras/vms_systems.",
            adapter.system_name, len(cameras),
        )
    except Exception:
        session.rollback()
        logger.error("Failed to register adapter %s", adapter.system_name, exc_info=True)
        raise
    finally:
        session.close()


async def register_adapter(
    db_session_factory: Callable,
    adapter: VMSAdapter,
    ownership: str = "government",
) -> None:
    """
    Ask a connected adapter for its camera inventory and upsert its
    system + camera rows into the shared cameras/vms_systems tables.
    Call this once per adapter after adapter.connect() succeeds and
    before starting its event stream, so the correlation engine's
    camera lookups resolve.

    `ownership` defaults to "government" since Police/RTO/Municipal are
    all government systems today; pass ownership="private" when a
    future adapter represents a private-vendor VMS instead (a mall or
    society's own system Sentinel has been given read access to).
    """
    cameras = await adapter.get_cameras()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, _upsert_system_and_cameras, db_session_factory, adapter, cameras, ownership
    )
