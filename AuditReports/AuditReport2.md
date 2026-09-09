# Sentinel - model2-analytics Audit Report

**Repo:** `github.com/Vishmayraj/MyFirstPythonCalculator` (internally: *Sentinel, Gujarat CCTV Integration Platform*)
**Commit audited:** `6619d16` (`docs: add audit report for findings 1-22`, 2026-09-06)
**Scope:** `model2-analytics/`, the `model2_analytics/` shim package, and the model2-relevant parts of `shared/` (`schemas/watchlist.py`, `schemas/persons_watchlist.py`, `schemas/grid.py`, `schemas/vms.py`, `adapters/`). This is the half explicitly deferred in `AuditReport1.md`.
**This is a report only.** Nothing in the repo was modified.

## How to use this document

Same conventions as `AuditReport1.md`: at-a-glance table first, then the execution order at the end. Each finding names the exact file/function and what needs to change at a spec level, not a diff.

## Context worth knowing before reading the findings

All 22 findings from `AuditReport1.md` were re-verified against the current code in this session (not just the commit messages) and every one holds up, several with extra care beyond what was originally asked for (path-traversal guarding on the detection-image route, IP-plus-username keying on the login rate limiter, department-scoped audit trails on both the API and the page). One small leftover: `departments.py` and `districts.py` each picked up a duplicate import (`get_current_user`, `User as UserModel`) somewhere in a merge - harmless, worth a quick tidy.

Two things changed the shape of this codebase since the first audit that are worth naming up front:

1. **A person watchlist / facial-recognition feature was added** (`app/routers/persons_watchlist.py`, `pipeline/faceembedding/`) - `Project_Context.md` Section 4 explicitly listed this as "bonus/stretch... explicitly cut unless everything else is done early," so this represents a scope decision that reversed since that doc was written. Not a finding on its own, just worth reconciling `Project_Context.md` Section 4's framing with reality (same category of doc-drift as the `DATASET.md` fix in the first round).
2. **A separate PR (`Ahad-Dngwala`) already fixed a real SSRF issue** in `grid.py`'s catalogue-sync endpoint (the `is_safe_url()` function blocking `localhost`, loopback, `169.254.169.254` - the cloud metadata IP, a classic SSRF target - and `.internal` hosts). Confirmed present and correctly used; no action needed, mentioned here so it isn't mistaken for a gap.

---

## At-a-glance

| # | Finding | File(s) | Severity | Done |
|---|---|---|---|---|
| 1 | Hardcoded, specific-looking credential committed to a public repo | `pipeline/config.py` (`CAM04_PASSWORD`) | Critical | ✅ |
| 2 | Live detection feed and pipeline start/stop controls have no auth at all | `app/routers/detections.py` (all 6 endpoints) | Critical | ✅ |
| 3 | Video upload endpoint accepts anonymous, unauthenticated 2 GB uploads | `app/routers/recorded.py` (`upload_recorded_video`) | Critical | ✅ |
| 4 | Every other endpoint in the same router is also unauthenticated | `app/routers/recorded.py` (remaining 6 endpoints) | Critical | ✅ |
| 5 | The `model2_analytics` / `model2-analytics` duplicate-package situation is still live | *(repo-wide, both packages — now consolidated into `model2_analytics/`)* | High | ✅ |
| 6 | Zero test coverage for the entire component | *(no `tests/` directory exists)* | High | ✅ |
| 7 | Dependencies are fully unpinned in a much heavier dependency tree than model1's | `model2_analytics/requirements.txt`, `pipeline/requirements.txt` | High | ✅ |
| 8 | Raw exception text echoed back to API callers | `app/routers/detections.py` | Medium | ✅ |
| 9 | Fragile (currently safe) f-string-built SQL WHERE clause | `app/routers/detections.py` (`detection_history`) | Medium | ✅ |
| 10 | Face-photo upload has no size limit, unlike the video upload | `app/routers/persons_watchlist.py` | Medium | ✅ |
| 11 | `model2_analytics/README.md` is stale and contains an unedited personal note | `model2_analytics/README.md` | Medium | ✅ |
| 12 | Two different grid domains appear in different places with no reconciliation | `model2_analytics/README.md` vs `config.py`/`catalogue.py` | Medium | ✅ |
| 13 | Face-detection model downloaded at runtime with no integrity check | `pipeline/faceembedding/quality_checker.py` | Low | ✅ |
| 14 | Minor REST convention inconsistency (query params instead of a body on a PATCH) | `app/routers/persons_watchlist.py` (`update_watchlist_person`) | Low | ✅ |
| 15 | Confirm intended behavior: `/api/ingest` requires login here, but the *source* grid's own version doesn't | `app/routers/grid.py` (`get_ingest_catalogue`) | Info - needs a decision, not a fix | ⏸️ needs decision |

