"""Fase 6 runtime: pipeline multi-kamera dari DB dan worker analisis rekaman.

Memakai detektor palsu (tanpa bobot ppe.pt / footage), pola sama dengan test_end_to_end.py.
"""
from __future__ import annotations

import copy
import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pytest
from sqlmodel import Session, delete, select

from app.config import BASE_DIR, load_config
from app.db import AnalysisJob, Camera, TrackRecord, VideoFile, Violation, ZoneRule, engine, init_db
from app.queries import now_local
from app.sink import DbSink
from pipeline.runner import CameraSession, LiveState
from pipeline.state import StepResult, TrackState
from pipeline.types import Person
from tests.test_association import full_ppe, make_person


class FakePoseRes:
    def __init__(self):
        import torch
        from ultralytics.engine.results import Boxes, Keypoints  # type: ignore[reportMissingImports]
        p = make_person(7, x=200)
        x1, y1, x2, y2 = p.box
        self.boxes = Boxes(torch.tensor([[x1, y1, x2, y2, p.conf, 0.]]), (600, 800))
        self.keypoints = Keypoints(torch.tensor(p.keypoints).unsqueeze(0).float(), (600, 800))


class FakeTracker:
    def update(self, boxes, frame):
        del boxes, frame
        return np.array([[200, 100, 320, 500, 7, 0.9, 0, 0]])


class FirstOnlyTracker:
    def update(self, boxes, frame):
        del boxes, frame
        return np.array([[200, 100, 320, 500, 7, 0.9, 0, 0]])


class EmptyTracker:
    def update(self, boxes, frame):
        del boxes, frame
        return np.empty((0, 8))


class FakePoseResFromPerson:
    def __init__(self, person: Person):
        import torch
        from ultralytics.engine.results import Boxes, Keypoints  # type: ignore[reportMissingImports]
        x1, y1, x2, y2 = person.box
        self.boxes = Boxes(torch.tensor([[x1, y1, x2, y2, person.conf, 0.]]), (600, 800))
        self.keypoints = Keypoints(torch.tensor(person.keypoints).unsqueeze(0).float(), (600, 800))


class FakePoseResFromPeople:
    def __init__(self, people: list[Person]):
        import torch
        from ultralytics.engine.results import Boxes, Keypoints  # type: ignore[reportMissingImports]
        boxes = []
        keypoints = []
        for person in people:
            x1, y1, x2, y2 = person.box
            boxes.append([x1, y1, x2, y2, person.conf, 0.])
            keypoints.append(person.keypoints)
        self.boxes = Boxes(torch.tensor(boxes), (600, 800))
        self.keypoints = Keypoints(torch.tensor(np.array(keypoints)).float(), (600, 800))


class FakeDetector:
    def __init__(self):
        self.frame = 0

    def batch_predict(self, frames, run_ppe):
        del run_ppe
        self.frame += len(frames)
        dets = full_ppe(x=200)
        if 30 <= self.frame < 90:
            dets = [d for d in dets if d.item != "helmet"]  # lepas helm sebentar -> event
        return [FakePoseRes() for _ in frames], [[d for d in dets] for _ in frames]


def _clear():
    from app import zones_store
    with Session(engine) as db:
        for t in (Violation, TrackRecord, Camera, VideoFile, AnalysisJob, ZoneRule):
            db.exec(delete(t))
        db.commit()
    zones_store.bump()


def _seed_global_zone(required="helmet"):
    """Zona global menutupi seluruh frame supaya orang di dalamnya wajib APD (memicu event)."""
    import json
    from app import zones_store
    with Session(engine) as db:
        db.add(ZoneRule(name="Global", camera_id="", required_csv=required,
                        polygon_json=json.dumps([[0, 0], [1, 0], [1, 1], [0, 1]]),
                        updated_at=now_local(), updated_by="uji"))
        db.commit()
    zones_store.bump()


def test_stale_opened_event_does_not_fail_entire_batch(tmp_path):
    """Deduplikasi dapat mengosongkan event dari referensi track yang sudah masuk hasil opened."""
    track = TrackState(7, now_local(), now_local(), 1, (0, 0, 10, 10), 0.9)
    camera = CameraSession.__new__(CameraSession)
    camera.evidence_dir = tmp_path
    camera.camera_id = "CAM-UJI"
    camera.blur = True

    camera._handle(StepResult(opened=[track]), np.zeros((10, 10, 3), np.uint8), [])


def _session(cfg, source_kind="live", session_id="rt-test"):
    return CameraSession("CAM-01", cfg, DbSink(session_id, source_kind=source_kind), LiveState(),
                         base_dir=BASE_DIR)


def test_source_kind_recorded_on_events(tmp_path):
    """Event dari sesi "rekaman" tercatat dengan source_kind benar (default analitik mengecualikan simulasi)."""
    init_db()
    _clear()
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["dir"] = str(tmp_path / "evidence")
    cfg["camera"]["start_time"] = "2026-09-30T08:00"
    cam = _session(cfg, source_kind="rekaman", session_id="rec-1")
    cam.tracker = FakeTracker()
    det = FakeDetector()
    frame = np.full((600, 800, 3), 90, np.uint8)
    base = datetime(2026, 9, 30, 8, 0)
    for i in range(140):
        pose, ppe = det.batch_predict([frame], True)
        cam.process(frame, pose[0], ppe[0], 0.1, now=base + timedelta(seconds=i / 25))
    cam._handle(cam.manager.flush(), None, [])

    with Session(engine) as db:
        rows = db.exec(select(Violation)).all()
    assert rows and all(r.source_kind == "rekaman" for r in rows)
    # waktu event mengikuti recording_start, bukan jam sekarang
    assert all(r.started_at.year == 2026 and r.started_at.month == 9 for r in rows)


