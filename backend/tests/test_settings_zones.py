"""Fase 4: aturan zona di database, settings, audit, dan status sistem."""
from __future__ import annotations

import copy
from datetime import datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app import auth, zones_store
from app.config import BASE_DIR, load_config
from app.db import AuditLog, Setting, SessionToken, TrackRecord, User, Violation, ZoneRule, engine, init_db
from app.main import app
from app.sink import DbSink
from pipeline.runner import LiveState, CameraSession
from tests.test_association import full_ppe, make_person

PW = "rahasia-zona-2026"


@pytest.fixture(autouse=True)
def accounts_and_clean_db():
    init_db()
    auth.throttle.clear()
    with Session(engine) as db:
        for t in (SessionToken, User, Violation, TrackRecord, AuditLog, Setting, ZoneRule):
            db.exec(delete(t))
        db.commit()
        for name, role in (("vina", "viewer"), ("sapto", "supervisor"), ("adi", "admin")):
            auth.create_user(db, name, PW, role)
        db.commit()
    zones_store.bump()
    zones_store.seed_from_config_if_empty()
    yield
    with Session(engine) as db:
        for t in (SessionToken, User, Violation, TrackRecord, AuditLog, Setting, ZoneRule):
            db.exec(delete(t))
        db.commit()
    zones_store.bump()
    auth.throttle.clear()


