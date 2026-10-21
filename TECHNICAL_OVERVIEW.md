# APD Vision — Technical Overview

A single reference covering APD Vision's product, architecture, computer vision pipeline, data model, security, and coding conventions.

---

## 1. Product overview

| | |
|---|---|
| Name | APD Vision |
| In one sentence | A CCTV-based system that detects compliance with personal protective equipment (PPE)—helmets, vests, gloves, and shoes—records violations with photographic evidence and timestamps, and answers questions about the data in Indonesian. |
| Vision | K3 (occupational safety and health) supervisors should not need to watch CCTV all day. The system identifies who violated a rule, when and where it happened, and provides supporting evidence. |

### Problems addressed

- Manual PPE monitoring cannot run around the clock and depends on an individual's attention.
- CCTV recordings accumulate without being searchable. Answering a question such as “How many people were not wearing gloves yesterday afternoon?” can take hours of video review.
- Common PPE detection systems check only helmets and vests, apply the same rule to every area, and can be wrong because of a single missed frame.

### Users

| User | Primary need |
|---|---|
| K3 supervisor / safety officer | See current violations, receive alerts, and review evidence. |
| K3 manager | Track compliance trends and high-risk hours, ask the Agent questions, and produce reports. |

### Design principles

- **Represent uncertainty honestly.** If a body part is not visible, its status is *uncertain*, not a violation.
- **Ground figures in data.** Agent answers use database queries rather than numbers invented by a language model.
- **Accessibility.** Status uses color **and** text or symbols, never color alone. The application interface is in Indonesian, and displayed times use WIB (Western Indonesian Time).

---

## 2. Features

### Camera input

