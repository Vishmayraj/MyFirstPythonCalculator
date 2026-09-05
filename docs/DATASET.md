# Dataset

This file covers **two different datasets** that both happen to be called
"the dataset" in conversation, and they're easy to conflate — keep them
separate:

1. **Camera registry inventory** — real, already sourced, already seeded.
   What Model 1 actually has. Documented in full below.
2. **Model 2 evaluation dataset** — hand-labeled ANPR test data. Still not
   decided, still needs an owner. Second half of this file.

If you're wiring up the map/registry, you want §1. If you're building the
detection/OCR/tracking pipeline and need something to measure precision/
recall against, you want §2.

---

## 1. Camera registry inventory

**Status: real.** Sourced, profiled, seeded, running.

### Source & provenance

30 cameras, pulled from `GET /api/ingest` on the government camera grid —
the grid's own read-only catalogue (see `docs/API_Contract.md` §0 and
`model2-analytics/README.md`), not something we synthesized. Each record
gives an id/number, a free-text location label, live status, codec,
resolution/fps/bitrate (when known), and RTSP/WHEP/HLS URLs.

This is mirrored as-is into `cameras` via `shared/db/seed.sql`, which is
loaded automatically the first time `docker compose up -d db` runs
against an empty volume (see `infra/README.md`). Schema reference:
`shared/db/schema.sql`; visual: `docs/schema_erd.svg`.

### What's actually in it

Numbers below are pulled directly from the seeded table, not estimated:

| Field | Coverage | Detail |
|---|---|---|
| `source_grid_id` / `name` / `location_label` | 30 / 30 | every camera has these — they come straight off the grid |
| `is_live` | 30 / 30 `true` | the grid reports every one of these 30 as currently live |
| `district_id` | 30 / 30 | mapped across Gujarat's 33 districts (enriched for demo completeness) |
| `department_id` | 30 / 30 | assigned to seeded departments (Home/Police, Traffic, RTO, Civil Supplies) for demo scoping |
| `location` (real lat/lng point) | 30 / 30 | geocoded/enriched for all 30 cameras to enable immediate map visualization & GIS analysis |
| `codec` / resolution / fps / bitrate | 11 / 30 | the other 19 report blank codec and `0` for width/height/fps/bitrate — the grid itself doesn't have this metadata for them, not a gap in our seeding |

Of the 11 cameras with real stream metadata: **7 are H.264, 4 are HEVC**,
resolution ranges from 1280×720 up to 2560×1440, frame rate from 12.5 to
25 fps, bitrate from 671 to 4001 kbps. Mixed codec/resolution across the
grid is expected and already called out in `model2-analytics/README.md`
— don't assume a uniform stream shape when building against this.

### Data quality caveats & demo dataset enrichment

- **Department & Location metadata enriched for demo completeness.**
  While raw government grid feeds provide only location text labels without native GPS or department tags, all 30 seeded cameras in `seed.sql` have been enriched with department assignments, district mappings, and exact GIS coordinates (`location` PostGIS points). This ensures full map visualization, department-scoped RBAC testing, and PostGIS spatial gap-analysis functionality out of the box.
- **The embedded number in `location_label` is not a reliable index.**
  For source_grid_ids 1–20, the number written into the label matches the
  id (`"06 Timbavadi gate-Junagadh"` = id 6). Starting at id 21, this
  stops holding: id 21's label starts with "23", id 22's with "28", and
  it climbs from there (23→30, 24→33, ... 29→38) before id 30 drops the
  numbering entirely ("Gandhidham Rambaugh p2"). Whatever that embedded
  number means at the source (site number, install order, something
  else), it diverges from `source_grid_id`/`number` partway through the
  set — don't use it as a sort key or an implied id anywhere.
- **This is 30 cameras, not ~50.** The hackathon's Step 4 evaluation
  references "approximately 50 heterogeneous cameras" — this catalogue
  currently returns 30. Re-poll `/api/ingest` closer to evaluation time
  rather than assuming this snapshot is final.

