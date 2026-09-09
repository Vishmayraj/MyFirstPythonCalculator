"""
Phase 4 — Detection API & WebSocket Router
==========================================
Endpoints:
  GET  /api/v1/detection/events    Recent confirmed vehicle sightings (memory + DB fallback)
  GET  /api/v1/detections          Paginated detection history from DB
  GET  /api/v1/detections/stats    Live counts & active tracker state
  WS   /ws/detections              Real-time push of new detection events
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Set

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from app.auth.dependencies import get_current_user, require_role
from shared.db.models import User as UserModel

logger = logging.getLogger("sentinel.detections")
logger.setLevel(logging.INFO)

router = APIRouter(tags=["detections"])

# ── In-memory event ring-buffer & WS hub ─────────────────────────
ACTIVE_WS: Set[WebSocket] = set()
RECENT_EVENTS: List[Dict] = []
MAX_EVENTS = 50
_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_db():
    import shared.db.session as _s
    return _s._SessionLocal() if _s._SessionLocal else None


# ── Called from background worker thread ─────────────────────────
def on_detection_event(payload: Dict):
    """Thread-safe: stores confirmed events in ring-buffer and broadcasts over WS."""
    msg_type = payload.get("type", "NEW_DETECTION")
    if msg_type == "NEW_DETECTION":
        RECENT_EVENTS.insert(0, payload)
        if len(RECENT_EVENTS) > MAX_EVENTS:
            RECENT_EVENTS.pop()

    if _loop and not _loop.is_closed() and _loop.is_running():
        asyncio.run_coroutine_threadsafe(_broadcast(payload), _loop)


async def _broadcast(payload: Dict):
    dead: Set[WebSocket] = set()
    msg_type = payload.get("type", "NEW_DETECTION")
    ws_text = json.dumps({"type": msg_type, "data": payload})
    for ws in list(ACTIVE_WS):
        try:
            await ws.send_text(ws_text)
        except Exception:
            dead.add(ws)
    ACTIVE_WS.difference_update(dead)


# ── Initialize pipeline runner (On-demand mode) ────────────────────
try:
    from pipeline.runner import MultiStreamPipelineRunner
    _RUNNER = MultiStreamPipelineRunner(
        event_callback     = on_detection_event,
        db_session_factory = _get_db,
    )
    # Runner starts ONLY when the /detections page opens!
    logger.info("Pipeline runner initialized in on-demand mode (starts on /detections visit).")
except Exception as _e:
    _RUNNER = None
    logger.warning(f"Pipeline runner not initialized: {_e}")


# ── REST — Recent events (memory + DB fallback) ───────────────────
@router.get("/api/v1/detection/events")
def recent_events(current_user: UserModel = Depends(get_current_user)):
    if not RECENT_EVENTS:
        db = _get_db()
        if db:
            try:
                rows = db.execute(text("""
                    SELECT d.id, c.source_grid_id, d."timestamp",
                           d.detected_plate, d.confidence, c.name
                    FROM detections d
                    JOIN cameras c ON d.camera_id = c.id
                    ORDER BY d."timestamp" DESC
                    LIMIT 20
                """)).fetchall()
                for r in rows:
                    sid = str(r[1] or "")
                    RECENT_EVENTS.append({
                        "detection_id":   str(r[0]),
                        "camera_tag":     "cam04" if sid == "4" else "cam22",
                        "timestamp":      r[2].strftime("%H:%M:%S") if r[2] else "",
                        "detected_plate": r[3],
                        "confidence":     round((r[4] or 0) * 100, 1),
                        "camera_name":    r[5],
                    })
            except Exception as e:
                logger.warning(f"events DB fallback failed: {e}")
            finally:
                db.close()

    return {"status": "ok", "count": len(RECENT_EVENTS), "events": RECENT_EVENTS}


# ── REST — Paginated history ──────────────────────────────────────
@router.get("/api/v1/detections")
def detection_history(
    camera_tag: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: UserModel = Depends(get_current_user),
):
    db = _get_db()
    if not db:
        return {"status": "error", "message": "DB unavailable", "detections": []}

    try:
        offset = (page - 1) * page_size
        grid_id = "4" if camera_tag == "cam04" else ("22" if camera_tag == "cam22" else None)
        # Fixed, non-attacker-influenced SQL text either way -- `grid_id` is
        # never interpolated into the query, only bound via :g below. Kept
        # as two complete, literal query strings (rather than an f-string
        # splicing a WHERE fragment into the query text) so a future change
        # can't accidentally turn this into a real injection point by
        # building `where` from anything less fixed than this boolean.
        # See AuditReport2.md finding 9.
        if grid_id:
            base_query = """
                SELECT d.id, c.source_grid_id, d."timestamp",
                       d.detected_plate, d.confidence, d.cropped_image_path, c.name, c.location_label,
                       COALESCE(vt.vehicle_type, 'Vehicle') as vehicle_type,
                       d.vehicle_track_id
                FROM detections d
                JOIN cameras c ON d.camera_id = c.id
                LEFT JOIN vehicle_tracks vt ON d.vehicle_track_id = vt.id
                WHERE c.source_grid_id = :g
                ORDER BY d."timestamp" DESC
                LIMIT :limit OFFSET :offset
            """
            count_query = """
                SELECT COUNT(*) FROM detections d
                JOIN cameras c ON d.camera_id = c.id
                WHERE c.source_grid_id = :g
            """
            params: Dict = {"limit": page_size, "offset": offset, "g": grid_id}
            count_params = {"g": grid_id}
        else:
            base_query = """
                SELECT d.id, c.source_grid_id, d."timestamp",
                       d.detected_plate, d.confidence, d.cropped_image_path, c.name, c.location_label,
                       COALESCE(vt.vehicle_type, 'Vehicle') as vehicle_type,
                       d.vehicle_track_id
                FROM detections d
                JOIN cameras c ON d.camera_id = c.id
                LEFT JOIN vehicle_tracks vt ON d.vehicle_track_id = vt.id
                ORDER BY d."timestamp" DESC
                LIMIT :limit OFFSET :offset
            """
            count_query = "SELECT COUNT(*) FROM detections d JOIN cameras c ON d.camera_id = c.id"
            params = {"limit": page_size, "offset": offset}
            count_params = {}

        rows = db.execute(text(base_query), params).fetchall()
        total = db.execute(text(count_query), count_params).scalar() or 0

        detections = []
        for r in rows:
            sid = str(r[1] or "")
            detections.append({
                "id":               str(r[0]),
                "camera_tag":       "cam04" if sid == "4" else "cam22",
                "timestamp":        r[2].strftime("%Y-%m-%d %H:%M:%S") if r[2] else "",
                "detected_plate":   r[3] or "—",
                "confidence":       round((r[4] or 0) * 100, 1),
                "crop_path":        r[5],
                "camera_name":      r[6],
                "location_label":   r[7],
                "vehicle_type":     r[8] or "Vehicle",
                "vehicle_track_id": str(r[9]) if r[9] else "—",
            })

        return {
            "status": "ok", "total": total, "page": page, "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "detections": detections,
        }
    except Exception as e:
        # Log the real exception server-side; never echo it back to the
        # caller (could leak schema/column names or query structure).
        # See AuditReport2.md finding 8.
        logger.error(f"history query failed: {e}")
        return {"status": "error", "message": "Failed to load detection history", "detections": []}
    finally:
        db.close()


# ── REST — Live stats ─────────────────────────────────────────────
@router.get("/api/v1/detections/stats")
def detection_stats(current_user: UserModel = Depends(get_current_user)):
    stats = {
        "total_today": 0,
        "total_all_time": 0,
        "active_tracks": {"cam04": 0, "cam22": 0},
        "pipeline_running": _RUNNER is not None,
    }

    if _RUNNER:
        for tag, worker in _RUNNER.workers.items():
            stats["active_tracks"][tag] = len(worker.tracker.active_tracks)

    db = _get_db()
    if db:
        try:
            row = db.execute(text("""
                SELECT
                  COUNT(*) FILTER (WHERE "timestamp"::date = CURRENT_DATE),
                  COUNT(*)
                FROM detections
            """)).fetchone()
            if row:
                stats["total_today"]    = int(row[0] or 0)
                stats["total_all_time"] = int(row[1] or 0)
        except Exception as e:
            logger.warning(f"stats query failed: {e}")
        finally:
            db.close()

    return {"status": "ok", "stats": stats}


# ── WebSocket — real-time push (Controls Runner Lifecycle) ────────
@router.websocket("/ws/detections")
async def ws_detections(
    websocket: WebSocket,
    current_user: UserModel = Depends(get_current_user),
):
    global _loop
    _loop = asyncio.get_running_loop()

    await websocket.accept()
    ACTIVE_WS.add(websocket)

    # Start detection on-demand when client arrives on Detection page
    if _RUNNER and not _RUNNER.is_running:
        logger.info("🟢 Client connected to Detection page — starting camera workers...")
        _RUNNER.start_all()

    try:
        # Send last 15 events as backlog on connect
        await websocket.send_text(json.dumps({"type": "INIT_BACKLOG", "data": RECENT_EVENTS[:15]}))
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        ACTIVE_WS.discard(websocket)
        # When all clients leave Detection page, stop workers to save CPU and stop background writes
        if _RUNNER and len(ACTIVE_WS) == 0:
            logger.info("⏸️ All clients left Detection page — stopping camera workers.")
            _RUNNER.stop_all()


# ── REST — Manual Start/Stop Controls ─────────────────────────────
@router.post("/api/v1/detections/start")
def start_detection(
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    if _RUNNER:
        _RUNNER.start_all()
        return {"status": "ok", "message": "Detection started"}
    return {"status": "error", "message": "Runner not available"}


@router.post("/api/v1/detections/stop")
def stop_detection(
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    if _RUNNER:
        _RUNNER.stop_all()
        return {"status": "ok", "message": "Detection stopped"}
    return {"status": "error", "message": "Runner not available"}
