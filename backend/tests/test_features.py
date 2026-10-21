"""Test fitur tambahan: klip, drift kamera, Telegram review, sumber kamera, intervensi, laporan, paparan, shift."""
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

import app.config as config_mod
from app import auth
from app import queries as Q
from app.db import (AnalysisJob, AuditLog, Camera, Intervention, QualityStat, TrackRecord, User,
                    VideoFile, Violation, engine, init_db)
from app.health import camera_health
from app.main import app, camera_limiter
from app.security import validate_stream_url
from app.telegram_bot import handle_callback
from pipeline.clips import ClipRecorder, _Pending, _effective_clip_fps, write_clip

init_db()
client = TestClient(app)


def _clear():
    with Session(engine) as db:
        for t in (Violation, TrackRecord, AuditLog, Intervention, QualityStat):
            db.exec(delete(t))
        db.commit()


# ---------- klip video ----------

def _jpeg(v: int) -> bytes:
    import cv2
    return cv2.imencode(".jpg", np.full((240, 320, 3), v, np.uint8))[1].tobytes()


def test_clip_contains_before_and_after_frames(tmp_path):
    done = []
    rec = ClipRecorder(before_s=1, after_s=1, width=320, fps=10, on_done=lambda *a: done.append(a))
    rec.set_fps(10)
    for i in range(30):
        rec.push(_jpeg(i))
    rec.start(5, tmp_path / "clip")
    for i in range(12):
        rec.push(_jpeg(100 + i))
    rec.flush()
    assert len(done) == 1
    event_id, path, sha = done[0]
    assert event_id == 5 and path.suffix == ".webm" and path.stat().st_size > 0 and len(sha) == 64
    import cv2
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or sum(1 for _ in iter(lambda: cap.read()[0], False))
    assert n >= 15  # 10 frame sebelum + 10 sesudah (toleransi codec)


def test_clip_flush_writes_partial_clip(tmp_path):
    rec = ClipRecorder(before_s=1, after_s=5, width=320, fps=10)
    rec.set_fps(10)
    rec.push(_jpeg(1))
    rec.start(1, tmp_path / "c")
    rec.push(_jpeg(2))
    rec.flush()
    assert (tmp_path / "c.webm").exists()


def test_write_clip_ignores_garbage(tmp_path):
    assert write_clip([b"bukan jpeg"], tmp_path / "x", 320, 10) is None


def test_write_clip_decodes_frames_incrementally(tmp_path, monkeypatch):
    """Klip besar tidak boleh menahan seluruh frame mentah di RAM sebelum encoding."""
    import pipeline.clips as clips_mod

    active = 0
    maximum = 0
    written = 0

    class FakeFrame:
        shape = (1080, 1920, 3)

        def __init__(self):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)

        def __del__(self):
            nonlocal active
            active -= 1

    class FakeWriter:
        def __init__(self, path, *_args):
            self.path = Path(path)

        def isOpened(self):
            return True

        def write(self, _frame):
            nonlocal written
            written += 1

        def release(self):
            self.path.write_bytes(b"video-uji")

    monkeypatch.setattr(clips_mod.cv2, "imdecode", lambda *_args: FakeFrame())
    monkeypatch.setattr(clips_mod.cv2, "resize", lambda frame, _size: frame)
    monkeypatch.setattr(clips_mod.cv2, "VideoWriter", FakeWriter)
    monkeypatch.setattr(clips_mod.cv2, "VideoWriter_fourcc", lambda *_args: 0)

    path = write_clip([b"jpeg"] * 40, tmp_path / "streaming", 640, 10)

    assert path and written == 40
    assert maximum <= 2


def test_clip_recorder_serializes_background_writers(tmp_path, monkeypatch):
    """Event serentak satu kamera tidak boleh mendekode beberapa klip sekaligus."""
    import pipeline.clips as clips_mod

    active = 0
    maximum = 0

    def fake_write(_frames, out_path, *_args):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        time.sleep(0.03)
        active -= 1
        path = out_path.with_suffix(".webm")
        path.write_bytes(b"video-uji")
        return path

    monkeypatch.setattr(clips_mod, "write_clip", fake_write)
    recorder = ClipRecorder(before_s=1, after_s=1, width=320, fps=1)
    pending = [_Pending(i, tmp_path / f"clip-{i}", [b"jpeg"], 0, 1.0) for i in range(4)]

    threads = [threading.Thread(target=recorder._write, args=(item,)) for item in pending]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert maximum == 1


