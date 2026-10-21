"""Fase 6: kamera multi-sumber, upload bertahap, sumber data, dan isolasi tracker."""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

import app.config as config_mod
from app import auth, camera_store, videos as videos_mod
from app.config import BASE_DIR
from app.db import (AnalysisJob, AuditLog, Camera, SessionToken, TrackRecord, UploadSession,
                    User, VideoFile, Violation, engine, init_db)
from app.queries import now_local

PW = "rahasia-kamera-2026"


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    # Kunci Fernet uji; camera_store membaca dari config_mod.CAMERA_SECRET_KEY
    config_mod.CAMERA_SECRET_KEY = Fernet.generate_key().decode()
    camera_store.CAMERA_SECRET_KEY = config_mod.CAMERA_SECRET_KEY
    monkeypatch.setattr(videos_mod, "free_bytes", lambda: 100 * 1024 ** 3)
    init_db()
    auth.throttle.clear()
    with Session(engine) as db:
        for t in (SessionToken, User, Violation, TrackRecord, AuditLog, Camera, VideoFile, UploadSession, AnalysisJob):
            db.exec(delete(t))
        db.commit()
        for name, role in (("vina", "viewer"), ("sapto", "supervisor"), ("adi", "admin")):
            auth.create_user(db, name, PW, role)
        db.commit()
    yield
    with Session(engine) as db:
        for t in (SessionToken, User, Violation, TrackRecord, AuditLog, Camera, VideoFile, UploadSession, AnalysisJob):
            db.exec(delete(t))
        db.commit()
    # Bersihkan file video/upload dummy yang dibuat test, supaya tidak menumpuk di data/videos.
    videos_mod.ensure_dirs()
    for p in list(videos_mod.VIDEO_DIR.glob("VID-*.mp4")) + list(videos_mod.UPLOAD_TMP.glob("*.part")):
        p.unlink(missing_ok=True)
    auth.throttle.clear()


def login(name: str) -> TestClient:
    c = TestClient(app_ref())
    r = c.post("/api/auth/login", json={"username": name, "password": PW})
    assert r.status_code == 200, r.text
    return c


def app_ref():
    from app.main import app
    return app


# ---------- enkripsi & penyamaran URL ----------

def test_url_encrypted_and_masked():
    url = "rtsp://admin:Secret123@192.168.1.50:554/stream1"
    enc = camera_store.encrypt_url(url)
    assert "Secret123" not in enc
    assert camera_store.decrypt_url(enc) == url
    masked = camera_store.mask_url(url)
    assert "Secret123" not in masked and masked == "rtsp://***@192.168.1.50:554/stream1"


def test_camera_credentials_never_leak_in_response_or_audit():
    adi = login("adi")
    url = "rtsp://admin:Secret123@192.168.1.60:554/s"
    r = adi.post("/api/cameras", json={"id": "CAM-09", "name": "Gerbang", "source_kind": "stream", "url": url})
    assert r.status_code == 201, r.text
    assert "Secret123" not in r.text
    assert r.json()["sumber"] == "rtsp://***@192.168.1.60:554/s"
    # audit juga tidak membocorkan
    with Session(engine) as db:
        logs = db.exec(select(AuditLog)).all()
    assert all("Secret123" not in (l.detail or "") for l in logs)


# ---------- matriks peran ----------

def test_camera_role_matrix():
    vina, sapto, adi = login("vina"), login("sapto"), login("adi")
    assert vina.get("/api/cameras").status_code == 200       # viewer boleh lihat
    body = {"id": "CAM-02", "name": "Gudang", "source_kind": "stream", "url": "rtsp://127.0.0.1/x"}
    assert vina.post("/api/cameras", json=body).status_code == 403
    assert sapto.post("/api/cameras", json=body).status_code == 403
    # admin: 400 karena URL loopback ditolak validasi (bukan 403 peran)
    assert adi.post("/api/cameras", json=body).status_code == 400
    assert vina.get("/api/videos").status_code == 403         # videos admin saja
    assert sapto.get("/api/jobs").status_code == 200          # jobs supervisor+
    assert vina.get("/api/jobs").status_code == 403


