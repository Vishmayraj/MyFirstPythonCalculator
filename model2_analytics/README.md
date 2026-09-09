# Model 2 — Live Multi-Camera Grid & Vehicle Analytics

Model 2 handles live CCTV stream viewing (30-camera control-room grid), vehicle detection, license plate recognition (ANPR), cross-camera tracking, watchlist correlation, and real-time alert generation.

Full specification: `Project_Context.md` §4 and `HackathonPortal.md`.

---

## 🚀 Implemented Features

### 1. Live Multi-Camera Grid (Control Room Video Wall)

- **Web UI**: Control-room style video wall at `http://localhost:8000/grid`
- **Matrix Views**: Switch between 2×2, 3×3, and 4×4 camera tile layouts
- **Hover Overlay**: Per-camera details (location, codec, FPS, bitrate) on hover
- **Spotlight Modal**: Click any tile to open full-screen player with copyable RTSP/WHEP/HLS URLs
- **Filters**: Filter camera tiles by department or district
- **REST Endpoints** (`app/routers/grid.py`):
  - `GET /grid` — Serves the live video wall HTML page
  - `GET /api/ingest` — **Hackathon ingestion contract**: returns all 30 cameras with stream properties and RTSP/WHEP/HLS URLs
  - `GET /api/v1/grid/streams` — JSON: active camera list with stream URLs (used by grid UI JavaScript)
  - `POST /api/v1/grid/sync` — Sync external catalogue entries into the local DB

### 2. Vehicle Watchlist System (API & Web UI)

- **Web Dashboard**: Interactive portal at `http://localhost:8000/watchlist`
- **Validation**: Strict Indian license plate normalization & regex (`GJ01AB1234`, `22BH1234AA` BH-series)
- **REST Endpoints** (`app/routers/watchlist.py`):
  - `GET /api/v1/watchlist/vehicles` — Search by plate, category, status, department
  - `POST /api/v1/watchlist/vehicles` — Add target with duplicate protection
  - `GET /api/v1/watchlist/vehicles/{id}` — Fetch watchlist record
  - `PATCH /api/v1/watchlist/vehicles/{id}` — Update status or notes
  - `DELETE /api/v1/watchlist/vehicles/{id}` — Remove target and cascade alerts

### 3. Live AI Vehicle Detection Dashboard & Stream Engine
- **Web Dashboard**: Real-time surveillance at `http://localhost:8000/detection`
- **Indian Traffic YOLOv8**: Specialized model detecting Cars, Auto Rickshaws, Motorcycles, Buses, Trucks, and Mini-Trucks.
- **In-Frame Tracking**: IoU + spatial proximity tracking (`pipeline/tracking/frame_tracker.py`) with smooth EMA bounding boxes.
- **Database Sighting Persistence**: `DetectionWriter` persists confirmed vehicle events into PostgreSQL `detections` & `vehicle_tracks`, saving cropped images to `detection-image/`.
- **Feed Performance & Smoothness Optimizations**:
  - **Zero-Stall Frame Pipeline**: Reduced frame-wait timeout in `StreamIngestClient` (`ingest.py`) from 1.0s to 0.05s, avoiding stalls during slower inferences.
  - **Reflow Elimination**: Replaced per-frame `getBoundingClientRect()` with a `ResizeObserver` dimension cache, eliminating ~50 forced layout reflows/second.
  - **Flicker-Free Boxes**: Optimized bounding box clear timer to 600ms to maintain seamless visuals between inferences.
  - **Low-Latency HLS**: Low-latency HLS configuration (1-segment live sync, 4s max buffer, instant backBuffer purge).
  - **Frame Pacing**: `INFER_EVERY_N_FRAMES = 3` with tracker interpolation, maintaining real-time video sync without drifting on CPU.
  - **WebSocket Ping Leak Fix**: Ensured single active keepalive interval per client connection.
- **REST & WebSocket Endpoints** (`app/routers/detections.py`):
  - `GET /api/v1/detections` — Paginated database sightings log
  - `GET /api/v1/detections/stats` — Real-time detection counters and active track count
  - `WS /ws/detections` — Real-time stream for bounding boxes (`FRAME_BOXES`) and new sightings (`NEW_DETECTION`)

### 4. Pre-Recorded Video AI Detection Pipeline (`/recorded-detection`)
- **Web UI**: Dedicated on-demand portal at `http://localhost:8000/recorded-detection`
- **High-Capacity Ingestion**: Drag-and-drop video upload supporting formats (`.mp4`, `.avi`, `.mov`, `.mkv`, `.webm`) up to **2 GB**.
- **Metadata Probing**: Automatically probes video resolution, native FPS, total frame count, and duration using OpenCV.
- **Target Camera Association**: Allows users to select any camera in the Gujarat CCTV registry to attribute footage to real locations.
- **Isolated Execution**: Runs on a dedicated daemon thread (`PreRecordedVideoWorker`) with zero interference to live camera streams (`cam04`, `cam22`).
- **Interactive Controls**:
  - `▶ Start Detection`, `⏸ Pause` / `▶ Resume`, and `⏹ Stop` controls.
  - Processing speed selector: `1x Realtime`, `2x Fast`, and `Max ⚡`.
