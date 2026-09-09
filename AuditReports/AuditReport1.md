# Sentinel - infra + model1-registry Audit Report

**Repo:** `github.com/Vishmayraj/MyFirstPythonCalculator` (internally: *Sentinel, Gujarat CCTV Integration Platform*)
**Commit audited:** `11ac22f` (`Merge pull request #10 from Phonicxxxx24/main`, 2026-09-04)
**Scope:** `infra/` + `model1-registry/` + the parts of `shared/` they depend on, plus repo-wide hygiene (`.gitkeep`, dead files). **`model2-analytics` / `model2_analytics` were not audited** - that's the next pass.
**This is a report only.** Nothing in the repo was modified. A prior session (transcript pasted into this one) had started applying a fix to `config.py` directly, but that fix was never merged, so the clone this report is based on is fully unfixed - every finding below is live in the current `main`.

## How to use this document

1. Read the **at-a-glance table** first, then the **suggested execution order** at the end - that's the actual priority queue.
2. Each finding gives the exact file (and line/function where useful), *why it matters*, and *what needs to change* at a spec level - not a diff. Whoever implements this still has to write the code.
3. If real time has passed since `11ac22f` before this gets picked up, assume drift is possible - spot-check a finding against current `main` before acting on it, the same way this session verified HEAD hadn't moved since the previous one.
4. Section 8 ("What's already solid") is deliberately included so nobody re-"fixes" things that are already correct.

---

## At-a-glance

| # | Finding | File(s) | Severity |
|---|---|---|---|
| 1 | Live camera video is served with **zero authentication** | `model1-registry/app/routers/streams.py` (all 4 endpoints) | Critical |
| 2 | Camera registry read endpoints have **no auth dependency at all** | `cameras.py`: `list_cameras`, `get_camera`, `get_camera_history` | Critical |
| 3 | `departments` and `districts` list endpoints are unauthenticated | `departments.py`, `districts.py` | Critical |
| 4 | JWT signing key is a hardcoded constant, with **no override path in docker-compose at all** | `config.py` line ~9; `infra/docker-compose.yml` | Critical |
| 5 | Detection-image static mount has no auth | `main.py` (`DETECTION_IMG_DIR` mount) | Critical |
| 6 | No TLS anywhere in `infra/`, despite docs saying it's planned; auth cookie missing `secure=True` | `infra/`, `routers/auth.py` | High |
| 7 | No rate limiting / lockout on login | `routers/auth.py` | High |
| 8 | Global audit log isn't role-scoped | `routers/audit.py`, `routers/pages.py` (`audit_page`) | High |
| 9 | Root README's doc link is a dead local Windows path, leaks a machine/folder name | `README.md` | High |
| 10 | Same external grid IP hardcoded in 4 places, some of it dead config | `config.py`, `infra/mediamtx.yml`, `infra/README.md`, `model2-analytics/app/ingestion/catalogue.py` | Medium |
| 11 | MediaMTX CORS origins hardcoded to `localhost:8000` - will silently break on any real deployment | `infra/mediamtx.yml` | Medium |
| 12 | `docker-compose.yml` hardcodes DB URL/password with no env override; `SECRET_KEY` isn't wired through at all | `infra/docker-compose.yml` | Medium |
| 13 | `.gitignore` pattern doesn't catch nested `.pt` files - 6 MB model weights committed to git | `.gitignore`, `model2-analytics/pipeline/detection/indian_traffic_yolov8.pt` | Medium |
| 14 | Unused heavy deps (`ultralytics`, `requests`) + fully unpinned versions | `model1-registry/requirements.txt` | Medium |
| 15 | `docs/DATASET.md` is stale - claims Model 2 tables don't exist; they do | `docs/DATASET.md` vs `shared/db/schema.sql` | Medium |
| 16 | No CI at all | *(repo-wide)* | Medium |
| 17 | Dynamic model2-router loader in model1's own entrypoint is a fragile pattern worth documenting | `main.py` | Medium |
| 18 | Config sprawl: `DISABLE_INGESTION` bypasses the central `Settings` object | `main.py`, `config.py` | Medium |
| 19 | 10 of 12 `.gitkeep` files are stale; 2 are legitimately still needed | *(listed below)* | Low |
| 20 | `/alerts` placeholder page has no login guard (every other page does) | `pages.py` | Low |
| 21 | Dockerfile runs as root | `infra/Dockerfile` | Low |
| 22 | One live-stream endpoint bypasses the standard `get_db` pattern | `streams.py` (`stream_camera_by_uuid`) | Low |