# ---------- upload bertahap ----------

def test_upload_resume_and_reject_non_video():
    adi = login("adi")
    data = b"x" * 25
    start = adi.post("/api/uploads", json={"name": "rekaman.mp4", "size": len(data)})
    assert start.status_code == 201, start.text
    uid = start.json()["id"]

    # kirim potongan pertama (10 byte)
    r1 = adi.put(f"/api/uploads/{uid}?offset=0", content=data[:10])
    assert r1.status_code == 200 and r1.json()["offset"] == 10

    # potongan ganda / offset salah -> 409 dengan offset benar
    dup = adi.put(f"/api/uploads/{uid}?offset=0", content=data[:10])
    assert dup.status_code == 409 and dup.json()["offset"] == 10

    # lanjutkan dari offset yang benar
    r2 = adi.put(f"/api/uploads/{uid}?offset=10", content=data[10:])
    assert r2.status_code == 200 and r2.json()["offset"] == 25

    # file bukan video -> complete ditolak
    done = adi.post(f"/api/uploads/{uid}/complete")
    assert done.status_code == 400


def test_upload_rejects_oversize(monkeypatch):
    adi = login("adi")
    monkeypatch.setattr(videos_mod, "free_bytes", lambda: 1024 ** 3)  # 1 GB bebas
    # minta 100 GB -> ditolak di awal
    r = adi.post("/api/uploads", json={"name": "besar.mp4", "size": 100 * 1024 ** 3})
    assert r.status_code == 400


def test_upload_complete_persists_video_and_audit(monkeypatch):
    adi = login("adi")
    data = b"video-dummy"
    start = adi.post("/api/uploads", json={"name": "rekaman.mp4", "size": len(data)})
    uid = start.json()["id"]
    assert adi.put(f"/api/uploads/{uid}?offset=0", content=data).status_code == 200

    def fake_probe(part, label):
        assert part.exists() and label == "rekaman.mp4"
        return {"id": "VID-UP", "label": label, "path": "VID-UP.mp4", "size": len(data),
                "duration_s": 12.0, "fps": 25.0, "width": 640, "height": 360, "sha256": "h-upload"}

    monkeypatch.setattr(videos_mod, "probe_and_store", fake_probe)
    done = adi.post(f"/api/uploads/{uid}/complete")
    assert done.status_code == 200, done.text
    with Session(engine) as db:
        assert db.get(VideoFile, "VID-UP") is not None
        assert db.get(UploadSession, uid) is None
        actions = {a.action for a in db.exec(select(AuditLog)).all()}
    assert "video_diupload" in actions


def test_server_video_path_traversal_blocked():
    with pytest.raises(ValueError):
        videos_mod.resolve_server_video("../../etc/passwd")


def test_upload_chunk_rejects_oversize_chunk(monkeypatch):
    adi = login("adi")
    monkeypatch.setattr(videos_mod, "chunk_bytes", lambda: 8)  # batas potongan kecil untuk uji
    data = b"x" * 25
    uid = adi.post("/api/uploads", json={"name": "rekaman.mp4", "size": len(data)}).json()["id"]
    big = adi.put(f"/api/uploads/{uid}?offset=0", content=data[:20])  # > 8 byte
    assert big.status_code == 413
    # offset tidak maju dan file .part tidak tumbuh
    assert adi.get(f"/api/uploads/{uid}").json()["offset"] == 0


def test_expired_upload_session_rejected_and_cleaned():
    adi = login("adi")
    uid = adi.post("/api/uploads", json={"name": "rekaman.mp4", "size": 10}).json()["id"]
    part = videos_mod.tmp_path(uid)
    assert part.exists()
    with Session(engine) as db:
        u = db.get(UploadSession, uid)
        assert u is not None
        u.expires_at = now_local() - timedelta(hours=1)
        db.add(u)
        db.commit()
    assert adi.get(f"/api/uploads/{uid}").status_code == 404
    assert not part.exists()
    with Session(engine) as db:
        assert db.get(UploadSession, uid) is None


