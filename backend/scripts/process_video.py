"""Proses satu video tanpa menjalankan server, hasilnya masuk database + folder evidence.

    python -m scripts.process_video --source data/videos/11.mp4 --start 2026-09-30T08:00

--start mengisi waktu mulai rekaman. Tanpa opsi ini, waktu event = waktu saat video diproses,
sehingga analitik per jam tidak mencerminkan kejadian di video. Event ditandai source_kind
"rekaman" supaya terpisah dari data kamera live.

Berbeda dari pipeline live, script ini membaca video frame demi frame secara berurutan (tidak ada
frame yang dibuang), supaya tracker melihat gerakan kontinu dan event bisa stabil.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta

import cv2

from app.config import BASE_DIR, load_config
from app.db import init_db
from app.sink import DbSink
from pipeline.detector import load_detector
from pipeline.runner import CameraSession, LiveState


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="path video atau URL RTSP (default: dari config.yaml)")
    ap.add_argument("--start", help="waktu mulai rekaman (WIB), format ISO, contoh 2026-09-30T08:00")
    ap.add_argument("--sample-rate", type=float, default=0.0,
                    help="pemeriksaan per detik video (mis. 5 atau 10); 0 = proses semua frame")
    args = ap.parse_args()

    cfg = load_config()
    source = args.source or str(cfg["camera"]["source"])
    start = datetime.fromisoformat(args.start).replace(tzinfo=None) if args.start else None
    init_db()

    detector = load_detector(cfg, BASE_DIR)
    # CameraSession tanpa reader-thread: kita suapi frame sendiri secara berurutan.
    session = CameraSession(cfg["camera"]["id"], cfg, DbSink(source_kind="rekaman"), LiveState(),
                            base_dir=BASE_DIR)

    path = source if source.startswith(("rtsp://", "http://", "https://")) else str(BASE_DIR / source)
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"Tidak bisa membuka video: {source}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(fps / args.sample_rate))) if args.sample_rate > 0 else 1
    session.src_fps = fps
    set_output_fps = getattr(session, "set_recorded_output_fps", None)
    if callable(set_output_fps):
        set_output_fps(fps)
    if session.clips:
        session.clips.set_fps(fps)

    frame_idx = 0
    processed = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            run_ppe = frame_idx % step == 0
            now = start + timedelta(seconds=frame_idx / fps) if start else None
            # Pose/tracking tetap jalan tiap frame agar bbox rekaman mengikuti gerakan asli;
            # APD/event saja yang disampling sesuai --sample-rate.
            pose_res, ppe_res = detector.batch_predict([frame], run_ppe)
            session.process(frame, pose_res[0], ppe_res[0] if run_ppe else None, 0.0, now=now)
            if run_ppe:
                processed += 1
            frame_idx += 1
        # Tutup event yang masih terbuka di akhir video.
        session._handle(session.manager.flush(), None, [])
    finally:
        cap.release()
        session.stop()
    print(f"Selesai. {processed} frame diperiksa dari {frame_idx} frame. Buka dashboard untuk melihat hasilnya.")


if __name__ == "__main__":
    main()