---

## 1. Critical findings

### 1.1 Live video is completely open - the single most important finding in this report

`model1-registry/app/routers/streams.py` defines four endpoints and **not one of them has an auth dependency**:

- `GET /api/v1/cameras/grid/{grid_id}/frame`
- `GET /api/v1/cameras/grid/{grid_id}/live`
- `GET /api/v1/cameras/{camera_id}/live`
- `GET /api/v1/streams/catalogue`

The server holds real credentials (`GRID_RTSP_USER` / `GRID_RTSP_PASS`) and uses them to connect upstream to the government camera grid (`_build_authenticated_rtsp_url`), decodes the feed, and then re-serves the *decoded video* to whoever hits these paths - no cookie, no bearer token, nothing. Anyone who finds the host can watch any of the 30 onboarded government CCTV feeds. The `get_stream_catalogue` docstring says "*Always returns sanitized, public URLs (passwords protected in backend config)*" - that part is true (see Section 8, it correctly avoids leaking the RTSP password into the URLs it returns) - but it solves a different problem than the one that matters: the catalogue's own `frame_url`/`mjpeg_url` fields point straight at this app's unauthenticated proxy endpoints, so "no password in the URL" doesn't mean "no unauthenticated access to the video."

This directly contradicts the project's own stated design - `Project_Context.md` Section 6 ("RBAC... Enforced at the API layer, not just hidden in the UI") and `docs/API_Contract.md` Section 0 ("Enforced at the router dependency level, not just hidden in the UI" - marked decided in that doc) - and it's also the kind of gap that directly undercuts the "Enhanced cybersecurity... role-based access controls" bonus-scoring line in `HackathonPortal.md` Step 7.

**What needs to change:** add an auth dependency (`Depends(get_current_user)` at minimum; consider `require_role("dept_admin", "operator")` if viewers shouldn't get live video, that's a product decision, not a code one) to all four endpoints. Confirmed safe to do without breaking the existing UI: `map.js`/the live-grid pages call these same-origin, so the browser already sends the `access_token` cookie automatically - no frontend change needed for logged-in users.

**Test coverage:** currently **zero** - no test file references any of these four endpoints at all. This is the biggest single gap in the whole test suite (see Section 6).

### 1.2 Camera registry read endpoints have no auth dependency

In `cameras.py`:

- `list_cameras` (`GET /api/v1/cameras`) - signature is `(department_id, district_id, connectivity_status, is_active, db: Session = Depends(get_db))`. No `current_user` parameter at all.
- `get_camera` (`GET /api/v1/cameras/{camera_id}`) - same, just `db: Session = Depends(get_db)`.
- `get_camera_history` (`GET /api/v1/cameras/{camera_id}/history`) - same.

Compare this to the mutating endpoints in the same file (`create_camera`, `bulk_import`, `update_camera`, `delete_camera`), which all correctly use `Depends(require_role("dept_admin"))` with proper department-scoping (see Section 8) - this isn't a "the team doesn't know how" gap, it's specifically the **read path** that was missed everywhere.

The response model (`shared/schemas/camera.py::Camera`) includes `rtsp_url`, `whep_url`, `hls_url`, exact `location` (lat/lon), `department_id`, `connectivity_status`, storage/retention metadata - the entire registry, unauthenticated. One nuance worth confirming precisely before treating this as a credential leak: `cam.rtsp_url` as stored in the DB is a plain, creds-free URL (confirmed via `model2-analytics/app/ingestion/catalogue.py` line 63 - `rtsp://103.250.160.189:8554/stream/cam{i:02d}`, no embedded user:pass), so this specific finding is "leaks full registry topology + exact camera GPS coordinates + which cameras are online/offline to the public internet," not "leaks the RTSP password" (that's finding 1.1's territory, and it's arguably worse since it's live video, not metadata).