def test_clip_effective_fps_does_not_speed_up_sparse_recorded_frames():
    """Klip rekaman yang framenya jarang tidak boleh diputar jauh lebih cepat dari durasi evidence."""
    assert _effective_clip_fps(frame_count=48, fps=25, target_duration_s=5.0) == pytest.approx(9.6)
    assert _effective_clip_fps(frame_count=125, fps=25, target_duration_s=5.0) == pytest.approx(25.0)


# ---------- kesehatan kamera (drift) ----------

def _stats(cam, t, sharp, conf, bright=120.0, persons=2.0):
    return QualityStat(camera_id=cam, minute=t, frames=300, persons_per_frame=persons, mean_person_conf=conf,
                       mean_ppe_conf=0.7, brightness=bright, sharpness=sharp)


def test_drift_detects_blur_and_darkness():
    _clear()
    now = datetime(2026, 10, 1, 10, 0)
    with Session(engine) as db:
        for d in range(1, 4):
            for m in range(0, 60, 5):
                db.add(_stats("CAM-X", now - timedelta(days=d) + timedelta(minutes=m - 30), 200, 0.82))
        for m in range(0, 30, 5):
            db.add(_stats("CAM-X", now - timedelta(minutes=m), 60, 0.6, bright=20))
        db.commit()
        h = camera_health(db, "CAM-X", now)
    assert h["status"] == "peringatan"
    text = " ".join(h["masalah"])
    assert "gelap" in text and "buram" in text and "Keyakinan" in text


def test_healthy_camera_and_no_data():
    _clear()
    now = datetime(2026, 10, 1, 10, 0)
    with Session(engine) as db:
        assert camera_health(db, "CAM-Y", now)["status"] == "tidak_ada_data"
        for d in range(0, 3):
            for m in range(0, 60, 5):
                db.add(_stats("CAM-Y", now - timedelta(days=d, minutes=m), 200, 0.82))
        db.commit()
        assert camera_health(db, "CAM-Y", now)["status"] == "baik"


# ---------- review lewat Telegram ----------

