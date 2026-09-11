"""
model3_federation.api.router
------------------------------
FastAPI router for the Model 3 Federation layer.

Mounts at /api/v3 in model1-registry/app/main.py (one added line).
Also provides the WebSocket endpoint /ws/federation used by the
federation.html dashboard.

At startup (lifespan hook in main.py) the three VMS adapters are
started as asyncio background tasks and the correlation engine is
wired in. This router also manages the WebSocket connection registry
and provides the ws_broadcast() callback to the engine.

All REST endpoints are read-only GET requests (plus one POST
for acknowledge and one POST for simulate-burst). No existing
Model 1 routes, models, or schemas are modified.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_role
from shared.db.models import User as UserModel
from shared.db.session import get_db
from model3_federation.bus.event_bus import FederationEventBus
from model3_federation.correlation.engine import CorrelationEngine
from model3_federation.registration import register_adapter
from model3_federation.adapters.police_vms_adapter import PoliceVMSAdapter
from model3_federation.adapters.rto_vms_adapter import RTOVMSAdapter
from model3_federation.adapters.municipal_vms_adapter import MunicipalVMSAdapter
from model3_federation.schemas.models import FederatedEvent, WSMessage

logger = logging.getLogger("sentinel.federation.api")

router = APIRouter(prefix="/api/v3", tags=["federation"])

# ── Module-level singletons (initialised in start_federation_services) ─────

_bus: Optional[FederationEventBus] = None
_engine: Optional[CorrelationEngine] = None
_adapters: list = []
_tasks: list[asyncio.Task] = []

# WebSocket connection registry: set of active WebSocket clients
_ws_clients: set[WebSocket] = set()

# In-memory rate counter: events per minute per system (for topology cards)
_events_per_min: dict[str, list[datetime]] = {}


# ── WebSocket broadcast helper ───────────────────────────────────────────────

async def _ws_broadcast(message: WSMessage) -> None:
    """Broadcast a WSMessage to all connected WebSocket clients."""
    dead: set[WebSocket] = set()
    payload = message.model_dump_json()
    for ws in list(_ws_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


# ── Rate tracker helper ──────────────────────────────────────────────────────

def _record_event_rate(system_id: str) -> None:
    now = datetime.now(tz=timezone.utc)
    bucket = _events_per_min.setdefault(system_id, [])
    bucket.append(now)
    # Keep only the last 60 seconds
    cutoff = now - timedelta(seconds=60)
    _events_per_min[system_id] = [t for t in bucket if t >= cutoff]


# ── Federation lifecycle ─────────────────────────────────────────────────────

def _on_task_done(task: asyncio.Task) -> None:
    """
    Log a background task's failure without touching its siblings.
    Each adapter and the correlation engine run as independent
    fire-and-forget tasks — one dying (e.g. a single bad VMS adapter)
    should not take down the others.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Federation task %r crashed: %s", task.get_name(), exc, exc_info=exc)


async def start_federation_services(db_session_factory, redis_url: str) -> None:
    """
    Called from model1-registry/app/main.py lifespan on startup.
    Initialises the event bus, correlation engine, and all three VMS adapters,
    registers each adapter's reported system/camera inventory into the DB,
    then starts each as its own independent background task and returns —
    it does not block waiting on them. Call stop_federation_services() on
    shutdown to cancel everything cleanly.
    """
    global _bus, _engine, _adapters, _tasks

    _bus = FederationEventBus(redis_url=redis_url)

    # Wrap bus publish to also track event rate per system
    _original_publish = _bus.publish

    async def _tracked_publish(event: FederatedEvent) -> None:
        _record_event_rate(event.system_id)
        await _original_publish(event)

    _bus.publish = _tracked_publish  # type: ignore[method-assign]

    _engine = CorrelationEngine(
        bus=_bus,
        db_session_factory=db_session_factory,
        ws_broadcast=_ws_broadcast,
    )

    _adapters = [PoliceVMSAdapter(), RTOVMSAdapter(), MunicipalVMSAdapter()]
    _tasks = []

    engine_task = asyncio.create_task(_engine.start(), name="federation-engine")
    engine_task.add_done_callback(_on_task_done)
    _tasks.append(engine_task)

    # Connect, register, and start each adapter's event stream independently.
    for adapter in _adapters:
        connected = await adapter.connect()
        if not connected:
            logger.error("Adapter failed to connect: %s", adapter.system_name)
            continue

        await register_adapter(db_session_factory, adapter)

        task = asyncio.create_task(
            adapter.start_event_stream(_bus.publish),
            name=f"federation-adapter-{adapter.vendor}",
        )
        task.add_done_callback(_on_task_done)
        _tasks.append(task)
        logger.info("Adapter started: %s", adapter.system_name)

    logger.info("Model 3 Federation services started. %d adapters running.", len(_adapters))