**What needs to change:** add `current_user: UserModel = Depends(get_current_user)` to all three (no role restriction needed here - `test_operator_and_viewer_can_login_but_are_not_dept_admin` already asserts operator/viewer can read cameras, so this is "must be logged in," not "must be dept_admin").

### 1.3 `departments` and `districts` list endpoints are unauthenticated

`model1-registry/app/routers/departments.py::list_departments` and `districts.py::list_districts` both take only `db: Session = Depends(get_db)` - no auth at all. Same root cause and same fix as 1.2: add `Depends(get_current_user)`.

### 1.4 Hardcoded JWT signing key, with no override path wired into deployment at all

`model1-registry/app/config.py`:
```python
SECRET_KEY: str = "sentinel-secret-key-hackathon-2026-secure"
```
This key signs every JWT (`auth/security.py::create_access_token`/`decode_access_token`). It's a *different kind of problem* than the intentionally-public demo login passwords (`password123` - that's fine, `HackathonPortal.md`'s own submission process expects teams to hand out test credentials to judges). The signing key is not meant to be public at all: anyone who has read this file on GitHub can mint a valid session token for **any user, any role**, on any deployed instance that hasn't had this value manually overridden - including a "hosted platform" URL, which `HackathonPortal.md` Step 5 explicitly invites teams to submit alongside the repo link. That means the login form itself becomes optional for an attacker.

It's worse than "just add validation," though: `infra/docker-compose.yml`'s `app` service passes through `GRID_HOST`, `MEDIAMTX_API`, `DISABLE_INGESTION`, `GRID_RTSP_USER`, `GRID_RTSP_PASS` as `${VAR:-default}`, but **there is no `SECRET_KEY` line in docker-compose.yml at all** - so even a deployer who wants to override it via an env var can't, without hand-editing the compose file itself.

**What needs to change (two parts, both needed):**
- `config.py`: stop shipping a default that *works*. Either require `SECRET_KEY` with no fallback (fails fast with a clear message if unset), or fail fast specifically when it still equals the checked-in value *and* the app isn't in an explicit local-dev mode - whichever the team prefers, but "silently signs real tokens with a public constant" shouldn't be a reachable state.
- `infra/docker-compose.yml`: add a `SECRET_KEY: "${SECRET_KEY:?SECRET_KEY must be set}"` (or similar) line to the `app` service's `environment:` block so there's an actual mechanism to set it without editing the compose file, and document it in `.env.example` (see Section 5).

### 1.5 Detection-image static mount has no auth

`main.py` mounts `DETECTION_IMG_DIR` (cropped vehicle/plate images from Model 2's detection pipeline) at `/detection-image` via plain `StaticFiles`, with no auth wrapper of any kind. These are exactly the kind of images a watchlist-alert system produces - arguably as sensitive as the live video in 1.1. `StaticFiles` doesn't do directory listing, so this needs a knowable filename to exploit, but filenames coming from an automated pipeline (camera_id + timestamp patterns, etc.) are plausibly guessable/enumerable.

**What needs to change:** this one's slightly more involved than adding a `Depends` (you can't put a FastAPI dependency directly on a `StaticFiles` mount) - options are a small wrapping route that checks auth before serving the file, or moving this behind a reverse-proxy `auth_request` rule once TLS/proxy is in place (finding 1.6). Flag this precisely for whoever picks it up rather than treating it as a one-line fix.

---

## 2. High-priority findings

### 2.1 No TLS anywhere in `infra/`; auth cookie missing `secure=True`

`Project_Context.md` Section 6 lists "Transport encryption: TLS everywhere via Caddy/Nginx, already planned" as a thing they build (not just document). `infra/` contains `Dockerfile`, `Dockerfile.db`, `docker-compose.yml`, `mediamtx.yml` - **no Nginx or Caddy config anywhere**. Separately, `routers/auth.py::login` sets the cookie with `httponly=True, samesite="lax", path="/"` but no `secure=True` - meaning even once TLS exists, the cookie itself doesn't refuse to travel over plain HTTP.