@pytest.fixture
def violation_id(monkeypatch):
    _clear()
    monkeypatch.setattr(config_mod, "TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setattr(config_mod, "TELEGRAM_REVIEWER_IDS", "")
    monkeypatch.setattr("app.telegram_bot.telegram_call", lambda *a, **k: None)
    with Session(engine) as db:
        # Review Telegram wajib lewat akun tertaut (supervisor+) sejak fallback dihapus.
        if not db.exec(select(User).where(User.username == "spv")).first():
            auth.create_user(db, "spv", "Rahasia#Kuat2026", "supervisor")
        u = db.exec(select(User).where(User.username == "spv")).one()
        u.telegram_id = "42"
        db.add(u)
        v = Violation(session="s", camera_id="C", track_id=1, started_at=datetime(2026, 10, 1, 8), missing_items="helmet")
        db.add(v)
        db.commit()
        vid = v.id
    yield vid
    # bersihkan akun uji supaya tidak mencemari test mode-lokal / agent
    with Session(engine) as db:
        u = db.exec(select(User).where(User.username == "spv")).first()
        if u:
            db.delete(u)
            db.commit()


def _cb(vid, choice="fp", chat="-100123", user=42, data=None):
    return {"id": "1", "data": data or f"rv:{vid}:{choice}", "from": {"id": user, "username": "spv"},
            "message": {"message_id": 9, "chat": {"id": int(chat)}, "caption": "PELANGGARAN"}}


def test_telegram_review_updates_db_and_audit(violation_id):
    assert "False positive" in handle_callback(_cb(violation_id, "fp"))
    with Session(engine) as db:
        assert db.get(Violation, violation_id).review == "false_positive"
        log = db.exec(select(AuditLog).where(AuditLog.violation_id == violation_id)).one()
        assert "via Telegram oleh spv" in log.detail


def test_telegram_rejects_other_chat_and_forged_data(violation_id, monkeypatch):
    assert "tidak berwenang" in handle_callback(_cb(violation_id, chat="-999"))
    assert "tidak dikenal" in handle_callback(_cb(violation_id, data="rv:1;DROP TABLE:fp"))
    # user tanpa akun tertaut ditolak (fallback TELEGRAM_REVIEWER_IDS sudah dihapus)
    assert "tidak terdaftar" in handle_callback(_cb(violation_id, user=999))
    with Session(engine) as db:
        assert db.get(Violation, violation_id).review == "pending"


def test_telegram_handle_message(violation_id, monkeypatch):
    from app.telegram_bot import handle_message
    assert violation_id is not None

    sent = []
    def fake_call(method, data=None, files=None, timeout=15):
        sent.append((method, data))
        return {"ok": True}

    monkeypatch.setattr("app.telegram_bot.telegram_call", fake_call)
    monkeypatch.setattr("app.config.TELEGRAM_CHAT_ID", "-100123")

    # 1. /start message
    msg_start = {"chat": {"id": -100123, "type": "supergroup"}, "from": {"id": 42}, "text": "/start"}
    handle_message(msg_start)
    assert any(m == "sendMessage" and "Halo! Saya Asisten AI K3" in p["text"] for m, p in sent)

    # 2. Regulation query message in private chat
    sent.clear()
    msg_reg = {"chat": {"id": 42, "type": "private"}, "from": {"id": 42}, "text": "mengapa wajib helm di loading dock menurut permenaker?"}
    handle_message(msg_reg)
    assert any(m == "sendChatAction" and p["action"] == "typing" for m, p in sent)
    assert any(m == "sendMessage" and "Permenaker" in p["text"] for m, p in sent)

    # 3. Message from unauthorized group is ignored
    sent.clear()
    msg_bad = {"chat": {"id": -999999, "type": "supergroup"}, "from": {"id": 42}, "text": "halo"}
    handle_message(msg_bad)
    assert len(sent) == 0


# ---------- sumber kamera ----------

@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/meta-data", "rtsp://127.0.0.1/x", "rtsp://localhost/x",
                                 "http://0.0.0.0:8000/", "file:///etc/passwd", "rtsp://a b/x", "", "javascript:alert(1)"])
def test_stream_url_rejected(url):
    with pytest.raises(ValueError):
        validate_stream_url(url)


def test_lan_camera_allowed():
    assert validate_stream_url("http://192.168.1.20:8080/video") == "http://192.168.1.20:8080/video"


def test_camera_switch_rate_limited(monkeypatch):
    camera_limiter._hits.clear()
    monkeypatch.setattr(camera_limiter, "max_calls", 1)
    assert client.post("/api/camera", json={"mode": "stream", "url": "rtsp://127.0.0.1/x"}).status_code == 400
    assert client.post("/api/camera", json={"mode": "stream", "url": "rtsp://127.0.0.1/x"}).status_code == 429
    camera_limiter._hits.clear()


# ---------- intervensi, paparan, shift, laporan ----------

def test_intervention_crud_and_compare():
    _clear()
    r = client.post("/api/interventions", json={"at": "2026-09-20T07:00", "title": "Briefing", "note": "uji"})
    assert r.status_code == 201
    iid = r.json()["id"]
    c = client.get(f"/api/interventions/{iid}/compare", params={"days": 3}).json()
    assert c["kesimpulan"] == "Data belum cukup untuk dibandingkan."
    assert client.post("/api/interventions", json={"at": "x", "title": ""}).status_code == 422
    assert client.delete(f"/api/interventions/{iid}").status_code == 204
    assert client.get(f"/api/interventions/{iid}/compare").status_code == 404


def test_two_proportion_test():
    t = Q.two_proportion_test(50, 100, 80, 100)
    assert t["signifikan"] and t["z"] > 0
    assert not Q.two_proportion_test(50, 100, 52, 100)["signifikan"]
    assert Q.two_proportion_test(0, 0, 1, 2) is None


