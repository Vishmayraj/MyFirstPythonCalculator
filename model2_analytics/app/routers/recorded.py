"""
Model 2 — Pre-Recorded Video AI Detection API & WebSocket Router
================================================================
Endpoints:
  GET  /api/v1/recorded/cameras        List active cameras for UI location association
  POST /api/v1/recorded/upload         Upload video file (.mp4, .avi, .mov, .mkv) with metadata probing
  POST /api/v1/recorded/start          Start video worker for a job
  POST /api/v1/recorded/pause          Pause processing
  POST /api/v1/recorded/resume         Resume processing
  POST /api/v1/recorded/stop           Stop processing
  GET  /api/v1/recorded/status/{job_id} Query current job status
  WS   /ws/recorded/{job_id}           Real-time WebSocket stream (video frames, boxes, sightings, progress)
"""

import asyncio
import json
import logging
import os
import re
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set

import cv2
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.auth.dependencies import get_current_user, require_role
from pipeline.video_worker import PreRecordedVideoWorker
from shared.db.models import Camera as CameraModel, User as UserModel
from shared.db.session import get_db

logger = logging.getLogger("sentinel.recorded")
logger.setLevel(logging.INFO)

router = APIRouter(tags=["recorded-detection"])

# ── Upload directory resolution ──────────────────────────────────
UPLOADS_DIR = Path("/app/model2_analytics/uploads")
if not UPLOADS_DIR.exists():
    UPLOADS_DIR = Path(__file__).resolve().parents[2] / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}

# ── Module-level state & WebSocket hub ───────────────────────────
_JOBS: Dict[str, PreRecordedVideoWorker] = {}
_JOBS_META: Dict[str, Dict] = {}
_JOB_WS: Dict[str, Set[WebSocket]] = defaultdict(set)
_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_db():
    import shared.db.session as _s
    return _s._SessionLocal() if _s._SessionLocal else None


# ── Thread-safe event callback from PreRecordedVideoWorker ────────
def on_recorded_worker_event(payload: Dict):
    data = payload.get("data", {})
    job_id = data.get("job_id")
    if not job_id:
        return

    if _loop and not _loop.is_closed() and _loop.is_running():
        asyncio.run_coroutine_threadsafe(_broadcast_job_event(job_id, payload), _loop)


async def _broadcast_job_event(job_id: str, payload: Dict):
    clients = _JOB_WS.get(job_id, set())
    if not clients:
        return

    dead: Set[WebSocket] = set()
    text_data = json.dumps(payload)
    for ws in list(clients):
        try:
            await ws.send_text(text_data)
        except Exception:
            dead.add(ws)

    if dead:
        clients.difference_update(dead)


# ── Pydantic Request Models ───────────────────────────────────────
class JobControlRequest(BaseModel):
    job_id: str
    speed: Optional[str] = "1x"


# ── 1. Cameras for Association ────────────────────────────────────
@router.get("/api/v1/recorded/cameras")
def get_cameras_for_association(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """Returns active cameras with their departments and locations for user selection."""
    cameras = (
        db.query(CameraModel)
        .options(joinedload(CameraModel.department), joinedload(CameraModel.district))
        .filter(CameraModel.is_active.is_(True))
        .order_by(CameraModel.name)
        .all()
    )
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "source_grid_id": c.source_grid_id,
            "location_label": c.location_label or (c.district.name if c.district else "Gujarat"),
            "department_name": c.department.name if c.department else "Traffic Department",
            "connectivity_status": c.connectivity_status,
        }
        for c in cameras
    ]