**What needs to change:** add the reverse-proxy config the docs already claim exists (Caddy is the lower-effort option - automatic HTTPS with a real domain, minimal config), and add `secure=True` to the cookie once there's a TLS endpoint for it to matter on (setting `secure=True` before TLS exists would break local `http://localhost:8000` development, so sequence this - e.g., gate it on `settings.DEBUG` the same way other prod/dev splits are handled).

### 2.2 No rate limiting or lockout on `/api/v1/auth/login`

Confirmed by reading `routers/auth.py` in full: no throttling of any kind. Low urgency for the hackathon demo itself (the demo accounts are meant to be publicly known), but worth flagging clearly as a pre-production gap, and relevant sooner if this login form is ever pointed at real, non-demo accounts.

### 2.3 Global audit log isn't role-scoped

`routers/audit.py::get_global_audit_log` and `pages.py::audit_page` both require *some* logged-in user (`Depends(get_current_user)`), correctly - but neither restricts by role or department. A `viewer1` account (no department, read-only per the spec) can currently pull the complete cross-department change history for every camera in the system. `Project_Context.md` Section 3 calls for "**role-based** search, filter, export, and metadata audit trail" - whether the intended design is "dept_admin sees only their department's history" or "audit is deliberately admin-only, full stop" isn't fully specified in the docs as written, so this is flagged as a **spec question to resolve**, not a fix to just apply - whoever picks this up should decide the intended scoping (dept-admin-only? role-gated to dept_admin/operator? per-department filtering for dept_admins?) before changing the endpoint.

### 2.4 Root `README.md`'s own doc link is broken and leaks a local machine path

```markdown
See [Project_Context.md](file:///c:/Users/Asus%20f15/QuantumMachineLearning/Project_Context.md) ...
```
This is an absolute Windows filesystem path from whoever's machine last edited this line - broken for literally everyone else, and it incidentally names a personal machine ("Asus f15") and an unrelated project folder ("QuantumMachineLearning"). Trivial fix: `[Project_Context.md](./Project_Context.md)`, and worth a quick look for the same mistake anywhere else in the docs.

---

## 3. Medium-priority findings

### 3.1 The same external grid IP is hardcoded in four places, and some of it is dead config

`103.250.160.189` (the government camera grid's address) appears as a source-committed default/literal in:
- `config.py` - `GRID_RTSP_HOST: str = "103.250.160.189"`
- `infra/mediamtx.yml` - as a literal in all 30 `paths: camNN: source: rtsp://103.250.160.189:8554/...` entries
- `infra/README.md` - in its own "Dynamic Stream Registration" curl example
- `model2-analytics/app/ingestion/catalogue.py` line 63 - as an f-string fallback

Whether this specific IP counts as sensitive is genuinely unclear from this repo alone (it may just be the address every hackathon team is handed via the Resources page - `docs/DATASET.md` and `HackathonPortal.md` both suggest the grid feed is shared infrastructure for the evaluation, not a secret). Flagging it here primarily as a **maintainability** issue regardless of sensitivity: `docs/DATASET.md` itself already warns "camera ids can change... don't hard-code," and this pattern hardcodes the actual host in four independent places that would all need to be found and changed together if it ever does change.

More concretely actionable: `infra/README.md`'s own "Dynamic Stream Registration" section says *"Streams are registered at runtime by the IngestionSupervisor - NOT statically in mediamtx.yml"* - which means the 30 static `paths:` entries in `mediamtx.yml` **contradict the documented architecture** and are likely dead config left over from an earlier approach. Worth confirming with whoever wrote the ingestion supervisor, then deleting the static paths if confirmed dead.

### 3.2 MediaMTX CORS is hardcoded to `localhost:8000`

```yaml
hlsAllowOrigin: 'http://localhost:8000'
webrtcAllowOrigin: 'http://localhost:8000'
```
Good that CORS is restricted at all (comment says "to prevent unauthorized embedding" - correct instinct). But the moment this gets deployed to a real VPS URL for the hosted-platform-plus-test-credentials submission path `HackathonPortal.md` describes, HLS/WebRTC playback will silently fail with a CORS error in the browser console, because the deployed origin won't be `localhost:8000` anymore. This should be templated/env-driven (or at minimum, this needs to be on the pre-deployment checklist so it doesn't get discovered live in front of judges).

