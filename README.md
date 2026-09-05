# Sentinel — Gujarat CCTV Integration & GIS Platform

Sentinel is a unified CCTV management, registry, GIS mapping, and video analytics platform built for Gujarat's statewide surveillance network.

---

## 🚀 Quick Start (Running with Docker Compose)

The entire platform (PostgreSQL + PostGIS database and the FastAPI application) can be brought up in a single command using Docker Compose.

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (with Docker Compose v2+)

### Launch Instructions

1. **Clone & navigate to `infra/`**:
   ```bash
   cd infra
   ```

2. **Set a `SECRET_KEY`** — `docker-compose.yml` refuses to start without one (it signs every login session, so there's no safe default baked in). One command creates `infra/.env` with a real random key (`docker-compose.yml` already defaults `DEBUG=false` on its own, so this is the only variable you need to set to get running):
   ```bash
   # macOS/Linux
   echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" > .env
   ```
   ```powershell
   # Windows PowerShell
   "SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")" | Out-File -Encoding ascii .env
   ```
   (`../.env.example`, one directory up, documents every other override this file can hold — GRID_HOST, rate-limit tuning, DOMAIN for real HTTPS, etc. — none of the rest are required just to boot the app.)

3. **Start the containers**:
   ```bash
   docker compose up -d
   ```
   *This automatically builds the FastAPI app container (`infra/Dockerfile`) and PostgreSQL + PostGIS + pgvector database container (`infra/Dockerfile.db`), and creates the `sentinel` role/database on first boot — no separate setup step needed.*

   Give it 15-30s on first run (the `db` image build + healthcheck). The `app` image's first build is the slow part — its dependencies include `ultralytics`/PyTorch for the AI detection features, so a first-time `docker compose up -d` can take several minutes depending on your connection (subsequent runs are fast, everything's cached). It's not stuck; `docker compose logs -f app` will show the pip install progress if you want to confirm it's still working. `docker compose ps` should show `db` as `healthy` and `app` as `running` before step 4.

4. **Access the Web Dashboard**:
   Open your browser and navigate to:
   👉 **`http://localhost:8000`**

    - **Interactive Map Dashboard**: `http://localhost:8000/`
    - **Command Login Portal**: `http://localhost:8000/login`
    - **Camera Registry & CRUD**: `http://localhost:8000/cameras`
    - **Control Room Live Grid (30 Cameras)**: `http://localhost:8000/grid`
    - **Live AI Vehicle Detection (Cam 04 & 22)**: `http://localhost:8000/detection`
    - **Pre-Recorded Video AI Detection**: `http://localhost:8000/recorded-detection`
    - **Vehicle Watchlist (Model 2)**: `http://localhost:8000/watchlist`
    - **Person Watchlist & Biometrics (Model 2)**: `http://localhost:8000/watchlist/persons`
    - **Department Management**: `http://localhost:8000/departments`
    - **District Overview**: `http://localhost:8000/districts`
    - **System Audit Log**: `http://localhost:8000/audit`
    - **Surveillance Gap Analysis**: `http://localhost:8000/gap-analysis`
    - **Swagger OpenAPI Docs**: `http://localhost:8000/docs`

5. **Operational Demo Accounts** (Password: `password123`):
   - `admin_home` (`dept_admin` — Home Department)
   - `admin_rto` (`dept_admin` — Regional Transport Office)
   - `operator1` (`operator`)
   - `viewer1` (`viewer`)

6. **Resetting Database & Seed Data** (if needed):
   ```bash
   docker compose down -v --rmi local
   docker compose up -d
   ```

---

## 🖥️ Running Locally Without Docker

Prefer running the app or tests directly on your machine instead of in containers? You need a local Postgres 16 install with the `postgis`, `pgcrypto`, and `vector` (pgvector) extensions available, then:

```bash
# one-time: creates the `sentinel` role + `sentinel` database (mirrors what
# docker-compose gets automatically from the official Postgres image)
bash scripts/bootstrap_local_db.sh

cd model1-registry
pip install -r requirements-dev.txt
pytest   # also self-bootstraps the sentinel role/db if scripts/bootstrap_local_db.sh wasn't run first
```

See `model1-registry/README.md`'s Testing section for details, including what to check if a run looks stuck.

---

## 🛠️ Technology Stack

- **Backend Framework**: FastAPI (Python 3.12)
- **Database**: PostgreSQL 16 + PostGIS 3.4 + `pgvector` extension
- **ORM & Migrations**: SQLAlchemy 2.0 + GeoAlchemy2
- **Frontend Architecture**: Server-rendered Jinja2 templates + HTMX + Alpine.js (via CDN, no Node build step)
- **Live Video & Analytics**: HLS.js in-browser streaming, RTSP/HLS feeds, ANPR watchlist matching
- **Containerization**: Docker Compose (`infra/docker-compose.yml`)

---

## 📁 Repository Structure