---

## 1. Critical findings

### 1.1 A specific, real-looking credential is hardcoded in `pipeline/config.py`

```python
CAM04_RTSP = "rtsp://103.250.160.189:8554/stream/cam04"
CAM04_HLS = "https://cctv.corp8.cloud/cam04/index.m3u8"
CAM04_PASSWORD = "4VAE-DVDM-MW48"
```

Unlike `SECRET_KEY`'s old default (at least framed with a comment as an insecure placeholder) or `GRID_RTSP_USER`/`GRID_RTSP_PASS` (which correctly read from environment variables everywhere else in this codebase), `CAM04_PASSWORD` is a bare literal with no environment-variable indirection at all, and its format (`4VAE-DVDM-MW48`) looks like a real generated credential, not a placeholder like `changeme`.

Traced its usage across the whole `pipeline/` tree: `CAM04_RTSP` is imported and used in `live_demo.py` and `orchestrator.py`; `CAM04_PASSWORD` itself is **never referenced anywhere** - it's dead code. That doesn't reduce the exposure, though: it's already been committed to a public GitHub repository, so if it's a genuine working credential for anything, treat it as compromised regardless of whether current code paths use it.

**What needs to change:** remove the constant, rotate whatever it authenticates to (if it's real and still active), and if a per-camera credential is genuinely needed later, wire it through an environment variable the same way `GRID_RTSP_USER`/`GRID_RTSP_PASS` already work in `model1-registry/app/config.py` and `shared/adapters/factory.py`.

### 1.2 Live detection feed and pipeline controls have no authentication

`app/routers/detections.py` defines six endpoints and none of them have any auth dependency:

- `GET /api/v1/detection/events`
- `GET /api/v1/detections`
- `GET /api/v1/detections/stats`
- `WS /ws/detections`
- `POST /api/v1/detections/start`
- `POST /api/v1/detections/stop`

This is the same category of gap as `AuditReport1.md` finding 1.1 (open live video), but arguably more sensitive: the read endpoints and the websocket stream out **exactly which vehicle plate was seen at which camera at what time** - the specific surveillance output this whole platform exists to produce - to anyone, no login required. And the two POST endpoints are a genuine control-plane problem on top of the data exposure: `start_detection` kicks off real RTSP connections and inference against the live grid, `stop_detection` kills it - meaning any anonymous caller can currently turn a legitimate operator's live detection session on or off at will.

Worth noting the pattern this fits: `grid.py` and `watchlist.py` (the endpoints matching `docs/API_Contract.md`'s original, "decided" Model 2 spec) are consistently and correctly authenticated. `detections.py` and `recorded.py` (finding 1.3/1.4) - both later, more advanced additions - have none at all. That's useful for prevention, not just for this fix: whatever review step made sure `grid.py`/`watchlist.py` got auth evidently didn't get applied to the newer routers.

**What needs to change:** add `Depends(get_current_user)` to the four REST endpoints (no role restriction needed - matches the "any logged-in role can view" pattern used for camera reads); add `Depends(require_role("dept_admin", "operator"))` to `start_detection`/`stop_detection` (these are operational controls, not read access, so viewer should probably be excluded - a product call, but excluding viewer is consistent with how `watchlist.py` and `persons_watchlist.py` already treat write/control actions). For the websocket, FastAPI supports `Depends()` on websocket routes the same way as HTTP routes; the existing `get_current_user` dependency reads from the `access_token` cookie, which the browser already sends automatically on the websocket handshake for a same-origin connection - so this shouldn't need any frontend change for already-logged-in users, same reasoning as `AuditReport1.md` finding 1.1's fix.

### 1.3 The video upload endpoint accepts anonymous 2 GB uploads

`app/routers/recorded.py::upload_recorded_video` (`POST /api/v1/recorded/upload`) has no auth dependency of any kind. It does do real validation work once a file arrives - extension allowlist, a properly chunked read with a 2 GB cap enforced mid-stream (not just checked after the fact), and a sanitized, UUID-prefixed filename that can't path-traverse (`re.sub(r"[^\w\-.]", "_", filename)` strips `/` before the file ever touches disk) - so this isn't a sloppy endpoint, it's a careful one that's simply missing the one check that matters most here. An unauthenticated 2 GB upload endpoint is a straightforward disk-exhaustion vector on its own, and it's also feeding attacker-controlled bytes into native code (OpenCV's video decoder, right after) with zero gate in front of it - exactly the kind of endpoint that should require a session at minimum.

**What needs to change:** add an auth dependency (`Depends(get_current_user)` is likely sufficient given this feeds a per-user "your own footage" workflow; `require_role` if uploading should be dept_admin/operator-only, matching the other write-side endpoints in this codebase).

### 1.4 Every other endpoint in the same router is also unauthenticated

Rounding out `recorded.py`: `get_cameras_for_association`, `start_recorded_job`, `pause_recorded_job`, `resume_recorded_job`, `stop_recorded_job`, `get_recorded_job_status`, and `ws_recorded_feed` all take no auth dependency either. The job-control endpoints are the same start/stop-hijack problem as finding 1.2; `job_id` being a UUID tempers the practical risk of the status/websocket endpoints somewhat (you'd need to already know a valid one), but there's no reason for these to be open while everything else in the app requires login. Fix alongside 1.3 in the same pass, same dependency pattern.

---

## 2. High-priority findings

### 2.1 The `model2_analytics` / `model2-analytics` duplicate package situation is still live

Flagged as explicitly out of scope in `AuditReport1.md` Section 8, now formally in scope. Confirmed unchanged since the first audit: `model2_analytics/app/ingestion/*.py` (underscore) are thin shim files (24-114 lines) that manipulate `sys.path` and re-import from `model2-analytics/app/ingestion/*.py` (hyphen, the real implementation, 114-262 lines). `infra/Dockerfile` still `COPY`s both packages plus a third copy into `/app/model2_analytics_src`, and sets `PYTHONPATH` to include all three locations.

This isn't necessarily wrong (there may be a real reason both an importable-module name and a hyphenated directory name are needed - Python identifiers can't contain hyphens, so `model2-analytics` can never be `import`ed directly, which is plausibly the entire reason the shim exists), but as-is it's undocumented, and it means a change to the ingestion logic has to be made in (or at least verified against) two places, or it silently drifts. Given `catalogue.py` (262 lines) has grown substantially since the shim (24 lines) was written, they're already a long way from being trivially kept in sync by inspection.

**What needs to change:** at minimum, a comment at the top of `model2_analytics/app/ingestion/__init__.py` (there already is one, it's reasonably clear) should be mirrored by one in `model2-analytics/app/ingestion/__init__.py` pointing back the other way, so either file makes the relationship obvious. Longer-term, this is worth a real decision: either commit to the shim pattern permanently and add a test that fails if the two drift out of behavioral parity, or restructure so there's only one real package (e.g., rename the hyphenated directory and update the Dockerfile/PYTHONPATH accordingly) and drop the shim entirely.

**Resolved:** took the longer-term option rather than the "at minimum" one — the shim package is gone, `model2-analytics/` was renamed to `model2_analytics/` (a single real package now), and every reference to the old hyphenated path across the codebase (`infra/Dockerfile`, `infra/docker-compose.yml`, `infra/mediamtx.yml`, `model1-registry/app/main.py`, router/pipeline path constants, `.gitignore`, CI workflow, and docs) was updated to match. `infra/Dockerfile` now `COPY`s the package exactly once; `PYTHONPATH` still needs three entries (`/app`, `/app/model2_analytics`, `/app/model2_analytics/app`) because `pipeline.*` and `ingestion.*` are still imported as top-level names in a few places (see `model2_analytics/app/ingestion/__init__.py` and `main.py`'s own comment on this) — but they now all resolve to the same on-disk files instead of two that could disagree. Verified by booting the app with the real Docker `PYTHONPATH` value against a local build and confirming all five Model 2 routers (including `persons_watchlist.py`) still mount, and by running both test suites together from the repo root (previously the scenario that broke — see `model2_analytics/tests/conftest.py`'s docstring).

### 2.2 Zero test coverage for the entire component

No `tests/` directory exists anywhere under `model2-analytics/`, and no test file anywhere in the repo references anything from it. This is a sharp contrast with `model1-registry/tests/`, which is thorough and (per `AuditReport1.md`'s verification above) has grown further since the first audit. Given this component includes the highest-stakes new code in the whole repo (the face-recognition pipeline, the watchlist correlation logic that Project_Context.md Section 4 identifies as what Step 4's scored evaluation actually tests), having no automated check that any of it still works after a change is a real gap, independent of the specific auth findings above.

Not asking for a full test plan here per your original instructions - but a few non-obvious starting points worth naming explicitly rather than leaving implicit: the auth gaps in Section 1 need the same anonymous-401 pattern used throughout `model1-registry/tests/` (that suite is a good template to copy the shape from); `is_safe_url()` in `grid.py` is exactly the kind of security-relevant helper function that's cheap to unit-test in isolation (feed it a `169.254.169.254` URL, a `file://` scheme, etc.) without needing the full app/DB fixture machinery; and the plate-format validation mentioned in `model2-analytics/README.md` ("Strict Indian license plate normalization & regex") is a pure-function candidate for the same reason.

### 2.3 Dependencies are fully unpinned, in a much heavier tree than model1's

```
# model2-analytics/requirements.txt
opencv-python-headless==4.9.0.80
...
facenet-pytorch>=2.6.0

# model2-analytics/pipeline/requirements.txt
ultralytics>=8.3.0
torch>=2.2.0
torchvision>=0.17.0
...
```

Mixed, and mostly unpinned - `model2-analytics/requirements.txt` pins everything except `facenet-pytorch`, while `pipeline/requirements.txt` pins nothing at all (every line is a `>=` floor). `AuditReport1.md` finding 14 fixed exactly this pattern for `model1-registry/requirements.txt`; the same reasoning applies here, more urgently, since `torch`/`torchvision`/`ultralytics` are large, frequently-updated packages where an unpinned install on a different day can genuinely change model behavior or break compatibility, not just introduce a security drift.

**Addendum, found while getting the test environment running**: pinning each file in isolation isn't enough on its own -- `infra/Dockerfile` installs both `model1-registry/requirements.txt` and `model2-analytics/requirements.txt` into the *same* environment (`pip install -r requirements.txt -r requirements-model2.txt`, since model2's routers run inside model1's process per finding 5's dynamic loader). The two files had landed on different exact pins for two packages they both depend on (`sqlalchemy`: 2.0.52 vs 2.0.30; `opencv-python-headless`: 4.13.0.92 vs 4.9.0.80) -- harmless as long as nobody ever installed them together, but a hard `ResolutionImpossible` the moment they are, which is exactly what the real Docker build does. Confirmed with `pip install --dry-run` against both files together before and after. Now aligned to identical pins in both files.

---

## 3. Medium-priority findings

### 3.1 Raw exception text echoed back to API callers

`app/routers/detections.py::detection_history` and a couple of neighboring handlers return `{"status": "error", "message": str(e), ...}` directly from a caught exception. Low severity on its own, but it's an easy habit to fix while other changes are being made to this same file: log the real exception server-side, return a generic message to the caller.

### 3.2 Fragile (currently safe) SQL pattern in `detection_history`

```python
where = "WHERE c.source_grid_id = :g" if grid_id else ""
...
rows = db.execute(text(f"""... {where} ..."""), params).fetchall()
```

To be precise about the actual risk here: this is **not currently a SQL injection vulnerability** - `where` can only ever be one of two fixed literal strings, and the real dynamic value (`grid_id`) is properly bound via the `:g` parameter, not interpolated into the query text. It's flagged here purely as a fragile pattern: building a WHERE clause via an f-string, even one that's currently safe, is one bad refactor away from not being safe (e.g., if someone later changes `where` to be built from a field name that *is* attacker-influenced). Worth tidying to a clearly-parameterized form the next time this function is touched, more for the safety margin than because of a live bug.

### 3.3 Face-photo upload has no size limit

`app/routers/persons_watchlist.py::create_watchlist_person` does `photo_bytes = await photo.read()` with no size cap, in contrast to `recorded.py`'s video upload in the very same audit round, which carefully chunks the read and enforces a 2 GB limit mid-stream. Lower severity than 1.3 since this endpoint already requires `dept_admin`/`operator` auth, but worth bringing in line with the same pattern for consistency and to prevent a memory-exhaustion issue from an oversized "photo."

### 3.4 `model2-analytics/README.md` is stale and contains an unedited personal note

Two separate issues in the same file: first, the directory structure and feature list don't mention `persons_watchlist.py` or `pipeline/faceembedding/` at all, even though both now exist and are a meaningful part of what Model 2 does - the same kind of doc-drift `AuditReport1.md` finding 15 fixed in `docs/DATASET.md`. Second, this line is present verbatim:

> Note: live.corp8.cloud is the hackathon evaluation gateway. One problem that it is not accessible right now using the home wifi network maybe it can be accessible by the jury network at the event jus a guess please check this first

This reads like an unedited note-to-self rather than documentation - genuinely useful operational information (connectivity may be network-dependent, worth checking before the live evaluation), but worth cleaning up into a proper sentence, both for polish and because `HackathonPortal.md` Step 7's evaluation criteria explicitly scores "Clarity and completeness" of submitted materials.

### 3.5 Two different grid domains, no reconciliation

`model2-analytics/README.md`'s architecture diagram and endpoint table consistently use `live.corp8.cloud` (e.g. `http://live.corp8.cloud:8889/stream/<id>/whep`). Everywhere else in the codebase - `model1-registry/app/config.py`'s `GRID_HOST`/`GRID_CDN_HOST` defaults, and `model2-analytics/app/ingestion/catalogue.py`'s actual polled URL (`https://cctv.corp8.cloud/cameras.json`) - uses `cctv.corp8.cloud`. These might be the same service under two names used inconsistently in docs, or they might be genuinely different endpoints (e.g., one for the browser-facing CDN and one for the polling API) that happen to look like a typo from the outside. Either way, a reader can't currently tell which, and whoever wrote the code (which actually runs and is what the app depends on) is the more likely source of truth - flagging for someone with direct knowledge to confirm and then fix whichever side is wrong.

---

## 4. Low-priority / informational

### 4.1 Face-detection model downloaded at runtime with no integrity check

`pipeline/faceembedding/quality_checker.py` pulls `face_detection_yunet_2023mar.onnx` from a GitHub raw URL via `urllib.request.urlretrieve` with no hash verification of what comes back. Same category as unpinned dependencies (3.3 above, and `AuditReport1.md` 14): low urgency, cheap to add a checksum check against the known-good file while this code is next touched.

### 4.2 Minor REST inconsistency

`persons_watchlist.py::update_watchlist_person` takes its fields as query parameters on a `PATCH`, while the otherwise-parallel `watchlist.py::update_watchlist_vehicle` takes a proper JSON body via a Pydantic model (`VehicleWatchlistUpdate`). Not a bug, just worth matching for consistency next time this file is touched - `shared/schemas/persons_watchlist.py` already defines `PersonWatchlistUpdate` for exactly this purpose but the router doesn't currently use it.

### 4.3 Confirm intended behavior on `GET /api/ingest`

This one cuts the opposite direction from everything else in this report, which is why it's flagged separately rather than filed as a straightforward gap: `docs/API_Contract.md` Section 0 describes `/api/ingest` as belonging to the *government grid itself* - a read-only catalogue the hackathon exposes to every participating team, unauthenticated, as a resource. `grid.py::get_ingest_catalogue` is this app's own endpoint mirroring that same contract shape (per `model2-analytics/README.md`, listed as "Hackathon ingestion contract"), and it currently requires `Depends(get_current_user)`.

That's a defensible default and not flagged as wrong - but if the hackathon's evaluation process (or any other tooling) expects to query this app's `/api/ingest` directly the same way it queries the real grid's version (no login step), the current requirement would be a functional surprise rather than a security improvement. Worth a deliberate decision either way rather than leaving it as an incidental side effect of applying auth broadly.

---

## 5. What's already solid

- `grid.py` and `watchlist.py`: every endpoint in both files is correctly authenticated, with sensible role scoping (viewer excluded from watchlist entirely, matching the sensitivity of "stolen/wanted/blacklisted" data).
- `persons_watchlist.py` overall is some of the most careful code in the repo: auth on every endpoint including the photo-serving route, the same path-traversal defense pattern used in `AuditReport1.md`'s own detection-image fix (`requested.relative_to(FACES_DIR.resolve())`), and correct cleanup of the photo file on disk when a record is deleted rather than leaving orphaned face images behind. Whoever built this plausibly already had `AuditReport1.md` in front of them (the merge landed after several of its fix commits) and it shows.
- `is_safe_url()` in `grid.py` correctly blocks the cloud-metadata IP (`169.254.169.254`), loopback addresses, and `.internal` hosts on the catalogue-sync endpoint - real SSRF protection, already fixed by a collaborator's PR and confirmed present.
- `recorded.py`'s upload filename handling (`re.sub(r"[^\w\-.]", "_", filename)` plus a random UUID prefix) correctly prevents path traversal even though the endpoint itself needs the auth fix in 1.3.
- `catalogue.py`: fully parameterized SQL throughout, a sensible exponential-backoff-with-fallback design, and it's already using the single-source-of-truth `GRID_RTSP_HOST` environment variable fix from `AuditReport1.md` finding 10 rather than its own hardcoded copy.
- The `.gitkeep`/large-binary hygiene from the first audit round is holding steady - no new stray `.gitkeep` files or accidentally-committed model weights turned up anywhere in this pass.

---

## 6. Suggested execution order

1. **Finding 1.1** (the hardcoded credential) first and alone - it's a one-file, low-risk removal, and every day it stays in the repo is a day it's exposed regardless of what else gets fixed.
2. **Findings 1.2-1.4** (the two routers' auth) together, same reasoning and same low-risk-to-fix profile as `AuditReport1.md`'s own findings 1.1-1.3 (no existing consumer of these endpoints is doing so anonymously on purpose, as far as this audit can tell).
3. **Finding 4.3** needs an actual decision from whoever owns the hackathon-submission logistics before anyone touches `get_ingest_catalogue` further either way.
4. **Section 2** (duplicate package, tests, dependency pinning) - bigger, less urgent, good candidates for a session with more room to do them properly rather than squeezing them in.
5. **Section 3 and 4** - documentation and small consistency fixes, no particular ordering constraint, safe to interleave with anything else.