# ── 2. Video Upload & Probing ─────────────────────────────────────
@router.post("/api/v1/recorded/upload")
async def upload_recorded_video(
    request: Request,
    file: UploadFile = File(...),
    camera_id: str = Form(...),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    """
    Accepts video upload, validates format and size, stores file,
    probes video metadata (duration, FPS, resolution, total frames),
    and initializes job record.
    """
    filename = file.filename or "upload.mp4"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    try:
        cam_uuid = uuid.UUID(camera_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid camera_id UUID format")

    cam = db.query(CameraModel).filter(CameraModel.id == cam_uuid).first()
    if not cam:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selected camera does not exist in registry")

    job_id = str(uuid.uuid4())
    clean_filename = re.sub(r"[^\w\-.]", "_", filename)
    target_path = UPLOADS_DIR / f"{job_id}_{clean_filename}"

    # Stream file to disk with 2 GB size enforcement
    total_bytes = 0
    try:
        with open(target_path, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_SIZE:
                    target_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Video file exceeds maximum permitted size of 2 GB",
                    )
                buffer.write(chunk)
    except Exception as e:
        target_path.unlink(missing_ok=True)
        if isinstance(e, HTTPException):
            raise e
        logger.error(f"Failed to save upload: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to write video file: {str(e)}")

    # Probe video metadata using OpenCV
    cap = cv2.VideoCapture(str(target_path))
    if not cap.isOpened():
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Invalid or unreadable video file")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720
    cap.release()

    duration_s = (total_frames / fps) if fps > 0 else 0.0

    meta = {
        "job_id": job_id,
        "filename": filename,
        "saved_path": str(target_path),
        "file_size_mb": round(total_bytes / (1024 * 1024), 2),
        "camera_id": str(cam.id),
        "camera_name": cam.name,
        "width": width,
        "height": height,
        "fps": round(fps, 1),
        "total_frames": total_frames,
        "duration_s": round(duration_s, 1),
        "uploaded_by": current_user.username,
        "state": "ready",
    }
    _JOBS_META[job_id] = meta

    return {
        "status": "ok",
        "job_id": job_id,
        "metadata": meta,
    }


# ── 3. Start Video Processing ─────────────────────────────────────
@router.post("/api/v1/recorded/start")
def start_recorded_job(
    req: JobControlRequest,
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    job_id = req.job_id
    meta = _JOBS_META.get(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job not found. Upload video first.")

    # Stop any existing worker for this job
    existing = _JOBS.get(job_id)
    if existing and existing.is_running:
        existing.stop()

    worker = PreRecordedVideoWorker(
        job_id=job_id,
        file_path=meta["saved_path"],
        camera_uuid=uuid.UUID(meta["camera_id"]),
        camera_name=meta["camera_name"],
        speed=req.speed or "1x",
        event_callback=on_recorded_worker_event,
        db_session_factory=_get_db,
    )
    _JOBS[job_id] = worker
    meta["state"] = "running"
    worker.start()

    return {"status": "ok", "job_id": job_id, "state": "running", "speed": worker.speed}


# ── 4. Pause Processing ───────────────────────────────────────────
@router.post("/api/v1/recorded/pause")
def pause_recorded_job(
    req: JobControlRequest,
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    worker = _JOBS.get(req.job_id)
    if not worker or not worker.is_running:
        raise HTTPException(status_code=400, detail="Job is not actively running")

    worker.pause()
    return {"status": "ok", "job_id": req.job_id, "state": worker.state}


# ── 5. Resume Processing ──────────────────────────────────────────
@router.post("/api/v1/recorded/resume")
def resume_recorded_job(
    req: JobControlRequest,
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    worker = _JOBS.get(req.job_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Job worker not found")

    worker.resume()
    return {"status": "ok", "job_id": req.job_id, "state": worker.state}


# ── 6. Stop Processing ────────────────────────────────────────────
@router.post("/api/v1/recorded/stop")
def stop_recorded_job(
    req: JobControlRequest,
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    worker = _JOBS.get(req.job_id)
    if worker:
        worker.stop()
    meta = _JOBS_META.get(req.job_id)
    if meta:
        meta["state"] = "stopped"

    return {"status": "ok", "job_id": req.job_id, "state": "stopped"}


# ── 7. Query Job Status ───────────────────────────────────────────
@router.get("/api/v1/recorded/status/{job_id}")
def get_recorded_job_status(
    job_id: str,
    current_user: UserModel = Depends(get_current_user),
):
    meta = _JOBS_META.get(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job not found")

    worker = _JOBS.get(job_id)
    return {
        "status": "ok",
        "job_id": job_id,
        "metadata": meta,
        "state": worker.state if worker else meta.get("state", "ready"),
        "current_frame": worker.current_frame if worker else 0,
        "total_frames": worker.total_frames if worker else meta.get("total_frames", 0),
        "total_detections": worker.total_detections if worker else 0,
        "processing_fps": worker.processing_fps if worker else 0.0,
    }


# ── 8. WebSocket Stream ───────────────────────────────────────────
@router.websocket("/ws/recorded/{job_id}")
async def ws_recorded_feed(
    websocket: WebSocket,
    job_id: str,
    current_user: UserModel = Depends(get_current_user),
):
    global _loop
    _loop = asyncio.get_running_loop()

    await websocket.accept()
    _JOB_WS[job_id].add(websocket)
    logger.info(f"[{job_id}] WebSocket client connected. Active: {len(_JOB_WS[job_id])}")

    # Send initial state handshake if job exists
    meta = _JOBS_META.get(job_id)
    worker = _JOBS.get(job_id)
    if meta:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "JOB_META",
                    "data": {
                        "job_id": job_id,
                        "metadata": meta,
                        "state": worker.state if worker else meta.get("state", "ready"),
                    },
                }
            )
        )

    try:
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _JOB_WS[job_id].discard(websocket)
        logger.info(f"[{job_id}] WebSocket client disconnected. Remaining: {len(_JOB_WS[job_id])}")