```
├── Project_Context.md       Our working technical specification & architectural decisions
├── HackathonPortal.md       Official hackathon challenge brief
├── Model1ImplementationPlan.md Implementation plan for Model 1 (Registry & GIS)
├── docs/
│   ├── API_Contract.md      REST & WebSocket API specification
│   └── DATASET.md           Dataset notes & video stream catalogue
├── shared/                  Shared codebase across models
│   ├── db/                  SQLAlchemy models, schema.sql, triggers.sql, seed.sql
│   ├── schemas/             Pydantic request & response models
│   └── adapters/            VMS adapter interface definitions
├── model1-registry/         Model 1 — Registry & GIS Foundation
│   └── app/                 FastAPI application (routers, templates, static CSS/JS)
├── model2-analytics/        Model 2 — Analytics & Vehicle Tracking (ANPR, Watchlists, Alerts)
│   ├── app/routers/         Watchlist CRUD router
│   └── pipeline/            Analytics & ANPR pipeline architecture
├── infra/                   Docker environment (`docker-compose.yml`, `Dockerfile`, `Dockerfile.db`)
└── scripts/                 One-off setup scripts (e.g. `bootstrap_local_db.sh` for non-Docker local dev)
```

---

## 🔌 API Endpoints Summary

### Model 1 — Registry, Auth & GIS
- `POST /api/v1/auth/login` — Authenticate user and set httpOnly JWT cookie
- `POST /api/v1/auth/logout` — Log out user and clear session cookie
- `GET /api/v1/cameras` — List, filter by department, district, and status
- `POST /api/v1/cameras` — Create new camera (manual entry, `dept_admin` scoped)
- `POST /api/v1/cameras/bulk` — CSV bulk camera import (`dept_admin` scoped)
- `GET /api/v1/cameras/{id}` — Get camera detail & VMS stream URL
- `PATCH /api/v1/cameras/{id}` — Update camera (writes `status_history` audit log)
- `DELETE /api/v1/cameras/{id}` — Soft delete camera (`is_active = false`)
- `GET /api/v1/cameras/{id}/history` — Camera audit history
- `GET /api/v1/audit` — System-wide audit trail logs
- `GET /api/v1/departments` — List departments with active camera counts
- `GET /api/v1/districts` — List all 33 Gujarat districts with camera counts and GeoJSON boundaries
- `GET /api/v1/gap-analysis` — PostGIS spatial camera coverage calculation (1km buffer)

### Model 2 — Live Grid, AI Detection & Video Analytics
- `GET /grid` — Control-Room Multi-Camera Live Grid UI (2×2, 3×3, 4×4 matrix views)
- `GET /api/ingest` — Hackathon ingestion contract — returns all cameras with RTSP/WHEP/HLS URLs
- `GET /api/v1/grid/streams` — JSON API: all active camera stream URLs (with dept/district filters)
- `POST /api/v1/grid/sync` — Sync camera catalogue from external source into DB
- `GET /detections` or `GET /detection` — Live AI Vehicle Detection Dashboard
- `GET /api/v1/detections` — Paginated vehicle detection audit history from DB
- `GET /api/v1/detections/stats` — Real-time vehicle detection counts and active tracks
- `WS /ws/detections` — WebSocket stream for live bounding boxes, track IDs, and sightings
- `GET /recorded-detection` — Pre-Recorded Video AI Detection Dashboard UI
- `POST /api/v1/recorded/upload` — Multipart video upload (up to 2 GB) with OpenCV metadata extraction
- `GET /api/v1/recorded/cameras` — List active cameras for location association
- `POST /api/v1/recorded/start` — Start isolated background video analysis worker
- `POST /api/v1/recorded/pause` / `resume` / `stop` — Execution controls
- `GET /api/v1/recorded/status/{job_id}` — Query status, frame count, processing FPS
- `WS /ws/recorded/{job_id}` — Real-time video frame and bounding box WebSocket stream
- `GET /api/v1/watchlist/vehicles` — List & search vehicle targets (filter by `category`, `status`, `plate_number`, `department_id`)
- `POST /api/v1/watchlist/vehicles` — Add new vehicle target (with Indian plate format validation & duplicate checks)
- `GET /api/v1/watchlist/vehicles/{id}` — Get single watchlist target detail
- `PATCH /api/v1/watchlist/vehicles/{id}` — Update target case status (`active` / `resolved`) or details
- `DELETE /api/v1/watchlist/vehicles/{id}` — Delete watchlist target and cascade associated alerts
- `GET /watchlist/persons` — Person Watchlist & Facial Biometric Dashboard UI
- `GET /api/v1/watchlist/persons` — List & filter person targets (filter by `category`, `status`, `name`)
- `POST /api/v1/watchlist/persons` — Register person target with 5-gate AI quality validation (YuNet + solvePnP 3D pose) and 512-d InceptionResnetV1 embedding in `pgvector`
- `GET /api/v1/watchlist/persons/{id}` — Get single person watchlist target detail
- `PATCH /api/v1/watchlist/persons/{id}` — Update person details or toggle status (`active` / `resolved`)
- `DELETE /api/v1/watchlist/persons/{id}` — Remove person target and disk reference photo
- `GET /api/v1/watchlist/persons/photos/{photo_filename}` — Authenticated serving of reference face portrait

---

## 📄 License & Project Context

See [Project_Context.md](./Project_Context.md) for full architectural background, rationale, and design principles.