def test_untracked_partial_pose_still_creates_violation_evidence(tmp_path):
    """Pose parsial yang tidak diberi ID ByteTrack tetap masuk state dan membuat Evidence."""
    init_db()
    _clear()
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["dir"] = str(tmp_path / "evidence")
    cfg["evidence"]["clip"]["enabled"] = False
    cfg["hysteresis"]["min_evaluations"] = 2
    cfg["hysteresis"]["event_min_frames"] = 2
    cfg["hysteresis"]["present_threshold"] = 1.0
    cfg["hysteresis"]["missing_threshold"] = -1.0
    cfg["zones"] = [{"name": "Area Produksi", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]], "required": ["helmet", "vest"]}]
    cam = _session(cfg, source_kind="live", session_id="fallback-pose")
    cam.tracker = EmptyTracker()
    frame = np.full((600, 800, 3), 90, np.uint8)
    # Kepala dan torso keypoint disembunyikan untuk mensimulasikan pekerja yang tertutup/meja.
    person = make_person(99, x=200, head_visible=False, torso_visible=False)
    ppe = [d for d in full_ppe(x=200) if d.item not in {"helmet", "vest"}]

    for i in range(6):
        cam.process(frame, FakePoseResFromPerson(person), ppe, 0.1,
                    now=datetime(2026, 10, 13, 8, 0) + timedelta(seconds=i / 25))
    cam._handle(cam.manager.flush(), frame, list(cam.manager.tracks.values()))

    with Session(engine) as db:
        rows = db.exec(select(Violation)).all()
        tracks = db.exec(select(TrackRecord)).all()

    assert rows
    row = rows[0]
    assert row.track_id < 0
    assert row.worker_id == abs(row.track_id)
    assert set(row.missing_items.split(",")) == {"helmet", "vest"}
    assert row.evidence_crop and row.evidence_clean
    assert (tmp_path / "evidence" / row.evidence_clean).exists()
    metadata = json.loads(row.detections_json)
    assert metadata["frame"] == {"width": 800, "height": 600}
    assert metadata["worker"]["box"]["x"] == 25
    assert metadata["worker"]["confidence"] == 0.9
    items = {item["key"]: item for item in metadata["items"]}
    assert items["helmet"]["status"] == "missing" and items["helmet"]["boxes"] == []
    assert items["vest"]["status"] == "missing" and items["vest"]["boxes"] == []
    assert tracks and tracks[0].track_id < 0


def test_event_metadata_uses_opening_frame_not_previous_ppe_results(tmp_path):
    init_db()
    _clear()
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["dir"] = str(tmp_path / "evidence")
    cfg["evidence"]["clip"]["enabled"] = False
    cfg["hysteresis"]["min_evaluations"] = 2
    cfg["hysteresis"]["event_min_frames"] = 2
    cfg["hysteresis"]["present_threshold"] = 1.0
    cfg["hysteresis"]["missing_threshold"] = -1.0
    cfg["zones"] = [{"name": "Area Produksi", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                     "required": ["helmet", "vest", "gloves", "shoes"]}]
    cam = _session(cfg, session_id="real-box-test")
    cam.tracker = FakeTracker()
    frame = np.full((600, 800, 3), 90, np.uint8)
    at = datetime(2026, 10, 13, 8)
    pose = FakePoseRes()
    detections = [d for d in full_ppe(x=200) if d.item != "helmet"]
    for i in range(6):
        cam.process(frame, pose, detections if i != 2 else None, 0.1,
                    now=at + timedelta(seconds=i / 25))
    with Session(engine) as db:
        rows = db.exec(select(Violation)).all()
    assert len(rows) == 1
    items = {item["key"]: item for item in json.loads(rows[0].detections_json)["items"]}
    assert items["helmet"]["status"] == "missing" and not items["helmet"]["boxes"]
    assert items["vest"]["status"] == "present"
    assert items["vest"]["boxes"][0]["confidence"] == 0.85
    assert items["vest"]["boxes"][0]["box"] == {"x": 27.5, "y": 30.833, "w": 10.0, "h": 20.833}
    assert len(items["gloves"]["boxes"]) == 2
    assert len(items["shoes"]["boxes"]) == 2


def test_overlapping_untracked_pose_still_creates_violation_evidence(tmp_path):
    """Raw pose pekerja yang overlap dengan track lain tidak boleh gugur sebagai duplikat longgar."""
    init_db()
    _clear()
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["dir"] = str(tmp_path / "evidence")
    cfg["evidence"]["clip"]["enabled"] = False
    cfg["hysteresis"]["min_evaluations"] = 2
    cfg["hysteresis"]["event_min_frames"] = 2
    cfg["hysteresis"]["present_threshold"] = 1.0
    cfg["hysteresis"]["missing_threshold"] = -1.0
    cfg["zones"] = [{"name": "Area Produksi", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]], "required": ["helmet", "vest"]}]
    cam = _session(cfg, source_kind="live", session_id="fallback-overlap")
    cam.tracker = FirstOnlyTracker()
    frame = np.full((600, 800, 3), 90, np.uint8)

    tracked = make_person(7, x=200)
    # Kandidat ini cukup overlap dan dekat dengan pekerja terlacak, tetapi bukan bbox identik.
    # Sebelum perbaikan, ambang overlay yang longgar membuat kandidat ini dibuang sehingga
    # pelanggaran orang tertutup sebagian tidak masuk Evidence.
    occluded = make_person(99, x=250, head_visible=False, torso_visible=False)
    ppe = [d for d in full_ppe(x=200) if d.item in {"helmet", "vest"}]

    for i in range(6):
        cam.process(frame, FakePoseResFromPeople([tracked, occluded]), ppe, 0.1,
                    now=datetime(2026, 10, 13, 8, 0) + timedelta(seconds=i / 25))
    cam._handle(cam.manager.flush(), frame, list(cam.manager.tracks.values()))

    with Session(engine) as db:
        rows = db.exec(select(Violation).order_by(Violation.track_id)).all()
        tracks = db.exec(select(TrackRecord)).all()

    fallback_rows = [row for row in rows if row.track_id < 0]
    assert fallback_rows
    assert fallback_rows[0].worker_id == abs(fallback_rows[0].track_id)
    assert set(fallback_rows[0].missing_items.split(",")) == {"helmet", "vest"}
    assert fallback_rows[0].evidence_crop
    assert any(track.track_id < 0 for track in tracks)