### 3.3 `docker-compose.yml` hardcodes DB credentials with no override path; `SECRET_KEY` isn't wired at all

`DATABASE_URL: "postgresql://sentinel:sentinel_dev@db:5432/sentinel"` and `POSTGRES_PASSWORD: sentinel_dev` are both literal strings in the compose file, unlike `GRID_HOST`/`MEDIAMTX_API` which correctly use the `${VAR:-default}` passthrough pattern. Bringing `DATABASE_URL` in line with that same pattern (and adding the `SECRET_KEY` passthrough from finding 1.4) means a deployer can override everything that matters via `.env`/environment, without editing YAML.

### 3.4 `.gitignore` doesn't actually catch the committed model weights

```
model2-analytics/pipeline/*.pt
```
only matches `.pt` files directly inside `pipeline/`, not in subdirectories - gitignore's single `*` doesn't cross a `/`. The real file is at `model2-analytics/pipeline/detection/indian_traffic_yolov8.pt` (6.0 MB, confirmed committed via `git ls-tree`/`find` in this session). The pattern needs `model2-analytics/pipeline/**/*.pt` (or broaden it to `model2-analytics/**/*.pt`) to actually work, **and** the already-tracked file needs `git rm --cached` - fixing `.gitignore` alone won't remove something already committed.

### 3.5 Unused heavy dependencies + zero version pinning in `model1-registry/requirements.txt`

```
opencv-python-headless   # used - streams.py imports cv2 directly
requests                 # no `import requests` anywhere under model1-registry/app/ (confirmed by grep)
ultralytics              # no import anywhere under model1-registry/app/ either - only appears as template copy-text ("YOLOv8") in two .html files, never actually imported
```
`ultralytics` in particular pulls in PyTorch/torchvision - a large, slow install for a dependency that (as far as this file's own app code is concerned) does nothing. It's presumably needed by `model2-analytics`, which already has its own `requirements.txt`. Also worth doing while touching this file: every dependency here is unpinned (`sqlalchemy>=2.0` is a floor, not a pin; everything else has no version at all) - for a repo about to be handed to a jury/evaluator on a specific date, an unpinned `pip install` on install day can pull a different (possibly breaking) version than what was last tested against.

### 3.6 `docs/DATASET.md` is stale relative to the actual schema

`docs/DATASET.md` Section 2 states, at length, that `shared/db/schema.sql` "covers Model 1 and the shared foundation only" and that `detections`, `vehicle_tracks`, `vehicles_watchlist`, `persons_watchlist`, and `alerts` are "intentionally... removed... not lost," to be added later in a `schema_model2.sql`.

That's no longer true: the current `shared/db/schema.sql` and `shared/db/models.py` **both fully define** `vehicles_watchlist`, `persons_watchlist`, `vehicle_tracks`, `detections`, and `alerts` (with real indexes, check constraints, and pgvector embedding columns) - and `infra/README.md`'s own description of `40-seed.sql` already says it populates "sample vehicle watchlists/alerts," consistent with the real schema, not with `DATASET.md`'s claim. This is exactly the kind of drift `Project_Context.md`'s own header warns about ("stale context here is worse than no context") - and it matters beyond just tidiness, because whoever picks up the Model 2 audit next will be misled about what schema they're building against if they trust `DATASET.md` over the actual `schema.sql`.

**What needs to change:** rewrite `docs/DATASET.md` Section 2 to reflect that the Model 2 schema now exists in the main `schema.sql` (not a separate `schema_model2.sql`), and reconcile this with whatever the actual current intent is - this may also be a signal that whoever merged that schema change should be looped in on whether the DATASET.md Section 2 "not yet decided" framing is even still accurate for the eval-dataset question, separate from the storage-schema question.

### 3.7 No CI/CD at all

No `.github/workflows` (or any CI config) anywhere in the repo. Given "many PRs have been merged" while the project owner was away, nothing has been automatically confirming the (real, Postgres+PostGIS-backed) test suite still passes on each merge - this repo is exactly the kind of case where that matters, since the suite needs a live DB and can't be casually run by a reviewer glancing at a PR diff. A minimal workflow (spin up a `postgis/postgis` service container, `pip install -r requirements-dev.txt`, `pytest`) would close this gap; postgis + pgvector packages are confirmed available via plain `apt` (see Section 6), so this doesn't need anything exotic.

### 3.8 Dynamic model2-router loader lives in model1's own entrypoint

`main.py` scans `model2-analytics/app/routers/*.py` at startup via `importlib.util`, `exec`s each file, and mounts whatever `router` attribute it finds - with a broad `except Exception` around each load. This is a real architectural choice (not obviously a bug), but it means anything dropped into that directory gets live-mounted into the running app with no review step baked into the code itself, and it's not documented anywhere as an intentional pattern. Flagging for documentation (`main.py`'s own docstring, or `model1-registry/README.md`) rather than as something to change - whether it should change is a design call for whoever owns this, not something this audit is asserting.

