# Model 1 — Registry & GIS Foundation

Full spec: `Project_Context.md` §3 & `Model1ImplementationPlan.md`.

## What this owns

The camera registry and the GIS map dashboard. This handles camera onboarding, metadata tracking, status management, spatial visualization across Gujarat, department/district categorization, and audit logging.

## Features Implemented

- **Interactive GIS Map (`/`)**: Leaflet + OpenStreetMap, marker clustering via `Leaflet.markercluster`, connectivity status markers (🟢 online, 🔴 offline, 🟡 maintenance), district dropdown filtering, department layer toggles, and rich popup cards with VMS stream viewer links.
- **Camera Registry & Table View (`/cameras`)**: HTMX-driven sortable/filterable table, soft delete (`is_active = false`), and bulk CSV import (`/api/v1/cameras/bulk`).
- **Camera CRUD Modal Forms**: Alpine.js v3 + HTMX modal for creating and updating camera metadata, writing automated audit logs to `status_history`.
- **Department View (`/departments`)**: Read-only view with active camera counts per department.
- **District View (`/districts`)**: Coverage view for all 33 Gujarat districts.
- **Model 2 Stubs**: Inert navigation links for `/detections`, `/watchlist`, `/alerts`.

## Stack

FastAPI + Jinja2 templates + HTMX + Alpine.js (no Node build step). PostgreSQL + PostGIS via `shared/db/`.

## Directory layout

```
model1-registry/
└── app/
    ├── routers/     FastAPI routers — cameras, departments, districts, pages
    ├── templates/   Jinja2 HTML templates (map.html, cameras_list.html, camera_form.html, etc.)
    └── static/      Leaflet map logic (map.js) & CSS design system (main.css)
```

## Status

✅ **Completed — Phase 1 & Foundation Built**. Full CRUD, GIS map dashboard, dark theme glassmorphism UI, seed data, and docker setup are operational.

## Testing

The test suite runs against a real Postgres + PostGIS database (`sentinel_test`), not sqlite or mocks — the app relies on PostGIS geography functions and Postgres triggers with no sqlite equivalent, so testing against anything else wouldn't exercise the code paths that actually matter (RBAC scoping, geodesic gap-analysis math, audit-log triggers).

### Zero-setup path

If you already have Postgres 16 + PostGIS + pgvector running locally (or `docker compose up -d` — see `infra/README.md`), this is all you need:

```bash
pip install -r requirements-dev.txt
pytest
```

`tests/conftest.py` now bootstraps the `sentinel` role and `sentinel` database itself the first time it notices they're missing (via `../scripts/bootstrap_local_db.sh`), the same way `docker compose up` gets them for free from the official Postgres image's `POSTGRES_USER`/`POSTGRES_DB` env vars (see `infra/docker-compose.yml` + `infra/Dockerfile.db`). It does **not** install Postgres itself — you still need a Postgres 16 server with the `postgis`, `pgcrypto`, and `vector` extensions available (`apt install postgresql postgresql-contrib postgresql-16-postgis-3 postgresql-16-pgvector` on Debian/Ubuntu; `brew install postgresql postgis pgvector` on macOS).

If auto-bootstrap can't reach a Postgres superuser on your machine (uncommon setups, restricted permissions, Windows), run it yourself once and pytest picks it up from there:

```bash
bash scripts/bootstrap_local_db.sh
```

That script only touches the `sentinel` role and the base `sentinel` database — it never creates `sentinel_test`. That one is dropped and rebuilt from `shared/db/{schema,triggers,seed}.sql` by `tests/conftest.py` itself, fresh, every test session (exactly what `docker-compose` does in production), so the fixtures always exercise the real seeded departments/districts/cameras rather than stale leftovers.

### `psql` on PATH

`tests/conftest.py` shells out to `psql` (both for the bootstrap step above and to build `sentinel_test`), so `psql` needs to be resolvable when you run `pytest`. It's found automatically if it's on your `PATH` (`shutil.which("psql")`, checked first) or, on Windows, in the default install location (`C:\Program Files\PostgreSQL\<version>\bin`). If neither applies - e.g. a portable/zip install, or a terminal that was already open before installing Postgres and hasn't picked up the updated `PATH` - set `PSQL_PATH` to `psql`'s full path (`psql.exe` on Windows) before running `pytest`, and it'll be used directly with no PATH changes needed:

```powershell
$env:PSQL_PATH = "C:\Program Files\PostgreSQL\16\bin\psql.exe"
pytest
```

### If you're missing a Postgres extension (`postgis` / `vector` not available)

Postgres itself being installed isn't enough — `schema.sql` needs the `postgis`, `pgcrypto`, and `vector` (pgvector) extension binaries actually present on that server, and `pgcrypto` is the only one that ships with vanilla Postgres. If `pytest` fails partway through applying `schema.sql` with `extension "..." is not available`, the error message now tells you exactly which one and how to fix it — but the short version is:

- **Easiest, especially on Windows** (pgvector requires compiling with Visual Studio's C++ build tools there): stop your local Postgres service and let Docker provide just the database instead — its image already bundles all three extensions:
  ```powershell
  cd infra
  $env:SECRET_KEY = "local-dev-only-not-secret"   # required by docker-compose.yml even though we're only starting `db`
  docker compose up -d db
  cd ..\model1-registry
  pytest
  ```
  `pytest` connects to `127.0.0.1:5432` regardless of whether that's your local Postgres or this container, so nothing else about the test setup changes.
- **Installing extensions directly**: `postgis` and `pgvector` are one-line package installs on Linux/macOS (e.g. `apt install postgresql-16-postgis-3 postgresql-16-pgvector` on Debian/Ubuntu, `brew install postgis pgvector` on macOS) but need a full compile-from-source on Windows — see https://github.com/pgvector/pgvector#windows.

### If `pytest` seems to hang

Every `psql` call the suite makes has a hard timeout, so a genuinely broken connection now fails loudly instead of hanging forever. If a run still looks stuck, it's almost always one of:

- **A stale connection blocking `DROP DATABASE sentinel_test`** — from a previous run that was killed mid-test. `tests/conftest.py` now terminates other backends on `sentinel_test` before dropping it, so this shouldn't happen anymore, but if it does: `psql -U sentinel -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'sentinel_test';"`.
- **No network access.** The app's background camera-catalogue poller (`app/main.py`'s `lifespan()`) makes a real HTTPS call to the live grid on every startup and retries with backoff on failure — normal in production, but a real problem for a `TestClient` started once per test in a sandboxed/offline environment. `tests/conftest.py` sets `DISABLE_CATALOGUE_POLL=true` before any app import specifically to skip this during tests; if you're running the app itself (not the test suite) with no network access, you'll still see this.

Each test runs inside its own transaction + `SAVEPOINT` that's rolled back afterward, so tests can freely create/update/delete rows (including through the real API, which calls `db.commit()`) without leaking state between tests or needing a reseed per test.

Coverage: auth (login/logout/role checks), camera CRUD + department-scoped RBAC (create/update/delete cross-department 403s, bulk import), districts (including a regression guard for the empty-`districts`-table seed bug), and gap-analysis geodesic math (coverage bounds, radius scaling, real-world area sanity checks per district).
