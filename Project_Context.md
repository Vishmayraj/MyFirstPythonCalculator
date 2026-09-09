# Sentinels, Gujarat CCTV Integration Platform, Project Context

> **Read this before doing anything else.** This is the internal working
> context for our team's build, not the official hackathon brief (that's
> `HackathonPortal.md`, which this document summarizes and then extends
> with *our* decisions). Any AI assistant or teammate picking this project
> up should read this file first. Keep it updated as decisions change,
> stale context here is worse than no context.

---

## 0. The one rule that overrides everything else

**Do not optimize for "technically complete." Optimize for "the jury looks
at this for 30 seconds and knows it's different."**

The evaluation criteria (Step 7 of the brief) score architecture soundness,
interoperability, analytics quality, and scalability readiness, not raw
feature count. Dozens of teams will submit a map with pins on it and call
it Model 1. A working demo that checks every box but looks and feels like
every other submission will not stand out, and standing out is the actual
goal here, not just passing the checklist.

Every design decision below should be checked against this question:
**"Does this make a jury member say 'oh, that's a genuinely better way
to do this,' or does it just make the checklist green?"** If a feature
only does the latter, it's not disqualified, it's just not where our
effort goes first.

**Correction to this rule, added after review:** this applies to UI and
UX decisions. It does not apply to backend framework or language choice.
The jury does not inspect `package.json`. Not using Django or React is a
defensible engineering call for our own risk reduction (see §2), it is
not itself a differentiator and should not be pitched as one. The actual
"jury notices in 2 seconds" material is custom map icons, honest framing
of what each model does and doesn't do, and district-level accuracy that
shows we read the brief closely, not our web framework.

---

## 1. What the hackathon actually wants (condensed)

- 26 Gujarat govt departments run independent, heterogeneous CCTV systems
  (different vendors, VMS, storage, retention periods) across geographically
  scattered sites (up to ~1000km apart).
- Government wants these unified into one ecosystem, eventually integrated
  with law-enforcement databases (VAHAN, SARTHI, eGujCop, AFIS, NAFIS) for
  automated real-time alerts.
- Five reference architectures are offered (Model 1-4, or Hybrid/Custom).
  **Model 1 is explicitly the shared foundation** other models build on.
- **The actual scored test (Step 4):** onboard ~50 real, heterogeneous
  government-provided cameras, trace one designated vehicle by plate
  number across the network with timestamped movement history, and
  demonstrate live cross-referencing against a watchlist DB with
  real-time alert generation. This is model-agnostic, whatever
  architecture we pick has to survive this test.
- Long-term ambition stated in the brief: ~80,000 cameras statewide.
  We don't need to build that, we need to *credibly document* how our
  architecture gets there without a redesign. This is a graded written
  deliverable (Step 6), not optional color commentary. See §7.

Full official spec lives in `HackathonPortal.md`, this doc doesn't
replace it, it's our working interpretation plus our decisions on top of it.

---

## 2. Our chosen approach

**Sequencing:** Model 1 (Registry & GIS Foundation) first, standalone and
solid, then Model 2 (Unified Viewing & Analytics) on top of it, since
Model 2 is where ANPR and watchlist correlation live, and that's what
Step 4 actually scores. Model 3/4/Hybrid framing is a later decision once
1+2 are real and working, we will not stretch thin chasing every model.

**Tech stack, deliberately not the "suggested" one:**

| Layer | Choice | Why |
|---|---|---|
| Backend | **FastAPI** (not Node.js/Django) | Async-native, this system is I/O-bound (many camera connections, WebSocket pushes, concurrent DB writes). Same language as our AI/analytics layer (OpenCV, ANPR models), so Model 2 doesn't need a separate service just to talk to Model 1. |
| Frontend | **Leaflet + Jinja2 templates + HTMX/Alpine.js** (no React, no Node, no build step) | Model 1 is map- and interaction-heavy, not component-state-heavy. No npm/node_modules to manage, no build pipeline, served straight from FastAPI. |
| Database | **PostgreSQL + PostGIS** | Required, not optional, Model 1 is a *GIS* foundation, and gap-analysis (coverage holes) needs real spatial operations (buffers, polygon containment), not just lat/lng floats. PostGIS is a lightweight extension, not a heavy separate service, negligible overhead at our scale. |
| Time-series (deferred) | **TimescaleDB, not yet** | Solves a different problem (high-volume timestamped data: health-check logs, ANPR events, vehicle-position history). Not needed while Model 1 only tracks *current* status. Introduce it when Model 2's event/detection logging reaches real volume, call this out explicitly in the HLD's scalability section as the intended future addition. |
| Streaming (Model 2+) | **MediaMTX (Go, open source), prebuilt binary only** for RTSP→WebRTC/HLS bridging | Decided against custom Pion glue for this build: 1-2 week timeline doesn't leave room for us to write and debug our own Go media code, and MediaMTX's stock RTSP/RTMP/ONVIF ingestion plus WebRTC/HLS re-serving already covers what Step 4 needs. We run it as an external service, we don't write Go. Revisit custom glue only if MediaMTX genuinely can't do something we need, not preemptively. |
| Deployment | Docker Compose locally and on a VPS for consistency; Nginx/Caddy reverse proxy + TLS | Keep local/VPS parity so "works on my machine" isn't a demo-day risk. |