### 3.9 `DISABLE_INGESTION` bypasses the centralized config object

Every other setting flows through `app.config.settings` (pydantic-settings). `DISABLE_INGESTION` is read directly via `os.environ.get("DISABLE_INGESTION", "false")` in `main.py`'s `lifespan()`, and isn't a field on `Settings` at all, even though `docker-compose.yml` passes it through as if it were consistent with the others (`DISABLE_INGESTION: "${DISABLE_INGESTION:-true}"`). Low-stakes, but worth folding into `Settings` for consistency next time `config.py` gets touched anyway (e.g., for finding 1.4).

---

## 4. Low-priority / hygiene findings

### 4.1 `.gitkeep` disposition - precise, not a blanket "remove them all"

| Path | Directory now contains | Disposition |
|---|---|---|
| `shared/schemas/.gitkeep` | 7 real files | **Remove** - stale |
| `shared/adapters/.gitkeep` | 5 real files | **Remove** - stale |
| `model1-registry/app/routers/.gitkeep` | 9 real files | **Remove** - stale |
| `model1-registry/app/static/.gitkeep` | `css/`, `js/` with real content | **Remove** - stale |
| `model1-registry/app/templates/.gitkeep` | 15 real templates | **Remove** - stale |
| `model2-analytics/app/routers/.gitkeep` | 5 real files | **Remove** - stale |
| `model2-analytics/pipeline/plate/.gitkeep` | 2 real files | **Remove** - stale |
| `model2-analytics/pipeline/tracking/.gitkeep` | 5 real files | **Remove** - stale |
| `model2-analytics/pipeline/ocr/.gitkeep` | only `__init__.py` (no real OCR impl yet - separate, out-of-scope observation) | **Remove** - `__init__.py` already keeps the dir tracked |
| `model2-analytics/pipeline/detection/.gitkeep` | 4 real files + the `.pt` weights | **Remove** - stale |
| `model2-analytics/detection-image/.gitkeep` | **nothing else** - genuinely empty | **Keep** - this is a real runtime output dir |
| `model2-analytics/uploads/.gitkeep` | **nothing else** - genuinely empty | **Keep** - real runtime upload dir |

