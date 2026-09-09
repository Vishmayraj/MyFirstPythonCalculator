# shared/ — Shared Domain Foundation

Code that both `model1-registry` and `model2_analytics` share. Model 1 and Model 2 run within the same FastAPI application context (see `Project_Context.md` §2), sharing database connections and schema models.

## `db/`

SQLAlchemy models (`shared/db/models.py`) + raw SQL scripts (`schema.sql`, `triggers.sql`, `seed.sql`).
Defines tables for both models:
- **Model 1**: `departments`, `districts`, `users`, `cameras`, `status_history`.
- **Model 2**: `vehicles_watchlist`, `persons_watchlist`, `vehicle_tracks`, `detections`, `alerts`.

## `schemas/`

Pydantic request/response schemas:
- `camera.py` — `Camera`, `CameraCreate`, `CameraUpdate`, `GeoJSONPoint`, `BulkImportResult`.
- `department.py` — `Department`, `DepartmentCreate`.
- `district.py` — `District`, `DistrictCreate`.

## `adapters/`

VMS adapter interface definitions (`connect()`, `get_stream()`, `get_metadata()`) for vendor camera integrations.

## Status

✅ **Implemented** — SQLAlchemy ORM models, Pydantic schemas, triggers, and seed script are fully configured and synced with `schema.sql`.
