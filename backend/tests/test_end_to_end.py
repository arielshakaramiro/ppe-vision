"""Uji ujung ke ujung: runner dengan detektor palsu -> database -> API -> agent offline."""
import copy

import numpy as np
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.config import BASE_DIR, load_config
from app.db import Camera, ChatHistory, TrackRecord, Violation, engine, init_db
from app.main import app
from app.sink import DbSink
from pipeline.runner import LiveState, CameraSession
from tests.test_association import full_ppe, make_person


def _evidence_path(url: str) -> str:
    return url.removeprefix("/evidence/").split("?", 1)[0]


def _clear_e2e_data() -> None:
    with Session(engine) as db:
        for table in (ChatHistory, Violation, TrackRecord, Camera):
            db.exec(delete(table))
        db.commit()


class FakePoseRes:
    """Bungkus satu Person uji (box + keypoint asli) sebagai hasil pose Ultralytics."""
    def __init__(self):
        import torch
        from ultralytics.engine.results import Boxes, Keypoints
        p = make_person(7, x=200)
        x1, y1, x2, y2 = p.box
        self.boxes = Boxes(torch.tensor([[x1, y1, x2, y2, p.conf, 0.]]), (600, 800))
        self.keypoints = Keypoints(torch.tensor(p.keypoints).unsqueeze(0).float(), (600, 800))

class FakeTracker:
    def update(self, boxes, frame):
        return np.array([[200, 100, 320, 500, 7, 0.9, 0, 0]])

class FakeDetector:
    def __init__(self):
        self.frame = 0

    def batch_predict(self, frames, run_ppe):
        self.frame += len(frames)
        dets = full_ppe(x=200)
        if 30 <= self.frame < 90:
            dets = [d for d in dets if d.item != "helmet"]
        return [FakePoseRes() for _ in frames], [[d for d in dets] for _ in frames]


def test_runner_to_api_to_agent(tmp_path):
    init_db()
    _clear_e2e_data()
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["dir"] = str(tmp_path / "evidence")
    cfg["evidence"]["clip"]["enabled"] = True
    live = LiveState()

    cam = CameraSession("CAM-01", cfg, DbSink("test-session"), live, base_dir=BASE_DIR)
    cam.tracker = FakeTracker()
    det = FakeDetector()
    frame = np.full((600, 800, 3), 90, np.uint8)
    for i in range(140):
        pose, ppe = det.batch_predict([frame], True)
        cam.process(frame, pose[0], ppe[0], 0.1)
    cached = cam._people_snapshot([make_person(7, x=200)], evidence=None)
    cam._handle(cam.manager.flush(), None, [])
    if cam.clips:
        cam.clips.flush()

    assert live.jpeg and live.people[0]["track_id"] == 7
    assert live.people[0]["worker_id"] == 7 and live.people[0]["pekerja"] == "W007"
    assert live.clean_jpeg
    assert live.people[0]["box"] == {"x": 25.0, "y": 16.667, "w": 15.0, "h": 66.667}
    assert live.people[0]["confidence"]["vest"] == 0.85
    assert cached[0]["confidence"]["vest"] == 0.85

    client = TestClient(app)
    v = client.get("/api/violations", params={"period": "hari_ini"}).json()
    assert v["total"] == 1
    item = v["items"][0]
    assert item["apd_kurang"] == ["helm"] and item["durasi_detik"] is not None and len(item["sha256"]) == 64
    assert (tmp_path / "evidence" / _evidence_path(item["foto"])).exists()
    assert item["foto_clean"] and (tmp_path / "evidence" / _evidence_path(item["foto_clean"])).exists()
    detections = item["deteksi"]
    assert detections["frame"] == {"width": 800, "height": 600}
    assert detections["worker"]["box"] == {"x": 25.0, "y": 16.667, "w": 15.0, "h": 66.667}
    items = {det["key"]: det for det in detections["items"]}
    assert items["helmet"]["status"] == "missing" and items["helmet"]["boxes"] == []
    assert items["vest"]["boxes"][0]["confidence"] == 0.85
    assert len(items["gloves"]["boxes"]) == 2
    assert item["klip"] and (tmp_path / "evidence" / _evidence_path(item["klip"])).exists()
    assert item["versi_model"] == "v2"

    s = client.get("/api/stats", params={"period": "hari_ini"}).json()
    assert s["pekerja_terpantau"] == 1 and s["pekerja_melanggar"] == 1

    r = client.patch(f"/api/violations/{item['id']}/review", json={"review": "confirmed"}).json()
    assert r["review"] == "confirmed"

    a = client.post("/api/agent/chat", json={"message": "berapa orang yang tidak pakai helm hari ini?"}).json()
    assert "1 orang" in a["answer"] and a["evidence"][0]["id"] == item["id"]
    assert any("pekerja unik" in reason for reason in a["alasan_jawaban"])
    assert a["audit_data"]["metrik"]["orang_unik"] == 1


def test_period_resolution():
    from datetime import datetime

    from app.queries import resolve_range

    now = datetime(2026, 10, 1, 14, 30)
    assert resolve_range("kemarin", now=now) == (datetime(2026, 9, 30), datetime(2026, 10, 1))
    assert resolve_range("minggu_ini", now=now)[0] == datetime(2026, 9, 28)  # Senin
    assert resolve_range(start="2026-10-01T08:00", end="2026-10-01T12:00")[0].hour == 8


def test_offline_parser():
    from app.agent import parse_offline

    init_db()
    _clear_e2e_data()
    assert parse_offline("berapa yang tanpa sarung tangan kemarin di cam-02?") == {
        "period": "kemarin", "item": "gloves", "camera_id": "CAM-02"}
