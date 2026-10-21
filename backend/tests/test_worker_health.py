import json
from datetime import datetime, timedelta, timezone

from app import worker_health


def test_worker_heartbeat_is_atomic_and_reports_fresh_status(tmp_path, monkeypatch):
    path = tmp_path / "worker-health.json"
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(worker_health, "HEARTBEAT_PATH", path)
    monkeypatch.setattr(worker_health, "_now_utc", lambda: now)

    worker_health.publish(started_at=now - timedelta(minutes=2), pipeline_enabled=True, cameras=3)

    raw = json.loads(path.read_text(encoding="utf-8"))
    status = worker_health.read(now=now + timedelta(seconds=4))
    assert raw["status"] == "berjalan"
    assert status == {
        "sehat": True,
        "status": "berjalan",
        "usia_heartbeat_detik": 4.0,
        "heartbeat_pada": now.isoformat(),
        "mulai_pada": (now - timedelta(minutes=2)).isoformat(),
        "pipeline_aktif": True,
        "kamera_aktif": 3,
    }
    assert not list(tmp_path.glob("*.tmp"))


def test_worker_heartbeat_handles_stale_missing_and_invalid_files(tmp_path, monkeypatch):
    path = tmp_path / "worker-health.json"
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(worker_health, "HEARTBEAT_PATH", path)

    assert worker_health.read(now=now)["status"] == "heartbeat_tidak_tersedia"
    path.write_text("bukan-json", encoding="utf-8")
    assert worker_health.read(now=now)["status"] == "heartbeat_tidak_tersedia"
    path.write_text(json.dumps({
        "heartbeat_pada": (now - timedelta(seconds=31)).isoformat(),
        "pipeline_aktif": False,
        "kamera_aktif": 0,
    }), encoding="utf-8")
    status = worker_health.read(max_age_s=30, now=now)
    assert status["sehat"] is False
    assert status["status"] == "heartbeat_kedaluwarsa"


def test_worker_heartbeat_reports_unavailable_on_io_error(monkeypatch):
    class UnreadablePath:
        def read_text(self, **_kwargs):
            raise PermissionError("simulasi tidak dapat dibaca")

    monkeypatch.setattr(worker_health, "HEARTBEAT_PATH", UnreadablePath())
    status = worker_health.read()
    assert status["sehat"] is False
    assert status["status"] == "heartbeat_tidak_tersedia"