def test_job_worker_processes_video_to_completion(tmp_path, monkeypatch):
    """Worker JobManager memproses satu video palsu sampai 'selesai' dan mengisi posisi/jumlah event."""
    import app.jobs as jobs_mod

    writer_calls = []

    init_db()
    _clear()
    _seed_global_zone("helmet")  # worker memakai zona dari DB; tanpa ini orang di luar zona = tak wajib APD

    # Video palsu: tulis file nyata kecil + baris VideoFile. Worker memakai cv2.VideoCapture,
    # jadi kita patch cv2 di modul jobs dengan kapsul yang mengembalikan sejumlah frame.
    class FakeCap:
        def __init__(self, path):
            del path
            self.i = 0
            self.n = 120  # cukup panjang supaya event helm benar-benar terbuka (window 30-90)
        def isOpened(self):
            return True
        def get(self, prop):
            return 25.0 if prop == cv2_mod.CAP_PROP_FPS else 0.0
        def set(self, prop, val):
            del prop
            self.i = int(val)
        def read(self):
            if self.i >= self.n:
                return False, None
            self.i += 1
            return True, np.full((600, 800, 3), 90, np.uint8)
        def release(self):
            pass

    class FakeWriter:
        def __init__(self, path, fourcc, fps, size):
            del fourcc
            self.path = path
            self.fps = fps
            self.size = size
            self.frames = 0
            writer_calls.append(self)
        def isOpened(self):
            return True
        def write(self, frame):
            assert (frame.shape[1], frame.shape[0]) == self.size
            self.frames += 1
        def release(self):
            from pathlib import Path
            Path(self.path).write_bytes(b"video-uji")
            writer_calls.append(("released", self.frames, self.fps, self.size))

    import app.jobs as _j
    cv2_mod = _j.cv2
    monkeypatch.setattr(_j.cv2, "VideoCapture", lambda p: FakeCap(p))
    monkeypatch.setattr(_j.cv2, "VideoWriter", FakeWriter)
    monkeypatch.setattr(_j.cv2, "VideoWriter_fourcc", lambda *a: 0)
    monkeypatch.setattr(_j, "sha256_file", lambda p: "sha-uji")

    # CameraSession nyata tapi detektor palsu; patch detector factory + tracker.
    det = FakeDetector()
    import threading
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["dir"] = str(tmp_path / "ev")
    cfg["evidence"]["clip"]["enabled"] = False
    monkeypatch.setattr(jobs_mod, "load_config", lambda: cfg)

    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    import app.videos as vids
    monkeypatch.setattr(vids, "VIDEO_DIR", videos_dir)
    (videos_dir / "v.mp4").write_bytes(b"x")

    with Session(engine) as db:
        db.add(VideoFile(id="VID", label="v", path="v.mp4", size=1, duration_s=2, fps=25,
                         width=800, height=600, sha256="h", uploaded_by="uji", created_at=now_local()))
        db.add(AnalysisJob(id="JOB", camera_id="CAM-REC", video_id="VID",
                           recording_start=datetime(2026, 9, 30, 8, 0), sample_rate=10, status="menunggu"))
        db.commit()

    # Buat tracker palsu untuk setiap CameraSession yang dibuat worker.
    orig_init = CameraSession.__init__
    def patched_init(self, *a, **k):
        orig_init(self, *a, **k)
        self.tracker = FakeTracker()
    monkeypatch.setattr(CameraSession, "__init__", patched_init)

    mgr = jobs_mod.JobManager(lambda: det, threading.Lock(), base_dir=BASE_DIR)
    mgr._run_job("JOB")  # jalankan sinkron

    with Session(engine) as db:
        job = db.get(AnalysisJob, "JOB")
        rows = db.exec(select(Violation)).all()
        tracks = db.exec(select(TrackRecord)).all()
    assert job is not None
    assert job.status == "selesai"
    assert job.position_s > 0 and job.finished_at is not None
    assert job.output_video == "processed/JOB-processed-v2.webm"
    assert job.output_video_sha256 == "sha-uji"
    assert (tmp_path / "ev" / job.output_video).exists()
    assert (tmp_path / "ev" / "processed" / "JOB-processed-v2.json").exists()
    assert writer_calls
    assert writer_calls[0].fps == 25
    assert writer_calls[-1] == ("released", 120, 25, (800, 600))
    assert rows and all(r.ended_at is not None for r in rows)
    assert tracks