def test_exposure_caps_unclosed_events():
    now = datetime(2026, 10, 1, 12, 0)
    closed = Violation(session="s", camera_id="C", track_id=1, started_at=now, duration_s=120, missing_items="helmet,gloves")
    stuck = Violation(session="s", camera_id="C", track_id=2, started_at=now - timedelta(days=2), missing_items="vest")
    e = Q.exposure([closed, stuck], now)
    assert e["menit_paparan"] == round((120 + Q.MAX_OPEN_EVENT_S) / 60, 1)
    per = {x["item"]: x["menit"] for x in e["menit_paparan_per_apd"]}
    assert per["helmet"] == 2.0 and per["gloves"] == 2.0 and per["vest"] == 15.0


def test_exposure_is_clipped_to_analytics_period():
    start = datetime(2026, 10, 1, 0, 0)
    end = start + timedelta(days=1)
    crossing_start = Violation(session="s", camera_id="C", track_id=1,
                               started_at=start - timedelta(minutes=5), duration_s=600,
                               missing_items="helmet")
    crossing_end = Violation(session="s", camera_id="C", track_id=2,
                             started_at=end - timedelta(minutes=5), duration_s=600,
                             missing_items="vest")

    result = Q.exposure([crossing_start, crossing_end], end, start, end)
    per = {row["item"]: row["menit"] for row in result["menit_paparan_per_apd"]}

    assert result["menit_paparan"] == 10.0
    assert per["helmet"] == 5.0
    assert per["vest"] == 5.0


def test_summary_includes_exposure_from_event_started_before_period():
    _clear()
    start = datetime(2026, 10, 1, 0, 0)
    end = start + timedelta(days=1)
    with Session(engine) as db:
        db.add(Violation(session="s", camera_id="C", track_id=1,
                         started_at=start - timedelta(minutes=5),
                         ended_at=start + timedelta(minutes=5), duration_s=600,
                         missing_items="helmet"))
        db.add(Violation(session="s", camera_id="C", track_id=2,
                         started_at=end - timedelta(minutes=5),
                         ended_at=end + timedelta(minutes=5), duration_s=600,
                         missing_items="vest"))
        db.commit()
        result = Q.summary(db, start, end)

    assert result["event_pelanggaran"] == 1
    assert result["menit_paparan"] == 10.0
    assert sum(row["menit_paparan"] for row in result["per_shift"]) == 10.0


def test_shift_wraps_midnight():
    shifts = [{"name": "Pagi", "start": "07:00", "end": "15:00"}, {"name": "Malam", "start": "23:00", "end": "07:00"}]
    assert Q.shift_of(datetime(2026, 1, 1, 6, 59), shifts) == "Malam"
    assert Q.shift_of(datetime(2026, 1, 1, 23, 30), shifts) == "Malam"
    assert Q.shift_of(datetime(2026, 1, 1, 7, 0), shifts) == "Pagi"
    assert Q.shift_of(datetime(2026, 1, 1, 16, 0), shifts) == "Di luar shift"


def test_report_escapes_html():
    _clear()
    now = Q.now_local()
    with Session(engine) as db:
        db.add(Violation(session="s", camera_id="<script>x</script>", zone="Z", track_id=1,
                         started_at=now - timedelta(seconds=1), missing_items="helmet",
                         evidence_crop='a" onerror="alert(1)'))
        db.add(Intervention(at=now - timedelta(seconds=1), title="<b>t</b>", created_at=now))
        db.commit()
    html = client.get("/api/reports/k3", params={"period": "hari_ini"}).text
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert 'onerror="alert' not in html and "&lt;b&gt;t" in html
    assert "bukan laporan audit SMK3 atau ISO 45001 resmi" in html


def test_model_quality_per_version():
    _clear()
    with Session(engine) as db:
        for i, (v, rv) in enumerate([("v1", "confirmed"), ("v1", "false_positive"), ("v2", "confirmed"), ("v2", "pending")]):
            db.add(Violation(session="s", camera_id="C", track_id=i, started_at=datetime(2026, 9, 30), missing_items="helmet",
                             review=rv, model_version=v))
        db.commit()
    q = {x["versi"]: x for x in client.get("/api/model-quality").json()}
    assert q["v1"]["precision"] == 0.5 and q["v2"]["precision"] == 1.0 and q["v2"]["cakupan_review"] == 0.5


