"""Sumber aturan zona (poligon + APD wajib) dari database, aman untuk dibaca banyak thread.

Alur:
- Saat start, jika tabel ZoneRule kosong, diisi dari config.yaml (config tetap jadi nilai awal).
- Setelah itu database adalah sumber kebenaran.
- Pipeline membaca aturan lewat `snapshot()` yang mengembalikan list `pipeline.zones.Zone`.
  `bump()` dipanggil setiap aturan berubah; `snapshot()` memuat ulang dari DB bila perlu,
  jadi pipeline memakai aturan terbaru tanpa restart.

Modul ini milik `app/` (boleh menyentuh database). Pipeline tidak meng-import modul ini;
Runner menerima penyedia lewat parameter.
"""
from __future__ import annotations

import json
import threading
import time

from sqlmodel import Session, col, select

from pipeline.types import ITEMS
from pipeline.zones import Zone
from . import config
from .config import load_config
from .db import ZoneRule, engine
from .queries import now_local

_lock = threading.Lock()
_cache: dict[str, list[Zone]] = {}
_cache_signature: tuple[tuple[int | None, str], ...] | None = None
_next_signature_check = 0.0


def _parse_required(csv: str) -> tuple[str, ...]:
    items = tuple(i for i in (x.strip() for x in csv.split(",")) if i in ITEMS)
    if getattr(config, "PRODUCTION", False):
        # Di mode produksi, APD eksperimental (sarung tangan dan sepatu) tidak dijadikan kewajiban
        items = tuple(i for i in items if i in getattr(config, "STABLE_ITEMS", ("helmet", "vest")))
    return items


def seed_from_config_if_empty() -> None:
    """Isi ZoneRule dari config.yaml bila tabel masih kosong. Dipanggil sekali saat start."""
    cfg = load_config()
    with Session(engine) as db:
        if db.exec(select(ZoneRule).limit(1)).first():
            return
        now = now_local()
        allowed = getattr(config, "STABLE_ITEMS", ("helmet", "vest")) if getattr(config, "PRODUCTION", False) else ITEMS
        for z in cfg.get("zones", []):
            required = [i for i in z.get("required", ITEMS) if i in allowed]
            db.add(ZoneRule(
                name=z["name"], camera_id="",
                polygon_json=json.dumps([[float(x), float(y)] for x, y in z["polygon"]]),
                required_csv=",".join(required), updated_at=now, updated_by="config",
            ))
        db.commit()
    bump()


def bump() -> None:
    """Buang cache proses ini setelah endpoint mengubah aturan zona."""
    global _cache, _cache_signature, _next_signature_check
    with _lock:
        _cache = {}
        _cache_signature = None
        _next_signature_check = 0.0


def _database_signature(db: Session) -> tuple[tuple[int | None, str], ...]:
    """Tanda tangan ringan untuk mendeteksi perubahan dari proses/container lain."""
    rows = db.exec(select(ZoneRule.id, ZoneRule.updated_at).order_by(ZoneRule.id)).all()
    return tuple((zone_id, updated_at.isoformat()) for zone_id, updated_at in rows)


def _load_zones(db: Session, camera_id: str) -> list[Zone]:
    """Zona untuk satu kamera: gabungan zona global (camera_id="") + zona khusus kamera itu."""
    rows = db.exec(select(ZoneRule).where(col(ZoneRule.camera_id).in_(("", camera_id)))
                   .order_by(col(ZoneRule.camera_id).desc(), ZoneRule.name)).all()
    zones: list[Zone] = []
    for r in rows:
        try:
            poly = [(float(x), float(y)) for x, y in json.loads(r.polygon_json)]
        except (ValueError, TypeError):
            continue
        if len(poly) < 3:
            continue
        zones.append(Zone(r.name, poly, _parse_required(r.required_csv)))
    return zones


def snapshot(camera_id: str = "", max_age_s: float = 1.0) -> list[Zone]:
    """Daftar Zone terbaru untuk satu kamera, termasuk perubahan dari proses lain.

    `camera_id=""` mengembalikan zona global saja (dipakai pipeline mandiri/proses video).
    Tanda tangan database diperiksa maksimal sekali per `max_age_s`; endpoint lokal tetap
    memanggil `bump()` agar perubahan terlihat tanpa menunggu interval tersebut.
    """
    global _cache, _cache_signature, _next_signature_check
    now = time.monotonic()
    with _lock:
        check_signature = _cache_signature is None or now >= _next_signature_check
        if not check_signature and camera_id in _cache:
            return _cache[camera_id]
    with Session(engine) as db:
        signature = _database_signature(db) if check_signature else None
        with _lock:
            if signature is not None:
                if signature != _cache_signature:
                    _cache = {}
                _cache_signature = signature
                _next_signature_check = now + max(0.0, max_age_s)
            if camera_id in _cache:
                return _cache[camera_id]
        zones = _load_zones(db, camera_id)
    with _lock:
        _cache[camera_id] = zones
    return zones