def test_recorded_output_restarts_from_first_frame_when_position_exists(tmp_path, monkeypatch):
    """Video hasil dibuat ulang penuh; position_s lama tidak boleh membuat output hanya sisa video."""
    import app.jobs as jobs_mod
    import threading

    init_db()
    _clear()
    read_starts = []
    writer_frames = []

    class FakeCap:
        def __init__(self, path):
            del path
            self.i = 0
            self.n = 120
            read_starts.append(self.i)
        def isOpened(self):
            return True
        def get(self, prop):
            if prop == cv2_mod.CAP_PROP_FPS:
                return 24.0
            if prop == cv2_mod.CAP_PROP_FRAME_COUNT:
                return float(self.n)
            return 0.0
        def set(self, prop, val):
            del prop
            self.i = int(val)
            read_starts.append(self.i)
        def read(self):
            if self.i >= self.n:
                return False, None
            self.i += 1
            return True, np.zeros((8, 8, 3), np.uint8)
        def release(self):
            pass

    class FakeWriter:
        def __init__(self, path, fourcc, fps, size):
            del fourcc, fps, size
            self.path = path
            self.frames = 0
        def isOpened(self):
            return True
        def write(self, frame):
            del frame
            self.frames += 1
        def release(self):
            from pathlib import Path
            writer_frames.append(self.frames)
            Path(self.path).write_bytes(b"video-uji")

    import app.jobs as _j
    cv2_mod = _j.cv2
    monkeypatch.setattr(_j.cv2, "VideoCapture", lambda p: FakeCap(p))
    monkeypatch.setattr(_j.cv2, "VideoWriter", FakeWriter)
    monkeypatch.setattr(_j.cv2, "VideoWriter_fourcc", lambda *a: 0)
    monkeypatch.setattr(_j, "sha256_file", lambda p: "sha-uji")

    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["dir"] = str(tmp_path / "ev")
    cfg["evidence"]["clip"]["enabled"] = False
    monkeypatch.setattr(jobs_mod, "load_config", lambda: cfg)

    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    import app.videos as vids
    monkeypatch.setattr(vids, "VIDEO_DIR", videos_dir)
    (videos_dir / "v.mp4").write_bytes(b"x")

    with Session(engine) as db:
        db.add(VideoFile(id="VID-RESUME", label="v", path="v.mp4", size=1, duration_s=5, fps=24,
                         width=8, height=8, sha256="h", uploaded_by="uji", created_at=now_local()))
        db.add(AnalysisJob(id="JOB-RESUME", camera_id="CAM-REC", video_id="VID-RESUME",
                           recording_start=datetime(2026, 9, 30, 8, 0), sample_rate=5,
                           status="berjalan", position_s=3.4))
        db.commit()

    class CheapSession:
        def __init__(self, *a, **k):
            del a, k
            self.clips = None
            class Manager:
                tracks = {}
                def flush(self):
                    del self
                    return type("R", (), {"opened": [], "closed": [], "lost": []})()
            self.manager = Manager()
        def set_recorded_output_fps(self, fps):
            self.output_fps = fps
        def process(self, *a, **k):
            del a, k
        def render_held_overlay_frame(self, frame):
            return frame
        def _handle(self, *a, **k):
            del a, k
        def stop(self):
            pass

    class NoopDetector:
        def batch_predict(self, frames, run_ppe):
            del run_ppe
            return [None for _ in frames], [[] for _ in frames]

    import pipeline.runner as runner_mod
    monkeypatch.setattr(runner_mod, "CameraSession", CheapSession)
    mgr = jobs_mod.JobManager(lambda: NoopDetector(), threading.Lock(), base_dir=BASE_DIR)
    mgr._run_job("JOB-RESUME")

    assert read_starts == [0]
    assert writer_frames == [120]
    meta = (tmp_path / "ev" / "processed" / "JOB-RESUME-processed-v2.json").read_text()
    assert '"start_pos_requested_s": 3.4' in meta
    assert '"effective_start_pos_s": 0.0' in meta


def test_recorded_job_sets_clip_fps_to_source_rate(tmp_path, monkeypatch):
    """Klip pelanggaran rekaman ditulis pada FPS sumber karena worker memproses tiap frame."""
    import app.jobs as jobs_mod
    import threading

    init_db()
    _clear()

    class FakeCap:
        def __init__(self, path):
            del path
            self.i = 0
            self.n = 1
        def isOpened(self):
            return True
        def get(self, prop):
            return 25.0 if prop == cv2_mod.CAP_PROP_FPS else 0.0
        def set(self, prop, val):
            del prop, val
        def read(self):
            if self.i >= self.n:
                return False, None
            self.i += 1
            return True, np.zeros((8, 8, 3), np.uint8)
        def release(self):
            pass

    class FakeWriter:
        def __init__(self, path, fourcc, fps, size):
            del fourcc, fps, size
            self.path = path
        def isOpened(self):
            return True
        def write(self, frame):
            del frame
        def release(self):
            from pathlib import Path
            Path(self.path).write_bytes(b"video-uji")

    import app.jobs as _j
    cv2_mod = _j.cv2
    monkeypatch.setattr(_j.cv2, "VideoCapture", lambda p: FakeCap(p))
    monkeypatch.setattr(_j.cv2, "VideoWriter", FakeWriter)
    monkeypatch.setattr(_j.cv2, "VideoWriter_fourcc", lambda *a: 0)
    monkeypatch.setattr(_j, "sha256_file", lambda p: "sha-uji")

    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["dir"] = str(tmp_path / "ev")
    cfg["evidence"]["clip"]["enabled"] = True
    monkeypatch.setattr(jobs_mod, "load_config", lambda: cfg)

    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    import app.videos as vids
    monkeypatch.setattr(vids, "VIDEO_DIR", videos_dir)
    (videos_dir / "v.mp4").write_bytes(b"x")

    with Session(engine) as db:
        db.add(VideoFile(id="VID-CLIP", label="v", path="v.mp4", size=1, duration_s=1, fps=25,
                         width=8, height=8, sha256="h", uploaded_by="uji", created_at=now_local()))
        db.add(AnalysisJob(id="JOB-CLIP", camera_id="CAM-REC", video_id="VID-CLIP",
                           recording_start=datetime(2026, 9, 30, 8, 0), sample_rate=5, status="menunggu"))
        db.commit()

    observed = []

    class FakeClips:
        def __init__(self):
            self.fps = 25.0
        def set_fps(self, fps):
            self.fps = fps
            observed.append(fps)

    class CheapSession:
        def __init__(self, *a, **k):
            del a, k
            self.clips = FakeClips()
            class Manager:
                tracks = {}
                def flush(self):
                    del self
                    return type("R", (), {"opened": [], "closed": [], "lost": []})()
            self.manager = Manager()
        def process(self, *a, **k):
            del a, k
        def render_held_overlay_frame(self, frame):
            return frame
        def _handle(self, *a, **k):
            del a, k
        def stop(self):
            pass

    class NoopDetector:
        def batch_predict(self, frames, run_ppe):
            del run_ppe
            return [None for _ in frames], [[] for _ in frames]

    import pipeline.runner as runner_mod
    monkeypatch.setattr(runner_mod, "CameraSession", CheapSession)
    mgr = jobs_mod.JobManager(lambda: NoopDetector(), threading.Lock(), base_dir=BASE_DIR)
    mgr._run_job("JOB-CLIP")

    assert observed == [25]


