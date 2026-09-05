"""
Sentinel - Model 1 Registry & GIS
FastAPI application entry point.

# Boots the app, mounts routers, configures templates and static files.
# Run with:  uvicorn app.main:app --reload

"""

import asyncio
import logging
import os
import queue
import sys
import importlib.util as _ilu
from contextlib import asynccontextmanager
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Make `shared` importable when running locally
current_dir = Path(__file__).resolve().parent
local_repo_root = current_dir.parent.parent
if (local_repo_root / "shared").exists() and str(local_repo_root) not in sys.path:
    sys.path.insert(0, str(local_repo_root))

from app.auth.dependencies import get_current_user  # noqa: E402
from app.config import settings  # noqa: E402
from app.routers import audit, auth, cameras, departments, districts, gap_analysis, pages, streams  # noqa: E402
from shared.db.models import User as UserModel  # noqa: E402
from model2_analytics.app.ingestion.supervisor import IngestionSupervisor  # noqa: E402
from model2_analytics.app.ingestion.catalogue import (  # noqa: E402
    CataloguePoller,
    upsert_cameras_to_db,
    register_stream_in_mediamtx,
)
from shared.db.session import init_engine  # noqa: E402


async def _sync_cameras(supervisor: IngestionSupervisor, cameras, mediamtx_api: str) -> None:
    """
    Upsert cameras to DB (real UUIDs), sync workers, then register new
    streams in MediaMTX.
    """
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, upsert_cameras_to_db, cameras)
    running_before = set(supervisor._workers.keys())
    supervisor.sync(rows)
    for row in rows:
        grid_id = row.get("source_grid_id") or str(row.get("id"))
        rtsp_url = row.get("rtsp_url")
        if grid_id not in running_before and row.get("is_live") and rtsp_url:
            asyncio.create_task(
                register_stream_in_mediamtx(
                    mediamtx_api=mediamtx_api,
                    stream_name=grid_id,
                    rtsp_source_url=rtsp_url,
                )
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine(settings.DATABASE_URL)

    # Wire VMS ingestion layer (Task 7)
    frame_queue = queue.Queue(maxsize=500)
    app.state.frame_queue = frame_queue  # exact attribute name, analytics pipeline reads this

    supervisor = IngestionSupervisor(output_queue=frame_queue, mediamtx_api=settings.MEDIAMTX_API)
    app.state.supervisor = supervisor

    poller = CataloguePoller(grid_host=settings.GRID_HOST)
    app.state.poller = poller

    disable_ingestion = os.environ.get("DISABLE_INGESTION", "false").lower() == "true"

    # DISABLE_INGESTION only turns off RTSP/MediaMTX registration - the
    # catalogue poll itself still runs (and still writes to the DB via
    # _db_only_sync below) so the registry stays in sync even with
    # streaming off. That's fine in normal operation, but it's wrong
    # during automated tests: CataloguePoller.fetch() makes a real HTTPS
    # call to the live grid host on every app startup, retries with
    # exponential backoff (2s -> 30s) whenever that call fails or times
    # out - which it always does with no network access, e.g. in CI or
    # any sandboxed/offline environment - and its fallback path still
    # writes cam01..cam30 to the `cameras` table through its own DB
    # session, outside of and concurrently with whatever transaction a
    # test is using. Against the isolated-per-test SAVEPOINT setup in
    # tests/conftest.py, that write can block on a lock the test already
    # holds on the very same seeded rows - which looks exactly like
    # `pytest` hanging/freezing for no visible reason. DISABLE_CATALOGUE_POLL
    # (set by tests/conftest.py before any TestClient is created) skips
    # starting this task entirely; it's unset (poll runs normally) for
    # every real deployment, including docker-compose.
    disable_catalogue_poll = os.environ.get("DISABLE_CATALOGUE_POLL", "false").lower() == "true"

    poll_task = None
    if not disable_catalogue_poll:
        if disable_ingestion:
            async def _db_only_sync(cams):
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, upsert_cameras_to_db, cams)

            poll_task = asyncio.create_task(
                poller.poll_forever(callback=_db_only_sync)
            )
        else:
            poll_task = asyncio.create_task(
                poller.poll_forever(
                    callback=lambda cams: _sync_cameras(supervisor, cams, settings.MEDIAMTX_API)
                )
            )

    yield

    if poll_task is not None:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
    supervisor.stop_all()


app = FastAPI(
    title="Sentinel - Registry & GIS",
    description="Model 1: Camera registry, GIS mapping, and department/district management for Gujarat's CCTV network.",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Templates & static ──────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Model-2 detection-image directory (cropped vehicle/plate images from the
# detection pipeline). NOT a plain StaticFiles mount (AuditReport1.md
# finding 1.5) — a FastAPI Depends() can't be attached directly to a
# StaticFiles mount, so this wraps the same directory in an explicit route
# that requires a logged-in user and rejects path traversal before ever
# touching the filesystem, instead of serving every file to anyone who can
# guess a filename.
DETECTION_IMG_DIR = Path("/model2-analytics/detection-image")
if not DETECTION_IMG_DIR.exists():
    DETECTION_IMG_DIR = Path(__file__).resolve().parents[2] / "model2-analytics" / "detection-image"
DETECTION_IMG_DIR.mkdir(parents=True, exist_ok=True)
_DETECTION_IMG_DIR_RESOLVED = DETECTION_IMG_DIR.resolve()


@app.get("/detection-image/{file_path:path}", name="detection-image")
async def get_detection_image(
    file_path: str,
    current_user: UserModel = Depends(get_current_user),
):
    """Serve a detection-pipeline crop image, but only to a logged-in user."""
    requested = (_DETECTION_IMG_DIR_RESOLVED / file_path).resolve()
    try:
        requested.relative_to(_DETECTION_IMG_DIR_RESOLVED)
    except ValueError:
        # Path escapes DETECTION_IMG_DIR (e.g. "../../etc/passwd") — treat
        # exactly like "not found" rather than confirming it exists elsewhere.
        raise HTTPException(status_code=404, detail="Not found")
    if not requested.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(requested))

# ── Model 1 Routers ──────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(audit.router)
app.include_router(cameras.router)
app.include_router(streams.router)
app.include_router(streams.streams_router)
app.include_router(departments.router)
app.include_router(districts.router)
app.include_router(gap_analysis.router)
app.include_router(pages.router)

# ── Model 2 Routers (auto-discovery) ─────────────────────────────
_M2_ROUTERS_DIR_CANDIDATES = [
    Path("/model2-analytics/app/routers"),                              # Docker
    local_repo_root / "model2-analytics" / "app" / "routers",          # Local dev
]
_m2_routers_dir = next((p for p in _M2_ROUTERS_DIR_CANDIDATES if p.is_dir()), None)

if _m2_routers_dir:
    for _router_file in sorted(_m2_routers_dir.glob("*.py")):
        if _router_file.name.startswith("_"):
            continue
        try:
            _spec = _ilu.spec_from_file_location(
                f"model2.routers.{_router_file.stem}", _router_file
            )
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            if hasattr(_mod, "router"):
                app.include_router(_mod.router)
                print(f"[model2] mounted : {_router_file.name}")
            else:
                print(f"[model2] skipped  : {_router_file.name}  (no `router` attribute)")
        except Exception as _exc:
            print(f"[model2] ERROR    : {_router_file.name}  -> {_exc}")
else:
    print("[model2] routers directory not found - Model 2 endpoints unavailable.")
