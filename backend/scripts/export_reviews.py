"""Ekspor hasil review supervisor sebagai bahan perbaikan model (siklus v1 -> v2).

    python -m scripts.export_reviews --out ../datasets/review_export [--version v1]

Hasil:
    <out>/false_positive/   frame utuh event yang ternyata BUKAN pelanggaran -> model salah membaca APD.
                            Manifest mencatat APD yang dibetulkan reviewer, misalnya sepatu sebenarnya dipakai.
                            Anotasi ulang APD yang sebenarnya dipakai, lalu masukkan ke data training.
    <out>/confirmed/        frame pelanggaran yang benar -> contoh positif yang sudah tervalidasi.
    <out>/manifest.csv      id, versi model, review, APD yang dilaporkan kurang, kamera, waktu, sha256, cocok_hash

Langkah berikutnya: unggah kedua folder ke Roboflow/CVAT, anotasi, gabungkan dengan
training/merge_datasets.py, latih ulang, lalu naikkan models.version di config.yaml (misalnya v2).
Bandingkan precision lapangan per versi di halaman Sistem dashboard.

Wajah di frame sudah diblur. Jangan unggah ke layanan publik tanpa izin pemilik lokasi.
"""
from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

from sqlmodel import Session, col, select

from app.config import EVIDENCE_DIR
from app import queries as Q
from app.db import Violation, engine, init_db
from pipeline.evidence import sha256_file


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--version", help="hanya event dari versi model ini")
    a = ap.parse_args()
    out = Path(a.out)
    init_db()
    with Session(engine) as db:
        q = select(Violation).where(col(Violation.review).in_(["confirmed", "false_positive"]))
        if a.version:
            q = q.where(Violation.model_version == a.version)
        rows = db.exec(q.order_by(Violation.started_at)).all()

    counts = {"confirmed": 0, "false_positive": 0, "hilang": 0, "hash_tidak_cocok": 0}
    for sub in ("confirmed", "false_positive"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "versi_model", "review", "apd_dilaporkan_kurang", "apd_dibenarkan", "kamera", "waktu",
                    "file", "sha256", "cocok_hash"])
        for r in rows:
            src = EVIDENCE_DIR / r.evidence_full
            if not src.exists():
                counts["hilang"] += 1
                continue
            ok = sha256_file(src) == r.evidence_sha256
            if not ok:
                counts["hash_tidak_cocok"] += 1  # file berubah sejak direkam: tetap diekspor tapi ditandai
            name = f"{r.id}_{Path(r.evidence_full).name}"
            shutil.copy2(src, out / r.review / name)
            w.writerow([r.id, r.model_version, r.review, r.missing_items, ",".join(Q.corrected_item_codes(r)), r.camera_id,
                        r.started_at.isoformat(timespec="seconds"), f"{r.review}/{name}", r.evidence_sha256, ok])
            counts[r.review] += 1
    print(f"Ekspor selesai ke {out}: {counts}")


if __name__ == "__main__":
    main()