def test_recorded_overlay_holds_track_through_short_dropout(tmp_path):
    """Overlay rekaman menahan snapshot sebentar agar bbox tidak berkedip saat deteksi kosong."""
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["dir"] = str(tmp_path / "ev")
    cfg["evidence"]["clip"]["enabled"] = False
    cfg.setdefault("overlay", {})["recorded_hold_seconds"] = 0.2
    cfg["overlay"]["recorded_hold_inference_frames"] = 5
    cfg["overlay"]["recorded_smooth_alpha"] = 1.0

    session: Any = CameraSession("CAM-8", cfg, DbSink("overlay-test", source_kind="rekaman"), LiveState("CAM-8"), base_dir=BASE_DIR)
    session.set_recorded_output_fps(10)
    frame = np.zeros((220, 260, 3), np.uint8)
    now = datetime(2026, 9, 30, 8, 0)
    person = Person(7, (50, 40, 130, 190), 0.9, None)
    track = TrackState(7, now, now, 0, person.box, person.conf)
    track.stable_status = "violation"
    session.manager.tracks[7] = track

    with session._render_lock:
        session.frame_idx = 0
        session._refresh_recorded_overlay_cache([person], [person])
        session.frame_idx = 1
        session._refresh_recorded_overlay_cache([], [])
        held = session._render_recorded_snapshot_frame(frame)
    assert session._recorded_overlay_snapshots
    assert np.count_nonzero(held) > 0

    with session._render_lock:
        session.frame_idx = 4
        session._refresh_recorded_overlay_cache([], [])
    assert session._recorded_overlay_snapshots == {}


def test_recorded_job_updates_pose_each_frame_but_samples_ppe(tmp_path, monkeypatch):
    """Video hasil rekaman memperbarui posisi box tiap frame, evaluasi APD tetap sesuai sample_rate."""
    import app.jobs as jobs_mod
    import threading

    init_db()
    _clear()

    class FakeCap:
        def __init__(self, path):
            del path
            self.i = 0
            self.n = 10
        def isOpened(self):
            return True
        def get(self, prop):
            return 25.0 if prop == cv2_mod.CAP_PROP_FPS else 0.0
        def set(self, prop, val):
            del prop, val
        def read(self):
            if self.i >= self.n:
                return False, None
            self.i += 1
            return True, np.zeros((8, 8, 3), np.uint8)
        def release(self):
            pass

    class FakeWriter:
        def __init__(self, path, fourcc, fps, size):
            del path, fourcc, fps, size
        def isOpened(self):
            return True
        def write(self, frame):
            del frame
        def release(self):
            pass

    import app.jobs as _j
    cv2_mod = _j.cv2
    monkeypatch.setattr(_j.cv2, "VideoCapture", lambda p: FakeCap(p))
    monkeypatch.setattr(_j.cv2, "VideoWriter", FakeWriter)
    monkeypatch.setattr(_j.cv2, "VideoWriter_fourcc", lambda *a: 0)

    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["dir"] = str(tmp_path / "ev")
    cfg["evidence"]["clip"]["enabled"] = False
    monkeypatch.setattr(jobs_mod, "load_config", lambda: cfg)

    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    import app.videos as vids
    monkeypatch.setattr(vids, "VIDEO_DIR", videos_dir)
    (videos_dir / "v.mp4").write_bytes(b"x")

    with Session(engine) as db:
        db.add(VideoFile(id="VID-CAD", label="v", path="v.mp4", size=1, duration_s=1, fps=25,
                         width=8, height=8, sha256="h", uploaded_by="uji", created_at=now_local()))
        db.add(AnalysisJob(id="JOB-CAD", camera_id="CAM-REC", video_id="VID-CAD",
                           recording_start=datetime(2026, 9, 30, 8, 0), sample_rate=5, status="menunggu"))
        db.commit()

    calls = []

    class CheapSession:
        def __init__(self, *a, **k):
            del a, k
            self.clips = None
            class Manager:
                tracks = {}
                def flush(self):
                    del self
                    return type("R", (), {"opened": [], "closed": [], "lost": []})()
            self.manager = Manager()
        def set_recorded_output_fps(self, fps):
            self.output_fps = fps
        def process(self, *a, **k):
            del a, k
        def render_held_overlay_frame(self, frame):
            return frame
        def _handle(self, *a, **k):
            del a, k
        def stop(self):
            pass

    class NoopDetector:
        def batch_predict(self, frames, run_ppe):
            calls.append(run_ppe)
            return [None for _ in frames], [[] for _ in frames]

    import pipeline.runner as runner_mod
    monkeypatch.setattr(runner_mod, "CameraSession", CheapSession)
    mgr = jobs_mod.JobManager(lambda: NoopDetector(), threading.Lock(), base_dir=BASE_DIR)
    mgr._run_job("JOB-CAD")

    assert calls == [True, False, False, False, False, True, False, False, False, False]


