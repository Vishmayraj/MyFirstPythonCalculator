"""
Phase 3 — Alerts API Router
============================
Endpoints:
  GET  /api/v1/alerts              List alerts (with filtering)
  GET  /api/v1/alerts/stats        Alert statistics (counts by severity)
  PATCH /api/v1/alerts/{id}/ack    Acknowledge an alert
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from shared.db.session import get_db

logger = logging.getLogger("sentinel.alerts_api")
logger.setLevel(logging.INFO)

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


def _row_to_dict(row) -> dict:
    return {
        "id":               str(row[0]) if row[0] else None,
        "detection_id":     str(row[1]) if row[1] else None,
        "watchlist_id":     str(row[2]) if row[2] else None,
        "alert_type":       row[3],
        "severity":         row[4],
        "created_at":       row[5].isoformat() if row[5] else None,
        "acknowledged_by":  str(row[6]) if row[6] else None,
        "acknowledged_at":  row[7].isoformat() if row[7] else None,
        # Enriched fields from joins
        "detected_plate":   row[8]  if len(row) > 8  else None,
        "camera_name":      row[9]  if len(row) > 9  else None,
        "category":         row[10] if len(row) > 10 else None,
        "plate_number_wl":  row[11] if len(row) > 11 else None,
    }


@router.get("")
def list_alerts(
    severity: Optional[str] = Query(None, description="Filter by severity"),
    alert_type: Optional[str] = Query(None, description="Filter by alert_type"),
    acknowledged: Optional[bool] = Query(None, description="Filter acknowledged vs unacknowledged"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List alerts with optional filters."""
    where: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if severity:
        where.append("a.severity = :severity")
        params["severity"] = severity
    if alert_type:
        where.append("a.alert_type = :alert_type")
        params["alert_type"] = alert_type
    if acknowledged is not None:
        if acknowledged:
            where.append("a.acknowledged_by IS NOT NULL")
        else:
            where.append("a.acknowledged_by IS NULL")

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT a.id, a.detection_id, a.watchlist_id, a.alert_type,
               a.severity, a.created_at, a.acknowledged_by, a.acknowledged_at,
               d.detected_plate,
               c.name,
               vw.category, vw.plate_number
        FROM alerts a
        LEFT JOIN detections d ON a.detection_id = d.id
        LEFT JOIN cameras c    ON d.camera_id    = c.id
        LEFT JOIN vehicles_watchlist vw ON a.watchlist_id = vw.id
        {where_clause}
        ORDER BY a.created_at DESC
        LIMIT :limit OFFSET :offset
    """
    try:
        rows = db.execute(text(sql), params).fetchall()
        return {
            "status": "ok",
            "alerts": [_row_to_dict(r) for r in rows],
        }
    except Exception as e:
        logger.error(f"list_alerts query failed: {e}")
        return {"status": "error", "message": str(e), "alerts": []}


@router.get("/stats")
def alert_stats(db: Session = Depends(get_db)):
    """Alert statistics — total, today, by severity."""
    stats = {
        "total": 0,
        "today": 0,
        "unacked": 0,
        "by_severity": {},
    }
    try:
        row = db.execute(text("""
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE),
                COUNT(*) FILTER (WHERE acknowledged_by IS NULL),
                COUNT(*) FILTER (WHERE severity = 'critical' AND acknowledged_by IS NULL),
                COUNT(*) FILTER (WHERE severity = 'high'     AND acknowledged_by IS NULL),
                COUNT(*) FILTER (WHERE severity = 'medium'   AND acknowledged_by IS NULL),
                COUNT(*) FILTER (WHERE severity = 'low'      AND acknowledged_by IS NULL)
            FROM alerts
        """)).fetchone()
        if row:
            stats["total"] = int(row[0] or 0)
            stats["today"] = int(row[1] or 0)
            stats["unacked"] = int(row[2] or 0)
            stats["by_severity"] = {
                "critical": int(row[3] or 0),
                "high":     int(row[4] or 0),
                "medium":   int(row[5] or 0),
                "low":      int(row[6] or 0),
            }
    except Exception as e:
        logger.error(f"alert_stats query failed: {e}")
    return {"status": "ok", "stats": stats}


@router.patch("/{alert_id}/ack")
def acknowledge_alert(
    alert_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Acknowledge an alert (sets acknowledged_by + acknowledged_at)."""
    try:
        row = db.execute(
            text("SELECT id FROM alerts WHERE id = :id"),
            {"id": str(alert_id)},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Alert not found")

        # Use a default system user UUID for acknowledgement (anonymous ack)
        db.execute(
            text("""
                UPDATE alerts
                SET acknowledged_by = '00000000-0000-0000-0000-000000000000',
                    acknowledged_at = now()
                WHERE id = :id
            """),
            {"id": str(alert_id)},
        )
        db.commit()
        return {"status": "ok", "alert_id": str(alert_id), "acknowledged": True}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"acknowledge_alert failed: {e}")
        return {"status": "error", "message": str(e)}