"""Isi database dengan data contoh supaya dashboard dan agent bisa dicoba tanpa model.

    python -m scripts.seed_demo --days 7

Gambar evidence dibuat sintetis (siluet sederhana), bukan foto asli.
"""
from __future__ import annotations

import argparse
import random
from datetime import timedelta

import cv2
import numpy as np
from sqlmodel import Session, col, delete, select

from app.config import EVIDENCE_DIR
from app.db import AuditLog, Intervention, QualityStat, TrackRecord, Violation, engine, init_db
from app.queries import now_local
from pipeline.evidence import sha256_file

ITEMS = ["helmet", "vest", "gloves", "shoes"]
CAMERAS = {"CAM-01": "Area Produksi", "CAM-02": "Gudang", "CAM-03": "Loading Dock"}
MISS_P = {"helmet": 0.10, "vest": 0.07, "gloves": 0.22, "shoes": 0.12}  # sarung tangan paling sering lupa
HOUR_W = {h: w for h, w in zip(range(7, 18), [2, 3, 3, 2, 1, 4, 3, 2, 2, 3, 2])}  # jam 12 & 13 rawan


def fake_image(path, missing: set[str], label: str) -> None:
    img = np.full((360, 640, 3), (58, 62, 66), np.uint8)
    cv2.rectangle(img, (0, 250), (640, 360), (80, 84, 88), -1)
    x, y = 290, 70
    cv2.circle(img, (x + 30, y + 25), 22, (170, 185, 205), -1)                       # kepala
    if "helmet" not in missing:
        cv2.ellipse(img, (x + 30, y + 14), (26, 18), 0, 180, 360, (0, 210, 255), -1)
    torso = (0, 230, 180) if "vest" not in missing else (120, 90, 70)
    cv2.rectangle(img, (x, y + 50), (x + 60, y + 150), torso, -1)
    for hx in (x - 14, x + 62):
        cv2.rectangle(img, (hx, y + 55), (hx + 12, y + 140), (110, 90, 75), -1)
        cv2.circle(img, (hx + 6, y + 146), 8, (240, 240, 240) if "gloves" not in missing else (170, 185, 205), -1)
    for fx in (x + 6, x + 34):
        cv2.rectangle(img, (fx, y + 150), (fx + 20, y + 230), (70, 60, 50), -1)
        cv2.rectangle(img, (fx - 3, y + 228), (fx + 24, y + 244), (30, 30, 30) if "shoes" not in missing else (200, 200, 200), -1)
    color = (68, 68, 239) if missing else (94, 197, 34)
    cv2.rectangle(img, (x - 30, y - 10), (x + 95, y + 250), color, 2)
    cv2.putText(img, label, (x - 30, y - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    cv2.putText(img, "DATA DEMO (sintetis)", (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)


DEMO_SUFFIX = "-demo"


def has_real_data(db: Session) -> bool:
    """True jika ada baris yang bukan buatan script ini (session tidak berakhiran -demo)."""
    for table in (Violation, TrackRecord):
        if db.exec(select(table).where(~col(table.session).endswith(DEMO_SUFFIX)).limit(1)).first():
            return True
    return False


def seed_quality(db: Session, now, days: int) -> None:
    """Statistik kualitas kamera per 5 menit. CAM-03 dibuat buram 2 jam terakhir untuk mencoba peringatan drift."""
    rows = []
    start = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    t = start
    while t <= now:
        work = 7 <= t.hour < 18
        for cam in CAMERAS:
            dirty = cam == "CAM-03" and now - t < timedelta(hours=2)
            rows.append(QualityStat(
                camera_id=cam, minute=t, frames=300,
                persons_per_frame=round(random.uniform(1.5, 4.0) if work else random.uniform(0, 0.3), 2),
                mean_person_conf=round(random.uniform(0.78, 0.86) * (0.8 if dirty else 1), 3) if work else 0.0,
                mean_ppe_conf=round(random.uniform(0.6, 0.75), 3) if work else 0.0,
                brightness=round(random.uniform(110, 150) if 6 <= t.hour < 18 else random.uniform(55, 80), 1),
                sharpness=round(random.uniform(180, 240) * (0.3 if dirty else 1), 1),
            ))
        t += timedelta(minutes=5)
    db.add_all(rows)


def main(days: int, per_day: int, seed: int, force: bool = False) -> None:
    random.seed(seed)
    init_db()
    with Session(engine) as db:
        if has_real_data(db) and not force:
            raise SystemExit("Database berisi data hasil kamera asli. Seed demo akan MENGHAPUS semuanya.\n"
                             "Batal. Pakai DATABASE_URL lain, atau tambahkan --force jika memang yakin.")
        for table in (Violation, TrackRecord, AuditLog, Intervention, QualityStat):
            db.exec(delete(table))
        db.commit()

        now = now_local()
        hours, weights = list(HOUR_W), list(HOUR_W.values())
        for d in range(days, -1, -1):
            day = (now - timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
            session = day.strftime("%Y%m%d") + DEMO_SUFFIX
            for tid in range(1, per_day + 1):
                cam = random.choice(list(CAMERAS))
                t0 = day + timedelta(hours=random.choices(hours, weights)[0], minutes=random.randint(0, 59),
                                     seconds=random.randint(0, 59))
                if t0 > now:
                    continue
                t1 = t0 + timedelta(seconds=random.randint(20, 240))
                missing = {i for i in ITEMS if random.random() < MISS_P[i]}
                uncertain = not missing and random.random() < 0.08
                status = "violation" if missing else ("uncertain" if uncertain else "compliant")
                db.add(TrackRecord(session=session, camera_id=cam, zone=CAMERAS[cam], track_id=tid,
                                   first_seen=t0, last_seen=t1, final_status=status, missing_items=",".join(sorted(missing))))
                if status == "compliant":
                    continue
                kind = status
                stem = f"{t0:%Y-%m-%d}/{cam}_{t0:%H%M%S}_W{tid:03d}"
                full, crop = f"{stem}_full.jpg", f"{stem}_crop.jpg"
                fake_image(EVIDENCE_DIR / full, missing, f"W{tid:03d}")
                fake_image(EVIDENCE_DIR / crop, missing, f"W{tid:03d}")
                ev_start = t0 + timedelta(seconds=random.randint(2, 15))
                ev_end = min(t1, ev_start + timedelta(seconds=random.randint(10, 120)))
                review = "pending" if d == 0 else random.choices(["confirmed", "false_positive", "pending"], [7, 1, 2])[0]
                db.add(Violation(
                    session=session, camera_id=cam, zone=CAMERAS[cam], track_id=tid, kind=kind,
                    started_at=ev_start, ended_at=ev_end, duration_s=(ev_end - ev_start).total_seconds(),
                    missing_items=",".join(sorted(missing)),
                    uncertain_items="gloves" if kind == "uncertain" else "",
                    confidence=round(random.uniform(0.6, 0.95), 3),
                    evidence_full=full, evidence_crop=crop, evidence_sha256=sha256_file(EVIDENCE_DIR / full),
                    review=review,
                    # versi model hanya label demo; tingkat false positive v1 dan v2 sengaja disamakan
                    # supaya data demo tidak mengarang "peningkatan" yang belum pernah diukur
                    model_version="v2" if d <= 2 else "v1",
                ))
        db.commit()
        seed_quality(db, now, days)
        db.add(Intervention(at=(now - timedelta(days=min(4, days))).replace(hour=7, minute=0, second=0, microsecond=0),
                            title="Safety briefing APD (contoh)", note="Data demo: intervensi contoh untuk mencoba fitur.",
                            created_at=now))
        db.commit()
    print(f"Data demo dibuat untuk {days + 1} hari ({per_day} pekerja/hari) di {EVIDENCE_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--per-day", type=int, default=40)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--force", action="store_true", help="hapus data meskipun berisi data kamera asli")
    a = ap.parse_args()
    main(a.days, a.per_day, a.seed, a.force)