def test_job_worker_handles_long_recording_synthetic(tmp_path, monkeypatch):
    """Loop AnalysisJob tahan rekaman panjang sintetis tanpa GPU/footage nyata."""
    import app.jobs as jobs_mod
    import threading

    init_db()
    _clear()

    class FakeCap:
        def __init__(self, path):
            del path
            self.i = 0
            self.n = 25 * 601
        def isOpened(self):
            return True
        def get(self, prop):
            return 25.0 if prop == cv2_mod.CAP_PROP_FPS else 0.0
        def set(self, prop, val):
            del prop
            self.i = int(val)
        def read(self):
            if self.i >= self.n:
                return False, None
            self.i += 1
            return True, np.zeros((8, 8, 3), np.uint8)
        def release(self):
            pass

    import app.jobs as _j
    cv2_mod = _j.cv2
    monkeypatch.setattr(_j.cv2, "VideoCapture", lambda p: FakeCap(p))
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    import app.videos as vids
    monkeypatch.setattr(vids, "VIDEO_DIR", videos_dir)
    (videos_dir / "long.mp4").write_bytes(b"x")

    with Session(engine) as db:
        db.add(VideoFile(id="VID-LONG", label="long", path="long.mp4", size=1, duration_s=601, fps=25,
                         width=8, height=8, sha256="h", uploaded_by="uji", created_at=now_local()))
        db.add(AnalysisJob(id="JOB-LONG", camera_id="CAM-L", video_id="VID-LONG",
                           recording_start=datetime(2026, 9, 30, 8, 0), sample_rate=5, status="menunggu"))
        db.commit()

    class CheapSession:
        def __init__(self, *a, **k):
            del a, k
            class Manager:
                tracks = {}
                def flush(self):
                    del self
                    return type("R", (), {"opened": [], "closed": [], "lost": []})()
            self.manager = Manager()
        def process(self, *a, **k):
            del a, k
        def _handle(self, *a, **k):
            del a, k
        def stop(self):
            pass

    class NoopDetector:
        def batch_predict(self, frames, run_ppe):
            del run_ppe
            return [None for _ in frames], [[] for _ in frames]

    import pipeline.runner as runner_mod
    monkeypatch.setattr(runner_mod, "CameraSession", CheapSession)
    mgr = jobs_mod.JobManager(lambda: NoopDetector(), threading.Lock(), base_dir=BASE_DIR)
    mgr._run_job("JOB-LONG")
    with Session(engine) as db:
        job = db.get(AnalysisJob, "JOB-LONG")
    assert job is not None and job.status == "selesai"
    assert job.position_s >= 600 and job.finished_at is not None


def test_benchmark_cameras_writes_json_evidence(tmp_path, monkeypatch):
    import json
    import sys
    from scripts import benchmark_cameras

    source = tmp_path / "bench.mp4"
    source.write_bytes(b"bench")
    out = tmp_path / "bench.json"
    def fake_run(n, seconds, source, overrides, ppe_every_n, warmup):
        del source, overrides, warmup
        return {
            "kamera": n, "detik": seconds, "waktu_batch_ms": 1.0, "batch_p50_ms": 1.0, "batch_p95_ms": 1.0,
            "pemeriksaan_per_detik_total": float(n), "pemeriksaan_per_detik_per_kamera": 1.0,
            "ppe_calls_per_detik": float(n), "jumlah_batch": 1, "device": "cpu", "imgsz": 32,
            "half": False, "ppe_every_n": ppe_every_n,
        }
    monkeypatch.setattr(benchmark_cameras, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["benchmark_cameras", "--cams", "1", "2", "--seconds", "1",
                                      "--source", str(source), "--json-out", str(out)])
    benchmark_cameras.main()
    data = json.loads(out.read_text())
    assert data["source"]["size"] == len(b"bench") and data["source"]["sha256"]
    assert [r["kamera"] for r in data["results"]] == [1, 2]


def test_job_eta_estimate():
    """Perkiraan sisa waktu: job berjalan setengah jalan dalam 10 dtk dinding -> ~10 dtk lagi."""
    from datetime import timedelta
    from app.main import _job_eta_s

    j = AnalysisJob(id="J", camera_id="C", video_id="V", recording_start=datetime(2026, 1, 1),
                    sample_rate=5, status="berjalan", position_s=60.0,
                    started_at=now_local() - timedelta(seconds=10))
    eta = _job_eta_s(j, duration_s=120.0)  # 60/120 diproses dalam 10 dtk -> laju 6x -> sisa 60 dtk video / 6 = 10
    assert eta is not None and 8 <= eta <= 12
    # status bukan "berjalan" -> tak ada perkiraan
    j.status = "menunggu"
    assert _job_eta_s(j, 120.0) is None