def login(name: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": name, "password": PW})
    assert r.status_code == 200, r.text
    return c


def test_zone_endpoints_role_matrix_and_validation():
    viewer, supervisor, admin = login("vina"), login("sapto"), login("adi")

    zones = viewer.get("/api/zones")
    assert zones.status_code == 200
    assert zones.json()[0]["name"] == "Area Produksi"
    assert zones.json()[0]["required"] == ["helmet", "vest"]

    assert viewer.patch("/api/zones/Area Produksi/required", json={"required": ["helmet"]}).status_code == 403
    assert supervisor.patch("/api/zones/Area Produksi/required", json={"required": []}).status_code == 400
    assert supervisor.patch("/api/zones/Area Produksi/required", json={"required": ["mask"]}).status_code == 400

    ok = supervisor.patch("/api/zones/Area Produksi/required", json={"required": ["helmet", "vest"]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["required"] == ["helmet", "vest"]

    assert viewer.post("/api/zones", json={"name": "Gudang", "polygon": [[0, 0], [1, 0], [1, 1]], "required": ["helmet"]}).status_code == 403
    assert supervisor.post("/api/zones", json={"name": "Gudang", "polygon": [[0, 0], [1, 0], [1, 1]], "required": ["helmet"]}).status_code == 403
    bad_poly = admin.post("/api/zones", json={"name": "Gudang", "polygon": [[0, 0], [2, 0], [1, 1]], "required": ["helmet"]})
    assert bad_poly.status_code == 400

    created = admin.post("/api/zones", json={"name": " Gudang ", "camera_id": " CAM-01 ", "polygon": [[0, 0], [1, 0], [1, 1]], "required": ["helmet"]})
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Gudang" and created.json()["camera_id"] == "CAM-01"
    # Nama sama pada kamera sama ditolak; nama sama pada kamera lain (atau global) diperbolehkan.
    duplicate = admin.post("/api/zones", json={"name": "Gudang", "camera_id": "CAM-01", "polygon": [[0, 0], [1, 0], [1, 1]], "required": ["helmet"]})
    assert duplicate.status_code == 400
    other_cam = admin.post("/api/zones", json={"name": "Gudang", "camera_id": "CAM-02", "polygon": [[0, 0], [1, 0], [1, 1]], "required": ["helmet"]})
    assert other_cam.status_code == 201, other_cam.text
    assert admin.delete("/api/zones/Gudang", params={"camera_id": "CAM-02"}).status_code == 204
    renamed = admin.put("/api/zones/Gudang", params={"camera_id": "CAM-01"}, json={"name": " Gudang Utama ", "camera_id": " CAM-01 ", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]], "required": ["helmet", "shoes"]})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Gudang Utama" and renamed.json()["camera_id"] == "CAM-01"
    assert admin.delete("/api/zones/Gudang Utama", params={"camera_id": "CAM-01"}).status_code == 204

    with Session(engine) as db:
        actions = {r.action for r in db.exec(select(AuditLog)).all()}
    assert {"zone_required_diubah", "zone_dibuat", "zone_diubah", "zone_dihapus"} <= actions


def test_settings_audit_and_system_status(monkeypatch):
    from app import worker_health
    monkeypatch.setattr(worker_health, "read", lambda: {
        "sehat": True, "status": "berjalan", "usia_heartbeat_detik": 2.0,
        "heartbeat_pada": "2026-10-16T12:00:00+00:00", "mulai_pada": "2026-10-16T11:00:00+00:00",
        "pipeline_aktif": True, "kamera_aktif": 3,
    })
    viewer, supervisor, admin = login("vina"), login("sapto"), login("adi")

    assert viewer.get("/api/settings").status_code == 403
    assert supervisor.get("/api/settings").status_code == 403
    settings = admin.get("/api/settings")
    assert settings.status_code == 200
    body = settings.json()
    assert body["sensitivity"] == "seimbang" and "preset_values" in body

    bad = admin.put("/api/settings", json={"sensitivity": "bebas"})
    assert bad.status_code == 400
    bad_bool = admin.put("/api/settings", json={"alert": {"cooldown_s": True}, "retention": {"evidence_days": True, "quality_days": 10}})
    assert bad_bool.status_code == 400
    changed = admin.put("/api/settings", json={
        "sensitivity": "ketat",
        "shifts": [{"name": "Pagi", "start": "07:00", "end": "15:00"}],
        "alert": {"telegram": True, "cooldown_s": 60},
        "retention": {"evidence_days": 30, "quality_days": 10},
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["sensitivity"] == "ketat"

    with Session(engine) as db:
        db.add(Violation(session="20261001-demo", camera_id="CAM-01", track_id=1, started_at=datetime(2026, 10, 1, 8)))
        db.commit()
    status = viewer.get("/api/system/status")
    assert status.status_code == 200
    assert status.json()["data_contoh"] is True
    assert status.json()["versi_model"] == "v2"
    assert status.json()["alert"] == {"telegram": True, "cooldown_s": 60, "reviewer_terhubung": 0}
    assert status.json()["retensi"]["evidence_days"] == 30
    assert status.json()["retensi"]["quality_days"] == 10
    assert status.json()["model"]["apd"] == "best.pt"
    assert len(status.json()["kapasitas"]["proyeksi_kamera"]) == 4
    assert status.json()["worker"]["sehat"] is True
    assert status.json()["worker"]["kamera_aktif"] == 3

    assert viewer.get("/api/audit").status_code == 403
    audit = supervisor.get("/api/audit", params={"period": "30_hari"})
    assert audit.status_code == 200
    assert any(i["aksi"] == "pengaturan_diubah" and i["aktor"] == "adi" for i in audit.json()["items"])


def test_capacity_summary_uses_runtime_throughput(monkeypatch):
    from app import main

    monkeypatch.setattr(main.os, "getloadavg", lambda: (2.0, 1.0, 0.5))
    monkeypatch.setattr(main.os, "cpu_count", lambda: 4)
    result = main._capacity_summary([
        {"running": True, "waktu_batch_ms": 100.0, "pemeriksaan_per_detik": 6.0},
        {"running": True, "waktu_batch_ms": 120.0, "pemeriksaan_per_detik": 4.0},
        {"running": False, "waktu_batch_ms": 0.0, "pemeriksaan_per_detik": 0.0},
    ], {"pipeline": {"min_rate_per_camera": 5}, "models": {"device": "mps"}})

    assert result["waktu_batch_ms"] == 110.0
    assert result["jumlah_frame_batch"] == 2
    assert result["pemeriksaan_per_kamera"] == 5.0
    assert result["beban_perangkat_persen"] == 50.0
    assert result["perangkat"] == "mps"
    assert result["proyeksi_kamera"][1] == {"jumlah": 2, "pemeriksaan_per_kamera": 5.0, "memenuhi_batas": True}
    assert result["proyeksi_kamera"][2]["memenuhi_batas"] is False


class _FakeTracker:
    """Kembalikan satu track id tetap (7) pada kotak pertama."""
    def update(self, boxes, frame):
        return np.array([[200, 100, 320, 500, 7, 0.9, 0, 0]])


class _FakePoseRes:
    def __init__(self):
        import torch
        from ultralytics.engine.results import Boxes, Keypoints
        p = make_person(7, x=200)
        x1, y1, x2, y2 = p.box
        self.boxes = Boxes(torch.tensor([[x1, y1, x2, y2, p.conf, 0.]]), (600, 800))
        self.keypoints = Keypoints(torch.tensor(p.keypoints).unsqueeze(0).float(), (600, 800))


def test_runner_uses_zone_provider_after_required_items_change(tmp_path):
    cfg = copy.deepcopy(load_config())
    cfg["evidence"]["dir"] = str(tmp_path / "evidence")
    cfg["hysteresis"]["min_evaluations"] = 1
    cfg["hysteresis"]["event_min_frames"] = 1
    cfg["hysteresis"]["present_threshold"] = 0.1
    live = LiveState()
    cam = CameraSession("CAM-01", cfg, DbSink("zone-test"), live, base_dir=BASE_DIR,
                        zone_provider=zones_store.snapshot)
    cam.tracker = _FakeTracker()

    frame = np.full((600, 800, 3), 90, np.uint8)
    ppe = full_ppe(x=200)
    cam.process(frame, _FakePoseRes(), ppe, 0.1)
    assert live.people[0]["zone"] == "Area Produksi"
    assert live.people[0]["checklist"]["helmet"] == "present"
    assert live.people[0]["checklist"]["vest"] == "present"
    assert live.people[0]["checklist"]["gloves"] == "not_required"
    assert live.people[0]["checklist"]["shoes"] == "not_required"

    c = login("sapto")
    r = c.patch("/api/zones/Area Produksi/required", json={"required": ["vest"]})
    assert r.status_code == 200, r.text

    cam.process(frame, _FakePoseRes(), ppe, 0.1)
    assert live.people[0]["checklist"]["helmet"] == "not_required"
    assert live.people[0]["checklist"]["vest"] == "present"


def test_zone_snapshot_detects_change_from_another_process():
    """Worker harus melihat commit API walau bump() hanya terjadi di proses API."""
    before = zones_store.snapshot("CAM-01", max_age_s=0)
    area = next(zone for zone in before if zone.name == "Area Produksi")
    assert area.required == ("helmet", "vest")

    with Session(engine) as db:
        row = db.exec(select(ZoneRule).where(ZoneRule.name == "Area Produksi", ZoneRule.camera_id == "")).one()
        row.required_csv = "helmet,vest,gloves,shoes"
        row.updated_at = datetime(2026, 10, 16, 20, 0, 0)
        row.updated_by = "proses-api"
        db.add(row)
        db.commit()

    # Sengaja tanpa zones_store.bump(): simulasi cache worker di container terpisah.
    after = zones_store.snapshot("CAM-01", max_age_s=0)
    area = next(zone for zone in after if zone.name == "Area Produksi")
    assert area.required == ("helmet", "vest", "gloves", "shoes")


def test_production_mode_zone_guards_and_api(monkeypatch):
    """Di mode produksi, APD eksperimental (sarung tangan/sepatu) tidak boleh diwajibkan."""
    from app import config as cfg_mod

    admin_client = login("adi")

    # Set database memuat 4 kelas
    with Session(engine) as db:
        row = db.exec(select(ZoneRule).where(ZoneRule.name == "Area Produksi", ZoneRule.camera_id == "")).one()
        row.required_csv = "helmet,vest,gloves,shoes"
        db.add(row)
        db.commit()
    zones_store.bump()

    monkeypatch.setattr(cfg_mod, "PRODUCTION", True)

    # 1. snapshot memfilter APD eksperimental
    sn = zones_store.snapshot("CAM-01", max_age_s=0)
    area = next(z for z in sn if z.name == "Area Produksi")
    assert area.required == ("helmet", "vest")

    # 2. GET /api/zones menyembunyikan APD eksperimental dari status wajib
    r_get = admin_client.get("/api/zones")
    assert r_get.status_code == 200
    assert r_get.json()[0]["required"] == ["helmet", "vest"]

    # 3. PATCH /api/zones/{name}/required menolak sarung tangan dan sepatu
    r_patch = admin_client.patch("/api/zones/Area Produksi/required", json={"required": ["helmet", "gloves"]})
    assert r_patch.status_code == 400
    assert "eksperimental" in r_patch.text

    # 4. POST /api/zones menolak penambahan zona baru dengan sarung tangan / sepatu
    r_post = admin_client.post("/api/zones", json={
        "name": "Zona Las",
        "polygon": [[0, 0], [1, 0], [1, 1]],
        "required": ["helmet", "shoes"],
    })
    assert r_post.status_code == 400
    assert "eksperimental" in r_post.text


def test_validate_production_config_rules(monkeypatch):
    from app import config as cfg_mod

    valid_cfg = {
        "zones": [{"name": "Area Produksi", "required": ["helmet", "vest"]}],
    }

    # Kondisi ideal: lolos tanpa exception
    monkeypatch.setattr(cfg_mod, "AGENT_HISTORY_KEY", "kunci-rahasia-panjang-123456789")
    monkeypatch.setattr(cfg_mod, "COOKIE_SECURE", True)
    monkeypatch.setattr(cfg_mod, "API_DOCS", False)
    cfg_mod.validate_production_config(valid_cfg)

    # Gagal jika AGENT_HISTORY_KEY kosong atau terlalu pendek
    monkeypatch.setattr(cfg_mod, "AGENT_HISTORY_KEY", "pendek")
    with pytest.raises(ValueError, match="AGENT_HISTORY_KEY"):
        cfg_mod.validate_production_config(valid_cfg)

    # Gagal jika COOKIE_SECURE False
    monkeypatch.setattr(cfg_mod, "AGENT_HISTORY_KEY", "kunci-rahasia-panjang-123456789")
    monkeypatch.setattr(cfg_mod, "COOKIE_SECURE", False)
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        cfg_mod.validate_production_config(valid_cfg)

    # Gagal jika API_DOCS True
    monkeypatch.setattr(cfg_mod, "COOKIE_SECURE", True)
    monkeypatch.setattr(cfg_mod, "API_DOCS", True)
    with pytest.raises(ValueError, match="API_DOCS"):
        cfg_mod.validate_production_config(valid_cfg)

    # Gagal jika default zona di config memuat APD eksperimental
    monkeypatch.setattr(cfg_mod, "API_DOCS", False)
    bad_zones_cfg = {
        "zones": [{"name": "Area Produksi", "required": ["helmet", "vest", "gloves"]}],
    }
    with pytest.raises(ValueError, match="eksperimental"):
        cfg_mod.validate_production_config(bad_zones_cfg)