async def stop_federation_services() -> None:
    """Cancel all federation background tasks. Called from main.py lifespan on shutdown."""
    global _tasks
    for task in _tasks:
        task.cancel()
    for task in _tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            # Already logged by _on_task_done; swallow here so shutdown proceeds.
            pass
    _tasks = []


# ── WebSocket endpoint ───────────────────────────────────────────────────────

@router.websocket("/ws/federation")
async def ws_federation(
    websocket: WebSocket,
    current_user: UserModel = Depends(get_current_user),
):
    """
    WebSocket endpoint for the live federation dashboard.
    Pushes FederatedEvent, FederatedAlert, and CorrelationResult objects
    as JSON to every connected browser client.

    Auth follows the same pattern as model2_analytics's /ws/detections:
    get_current_user reads the access_token cookie/header, so only
    logged-in users can open this socket.
    """
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info("WebSocket client connected. Total: %d", len(_ws_clients))

    # Send initial heartbeat so the client knows it's connected
    try:
        await websocket.send_text(json.dumps({
            "type": "heartbeat",
            "payload": {
                "message": "Federation WebSocket connected",
                "adapter_count": len(_adapters),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
        }))
    except Exception:
        pass

    try:
        while True:
            # Keep the connection alive; the engine pushes messages via _ws_broadcast
            await asyncio.sleep(30)
            try:
                await websocket.send_text(json.dumps({
                    "type": "heartbeat",
                    "payload": {"timestamp": datetime.now(tz=timezone.utc).isoformat()}
                }))
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)
        logger.info("WebSocket client disconnected. Total: %d", len(_ws_clients))


# ── REST endpoints ───────────────────────────────────────────────────────────

