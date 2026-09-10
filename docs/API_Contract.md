# API Contract

Contract between `model1-registry` and `model2_analytics`, and between
either of them and the frontend. This is the thing that lets both models
get built in parallel without stepping on each other — **if you change an
endpoint's shape, update this file in the same PR.** Stale contract docs
are worse than none (same rule as `Project_Context.md`'s header).

Status markers used below:
- ✅ decided — build against this
- 🚧 draft — shape is likely but not final, confirm before depending on it
- ❓ open — see `Project_Context.md` §9 or ask before building on it

---

## 0. Conventions

- All endpoints are under FastAPI, prefixed `/api/v1/`.
- Auth: JWT bearer token (`Authorization: Bearer <token>`), issued by
  `POST /api/v1/auth/login`. Three roles per `Project_Context.md` §6:
  `dept_admin`, `operator`, `viewer`. Enforced at the router dependency
  level, not just hidden in the UI. ✅
- Errors: standard shape —
  ```json
  { "error": { "code": "string", "message": "string", "details": {} } }
  ```
  🚧 — confirm before both sides start branching on `error.code`.
- Timestamps: ISO 8601 UTC everywhere. No local-time fields.
- Pagination: `?page=&page_size=` on list endpoints, response wraps in
  `{ "items": [...], "total": N, "page": N, "page_size": N }`. 🚧

**Do not confuse this with the external camera-feed API.** The
government camera grid exposes its own read-only catalogue at
`GET http://<host>/api/ingest` (returns each camera's id, location,
codec, live status, and its RTSP/WHEP/HLS URLs) — that's the *source*
Model 2's ingestion adapters poll, not part of our API surface. See
`model2_analytics/README.md` for ingestion notes.

---

## 1. Model 1 — Registry endpoints

Owner: `model1-registry`. Data model reference: `Project_Context.md` §3.

| Method & path | Purpose | Status |
|---|---|---|
| `GET /api/v1/cameras` | List/search/filter cameras (by department, district, status) | ✅ |
| `POST /api/v1/cameras` | Create camera (manual entry) | ✅ |
| `POST /api/v1/cameras/bulk` | CSV bulk import — see `Project_Context.md` §8, no wizard UX | ✅ |
| `GET /api/v1/cameras/{id}` | Camera detail incl. metadata + `vms_url` | ✅ |
| `PATCH /api/v1/cameras/{id}` | Update camera; writes a `status_history` row | ✅ |
| `DELETE /api/v1/cameras/{id}` | Soft delete camera (sets `is_active = false`, trigger timestamps `decommissioned_at`) | ✅ |
| `GET /api/v1/cameras/{id}/history` | Audit trail for one camera | ✅ |
| `GET /api/v1/departments` | List departments | ✅ |
| `GET /api/v1/districts` | List districts incl. PostGIS boundary (GeoJSON) | ✅ |
| `GET /api/v1/gap-analysis` | Coverage-hole report (PostGIS spatial query) per `Project_Context.md` §3 | ✅ |
| `GET /api/v1/export` | CSV/JSON export of filtered camera set | 🚧 |

### Camera object (`shared/schemas`)

```json
{
  "id": "uuid",
  "name": "string",
  "department_id": "uuid",
  "location": { "type": "Point", "coordinates": [lon, lat] },
  "district": "string",
  "camera_type": "string",
  "ownership": "string",
  "connectivity_status": "online | offline | maintenance",
  "storage_type": "string",
  "retention_days": "int",
  "vms_url": "string | null",
  "created_at": "datetime",
  "updated_at": "datetime"
}
```
Matches `Project_Context.md` §3's data model sketch — refine here as
fields get added, don't let this drift from `shared/db/`.

---

## 2. Model 2 — Analytics & Watchlist endpoints

Owner: `model2_analytics`. Data model reference: `Project_Context.md` §4.

| Method & path | Purpose | Status |
|---|---|---|
| `GET /grid` | Control-Room Multi-Camera Live Grid UI (2×2, 3×3, 4×4 views) | ✅ |
| `GET /api/ingest` | Hackathon ingestion contract — all 30 cameras with RTSP/WHEP/HLS URLs, codec, FPS, resolution | ✅ |
| `GET /api/v1/grid/streams` | List active camera streams with URLs, filterable by dept/district/status | ✅ |
| `POST /api/v1/grid/sync` | Sync camera catalogue from external source into DB | ✅ |
| `GET /api/v1/watchlist/vehicles` | List & search vehicle watchlist entries (filters: `status`, `category`, `plate_number`, `department_id`) | ✅ |
| `POST /api/v1/watchlist/vehicles` | Add new vehicle target (strict Indian plate format validation & duplicate checks) | ✅ |
| `GET /api/v1/watchlist/vehicles/{id}` | Get specific watchlist record details | ✅ |
| `PATCH /api/v1/watchlist/vehicles/{id}` | Update watchlist entry status (`active`/`resolved`) or incident description | ✅ |
| `DELETE /api/v1/watchlist/vehicles/{id}` | Delete watchlist entry and cascade associated alert references | ✅ |
| `GET /api/v1/watchlist/persons` | List & filter person watchlist entries (filters: `status`, `category`, `name`) | ✅ |
| `POST /api/v1/watchlist/persons` | Register person target with 5-gate AI quality check & 512-d InceptionResnetV1 embedding in pgvector | ✅ |
| `GET /api/v1/watchlist/persons/{id}` | Get specific person watchlist record details | ✅ |
| `PATCH /api/v1/watchlist/persons/{id}` | Update person record details or toggle status (`active`/`resolved`) | ✅ |
| `DELETE /api/v1/watchlist/persons/{id}` | Permanently delete person target and remove reference portrait from disk | ✅ |
| `GET /api/v1/watchlist/persons/photos/{photo_filename}` | Authenticated serving of reference face portrait | ✅ |
| `GET /recorded-detection` | Pre-Recorded Video AI Vehicle Detection Dashboard UI | ✅ |
| `GET /api/v1/recorded/cameras` | List active cameras for footage location association | ✅ |
| `POST /api/v1/recorded/upload` | Upload surveillance footage (up to 2 GB) with OpenCV metadata probing | ✅ |
| `POST /api/v1/recorded/start` | Start background vehicle detection & tracking worker (1x, 2x, max speed) | ✅ |
| `POST /api/v1/recorded/pause` / `resume` / `stop` | Real-time playback and execution controls for vehicle analysis worker | ✅ |
| `GET /api/v1/recorded/status/{job_id}` | Query current job status, total frames, detections count, and processing FPS | ✅ |
| `WS /ws/recorded/{job_id}` | Real-time WebSocket channel streaming `VIDEO_FRAME`, `FRAME_BOXES`, `NEW_DETECTION`, `JOB_PROGRESS` | ✅ |
| `GET /face-detection` | Surveillance Video Face Detection & Watchlist Alerting Dashboard UI | ✅ |
| `GET /api/v1/face-detection/active-jobs` | List all active and uploaded face analysis jobs | ✅ |
| `POST /api/v1/face-detection/upload` | Upload surveillance footage (up to 2 GB) with OpenCV metadata probing | ✅ |
| `POST /api/v1/face-detection/start` | Start isolated face detection & watchlist matching worker (1x, 2x, max speed) | ✅ |
| `POST /api/v1/face-detection/pause` / `resume` / `stop` | Execution controls for face analysis worker | ✅ |
| `GET /api/v1/face-detection/status/{job_id}` | Query current face job status, total frames, face count, and match count | ✅ |
| `GET /api/v1/face-detection/alerts` | Paginated person watchlist match alerts with similarity scores and timestamps | ✅ |
| `GET /api/v1/face-detection/crops/{filename}` | Authenticated serving of detected face match crop thumbnails | ✅ |
| `WS /api/v1/face-detection/ws/{job_id}` | Real-time WebSocket channel streaming `VIDEO_FRAME`, `FACE_BOXES`, `PERSON_MATCH`, `JOB_PROGRESS` | ✅ |
| `GET /detection-image/{file_path}` | Authenticated serving of vehicle/plate cropped detection images | ✅ |
| `GET /api/v1/detections` | List detections, filterable by camera/plate/time range | ✅ |
| `GET /api/v1/vehicle-tracks/{plate_number}` | Full route reconstruction for a plate — **this is the Step 4 scored test** | 🚧 |
| `GET /api/v1/alerts` | List alerts, filter by acknowledged/severity | 🚧 |
| `POST /api/v1/alerts/{id}/acknowledge` | Ack an alert — writes `acknowledged_by`/`acknowledged_at` | 🚧 |
| `WS /api/v1/ws/alerts` | Real-time alert push to dashboard on watchlist match | 🚧 |
| `WS /ws/detections` | Real-time live RTSP camera vehicle detections and track stream | ✅ |

### Vehicle Watchlist object (`shared/schemas/watchlist.py`)

```json
{
  "id": "uuid",
  "plate_number": "GJ01AB1234",
  "category": "stolen | wanted | blacklisted",
  "reported_date": "2026-08-30",
  "department_id": "uuid | null",
  "department_name": "Home Department (Police) | null",
  "description": "White Hyundai Creta, missing since Sunday FIR #402/2026",
  "status": "active | resolved",
  "created_at": "datetime"
}
```

### Person Watchlist Contract (`shared/schemas/persons_watchlist.py`)

#### 1. Registration Request (`POST /api/v1/watchlist/persons`)
Sent from Frontend UI as `multipart/form-data`:
- `name` (string, required): Full name or alias (min 2, max 120 chars)
- `category` (string, required): `'wanted'` | `'missing'` | `'suspect'`
- `status` (string, optional): `'active'` | `'resolved'` (defaults to `'active'`)
- `photo` (binary file, required): Reference portrait JPEG/PNG image (evaluated by 5 AI quality gates)

#### 2. Database Storage (`persons_watchlist` table in PostgreSQL + pgvector)
What is actually persisted in the database:
| Column | Type | Description |
|---|---|---|
| `id` | `UUID PRIMARY KEY` | Auto-generated target identifier |
| `name` | `TEXT` | Target name or alias |
| `category` | `TEXT` | Checked: `'wanted'`, `'missing'`, `'suspect'` |
| `face_embedding` | `VECTOR(512)` | **L2 unit-normalized 512-d vector** indexed via HNSW (`idx_persons_watchlist_embedding`) |
| `photo_path` | `TEXT` | Disk path/URL: `/api/v1/watchlist/persons/photos/{uuid}.jpg` |
| `status` | `TEXT` | Checked: `'active'`, `'resolved'` |
| `created_at` | `TIMESTAMPTZ` | Record timestamp (`now()`) |

#### 3. API Response JSON (`PersonWatchlistResponse`)
Returned on `GET` and `POST` endpoints:
```json
{
  "id": "uuid",
  "name": "string",
  "category": "wanted | missing | suspect",
  "status": "active | resolved",
  "photo_path": "/api/v1/watchlist/persons/photos/{uuid}.jpg | null",
  "has_embedding": true,
  "embedding_dim": 512,
  "created_at": "datetime",
  "quality_metrics": {
    "face_detected": true,
    "sharpness_score": 45.2,
    "face_resolution": [274, 344],
    "yaw_ratio": 2.1,
    "roll_angle_deg": 1.4,
    "brightness_mean": 128.5,
    "is_frontal": true,
    "quality_passed": true,
    "rejection_reason": null
  }
}
```

### Detection object

```json
{
  "id": "uuid",
  "camera_id": "uuid",
  "timestamp": "datetime",
  "detected_plate": "string",
  "confidence": "float",
  "cropped_image_path": "string",
  "vehicle_track_id": "uuid | null"
}
```

### Alert object

```json
{
  "id": "uuid",
  "detection_id": "uuid",
  "watchlist_id": "uuid",
  "alert_type": "string",
  "severity": "low | medium | high | critical",
  "created_at": "datetime",
  "acknowledged_by": "uuid | null",
  "acknowledged_at": "datetime | null"
}
```

### Person Alert object (`person_alerts` table in PostgreSQL)

Created when a face detected in pre-recorded or live surveillance footage matches an active entry in `persons_watchlist` with cosine distance $\le 0.30$ (similarity $\ge 0.70$).

```json
{
  "id": "uuid",
  "person_id": "uuid",
  "person_name": "string",
  "category": "wanted | missing | suspect",
  "camera_id": "string (e.g. 'prerecorded' or camera UUID)",
  "similarity_score": 0.8421,
  "distance": 0.1579,
  "face_crop_path": "/api/v1/face-detection/crops/{alert_id}.jpg",
  "reference_photo_path": "/api/v1/watchlist/persons/photos/{person_id}.jpg",
  "frame_timestamp": "datetime",
  "created_at": "datetime"
}
```

### WebSocket Streaming Channels

#### 1. Live RTSP Vehicle Detections (`/ws/detections`)
- `FRAME_BOXES`: Streaming tracking bounding boxes `[{ track_id, bbox: [x1, y1, x2, y2], class_name, confidence }]`
- `NEW_DETECTION`: Confirmed vehicle sighting persisted in DB with plate OCR and crop path.

#### 2. Pre-Recorded Vehicle Video Stream (`/ws/recorded/{job_id}`)
- `VIDEO_FRAME`: `jpeg_b64` frame bytes, `frame_n`, `total_frames`, `pts_ms`
- `FRAME_BOXES`: Real-time tracking boxes for active vehicle tracks
- `NEW_DETECTION`: Confirmed sightings with vehicle class and crop
- `JOB_PROGRESS`: Current frame, processing FPS, percentage complete
- `JOB_DONE`: Completion event with total processed frames and detection totals

#### 3. Surveillance Video Face Detection Stream (`/api/v1/face-detection/ws/{job_id}`)
- `VIDEO_FRAME`: Streaming `jpeg_b64` frame with frame index and total frames
- `FACE_BOXES`: Real-time face bounding boxes, confidence score, and match status
- `PERSON_MATCH`: Instant alert event containing `person_id`, `name`, `category`, `similarity_score`, `distance`, and `crop_url`
- `JOB_PROGRESS`: Real-time processing FPS, total faces, total matches, percentage complete
- `JOB_DONE`: Final completion payload with total faces and watchlist hits

### WebSocket contract — `/api/v1/ws/alerts` 🚧

Server pushes on every new `alerts` row:
```json
{ "type": "alert.created", "payload": { ...Alert object... } }
```
Confirm reconnect/backoff expectations on the client side before both
sides build against this — same reconnect discipline as the camera
streams (see §3 below), this is our own service, not exempt from it.

---

## 3. Cross-cutting: camera stream ingestion (Model 2 concern)

Model 2's pipeline consumes the government camera grid directly per the
resource doc — not through Model 1's API. Key constraints Model 2's
ingestion code must follow (full detail in
`model2_analytics/README.md`):

- RTSP forced over TCP, never trust `UDP`.
- Read the camera list from the grid's own `/api/ingest`, not
  hard-coded URLs — camera ids can change.
- Drive all timing off PTS (`CAP_PROP_POS_MSEC` / buffer PTS), never
  wall-clock arrival time.
- Reconnect with exponential backoff (~2s → cap 30s), never a tight loop.
- Tolerate decode warnings on join and scene-cut discontinuities at
  loop points — not fatal, not a disconnect.

### FramePacket — VMS → Analytics Queue Contract ✅

The VMS layer passes frames to the analytics pipeline via a shared in-memory queue.
The queue is created at app startup and accessible as `app.state.frame_queue`.

```python
# Created once at app startup (in main.py lifespan) and stored on app.state:
frame_queue: queue.Queue[FramePacket] = queue.Queue(maxsize=500)
app.state.frame_queue = frame_queue
```

**FramePacket fields** (`shared/adapters/base.py`):

| Field | Type | Description |
|---|---|---|
| `frame` | `np.ndarray` | BGR image, shape `(height, width, 3)` — OpenCV default |
| `pts_ms` | `float` | Milliseconds since stream epoch — **use this for all DB timestamps** |
| `camera_id` | `str` | UUID string — write to `detections.camera_id` |
| `source_grid_id` | `str` | Grid's camera ID — for logging only, not for DB writes |
| `width` | `int` | Frame width in pixels |
| `height` | `int` | Frame height in pixels |

**Critical rules for analytics pipeline consumers:**
- Use `pts_ms` for every timestamp written to `detections` — never use `time.time()` or `datetime.now()`
- On queue full, VMS drops frames (non-blocking `put_nowait`) — the consumer is the bottleneck, not VMS
- Queue is thread-safe (`queue.Queue`) — analytics workers can call `get()` from any thread

**Importing FramePacket:**
```python
from shared.adapters.base import FramePacket
# or
from shared.adapters import FramePacket
```

---

## 4. Open items

- Exact error-code enum — needs a decision once both models have real
  failure cases to enumerate. ❓
- Pagination defaults (page size). ❓
- Synchronous execution for `gap-analysis` (30 cameras / 33 districts runs in <20ms). Revisit background job queue at statewide scale (80,000+ cameras). ✅

Update this section as decisions land — don't leave it stale while the
actual API diverges from what's written here.
