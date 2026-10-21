"""Heartbeat worker produksi melalui volume sementara bersama API."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path


HEARTBEAT_PATH = Path(tempfile.gettempdir()) / "ppe_live" / "worker-health.json"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def publish(*, started_at: datetime, pipeline_enabled: bool, cameras: int) -> None:
    """Tulis status minimum secara atomik agar pembaca tidak melihat JSON parsial."""
    now = _now_utc()
    payload = {
        "status": "berjalan",
        "pid": os.getpid(),
        "mulai_pada": started_at.astimezone(timezone.utc).isoformat(),
        "heartbeat_pada": now.isoformat(),
        "pipeline_aktif": pipeline_enabled,
        "kamera_aktif": max(0, int(cameras)),
    }
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = HEARTBEAT_PATH.with_name(f"{HEARTBEAT_PATH.name}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(HEARTBEAT_PATH)


def read(max_age_s: float = 30.0, *, now: datetime | None = None) -> dict:
    """Baca heartbeat dan beri status aman untuk API maupun healthcheck Compose."""
    try:
        payload = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
        heartbeat = datetime.fromisoformat(str(payload["heartbeat_pada"]))
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        age_s = max(0.0, ((now or _now_utc()) - heartbeat.astimezone(timezone.utc)).total_seconds())
        healthy = age_s <= max_age_s
        return {
            "sehat": healthy,
            "status": "berjalan" if healthy else "heartbeat_kedaluwarsa",
            "usia_heartbeat_detik": round(age_s, 1),
            "heartbeat_pada": heartbeat.astimezone(timezone.utc).isoformat(),
            "mulai_pada": payload.get("mulai_pada"),
            "pipeline_aktif": bool(payload.get("pipeline_aktif", False)),
            "kamera_aktif": max(0, int(payload.get("kamera_aktif", 0))),
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "sehat": False,
            "status": "heartbeat_tidak_tersedia",
            "usia_heartbeat_detik": None,
            "heartbeat_pada": None,
            "mulai_pada": None,
            "pipeline_aktif": False,
            "kamera_aktif": 0,
        }


if __name__ == "__main__":
    raise SystemExit(0 if read()["sehat"] else 1)