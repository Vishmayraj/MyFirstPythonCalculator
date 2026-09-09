# infra/ — Docker & Infrastructure Environment

This directory contains the Docker Compose configuration and container definitions for local development and VPS deployment.

---

## 📦 Container Setup

| Container | Image / Dockerfile | Purpose |
|---|---|---|
| `db` | `Dockerfile.db` (`postgis/postgis:16-3.4` + `pgvector`) | PostgreSQL + PostGIS spatial engine + vector embeddings |
| `app` | `Dockerfile` (`python:3.12-slim`) | Sentinel FastAPI Web App (Model 1 & Model 2) |
| `caddy` | `caddy:2-alpine` | Reverse proxy + automatic HTTPS in front of `app` |

---

## 🚀 Commands

### Starting the Environment

From the `infra/` directory (or root):

```bash
cd infra
docker compose up -d
```

Compose automatically builds `infra/Dockerfile.db` for the database and `infra/Dockerfile` for the app.

### Database Initialization & Seed Data

On initial boot, PostgreSQL runs scripts mounted from `shared/db/`:
1. `20-schema.sql` — Creates extensions (`postgis`, `pgcrypto`, `vector`), tables, constraints, and indexes.
2. `30-triggers.sql` — Sets `updated_at` timestamps and logs audit entries in `status_history`.
3. `40-seed.sql` — Populates 5 departments, 4 default RBAC users (`admin_home`, `admin_rto`, `operator1`, `viewer1`), 33 Gujarat districts with PostGIS MultiPolygon boundaries, 30 seed cameras with rich metadata, and sample vehicle watchlists/alerts.

### Resetting Data (Clean Re-seed)

Postgres init scripts fire only when the data volume is empty. To reset and re-seed from scratch:

```bash
docker compose down -v --rmi local
docker compose up -d
```

### Checking Database Status

```bash
docker compose exec db psql -U sentinel -d sentinel -c "SELECT count(*) FROM cameras;"
```

---

## 🔒 Configuration

`docker-compose.yml`'s `app` service gets its `DATABASE_URL` built from the same `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` env vars as the `db` service below (defaulting to `postgresql://sentinel:sentinel_dev@db:5432/sentinel` if none are set), so the two can't drift out of sync — override `POSTGRES_PASSWORD` in `.env` and both services pick it up automatically. Set `DATABASE_URL` directly instead if you want `app` to talk to an external/managed Postgres rather than the `db` service entirely. See `.env.example` for all of these.

The `sentinel` role and `sentinel` database themselves need no manual setup — the official Postgres image bootstraps them automatically from the `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` env vars on `db`'s first boot (see `docker-compose.yml`), the same way it would for anyone cloning this repo fresh. If you're running the app or tests directly on the host instead of through Docker, `scripts/bootstrap_local_db.sh` (repo root) does the equivalent one-time setup against a local Postgres install — see `model1-registry/README.md`'s Testing section.

`DISABLE_INGESTION` (env var on the `app` service, defaults to `true` in this compose file) only turns off RTSP capture/MediaMTX registration — the camera-catalogue poll against the live grid host still runs either way, so the registry stays in sync. That poll is separately skippable via `DISABLE_CATALOGUE_POLL`, which the test suite sets on its own and which you should leave unset here.

---

## 🔐 TLS / Reverse Proxy (Caddy)

`app` itself only ever speaks plain HTTP on the private compose network -
`caddy` is the only service exposed on ports 80/443, and it terminates TLS
before forwarding to `app:8000`. Config lives in `infra/Caddyfile`.

- **Local dev**: leave `DOMAIN` unset. Caddy falls back to `localhost`
  and serves its own locally-trusted self-signed certificate - no DNS or
  extra setup needed, `docker compose up -d` just works.
- **Real deployment**: point a real DNS record at this host and set
  `DOMAIN=your-real-domain.example` (e.g. in a `.env` file next to
  `docker-compose.yml`, see `.env.example`). Caddy automatically obtains
  and renews a real Let's Encrypt certificate for that domain - no manual
  certbot/renewal steps required.

The app's own login cookie (`routers/auth.py`) sets `secure=True`
whenever `DEBUG=false` (the default `docker-compose.yml` already sets
this), so once Caddy is fronting the app with real TLS, the session
cookie also refuses to travel over plain HTTP - see AuditReport1.md
finding 2.1 for why this was previously missing entirely.

---

## 📡 MediaMTX — RTSP → WebRTC/HLS Bridge

MediaMTX re-serves the government grid's RTSP streams as WebRTC (WHEP) and HLS for the browser-based multi-camera viewer. It runs as a Docker service alongside `db` and `app`.

| Container | Image | Ports | Purpose |
|---|---|---|---|
| `mediamtx` | `bluenviron/mediamtx:latest` | 8554/8889/8888/9997 | RTSP→WebRTC/HLS bridge |

### Starting MediaMTX

MediaMTX starts automatically with `docker compose up -d`. To start it alone:

```bash
cd infra
docker compose up -d mediamtx
```

### Stream URLs (after registration)

Once the ingestion supervisor registers a camera stream (via the MediaMTX API):

- **HLS**: `http://<host>:8888/<source_grid_id>/index.m3u8`
- **WHEP** (WebRTC): `http://<host>:8889/<source_grid_id>/whep`

Where `source_grid_id` is the government grid's camera ID (e.g., `cam01`, `cam06`).

### Dynamic Stream Registration

Streams are registered at runtime by the `IngestionSupervisor` — NOT statically in `mediamtx.yml` (there is deliberately no static `paths:` block in that file — see the comment there). To manually register a stream for testing:

```bash
# 103.250.160.189 below is the current GRID_RTSP_HOST default (see
# model1-registry/app/config.py and .env.example) - if you've overridden
# that env var to point at a different grid host, use that value instead.
curl -X POST http://localhost:9997/v3/config/paths/add/cam01 \
  -H "Content-Type: application/json" \
  -d '{"source": "rtsp://103.250.160.189:8554/stream/cam01", "sourceOnDemand": false}'
# Expected: 200 OK

# Verify HLS is serving
ffprobe http://localhost:8888/cam01/index.m3u8
# Expected: shows video stream info, no errors

# Verify WHEP endpoint exists
curl -I http://localhost:8889/cam01/whep
# Expected: 405 Method Not Allowed (correct — WHEP needs POST with SDP offer, not GET)
```

### Configuration

MediaMTX config is at `infra/mediamtx.yml`. Key settings:
- `protocols: [tcp]` — TCP-only RTSP (no UDP, matches our ingestion workers)
- `api: yes` on port 9997 — used by supervisor to register streams dynamically
- `hlsAlwaysRemux: yes` — HLS available even when no viewer is watching
- `hlsAllowOrigin` / `webrtcAllowOrigin` — CORS origin allowed to embed playback. The file's own defaults are `localhost:8000` for local dev, but `docker-compose.yml`'s `mediamtx` service overrides both via `MTX_HLSALLOWORIGIN`/`MTX_WEBRTCALLOWORIGIN` env vars driven off a single `MEDIAMTX_CORS_ORIGIN` (see `.env.example`) — **set this to your real deployed origin**, or HLS/WebRTC playback will fail with a CORS error in the browser console once this isn't running on `localhost:8000` anymore.