-- ============================================================
-- Sentinel — Model 1 (Registry & GIS) + shared foundation schema
-- ============================================================
-- Tested against a real Postgres 16 + PostGIS 3.4 instance
-- (matching infra/docker-compose.yml, db name `sentinel`) —
-- every statement below actually ran clean, not just eyeballed.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector, for appearance-embedding fallback matching

-- ============================================================
-- Shared foundation
-- ============================================================

CREATE TABLE departments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL UNIQUE,
    category    TEXT,            -- e.g. Home/Police, Food & Civil Supplies, RTO, Municipal Corporation
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE districts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL UNIQUE,          -- Gujarat's 33 districts
    boundary    GEOGRAPHY(MULTIPOLYGON, 4326), -- null until a real shapefile is sourced
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_districts_boundary ON districts USING GIST (boundary);

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        TEXT NOT NULL UNIQUE,
    email           TEXT UNIQUE,
    hashed_password TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('dept_admin', 'operator', 'viewer')),
    department_id   UUID REFERENCES departments(id) ON DELETE SET NULL,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_department ON users (department_id);

-- ============================================================
-- Model 1 — Registry & GIS
-- ============================================================
--
-- `cameras` carries two kinds of columns, kept visually grouped
-- below because they come from different places and get filled
-- in at different times:
--
--   1. Registry/onboarding fields (Project_Context.md §3) — set by
--      us: department, district, ownership, retention, RBAC-relevant
--      metadata. These are what the hackathon's Model 1 brief is
--      actually about.
--   2. Grid catalogue fields (GET /api/ingest on the camera grid
--      itself, docs/API_Contract.md §0 + model2_analytics/README.md)
--      — mirrored from the source so we don't hard-code stream URLs
--      or assume a uniform codec/resolution across ~80,000 cameras.
--      Notably: the grid gives a human-written location LABEL
--      ("06 Timbavadi gate-Junagadh"), not coordinates. `location`
--      is nullable in schema definition; seed.sql enriches all 30
--      cameras with coordinates and department metadata for demo completeness.

CREATE TABLE cameras (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  TEXT NOT NULL,

    -- registry / onboarding fields
    department_id         UUID REFERENCES departments(id) ON DELETE RESTRICT,
    district_id           UUID REFERENCES districts(id) ON DELETE SET NULL,
    location              GEOGRAPHY(POINT, 4326),          -- nullable, see note above
    camera_type           TEXT,                            -- fixed/PTZ/dome/bullet — no fixed taxonomy given yet
    ownership             TEXT,                            -- government | private (societies/malls, per the brief)
    storage_type          TEXT,                            -- cloud | local, per the brief's wording
    retention_days        INT,
    vms_url               TEXT,                            -- optional hyperlink out to native VMS viewer
    connectivity_status   TEXT NOT NULL DEFAULT 'offline'
                           CHECK (connectivity_status IN ('online', 'offline', 'maintenance')),
    is_active             BOOLEAN NOT NULL DEFAULT true,    -- soft delete — see open question on DELETE endpoint
    decommissioned_at     TIMESTAMPTZ,

    -- grid catalogue fields, mirrored from GET /api/ingest
    source_grid_id        TEXT UNIQUE,                     -- "id" from /api/ingest — resync key, not our PK
    location_label        TEXT,                            -- raw "location" string from /api/ingest, e.g. "06 Timbavadi gate-Junagadh"
    is_live               BOOLEAN,                          -- grid's reported "live" flag
    codec                 TEXT,                            -- h264 | hevc | '' (grid reports blank when unknown) — mixed per model2_analytics/README
    stream_width          INT,
    stream_height         INT,
    stream_fps            REAL,
    bitrate_kbps          INT,
    rtsp_url              TEXT,
    whep_url              TEXT,                            -- grid calls this "webrtc_url"; it's WHEP specifically
    hls_url               TEXT,
    grid_synced_at         TIMESTAMPTZ,                     -- last time this row was refreshed from /api/ingest

    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_cameras_location ON cameras USING GIST (location);
CREATE INDEX idx_cameras_department ON cameras (department_id);
CREATE INDEX idx_cameras_district ON cameras (district_id);
CREATE INDEX idx_cameras_status ON cameras (connectivity_status);
CREATE INDEX idx_cameras_active ON cameras (is_active);

CREATE TABLE status_history (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id     UUID NOT NULL REFERENCES cameras(id) ON DELETE RESTRICT,
    changed_field TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    changed_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_status_history_camera ON status_history (camera_id, changed_at DESC);

-- ============================================================
-- Model 2 — Unified Viewer & Analytics
-- ============================================================

-- Watchlists
CREATE TABLE vehicles_watchlist (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plate_number  TEXT NOT NULL,             -- always store normalized (uppercase, no spaces)
    category      TEXT NOT NULL CHECK (category IN ('stolen', 'wanted', 'blacklisted')),
    reported_date DATE,
    department_id UUID REFERENCES departments(id) ON DELETE SET NULL,
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'resolved')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_vehicles_watchlist_plate_unique ON vehicles_watchlist (plate_number) WHERE status = 'active';

-- Persons watchlist with 512-d biometric face embedding
CREATE TABLE persons_watchlist (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name           TEXT NOT NULL,
    category       TEXT NOT NULL CHECK (category IN ('wanted', 'missing', 'suspect')),
    face_embedding VECTOR(512),
    photo_path     TEXT,
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'resolved')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_persons_watchlist_embedding ON persons_watchlist USING hnsw (face_embedding vector_cosine_ops);

-- Vehicle tracks — resolved global identity for vehicles
CREATE TABLE vehicle_tracks (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plate_number          TEXT,               -- nullable: a track can exist before plate is read
    appearance_embedding  VECTOR(512),         -- representative embedding for fallback matching
    vehicle_color         TEXT,
    vehicle_type          TEXT,
    first_seen            TIMESTAMPTZ NOT NULL,
    last_seen             TIMESTAMPTZ NOT NULL,
    is_watchlisted        BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX idx_vehicle_tracks_plate ON vehicle_tracks (plate_number);
CREATE INDEX idx_vehicle_tracks_embedding ON vehicle_tracks USING hnsw (appearance_embedding vector_cosine_ops);

-- Detections — individual vehicle sightings at cameras
CREATE TABLE detections (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id           UUID NOT NULL REFERENCES cameras(id) ON DELETE RESTRICT,
    "timestamp"         TIMESTAMPTZ NOT NULL,
    detected_plate       TEXT,                -- specific sighting's OCR output
    confidence           REAL,
    cropped_image_path   TEXT,
    vehicle_track_id     UUID REFERENCES vehicle_tracks(id) ON DELETE SET NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_detections_camera_time ON detections (camera_id, "timestamp" DESC);
CREATE INDEX idx_detections_track_time ON detections (vehicle_track_id, "timestamp");
CREATE INDEX idx_detections_plate ON detections (detected_plate);

-- Alerts — generated on watchlist match
CREATE TABLE alerts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    detection_id     UUID NOT NULL REFERENCES detections(id) ON DELETE CASCADE,
    watchlist_id     UUID NOT NULL REFERENCES vehicles_watchlist(id) ON DELETE RESTRICT,
    alert_type       TEXT NOT NULL DEFAULT 'vehicle_match',
    severity         TEXT CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    acknowledged_at  TIMESTAMPTZ
);
CREATE INDEX idx_alerts_watchlist ON alerts (watchlist_id);
CREATE INDEX idx_alerts_created ON alerts (created_at DESC);