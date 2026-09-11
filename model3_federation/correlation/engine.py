"""
model3_federation.correlation.engine
--------------------------------------
Cross-system correlation engine — the intelligence layer of Model 3.

Writes to the same shared tables Model 1/2 already use (shared/db/schema.sql)
instead of a model3-private schema — see model3_federation/registration.py's
docstring for why. Concretely, that means:

For every FederatedEvent arriving on the bus:
  1. Resolve the `cameras` row for (vms_system_id, camera_external_id).
  2. Resolve-or-create a `vehicle_tracks` row for the plate, using the
     same deterministic id (uuid5 of the normalised plate) that
     model2_analytics/pipeline/tracking/associator_interface.py's
     TrackAssociatorStub uses — so a plate resolves to the *same*
     track whether Model 2's real analytics pipeline or a Model 3
     federated adapter saw it first.
  3. Write to `detections` (full audit trail, camera_id + vehicle_track_id).
  4. Watchlist check: query vehicles_watchlist WHERE plate = normalize(plate).
       -> If match: INSERT alerts, broadcast alert via WebSocket.
  5. Cross-system correlation: query `detections` for the same
     vehicle_track_id seen via a *different* vms_system_id within the
     last 30 minutes. No separate correlation_results table — this is
     always derivable from detections + cameras + vms_systems, so it's
     computed here (and again on demand by the API router) rather than
     cached in a table that could drift from the raw detections.
  6. Broadcast event (+ any alert/correlation) to /ws/federation WebSocket.
  7. Deduplication: same plate + same camera_external_id + within 60 s = skip.

The engine runs as a single asyncio background task. It uses the
shared DB session in thread-executor (synchronous SQLAlchemy calls
run in a thread pool) to avoid blocking the event loop.

WebSocket broadcast: the engine receives an async callback `ws_broadcast`
that it calls with a WSMessage. The API router owns the actual WS
connection registry and provides this callback at startup.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from model3_federation.bus.event_bus import FederationEventBus
from model3_federation.schemas.models import (
    FederatedAlert,
    FederatedEvent,
    CorrelationResult,
    WSMessage,
)

logger = logging.getLogger("sentinel.federation.engine")


def _normalize_plate(plate: Optional[str]) -> Optional[str]:
    """Uppercase, strip spaces and hyphens — matches how the watchlist is stored."""
    if not plate:
        return None
    return re.sub(r"[\s\-]", "", plate.upper())


def _track_id_for_plate(plate_norm: str) -> str:
    """
    Same derivation as model2_analytics's TrackAssociatorStub.associate():
    a deterministic UUID from the plate, so the same plate always
    resolves to the same vehicle_tracks row no matter which pipeline
    (Model 2's real analytics or a Model 3 federated adapter) sees it
    first — that's how cross-system correlation and Model 2's own
    cross-camera correlation end up being the same mechanism.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, plate_norm))


