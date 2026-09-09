"""
model3_federation.correlation.engine
--------------------------------------
Cross-system correlation engine — the intelligence layer of Model 3.

For every FederatedEvent arriving on the bus:
  1. Write to `federated_events` table (full audit trail).
  2. Watchlist check: query vehicles_watchlist WHERE plate = normalize(plate).
       → If match: INSERT federated_alerts, broadcast alert via WebSocket.
  3. Cross-system correlation:
       → Query federated_events WHERE detected_plate = this plate
          AND system_id != this system AND received_at > now() - 30 min.
       → If found: INSERT/UPDATE correlation_results, broadcast correlation.
  4. Broadcast event (+ any alert/correlation) to /ws/federation WebSocket.
  5. Deduplication: same plate + same camera_external_id + within 60 s = skip.

The engine runs as a single asyncio background task. It uses the
shared DB session in thread-executor (synchronous SQLAlchemy ORM calls
run in a thread pool) to avoid blocking the event loop.

WebSocket broadcast: the engine receives an async callback `ws_broadcast`
that it calls with a WSMessage. The API router owns the actual WS
connection registry and provides this callback at startup.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional
from uuid import uuid4

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

        try:
            from shared.db.session import get_db_direct
        except ImportError:
            # get_db_direct may not exist yet; try alternate import path
            try:
                from shared.db.session import SessionLocal as _SL
                def get_db_direct():
                    return _SL()
            except ImportError:
                logger.warning("DB session not available — skipping DB write for event %s", event.id)
                return result

        session = None
        try:
            session = self._db_session_factory()

            # ── 2a. Persist to federated_events ─────────────────
            from sqlalchemy import text

            # Look up the federated_camera UUID from external_id + system_id
            cam_row = session.execute(text(
                "SELECT id FROM federated_cameras "
                "WHERE system_id = :sys AND external_id = :ext LIMIT 1"
            ), {"sys": event.system_id, "ext": event.camera_external_id}).fetchone()
            cam_id = str(cam_row[0]) if cam_row else None

            import json as _json
            session.execute(text(
                """
                INSERT INTO federated_events
                  (system_id, camera_id, event_type, detected_plate, confidence,
                   vehicle_type, snapshot_url, raw_payload, received_at, source_timestamp)
                VALUES
                  (:sys, :cam, :etype, :plate, :conf,
                   :vtype, :snap, :raw::jsonb, :recv, :src)
                """
            ), {
                "sys":   event.system_id,
                "cam":   cam_id,
                "etype": event.event_type,
                "plate": plate_norm,
                "conf":  event.confidence,
                "vtype": event.vehicle_type,
                "snap":  event.snapshot_url,
                "raw":   _json.dumps(event.raw_payload),
                "recv":  event.received_at,
                "src":   event.source_timestamp,
            })

            # ── 2b. Watchlist check ───────────────────────────────
            if plate_norm:
                wl_row = session.execute(text(
                    "SELECT id FROM vehicles_watchlist "
                    "WHERE plate_number = :p AND status = 'active' LIMIT 1"
                ), {"p": plate_norm}).fetchone()

                if wl_row:
                    # Fetch the just-inserted event row ID
                    ev_row = session.execute(text(
                        "SELECT id FROM federated_events "
                        "WHERE system_id = :sys AND detected_plate = :p "
                        "ORDER BY received_at DESC LIMIT 1"
                    ), {"sys": event.system_id, "p": plate_norm}).fetchone()

                    ev_db_id = str(ev_row[0]) if ev_row else str(uuid4())

                    session.execute(text(
                        """
                        INSERT INTO federated_alerts
                          (event_id, watchlist_id, system_id, severity, alert_type)
                        VALUES
                          (:ev, :wl, :sys, 'high', 'federated_vehicle_match')
                        """
                    ), {"ev": ev_db_id, "wl": str(wl_row[0]), "sys": event.system_id})

                    result["alert"] = FederatedAlert(
                        event_id=ev_db_id,
                        plate_number=plate_norm,
                        system_name=event.system_name,
                        camera_name=event.camera_name,
                        severity="high",
                    )

            # ── 2c. Cross-system correlation ──────────────────────
            if plate_norm:
                window_start = event.received_at - timedelta(minutes=30)
                other_rows = session.execute(text(
                    """
                    SELECT fe.id, fe.system_id, fe.camera_id, fe.received_at,
                           fc.name AS camera_name,
                           ST_Y(fc.location::geometry) AS lat,
                           ST_X(fc.location::geometry) AS lng
                    FROM   federated_events fe
                    LEFT JOIN federated_cameras fc ON fc.id = fe.camera_id
                    WHERE  fe.detected_plate = :p
                      AND  fe.system_id != :sys
                      AND  fe.received_at >= :win
                    ORDER  BY fe.received_at ASC
                    LIMIT  20
                    """
                ), {"p": plate_norm, "sys": event.system_id, "win": window_start}).fetchall()

                if other_rows:
                    all_system_ids = list({event.system_id} | {str(r[1]) for r in other_rows})
                    all_event_ids  = [str(r[0]) for r in other_rows]

                    seq = []
                    for r in other_rows:
                        seq.append({
                            "camera_name": r[4] or "Unknown",
                            "system_id":   str(r[1]),
                            "timestamp":   r[3].isoformat() if r[3] else None,
                            "lat":         float(r[5]) if r[5] else None,
                            "lng":         float(r[6]) if r[6] else None,
                        })
                    # Add current event at the end
                    cam_info = cam_row  # already fetched above
                    seq.append({
                        "camera_name": event.camera_name,
                        "system_id":   event.system_id,
                        "timestamp":   event.received_at.isoformat(),
                        "lat":         None,
                        "lng":         None,
                    })

                    first_seen = other_rows[0][3] if other_rows else event.received_at
                    travel_secs = int((event.received_at - first_seen).total_seconds())

                    import json as _json2
                    # Upsert correlation_results by plate_number
                    existing = session.execute(text(
                        "SELECT id FROM correlation_results WHERE plate_number = :p LIMIT 1"
                    ), {"p": plate_norm}).fetchone()

                    if existing:
                        session.execute(text(
                            """
                            UPDATE correlation_results
                            SET last_seen        = :last,
                                travel_time_secs = :tt,
                                camera_sequence  = :seq::jsonb,
                                updated_at       = now()
                            WHERE id = :id
                            """
                        ), {
                            "last": event.received_at,
                            "tt":   travel_secs,
                            "seq":  _json2.dumps(seq),
                            "id":   str(existing[0]),
                        })
                        corr_id = str(existing[0])
                    else:
                        corr_id = str(uuid4())
                        session.execute(text(
                            """
                            INSERT INTO correlation_results
                              (id, plate_number, event_ids, system_ids,
                               first_seen, last_seen, travel_time_secs,
                               camera_sequence, is_watchlisted)
                            VALUES
                              (:id, :p, :eids, :sids,
                               :first, :last, :tt,
                               :seq::jsonb, :wl)
                            """
                        ), {
                            "id":    corr_id,
                            "p":     plate_norm,
                            "eids":  all_event_ids,
                            "sids":  all_system_ids,
                            "first": first_seen,
                            "last":  event.received_at,
                            "tt":    travel_secs,
                            "seq":   _json2.dumps(seq),
                            "wl":    result.get("alert") is not None,
                        })

                    # Resolve system names for the result object
                    sys_name_rows = session.execute(text(
                        "SELECT name FROM federated_systems WHERE id = ANY(:ids)"
                    ), {"ids": all_system_ids}).fetchall()
                    sys_names = [r[0] for r in sys_name_rows] if sys_name_rows else all_system_ids

                    result["correlation"] = CorrelationResult(
                        id=corr_id,
                        plate_number=plate_norm,
                        systems_involved=sys_names,
                        first_seen=first_seen,
                        last_seen=event.received_at,
                        travel_time_secs=travel_secs,
                        camera_sequence=seq,
                        is_watchlisted=result.get("alert") is not None,
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