- Video files for demos, with optional looping (`loop`).
- Offline video processing without a server (`scripts.process_video`; `--start` sets the recording's start time).
- RTSP / phone streams with automatic reconnection; sources can be changed from the dashboard, subject to anti-SSRF validation.
- Flexible video sources: resumable uploads in 10 MB chunks, server-side files, and **simulated live** or **recorded analysis** modes with a recording start time.
- Multiple cameras share one model and are processed together in batches; each camera has separate tracking, zones, required PPE, and status.

### Detection and analysis

- Person detection with 17 keypoints (YOLO11n-pose) and detection of four PPE items (fine-tuned model).
- PPE-to-person association by body region (head, torso, wrists, and ankles; IoA ≥ 0.40).
- Worker tracking IDs through ByteTrack.
- Three states with hysteresis: `compliant` / `violation` / `uncertain`.
- Zone-specific PPE rules (polygons stored in the database and initially seeded from configuration).
- Face blurring in evidence and the live view using head keypoints.

### Evidence and data

- Violation and uncertain events with time, duration, camera, zone, and missing PPE items.
- Annotated full-frame photographs and worker crops, each with a SHA-256 hash.
- Five-second video clips (two seconds before and three seconds after an event).
- Model version recorded per event, enabling field precision comparisons by version.
- Supervisor reviews (confirmed / false positive) with an audit log; reviews affect the compliance rate.
- Review exports for retraining.
- Automatic evidence retention and database/evidence backups.

### Alerts

- Telegram messages with a photograph, camera, zone, time, and missing PPE items, subject to a per-worker cooldown.
- Review buttons in Telegram messages (long polling, no webhook).
- Alerts when camera image quality deteriorates (drift).

### Dashboard (React)

The page names below are the Indonesian labels used by the application.

| Page | Contents |
|---|---|
| Live | MJPEG stream, KPIs, per-worker checklists, today's violations, and active PPE selection by zone |
| Analitik (Analytics) | Compliance rate and PPE compliance by hour (including high-risk hours), day, camera, and zone |
| Evidence | Gallery with period/type/PPE/review filters, a detail modal, and review actions |
| Agent | Indonesian-language chat with a strip of evidence photographs |
| Intervensi (Interventions) | Record interventions and compare before-and-after outcomes with a statistical test |
| Sistem (System) | Camera health (drift) and model precision by version |
| Bisnis (Business) | Cost/ROI calculator with adjustable assumptions |
| Login | Single form; failed-login messages do not reveal usernames |
| Pengguna (Users, admin) | Create accounts, change roles, deactivate users, reset passwords, and link Telegram IDs |
| Pengaturan (Settings, admin) | Zone polygon editor, required PPE by zone, sensitivity, shifts, Telegram, retention, camera management, and uploads |
| Audit log | Actions by actor, filterable by period, actor, and action |

### K3 Agent

- Indonesian-language questions and answers through tool calling: four **read-only** tools (`count_violations`, `get_evidence`, `compliance_summary`, `intervention_effect`).
- Relative periods (today, yesterday, this week, and the last 7 or 30 days).
- Answers containing figures, evidence photographs, and timestamps.
- Fully local operation: Ollama by default, with an offline router fallback; a cloud API is optional.
- Retrieval of K3 regulations (Permenaker No. 8/2010, UU No. 1/1970, PP No. 50/2012) using cosine similarity and the `search_regulation` tool.
- Printable K3 reports (HTML, printable to PDF from the browser).
- Scope restrictions: reject coding requests, unrelated topics, prompt injection, and attempts to bypass safeguards; record rejections.

### Accounts and roles

- Individual logins, sessions with logout, and server-enforced `viewer` / `supervisor` / `admin` roles.
- Layered failed-login throttling (IP+username / IP / username) stored in the database.
- Rejection of common passwords, passwords containing the username, and sequential strings.
- An audit log that records who performed each action.

### Intentionally out of scope

Face recognition or worker identity, cross-camera re-identification, fall detection, hazardous-zone intrusion, heavy-equipment detection, multi-tenancy and SSO, a mobile app, and distinguishing certified safety footwear from ordinary shoes.

---

## 3. System architecture

```text
Video / RTSP ─▶ worker: InferenceScheduler ─▶ CameraSession ─▶ DbSink ─▶ SQLite + evidence/
                       │                                           └─▶ Telegram
                       └─▶ JPEG + status JSON ─▶ shared /tmp/ppe_live volume ─┐
                                                                              ▼
Browser ─▶ Caddy HTTPS ─▶ app: FastAPI ─▶ RemoteLiveState + queries ─▶ API, MJPEG, SPA
                                      └─▶ agent ─▶ Ollama/offline ─▶ read-only tools
```

With **production Compose**, the API and background work run in separate processes/containers. The `worker` service handles the camera pipeline, recorded-analysis jobs, Telegram polling, backups, and retention. The `app` service is configured with `RUN_PIPELINE=0` and `ANALYSIS_JOB_RUNNER=0`; it reads live frames and status from the shared `/tmp/ppe_live` volume and events from the shared database. Analytics, Evidence, and Agent pages remain functional if the pipeline stops.

In **native development**, `RUN_PIPELINE=1` and/or `ANALYSIS_JOB_RUNNER=1` can run the pipeline and jobs within the API process, without starting a production worker.

### Technology stack

| Layer | Technology |
|---|---|
| Person detection, keypoints, and tracking | Ultralytics YOLO11n-pose, ByteTrack |
| PPE detection | Fine-tuned YOLO (`weights/best.pt`, selected through `models.ppe`) |
| Image processing | OpenCV (headless), NumPy |
| Backend | FastAPI, Uvicorn |
| ORM / database | SQLModel over SQLite |
| Agent | Anthropic SDK / HTTP Ollama / keyword router (tool calling, no unrestricted SQL) |
| Alerts | Telegram Bot API via httpx (daemon thread) |
| Frontend | React 18, Vite 5, strict TypeScript, Recharts; hash-routed SPA |
| Training | Ultralytics on Google Colab |
| Packaging | Multi-stage Docker build, Docker Compose (CPU) |

### Repository layout

```text
docs/                     local product, architecture, design, API, database, and security notes
backend/
  config.yaml             cameras, models, class_map, association, hysteresis, initial zones, evidence
  pipeline/               computer vision logic (independent of the database)
  app/                    FastAPI, queries, auth, security, agent, guard, sink, etc.
  scripts/                seed_demo, process_video, export_reviews, users, retention, backup, worker
  tests/                  pytest
  weights/                best.pt + yolo11n-pose.pt (not committed)
  data/                   ppe.db + evidence/ + videos/ (not committed; created as needed)
deploy/                   Caddyfile + docker-compose.prod.yml (app + worker + Caddy)
frontend/
  src/api.ts              fetch functions and response types
  src/features/           feature-based pages (live, evidence, analytics, agent, admin, etc.)
  src/ui/ + src/components/  shared presentational components
training/                 Colab notebooks + dataset/evaluation scripts
```

---

## 4. Computer vision pipeline

The pipeline **does not know about the database**. It only calls `EventSink` methods (`on_open`, `on_close`, `on_track_lost`, and optionally `on_clip` / `on_quality`); the implementation is in `app/sink.py`. Dynamic zone rules are supplied by a `zone_provider` callable from `app/main.py`, so `pipeline/` never imports `app/` and can be tested with a fake detector.

| Module | Responsibility |
|---|---|
| `types.py` | Data types (`Person`, `PPEDet`, `Evidence`), geometry, COCO keypoint indices |
| `detector.py` | Wraps both YOLO models; the only place that imports `ultralytics` |
| `association.py` | Derives body regions from keypoints and associates PPE with each region |
| `state.py` | Per-item hysteresis, stable per-person state, event opening/closing |
| `zones.py` | Point-in-polygon checks using normalized foot positions |
| `evidence.py` | Head blurring, cropping, annotation, JPG storage, SHA-256 hashing |
| `draw.py` | State colors, boxes and checklists, zone lines, blurring |
| `clips.py` | JPEG buffer and WebM (VP8) clip writer on a separate thread |
| `quality.py` | Brightness, sharpness, confidence → per-minute summary |
| `runner.py` | Video loop integrating the modules, calling `EventSink`, updating `LiveState` |

### State and event lifecycle

```text
Item score: decay of 0.90 per frame, +confidence when worn, -1.0 when visibly missing, capped at [-3, 3]
  score ≥ +1.0                         → present
  score ≤ -1.5 and evaluations ≥ 5    → missing
  no evidence for ≥ 45 frames          → unknown
  between thresholds                  → retain previous state

Person state (considering only PPE required in that zone):
  any missing  → violation
  any unknown  → uncertain
  otherwise    → compliant

Stable state: violation/compliant require 8 consecutive frames; uncertain requires 30
Event: opens when the stable state becomes violation/uncertain; closes when it changes
       or when the track has been missing for > 30 frames
```

These values come from `config.yaml` and can be adjusted by location. Default demo/top-down zones require only `helmet` and `vest`; `gloves` and `shoes` are still detected but remain `not_required` until an operator enables them for a zone.

### Multiple cameras

One model serves all cameras in a shared batch. Frame capture continues independently; only model inference time is shared.

```text
CAM-01 ─ CameraReader (thread) ─┐  latest frame only
CAM-02 ─ CameraReader (thread) ─┼─> InferenceScheduler (1 thread)
CAM-03 ─ CameraReader (thread) ─┘     pose.predict([f1,f2,f3])  ← one call for all
                                      ppe.predict([f1,f2,f3])
                     ┌──────────────────────┼──────────────────────┐
              CameraSession CAM-01   CameraSession CAM-02   CameraSession CAM-03
              (separate tracker, zones, hysteresis, evidence, clips, statistics)
                                            │
                                        EventSink → database, alerts
```

Each camera must have its own tracker; tracking across a mixed batch can swap worker IDs between cameras. A disconnected camera leaves the batch until it reconnects, while other cameras continue. Measure actual capacity with `scripts/benchmark_cameras.py` on the target hardware.

### Video sources: simulation versus analysis

```text
Simulated live  : video ─(looped at original speed)─> shared camera batch ─> "simulasi" events
Recorded analysis: video ─> background AnalysisJob (5–10 checks/sec of video time,
                   pausable and resumable after restart) ─> "rekaman" events
                   event time = recording start time + position within the video
```

`source_kind` (`live` | `rekaman` | `simulasi`) is stored on each event. Analytics, the Agent, and K3 reports include `live` + `rekaman` by default; simulated data is included only if explicitly selected and is never included in K3 reports.

### K3 Agent request flow

1. `POST /api/agent/chat` receives a message and history; applies a per-username and per-IP rate limit; `clean_history` retains at most eight messages that pass filtering.
2. `guard.check_input` checks the question. If rejected, it returns a fixed response without calling the model and records the rejection in `AuditLog`.
3. The model calls read-only tools (at most six rounds); `_clean_args` revalidates arguments before calling `queries.py`.
4. `guard.check_output` checks the answer; responses containing code or leaked instructions are replaced with a refusal.
5. Evidence is returned separately from the answer text so the frontend shows real photographs rather than model-invented URLs.

---

## 5. Data model

- **Database:** SQLite (`backend/data/ppe.db`) with SQLModel. `init_db` creates tables at startup.
- **Lightweight migration:** new columns are added to existing tables automatically at startup. Renaming or dropping columns and changing their types are not handled automatically.
- **Time:** all `NaiveDatetime` columns store WIB local time without a time-zone offset, produced by `queries.now_local()` or `Runner._now()`.

| Table | Contents |
|---|---|
| `Violation` | One violation or uncertain event: camera, zone, track/worker IDs, start/end times, duration, missing/uncertain PPE, confidence, evidence paths and hashes (photos and clips), review, model version, `source_kind` |
| `TrackRecord` | Summary of one worker per session: worst state and PPE items ever missing; basis of the compliance rate |
| `AuditLog` | Append-only record of human actions: reviews, accounts, zones, cameras/videos/jobs, interventions, settings, Agent rejections, retention |
| `User` | Dashboard account: username, scrypt password hash, role, active status, `telegram_id` |
| `SessionToken` | Login session: token hash and expiration |
| `LoginFailure` | Failed-login attempts for layered throttling |
| `Intervention` | K3 action for before-and-after comparison |
| `ZoneRule` | Dashboard zone rules: normalized polygon and required PPE for each `(name, camera_id)` |
| `Setting` | Dashboard detection settings (sensitivity, shifts, alerts, retention) |
| `QualityStat` | Per-camera, per-minute input quality (brightness, sharpness, confidence) |
| `Camera` | Multi-source camera: `source_kind`, encrypted URL (Fernet), `video_id`, mode, `sample_rate` |
| `VideoFile` / `UploadSession` / `AnalysisJob` | Video registry, resumable upload, and recorded-analysis jobs |

**Relationships** are logical (no declared foreign keys): `Violation` ↔ `TrackRecord` through `(session, camera_id, track_id)`; `AuditLog.violation_id` → `Violation.id`. A “unique person” is a distinct `(session, camera_id, track_id)` combination.

**Evidence files** are stored under `backend/data/evidence/YYYY-MM-DD/`; the database stores only relative paths. Files must not be changed after writing, or their hashes will no longer match. Retention deletes older files and clears their paths while keeping the database rows; `pending` events are not touched.

---

## 6. Security

The Agent uses layered defenses:

| Layer | Mechanism |
|---|---|
| Input filter | `guard.check_input` runs before the model: rejects prompt injection, requests for secrets or data changes, coding requests, and topics outside K3/PPE. Text is normalized first (accented characters, invisible characters, substitutions such as `h4ck`). Rejected questions **never reach the model**. |
| Read-only tools | The model has access only to four read-only tools; it cannot run SQL, access files or a shell, or perform writes. |
| Argument validation | `_clean_args`: `item` must be one of the four PPE codes; `limit` is restricted to 1–12; `camera_id` and `zone` are truncated to 64 characters. |
| History | `clean_history` accepts assistant messages only with a valid server-issued HMAC signature (`sign_answer`), preventing fabricated replies carrying new instructions from reaching the model. |
| Output filter | `guard.check_output` replaces answers containing code or leaked instructions with a refusal. Chat photographs come from tool results, not model-provided URLs. |

Additional protections include accounts with session cookies and server-enforced roles, scrypt password hashes from the standard library, layered login throttling, error redaction (generic 502 responses with details retained only in server logs), anti-SSRF camera URL validation, Origin checks on write requests, and encrypted camera URLs (Fernet). Sensitive variables (`DASHBOARD_PASSWORD`, API keys, Telegram tokens, RTSP URLs, `DATABASE_URL`) are stored in `.env`, which is not committed.

The database and evidence contain personal data (relevant to Indonesia's Personal Data Protection Law, UU PDP); faces are blurred in evidence and the live view.

---

## 7. Coding conventions

### Stack and principles

- Backend and pipeline: Python 3.11, FastAPI, SQLModel, OpenCV, NumPy, Ultralytics.
- Frontend: strict TypeScript, React 18 function components, Vite, plain CSS, Recharts.
- Tests: pytest for the backend; frontend type checking runs through `npm run build`.
- Readable code is preferable to clever code. Each function should have one responsibility. Tunable values belong in `config.yaml`, not scattered constants.

### Language

| Element | Language |
|---|---|
| Variable, function, class, and file names | English (`track_id`, `associate`, `StateManager`) |
| Docstrings and comments | Indonesian |
| UI text, user-facing errors, Agent answers | Indonesian |
| API JSON response keys | Indonesian (`waktu`, `apd_kurang`) |
| Internal PPE codes | English: `helmet`, `vest`, `gloves`, `shoes` |

### Layering rules

- `pipeline/` must not import `app/`.
- `app/main.py` contains thin routes: validate → call `queries` / `agent` → return the result.
- Every data-changing endpoint uses `Depends(supervisor)` / `Depends(admin)` and records `auth.audit(...)`.
- Database access uses `Session(engine)` and `select(...)`; no raw SQL.
- The frontend hides unauthorized actions using `can(useMe(), "supervisor")`; the server remains the authority.

### Comments

Explain **why**, not **what**. Document thresholds whose origin is unclear or move them to `config.yaml`.

### Checklist before finishing a change

- `cd backend && python -m pytest -q` passes.
- `cd frontend && npm run build` passes when the frontend changes.
- Open modified pages at desktop width and at ≤ 900px.
- No new debug `print`/`console.log`, dead code, or unused imports.
- Update any affected documentation under `docs/`.

---

## 8. Deployment

| Environment | Command | Notes |
|---|---|---|
| Development on Mac M1 | `uvicorn` + `npm run dev` | `device: mps` in configuration |
| Docker demo | `docker compose up --build` | CPU, non-root user, one worker, volumes for data/weights/config |
| Production | `docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d --build` | Caddy HTTPS in front, port 8000 closed externally, `COOKIE_SECURE=1`, `/api/healthz` health check |

### External services

| Service | Purpose | Required? | On failure |
|---|---|---|---|
| Anthropic API / Ollama | Agent model | No | Agent endpoint returns 502; the offline router can still answer deterministic count questions |
| Telegram Bot API | Alerts and review buttons | No | Error logged; pipeline continues, polling retries with a delay of up to 60 seconds |
| Pose-weight download | First pipeline run | Yes, once | Pipeline startup fails; error appears in `/api/health` |

### Scaling considerations

- Run **one** `worker` service instance (one model and one scheduler for all cameras).
- SQLite is sufficient for one writer; multiple writers would require PostgreSQL via `DATABASE_URL` and a migration.
- Evidence is stored on local disk; long-term deployments would need object storage and a retention policy.
- Inference can be exported to CoreML/ONNX for higher throughput; PPE detection can run every N frames because hysteresis smooths the results.
- Monitoring: `/api/healthz` (public), `/api/health` and the Sistem (System) page (login required), Compose health checks, worker heartbeat, and per-minute camera health.