class CorrelationEngine:
    """
    Subscribes to the FederationEventBus and processes every incoming event.

    Parameters
    ----------
    bus:
        FederationEventBus to subscribe to.
    db_session_factory:
        Callable returning a synchronous SQLAlchemy Session.
        Engine calls it in a thread executor to avoid blocking the event loop.
    ws_broadcast:
        Async callback to push WSMessage to all connected WebSocket clients.
        Provided by the API router at startup.
    """

    def __init__(
        self,
        bus: FederationEventBus,
        db_session_factory: Callable,
        ws_broadcast: Callable[[WSMessage], Awaitable[None]],
    ) -> None:
        self._bus = bus
        self._db_session_factory = db_session_factory
        self._ws_broadcast = ws_broadcast

        # Dedup cache: (plate, camera_external_id) → last_seen UTC
        self._dedup: dict[tuple[str, str], datetime] = {}
        self._dedup_ttl = timedelta(seconds=60)

    # ── Lifecycle ───────────────────────────────────────────────

    async def start(self) -> None:
        """Subscribe to bus and process events indefinitely."""
        logger.info("Correlation engine started.")
        await self._bus.subscribe(self.on_event)

    # ── Main event handler ──────────────────────────────────────

    async def on_event(self, event: FederatedEvent) -> None:
        """Called for every FederatedEvent arriving on the bus."""
        try:
            # 1. Deduplication check
            plate_norm = _normalize_plate(event.detected_plate)
            if plate_norm:
                dedup_key = (plate_norm, event.camera_external_id)
                last = self._dedup.get(dedup_key)
                now = datetime.now(tz=timezone.utc)
                if last and (now - last) < self._dedup_ttl:
                    logger.debug("Dedup skip: plate=%s camera=%s", plate_norm, event.camera_external_id)
                    return
                self._dedup[dedup_key] = now
                # Prune stale dedup entries (keep memory bounded)
                if len(self._dedup) > 10_000:
                    cutoff = now - self._dedup_ttl * 5
                    self._dedup = {k: v for k, v in self._dedup.items() if v > cutoff}

            # 2. Persist event to DB + watchlist check + correlation (thread executor)
            loop = asyncio.get_running_loop()
            db_result = await loop.run_in_executor(
                None,
                self._process_in_db,
                event,
                plate_norm,
            )

            alert: Optional[FederatedAlert] = db_result.get("alert")
            correlation: Optional[CorrelationResult] = db_result.get("correlation")

            # 3. Broadcast event to WebSocket clients
            await self._ws_broadcast(WSMessage(
                type="event",
                payload=event.model_dump(),
            ))

            # 4. Broadcast alert if generated
            if alert:
                logger.warning(
                    "WATCHLIST HIT: plate=%s system=%s camera=%s",
                    alert.plate_number, alert.system_name, alert.camera_name,
                )
                await self._ws_broadcast(WSMessage(
                    type="alert",
                    payload=alert.model_dump(),
                ))

            # 5. Broadcast correlation if generated/updated
            if correlation:
                logger.info(
                    "CORRELATION: plate=%s systems=%s",
                    correlation.plate_number, correlation.systems_involved,
                )
                await self._ws_broadcast(WSMessage(
                    type="correlation",
                    payload=correlation.model_dump(),
                ))

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Engine error processing event %s: %s", event.id, exc, exc_info=True)

    # ── DB operations (run in thread executor) ──────────────────

    def _process_in_db(
        self,
        event: FederatedEvent,
        plate_norm: Optional[str],
    ) -> dict:
        """
        All synchronous DB work for one event, executed in a thread pool.
        Returns dict with optional 'alert' and 'correlation' keys.
        """
        result: dict = {}

        session = None
        try:
            session = self._db_session_factory()

            from sqlalchemy import text
            import json as _json

            # ── 2a. Look up the camera row for this system + external_id ──
            cam_row = session.execute(text(
                "SELECT id FROM cameras WHERE vms_system_id = :sys AND source_grid_id = :ext LIMIT 1"
            ), {"sys": event.system_id, "ext": event.camera_external_id}).fetchone()

            if cam_row is None:
                # Adapter reported an event for a camera we never registered.
                # Skip the DB write rather than violating detections.camera_id's
                # NOT NULL/FK constraint with a made-up id.
                logger.warning(
                    "No camera row for system=%s external_id=%s — skipping DB write for event %s",
                    event.system_id, event.camera_external_id, event.id,
                )
                return result
            cam_id = str(cam_row[0])

            # ── 2b. Resolve-or-create the vehicle_tracks row ──────
            track_id: Optional[str] = None
            if plate_norm:
                track_id = _track_id_for_plate(plate_norm)
                session.execute(text(
                    """
                    INSERT INTO vehicle_tracks (id, plate_number, vehicle_type, first_seen, last_seen)
                    VALUES (:id, :plate, :vtype, :ts, :ts)
                    ON CONFLICT (id) DO UPDATE
                    SET last_seen = EXCLUDED.last_seen,
                        plate_number = COALESCE(vehicle_tracks.plate_number, EXCLUDED.plate_number)
                    """
                ), {
                    "id": track_id,
                    "plate": plate_norm,
                    "vtype": event.vehicle_type,
                    "ts": event.received_at,
                })

            # ── 2c. Watchlist check (done before the detections insert
            #        so we know is_watchlisted before writing the row) ──
            watchlist_id: Optional[str] = None
            if plate_norm:
                wl_row = session.execute(text(
                    "SELECT id FROM vehicles_watchlist "
                    "WHERE plate_number = :p AND status = 'active' LIMIT 1"
                ), {"p": plate_norm}).fetchone()
                if wl_row:
                    watchlist_id = str(wl_row[0])
                    if track_id:
                        session.execute(text(
                            "UPDATE vehicle_tracks SET is_watchlisted = true WHERE id = :id"
                        ), {"id": track_id})

            # ── 2d. Persist to detections ──────────────────────────
            detection_id = str(uuid.uuid4())
            session.execute(text(
                """
                INSERT INTO detections
                  (id, camera_id, "timestamp", event_type, detected_plate,
                   vehicle_type, confidence, cropped_image_path,
                   raw_payload, source_timestamp, vehicle_track_id)
                VALUES
                  (:id, :cam, :ts, :etype, :plate,
                   :vtype, :conf, :snap,
                   :raw::jsonb, :src, :track)
                """
            ), {
                "id":    detection_id,
                "cam":   cam_id,
                "ts":    event.received_at,
                "etype": event.event_type,
                "plate": plate_norm,
                "vtype": event.vehicle_type,
                "conf":  event.confidence,
                "snap":  event.snapshot_url,
                "raw":   _json.dumps(event.raw_payload),
                "src":   event.source_timestamp,
                "track": track_id,
            })

            # ── 2e. Alert on watchlist hit ─────────────────────────
            if watchlist_id:
                session.execute(text(
                    """
                    INSERT INTO alerts (detection_id, watchlist_id, alert_type, severity)
                    VALUES (:det, :wl, 'federated_vehicle_match', 'high')
                    """
                ), {"det": detection_id, "wl": watchlist_id})

                result["alert"] = FederatedAlert(
                    event_id=detection_id,
                    plate_number=plate_norm,
                    system_name=event.system_name,
                    camera_name=event.camera_name,
                    severity="high",
                )

            # ── 2f. Cross-system correlation (computed, not stored) ─
            if track_id:
                window_start = event.received_at - timedelta(minutes=30)
                other_rows = session.execute(text(
                    """
                    SELECT d.id, c.vms_system_id, d."timestamp",
                           c.name AS camera_name,
                           vs.name AS system_name,
                           ST_Y(c.location::geometry) AS lat,
                           ST_X(c.location::geometry) AS lng
                    FROM   detections d
                    JOIN   cameras c ON c.id = d.camera_id
                    LEFT JOIN vms_systems vs ON vs.id = c.vms_system_id
                    WHERE  d.vehicle_track_id = :track
                      AND  c.vms_system_id IS DISTINCT FROM :this_sys
                      AND  d."timestamp" >= :win
                    ORDER  BY d."timestamp" ASC
                    LIMIT  20
                    """
                ), {"track": track_id, "this_sys": event.system_id, "win": window_start}).fetchall()

                if other_rows:
                    sys_names = list({event.system_name} | {r[4] for r in other_rows if r[4]})

                    seq = []
                    for r in other_rows:
                        seq.append({
                            "camera_name": r[3] or "Unknown",
                            "system_name": r[4],
                            "timestamp":   r[2].isoformat() if r[2] else None,
                            "lat":         float(r[5]) if r[5] is not None else None,
                            "lng":         float(r[6]) if r[6] is not None else None,
                        })
                    # Add current event at the end
                    seq.append({
                        "camera_name": event.camera_name,
                        "system_name": event.system_name,
                        "timestamp":   event.received_at.isoformat(),
                        "lat":         None,
                        "lng":         None,
                    })

                    first_seen = other_rows[0][2] if other_rows else event.received_at
                    travel_secs = int((event.received_at - first_seen).total_seconds())

                    result["correlation"] = CorrelationResult(
                        id=track_id,
                        plate_number=plate_norm,
                        systems_involved=sys_names,
                        first_seen=first_seen,
                        last_seen=event.received_at,
                        travel_time_secs=travel_secs,
                        camera_sequence=seq,
                        is_watchlisted=watchlist_id is not None,
                    )

            session.commit()

        except Exception as exc:
            logger.error("DB error for event %s: %s", event.id, exc, exc_info=True)
            if session:
                try:
                    session.rollback()
                except Exception:
                    pass
        finally:
            if session:
                try:
                    session.close()
                except Exception:
                    pass

        return result