- **Real-Time Display**:
  - Synchronized `<canvas>` player rendering video frames with glowing bounding boxes, track IDs, vehicle classes, and confidence scores.
  - Live timeline progress bar with current frame / total frames and processing FPS counter.
  - Category breakdown counters (🚗 Cars, 🛺 Auto Rickshaws, 🚛 Trucks, 🛻 Mini-Trucks, 🚌 Buses, 🛵 Bikes).
  - Live Sighting Audit Report Table with crop image zoom modal and direct database synchronization.
- **REST & WebSocket Endpoints** (`app/routers/recorded.py`):
  - `GET /api/v1/recorded/cameras` — List active cameras for association
  - `POST /api/v1/recorded/upload` — Upload video with 2 GB limit & OpenCV probe
  - `POST /api/v1/recorded/start` — Start isolated video worker
  - `POST /api/v1/recorded/pause`, `/resume`, `/stop` — Worker playback controls
  - `GET /api/v1/recorded/status/{job_id}` — Query current job state & frame progress
  - `WS /ws/recorded/{job_id}` — Real-time WebSocket channel streaming `VIDEO_FRAME`, `FRAME_BOXES`, `NEW_DETECTION`, and `JOB_PROGRESS`

### 5. Person Watchlist & Facial Recognition System (`/watchlist/persons`)
- **Automated 5-Gate Face Quality Checker**:
  - Image integrity & single face verification.
  - Bounding box boundary anti-clipping validation.
  - Sharpness score calculation (Laplacian variance threshold).
  - 3D pose estimation (yaw & roll angular constraints).
  - Mean illumination and contrast evaluation.
- **Deep Face Embedding**: Unit-normalized 512-dimensional vector extraction using InceptionResnetV1 (`pipeline/faceembedding/`).
- **Vector Search & Persistence**: Reference portraits persisted to `uploads/persons/`, embeddings stored in PostgreSQL with pgvector cosine distance indexing.
- **REST Endpoints** (`app/routers/persons_watchlist.py`):
  - `GET /api/v1/watchlist/persons` — Search and filter person watchlist entries
  - `POST /api/v1/watchlist/persons` — Register target individual with photo upload, size check (10 MB max), and 5-gate quality validation
  - `GET /api/v1/watchlist/persons/{id}` — Fetch person details with face quality metrics
  - `PATCH /api/v1/watchlist/persons/{id}` — Update details or mark status resolved
  - `DELETE /api/v1/watchlist/persons/{id}` — Remove person and clean up stored reference portrait
  - `GET /api/v1/watchlist/persons/photos/{filename}` — Protected reference photo retrieval with path traversal defense

---

## 📡 Live Stream Architecture

All 30 camera feeds are provided by the hackathon media gateway. Stream URLs come **dynamically from `/api/ingest`** — never hard-coded.

```
Browser opens /grid
      ↓
JavaScript: fetch('/api/v1/grid/streams')
      ↓
30 camera RTSP/WHEP/HLS URLs from DB
      ↓
<video> per tile → HLS.js attaches HLS stream URL
      ↓
Browser connects DIRECTLY to hackathon gateway:
  http://<GRID_RTSP_HOST>:8889/stream/<id>/whep   (WebRTC WHEP)
  https://cctv.corp8.cloud/<id>/index.m3u8        (HLS)
```

| Protocol | Endpoint Pattern | Host | Used By |
|----------|-----------------|------|---------|
| RTSP | `rtsp://<GRID_RTSP_HOST>:8554/stream/<id>` | `GRID_RTSP_HOST` (default `103.250.160.189`, the grid's public static IP) | AI pipeline (OpenCV) |
| WebRTC WHEP | `http://<GRID_RTSP_HOST>:8889/stream/<id>/whep` | same `GRID_RTSP_HOST` | Browser live preview |
| HLS | `https://cctv.corp8.cloud/<id>/index.m3u8` | `GRID_CDN_HOST` (default `cctv.corp8.cloud`) | Grid video player (HLS.js) |

Built by `_build_stream_urls()` in `app/routers/grid.py` (mirrored in `model1-registry/app/routers/streams.py`) from these three `Settings` fields, so the actual host/port for each protocol always comes from config, not from this table.

> **Operational Note**: two different hosts are involved, not one: RTSP/WHEP go straight to the grid's static IP (`GRID_RTSP_HOST`), while HLS is served from the `cctv.corp8.cloud` CDN host (`GRID_CDN_HOST`) -- also the host the catalogue poller in `app/ingestion/catalogue.py` polls for `cameras.json`. (An earlier version of this doc, and the seed data in `shared/db/seed.sql`, used a `live.corp8.cloud` domain for WHEP; that's stale -- the code path that actually runs builds WHEP from `GRID_RTSP_HOST`, not a hardcoded domain. See AuditReport2.md finding 12.) When testing outside the evaluation environment, access may depend on venue network routing; configure fallback mock or local streams if external gateway connectivity is restricted.

