"""Pengaturan deteksi yang bisa diubah dari dashboard: sensitivitas (preset hysteresis),
shift, alert, retensi. Disimpan di tabel Setting (JSON per kunci); nilai awal dari config.yaml.

Pipeline tidak membaca modul ini; perubahan sensitivitas memengaruhi runner berikutnya
(hysteresis dibangun saat runner start). Zona (poligon + APD wajib) ditangani zones_store
dan berlaku tanpa restart.
"""
from __future__ import annotations

import json
import re
import threading

from sqlmodel import Session, select

from .config import load_config
from .db import Setting, engine
from .queries import now_local

_lock = threading.Lock()

# Preset sensitivitas -> nilai hysteresis. "seimbang" = nilai bawaan config.yaml.
# Ketat: lebih cepat menandai pelanggaran (ambang lebih rendah, butuh lebih sedikit frame).
# Longgar: lebih lambat, mengurangi false positive.
PRESETS = {
    "ketat": {"present_threshold": 0.8, "missing_threshold": -1.0, "event_min_frames": 6},
    "seimbang": {"present_threshold": 1.0, "missing_threshold": -1.5, "event_min_frames": 8},
    "longgar": {"present_threshold": 1.3, "missing_threshold": -2.0, "event_min_frames": 12},
}


def _defaults() -> dict:
    cfg = load_config()
    shifts = cfg.get("shifts", [])
    alerts = cfg.get("alerts", {})
    retention = cfg.get("retention", {})
    return {
        "sensitivity": "seimbang",
        "shifts": [{"name": s["name"], "start": s["start"], "end": s["end"]} for s in shifts],
        "alert": {
            "telegram": bool(alerts.get("telegram", False)),
            "cooldown_s": int(alerts.get("cooldown_s", 120)),
        },
        "retention": {
            "evidence_days": int(retention.get("evidence_days", 90)),
            "quality_days": int(retention.get("quality_days", 30)),
        },
    }


def get() -> dict:
    """Pengaturan efektif: default dari config, ditimpa nilai tersimpan di DB."""
    data = _defaults()
    with Session(engine) as db:
        row = db.exec(select(Setting).where(Setting.key == "detection")).first()
        if row and row.value_json:
            try:
                saved = json.loads(row.value_json)
            except ValueError:
                saved = {}
            data.update({k: v for k, v in saved.items() if k in data})
    data["preset_values"] = PRESETS
    return data


_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def validate(incoming: dict) -> dict:
    """Validasi ketat; kembalikan dict bersih siap disimpan. Lempar ValueError bila tidak valid."""
    base = _defaults()
    out: dict = {}

    sens = incoming.get("sensitivity", base["sensitivity"])
    if sens not in PRESETS:
        raise ValueError(f"Sensitivitas harus salah satu dari: {', '.join(PRESETS)}")
    out["sensitivity"] = sens

    shifts = incoming.get("shifts", base["shifts"])
    if not isinstance(shifts, list) or not (1 <= len(shifts) <= 6):
        raise ValueError("Shift harus 1-6 item")
    clean_shifts = []
    for s in shifts:
        if not isinstance(s, dict):
            raise ValueError("Format shift salah")
        name = str(s.get("name", "")).strip()[:32]
        start, end = str(s.get("start", "")), str(s.get("end", ""))
        if not name:
            raise ValueError("Nama shift wajib diisi")
        if not _TIME.match(start) or not _TIME.match(end):
            raise ValueError("Jam shift harus format HH:MM (00:00-23:59)")
        clean_shifts.append({"name": name, "start": start, "end": end})
    out["shifts"] = clean_shifts

    alert = incoming.get("alert", base["alert"])
    if not isinstance(alert, dict):
        raise ValueError("Format alert salah")
    cooldown = alert.get("cooldown_s", base["alert"]["cooldown_s"])
    if isinstance(cooldown, bool) or not isinstance(cooldown, int) or not (0 <= cooldown <= 86400):
        raise ValueError("Cooldown alert harus 0-86400 detik")
    out["alert"] = {"telegram": bool(alert.get("telegram", False)), "cooldown_s": cooldown}

    retention = incoming.get("retention", base["retention"])
    if not isinstance(retention, dict):
        raise ValueError("Format retensi salah")
    ev = retention.get("evidence_days", base["retention"]["evidence_days"])
    ql = retention.get("quality_days", base["retention"]["quality_days"])
    for label, v in (("evidence", ev), ("kualitas", ql)):
        if isinstance(v, bool) or not isinstance(v, int) or not (1 <= v <= 3650):
            raise ValueError(f"Retensi {label} harus 1-3650 hari")
    out["retention"] = {"evidence_days": ev, "quality_days": ql}
    return out


def save_to_session(db: Session, clean: dict, actor: str) -> Setting:
    """Simpan ke Session yang diberikan; caller yang commit bersama audit log."""
    with _lock:
        row = db.exec(select(Setting).where(Setting.key == "detection")).first()
        if row:
            row.value_json, row.updated_at, row.updated_by = json.dumps(clean), now_local(), actor
        else:
            row = Setting(key="detection", value_json=json.dumps(clean), updated_at=now_local(), updated_by=actor)
        db.add(row)
        return row


def merge_saved(clean: dict) -> dict:
    """Bentuk response efektif setelah `clean` tersimpan."""
    data = _defaults()
    data.update(clean)
    data["preset_values"] = PRESETS
    return data