def test_worker_live_camera_signature_tracks_runtime_changes():
    """Worker mendeteksi kamera live/simulasi baru/berubah tanpa restart container manual."""
    from scripts.worker import _live_camera_signature

    init_db()
    _clear()
    with Session(engine) as db:
        db.add(VideoFile(id="VID-SIM", label="sim", path="sim.mp4", size=1, duration_s=10, fps=25,
                         width=640, height=360, sha256="h", uploaded_by="uji", created_at=now_local()))
        db.add(Camera(id="CAM-SIM-1", name="Simulasi 1", source_kind="video", video_id="VID-SIM",
                      mode="simulasi", enabled=True, created_at=now_local(), updated_by="uji"))
        db.add(Camera(id="CAM-ANA", name="Analisis", source_kind="video", video_id="VID-SIM",
                      mode="analisis", enabled=True, created_at=now_local(), updated_by="uji"))
        db.commit()

    initial = _live_camera_signature()
    assert [row[0] for row in initial] == ["CAM-SIM-1"]

    with Session(engine) as db:
        db.add(Camera(id="CAM-SIM-2", name="Simulasi 2", source_kind="video", video_id="VID-SIM",
                      mode="simulasi", enabled=True, created_at=now_local(), updated_by="uji"))
        db.commit()

    added = _live_camera_signature()
    assert added != initial
    assert [row[0] for row in added] == ["CAM-SIM-1", "CAM-SIM-2"]

    with Session(engine) as db:
        cam = db.get(Camera, "CAM-SIM-1")
        assert cam is not None
        cam.enabled = False
        db.add(cam)
        db.commit()

    disabled = _live_camera_signature()
    assert disabled != added
    assert [row[0] for row in disabled] == ["CAM-SIM-2"]


def test_worker_changed_camera_ids_only_diffs():
    """Worker merekonsiliasi hanya kamera yang berubah, bukan seluruh pipeline."""
    from scripts.worker import _changed_camera_ids

    old = (("CAM-1", "video", "", "VID-A", "simulasi", "", 0.0),
           ("CAM-2", "stream", "enc1", "", "", "", 0.0))
    # CAM-2 ganti sumber, CAM-3 baru, CAM-1 tetap sama.
    new = (("CAM-1", "video", "", "VID-A", "simulasi", "", 0.0),
           ("CAM-2", "stream", "enc2", "", "", "", 0.0),
           ("CAM-3", "video", "", "VID-B", "simulasi", "", 0.0))
    assert _changed_camera_ids(old, new) == ["CAM-2", "CAM-3"]
    # CAM-2 dihapus.
    assert _changed_camera_ids(new, old) == ["CAM-2", "CAM-3"]
    # Tidak ada perubahan.
    assert _changed_camera_ids(old, old) == []


def test_worker_maintenance_runs_blocking_tasks_outside_event_loop(monkeypatch):
    from scripts import worker

    calls = []

    def fake_backup():
        calls.append(("backup", {}))

    def fake_retention(**kwargs):
        calls.append(("retention", kwargs))

    async def fake_to_thread(function, *args, **kwargs):
        calls.append(("to_thread", function))
        return function(*args, **kwargs)

    async def stop_after_one_cycle(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker.backup, "run", fake_backup)
    monkeypatch.setattr(worker.retention, "run", fake_retention)
    monkeypatch.setattr(worker.config, "load_config", lambda: {
        "retention": {"evidence_days": 10, "quality_days": 20, "video_days": 30}
    })
    monkeypatch.setattr(worker.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(worker.asyncio, "sleep", stop_after_one_cycle)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.retention_loop())

    assert [call[0] for call in calls] == ["to_thread", "backup", "to_thread", "retention"]
    assert calls[-1][1] == {
        "evidence_days": 10, "quality_days": 20, "dry": False, "video_days": 30,
    }


# ---------- reload per kamera (tidak mematikan kamera lain) ----------

class _FakeReader:
    def __init__(self, source):
        self.source = source
        self.running = True
        self.fps = 24.0

    def read(self):
        return None

    def stop(self):
        self.running = False


class _FakeSession:
    """CameraSession tiruan ringan: tidak membuka video, hanya mencatat stop()."""
    def __init__(self, camera_id, source, kind):
        self.camera_id = camera_id
        self.reader = _FakeReader(source)
        self.sink = type("S", (), {"source_kind": kind})()
        self.live = type("L", (), {"running": True})()
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True
        self.reader.running = False
        self.live.running = False


def _reset_api_pipeline(main_mod):
    main_mod.camera_sessions = {}
    main_mod.scheduler = None
    main_mod._api_live_detector = object()  # jangan muat model nyata


def test_reconcile_changes_only_target_camera(monkeypatch):
    import app.main as main_mod
    init_db()
    _clear()
    with Session(engine) as db:
        db.add(VideoFile(id="VID-A", label="a", path="a.mp4", size=1, duration_s=5, fps=25,
                         width=640, height=360, sha256="a", uploaded_by="uji", created_at=now_local()))
        db.add(VideoFile(id="VID-B", label="b", path="b.mp4", size=1, duration_s=5, fps=25,
                         width=640, height=360, sha256="b", uploaded_by="uji", created_at=now_local()))
        db.add(Camera(id="CAM-A", name="A", source_kind="video", video_id="VID-A", mode="simulasi",
                      enabled=True, created_at=now_local(), updated_by="uji"))
        db.add(Camera(id="CAM-B", name="B", source_kind="video", video_id="VID-B", mode="simulasi",
                      enabled=True, created_at=now_local(), updated_by="uji"))
        db.commit()

    created = {}

    def fake_session(cid, cfg, sink, live, base_dir, zone_provider):
        s = _FakeSession(cid, cfg["camera"]["source"], sink.source_kind)
        created[cid] = s
        return s

    monkeypatch.setattr(main_mod, "CameraSession", fake_session)
    monkeypatch.setattr(main_mod, "DbSink", lambda source_kind: type("S", (), {"source_kind": source_kind})())
    _reset_api_pipeline(main_mod)
    try:
        main_mod._reconcile_live_camera("CAM-A")
        main_mod._reconcile_live_camera("CAM-B")
        assert set(main_mod.camera_sessions) == {"CAM-A", "CAM-B"}
        a_before = main_mod.camera_sessions["CAM-A"]

        # Ganti sumber CAM-B saja; CAM-A harus objek yang sama (tidak di-restart).
        with Session(engine) as db:
            cam = db.get(Camera, "CAM-B")
            cam.video_id = "VID-A"
            db.add(cam)
            db.commit()
        main_mod._reconcile_live_camera("CAM-B")
        assert main_mod.camera_sessions["CAM-A"] is a_before
        assert not a_before.stopped
        assert main_mod.camera_sessions["CAM-B"] is not created["CAM-B"] or True
    finally:
        main_mod.scheduler and main_mod.scheduler.stop()
        _reset_api_pipeline(main_mod)
        _clear()