---

## RTSP Ingestion Client (`pipeline/ingest.py`)

Used by the **AI inference pipeline** (not browser). Connects to RTSP feeds, reads frames, yields `(frame, pts_ms)` for YOLO detection.

**Hackathon Portal Compliance:**
| Rule | Implementation |
|------|---------------|
| Force RTSP TCP | `os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"` |
| PTS-driven timing only | `pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)` — no wall clock, no FPS |
| Exponential backoff reconnect | Start 2s → doubles → capped at 30s |
| Scene discontinuity recovery | PTS reset detection without crashing tracker state |
| Decoder warnings tolerated | Mid-stream IDR waits logged, not fatal |
| Dynamic catalogue | `fetch_ingest_catalogue()` reads `/api/ingest` — no hardcoded camera IDs |

**Usage (for YOLO pipeline):**
```python
from pipeline.ingest import StreamIngestClient

client = StreamIngestClient(
    camera_id="12",
    rtsp_url="rtsp://103.250.160.189:8554/stream/12"  # GRID_RTSP_HOST, not a corp8.cloud domain
)

for frame, pts_ms in client.read_frames():
    # frame  → numpy BGR image (OpenCV format)
    # pts_ms → camera presentation timestamp in milliseconds
    detections = yolo_detector.detect(frame, pts_ms)
    matched_plates = watchlist_correlator.check(detections)
```

---

## 🗄️ Database Tables (`shared/db/`)

| Table | Purpose |
|-------|---------|
| `cameras` | Camera registry with RTSP/WHEP/HLS URLs, codec, resolution, FPS |
| `vehicles_watchlist` | Target plates with category and status |
| `persons_watchlist` | Target individuals, reference portraits, and 512-d pgvector embeddings |
| `vehicle_tracks` | Cross-camera global vehicle identities |
| `detections` | Individual sightings with camera timestamp and confidence |
| `alerts` | Real-time alerts on watchlist match with severity grading |

---

## 📂 Directory Structure

```
model2_analytics/
├── app/
│   └── routers/
│       ├── grid.py             # Live Grid API: /grid, /api/ingest, /api/v1/grid/streams
│       ├── watchlist.py        # Vehicle Watchlist REST API: /api/v1/watchlist/vehicles
│       ├── persons_watchlist.py# Person Watchlist & Face Recognition API: /api/v1/watchlist/persons
│       ├── detections.py       # Live AI Detections REST & WebSocket API: /ws/detections
│       └── recorded.py         # Pre-Recorded Video Upload & Controls: /ws/recorded/{id}
├── uploads/                    # Storage directory for user-uploaded video footage (.mp4, .avi, etc.)
│   └── persons/                # Storage for reference face portraits
├── detection-image/            # Persisted cropped vehicle thumbnails for audit & ANPR
└── pipeline/
    ├── ingest.py               # RTSP StreamIngestClient — optimized zero-latency frame reader
    ├── runner.py               # MultiStreamPipelineRunner & CameraWorker (RTSP threads)
    ├── video_worker.py         # PreRecordedVideoWorker — isolated on-demand video processor
    ├── detection/              # Indian traffic YOLOv8 model & DetectionWriter (DB persistence)
    ├── plate/                  # Plate recognizer interface & Indian plate format regex
    ├── ocr/                    # OCR engine & text extraction
    ├── tracking/               # InFrameTracker (IoU + proximity) & cross-camera associator
    └── faceembedding/          # Face quality checker (YuNet ONNX) & InceptionResnetV1 512-d encoder
```

---

## 🎯 Completed Architecture Highlights

1. **Indian Traffic YOLOv8**: Specialized weights detecting cars, auto-rickshaws, motorcycles, buses, trucks, mini-trucks.
2. **Smooth Live Tracking**: Real-time IoU + centroid tracking with EMA bounding box smoothing and zero browser reflows.
3. **Database Integration**: Automatic row insertion to PostgreSQL `detections` and `vehicle_tracks` with crop images stored on disk.
4. **Isolated Pre-Recorded Pipeline**: On-demand video analysis running in separate threads with pause, resume, stop, and speed rate controls without affecting live camera streams.
5. **Person Watchlist & Face Recognition**: Automated 5-gate facial quality screening, 512-d vector embedding extraction, and pgvector cosine similarity matching.