10 of 12 are safe to delete; the last two are doing their actual job (both are already correctly referenced in `.gitignore`'s own `!model2-analytics/detection-image/.gitkeep` / `!model2-analytics/uploads/.gitkeep` negation lines, which is a good sign the original author understood the distinction even if the other 10 were never cleaned up).

### 4.2 `/alerts` placeholder page has no login guard

`pages.py::alerts_placeholder` is the only page route with no `user` parameter and no redirect check - every other page route (`/`, `/live`, `/cameras`, `/departments`, `/districts`, `/audit`, `/gap-analysis`, `/detections`, `/recorded-detection`, `/watchlist`) redirects anonymous visitors to `/login`. Low stakes since it renders a static "not built yet" placeholder with no real data, but worth matching the pattern for consistency.

### 4.3 Dockerfile runs as root

`infra/Dockerfile` never sets a `USER` - the container runs as root by default. Standard hardening step (add a non-root user, `chown` what it needs, `USER app`), not urgent given this doesn't handle genuinely untrusted input execution, but cheap to do while touching this file for other reasons.

### 4.4 One stream endpoint bypasses the standard DB session pattern

`streams.py::stream_camera_by_uuid` reaches for `shared.db.session._SessionLocal` directly (a "private," underscore-prefixed module attribute) inside a manual `with` block, instead of the `Depends(get_db)` pattern every other endpoint in the codebase uses. Not a security issue, just an inconsistency worth cleaning up alongside the auth fix for this same function (1.1).

---

## 5. Documentation fixes needed

- **`.env.example`** currently documents only `GRID_RTSP_USER`/`GRID_RTSP_PASS`. `config.py`'s `Settings` class has 13 fields total; at minimum `SECRET_KEY`, `DATABASE_URL`, `DEBUG`, and the `GRID_*` host/port settings should be listed here (with a loud comment on `SECRET_KEY` specifically: *must be overridden, no safe default exists*) so a deployer has one place to see everything that's actually configurable.
- **`docs/DATASET.md`** Section 2 - see finding 3.6.
- **`README.md`** - fix the broken local-path link (2.4); worth a once-over for the same class of mistake elsewhere.
- **`infra/README.md`** - once 3.1's "is `mediamtx.yml`'s static `paths:` block dead config" question is resolved, update this doc to match whichever way it's resolved.
- **`model1-registry/README.md`** and **root `README.md`** already accurately describe what's implemented for Model 1 - no changes needed there, they were cross-checked against the actual routers and matched (see Section 8).

---

## 6. Test coverage - explicit gaps, not a test list

The suite is real and reasonably thorough where it exists (department-scoped RBAC on camera mutations, the empty-districts-table regression guard, gap-analysis geodesic math, and the independent-cookie-jar fixture design in `conftest.py` are all genuinely well done - see Section 8). The gaps below are specifically the ones easy to miss because the suite *passes today despite them* - nothing currently fails, which is exactly why they're worth calling out explicitly rather than assuming "green tests" means "no auth gaps":

- **`streams.py` has no test file and is referenced by zero tests.** Given finding 1.1, this is the highest-value place to add coverage - at minimum, an anonymous-401 test per endpoint once the auth dependency is added.
- **No `test_departments.py` or `test_audit.py` exists at all** - both routers currently have 0% coverage, not just gaps in it.
- **No test currently asserts that an anonymous client gets 401 from `GET /api/v1/cameras`, `GET /api/v1/cameras/{id}`, `GET /api/v1/cameras/{id}/history`, `GET /api/v1/departments`, or `GET /api/v1/districts`.** Contrast with `test_gap_analysis.py::test_gap_analysis_requires_auth`, which does exactly this for gap-analysis - the pattern is already established and working in the suite, it just wasn't replicated onto the other read endpoints. That's the fastest way to write these once the auth fix lands: copy that test's shape.
- Once auth is added to the endpoints in Section 1, **every existing test that calls them does so through an already-authenticated fixture client** (`admin_home_client`, `operator_client`, `viewer_client`) - confirmed by reading every test file in full. So the fix is safe to make first and test second; nothing currently green should turn red.
- **Dynamic test execution was not performed in this pass** - this was a static read-through of every relevant file, not a `pytest` run. Running it is feasible in a fresh environment (`postgresql-16-postgis-3` 3.4.2 and `postgresql-16-pgvector` 0.6.0 are both available via plain Ubuntu `apt`, confirmed this session), but `requirements-dev.txt` pulls in `model1-registry/requirements.txt`, which currently includes `ultralytics` (see 3.5) - expect a slow/heavy install until that's trimmed. Whoever picks this up next should actually run `pytest` per `model1-registry/README.md`'s own instructions as an early step, both to confirm nothing regressed across the recent merges and to get a real pass/fail baseline before making changes.

---

## 7. What's already solid - don't spend time re-fixing these

For balance, and so effort isn't wasted double-checking things that are fine:

- Password hashing (`bcrypt` via `auth/security.py`) and JWT handling are both implemented correctly - algorithm is explicitly pinned on decode (`algorithms=[settings.ALGORITHM]`), avoiding the classic "alg: none" class of JWT bug.
- Every SQL statement found in scope - both raw `text()` queries (`gap_analysis.py`, the `SET LOCAL app.current_user_id` calls in `cameras.py`) and ORM usage - is properly parameterized. No injection risk found anywhere in `infra`/`model1-registry`.
- Department-scoped RBAC on the *mutating* camera endpoints (`create`, `update`, `delete`, `bulk`) is correctly implemented and well-tested - cross-department 403s work and are asserted.
- `_build_authenticated_rtsp_url` correctly percent-encodes the RTSP username/password (`quote(..., safe="")`), which specifically guards against a `@` in an email-style username breaking the URI - a real, non-obvious detail that was clearly considered on purpose.
- The stream catalogue endpoint deliberately builds a separate, credential-free "public" RTSP URL for display rather than reusing the internal connection-string builder - the team already understood not to leak the upstream password, they just didn't extend that instinct to "therefore also gate who can reach the proxy."
- Jinja2's default autoescaping is intact throughout - no `|safe` filters or `Markup()` calls anywhere in `model1-registry/app/templates/`, so no template-injection XSS footgun.
- Seed-data passwords are properly `bcrypt`-hashed in `users.hashed_password` - not stored in plaintext anywhere.
- Page-level auth (redirect-anonymous-to-`/login`) is applied consistently across nearly every HTML route, and `camera_new_form`/`camera_edit_form` go a step further, deliberately pre-checking department ownership so a dept_admin is never shown an edit form that's guaranteed to fail on submit - the inline comments explaining *why* this check exists show real care, not just pattern-matching.
- `conftest.py`'s independent-`TestClient`-per-role fixture design (and its comment explaining the cookie-jar bug it's specifically avoiding) is a genuinely good piece of test infrastructure.

