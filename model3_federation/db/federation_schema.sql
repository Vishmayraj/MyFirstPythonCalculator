-- ============================================================
-- Model 3 — VMS Federation & Middleware: New tables
-- ============================================================
-- APPEND-ONLY: no existing tables are modified.
-- Run AFTER shared/db/schema.sql (which creates departments,
-- users, and vehicles_watchlist that we FK into).
-- Safe to re-run: all CREATE TABLE statements use IF NOT EXISTS.
-- ============================================================

-- Registered federated VMS systems (one row per department VMS)
CREATE TABLE IF NOT EXISTS federated_systems (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    vendor          TEXT NOT NULL,             -- Milestone | Hikvision | Dahua | ONVIF | custom
    department_id   UUID REFERENCES departments(id) ON DELETE SET NULL,
    api_endpoint    TEXT,                      -- future real endpoint; NULL for simulated adapters
    protocol        TEXT,                      -- REST_API | SDK | ONVIF | WebSocket | simulated
    status          TEXT NOT NULL DEFAULT 'connected'
                    CHECK (status IN ('connected','disconnected','error','degraded')),
    last_heartbeat  TIMESTAMPTZ,
    camera_count    INT DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cameras registered in a federated VMS (not in our direct registry)
CREATE TABLE IF NOT EXISTS federated_cameras (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    system_id       UUID NOT NULL REFERENCES federated_systems(id) ON DELETE CASCADE,
    external_id     TEXT NOT NULL,             -- camera ID inside the source VMS
    name            TEXT NOT NULL,
    location        GEOGRAPHY(POINT, 4326),
    location_label  TEXT,
    department_id   UUID REFERENCES departments(id) ON DELETE SET NULL,
    is_active       BOOLEAN DEFAULT true,
    UNIQUE (system_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_fed_cameras_system   ON federated_cameras (system_id);
CREATE INDEX IF NOT EXISTS idx_fed_cameras_location ON federated_cameras USING GIST (location);

-- Every detection event that passes through the federation bus
CREATE TABLE IF NOT EXISTS federated_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    system_id        UUID NOT NULL REFERENCES federated_systems(id) ON DELETE CASCADE,
    camera_id        UUID REFERENCES federated_cameras(id) ON DELETE SET NULL,
    event_type       TEXT NOT NULL,            -- vehicle_detection | person_detection | intrusion
    detected_plate   TEXT,
    confidence       REAL,
    vehicle_type     TEXT,
    snapshot_url     TEXT,
    raw_payload      JSONB,                    -- original un-translated vendor JSON for audit
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_timestamp TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_fed_events_system_time ON federated_events (system_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_fed_events_plate       ON federated_events (detected_plate);
CREATE INDEX IF NOT EXISTS idx_fed_events_received    ON federated_events (received_at DESC);

-- Cross-system correlation records (same plate seen in 2+ VMS)
CREATE TABLE IF NOT EXISTS correlation_results (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plate_number     TEXT NOT NULL,
    event_ids        UUID[] NOT NULL,
    system_ids       UUID[] NOT NULL,
    first_seen       TIMESTAMPTZ NOT NULL,
    last_seen        TIMESTAMPTZ NOT NULL,
    camera_sequence  JSONB,                    -- [{camera_name, system_name, timestamp, lat, lng}]
    travel_time_secs INT,
    is_watchlisted   BOOLEAN DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_correlation_plate ON correlation_results (plate_number);
CREATE INDEX IF NOT EXISTS idx_correlation_time  ON correlation_results (last_seen DESC);

-- Federated alerts: watchlist match detected via the federation bus
CREATE TABLE IF NOT EXISTS federated_alerts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id        UUID NOT NULL REFERENCES federated_events(id) ON DELETE CASCADE,
    watchlist_id    UUID NOT NULL REFERENCES vehicles_watchlist(id) ON DELETE RESTRICT,
    system_id       UUID NOT NULL REFERENCES federated_systems(id) ON DELETE CASCADE,
    severity        TEXT CHECK (severity IN ('low','medium','high','critical')),
    alert_type      TEXT NOT NULL DEFAULT 'federated_vehicle_match',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_by UUID REFERENCES users(id) ON DELETE SET NULL,
    acknowledged_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_fed_alerts_created ON federated_alerts (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fed_alerts_system  ON federated_alerts (system_id);