**Hard constraint:** everything in the stack must be fully open source, no
proprietary VMS SDKs as a dependency for the core platform. Camera
hyperlinks to native VMS software (see §3) are an exception since they
link *out* to existing departmental infra, not something we depend on.

**Analog cameras, decided:** out of scope for our demo. The brief
mentions analog infrastructure alongside IP, but digitizing analog feeds
needs hardware (encoders) we don't have and can't test against in 1-2
weeks. Our demo and the government-feed evaluation will be IP/ONVIF only.
This is documented as a Future Roadmap item in the HLD (see §7), not
silently dropped, an encoder/gateway layer ahead of our ingestion adapters
is a bounded, well-understood addition and we say so explicitly rather
than pretend the platform already handles it.

---

## 3. Model 1: detailed spec

**Map:**
- Leaflet + OpenStreetMap base layer.
- Camera markers, clustered (Leaflet.markercluster) for the "flock" effect
  at low zoom, clusters resolve into individual cameras as you zoom into
  a district/city.
- **Custom-designed marker icons, not default Leaflet pins.** Each status
  (online / offline / maintenance) and each department gets its own
  deliberately designed icon, not a color-tinted stock pin. This is one of
  the actual "make it look different" investments, see the correction in §0.
- Layer toggle control: filter by department (Home/Police, Food & Civil
  Supplies, RTO, Municipal Corporations, etc.).
- **Filter by district** (not just "city"), Gujarat's actual administrative
  unit is the district (33 total), and the brief names specific districts
  (Valsad, Dahod, Somnath, Jamnagar, Dwarka) as deployment points. This
  reads as us understanding the real geography, not a generic template.
- Click a marker → side panel/popup with metadata: department, camera
  type, ownership, connectivity status, storage/retention details.
  **Optional hyperlink out to the camera's native VMS viewer**, honest
  about what Model 1 is (registry only, no centralized video) while
  visibly gesturing at how it plugs into Model 2+.