---

## 8. Explicitly out of scope this round

- **All of `model2-analytics/` and `model2_analytics/`** - including the ANPR/detection pipeline, the watchlist API, and the duplicate `model2_analytics` (underscore) vs. `model2-analytics` (hyphen) shim-package situation. That duplication was already mapped in detail in the prior session (shim files fully read, sizes compared: real files are 10x+ larger than their shim counterparts) and is reused here only in that it's baked into `infra/Dockerfile`, which `COPY`s and `PYTHONPATH`s all three of `model2-analytics/`, `model2_analytics/`, and a third copy into `/app/model2_analytics_src` - worth knowing about when someone does audit model2, since the infra layer currently ships all of them into the image.
- Whether `persons_watchlist.face_embedding` / facial-recognition-adjacent schema (present in `schema.sql` but marked "bonus scope, likely cut" in `Project_Context.md` Section 4) should exist at all is a Model 2 / product-scope question, not an infra/model1 finding - noted here only so it isn't missed when that audit happens.

---

## 9. Suggested execution order for the next session

Given the "hand this to another Claude" workflow, in priority order:

1. **Findings 1.1-1.5** (the auth gaps + the JWT key). These are the highest-severity items, they're low-risk to fix (existing tests already use authenticated clients, so nothing green should break), and they're the ones that actually matter most for the "role-based access controls" bonus-scoring line.
2. **Add the missing anonymous-401 tests** (Section 6) right alongside each fix above, copying `test_gap_analysis_requires_auth`'s shape - cheapest way to lock the fix in.
3. **Findings 2.1-2.4** (TLS/cookie, rate limiting, audit scoping - this one needs a product decision first, see 2.3 - and the README link).
4. **Section 3 (medium)** and **Section 4 (low)** - genuinely lower stakes, good candidates for a session that's cleaning up rather than fixing security.
5. **Section 5 (docs)** can happen in parallel with any of the above; none of it is blocked on code changes.
6. Once the above lands: run `pytest` for real (Section 6) before calling this round done.