def test_false_positive_review_stores_corrected_items_for_retraining():
    _clear()
    with Session(engine) as db:
        row = Violation(session="s", camera_id="C", track_id=1, started_at=datetime(2026, 9, 30),
                        missing_items="helmet,shoes", model_version="v3")
        db.add(row)
        db.commit()
        db.refresh(row)
        vid = row.id
    r = client.patch(f"/api/violations/{vid}/review", json={"review": "false_positive", "corrected_items": ["shoes"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review"] == "false_positive"
    assert body["apd_dibenarkan"] == ["sepatu"]
    assert body["apd_dibenarkan_kode"] == ["shoes"]
    q = {x["versi"]: x for x in client.get("/api/model-quality").json()}
    per = {x["item"]: x for x in q["v3"]["per_apd"]}
    assert per["helmet"]["false_positive"] == 0
    assert per["shoes"]["false_positive"] == 1
    with Session(engine) as db:
        log = db.exec(select(AuditLog).where(AuditLog.action == "review_false_positive")).one()
    assert "APD dibetulkan: shoes" in log.detail


def test_active_feedback_learning_suppresses_reviewed_false_positives(monkeypatch):
    """Ketika pengawas mereview pelanggaran sebagai false positive, sistem belajar dan tidak mengirim alert berulang."""
    from app.sink import DbSink
    from pipeline.state import TrackState, ActiveEvent
    from pipeline.evidence import SavedEvidence

    _clear()
    sent_alerts = []
    monkeypatch.setattr("app.alerts.send_violation_alert", lambda *a, **k: sent_alerts.append(a))

    sink = DbSink(session_id="test-session", source_kind="simulasi")

    now = datetime(2026, 10, 18, 10, 0)
    t = TrackState(track_id=7, first_seen=now, last_seen=now, last_frame=1,
                   box=(100, 100, 200, 300), conf=0.85, worker_id=7, person_key="p7:20261018")
    t.event = ActiveEvent(kind="violation", started_at=now, missing={"helmet"})

    ev = SavedEvidence(full_path="ev_full.jpg", crop_path="ev_crop.jpg", clean_path="ev_clean.jpg",
                       sha256="abc", detections_json="")

    # 1. Event pertama dibuka sebelum ada review pengawas -> alert dikirim
    vid1 = sink.on_open("CAM-01", t, ev)
    assert vid1 > 0
    assert len(sent_alerts) == 1

    # 2. Pengawas menandai event pertama sebagai FALSE POSITIVE
    with Session(engine) as db:
        v = db.get(Violation, vid1)
        assert v is not None
        v.review = "false_positive"
        v.corrected_items = "helmet"
        db.add(v)
        db.commit()

    sink._fp_cache.clear()  # reset cache untuk simulasi proses baru
    sent_alerts.clear()

    # 3. Pada frame berikutnya/loop berikutnya, pekerja yang sama (W007) terdeteksi lagi tanpa helmet
    now2 = datetime(2026, 10, 18, 10, 1)
    t2 = TrackState(track_id=7, first_seen=now2, last_seen=now2, last_frame=20,
                    box=(100, 100, 200, 300), conf=0.85, worker_id=7, person_key="p7:20261018loop")
    t2.event = ActiveEvent(kind="violation", started_at=now2, missing={"helmet"})

    vid2 = sink.on_open("CAM-01", t2, ev)
    assert vid2 > 0

    # BUKTI BELAJAR: Tidak ada alert baru yang dikirim ke pengawas!
    assert len(sent_alerts) == 0

    # Status track di memori disesuaikan menjadi compliant
    assert t2.stable_status == "compliant"
    assert t2.items["helmet"].status == "present"

    # Di database, baris baru tercatat otomatis sebagai false_positive (tidak masuk antrean review baru)
    with Session(engine) as db:
        v2 = db.get(Violation, vid2)
        assert v2 is not None
        assert v2.review == "false_positive"
        assert v2.corrected_items == "helmet"


# ---------- migrasi ringan ----------

def test_old_database_gets_new_columns(tmp_path):
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.execute("create table violation (id integer primary key, session varchar not null, camera_id varchar not null, "
              "zone varchar not null, track_id integer not null, kind varchar not null, started_at datetime not null, "
              "ended_at datetime, duration_s float, missing_items varchar not null, uncertain_items varchar not null, "
              "confidence float not null, evidence_full varchar not null, evidence_crop varchar not null, "
              "evidence_sha256 varchar not null, review varchar not null, reviewed_at datetime)")
    c.commit()
    c.close()
    code = ("from app.db import init_db, engine; from sqlalchemy import inspect; init_db(); "
            "print(sorted(c['name'] for c in inspect(engine).get_columns('violation')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parent.parent, env={"DATABASE_URL": f"sqlite:///{db}",
                                                                          "PATH": "/usr/bin:/bin"})
    assert all(name in out.stdout for name in ("model_version", "evidence_clip", "corrected_items",
                                               "worker_id", "evidence_clean", "detections_json")), out.stderr[-500:]


def test_always_dark_camera_is_info_not_alert():
    _clear()
    now = datetime(2026, 10, 1, 2, 0)
    with Session(engine) as db:
        for d in range(0, 4):
            for m in range(0, 60, 5):
                db.add(_stats("CAM-N", now - timedelta(days=d, minutes=m), 200, 0.8, bright=20))
        db.commit()
        h = camera_health(db, "CAM-N", now)
    assert h["status"] == "baik" and h["info"]


def test_comparison_divides_by_days_with_data():
    _clear()
    at = datetime(2026, 9, 27, 7, 0)
    with Session(engine) as db:
        for day in (24, 25, 26):   # data baru ada 3 hari sebelum intervensi
            for i in range(10):
                t = datetime(2026, 9, day, 9, i)
                db.add(TrackRecord(session="s", camera_id="C", track_id=day * 100 + i, first_seen=t, last_seen=t,
                                   final_status="violation", missing_items="helmet"))
                db.add(Violation(session="s", camera_id="C", track_id=day * 100 + i, started_at=t, duration_s=60,
                                 missing_items="helmet"))
        db.commit()
        c = Q.compare_intervention(db, at, 7)
    assert 3 <= c["sebelum"]["hari"] < 3.5
    assert c["sebelum"]["pelanggaran_per_hari"] > 8   # 30 pelanggaran / ~3 hari, bukan / 7 hari
    assert "hanya mencakup" in c["catatan"]


def test_no_accounts_blocks_other_hosts():
    from app import main as main_mod
    remote = TestClient(main_mod.app, client=("10.0.0.9", 5000))
    assert remote.post("/api/camera", json={"mode": "demo"}).status_code == 403
    assert remote.get("/api/stats").status_code == 403


def test_shift_rows_are_unique():
    _clear()
    t = datetime(2026, 9, 30, 9, 0)
    with Session(engine) as db:
        db.add(TrackRecord(session="s", camera_id="C", track_id=1, first_seen=t, last_seen=t, final_status="compliant"))
        db.add(Violation(session="s", camera_id="C", track_id=2, started_at=t, duration_s=30, missing_items="helmet"))
        db.commit()
        names = [x["shift"] for x in Q.summary(db, t - timedelta(hours=1), t + timedelta(hours=1))["per_shift"]]
    assert len(names) == len(set(names))


def test_retention_removes_old_files_but_keeps_pending(tmp_path, monkeypatch):
    import scripts.retention as R
    _clear()
    monkeypatch.setattr(R, "EVIDENCE_DIR", tmp_path)
    old = Q.now_local() - timedelta(days=100)
    for name in ("a_full.jpg", "a_crop.jpg", "a_clean.jpg", "b_full.jpg", "b_clean.jpg"):
        (tmp_path / name).write_bytes(b"x")
    outside = tmp_path.parent / "jangan_dihapus.txt"
    outside.write_text("x")
    with Session(engine) as db:
        db.add(Violation(session="s", camera_id="C", track_id=1, started_at=old, missing_items="helmet",
                         review="confirmed", evidence_full="a_full.jpg", evidence_crop="a_crop.jpg",
                         evidence_clean="a_clean.jpg"))
        db.add(Violation(session="s", camera_id="C", track_id=2, started_at=old, missing_items="helmet",
                         review="pending", evidence_full="b_full.jpg", evidence_clean="b_clean.jpg"))
        db.add(Violation(session="s", camera_id="C", track_id=3, started_at=old, missing_items="helmet",
                         review="confirmed", evidence_full="../jangan_dihapus.txt"))
        db.add(_stats("C", old, 200, 0.8))
        db.commit()
    assert R.run(90, 30, dry=True)["file"] == 3 and (tmp_path / "a_full.jpg").exists()
    c = R.run(90, 30, dry=False)
    assert c["file"] == 3 and c["quality"] == 1
    assert not (tmp_path / "a_full.jpg").exists() and not (tmp_path / "a_clean.jpg").exists()
    assert (tmp_path / "b_full.jpg").exists() and (tmp_path / "b_clean.jpg").exists() and outside.exists()
    with Session(engine) as db:
        assert db.exec(select(Violation).where(Violation.track_id == 1)).first().evidence_clean == ""
    with Session(engine) as db:
        log = db.exec(select(AuditLog).where(AuditLog.action == "retensi")).first()
        assert log is not None and log.actor == "sistem"


def test_retention_removes_old_videos_but_keeps_active(tmp_path, monkeypatch):
    import scripts.retention as R
    _clear()
    monkeypatch.setattr(R, "VIDEO_DIR", tmp_path)
    old = Q.now_local() - timedelta(days=30)
    for name in ("hapus.mp4", "kamera.mp4", "job.mp4", "hilang.mp4"):
        (tmp_path / name).write_bytes(b"x")
    outside = tmp_path.parent / "video_luar.mp4"
    outside.write_text("x")
    with Session(engine) as db:
        db.add(VideoFile(id="hapus", label="hapus", path="hapus.mp4", size=1, duration_s=1, fps=1,
                         width=1, height=1, sha256="a", uploaded_by="uji", created_at=old))
        db.add(VideoFile(id="kamera", label="kamera", path="kamera.mp4", size=1, duration_s=1, fps=1,
                         width=1, height=1, sha256="b", uploaded_by="uji", created_at=old))
        db.add(VideoFile(id="job", label="job", path="job.mp4", size=1, duration_s=1, fps=1,
                         width=1, height=1, sha256="c", uploaded_by="uji", created_at=old))
        db.add(VideoFile(id="luar", label="luar", path="../video_luar.mp4", size=1, duration_s=1, fps=1,
                         width=1, height=1, sha256="d", uploaded_by="uji", created_at=old))
        db.add(VideoFile(id="hilang", label="hilang", path="hilang.mp4", size=1, duration_s=1, fps=1,
                         width=1, height=1, sha256="e", uploaded_by="uji", created_at=old))
        db.add(Camera(id="CAM-RET", name="Gudang", source_kind="video", video_id="kamera",
                      created_at=Q.now_local(), updated_by="uji"))
        db.add(AnalysisJob(id="JOB-RET", camera_id="CAM-RET", video_id="job", recording_start=old,
                           sample_rate=5, status="berjalan"))
        db.commit()
    (tmp_path / "hilang.mp4").unlink()

    assert R.run(90, 30, dry=True, video_days=14)["video"] == 1
    assert (tmp_path / "hapus.mp4").exists()
    c = R.run(90, 30, dry=False, video_days=14)
    assert c["video"] == 1
    assert not (tmp_path / "hapus.mp4").exists()
    assert (tmp_path / "kamera.mp4").exists() and (tmp_path / "job.mp4").exists() and outside.exists()
    with Session(engine) as db:
        remaining = {v.id for v in db.exec(select(VideoFile)).all()}
    assert {"kamera", "job", "luar"}.issubset(remaining) and "hapus" not in remaining and "hilang" not in remaining