@router.get("/systems")
def get_federated_systems(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all VMS systems (main grid + federated) with status, camera count, and last heartbeat."""
    rows = db.execute(text(
        """
        SELECT vs.id, vs.name, vs.vendor, vs.status,
               vs.camera_count, vs.last_heartbeat, vs.protocol, vs.ownership,
               d.name AS department_name
        FROM   vms_systems vs
        LEFT JOIN departments d ON d.id = vs.department_id
        ORDER  BY vs.name
        """
    )).fetchall()

    result = []
    for r in rows:
        sys_id = str(r[0])
        epm_bucket = _events_per_min.get(sys_id, [])
        result.append({
            "id":              sys_id,
            "name":            r[1],
            "vendor":          r[2],
            "status":          r[3],
            "camera_count":    r[4] or 0,
            "last_heartbeat":  r[5].isoformat() if r[5] else None,
            "protocol":        r[6],
            "ownership":       r[7],
            "department":      r[8],
            "events_per_min":  len(epm_bucket),
        })
    return result


@router.get("/cameras")
def get_federated_cameras(
    system_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Federated cameras (cameras with a non-null vms_system_id), optionally filtered by system_id."""
    q = """
        SELECT c.id, c.vms_system_id, c.source_grid_id, c.name,
               c.location_label, c.is_active,
               ST_Y(c.location::geometry) AS lat,
               ST_X(c.location::geometry) AS lng,
               vs.name AS system_name, vs.vendor
        FROM   cameras c
        JOIN   vms_systems vs ON vs.id = c.vms_system_id
    """
    params: dict = {}
    if system_id:
        q += " WHERE c.vms_system_id = :sys"
        params["sys"] = system_id
    q += " ORDER BY vs.name, c.name"

    rows = db.execute(text(q), params).fetchall()
    return [
        {
            "id":             str(r[0]),
            "system_id":      str(r[1]),
            "external_id":    r[2],
            "name":           r[3],
            "location_label": r[4],
            "is_active":      r[5],
            "lat":            float(r[6]) if r[6] is not None else None,
            "lng":            float(r[7]) if r[7] is not None else None,
            "system_name":    r[8],
            "vendor":         r[9],
        }
        for r in rows
    ]


@router.get("/events")
def get_federated_events(
    system_id: Optional[str] = Query(None),
    plate: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Recent federated detections (cameras with a non-null vms_system_id). Filterable by system_id and plate."""
    q = """
        SELECT d.id, c.vms_system_id, d.event_type, d.detected_plate,
               d.confidence, d.vehicle_type, d."timestamp", d.source_timestamp,
               vs.name AS system_name, vs.vendor,
               c.name AS camera_name
        FROM   detections d
        JOIN   cameras c ON c.id = d.camera_id
        JOIN   vms_systems vs ON vs.id = c.vms_system_id
        WHERE  c.vms_system_id IS NOT NULL
    """
    params: dict = {}
    if system_id:
        q += " AND c.vms_system_id = :sys"
        params["sys"] = system_id
    if plate:
        from model3_federation.correlation.engine import _normalize_plate
        params["plate"] = _normalize_plate(plate)
        q += " AND d.detected_plate = :plate"
    q += " ORDER BY d.\"timestamp\" DESC LIMIT :lim"
    params["lim"] = limit

    rows = db.execute(text(q), params).fetchall()
    return [
        {
            "id":               str(r[0]),
            "system_id":        str(r[1]),
            "event_type":       r[2],
            "detected_plate":   r[3],
            "confidence":       r[4],
            "vehicle_type":     r[5],
            "received_at":      r[6].isoformat() if r[6] else None,
            "source_timestamp": r[7].isoformat() if r[7] else None,
            "system_name":      r[8],
            "vendor":           r[9],
            "camera_name":      r[10],
        }
        for r in rows
    ]


@router.get("/events/stats")
def get_events_stats(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Events per minute per system (live rate from in-memory counter)."""
    rows = db.execute(text(
        "SELECT id, name, vendor FROM vms_systems ORDER BY name"
    )).fetchall()

    return [
        {
            "system_id":      str(r[0]),
            "system_name":    r[1],
            "vendor":         r[2],
            "events_per_min": len(_events_per_min.get(str(r[0]), [])),
        }
        for r in rows
    ]


@router.get("/correlations")
def get_correlations(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """
    Cross-system correlations, most recent first. Computed on the fly from
    detections + vehicle_tracks — there's no stored correlation_results
    table to drift out of sync with the raw sightings (see engine.py's
    module docstring). A "correlation" here is any vehicle_track whose
    detections span more than one distinct vms_system_id.
    """
    rows = db.execute(text(
        """
        SELECT vt.id, vt.plate_number, vt.first_seen, vt.last_seen, vt.is_watchlisted,
               array_agg(DISTINCT vs.name) FILTER (WHERE vs.name IS NOT NULL) AS systems,
               count(DISTINCT c.vms_system_id) AS system_count
        FROM   vehicle_tracks vt
        JOIN   detections d ON d.vehicle_track_id = vt.id
        JOIN   cameras c ON c.id = d.camera_id
        LEFT JOIN vms_systems vs ON vs.id = c.vms_system_id
        WHERE  c.vms_system_id IS NOT NULL
        GROUP  BY vt.id
        HAVING count(DISTINCT c.vms_system_id) > 1
        ORDER  BY vt.last_seen DESC
        LIMIT  :lim
        """
    ), {"lim": limit}).fetchall()

    result = []
    for r in rows:
        travel_secs = int((r[3] - r[2]).total_seconds()) if r[2] and r[3] else None
        result.append({
            "id":               str(r[0]),
            "plate_number":     r[1],
            "systems_involved": r[5] or [],
            "first_seen":       r[2].isoformat() if r[2] else None,
            "last_seen":        r[3].isoformat() if r[3] else None,
            "travel_time_secs": travel_secs,
            "is_watchlisted":   r[4],
        })
    return result


@router.get("/correlations/track")
def track_vehicle(
    plate: str = Query(..., description="Vehicle plate number to track across all VMS systems"),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Full multi-system route for a specific plate number. Now that cameras
    live in one shared table, this naturally covers sightings from both
    the main grid and federated VMS systems, not just federated ones.
    """
    from model3_federation.correlation.engine import _normalize_plate
    plate_norm = _normalize_plate(plate)
    if not plate_norm:
        raise HTTPException(status_code=400, detail="plate parameter is required")

    rows = db.execute(text(
        """
        SELECT d.id, c.vms_system_id, d."timestamp", d.source_timestamp,
               d.confidence, d.vehicle_type,
               c.name AS camera_name, c.location_label,
               ST_Y(c.location::geometry) AS lat,
               ST_X(c.location::geometry) AS lng,
               vs.name AS system_name, vs.vendor
        FROM   detections d
        JOIN   cameras c ON c.id = d.camera_id
        LEFT JOIN vms_systems vs ON vs.id = c.vms_system_id
        WHERE  d.detected_plate = :p
        ORDER  BY d."timestamp" ASC
        LIMIT  100
        """
    ), {"p": plate_norm}).fetchall()

    sightings = [
        {
            "event_id":       str(r[0]),
            "system_id":      str(r[1]) if r[1] else None,
            "received_at":    r[2].isoformat() if r[2] else None,
            "source_timestamp": r[3].isoformat() if r[3] else None,
            "confidence":     r[4],
            "vehicle_type":   r[5],
            "camera_name":    r[6],
            "location_label": r[7],
            "lat":            float(r[8]) if r[8] is not None else None,
            "lng":            float(r[9]) if r[9] is not None else None,
            "system_name":    r[10] or "Sentinel Camera Grid",
            "vendor":         r[11],
        }
        for r in rows
    ]

    return {
        "plate":     plate_norm,
        "sightings": sightings,
        "count":     len(sightings),
    }


@router.get("/alerts")
def get_federated_alerts(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Federation-originated watchlist alerts (from cameras with a non-null vms_system_id), most recent first."""
    rows = db.execute(text(
        """
        SELECT a.id, a.created_at, a.severity, a.alert_type,
               a.acknowledged_at,
               d.detected_plate,
               c.name AS camera_name,
               vs.name AS system_name
        FROM   alerts a
        JOIN   detections d ON d.id = a.detection_id
        JOIN   cameras c ON c.id = d.camera_id
        JOIN   vms_systems vs ON vs.id = c.vms_system_id
        ORDER  BY a.created_at DESC
        LIMIT  :lim
        """
    ), {"lim": limit}).fetchall()

    return [
        {
            "id":             str(r[0]),
            "created_at":     r[1].isoformat() if r[1] else None,
            "severity":       r[2],
            "alert_type":     r[3],
            "acknowledged":   r[4] is not None,
            "plate_number":   r[5],
            "camera_name":    r[6],
            "system_name":    r[7],
        }
        for r in rows
    ]


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
) -> dict[str, str]:
    """Mark a federated alert as acknowledged. Same role gate as model2's write endpoints."""
    result = db.execute(text(
        """
        UPDATE alerts
        SET    acknowledged_at = now(),
               acknowledged_by = :uid
        WHERE  id = :id
          AND  acknowledged_at IS NULL
        RETURNING id
        """
    ), {"id": alert_id, "uid": str(current_user.id)}).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Alert not found or already acknowledged")

    db.commit()
    return {"status": "acknowledged", "alert_id": alert_id}


@router.post("/systems/{system_id}/simulate")
async def simulate_burst(
    system_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Trigger a burst of 10 events from the specified VMS system.
    Used for live demo — gives judges an immediate flood of events to watch.
    """
    if _bus is None:
        raise HTTPException(status_code=503, detail="Federation bus not initialised")

    # Find the adapter for this system_id
    adapter = next(
        (a for a in _adapters if a.system_id == system_id),
        None,
    )
    if adapter is None:
        raise HTTPException(status_code=404, detail=f"No adapter found for system_id={system_id}")

    cameras = await adapter.get_cameras()
    if not cameras:
        raise HTTPException(status_code=404, detail="Adapter returned no cameras")

    import random
    plates = ["GJ05AB1234", "GJ01XX9999", "GJ04ZZ3210", "GJ01CD5678", "GJ03KL5566"]
    now = datetime.now(tz=timezone.utc)

    fired = 0
    for i in range(10):
        cam = random.choice(cameras)
        plate = plates[i % len(plates)]
        event = FederatedEvent(
            system_id=adapter.system_id,
            system_name=adapter.system_name,
            vendor=adapter.vendor,
            camera_external_id=cam.external_id,
            camera_name=cam.name,
            event_type="vehicle_detection",
            detected_plate=plate,
            confidence=round(random.uniform(0.78, 0.99), 2),
            vehicle_type=random.choice(["car", "truck", "motorcycle"]),
            source_timestamp=now,
            raw_payload={"simulated": True, "burst_index": i},
        )
        await _bus.publish(event)
        fired += 1
        await asyncio.sleep(0.05)  # Small delay so WS clients receive them as a stream

    return {"status": "ok", "events_fired": fired, "system": adapter.system_name}
