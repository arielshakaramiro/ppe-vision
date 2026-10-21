"""FastAPI: API dashboard, stream live, agent, dan penyajian frontend."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Literal

import tempfile
import uuid as _uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, col, delete, select

from pipeline.runner import LiveState, CameraSession
from pipeline.scheduler import InferenceScheduler
from pipeline.detector import load_detector

from . import agent, auth, camera_runtime, camera_store, config, videos
from .alerts import telegram_enabled
from .telegram_bot import TelegramPoller
from . import queries as Q
from . import settings_store, worker_health, zones_store
from . import jobs as jobs_mod
from .config import BASE_DIR, EVIDENCE_DIR, load_config
from .auth import CurrentUser
from .db import (AnalysisJob, AuditLog, Camera, ChatHistory, Conversation, Intervention, TrackRecord, UploadSession,
                 User, VideoFile, Violation, ZoneRule, engine, extract_conversation_title, init_db)
from .health import all_cameras
from .report import build_report
from .review_export import build_review_zip, iter_file
from .security import RateLimiter, parse_networks, redact, resolve_client, validate_stream_url
from .sink import DbSink


class RemoteLiveState:
    def __init__(self, camera_id: str = "default"):
        self.camera_id = camera_id
        self.tmp_dir = Path(tempfile.gettempdir()) / "ppe_live"

    @property
    def jpeg(self) -> bytes | None:
        try:
            return (self.tmp_dir / f"{self.camera_id}.jpg").read_bytes()
        except FileNotFoundError:
            return None

    @property
    def clean_jpeg(self) -> bytes | None:
        try:
            return (self.tmp_dir / f"{self.camera_id}.clean.jpg").read_bytes()
        except FileNotFoundError:
            return None

    @property
    def data(self) -> dict:
        try:
            return json.loads((self.tmp_dir / f"{self.camera_id}.json").read_text())
        except Exception:
            return {"people": [], "fps": 0.0, "running": False, "error": None}

    @property
    def lock(self):
        class DummyLock:
            def __enter__(self): pass
            def __exit__(self, *args): pass
        return DummyLock()

def get_remote_live(camera: str | None) -> RemoteLiveState:
    cam_id = camera if camera else str(load_config()["camera"]["id"])
    return RemoteLiveState(cam_id)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ppe.api")

live = LiveState("default")
scheduler: InferenceScheduler | None = None
camera_sessions: dict[str, CameraSession] = {}
runner_lock = threading.Lock()
inference_lock = threading.Lock()   # dibagi scheduler live + worker job agar model tidak paralel
camera_mode = "demo"          # demo | stream
agent_limiter = RateLimiter(config.AGENT_RATE_LIMIT, 60)
camera_limiter = RateLimiter(6, 60)

Item = Literal["helmet", "vest", "gloves", "shoes"]
Kind = Literal["violation", "uncertain", "all"]
Review = Literal["pending", "confirmed", "false_positive"]


def _stop_pipeline(timeout: float = 15) -> None:
    global scheduler, camera_sessions
    if scheduler:
        scheduler.stop()
        if scheduler.thread.is_alive():
            scheduler.thread.join(timeout)
            if scheduler.thread.is_alive():
                raise RuntimeError("Scheduler belum berhenti; sesi kamera tidak boleh dihentikan saat batch masih aktif")
    for c in camera_sessions.values():
        c.stop()
    scheduler = None
    camera_sessions = {}
    live.running = False


def _detector():
    """Bangun detektor dari config (bobot pose + ppe). Dipakai scheduler live dan worker job."""
    return load_detector(copy.deepcopy(load_config()), BASE_DIR)


recorded_job_manager = jobs_mod.JobManager(_detector, inference_lock, base_dir=BASE_DIR)
_api_live_detector = None
telegram_poller: TelegramPoller | None = None


def _start_telegram_poller() -> None:
    """Jalankan poller pesan & review Telegram jika bot aktif dan worker mandiri tidak berjalan."""
    global telegram_poller
    if telegram_poller and not telegram_poller._stop.is_set():
        return
    if config.RUN_PIPELINE and telegram_enabled() and config.TELEGRAM_POLLING and not worker_health.read().get("sehat"):
        telegram_poller = TelegramPoller()
        telegram_poller.start()
        log.info("Telegram poller aktif di proses API.")


def _stop_telegram_poller() -> None:
    global telegram_poller
    if telegram_poller:
        telegram_poller.stop()
        telegram_poller = None


def _start_recorded_job_runner() -> None:
    """Mulai runner ringan khusus AnalysisJob tanpa loop backup/retensi worker produksi."""
    with Session(engine) as db:
        for j in db.exec(select(AnalysisJob).where(AnalysisJob.status == "berjalan")).all():
            j.status = "menunggu"
            db.add(j)
        db.commit()
    recorded_job_manager.start()
    log.info("Runner analisis rekaman aktif di proses API.")


def _reload_api_live_pipeline(reason: str) -> None:
    """Restart pipeline live/simulasi di proses API agar mengikuti konfigurasi kamera terbaru."""
    global _api_live_detector
    with runner_lock:
        log.info("Memuat ulang pipeline live API: %s", reason)
        if _api_live_detector is None:
            _api_live_detector = _detector()
        _stop_pipeline()
        _start_pipeline(detector=_api_live_detector)


def _reconcile_live_camera(cid: str, detector=None) -> None:
    """Ganti hanya sesi kamera yang berubah tanpa menghentikan scheduler kamera lain."""
    global scheduler, _api_live_detector
    with runner_lock:
        with Session(engine) as db:
            cam = db.get(Camera, cid)
            active = cam is not None and cam.enabled and not (cam.source_kind == "video" and cam.mode == "analisis")
            video = db.get(VideoFile, cam.video_id) if active and cam is not None and cam.video_id else None
            cfg = camera_runtime.live_camera_config(cam, video) if active and cam is not None else None
            kind = camera_runtime.source_kind_for(cam) if active and cam is not None else None

        old = camera_sessions.get(cid)
        if old and cfg and old.reader.source == cfg["camera"]["source"] and getattr(old.sink, "source_kind", None) == kind:
            return

        if scheduler and old:
            scheduler.remove_camera(cid)  # tunggu batch lama kamera ini sebelum stop/flush
        if old:
            old.stop()
            camera_sessions.pop(cid, None)

        if cfg is not None and kind is not None:
            session = None
            try:
                session = CameraSession(cid, cfg, DbSink(source_kind=kind), LiveState(cid), base_dir=BASE_DIR,
                                        zone_provider=(lambda camera_id=cid: zones_store.snapshot(camera_id)))
                session.start()
                if scheduler is None:
                    if detector is None:
                        if _api_live_detector is None:
                            _api_live_detector = _detector()
                        detector = _api_live_detector
                    scheduler = InferenceScheduler(detector, [], lock=inference_lock)
                    scheduler.start()
                scheduler.add_camera(session)
                camera_sessions[cid] = session
            except Exception as e:
                if session is not None and session.live.running:
                    session.stop()
                failed = LiveState(cid)
                failed.error = f"Gagal memulai kamera: {type(e).__name__}"
                failed.update(None, [], 0)
                log.exception("Gagal memulai kamera %s", cid)
        live.running = bool(camera_sessions)


def _reload_api_live_pipeline_if_enabled(reason: str, camera_id: str | None = None) -> None:
    """Reload pipeline API hanya bila RUN_PIPELINE=1.

    Perubahan kamera tetap berhasil disimpan walaupun model/sumber video gagal dibuka; error
    pipeline dicatat ke status live supaya operator melihat masalahnya di dashboard.
    """
    if not config.RUN_PIPELINE:
        return
    try:
        if camera_id is None:
            _reload_api_live_pipeline(reason)
        else:
            _reconcile_live_camera(camera_id)
    except Exception as e:
        if camera_id is None:
            live.running = False
            live.error = f"Gagal memuat ulang pipeline live: {type(e).__name__}"
        else:
            failed = LiveState(camera_id)
            failed.error = f"Gagal memuat ulang kamera: {type(e).__name__}"
            failed.update(None, [], 0)
        log.exception("Gagal memuat ulang pipeline live API: %s", reason)


def _start_pipeline(source: str | None = None, detector=None) -> None:
    """Mulai pipeline multi-kamera dari baris Camera yang aktif di database.

    - Kamera stream -> event "live"; kamera video mode "simulasi" -> event "simulasi"
      (keduanya masuk scheduler live). Kamera video mode "analisis" TIDAK dijalankan di sini;
      itu ditangani worker JobManager.
    - `source` (opsional) hanya dipakai jalur kompatibilitas `POST /api/camera`: menimpa
      sumber kamera default supaya demo lama tetap jalan.
    """
    global scheduler, camera_sessions
    base_cfg = copy.deepcopy(load_config())
    live.error = None
    detector = detector or _detector()

    sessions: list[CameraSession] = []
    camera_sessions = {}
    with Session(engine) as db:
        cams = db.exec(select(Camera).where(Camera.enabled == True)).all()  # noqa: E712
        default_id = base_cfg["camera"]["id"]
        for cam in cams:
            if cam.source_kind == "video" and cam.mode == "analisis":
                continue  # ditangani worker job, bukan scheduler live
            video = db.get(VideoFile, cam.video_id) if cam.video_id else None
            cfg = camera_runtime.live_camera_config(cam, video, base_cfg)
            if source and cam.id == default_id:
                cfg["camera"]["source"] = source  # jalur kompatibilitas /api/camera
            # Frame/status ditulis ke tempdir per kamera agar bisa dibaca konsisten oleh endpoint API.
            ls = LiveState(cam.id)
            sk = camera_runtime.source_kind_for(cam)
            try:
                session = CameraSession(cam.id, cfg, DbSink(source_kind=sk), ls, base_dir=BASE_DIR,
                                        zone_provider=(lambda cid=cam.id: zones_store.snapshot(cid)))
                session.start()
            except Exception as e:
                ls.error = f"Gagal memulai kamera: {type(e).__name__}"
                ls.update(None, [], 0)
                log.exception("Gagal memulai kamera %s", cam.id)
                continue
            camera_sessions[cam.id] = session
            sessions.append(session)

    if not sessions:
        live.running = False
        return
    scheduler = InferenceScheduler(detector, sessions, lock=inference_lock)
    scheduler.start()
    live.running = True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if config.PRODUCTION:
        config.validate_production_config()
    init_db()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    zones_store.seed_from_config_if_empty()
    seed_camera_if_empty()
    auth.bootstrap_admin()
    with Session(engine) as db:
        if not auth.has_accounts(db):
            log.warning("Belum ada akun: dashboard hanya bisa dibuka dari komputer server ini. "
                        "Buat admin lewat halaman Pengguna atau: python -m scripts.users add <nama> --role admin")
        elif config.PRODUCTION and config.DASHBOARD_PASSWORD:
            log.warning("PERINGATAN KEAMANAN: DASHBOARD_PASSWORD masih terisi di .env pada mode produksi meskipun akun sudah ada. "
                        "Segera kosongkan DASHBOARD_PASSWORD dari .env.")
    if config.ANALYSIS_JOB_RUNNER:
        _start_recorded_job_runner()
    if config.RUN_PIPELINE:
        _reload_api_live_pipeline_if_enabled("startup API")
    _start_telegram_poller()
    try:
        yield
    finally:
        _stop_telegram_poller()
        _stop_pipeline()
        recorded_job_manager.stop()

app = FastAPI(title="APD Vision API", lifespan=lifespan, docs_url="/docs" if config.API_DOCS else None,
              redoc_url=None, openapi_url="/openapi.json" if config.API_DOCS else None)
app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS, allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type"])

PUBLIC_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/healthz"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


_TRUSTED_PROXIES = parse_networks(config.TRUSTED_PROXIES)


def _client_info(request: Request) -> tuple[str, bool]:
    peer = request.client.host if request.client else None
    return resolve_client(peer, request.headers.get("x-forwarded-for"), _TRUSTED_PROXIES)


def _client(request: Request) -> str:
    return _client_info(request)[0]


def _origin_allowed(request: Request) -> bool:
    """Perlindungan CSRF: request pengubah data dari situs lain ditolak.

    Browser selalu mengirim header Origin pada request lintas situs. Cookie sesi juga SameSite=Strict,
    jadi ini lapis kedua.
    """
    from urllib.parse import urlsplit
    origin = request.headers.get("origin")
    if not origin:
        return True  # bukan dari browser (curl, test, script); tetap butuh sesi yang valid
    return urlsplit(origin).netloc == request.headers.get("host", "") or origin in config.ALLOWED_ORIGINS


def _resolve_user(request: Request) -> CurrentUser | str | None:
    with Session(engine) as db:
        if not auth.has_accounts(db):
            ip, via_proxy = _client_info(request)
            # Lewat reverse proxy = bukan dari komputer server, walaupun proxy-nya berjalan di localhost.
            # Header penerusan dianggap tanda proxy, juga saat TRUSTED_PROXIES belum diisi.
            forwarded = any(h in request.headers for h in ("x-forwarded-for", "forwarded", "x-real-ip"))
            if not via_proxy and not forwarded and ip in auth.LOCAL_HOSTS:
                return CurrentUser("lokal", "admin", local=True)
            return "no_accounts"
        return auth.user_from_token(db, request.cookies.get(auth.COOKIE))


@app.middleware("http")
async def session_auth(request: Request, call_next):
    """Semua /api dan /evidence butuh sesi login. File frontend boleh dimuat supaya halaman login tampil."""
    path = request.url.path
    if request.method in MUTATING and not _origin_allowed(request):
        return JSONResponse({"detail": "Asal request tidak diizinkan"}, status_code=403)
    protected = path.startswith("/api/") or path.startswith("/evidence/")
    if not protected or request.method == "OPTIONS":
        return await call_next(request)
    user = await run_in_threadpool(_resolve_user, request)
    request.state.user = user if isinstance(user, CurrentUser) else None
    if path in PUBLIC_PATHS:
        return await call_next(request)
    if user == "no_accounts":
        return JSONResponse({"detail": "Belum ada akun. Buka dashboard dari komputer server untuk membuat admin."},
                            status_code=403)
    if user is None:
        return JSONResponse({"detail": "Perlu login"}, status_code=401)
    return await call_next(request)


def require(role: str):
    """Dependency peran minimum. Dicek di server; menyembunyikan tombol di UI saja tidak cukup."""
    def dep(request: Request) -> CurrentUser:
        user: CurrentUser | None = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(401, "Perlu login")
        if auth.LEVEL[user.role] < auth.LEVEL[role]:
            raise HTTPException(403, f"Butuh peran {role} atau lebih tinggi")
        return user
    return dep


viewer, supervisor, admin = require("viewer"), require("supervisor"), require("admin")


def get_db():
    with Session(engine) as db:
        yield db


def _range(period: str | None, start: str | None, end: str | None):
    try:
        return Q.resolve_range(period, start, end)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/healthz")
def healthz():
    """Cek hidup untuk Docker/monitoring. Publik, jadi sengaja tidak memuat detail apa pun."""
    return {"ok": True}


@app.get("/api/health")
def health(user: CurrentUser = Depends(viewer)):
    return {"ok": True, "llm": agent.provider(), "ollama": agent.ollama_status(),
            "worker": worker_health.read()}


@app.post("/api/agent/warmup")
def agent_warmup(db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    try:
        ok = agent.warm_ollama()
        auth.audit(db, "agent_ollama_warmup", user.username, "Model Ollama dimuat", commit=False)
        db.commit()
        return {"ok": ok, "ollama": agent.ollama_status()}
    except Exception as e:
        raise HTTPException(503, f"Ollama belum siap: {type(e).__name__}")


@app.get("/api/live")
def live_status(camera: str | None = Query(None, max_length=32), db: Session = Depends(get_db)):
    ls = get_remote_live(camera)
    data = ls.data
    people = data.get("people", [])
    running = data.get("running", False)
    err = data.get("error")
    fps = data.get("fps", 0.0)

    total = len(people)
    compliant = sum(1 for p in people if p["status"] == "compliant")
    s, e = Q.resolve_range("hari_ini")
    # Live dipakai juga untuk video simulasi saat demo/uji upload. Gunakan source="semua"
    # agar panel "Pelanggaran hari ini" tidak terlihat kosong padahal pipeline baru mencatat
    # event dari video simulasi.
    recent = [Q.violation_dict(v) for v in Q.fetch_violations(db, s, e, source="semua")[:8]]
    return {
        "running": running, "error": err, "fps": round(fps, 1),
        "frame": data.get("frame"),
        "people": people,
        "kpi": {
            "pekerja_terpantau": total,
            "pelanggaran_aktif": sum(1 for p in people if p["status"] == "violation"),
            "safety_score": round(100 * compliant / total) if total else None,
        },
        "recent": recent,
    }


_streams: dict[str, int] = {}
MAX_STREAMS_PER_USER = 12


def _stream_interval_s() -> float:
    target = float(load_config().get("stream", {}).get("target_fps", 24))
    target = max(1.0, min(30.0, target))
    return 1.0 / target


def _session_still_valid(request: Request) -> bool:
    res = _resolve_user(request)
    return isinstance(res, CurrentUser)


@app.get("/api/stream.mjpg")
async def stream(request: Request, camera: str | None = Query(None, max_length=32),
                 clean: bool = False,
                 user: CurrentUser = Depends(viewer)):
    if _streams.get(user.username, 0) >= MAX_STREAMS_PER_USER:
        raise HTTPException(429, "Terlalu banyak tab Live terbuka. Tutup salah satunya.")
    _streams[user.username] = _streams.get(user.username, 0) + 1
    ls = get_remote_live(camera)
    return StreamingResponse(_guarded_frames(request, user, ls, clean=clean), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


async def _guarded_frames(request: Request, user: CurrentUser, ls, clean: bool = False):
    """Kirim frame selama sesi masih berlaku. Sesi dicek ulang tiap 5 detik: logout, akun dinonaktifkan,
    atau password direset langsung menghentikan stream yang sedang terbuka."""
    last, next_check = None, 0.0
    interval = _stream_interval_s()
    loop = asyncio.get_event_loop()
    try:
        while not await request.is_disconnected():
            if loop.time() >= next_check:
                if not await run_in_threadpool(_session_still_valid, request):
                    break
                next_check = loop.time() + 5
            with ls.lock:
                jpg = ls.clean_jpeg if clean else ls.jpeg
            if jpg is not None and jpg != last:
                last = jpg
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            await asyncio.sleep(interval)
    finally:
        _streams[user.username] = max(0, _streams.get(user.username, 1) - 1)


Source = Literal["nyata", "live", "semua"]


@app.get("/api/violations")
def list_violations(period: str | None = "7_hari", start: str | None = None, end: str | None = None,
                    item: Item | None = None, camera_id: str | None = Query(None, max_length=64),
                    zone: str | None = Query(None, max_length=64), kind: Kind = "violation",
                    review: Review | None = None, source: Source = "nyata", limit: int = Query(60, ge=1, le=500),
                    offset: int = Query(0, ge=0), group_by_worker: bool = False,
                    db: Session = Depends(get_db)):
    s, e = _range(period, start, end)
    rows = Q.fetch_violations(db, s, e, item, camera_id, zone, kind, include_false_positive=True, source=source)
    if review:
        rows = [r for r in rows if r.review == review]
    if group_by_worker:
        total_groups, items = Q.worker_evidence_groups(rows, limit=limit, offset=offset)
        return {"total": total_groups, "total_event": len(rows), "items": items}
    return {"total": len(rows), "items": [Q.violation_dict(r) for r in rows[offset:offset + limit]]}


@app.get("/api/violations/{vid}")
def get_violation(vid: int, db: Session = Depends(get_db), user: CurrentUser = Depends(viewer)):
    row = db.get(Violation, vid)
    if not row:
        raise HTTPException(404, "Event tidak ditemukan")
    return Q.violation_dict(row)


class ReviewIn(BaseModel):
    review: Review
    corrected_items: list[Item] = Field(default_factory=list)


@app.patch("/api/violations/{vid}/review")
def review_violation(vid: int, body: ReviewIn, db: Session = Depends(get_db), user: CurrentUser = Depends(supervisor)):
    row = db.get(Violation, vid)
    if not row:
        raise HTTPException(404, "Event tidak ditemukan")
    previous = row.review
    reported = set(Q.split_items(row.missing_items) + Q.split_items(row.uncertain_items))
    corrected = [i for i in body.corrected_items if i in reported] if body.review == "false_positive" else []
    if body.review == "false_positive" and not corrected:
        corrected = Q.split_items(row.missing_items)
    row.review, row.reviewed_at = body.review, Q.now_local()
    row.corrected_items = Q.item_csv(corrected)
    db.add(row)
    detail = f"sebelumnya: {previous}"
    if row.corrected_items:
        detail += f"; APD dibetulkan: {row.corrected_items}"
    auth.audit(db, f"review_{body.review}", user.username, detail, violation_id=vid, commit=False)
    db.commit()  # perubahan dan jejak auditnya tersimpan bersama atau tidak sama sekali
    db.refresh(row)
    return Q.violation_dict(row)


@app.get("/api/stats")
def stats(period: str | None = "7_hari", start: str | None = None, end: str | None = None,
          source: Source = "nyata", camera_ids: str | None = Query(None, max_length=1000),
          db: Session = Depends(get_db)):
    s, e = _range(period, start, end)
    cameras = [c.strip() for c in (camera_ids or "").split(",") if c.strip()]
    return Q.summary(db, s, e, source=source, camera_ids=cameras or None)


# ---------- kamera: ganti sumber dari dashboard (demo video / stream HP atau IP camera) ----------

class CameraIn(BaseModel):
    mode: Literal["demo", "stream"]
    url: str | None = Field(None, max_length=500)


@app.get("/api/camera")
def camera_status():
    cfg = load_config()
    cid = cfg["camera"]["id"]
    with Session(engine) as db:
        c = db.get(Camera, cid)
        sumber = camera_store.masked_from_enc(c.source_enc) if c and c.source_kind == "stream" else str(c.video_id if c else cfg["camera"]["source"])
    return {"mode": camera_mode, "sumber": sumber, **get_remote_live(cid).data}


@app.post("/api/camera")
def camera_switch(body: CameraIn, request: Request, user: CurrentUser = Depends(admin)):
    # Mengganti kamera sekarang disimpan ke database; worker perlu direstart/reload agar sumber baru dipakai.
    global camera_mode
    client = _client(request)
    if not camera_limiter.allow(client):
        raise HTTPException(429, "Terlalu sering mengganti kamera. Tunggu sebentar.")
    # Validasi URL dulu (SSRF guard) sebelum sentuh database.
    source = ""
    if body.mode == "stream":
        try:
            source = validate_stream_url(body.url or "")
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e))
    cfg = load_config()
    cid = cfg["camera"]["id"]
    with Session(engine) as db:
        cam = db.get(Camera, cid)
        if not cam:
            raise HTTPException(404, "Kamera default tidak ditemukan")
        if body.mode == "stream":
            cam.source_kind = "stream"
            cam.source_enc = camera_store.encrypt_url(source)
            cam.video_id = ""
        else:
            cam.source_kind = "video"
            cam.source_enc = ""
            cam.video_id = str(cfg["camera"]["source"])
        cam.updated_by = user.username
        db.add(cam)
        from pipeline.runner import mask_source
        auth.audit(db, "camera_switch", user.username, f"{body.mode}: {mask_source(source) if source else 'video demo'}", commit=False)
        db.commit()
    camera_mode = body.mode
    _reload_api_live_pipeline_if_enabled(f"camera_switch {cid}", camera_id=cid)
    return camera_status()


# ---------- intervensi: bandingkan kepatuhan sebelum vs sesudah ----------

class InterventionIn(BaseModel):
    at: datetime
    title: str = Field(min_length=1, max_length=120)
    note: str = Field("", max_length=1000)
    camera_id: str = Field("", max_length=64)


def _intervention_dict(i: Intervention) -> dict:
    return {"id": i.id, "waktu": i.at.isoformat(timespec="minutes"), "judul": i.title, "catatan": i.note,
            "kamera": i.camera_id or None}


@app.get("/api/interventions")
def list_interventions(db: Session = Depends(get_db)):
    rows = db.exec(select(Intervention).order_by(col(Intervention.at).desc())).all()
    return [_intervention_dict(i) for i in rows]


@app.post("/api/interventions", status_code=201)
def create_intervention(body: InterventionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(supervisor)):
    at = Q._to_local(body.at)
    row = Intervention(at=at, title=body.title.strip(), note=body.note.strip(), camera_id=body.camera_id.strip(),
                       created_at=Q.now_local())
    db.add(row)
    db.flush()  # dapatkan id sebelum commit
    auth.audit(db, "intervention_created", user.username, f"#{row.id} {row.title}", commit=False)
    db.commit()
    db.refresh(row)
    return _intervention_dict(row)


@app.delete("/api/interventions/{iid}", status_code=204)
def delete_intervention(iid: int, db: Session = Depends(get_db), user: CurrentUser = Depends(supervisor)):
    row = db.get(Intervention, iid)
    if not row:
        raise HTTPException(404, "Intervensi tidak ditemukan")
    title = row.title
    db.delete(row)
    auth.audit(db, "intervention_deleted", user.username, f"#{iid} {title}", commit=False)
    db.commit()


@app.get("/api/interventions/{iid}/compare")
def compare_intervention(iid: int, days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db)):
    row = db.get(Intervention, iid)
    if not row:
        raise HTTPException(404, "Intervensi tidak ditemukan")
    return {"intervensi": _intervention_dict(row), "hari": days,
            **Q.compare_intervention(db, row.at, days, row.camera_id or None)}


# ---------- kualitas model, kesehatan kamera, laporan ----------

@app.get("/api/model-quality")
def model_quality(db: Session = Depends(get_db)):
    return Q.model_quality(db)


@app.post("/api/model-quality/export")
def export_model_reviews(version: str | None = Query(None, max_length=64),
                         db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    query = select(Violation).where(col(Violation.review).in_(["confirmed", "false_positive"]))
    if version:
        query = query.where(Violation.model_version == version)
    rows = db.exec(query.order_by(Violation.started_at, Violation.id)).all()
    archive, counts = build_review_zip(rows, EVIDENCE_DIR)
    detail = (f"versi={version or 'semua'}; terkonfirmasi={counts['confirmed']}; "
              f"positif_palsu={counts['false_positive']}; hilang={counts['hilang']}; "
              f"hash_tidak_cocok={counts['hash_tidak_cocok']}")
    auth.audit(db, "hasil_review_diekspor", user.username, detail, commit=False)
    db.commit()
    stamp = Q.now_local().strftime("%Y%m%d-%H%M%S")
    filename = f"hasil-review-apd-{stamp}.zip"
    return StreamingResponse(
        iter_file(archive), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
        background=BackgroundTask(archive.close),
    )


@app.get("/api/cameras/health")
def cameras_health(db: Session = Depends(get_db)):
    return all_cameras(db, Q.now_local())


@app.get("/api/reports/k3", response_class=HTMLResponse)
def report_k3(period: str | None = "30_hari", start: str | None = None, end: str | None = None,
              db: Session = Depends(get_db)):
    s, e = _range(period, start, end)
    return HTMLResponse(build_report(db, s, e), headers={"Cache-Control": "no-store"})


# ---------- zona: poligon dan APD wajib (dibaca pipeline lewat zones_store) ----------

APD_CODES = ("helmet", "vest", "gloves", "shoes")


def _zone_dict(z: ZoneRule) -> dict:
    try:
        polygon = json.loads(z.polygon_json) if z.polygon_json else []
    except ValueError:
        polygon = []
    required = [i for i in z.required_csv.split(",") if i in APD_CODES]
    if getattr(config, "PRODUCTION", False):
        required = [i for i in required if i in getattr(config, "STABLE_ITEMS", ("helmet", "vest"))]
    return {"name": z.name, "camera_id": z.camera_id, "polygon": polygon, "required": required,
            "updated_at": z.updated_at.isoformat(), "updated_by": z.updated_by}


def _check_required(items: list[str]) -> list[str]:
    cleaned = [i for i in dict.fromkeys(items) if i in APD_CODES]  # buang duplikat, jaga urutan
    if not cleaned:
        raise HTTPException(400, "Minimal satu APD wajib dipilih")
    unknown = [i for i in items if i not in APD_CODES]
    if unknown:
        raise HTTPException(400, f"Kode APD tidak dikenal: {', '.join(sorted(set(unknown)))}")
    if getattr(config, "PRODUCTION", False):
        experimental = [i for i in cleaned if i in getattr(config, "EXPERIMENTAL_ITEMS", ("gloves", "shoes"))]
        if experimental:
            raise HTTPException(
                400,
                f"APD eksperimental ({', '.join(experimental)}) belum diizinkan sebagai APD wajib produksi "
                "(akurasi model sarung tangan dan sepatu belum memenuhi gerbang rilis)",
            )
    return cleaned


def _check_polygon(polygon: list) -> str:
    if not isinstance(polygon, list) or not (3 <= len(polygon) <= 20):
        raise HTTPException(400, "Poligon harus 3-20 titik")
    pts = []
    for p in polygon:
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            raise HTTPException(400, "Setiap titik poligon harus [x, y]")
        x, y = p
        if not all(isinstance(v, (int, float)) for v in (x, y)) or not (0 <= x <= 1 and 0 <= y <= 1):
            raise HTTPException(400, "Koordinat titik harus di dalam frame (0..1)")
        pts.append([float(x), float(y)])
    return json.dumps(pts)


class ZoneRequiredIn(BaseModel):
    required: list[str] = Field(default_factory=list)


class ZoneIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    camera_id: str = Field(default="", max_length=64)
    polygon: list = Field(default_factory=list)
    required: list[str] = Field(default_factory=list)


def _find_zone(db: Session, name: str, camera_id: str) -> ZoneRule | None:
    """Zona unik per (name, camera_id). camera_id="" = zona global semua kamera."""
    return db.exec(select(ZoneRule).where(ZoneRule.name == name,
                                          ZoneRule.camera_id == camera_id)).first()


@app.get("/api/zones")
def list_zones(camera_id: str | None = Query(None, max_length=64),
               db: Session = Depends(get_db), _: CurrentUser = Depends(viewer)):
    q = select(ZoneRule)
    if camera_id is not None:
        # Zona yang berlaku untuk kamera ini: khusus kamera + global.
        q = q.where(col(ZoneRule.camera_id).in_(("", camera_id)))
    return [_zone_dict(z) for z in db.exec(q.order_by(ZoneRule.camera_id, ZoneRule.name)).all()]


@app.patch("/api/zones/{name}/required")
def set_zone_required(name: str, body: ZoneRequiredIn, camera_id: str = Query("", max_length=64),
                      db: Session = Depends(get_db), user: CurrentUser = Depends(supervisor)):
    z = _find_zone(db, name, camera_id)
    if not z:
        raise HTTPException(404, "Zona tidak ditemukan")
    required = _check_required(body.required)
    z.required_csv = ",".join(required)
    z.updated_at, z.updated_by = Q.now_local(), user.username
    db.add(z)
    auth.audit(db, "zone_required_diubah", user.username, f"{name}: {z.required_csv}", commit=False)
    db.commit()
    zones_store.bump()
    db.refresh(z)
    return _zone_dict(z)


@app.post("/api/zones", status_code=201)
def create_zone(body: ZoneIn, db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    name = body.name.strip()
    cam = body.camera_id.strip()
    if not name:
        raise HTTPException(400, "Nama zona wajib diisi")
    if _find_zone(db, name, cam):
        raise HTTPException(400, "Nama zona sudah dipakai untuk kamera ini")
    polygon_json = _check_polygon(body.polygon)
    required = _check_required(body.required)
    z = ZoneRule(name=name, camera_id=cam, polygon_json=polygon_json,
                 required_csv=",".join(required), updated_at=Q.now_local(), updated_by=user.username)
    db.add(z)
    auth.audit(db, "zone_dibuat", user.username, f"{name} ({cam or 'semua kamera'})", commit=False)
    db.commit()
    zones_store.bump()
    db.refresh(z)
    return _zone_dict(z)


@app.put("/api/zones/{name}")
def update_zone(name: str, body: ZoneIn, camera_id: str = Query("", max_length=64),
                db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    z = _find_zone(db, name, camera_id)
    if not z:
        raise HTTPException(404, "Zona tidak ditemukan")
    new_name = body.name.strip()
    new_cam = body.camera_id.strip()
    if not new_name:
        raise HTTPException(400, "Nama zona wajib diisi")
    if (new_name, new_cam) != (name, camera_id) and _find_zone(db, new_name, new_cam):
        raise HTTPException(400, "Nama zona sudah dipakai untuk kamera ini")
    z.polygon_json = _check_polygon(body.polygon)
    z.required_csv = ",".join(_check_required(body.required))
    z.name, z.camera_id = new_name, new_cam
    z.updated_at, z.updated_by = Q.now_local(), user.username
    db.add(z)
    auth.audit(db, "zone_diubah", user.username, f"{name} -> {new_name}", commit=False)
    db.commit()
    zones_store.bump()
    db.refresh(z)
    return _zone_dict(z)


@app.delete("/api/zones/{name}", status_code=204)
def delete_zone(name: str, camera_id: str = Query("", max_length=64),
                db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    z = _find_zone(db, name, camera_id)
    if not z:
        raise HTTPException(404, "Zona tidak ditemukan")
    db.delete(z)
    auth.audit(db, "zone_dihapus", user.username, name, commit=False)
    db.commit()
    zones_store.bump()


# ---------- audit log ----------

@app.get("/api/audit/actors")
def list_audit_actors(period: str | None = "7_hari", start: str | None = None, end: str | None = None,
                      db: Session = Depends(get_db), user: CurrentUser = Depends(supervisor)):
    s, e = _range(period, start, end)
    rows = db.exec(
        select(AuditLog.actor)
        .where(AuditLog.at >= s, AuditLog.at < e, AuditLog.actor != "")
        .distinct()
        .order_by(col(AuditLog.actor))
    ).all()
    return {"items": list(rows)}


@app.get("/api/audit")
def list_audit(period: str | None = "7_hari", start: str | None = None, end: str | None = None,
               actor: str | None = Query(None, max_length=64), action: str | None = Query(None, max_length=64),
               limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db), user: CurrentUser = Depends(supervisor)):
    s, e = _range(period, start, end)
    q = select(AuditLog).where(AuditLog.at >= s, AuditLog.at < e)
    if actor:
        q = q.where(AuditLog.actor == actor)
    if action:
        q = q.where(AuditLog.action == action)
    total = db.exec(select(func.count()).select_from(q.subquery())).one()
    rows = db.exec(q.order_by(col(AuditLog.at).desc()).offset(offset).limit(limit)).all()
    items = [{"waktu": r.at.isoformat(), "aksi": r.action, "aktor": r.actor,
              "detail": redact(r.detail), "violation_id": r.violation_id} for r in rows]
    return {"total": total, "items": items}


# ---------- pengaturan deteksi (sensitivitas, shift, alert, retensi) ----------

@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    return settings_store.get()


@app.put("/api/settings")
def put_settings(body: dict, db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    try:
        clean = settings_store.validate(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    settings_store.save_to_session(db, clean, user.username)
    auth.audit(db, "pengaturan_diubah", user.username,
               f"sensitivitas={clean['sensitivity']}, retensi={clean['retention']['evidence_days']}h", commit=False)
    db.commit()  # pengaturan dan jejak audit tersimpan bersama atau tidak sama sekali
    return settings_store.merge_saved(clean)



@app.post("/api/retention/simulate")
def simulate_retention(user: CurrentUser = Depends(admin)):
    from scripts import retention
    cfg = settings_store.get()["retention"]
    video_days = int(load_config().get("retention", {}).get("video_days", 14))
    r = retention.run(cfg["evidence_days"], cfg["quality_days"], dry=True, video_days=video_days)
    return {"message": (f"Simulasi: {r['file']} file evidence dari {r['event']} event, {r['video']} file video, "
                        f"dan {r['quality']} baris kualitas kamera akan dihapus. Event yang menunggu review dilewati.")}

# ---------- status sistem (bar status, label "Contoh data") ----------

_rate_states: dict[str, dict[str, int | bool]] = {}


def _rate_below_limit(cid: str, running: bool, rate: float, min_rate: float) -> bool:
    if not running:
        _rate_states.pop(cid, None)
        return False
    state = _rate_states.setdefault(cid, {"low": 0, "ok": 0, "warn": False})
    warn = bool(state["warn"])
    if rate < min_rate:
        state["low"] = int(state["low"]) + 1
        state["ok"] = 0
    elif rate >= min_rate * 1.2:
        state["ok"] = int(state["ok"]) + 1
        state["low"] = 0
    else:
        state["low"] = 0
        state["ok"] = 0
    if not warn and int(state["low"]) >= 3:
        warn = True
    elif warn and int(state["ok"]) >= 2:
        warn = False
    state["warn"] = warn
    return warn


def _camera_speeds() -> list[dict]:
    """Kecepatan per kamera dari state yang ditulis worker ke tempdir (/tmp/ppe_live)."""
    out = []
    with Session(engine) as db:
        cams = db.exec(select(Camera).where(Camera.enabled == True)).all()  # noqa: E712
    ids = [c.id for c in cams] or [load_config()["camera"]["id"]]
    min_rate = float(load_config().get("pipeline", {}).get("min_rate_per_camera", 5))
    for cid in ids:
        data = get_remote_live(cid).data
        fps = float(data.get("fps") or 0.0)
        running = bool(data.get("running"))
        rate = float(data.get("pemeriksaan_per_detik") or 0.0)
        out.append({"kamera": cid, "fps": round(fps, 1), "running": running,
                    "waktu_batch_ms": round(float(data.get("waktu_batch_ms") or 0.0), 1),
                    "pemeriksaan_per_detik": round(rate, 1),
                    "persen_frame_dilewati": round(float(data.get("persen_frame_dilewati") or 0.0), 1),
                    "di_bawah_batas": _rate_below_limit(cid, running, rate, min_rate)})
    return out


def _capacity_summary(speeds: list[dict], cfg: dict) -> dict:
    running = [row for row in speeds if row["running"]]
    measured = [row for row in running if row["pemeriksaan_per_detik"] > 0]
    batch_values = [row["waktu_batch_ms"] for row in running if row["waktu_batch_ms"] > 0]
    total_rate = sum(row["pemeriksaan_per_detik"] for row in measured)
    min_rate = float(cfg.get("pipeline", {}).get("min_rate_per_camera", 5))
    try:
        load_percent = round(min(100.0, os.getloadavg()[0] / max(1, os.cpu_count() or 1) * 100), 1)
    except (AttributeError, OSError):
        load_percent = None
    return {
        "waktu_batch_ms": round(sum(batch_values) / len(batch_values), 1) if batch_values else None,
        "jumlah_frame_batch": len(running),
        "pemeriksaan_per_kamera": round(total_rate / len(measured), 1) if measured else None,
        "batas_aman_per_kamera": min_rate,
        "beban_perangkat_persen": load_percent,
        "beban_perangkat_sumber": "load average sistem 1 menit" if load_percent is not None else "tidak tersedia",
        "perangkat": str(cfg.get("models", {}).get("device", "cpu")),
        "proyeksi_kamera": [
            {
                "jumlah": count,
                "pemeriksaan_per_kamera": round(total_rate / count, 1) if total_rate > 0 else None,
                "memenuhi_batas": (total_rate / count) >= min_rate if total_rate > 0 else None,
            }
            for count in range(1, 5)
        ],
        "catatan_proyeksi": (
            "Proyeksi membagi throughput total yang sedang terukur; lakukan benchmark di perangkat dan sumber kamera target sebelum produksi."
        ),
    }


@app.get("/api/system/status")
def system_status(db: Session = Depends(get_db), user: CurrentUser = Depends(viewer)):
    cfg = load_config()
    speeds = _camera_speeds()
    settings = settings_store.get()
    demo = bool(db.exec(select(Violation).where(Violation.session.endswith("-demo")).limit(1)).first()) or \
        bool(db.exec(select(TrackRecord).where(TrackRecord.session.endswith("-demo")).limit(1)).first())
    telegram_reviewers = db.exec(select(func.count()).select_from(User).where(
        User.active == True, User.telegram_id.is_not(None), User.telegram_id != ""  # noqa: E712
    )).one()
    return {
        "pipeline": {"running": get_remote_live(None).data.get("running", False), "error": get_remote_live(None).data.get("error"), "fps": round(float(get_remote_live(None).data.get("fps", 0.0)), 1)},
        "worker": worker_health.read(),
        "kamera": speeds,
        "versi_model": str(cfg["models"].get("version", "")),
        "model": {
            "pose": Path(str(cfg["models"].get("pose", ""))).name,
            "apd": Path(str(cfg["models"].get("ppe", ""))).name,
            "apd_setiap_n_frame": int(cfg["models"].get("ppe_every_n", 1)),
        },
        "kapasitas": _capacity_summary(speeds, cfg),
        "alert": {
            "telegram": bool(settings["alert"]["telegram"]),
            "cooldown_s": int(settings["alert"]["cooldown_s"]),
            "reviewer_terhubung": int(telegram_reviewers),
        },
        "retensi": {
            "evidence_days": int(settings["retention"]["evidence_days"]),
            "quality_days": int(settings["retention"]["quality_days"]),
            "video_days": int(cfg.get("retention", {}).get("video_days", 14)),
        },
        "data_contoh": demo,
    }



# ---------- kamera, video, upload, dan pekerjaan analisis (Fase 6) ----------

class CameraIn2(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    source_kind: Literal["stream", "video"] = "stream"
    url: str | None = Field(None, max_length=500)
    video_id: str | None = Field(None, max_length=64)
    mode: Literal["simulasi", "analisis"] | None = None
    recording_start: str | None = None
    sample_rate: float | None = None
    enabled: bool = True


class CameraPatch(BaseModel):
    name: str | None = Field(None, max_length=64)
    source_kind: Literal["stream", "video"] | None = None
    url: str | None = Field(None, max_length=500)
    video_id: str | None = Field(None, max_length=64)
    mode: Literal["simulasi", "analisis"] | None = None
    recording_start: str | None = None
    sample_rate: float | None = None
    enabled: bool | None = None


CAMERA_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
SAMPLE_RATES = (5.0, 10.0)


def _clean_camera_id(cid: str) -> str:
    cid = (cid or "").strip()
    if not CAMERA_ID_RE.match(cid):
        raise HTTPException(400, "ID kamera 1-32 karakter: huruf, angka, titik, garis bawah, atau strip")
    return cid


def _parse_recording_start(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(400, "Jam mulai rekaman harus ISO 8601, contoh 2026-09-30T08:00")


def _clean_sample_rate(value: float | None) -> float:
    if value is None:
        return 0.0
    if float(value) not in SAMPLE_RATES:
        raise HTTPException(400, "Kecepatan pemeriksaan hanya boleh 5 atau 10 per detik")
    return float(value)


def _camera_dict(c: Camera, speeds: dict[str, dict] | None = None) -> dict:
    speeds = speeds if speeds is not None else {s["kamera"]: s for s in _camera_speeds()}
    sp = speeds.get(c.id, {})
    return {
        "id": c.id, "name": c.name, "source_kind": c.source_kind,
        "sumber": camera_store.masked_from_enc(c.source_enc) if c.source_enc else (c.video_id or ""),
        "mode": c.mode, "video_id": c.video_id,
        "recording_start": c.recording_start.isoformat() if c.recording_start else None,
        "sample_rate": c.sample_rate, "enabled": c.enabled,
        "fps": sp.get("fps", 0.0), "running": sp.get("running", False),
        "pemeriksaan_per_detik": sp.get("pemeriksaan_per_detik", 0.0),
    }


def seed_camera_if_empty() -> None:
    """Isi CAM-01 dari sumber lama (config/CAMERA_SOURCE) supaya instalasi lama tetap jalan."""
    with Session(engine) as db:
        if db.exec(select(Camera).limit(1)).first():
            return
        cfg = load_config()
        src = str(cfg["camera"]["source"])
        is_stream = src.startswith(("rtsp://", "http://", "https://"))
        enc = ""
        if is_stream and config.CAMERA_SECRET_KEY:
            try:
                enc = camera_store.encrypt_url(src)
            except Exception:
                enc = ""
        db.add(Camera(id=cfg["camera"]["id"], name=cfg["camera"].get("id", "CAM-01"),
                      source_kind="stream" if is_stream else "video",
                      source_enc=enc, video_id="" if is_stream else src,
                      enabled=True, created_at=Q.now_local(), updated_by="sistem"))
        db.commit()


@app.get("/api/cameras")
def list_cameras(db: Session = Depends(get_db), user: CurrentUser = Depends(viewer)):
    speeds = {s["kamera"]: s for s in _camera_speeds()}
    return [_camera_dict(c, speeds) for c in db.exec(select(Camera).order_by(Camera.id)).all()]


def _ensure_analysis_job(db: Session, cam: Camera, actor: str) -> None:
    """Buat AnalysisJob otomatis untuk kamera video mode analisis, jika belum ada yang aktif.

    Dipanggil dalam transaksi yang sama dengan perubahan kamera (audit `job_dibuat`).
    """
    if not (cam.enabled and cam.source_kind == "video" and cam.mode == "analisis" and cam.video_id):
        return
    existing = db.exec(select(AnalysisJob).where(AnalysisJob.camera_id == cam.id,
                                                 col(AnalysisJob.status).in_(jobs_mod.ACTIVE_STATES))).first()
    if existing:
        return
    import uuid as _uuid
    job = AnalysisJob(id=_uuid.uuid4().hex, camera_id=cam.id, video_id=cam.video_id,
                      recording_start=cam.recording_start or Q.now_local(),
                      sample_rate=cam.sample_rate or 5.0, status="menunggu")
    db.add(job)
    auth.audit(db, "job_dibuat", actor, f"{cam.id} ({cam.video_id})", commit=False)


@app.post("/api/cameras", status_code=201)
def create_camera(body: CameraIn2, db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    cid = _clean_camera_id(body.id)
    if db.get(Camera, cid):
        raise HTTPException(400, "ID kamera sudah dipakai")
    enc, video_id = "", ""
    if body.source_kind == "stream":
        if not body.url:
            raise HTTPException(400, "URL kamera wajib untuk sumber stream")
        try:
            url = validate_stream_url(body.url)
            enc = camera_store.encrypt_url(url)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e))
    else:
        if not body.video_id or not db.get(VideoFile, body.video_id):
            raise HTTPException(400, "Video tidak ditemukan")
        video_id = body.video_id
    rec_start = _parse_recording_start(body.recording_start)
    sample_rate = _clean_sample_rate(body.sample_rate)
    c = Camera(id=cid, name=body.name, source_kind=body.source_kind, source_enc=enc, video_id=video_id,
               mode=body.mode or "", recording_start=rec_start, sample_rate=sample_rate,
               enabled=body.enabled, created_at=Q.now_local(), updated_by=user.username)
    db.add(c)
    auth.audit(db, "camera_dibuat", user.username, f"{cid} ({body.source_kind})", commit=False)
    _ensure_analysis_job(db, c, user.username)
    db.commit()
    _reload_api_live_pipeline_if_enabled(f"kamera dibuat {cid}", camera_id=cid)
    return _camera_dict(c)


@app.patch("/api/cameras/{cid}")
def update_camera(cid: str, body: CameraPatch, db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    c = db.get(Camera, cid)
    if not c:
        raise HTTPException(404, "Kamera tidak ditemukan")
    changes = []
    if body.name is not None:
        c.name = body.name; changes.append("nama")
    if body.source_kind is not None and body.source_kind != c.source_kind:
        c.source_kind = body.source_kind; changes.append("jenis")
        # Bersihkan field yang tak relevan supaya baris tidak inkonsisten.
        if body.source_kind == "stream":
            c.video_id = ""; c.mode = ""
        else:
            c.source_enc = ""
    if body.url is not None:
        try:
            c.source_enc = camera_store.encrypt_url(validate_stream_url(body.url))
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e))
        changes.append("url")
    if body.video_id is not None:
        if body.video_id and not db.get(VideoFile, body.video_id):
            raise HTTPException(400, "Video tidak ditemukan")
        c.video_id = body.video_id; changes.append("video")
    if body.mode is not None:
        c.mode = body.mode; changes.append("mode")
    if body.recording_start is not None:
        c.recording_start = _parse_recording_start(body.recording_start)
        changes.append("jam rekaman")
    if body.sample_rate is not None:
        c.sample_rate = _clean_sample_rate(body.sample_rate)
        changes.append("sample rate")
    if body.enabled is not None:
        c.enabled = body.enabled; changes.append("aktif" if body.enabled else "nonaktif")
    # Jaga invariant setelah merge.
    if c.source_kind == "stream" and not c.source_enc:
        raise HTTPException(400, "Kamera stream wajib punya URL")
    if c.source_kind == "video" and (not c.video_id or not db.get(VideoFile, c.video_id)):
        raise HTTPException(400, "Kamera video wajib menunjuk ke video yang ada")
    c.updated_by = user.username
    db.add(c)
    auth.audit(db, "camera_diubah", user.username, f"{cid}: {', '.join(changes)}", commit=False)
    _ensure_analysis_job(db, c, user.username)
    db.commit()
    _reload_api_live_pipeline_if_enabled(f"kamera diubah {cid}", camera_id=cid)
    return _camera_dict(c)


@app.delete("/api/cameras/{cid}", status_code=204)
def delete_camera(cid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    c = db.get(Camera, cid)
    if not c:
        raise HTTPException(404, "Kamera tidak ditemukan")
    db.delete(c)
    auth.audit(db, "camera_dihapus", user.username, cid, commit=False)
    db.commit()
    _reload_api_live_pipeline_if_enabled(f"kamera dihapus {cid}", camera_id=cid)


@app.post("/api/cameras/{cid}/test")
def test_camera(cid: str, body: CameraIn2, request: Request, user: CurrentUser = Depends(admin)):
    client = _client(request)
    if not camera_limiter.allow(client):
        raise HTTPException(429, "Terlalu sering menguji kamera. Tunggu sebentar.")
    if body.source_kind != "stream" or not body.url:
        raise HTTPException(400, "Uji koneksi hanya untuk sumber stream dengan URL")
    try:
        url = validate_stream_url(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    import cv2
    cap = cv2.VideoCapture(url)
    ok = cap.isOpened()
    if ok:
        ok, _ = cap.read()
    cap.release()
    return {"ok": bool(ok), "pesan": "Kamera bisa dibuka" if ok else "Kamera tidak merespons"}


# ---------- upload bertahap ----------

class UploadStart(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1)


@app.post("/api/uploads", status_code=201)
def start_upload(body: UploadStart, db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    if not videos.fits_on_disk(body.size):
        raise HTTPException(400, "Ukuran file melebihi batas atau sisa disk tidak cukup")
    import uuid as _uuid
    uid = _uuid.uuid4().hex
    now = Q.now_local()
    db.add(UploadSession(id=uid, label=videos.clean_label(body.name), size=body.size, received=0,
                         created_by=user.username, created_at=now, expires_at=now + timedelta(hours=24)))
    db.commit()
    videos.tmp_path(uid).write_bytes(b"")
    return {"id": uid, "offset": 0, "chunk_mb": videos.chunk_bytes() // (1024 * 1024)}


def _get_upload(uid: str, db: Session) -> UploadSession:
    """Ambil sesi upload; sesi kedaluwarsa dihapus (row + .part) dan dianggap tidak ada."""
    u = db.get(UploadSession, uid)
    if not u:
        raise HTTPException(404, "Sesi upload tidak ditemukan")
    if u.expires_at < Q.now_local():
        videos.tmp_path(uid).unlink(missing_ok=True)
        db.delete(u)
        db.commit()
        raise HTTPException(404, "Sesi upload sudah kedaluwarsa")
    return u


@app.get("/api/uploads/{uid}")
def upload_status(uid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    u = _get_upload(uid, db)
    return {"id": uid, "offset": u.received, "size": u.size}


@app.put("/api/uploads/{uid}")
async def upload_chunk(uid: str, request: Request, offset: int = Query(..., ge=0),
                       db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    u = _get_upload(uid, db)
    # Tolak potongan raksasa sebelum membaca body (lindungi memori); batas = uploads.chunk_mb.
    limit = videos.chunk_bytes()
    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > limit:
        raise HTTPException(413, "Potongan melebihi batas ukuran")
    part = videos.tmp_path(uid)
    current = part.stat().st_size if part.exists() else 0
    if offset != current:
        return JSONResponse({"offset": current}, status_code=409)
    data = await request.body()
    if len(data) > limit:
        raise HTTPException(413, "Potongan melebihi batas ukuran")
    if offset + len(data) > u.size:
        raise HTTPException(400, "Potongan melebihi ukuran yang dideklarasikan")
    with open(part, "ab") as f:
        f.write(data)
    u.received = part.stat().st_size
    db.add(u)
    db.commit()
    return {"id": uid, "offset": u.received}


@app.post("/api/uploads/{uid}/complete")
def complete_upload(uid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    u = _get_upload(uid, db)
    part = videos.tmp_path(uid)
    if not part.exists() or part.stat().st_size != u.size:
        raise HTTPException(400, "Upload belum lengkap")
    try:
        info = videos.probe_and_store(part, u.label)
    except ValueError as e:
        part.unlink(missing_ok=True)
        db.delete(u)
        db.commit()
        raise HTTPException(400, str(e))
    vf = VideoFile(id=info["id"], label=info["label"], path=info["path"], size=info["size"],
                   duration_s=info["duration_s"], fps=info["fps"], width=info["width"], height=info["height"],
                   sha256=info["sha256"], uploaded_by=user.username, created_at=Q.now_local())
    db.add(vf)
    db.delete(u)
    auth.audit(db, "video_diupload", user.username, f"{info['label']} ({info['id']})", commit=False)
    db.commit()
    return {"id": vf.id, "label": vf.label, "duration_s": vf.duration_s, "size": vf.size}


@app.delete("/api/uploads/{uid}", status_code=204)
def cancel_upload(uid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    u = db.get(UploadSession, uid)
    if u:
        videos.tmp_path(uid).unlink(missing_ok=True)
        db.delete(u)
        db.commit()


@app.get("/api/videos")
def list_videos(db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    rows = db.exec(select(VideoFile).order_by(col(VideoFile.created_at).desc())).all()
    return [{"id": v.id, "label": v.label, "size": v.size, "duration_s": v.duration_s,
             "fps": v.fps, "width": v.width, "height": v.height, "created_at": v.created_at.isoformat()}
            for v in rows]


@app.get("/api/server-videos")
def list_server_videos(db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    """File video di backend/data/videos/ yang belum terdaftar di VideoFile (disalin manual)."""
    registered = {v.path for v in db.exec(select(VideoFile)).all()}
    return [{"path": name} for name in videos.list_server_files() if name not in registered]


class RegisterVideoIn(BaseModel):
    path: str = Field(min_length=1, max_length=255)
    label: str | None = Field(None, max_length=255)


@app.post("/api/videos/register", status_code=201)
def register_video(body: RegisterVideoIn, db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    """Daftarkan video yang sudah ada di folder server (disalin manual) menjadi VideoFile."""
    try:
        info = videos.probe_server_video(body.path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if db.exec(select(VideoFile).where(VideoFile.path == info["path"])).first():
        raise HTTPException(400, "Video ini sudah terdaftar")
    if db.exec(select(VideoFile).where(VideoFile.sha256 == info["sha256"])).first():
        raise HTTPException(400, "Video dengan isi sama sudah terdaftar")
    import uuid as _uuid
    vid = _uuid.uuid4().hex
    vf = VideoFile(id=vid, label=videos.clean_label(body.label or body.path), path=info["path"],
                   size=info["size"], duration_s=info["duration_s"], fps=info["fps"],
                   width=info["width"], height=info["height"], sha256=info["sha256"],
                   uploaded_by=user.username, created_at=Q.now_local())
    db.add(vf)
    auth.audit(db, "video_didaftarkan", user.username, f"{vf.label} ({vid})", commit=False)
    db.commit()
    return {"id": vf.id, "label": vf.label, "duration_s": vf.duration_s, "size": vf.size}


# ---------- pekerjaan analisis rekaman ----------

def _job_eta_s(j: AnalysisJob, duration_s: float) -> float | None:
    """Perkiraan sisa waktu dinding (detik) untuk job berjalan.

    Laju proses = posisi video / waktu dinding sejak mulai; sisa = (durasi - posisi) / laju.
    None jika belum bisa diperkirakan (bukan berjalan, durasi/posisi/waktu belum cukup).
    """
    if j.status != "berjalan" or not j.started_at or duration_s <= 0 or j.position_s <= 0:
        return None
    elapsed = (Q.now_local() - j.started_at).total_seconds()
    if elapsed <= 0:
        return None
    rate = j.position_s / elapsed  # detik video diproses per detik dinding
    remaining_video = max(0.0, duration_s - j.position_s)
    return round(remaining_video / rate, 0) if rate > 0 else None


def _job_event_metadata(db: Session, j: AnalysisJob, duration_s: float) -> dict:
    end = j.recording_start + timedelta(seconds=duration_s) if duration_s > 0 else None
    exact_rows = db.exec(select(Violation).where(
        Violation.session == j.id,
        Violation.source_kind == "rekaman",
    ).order_by(col(Violation.started_at).asc())).all()
    query = select(Violation).where(
        Violation.camera_id == j.camera_id,
        Violation.source_kind == "rekaman",
        Violation.started_at >= j.recording_start,
    )
    if end:
        query = query.where(Violation.started_at <= end)
    rows = exact_rows or db.exec(query.order_by(col(Violation.started_at).asc())).all()
    zones = sorted({row.zone for row in rows if row.zone})
    evidence = next((row for row in rows if row.evidence_crop), None)
    return {
        "zona": ", ".join(zones),
        "thumbnail": f"/evidence/{evidence.evidence_crop}" if evidence else None,
    }


def _job_dict(j: AnalysisJob, video: VideoFile | None = None, metadata: dict | None = None) -> dict:
    duration_s = video.duration_s if video else 0.0
    metadata = metadata or {"zona": "", "thumbnail": None}
    output_video = getattr(j, "output_video", "")
    output_sha = getattr(j, "output_video_sha256", "")
    video_url = f"/evidence/{output_video}?v={output_sha}" if output_video and output_sha else (f"/evidence/{output_video}" if output_video else None)
    return {"id": j.id, "camera_id": j.camera_id, "video_id": j.video_id,
            "nama_file": video.label if video else "",
            "zona": metadata["zona"], "thumbnail": metadata["thumbnail"],
            "recording_start": j.recording_start.isoformat(), "sample_rate": j.sample_rate,
            "status": j.status, "position_s": j.position_s, "events": j.events,
            "durasi_video_s": duration_s or None, "perkiraan_sisa_s": _job_eta_s(j, duration_s),
            "video_hasil": video_url,
            "video_hasil_sha256": output_sha or None,
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None}


@app.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db), user: CurrentUser = Depends(supervisor)):
    jobs_rows = db.exec(select(AnalysisJob).order_by(AnalysisJob.id)).all()
    video_rows = {v.id: v for v in db.exec(select(VideoFile)).all()}
    return [
        _job_dict(j, video_rows.get(j.video_id), _job_event_metadata(db, j, video_rows[j.video_id].duration_s if j.video_id in video_rows else 0.0))
        for j in jobs_rows
    ]


class AnalysisJobIn(BaseModel):
    camera_id: str = Field(min_length=1, max_length=32)
    video_id: str = Field(min_length=1, max_length=64)
    recording_start: str = Field(min_length=1, max_length=40)
    sample_rate: float


@app.post("/api/jobs", status_code=201)
def create_analysis_job(body: AnalysisJobIn, db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    camera_id = _clean_camera_id(body.camera_id)
    if not db.get(Camera, camera_id):
        raise HTTPException(400, "Kamera tidak ditemukan")
    video = db.get(VideoFile, body.video_id)
    if not video:
        raise HTTPException(400, "Video tidak ditemukan")
    recording_start = _parse_recording_start(body.recording_start)
    if recording_start is None:
        raise HTTPException(400, "Jam mulai rekaman wajib diisi")
    sample_rate = _clean_sample_rate(body.sample_rate)
    existing = db.exec(select(AnalysisJob).where(
        AnalysisJob.camera_id == camera_id,
        AnalysisJob.video_id == video.id,
        col(AnalysisJob.status).in_(jobs_mod.ACTIVE_STATES),
    )).first()
    if existing:
        raise HTTPException(400, "Rekaman ini sudah berada dalam antrean untuk kamera tersebut")
    job = AnalysisJob(
        id=_uuid.uuid4().hex, camera_id=camera_id, video_id=video.id,
        recording_start=recording_start, sample_rate=sample_rate, status="menunggu",
    )
    db.add(job)
    auth.audit(db, "job_dibuat", user.username, f"{camera_id} ({video.id})", commit=False)
    db.commit()
    db.refresh(job)
    return _job_dict(job, video)


@app.post("/api/jobs/{jid}/{action}")
def control_job(jid: str, action: Literal["pause", "resume", "cancel"],
                db: Session = Depends(get_db), user: CurrentUser = Depends(admin)):
    j = db.get(AnalysisJob, jid)
    if not j:
        raise HTTPException(404, "Pekerjaan tidak ditemukan")
    target = {"pause": "dijeda", "resume": "berjalan", "cancel": "dibatalkan"}[action]
    if j.status in ("selesai", "dibatalkan"):
        raise HTTPException(400, "Pekerjaan sudah berakhir")
    j.status = target
    db.add(j)
    auth.audit(db, f"job_{action}", user.username, jid, commit=False)
    db.commit()
    vf = db.get(VideoFile, j.video_id)
    return _job_dict(j, vf, _job_event_metadata(db, j, vf.duration_s if vf else 0.0))


class ChatMessage(BaseModel):
    role: str = Field(max_length=16)
    content: str = Field(max_length=8000)
    tanda: str | None = Field(None, max_length=64)  # tanda tangan server untuk jawaban assistant


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = Field(default=None, max_length=64)


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    pinned: bool | None = None


def _conv_dict(c: Conversation) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "created": c.created.isoformat(),
        "updated": c.updated.isoformat(),
        "pinned": c.pinned,
    }


def _chat_history_query(username: str):
    """Urutkan riwayat agent stabil: timestamp pasangan user/assistant bisa sama."""
    return select(ChatHistory).where(ChatHistory.username == username).order_by(
        col(ChatHistory.at).desc(), col(ChatHistory.id).desc()
    )


def _chat_detail(row: ChatHistory) -> dict:
    if row.role != "assistant" or not row.detail_json:
        return {}
    try:
        detail = json.loads(row.detail_json)
    except json.JSONDecodeError:
        return {}
    return detail if isinstance(detail, dict) else {}


def _chat_message_dict(row: ChatHistory) -> dict:
    item = {
        "id": row.id,
        "role": row.role,
        "content": row.content,
        "at": row.at.isoformat(),
        **_chat_detail(row),
    }
    if row.tool_calls_json:
        try:
            item["tool_calls"] = json.loads(row.tool_calls_json)
        except json.JSONDecodeError:
            item["tool_calls"] = []
    if row.evidence_json:
        try:
            item["evidence"] = json.loads(row.evidence_json)
        except json.JSONDecodeError:
            item["evidence"] = []
    return item


@app.get("/api/agent/conversations")
def list_agent_conversations(q: str | None = Query(None, max_length=100),
                             limit: int = Query(50, ge=1, le=100),
                             db: Session = Depends(get_db),
                             user: CurrentUser = Depends(viewer)):
    query = select(Conversation).where(Conversation.owner == user.username)
    if q and q.strip():
        query = query.where(col(Conversation.title).ilike(f"%{q.strip()}%"))
    query = query.order_by(col(Conversation.pinned).desc(), col(Conversation.updated).desc()).limit(limit)
    rows = db.exec(query).all()
    return [_conv_dict(c) for c in rows]


@app.post("/api/agent/conversations", status_code=201)
def create_agent_conversation(body: ConversationCreate | None = None,
                              db: Session = Depends(get_db),
                              user: CurrentUser = Depends(viewer)):
    cid = _uuid.uuid4().hex
    now = Q.now_local()
    title = (body.title.strip() if body and body.title and body.title.strip() else "Percakapan baru")
    conv = Conversation(
        id=cid,
        owner=user.username,
        title=title,
        created=now,
        updated=now,
        pinned=False,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return _conv_dict(conv)


@app.get("/api/agent/conversations/{cid}")
def get_agent_conversation(cid: str,
                           db: Session = Depends(get_db),
                           user: CurrentUser = Depends(viewer)):
    conv = db.get(Conversation, cid)
    if not conv or conv.owner != user.username:
        raise HTTPException(404, "Percakapan tidak ditemukan")
    msg_rows = db.exec(
        select(ChatHistory)
        .where(ChatHistory.conversation_id == cid, ChatHistory.username == user.username)
        .order_by(col(ChatHistory.at).asc(), col(ChatHistory.id).asc())
    ).all()
    return {
        **_conv_dict(conv),
        "messages": [_chat_message_dict(r) for r in msg_rows],
    }


@app.patch("/api/agent/conversations/{cid}")
def update_agent_conversation(cid: str,
                              body: ConversationPatch,
                              db: Session = Depends(get_db),
                              user: CurrentUser = Depends(viewer)):
    conv = db.get(Conversation, cid)
    if not conv or conv.owner != user.username:
        raise HTTPException(404, "Percakapan tidak ditemukan")
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(400, "Judul percakapan tidak boleh kosong")
        conv.title = title
    if body.pinned is not None:
        conv.pinned = body.pinned
    conv.updated = Q.now_local()
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return _conv_dict(conv)


@app.delete("/api/agent/conversations/{cid}", status_code=204)
def delete_agent_conversation(cid: str,
                              db: Session = Depends(get_db),
                              user: CurrentUser = Depends(viewer)):
    conv = db.get(Conversation, cid)
    if not conv or conv.owner != user.username:
        raise HTTPException(404, "Percakapan tidak ditemukan")
    db.exec(delete(ChatHistory).where(col(ChatHistory.conversation_id) == cid))
    db.delete(conv)
    db.commit()


@app.get("/api/agent/history")
def agent_history(limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db),
                  user: CurrentUser = Depends(viewer)):
    rows = db.exec(_chat_history_query(user.username).limit(limit)).all()
    return [{"role": r.role, "content": r.content, "at": r.at.isoformat(), **_chat_detail(r)} for r in reversed(rows)]


@app.post("/api/agent/chat")
def agent_chat(body: ChatIn, request: Request, user: CurrentUser = Depends(viewer)):
    client = _client(request)
    if not agent_limiter.allow(f"{user.username}@{client}"):
        raise HTTPException(429, "Terlalu banyak pertanyaan. Tunggu sebentar lalu coba lagi.")
    conv_id = body.conversation_id
    with Session(engine) as db:
        if conv_id:
            conv = db.get(Conversation, conv_id)
            if not conv or conv.owner != user.username:
                raise HTTPException(404, "Percakapan tidak ditemukan")
            history_query = (
                select(ChatHistory)
                .where(ChatHistory.conversation_id == conv_id, ChatHistory.username == user.username)
                .order_by(col(ChatHistory.at).desc(), col(ChatHistory.id).desc())
            )
        else:
            history_query = _chat_history_query(user.username)
        rows = db.exec(history_query.limit(agent.MAX_HISTORY)).all()
        history = [{"role": r.role, "content": r.content, "tanda": r.tanda} for r in reversed(rows)]
    try:
        result = agent.chat(body.message, history, user.username)
    except Exception as e:
        # detail lengkap hanya ke log server; pesan dari SDK bisa memuat info internal
        log.error("Agent gagal (%s): %s", type(e).__name__, redact(str(e)))
        raise HTTPException(502, "Agent sedang tidak tersedia. Pastikan Ollama berjalan dan model sudah dimuat, lalu coba lagi.")
    with Session(engine) as db:
        now = Q.now_local()
        conv = None
        if conv_id:
            conv = db.get(Conversation, conv_id)
            if not conv or conv.owner != user.username:
                raise HTTPException(404, "Percakapan tidak ditemukan atau sudah dihapus")
            if conv.title == "Percakapan baru" or not conv.title.strip():
                conv.title = extract_conversation_title(body.message)
            conv.updated = now
            db.add(conv)
        detail = json.dumps({
            "alasan_jawaban": result.get("alasan_jawaban", []),
            "audit_data": result.get("audit_data", {}),
        }, ensure_ascii=False)
        tool_calls_json = json.dumps(result.get("tool_calls", []), ensure_ascii=False) if result.get("tool_calls") else ""
        evidence_json = json.dumps(result.get("evidence", []), ensure_ascii=False) if result.get("evidence") else ""
        db.add(ChatHistory(
            username=user.username,
            conversation_id=conv_id,
            role="user",
            content=body.message,
            at=now,
        ))
        db.add(ChatHistory(
            username=user.username,
            conversation_id=conv_id,
            role="assistant",
            content=result.get("answer", ""),
            tanda=result.get("tanda", ""),
            detail_json=detail,
            tool_calls_json=tool_calls_json,
            evidence_json=evidence_json,
            at=now,
        ))
        db.commit()
        if conv_id and conv:
            result["conversation_id"] = conv.id
            result["conversation_title"] = conv.title
    if result.get("ditolak"):
        # jejak percobaan di luar cakupan / upaya membobol, untuk ditinjau admin
        log.warning("Agent menolak pertanyaan (%s) dari %s", result["alasan"], client)
        with Session(engine) as db:
            auth.audit(db, "agent_ditolak", user.username, f"{result['alasan']}: {redact(body.message)}")
    return result


# ---------- login, sesi, dan akun ----------

class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, max_length=6)


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(auth.COOKIE, token, max_age=int(config.SESSION_HOURS * 3600), httponly=True,
                        samesite="strict", secure=config.COOKIE_SECURE, path="/")


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    client, name = _client(request), body.username.strip().lower()
    if auth.throttle.blocked(client, name):
        raise HTTPException(429, "Terlalu banyak percobaan login gagal. Coba lagi dalam 15 menit.")
    with Session(engine) as db:
        u = auth.authenticate(db, name, body.password)
        if not u:
            # Bebaskan transaksi baca SQLite sebelum throttle/audit membuka koneksi tulis sendiri.
            db.rollback()
            auth.throttle.fail(client, name)
            auth.audit(db, "login_gagal", name[:64], f"dari {client}")
            raise HTTPException(401, "Username atau password salah")
        username, role, totp_secret = u.username, u.role, u.totp_secret
        # Atribut user bisa memicu transaksi baca setelah commit di authenticate(); tutup sebelum throttle.reset().
        db.rollback()
        if role in ("supervisor", "admin") and totp_secret:
            if not auth.verify_totp(totp_secret, body.totp_code or ""):
                # Bebaskan transaksi baca SQLite sebelum throttle/audit membuka koneksi tulis sendiri.
                db.rollback()
                auth.throttle.fail(client, name)
                auth.audit(db, "login_gagal", name[:64], f"2FA salah dari {client}")
                raise HTTPException(401, "Kode 2FA salah")
        auth.throttle.reset(client, name)
        token = auth.create_session(db, username)
        auth.audit(db, "login", username, f"dari {client}")
    _set_cookie(response, token)
    return {"username": username, "role": role, "lokal": False}


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response):
    with Session(engine) as db:
        auth.end_session(db, request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")


@app.get("/api/auth/me")
def me(request: Request):
    user: CurrentUser | None = getattr(request.state, "user", None)
    if user is None:
        with Session(engine) as db:
            ada = auth.has_accounts(db)
        raise HTTPException(401 if ada else 403,
                            "Perlu login" if ada else "Belum ada akun. Buka dashboard dari komputer server untuk membuat admin.")
    return {"username": user.username, "role": user.role, "lokal": user.local}


class PasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


@app.post("/api/auth/password", status_code=204)
def change_password(body: PasswordIn, request: Request, user: CurrentUser = Depends(viewer)):
    if user.local:
        raise HTTPException(400, "Mode lokal tidak punya password. Buat akun dulu.")
    # Hitungan terpisah dari login (awalan "pw" pada IP dan username), supaya sesi curian tidak bisa menebak
    # password lama, dan salah ketik di sini tidak menghabiskan jatah login dari IP yang sama.
    client = _client(request)
    ip_key, key = f"pw|{client}", f"pw:{user.username}"
    if auth.throttle.blocked(ip_key, key):
        raise HTTPException(429, "Terlalu banyak percobaan. Coba lagi dalam 15 menit.")
    with Session(engine) as db:
        u = db.exec(select(User).where(User.username == user.username)).one()
        if not auth.verify_password(body.old_password, u.password_hash):
            username = u.username
            # Bebaskan transaksi baca SQLite sebelum throttle/audit membuka koneksi tulis sendiri.
            db.rollback()
            auth.throttle.fail(ip_key, key)
            auth.audit(db, "password_gagal", username, f"dari {client}")
            raise HTTPException(400, "Password lama salah")
        try:
            auth.validate_new_account(u.username, body.new_password, None)
        except ValueError as e:
            raise HTTPException(400, str(e))
        u.password_hash = auth.hash_password(body.new_password)
        db.add(u)
        auth.audit(db, "password_diganti", u.username, commit=False)
        db.commit()
        auth.revoke_sessions(db, u.username)  # semua perangkat lain ikut keluar


def _user_dict(u: User) -> dict:
    return {"username": u.username, "role": u.role, "aktif": u.active,
            "dibuat": u.created_at.isoformat(timespec="minutes"),
            "login_terakhir": u.last_login.isoformat(timespec="minutes") if u.last_login else None,
            "telegram_id": u.telegram_id or None}


@app.get("/api/auth/totp/setup")
def totp_setup(user: CurrentUser = Depends(viewer)):
    if user.role not in ("supervisor", "admin"):
        raise HTTPException(403, "Hanya untuk supervisor dan admin")
    secret = auth.generate_totp_secret()
    uri = f"otpauth://totp/PPEGuard:{user.username}?secret={secret}&issuer=PPEGuard"
    return {"secret": secret, "uri": uri}


class TotpVerifyIn(BaseModel):
    secret: str
    code: str


@app.post("/api/auth/totp/verify")
def totp_verify(body: TotpVerifyIn, user: CurrentUser = Depends(viewer)):
    if user.role not in ("supervisor", "admin"):
        raise HTTPException(403, "Hanya untuk supervisor dan admin")
    if not auth.verify_totp(body.secret, body.code):
        raise HTTPException(400, "Kode 2FA salah")
    with Session(engine) as db:
        u = db.exec(select(User).where(User.username == user.username)).first()
        if not u:
            raise HTTPException(404, "Akun tidak ditemukan")
        u.totp_secret = body.secret
        db.add(u)
        auth.audit(db, "totp_diaktifkan", u.username)
        db.commit()
    return {"ok": True}


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=256)
    role: Literal["viewer", "supervisor", "admin"] = "viewer"


class UserPatch(BaseModel):
    role: Literal["viewer", "supervisor", "admin"] | None = None
    active: bool | None = None
    password: str | None = Field(None, min_length=1, max_length=256)
    telegram_id: str | None = Field(None, max_length=20, pattern=r"^\d*$")  # "" = putuskan tautan


@app.get("/api/users")
def list_users(_: CurrentUser = Depends(admin), db: Session = Depends(get_db)):
    return [_user_dict(u) for u in db.exec(select(User).order_by(User.username)).all()]


@app.post("/api/users", status_code=201)
def create_user(body: UserIn, user: CurrentUser = Depends(admin), db: Session = Depends(get_db)):
    try:
        u = auth.create_user(db, body.username, body.password, body.role, actor=user.username)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _user_dict(u)


@app.patch("/api/users/{username}")
def update_user(username: str, body: UserPatch, user: CurrentUser = Depends(admin), db: Session = Depends(get_db)):
    u = db.exec(select(User).where(User.username == username)).first()
    if not u:
        raise HTTPException(404, "Pengguna tidak ditemukan")
    losing_admin = u.role == "admin" and u.active and (
        (body.role is not None and body.role != "admin") or body.active is False)
    if losing_admin and auth.active_admins(db) <= 1:
        raise HTTPException(400, "Tidak bisa: harus selalu ada minimal satu admin aktif")
    changes = []
    if body.role is not None and body.role != u.role:
        changes.append(f"peran {u.role}→{body.role}")
        u.role = body.role
    if body.active is not None and body.active != u.active:
        changes.append("diaktifkan" if body.active else "dinonaktifkan")
        u.active = body.active
    if body.telegram_id is not None and body.telegram_id != u.telegram_id:
        if body.telegram_id and db.exec(select(User).where(User.telegram_id == body.telegram_id,
                                                           User.username != u.username)).first():
            raise HTTPException(400, "ID Telegram ini sudah terhubung ke akun lain")
        changes.append(f"telegram {'dihubungkan' if body.telegram_id else 'diputus'}")
        u.telegram_id = body.telegram_id
    if body.password is not None:
        try:
            auth.validate_new_account(u.username, body.password, None)
        except ValueError as e:
            raise HTTPException(400, str(e))
        u.password_hash = auth.hash_password(body.password)
        changes.append("password direset")
    db.add(u)
    if changes:
        auth.audit(db, "user_diubah", user.username, f"{u.username}: {', '.join(changes)}", commit=False)
    db.commit()
    if body.active is False or body.password is not None or (body.role is not None and changes):
        auth.revoke_sessions(db, u.username)  # perubahan hak akses berlaku segera
    db.refresh(u)
    return _user_dict(u)


app.mount("/evidence", StaticFiles(directory=EVIDENCE_DIR, check_dir=False), name="evidence")

_dist = BASE_DIR.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
