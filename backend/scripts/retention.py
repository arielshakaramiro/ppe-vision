"""Hapus data lama sesuai kebijakan retensi (prinsip UU PDP: simpan data pribadi seperlunya).

    python -m scripts.retention --dry-run          # lihat apa yang akan dihapus
    python -m scripts.retention                    # jalankan sesuai config.yaml (retention.*)
    python -m scripts.retention --evidence-days 30 --quality-days 14

Yang dihapus:
- file foto/klip evidence yang lebih tua dari `evidence_days`. Baris event TETAP disimpan (untuk statistik
  dan jejak audit), hanya path file-nya dikosongkan. Satu baris ringkasan dicatat di audit log (actor "sistem").
- baris `QualityStat` yang lebih tua dari `quality_days`.
- file video (`VideoFile`) yang lebih tua dari `video_days` (berisi wajah pekerja). Video yang masih
  dipakai kamera atau pekerjaan analisis yang belum selesai TIDAK dihapus.
- sesi login yang sudah kedaluwarsa.

Event yang review-nya masih `pending` tidak disentuh, supaya bukti tidak hilang sebelum ditinjau.
Jalankan terjadwal (misalnya cron harian) di server produksi.
"""
from __future__ import annotations

import argparse
from datetime import timedelta

from sqlmodel import Session, select

from app import auth
from app.config import EVIDENCE_DIR, load_config
from app.db import (AnalysisJob, Camera, QualityStat, SessionToken, UploadSession, VideoFile,
                    Violation, engine, init_db)
from app.queries import now_local
from app.videos import VIDEO_DIR, tmp_path


def _remove(rel: str, dry: bool) -> bool:
    if not rel:
        return False
    path = (EVIDENCE_DIR / rel).resolve()
    if EVIDENCE_DIR.resolve() not in path.parents:  # jangan pernah menghapus di luar folder evidence
        return False
    if path.exists():
        if not dry:
            path.unlink()
        return True
    return False


def _remove_video(path_name: str, dry: bool) -> bool | None:
    """Hapus satu file video di VIDEO_DIR; None berarti path tidak aman dan baris DB dibiarkan."""
    if not path_name:
        return None
    path = (VIDEO_DIR / path_name).resolve()
    if VIDEO_DIR.resolve() not in path.parents:
        return None
    if path.exists():
        if not dry:
            path.unlink()
        return True
    return False


def run(evidence_days: int, quality_days: int, dry: bool, video_days: int = 14) -> dict:
    init_db()
    now = now_local()
    counts = {"event": 0, "file": 0, "quality": 0, "video": 0, "upload": 0, "sesi": 0}
    with Session(engine) as db:
        cutoff = now - timedelta(days=evidence_days)
        rows = db.exec(select(Violation).where(Violation.started_at < cutoff, Violation.review != "pending",
                                               (Violation.evidence_full != "") | (Violation.evidence_clip != "") |
                                               (Violation.evidence_clean != ""))).all()
        for v in rows:
            for rel in (v.evidence_full, v.evidence_crop, v.evidence_clean, v.evidence_clip):
                counts["file"] += _remove(rel, dry)
            if not dry:
                v.evidence_full = v.evidence_crop = v.evidence_clean = v.evidence_clip = ""
                db.add(v)
            counts["event"] += 1

        # Video (berisi wajah pekerja): hapus yang lama, kecuali masih dipakai kamera atau job belum selesai.
        v_cut = now - timedelta(days=video_days)
        active_jobs = {"menunggu", "berjalan", "dijeda"}
        in_use = {c.video_id for c in db.exec(select(Camera).where(Camera.video_id != "")).all()}
        in_use |= {j.video_id for j in db.exec(select(AnalysisJob)).all() if j.status in active_jobs}
        old_videos = db.exec(select(VideoFile).where(VideoFile.created_at < v_cut)).all()
        for vf in old_videos:
            if vf.id in in_use:
                continue
            removed = _remove_video(vf.path, dry)
            if removed is None:
                continue
            if removed:
                counts["video"] += 1
            if not dry:
                db.delete(vf)

        # Sesi upload kedaluwarsa + potongan .part yang tertinggal.
        expired_uploads = db.exec(select(UploadSession).where(UploadSession.expires_at < now)).all()
        counts["upload"] = len(expired_uploads)
        if not dry:
            for u in expired_uploads:
                tmp_path(u.id).unlink(missing_ok=True)
                db.delete(u)

        q_cut = now - timedelta(days=quality_days)
        old_quality = db.exec(select(QualityStat).where(QualityStat.minute < q_cut)).all()
        expired_sessions = db.exec(select(SessionToken).where(SessionToken.expires_at < now)).all()
        counts["quality"] = len(old_quality)
        counts["sesi"] = len(expired_sessions)
        if not dry:
            for row in old_quality:
                db.delete(row)
            for row in expired_sessions:
                db.delete(row)
            db.commit()
            auth.audit(db, "retensi", "sistem",
                       f"evidence>{evidence_days} hari: {counts['event']} event/{counts['file']} file; "
                       f"video>{video_days} hari: {counts['video']}; upload kedaluwarsa: {counts['upload']}; "
                       f"quality>{quality_days} hari: {counts['quality']}; sesi: {counts['sesi']}")
    return counts


def main() -> None:
    cfg = load_config().get("retention", {})
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-days", type=int, default=int(cfg.get("evidence_days", 90)))
    ap.add_argument("--quality-days", type=int, default=int(cfg.get("quality_days", 30)))
    ap.add_argument("--video-days", type=int, default=int(cfg.get("video_days", 14)))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.evidence_days < 1 or a.quality_days < 1 or a.video_days < 1:
        raise SystemExit("Jumlah hari minimal 1")
    c = run(a.evidence_days, a.quality_days, a.dry_run, a.video_days)
    print(("[SIMULASI] " if a.dry_run else "") + f"Evidence: {c['event']} event, {c['file']} file. "
          f"Video: {c['video']} file. QualityStat: {c['quality']} baris. Sesi kedaluwarsa: {c['sesi']}.")


if __name__ == "__main__":
    main()