# ---------- validasi kamera ----------

def test_camera_validation_rejects_bad_fields():
    adi = login("adi")
    # id tidak valid (spasi)
    bad_id = adi.post("/api/cameras", json={"id": "CAM 01", "name": "X", "source_kind": "stream",
                                            "url": "http://192.168.1.9/s"})
    assert bad_id.status_code == 400
    # sample_rate selain 5/10
    bad_rate = adi.post("/api/cameras", json={"id": "CAM-R", "name": "X", "source_kind": "stream",
                                              "url": "http://192.168.1.9/s", "sample_rate": 7})
    assert bad_rate.status_code == 400
    # recording_start tidak valid -> 400, bukan 500
    bad_start = adi.post("/api/cameras", json={"id": "CAM-S", "name": "X", "source_kind": "stream",
                                               "url": "http://192.168.1.9/s", "recording_start": "bukan-tanggal"})
    assert bad_start.status_code == 400


def test_camera_patch_keeps_source_consistent():
    adi = login("adi")
    r = adi.post("/api/cameras", json={"id": "CAM-P", "name": "X", "source_kind": "stream",
                                       "url": "http://192.168.1.9/s"})
    assert r.status_code == 201, r.text
    # ganti ke video tanpa video_id valid -> ditolak, baris tidak rusak
    bad = adi.patch("/api/cameras/CAM-P", json={"source_kind": "video"})
    assert bad.status_code == 400
    with Session(engine) as db:
        c = db.get(Camera, "CAM-P")
        assert c is not None
        assert c.source_kind == "stream" and c.source_enc


# ---------- job analisis otomatis ----------

def _make_video_file(db: Session, vid="VID-1") -> str:
    """Buat baris VideoFile + file dummy di VIDEO_DIR untuk uji (tanpa butuh video asli)."""
    videos_mod.ensure_dirs()
    (videos_mod.VIDEO_DIR / f"{vid}.mp4").write_bytes(b"x")
    db.add(VideoFile(id=vid, label="uji", path=f"{vid}.mp4", size=1, duration_s=1, fps=25,
                     width=10, height=10, sha256="h" + vid, uploaded_by="uji", created_at=now_local()))
    db.commit()
    return vid


def test_analysis_camera_creates_job_automatically():
    adi = login("adi")
    with Session(engine) as db:
        _make_video_file(db, "VID-JOB")
    r = adi.post("/api/cameras", json={"id": "CAM-AN", "name": "Rekaman", "source_kind": "video",
                                       "video_id": "VID-JOB", "mode": "analisis",
                                       "recording_start": "2026-09-30T08:00", "sample_rate": 5})
    assert r.status_code == 201, r.text
    jobs_list = adi.get("/api/jobs").json()
    assert any(j["camera_id"] == "CAM-AN" and j["status"] == "menunggu" for j in jobs_list)
    # audit mencatat job_dibuat
    with Session(engine) as db:
        actions = {a.action for a in db.exec(select(AuditLog)).all()}
    assert "job_dibuat" in actions


def test_live_pipeline_reloaded_when_camera_changes(monkeypatch):
    import app.main as main_mod
    adi = login("adi")
    with Session(engine) as db:
        _make_video_file(db, "VID-RELOAD")

    calls: list[str] = []
    monkeypatch.setattr(main_mod.config, "RUN_PIPELINE", True)
    monkeypatch.setattr(main_mod, "_reconcile_live_camera", lambda cid: calls.append(cid))

    created = adi.post("/api/cameras", json={"id": "CAM-RLD", "name": "Simulasi", "source_kind": "video",
                                             "video_id": "VID-RELOAD", "mode": "simulasi"})
    assert created.status_code == 201, created.text
    assert calls[-1] == "CAM-RLD"

    updated = adi.patch("/api/cameras/CAM-RLD", json={"enabled": False})
    assert updated.status_code == 200, updated.text
    assert calls[-1] == "CAM-RLD"

    deleted = adi.delete("/api/cameras/CAM-RLD")
    assert deleted.status_code == 204, deleted.text
    assert calls[-1] == "CAM-RLD"