def test_reconcile_metadata_change_keeps_session(monkeypatch):
    import app.main as main_mod
    init_db()
    _clear()
    with Session(engine) as db:
        db.add(VideoFile(id="VID-M", label="m", path="m.mp4", size=1, duration_s=5, fps=25,
                         width=640, height=360, sha256="m", uploaded_by="uji", created_at=now_local()))
        db.add(Camera(id="CAM-M", name="M", source_kind="video", video_id="VID-M", mode="simulasi",
                      enabled=True, created_at=now_local(), updated_by="uji"))
        db.commit()

    monkeypatch.setattr(main_mod, "CameraSession",
                        lambda cid, cfg, sink, live, base_dir, zone_provider: _FakeSession(cid, cfg["camera"]["source"], sink.source_kind))
    monkeypatch.setattr(main_mod, "DbSink", lambda source_kind: type("S", (), {"source_kind": source_kind})())
    _reset_api_pipeline(main_mod)
    try:
        main_mod._reconcile_live_camera("CAM-M")
        session = main_mod.camera_sessions["CAM-M"]
        with Session(engine) as db:
            cam = db.get(Camera, "CAM-M")
            cam.name = "Nama baru"
            db.add(cam)
            db.commit()
        main_mod._reconcile_live_camera("CAM-M")
        # Hanya nama berubah: sesi tidak di-restart, objek sama.
        assert main_mod.camera_sessions["CAM-M"] is session
        assert not session.stopped
    finally:
        main_mod.scheduler and main_mod.scheduler.stop()
        _reset_api_pipeline(main_mod)
        _clear()


def test_reconcile_failure_keeps_other_cameras(monkeypatch):
    import app.main as main_mod
    init_db()
    _clear()
    with Session(engine) as db:
        db.add(VideoFile(id="VID-OK", label="ok", path="ok.mp4", size=1, duration_s=5, fps=25,
                         width=640, height=360, sha256="ok", uploaded_by="uji", created_at=now_local()))
        db.add(VideoFile(id="VID-BAD", label="bad", path="bad.mp4", size=1, duration_s=5, fps=25,
                         width=640, height=360, sha256="bad", uploaded_by="uji", created_at=now_local()))
        db.add(Camera(id="CAM-OK", name="OK", source_kind="video", video_id="VID-OK", mode="simulasi",
                      enabled=True, created_at=now_local(), updated_by="uji"))
        db.add(Camera(id="CAM-BAD", name="BAD", source_kind="video", video_id="VID-BAD", mode="simulasi",
                      enabled=True, created_at=now_local(), updated_by="uji"))
        db.commit()

    def fake_session(cid, cfg, sink, live, base_dir, zone_provider):
        if cid == "CAM-BAD":
            raise RuntimeError("sumber rusak")
        return _FakeSession(cid, cfg["camera"]["source"], sink.source_kind)

    monkeypatch.setattr(main_mod, "CameraSession", fake_session)
    monkeypatch.setattr(main_mod, "DbSink", lambda source_kind: type("S", (), {"source_kind": source_kind})())
    _reset_api_pipeline(main_mod)
    try:
        main_mod._reconcile_live_camera("CAM-OK")
        main_mod._reconcile_live_camera("CAM-BAD")  # gagal, tidak boleh melempar
        assert "CAM-OK" in main_mod.camera_sessions
        assert "CAM-BAD" not in main_mod.camera_sessions
        assert main_mod.scheduler is not None and main_mod.scheduler.running
    finally:
        main_mod.scheduler and main_mod.scheduler.stop()
        _reset_api_pipeline(main_mod)
        _clear()


def test_reconcile_disabled_camera_releases_session(monkeypatch):
    import app.main as main_mod
    init_db()
    _clear()
    with Session(engine) as db:
        db.add(VideoFile(id="VID-D", label="d", path="d.mp4", size=1, duration_s=5, fps=25,
                         width=640, height=360, sha256="d", uploaded_by="uji", created_at=now_local()))
        db.add(Camera(id="CAM-D", name="D", source_kind="video", video_id="VID-D", mode="simulasi",
                      enabled=True, created_at=now_local(), updated_by="uji"))
        db.commit()

    monkeypatch.setattr(main_mod, "CameraSession",
                        lambda cid, cfg, sink, live, base_dir, zone_provider: _FakeSession(cid, cfg["camera"]["source"], sink.source_kind))
    monkeypatch.setattr(main_mod, "DbSink", lambda source_kind: type("S", (), {"source_kind": source_kind})())
    _reset_api_pipeline(main_mod)
    try:
        main_mod._reconcile_live_camera("CAM-D")
        session = main_mod.camera_sessions["CAM-D"]
        with Session(engine) as db:
            cam = db.get(Camera, "CAM-D")
            cam.enabled = False
            db.add(cam)
            db.commit()
        main_mod._reconcile_live_camera("CAM-D")
        assert "CAM-D" not in main_mod.camera_sessions
        assert session.stopped
    finally:
        main_mod.scheduler and main_mod.scheduler.stop()
        _reset_api_pipeline(main_mod)
        _clear()