**Required but easy to underweight (don't skip these for map polish):**
- Bulk import + manual entry + API-based onboarding.
- Camera health/maintenance-status tracking.
- Gap-analysis report generation (uncovered zones, ageing infrastructure),
  this is where PostGIS spatial queries actually get used, not just for
  display.
- Role-based search, filter, export, and metadata **audit trail**, see §6
  for the concrete RBAC and audit design.

**Data model sketch (to refine as we build):**
- `cameras`: id, name, department_id, location (PostGIS geography point),
  district, camera_type, ownership, connectivity_status, storage_type,
  retention_days, vms_url (nullable), created_at, updated_at.
- `departments`: id, name.
- `status_history` / audit log: camera_id, changed_field, old_value,
  new_value, changed_by, changed_at, plain Postgres table for now, not
  Timescale (see §2 rationale).
- `districts`: id, name, boundary (PostGIS polygon), needed for
  gap-analysis and the district filter.

---

## 4. Model 2: analytics pipeline & watchlist spec

Not a single ANPR model, a small pipeline:

1. **Vehicle detection**, localize vehicles in-frame, feeds both plate
   detection and cross-camera tracking.
2. **Plate detection**, localize plate region within the vehicle crop
   (YOLO-family, fine-tuned on Indian plate proportions/fonts, generic
   pretrained detectors underperform here, usually trained on US/EU plates).
3. **OCR / character recognition**, PaddleOCR / EasyOCR / small CRNN.
   Separate failure mode from detection: finding the plate is not the
   same as reading it right.
4. **Cross-camera vehicle tracking / route reconstruction**, this is what
   Step 4's scored test actually is ("trace the vehicle across the
   network"). Primary: join detections into a track by plate string and
   timestamp. Fallback when OCR fails at one camera: appearance-based
   re-id (color, vehicle type, rough embedding) so one bad read doesn't
   break the route.
5. **Bonus/stretch, not core path**: face recognition vs. person-watchlist,
   generic object/anomaly detection (intrusion, crowding). These appear in
   the HLD's analytics ask and Model 4's feature list but are not what
   Step 4 scores, don't let them dilute the vehicle pipeline. Given the
   1-2 week window (see §8), these are explicitly cut unless everything
   else is done early.

"Event tagging/indexing" in the brief is a data-pipeline concern, not a
model: every detection is tagged with `camera_id`, `timestamp`,
`department`, and a watchlist-match id if any, and indexed so "searchable
vehicle-movement records" is a query, not a separate feature.

**Watchlist DB schema:**

- `vehicles_watchlist`: id, plate_number (normalized), category
  (stolen/wanted/blacklisted), reported_date, department, description, status
- `persons_watchlist`: id, name, category (wanted/missing/suspect), face_embedding (VECTOR(512)), photo_path, status, created_at (with 5-gate AI quality pipeline and HNSW cosine index)
- `detections`: id, camera_id, timestamp, detected_plate, confidence,
  cropped_image_path, vehicle_track_id (nullable, groups detections of
  the same physical vehicle across cameras)
- `alerts`: id, detection_id, watchlist_id, alert_type, severity,
  created_at, acknowledged_by, acknowledged_at
- `vehicle_tracks`: id, plate_number, first_seen, last_seen, the route
  itself is a query joining `detections` on `vehicle_track_id`, ordered
  by timestamp

Every incoming detection is matched against `vehicles_watchlist` near
real-time, a match writes an `alerts` row and pushes to the dashboard
over WebSocket.

**Interoperability inside Model 2 (not Model 3):** Model 2 explicitly
connects to each VMS *directly*, with no intermediate middleware or
federation layer, that's what distinguishes it from Model 3. We are not
building Model 3. But the brief's architecture mandate ("documented
standard APIs, open protocols, SDKs, modular adapter-based frameworks,
avoid vendor lock-in") applies regardless of which model number we picked.
So: our VMS-connection code still uses a clean **adapter pattern**, one
adapter class per vendor/VMS, all implementing the same interface
(`connect()`, `get_stream()`, `get_metadata()`), and leans on **ONVIF**
wherever a camera supports it, since that's the actual industry-standard
protocol for this exact cross-vendor problem. This isn't "building Model
3," it's engineering that happens to satisfy the same mandate, and it
means extending toward Model 3 later, if we ever want to, doesn't require
ripping anything out.

---

## 5. Evaluation discipline: precision / recall / F1, never bare "accuracy"

Mandate: every analytics stage reports precision, recall, and F1, never a
single accuracy number. This is a **rule-0 differentiator**, not just
correctness: watchlist hits are rare events (a small % of traffic), so a
model that says "no match" every time scores misleadingly high on
accuracy alone while being useless. Most competing teams will report one
shiny accuracy % because it sounds good in a slide, a real confusion
matrix signals we understand what we built.

Apply separately at each stage, they measure different failures:

- **Plate detection**: precision/recall/F1 on IoU-thresholded boxes vs.
  ground truth (did we find the plate at all).
- **OCR read**: character-level accuracy AND full-plate exact-match rate,
  reported separately, a plate read "90% correct" character-wise is a 0%
  useful match.
- **Watchlist alerting** (the headline number, this is what Step 4 really
  tests): precision/recall/F1 on generated alerts. False positives flood
  the operator and get ignored, false negatives miss the wanted vehicle.
- **Cross-camera tracking** (if built): track-continuity precision/recall,
  correctly stitched the same vehicle's sightings without merging two
  different vehicles into one route.

Build a small hand-labeled test set from our own recorded feed (needed
anyway for the "Own-Feed Demonstration" deliverable) and run a real eval
harness against it for the output report, an actual measured report, not
a claimed number.

---

## 6. Security & auditability architecture

This was previously undocumented despite being an explicit HLD requirement
and a named bonus-scoring category ("enhanced cybersecurity, privacy
protection, auditability, role-based access controls"). Given we're
touching representative eGujCop/watchlist-style data categories, this
needs a real answer even at hackathon scope, not a TLS footnote.

**What we actually build for the demo:**
- **RBAC**: JWT-based session auth in FastAPI. Three roles to start,
  department admin (manages that department's cameras), operator (views
  feeds, acknowledges alerts), viewer (read-only). Enforced at the API
  layer, not just hidden in the UI.
- **Audit trail**: the `status_history` table from §3 already covers
  camera metadata changes. Extend the same pattern to alert handling,
  `alerts.acknowledged_by` and `acknowledged_at` already exist in the
  schema (§4), so alert auditability is close to free, just needs the
  write path wired up. This is a concrete example of the bonus
  "auditability" criterion being satisfied by something we already
  designed, worth calling out explicitly in the PPT.
- **Transport encryption**: TLS everywhere via Caddy/Nginx, already planned.

**What we document in the HLD but don't build (be explicit about this
distinction in the submission, don't imply it's implemented):**
- At-rest encryption for the watchlist/detections tables specifically
  (pgcrypto or disk-level), flagged as a pre-production requirement.
- Network segmentation: VMS-ingestion network conceptually separate from
  the public-facing dashboard network, described in the architecture
  diagram even though our demo runs on one VPS.
- API gateway / rate limiting (Kong or Nginx-level), mentioned as the
  statewide-scale answer, not needed at 50-camera demo scale.

---

## 7. Scale plan, cost-benefit, infra sizing (Step 6 deliverable)

This section exists because Step 6 ("Plan for Scale") is a graded
requirement with its own named subsections, and until this edit nothing
in this doc addressed it. Given 1-2 weeks, this is a **written plan for
the PPT/HLD**, not something we build or load-test. Say so plainly in the
submission rather than implying a load test happened.

- **Central/regional/edge compute**: central FastAPI + Postgres/PostGIS
  cluster, regional edge nodes running MediaMTX ingestion close to camera
  clusters to cut backhaul bandwidth, since sites span up to ~1000km apart.
- **AI processing capacity**: don't invent a specific GPU-count number we
  can't back up. Once we've measured our own pipeline's frames/sec on one
  GPU during the build, extrapolate from that real number for the 80k
  estimate instead of guessing cold, this is more credible than a made-up
  figure and takes five minutes once the pipeline runs.
- **Storage tiers**: hot (recent ~7 days, SSD/NVMe), warm (up to
  departmental retention, HDD or object storage), cold (beyond retention,
  self-hosted S3-compatible via MinIO, keeps us open-source). Explicitly
  respect each department's stated retention period rather than forcing
  one policy statewide, the brief calls out that retention already varies
  by department (7 vs 15+ days).
- **Low-bandwidth strategy**: edge-side ANPR, only detection metadata and
  a cropped snapshot travel upstream over constrained links, not full
  video. This doubles as a named Bonus criterion ("edge-processing,
  bandwidth-optimisation, low-connectivity operation").
- **Disaster recovery**: documented approach only, regional replicas,
  periodic backups, target RTO/RPO stated in the HLD. Real DR
  infrastructure is out of realistic reach in this timeframe, we say what
  we'd do, we don't demo it.
- **Cost-benefit**: fully open-source stack means zero licensing cost,
  the concrete argument for the PPT is licensing cost vs. Model 4's
  typical commercial-VMS cost profile. Main real cost driver at scale is
  GPU inference and cold storage volume, both quantifiable once we have
  our own pipeline's throughput number.

---

## 8. Timeline and scope for the 1-2 weeks we have

Be honest about what's real vs. stubbed rather than letting scope creep
silently eat the buffer before the live government-feed evaluation.

**Week 1:** Model 1 core (camera CRUD, map with custom icons, PostGIS gap
analysis, department/district filters) and Model 2 pipeline skeleton
(vehicle detection, plate detection, OCR) running against our own
recorded feed.

**Week 2:** watchlist matching, alert generation and WebSocket push,
cross-camera tracking with appearance-based fallback, RBAC and audit
write path (§6), HLD/PPT writing (architecture diagrams, §6 and §7
content), own-feed demo recording.

**Explicitly stubbed, not built, don't let these expand:**
- Bulk import: CSV upload is enough, no wizard/UX polish.
- Audit trail: DB writes only, a plain table view is enough, no dedicated
  audit UI.
- Face recognition, generic anomaly detection: cut unless everything
  above is done with days to spare.
- Disaster recovery, network segmentation: documented in HLD only (§6, §7).

**Keep 1-2 days at the end as an integration buffer**, not filled with new
features. The government-provided feed at evaluation time is an unknown
quantity (format, ONVIF compliance, latency), and that's exactly the kind
of surprise that eats a demo if there's no slack left to debug it.

---

## 9. Open questions / not yet decided

- Exact icon/visual design direction for markers (in progress,
  deliberately custom, not finalized).
- Watchlist DB matching logic details, plate normalization rules, fuzzy
  matching tolerance for OCR near-misses (Model 2 concern, revisit once
  the OCR stage has real error-rate data from our test set).
- Whether we pursue Hybrid framing once Model 1 + 2 are solid, or stop
  there, decide after Week 1, don't decide now.

---

## 10. Notes for whoever (human or AI) picks this up next

- Don't re-litigate the FastAPI/no-Node/no-React decision, it's made, and
  it's a defensible architecture call for our own risk reduction, not a
  shortcut and not itself the differentiator, see the correction in §0.
- Don't add TimescaleDB "just in case", see §2, it's a deliberate
  deferral, not an oversight.
- Don't add custom Pion/Go glue "just in case" either, MediaMTX's
  prebuilt binary is the decision for this build (§2), given the 1-2 week
  window. Revisit only if MediaMTX genuinely can't do something needed.
- Every UI decision gets checked against §0 before it gets checked against
  the feature checklist.
- §6 and §7 exist because they were missing graded deliverables, not
  optional reading, both need real content before submission even if most
  of it is documentation rather than code.
- Update this file when a decision changes, don't let it go stale while
  the actual build diverges from what's written here.