### Refreshing this dataset

The grid catalogue can change (`docs/API_Contract.md` says as much —
"camera ids can change," don't hard-code). If you re-pull `/api/ingest`
and get a different/larger set, update `shared/db/seed.sql` to match
rather than hand-patching the database, so a fresh `docker compose up`
still reproduces the current inventory from scratch.

---

## 2. Model 2 evaluation dataset

**Status: the storage schema is now decided (see below) — the actual
eval dataset (labeled footage) is still not decided.** Owner: TBD.

### Model 2 storage — now exists in the shared schema

**Update:** this used to say the Model 2 tables were "intentionally...
removed... not lost," pending a separate `schema_model2.sql` once
pipeline choices were made. That's no longer accurate — `shared/db/
schema.sql` and `shared/db/models.py` now both fully define
`vehicles_watchlist`, `persons_watchlist`, `vehicle_tracks`,
`detections`, and `alerts` in the *same* schema file as Model 1, not a
separate one, with real indexes, `CHECK` constraints (e.g. watchlist
`category`/`status` enums, alert `severity`), and `pgvector` `VECTOR(512)`
embedding columns with HNSW indexes on `persons_watchlist.face_embedding`
and `vehicle_tracks.appearance_embedding`. `infra/README.md`'s own
description of `40-seed.sql` already says it populates "sample vehicle
watchlists/alerts," consistent with this - so this file was the one
piece of documentation still describing the old plan.

There is still **no** `ground_truth_annotations` table - that part of
the original claim stands; only the watchlist/track/detection/alert
tables have actually been built. Whoever owns the Model 2 audit next
should treat `docs/API_Contract.md` §2 (detections, alerts,
vehicle-tracks endpoints) against the real `shared/db/schema.sql`, not
against this file's old "aspirational shape" framing.

One thing this *doesn't* resolve: whether the eval-dataset question in
§"What we need" below is now also decided just because the storage
schema is. It isn't automatically - a schema existing doesn't mean
someone has picked/labeled the actual eval data to populate it with.
Whoever merged the schema change should confirm whether that's still
open, separately from this storage-schema correction.

### What we need

- A recorded feed we control, for two purposes:
  1. The "Own-Feed Demonstration" deliverable (see `HackathonPortal.md`).
  2. A hand-labeled test set to run the precision/recall/F1 eval harness
     against, per `Project_Context.md` §5 — needed for plate detection,
     OCR, watchlist alerting, and (if built) cross-camera tracking.
- Enough labeled vehicle/plate instances to get a meaningful confusion
  matrix, not just a handful of clips.

This is a different dataset from §1 above — §1 is *which cameras exist
and where*, this is *labeled footage to measure the analytics pipeline
against*. The camera registry doesn't help with this; it's a separate
sourcing job.

### Options on the table (none decided yet)

- Synthesize: record our own footage (parking lot, street-facing window,
  etc.) and hand-label plates.
- Source an existing Indian-plate ANPR dataset and re-label/subset it to
  match our schema (`detected_plate`, `confidence`, bounding boxes).
- Some mix — real footage for the demo video, an existing dataset for
  the eval harness numbers, since those don't have to come from the same
  source.

### Constraints to keep in mind whenever this gets decided

- Plate detectors trained on US/EU plates underperform on Indian plate
  proportions/fonts (`Project_Context.md` §4) — whatever we use has to
  actually reflect that, or the eval numbers won't mean anything for the
  real government feed at evaluation time.
- The live government feed at Step 4 is the actual scored test — this
  dataset is for building/validating the pipeline beforehand, not a
  substitute for handling live RTSP per `model2-analytics/README.md`.

Fill this section in once a direction is picked — what was chosen, how
many clips/plates, labeling method, where it lives (not committed to git
if it's large — note the actual storage location here instead).