def test_jobs_response_includes_processed_video_url():
    adi = login("adi")
    started = now_local()
    with Session(engine) as db:
        _make_video_file(db, "VID-OUT")
        db.add(AnalysisJob(id="JOB-OUT", camera_id="CAM-OUT", video_id="VID-OUT",
                           recording_start=started, sample_rate=5, status="selesai",
                           output_video="processed/JOB-OUT.webm", output_video_sha256="abc123"))
        db.add(Violation(session="JOB-OUT", camera_id="CAM-OUT", source_kind="rekaman", zone="Gudang",
                         track_id=1, started_at=started, evidence_crop="2026/event_crop.jpg"))
        db.commit()

    rows = adi.get("/api/jobs")
    assert rows.status_code == 200, rows.text
    job = next(j for j in rows.json() if j["id"] == "JOB-OUT")
    assert job["video_hasil"] == "/evidence/processed/JOB-OUT.webm?v=abc123"
    assert job["video_hasil_sha256"] == "abc123"
    assert job["nama_file"] == "uji"
    assert job["zona"] == "Gudang"
    assert job["thumbnail"] == "/evidence/2026/event_crop.jpg"


def test_create_analysis_job_role_validation_and_audit():
    vina, sapto, adi = login("vina"), login("sapto"), login("adi")
    with Session(engine) as db:
        _make_video_file(db, "VID-DIRECT")
        db.add(Camera(id="CAM-DIRECT", name="Gudang", source_kind="stream", enabled=True,
                      created_at=now_local(), updated_by="uji"))
        db.commit()
    body = {
        "camera_id": "CAM-DIRECT", "video_id": "VID-DIRECT",
        "recording_start": "2026-10-15T08:30", "sample_rate": 5,
    }
    assert vina.post("/api/jobs", json=body).status_code == 403
    assert sapto.post("/api/jobs", json=body).status_code == 403
    assert adi.post("/api/jobs", json={**body, "video_id": "tidak-ada"}).status_code == 400
    assert adi.post("/api/jobs", json={**body, "sample_rate": 7}).status_code == 400

    created = adi.post("/api/jobs", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["camera_id"] == "CAM-DIRECT"
    assert created.json()["nama_file"] == "uji"
    assert created.json()["status"] == "menunggu"
    assert adi.post("/api/jobs", json=body).status_code == 400
    with Session(engine) as db:
        job = db.get(AnalysisJob, created.json()["id"])
        log = db.exec(select(AuditLog).where(AuditLog.action == "job_dibuat",
                                             AuditLog.detail.contains("VID-DIRECT"))).one()
    assert job is not None
    assert log.actor == "adi"


def test_job_control_role_status_and_audit():
    sapto, adi = login("sapto"), login("adi")
    with Session(engine) as db:
        _make_video_file(db, "VID-CTRL")
        db.add(AnalysisJob(id="JOB-CTRL", camera_id="CAM-J", video_id="VID-CTRL",
                           recording_start=now_local(), sample_rate=5, status="berjalan"))
        db.commit()

    assert sapto.post("/api/jobs/JOB-CTRL/pause").status_code == 403
    paused = adi.post("/api/jobs/JOB-CTRL/pause")
    assert paused.status_code == 200 and paused.json()["status"] == "dijeda"
    resumed = adi.post("/api/jobs/JOB-CTRL/resume")
    assert resumed.status_code == 200 and resumed.json()["status"] == "berjalan"
    cancelled = adi.post("/api/jobs/JOB-CTRL/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "dibatalkan"
    assert adi.post("/api/jobs/JOB-CTRL/pause").status_code == 400
    with Session(engine) as db:
        actions = {a.action for a in db.exec(select(AuditLog)).all()}
    assert {"job_pause", "job_resume", "job_cancel"} <= actions


# ---------- registrasi video folder server ----------

def test_register_server_video_rejects_traversal_and_missing():
    adi = login("adi")
    assert adi.post("/api/videos/register", json={"path": "../rahasia.mp4"}).status_code == 400
    assert adi.post("/api/videos/register", json={"path": "tidak-ada.mp4"}).status_code == 400


def test_server_videos_role_matrix():
    vina, adi = login("vina"), login("adi")
    assert vina.get("/api/server-videos").status_code == 403
    assert adi.get("/api/server-videos").status_code == 200


def test_camera_runtime_maps_sources():
    from app import camera_runtime
    stream = Camera(id="CAM-S", name="Stream", source_kind="stream", source_enc=camera_store.encrypt_url("http://192.168.1.9/s"),
                    created_at=now_local(), updated_by="uji")
    sim = Camera(id="CAM-V", name="Sim", source_kind="video", video_id="VID-V", mode="simulasi",
                 created_at=now_local(), updated_by="uji")
    rec = Camera(id="CAM-R", name="Rec", source_kind="video", video_id="VID-V", mode="analisis",
                 created_at=now_local(), updated_by="uji")
    video = VideoFile(id="VID-V", label="v", path="VID-V.mp4", size=1, duration_s=1, fps=25,
                      width=10, height=10, sha256="h", uploaded_by="uji", created_at=now_local())
    videos_mod.ensure_dirs()
    (videos_mod.VIDEO_DIR / "VID-V.mp4").write_bytes(b"x")

    assert camera_runtime.source_kind_for(stream) == "live"
    assert camera_runtime.source_kind_for(sim) == "simulasi"
    assert camera_runtime.source_kind_for(rec) == "rekaman"
    assert camera_runtime.resolve_source(stream, None) == "http://192.168.1.9/s"
    sim_cfg = camera_runtime.live_camera_config(sim, video)
    assert sim_cfg["camera"]["source"].endswith("VID-V.mp4")
    assert sim_cfg["camera"]["loop"] is True and sim_cfg["camera"]["realtime"] is True


# ---------- zona per kamera ----------

def test_zone_snapshot_is_per_camera():
    from app import zones_store
    from app.db import ZoneRule
    from app.queries import now_local
    import json as _json
    poly = _json.dumps([[0, 0], [1, 0], [1, 1]])
    with Session(engine) as db:
        for z in db.exec(select(ZoneRule)).all():
            db.delete(z)
        db.add(ZoneRule(name="Global", camera_id="", polygon_json=poly, required_csv="helmet",
                        updated_at=now_local(), updated_by="uji"))
        db.add(ZoneRule(name="Khusus", camera_id="CAM-Z", polygon_json=poly, required_csv="vest",
                        updated_at=now_local(), updated_by="uji"))
        db.commit()
    zones_store.bump()
    names_default = {z.name for z in zones_store.snapshot("")}
    names_camz = {z.name for z in zones_store.snapshot("CAM-Z")}
    assert names_default == {"Global"}
    assert names_camz == {"Global", "Khusus"}


# ---------- sumber data (source_kind) ----------

def test_simulation_excluded_from_default_stats():
    from app.queries import now_local
    today8 = now_local() - timedelta(seconds=1)
    with Session(engine) as db:
        db.add(Violation(session="s-live", camera_id="CAM-01", track_id=1, started_at=today8,
                         ended_at=today8 + timedelta(seconds=30), missing_items="helmet", source_kind="live"))
        db.add(Violation(session="s-sim", camera_id="CAM-01", track_id=1, started_at=today8,
                         ended_at=today8 + timedelta(seconds=30), missing_items="helmet", source_kind="simulasi"))
        db.add(Violation(session="s-rec", camera_id="CAM-01", track_id=1, started_at=today8,
                         ended_at=today8 + timedelta(seconds=30), missing_items="helmet", source_kind="rekaman"))
        db.add(Violation(session="s-old", camera_id="CAM-01", track_id=1, started_at=today8 - timedelta(days=1),
                         ended_at=today8 - timedelta(days=1, seconds=-30), missing_items="helmet", source_kind="live"))
        db.commit()
    adi = login("adi")
    nyata = adi.get("/api/violations", params={"period": "hari_ini", "source": "nyata"}).json()
    assert nyata["total"] == 2  # live + rekaman, tanpa simulasi
    semua = adi.get("/api/violations", params={"period": "hari_ini", "source": "semua"}).json()
    assert semua["total"] == 3
    live_only = adi.get("/api/violations", params={"period": "hari_ini", "source": "live"}).json()
    assert live_only["total"] == 1


# ---------- penjadwal multi-kamera ----------

class _FakeReader:
    def __init__(self, frames, running=True):
        self._frames = list(frames)
        self.running = running
        self.fps = 10.0
        self.source = "x"
    def read(self):
        return self._frames.pop(0) if self._frames else None


class _RecordingSession:
    """CameraSession tiruan: mencatat frame yang diproses lewat scheduler."""
    def __init__(self, cid, frames, running=True):
        self.camera_id = cid
        self.reader = _FakeReader(frames, running)
        self.processed = []
        self.stats = {"processed_frames": 0, "batch_times": []}
    def needs_ppe(self):
        return True
    def process(self, frame, pose_res, ppe_res, t_batch):
        del pose_res, ppe_res, t_batch
        self.processed.append(int(frame[0, 0, 0]))


class _CountingDetector:
    def __init__(self):
        self.batch_sizes = []
    def batch_predict(self, frames, run_ppe):
        del run_ppe
        self.batch_sizes.append(len(frames))
        return [None] * len(frames), [[] for _ in frames]


def test_scheduler_feeds_every_camera_each_batch():
    from pipeline.scheduler import InferenceScheduler
    import time
    f1 = [np.full((4, 4, 3), 11, np.uint8)]
    f2 = [np.full((4, 4, 3), 22, np.uint8)]
    c1 = _RecordingSession("CAM-01", f1)
    c2 = _RecordingSession("CAM-02", f2)
    det = _CountingDetector()
    sch = InferenceScheduler(det, [c1, c2])
    sch.start()
    for _ in range(50):
        if c1.processed and c2.processed:
            break
        time.sleep(0.05)
    sch.stop()
    # kedua kamera diproses dalam batch yang sama (tidak ada yang kelaparan)
    assert c1.processed == [11] and c2.processed == [22]
    assert 2 in det.batch_sizes


def test_scheduler_skips_disconnected_camera_but_keeps_others():
    from pipeline.scheduler import InferenceScheduler
    import time
    good = _RecordingSession("CAM-01", [np.full((4, 4, 3), 33, np.uint8)])
    down = _RecordingSession("CAM-02", [], running=False)  # kamera putus
    det = _CountingDetector()
    sch = InferenceScheduler(det, [good, down])
    sch.start()
    for _ in range(50):
        if good.processed:
            break
        time.sleep(0.05)
    sch.stop()
    assert good.processed == [33]
    assert down.processed == []  # kamera putus tidak menghentikan yang lain


def test_scheduler_remove_waits_for_in_flight_camera_without_stalling_peers():
    import threading
    import time
    from pipeline.scheduler import InferenceScheduler

    processing = threading.Event()
    release = threading.Event()
    old = _RecordingSession("CAM-OLD", [np.full((4, 4, 3), 11, np.uint8)])
    peer = _RecordingSession("CAM-PEER", [np.full((4, 4, 3), 22, np.uint8)])
    original_process = old.process

    def slow_process(frame, pose_res, ppe_res, t_batch):
        processing.set()
        assert release.wait(3)
        original_process(frame, pose_res, ppe_res, t_batch)

    old.process = slow_process
    scheduler = InferenceScheduler(_CountingDetector(), [old, peer])
    scheduler.start()
    try:
        assert processing.wait(3)
        removed = []
        removal = threading.Thread(target=lambda: removed.append(scheduler.remove_camera("CAM-OLD")))
        removal.start()
        assert not removed  # sesi lama belum boleh dihentikan saat process() masih berlangsung
        release.set()
        removal.join(3)
        assert not removal.is_alive()
        assert removed == [old]
        assert peer.processed == [22]
        replacement = _RecordingSession("CAM-OLD", [np.full((4, 4, 3), 33, np.uint8)])
        scheduler.add_camera(replacement)
        for _ in range(60):
            if replacement.processed:
                break
            time.sleep(0.02)
        assert replacement.processed == [33]
        assert old.processed == [11]
    finally:
        release.set()
        scheduler.stop()
        scheduler.thread.join(3)


def test_scheduler_process_failure_does_not_skip_other_camera():
    import time
    from pipeline.scheduler import InferenceScheduler

    bad = _RecordingSession("CAM-BAD", [np.full((4, 4, 3), 11, np.uint8)])
    good = _RecordingSession("CAM-GOOD", [np.full((4, 4, 3), 22, np.uint8)])
    def fail_process(frame, pose_res, ppe_res, t_batch):
        raise RuntimeError("kamera rusak")

    bad.process = fail_process
    scheduler = InferenceScheduler(_CountingDetector(), [bad, good])
    scheduler.start()
    try:
        for _ in range(60):
            if good.processed:
                break
            time.sleep(0.02)
        assert good.processed == [22]
        assert scheduler.thread.is_alive()
    finally:
        scheduler.stop()
        scheduler.thread.join(3)


def test_camera_speeds_uses_real_metrics_and_hysteresis():
    import app.main as main_mod
    main_mod._rate_states.clear()
    cid = "CAM-SLOW"
    with Session(engine) as db:
        db.add(Camera(id=cid, name="Slow", source_kind="video", video_id="data/videos/11.mp4", enabled=True,
                      created_at=now_local(), updated_by="uji"))
        db.commit()
    live = main_mod.get_remote_live(cid)
    for rate in (4.9, 4.8):
        live.tmp_dir.mkdir(parents=True, exist_ok=True)
        (live.tmp_dir / f"{cid}.json").write_text(
            '{"fps": 20, "running": true, "pemeriksaan_per_detik": %s, "waktu_batch_ms": 12.3, "persen_frame_dilewati": 7.5}' % rate
        )
        row = main_mod._camera_speeds()[0]
        assert row["di_bawah_batas"] is False
        assert row["waktu_batch_ms"] == 12.3 and row["persen_frame_dilewati"] == 7.5
    (live.tmp_dir / f"{cid}.json").write_text(
        '{"fps": 20, "running": true, "pemeriksaan_per_detik": 4.7, "waktu_batch_ms": 12.3, "persen_frame_dilewati": 7.5}'
    )
    assert main_mod._camera_speeds()[0]["di_bawah_batas"] is True
    for _ in range(2):
        (live.tmp_dir / f"{cid}.json").write_text(
            '{"fps": 20, "running": true, "pemeriksaan_per_detik": 6.1, "waktu_batch_ms": 12.3, "persen_frame_dilewati": 7.5}'
        )
        row = main_mod._camera_speeds()[0]
    assert row["di_bawah_batas"] is False


def test_tracker_ids_isolated_per_camera():
    from pipeline.runner import CameraSession, LiveState
    import copy
    from app.config import load_config
    cfg = copy.deepcopy(load_config())

    class Sink:
        def on_open(self, camera_id, track, ev):
            del camera_id, track, ev
            return 0
        def on_close(self, event_id, event, ended_at):
            del event_id, event, ended_at
        def on_track_lost(self, camera_id, track):
            del camera_id, track
        def on_clip(self, event_id, rel_path, sha256):
            del event_id, rel_path, sha256
        def on_quality(self, camera_id, stats):
            del camera_id, stats

    sink = Sink()
    a = CameraSession("CAM-01", cfg, sink, LiveState(), base_dir=BASE_DIR)
    b = CameraSession("CAM-02", cfg, sink, LiveState(), base_dir=BASE_DIR)
    # tiap sesi punya instance tracker sendiri (ID tidak dibagi antarkamera)
    assert a.tracker is not b.tracker
