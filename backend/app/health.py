"""Kesehatan kamera: bandingkan kualitas input terbaru dengan baseline untuk mendeteksi drift."""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median

from sqlmodel import Session, select

from .config import load_config
from .db import QualityStat


def _cfg() -> dict:
    q = load_config().get("quality", {})
    return {
        "window_min": int(q.get("window_min", 30)),
        "baseline_days": int(q.get("baseline_days", 7)),
        "min_brightness": float(q.get("min_brightness", 40)),
        "max_sharpness_drop": float(q.get("max_sharpness_drop", 0.5)),
        "max_conf_drop": float(q.get("max_conf_drop", 0.2)),
    }


def _weighted(rows: list[QualityStat], attr: str, weight: str = "frames") -> float | None:
    rows = [r for r in rows if getattr(r, weight) > 0]
    total = sum(getattr(r, weight) for r in rows)
    return sum(getattr(r, attr) * getattr(r, weight) for r in rows) / total if total else None


def camera_health(db: Session, camera_id: str, now: datetime) -> dict:
    c = _cfg()
    win_start = now - timedelta(minutes=c["window_min"])
    base_start = now - timedelta(days=c["baseline_days"])
    rows = db.exec(select(QualityStat).where(QualityStat.camera_id == camera_id,
                                             QualityStat.minute >= base_start)).all()
    recent = [r for r in rows if r.minute >= win_start]
    # baseline: jam yang sama di hari-hari sebelumnya, supaya malam tidak dibandingkan dengan siang
    hour = now.hour
    baseline = [r for r in rows if r.minute < now - timedelta(hours=1) and r.minute.hour == hour] \
        or [r for r in rows if r.minute < win_start]

    issues: list[str] = []
    info: list[str] = []
    cur = {
        "brightness": _weighted(recent, "brightness"),
        "sharpness": _weighted(recent, "sharpness"),
        "person_conf": _weighted([r for r in recent if r.mean_person_conf > 0], "mean_person_conf"),
        "persons_per_frame": _weighted(recent, "persons_per_frame"),
    }
    base = {
        "brightness": median([r.brightness for r in baseline]) if baseline else None,
        "sharpness": median([r.sharpness for r in baseline]) if baseline else None,
        "person_conf": median([r.mean_person_conf for r in baseline if r.mean_person_conf > 0] or [0]) or None,
        "persons_per_frame": median([r.persons_per_frame for r in baseline]) if baseline else None,
    }

    if not recent:
        status = "tidak_ada_data"
    else:
        if cur["brightness"] is not None and cur["brightness"] < c["min_brightness"]:
            if base["brightness"] is None or base["brightness"] >= c["min_brightness"]:
                issues.append("Gambar terlalu gelap; deteksi APD kemungkinan menurun. Periksa penerangan atau mode malam kamera.")
            else:  # memang selalu gelap di jam ini: bukan perubahan mendadak, jangan kirim alert tiap malam
                info.append("Kamera selalu gelap di jam ini; akurasi deteksi pada jam ini perlu diperhitungkan.")
        if cur["sharpness"] and base["sharpness"] and cur["sharpness"] < base["sharpness"] * (1 - c["max_sharpness_drop"]):
            issues.append("Gambar jauh lebih buram dari biasanya. Periksa lensa (kotor/berembun) atau fokus kamera.")
        if cur["person_conf"] and base["person_conf"] and cur["person_conf"] < base["person_conf"] * (1 - c["max_conf_drop"]):
            issues.append("Keyakinan deteksi orang turun dibanding biasanya. Kemungkinan sudut kamera berubah atau kondisi berbeda.")
        if base["persons_per_frame"] and base["persons_per_frame"] > 0.5 and (cur["persons_per_frame"] or 0) == 0:
            issues.append("Tidak ada orang terdeteksi padahal biasanya ada. Periksa apakah kamera bergeser atau tertutup.")
        status = "peringatan" if issues else "baik"

    def r(v, n=2):
        return round(v, n) if v is not None else None

    return {
        "kamera": camera_id,
        "status": status,
        "masalah": issues,
        "info": info,
        "terkini": {k: r(v, 3) for k, v in cur.items()},
        "baseline": {k: r(v, 3) for k, v in base.items()},
        "jendela_menit": c["window_min"],
    }


def all_cameras(db: Session, now: datetime, hours: int = 24) -> list[dict]:
    since = now - timedelta(hours=hours)
    cams = sorted({r for r in db.exec(select(QualityStat.camera_id).where(QualityStat.minute >= since)).all()})
    out = []
    for cam in cams:
        h = camera_health(db, cam, now)
        rows = db.exec(select(QualityStat).where(QualityStat.camera_id == cam, QualityStat.minute >= since)
                       .order_by(QualityStat.minute)).all()
        # ringkas per jam untuk grafik
        buckets: dict[datetime, list[QualityStat]] = {}
        for row in rows:
            buckets.setdefault(row.minute.replace(minute=0), []).append(row)
        h["per_jam"] = [
            {"jam": k.isoformat(timespec="minutes"),
             "kecerahan": round(_weighted(v, "brightness") or 0, 1),
             "ketajaman": round(_weighted(v, "sharpness") or 0, 1),
             "conf_orang": round(_weighted([x for x in v if x.mean_person_conf > 0], "mean_person_conf") or 0, 3)}
            for k, v in sorted(buckets.items())
        ]
        out.append(h)
    return out
