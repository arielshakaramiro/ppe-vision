"""Worker analisis rekaman (AnalysisJob).

Kamera bersumber video dengan mode "analisis" tidak ikut pipeline live. Sebagai gantinya,
dibuat satu `AnalysisJob` yang diproses di latar belakang oleh `JobManager`: video dibaca
sekali sampai habis dengan kecepatan pemeriksaan yang dipilih (`sample_rate` per detik video),
hasilnya masuk database sebagai event `source_kind="rekaman"`.

Prioritas kamera live dijaga dengan dua cara: hanya satu worker job berjalan pada satu waktu,
dan model dipakai lewat `inference_lock` yang sama dengan scheduler live (giliran, tidak paralel).

Pekerjaan bisa dijeda, dilanjutkan (dari `position_s`), dibatalkan, dan dilanjutkan otomatis
setelah server restart. Modul ini milik `app/`; pipeline tetap tidak meng-import `app/`.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import cv2
from sqlmodel import Session, col, select

from . import videos
from .config import BASE_DIR, load_config
from .db import AnalysisJob, VideoFile, engine
from .queries import now_local
from .security import redact
from .sink import DbSink
from pipeline.clips import CODEC, EXT, sha256_file

log = logging.getLogger("ppe.jobs")

ACTIVE_STATES = ("menunggu", "berjalan", "dijeda")
PROCESSED_VIDEO_VERSION = "processed-v2"
_PERSIST_EVERY_S = 2.0  # seberapa sering posisi/jumlah event disimpan ke database
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_job_stem(job_id: str) -> str:
    stem = _SAFE_STEM.sub("_", job_id).strip("._-")
    return stem[:80] or "job"


def _evidence_root(cfg: dict, base_dir: Path) -> Path:
    raw = Path(str(cfg.get("evidence", {}).get("dir", "data/evidence")))
    return raw if raw.is_absolute() else base_dir / raw


def _open_output_writer(job_id: str, cfg: dict, base_dir: Path, fps: float, frame: Any) -> tuple[cv2.VideoWriter, Path, Path, tuple[int, int]] | None:
    h, w = frame.shape[:2]
    w, h = max(2, (w // 2) * 2), max(2, (h // 2) * 2)
    out_dir = _evidence_root(cfg, base_dir).resolve() / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{_safe_job_stem(job_id)}-{PROCESSED_VIDEO_VERSION}"
    out_path = out_dir / f"{stem}{EXT}"
    tmp_path = out_dir / f"{stem}.part{EXT}"
    tmp_path.unlink(missing_ok=True)
    out_path.unlink(missing_ok=True)
    fourcc = getattr(cv2, "VideoWriter_fourcc")(*CODEC)
    writer = cv2.VideoWriter(str(tmp_path), fourcc, max(1.0, min(60.0, fps)), (w, h))
    if not writer.isOpened():
        return None
    return writer, tmp_path, out_path, (w, h)


def _output_fps(selected_fps: float, fallback_fps: float = 0.0) -> float:
    """Pilih FPS video hasil agar durasi playback sama dengan rekaman asli."""
    fps = selected_fps or fallback_fps or 25.0
    return max(1.0, min(60.0, float(fps)))


def _relative_to_evidence(path: Path, cfg: dict, base_dir: Path) -> str:
    root = _evidence_root(cfg, base_dir).resolve()
    return path.resolve().relative_to(root).as_posix()


def _write_output_metadata(path: Path, meta: dict) -> None:
    try:
        path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # metadata bantu audit; jangan gagalkan job karena sidecar
        log.warning("Gagal menulis metadata video hasil %s: %s", path.name, type(e).__name__)


class JobManager:
    """Menjalankan satu worker job pada satu waktu dari antrean AnalysisJob.

    Detektor dan `inference_lock` dibagikan oleh pemanggil (main.py) supaya model yang sama
    dipakai berggiliran dengan scheduler live.
    """

    def __init__(self, detector_factory, inference_lock, base_dir=BASE_DIR):
        self._detector_factory = detector_factory  # callable -> Detector (lazy, hanya saat ada job)
        self._detector = None
        self.lock = inference_lock
        self.base_dir = base_dir
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # job_id yang sedang diproses -> flag kontrol
        self._current_id: str | None = None

    # ---------- siklus hidup manager ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="job-manager")
        self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)

    def _loop(self) -> None:
        """Ambil satu job aktif berikutnya, jalankan, ulangi. Idle saat tidak ada job."""
        while not self._stop.is_set():
            job = self._next_job()
            if not job:
                time.sleep(1.0)
                continue
            try:
                self._run_job(job.id)
            except Exception as e:  # pragma: no cover - jaga worker tetap hidup
                log.error("Worker job %s gagal (%s): %s", job.id, type(e).__name__, redact(str(e)))
                self._set_status(job.id, "gagal")

    def _next_job(self) -> AnalysisJob | None:
        with Session(engine) as db:
            return db.exec(
                select(AnalysisJob).where(col(AnalysisJob.status).in_(("menunggu", "berjalan")))
                .order_by(AnalysisJob.id)
            ).first()

    # ---------- menjalankan satu job ----------

    def _run_job(self, job_id: str) -> None:
        with Session(engine) as db:
            job = db.get(AnalysisJob, job_id)
            if not job or job.status not in ("menunggu", "berjalan"):
                return
            video = db.get(VideoFile, job.video_id)
            recording_start = job.recording_start
            sample_rate = job.sample_rate or 5.0
            resume_pos = job.position_s or 0.0
            events = job.events or 0
        if not video:
            self._set_status(job_id, "gagal")
            return

        path = str(videos.resolve_server_video(video.path))
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            cap.release()
            self._set_status(job_id, "gagal")
            return

        timing = videos.effective_video_timing(cap, video.fps or 0.0, video.duration_s or 0.0)
        fps = _output_fps(timing["fps"], video.fps or 0.0)
        source_frames = int(timing.get("frame_count") or 0)
        source_duration = float(timing.get("duration_s") or video.duration_s or 0.0)
        step = max(1, int(round(fps / sample_rate)))  # evaluasi APD 1 dari tiap `step` frame; output tetap tiap frame
        # Video hasil selalu dibuat ulang dari awal. `position_s` lama hanya boleh dipakai untuk
        # progres UI/ETA, bukan untuk seek output, karena WebM yang dibuat ulang tidak bisa
        # menyambung frame yang sudah ditulis sebelum worker restart/jeda.
        start_pos = 0.0
        log.info("Mulai job rekaman %s: video=%s camera=%s fps=%.3f (%s) frames=%s durasi=%.2fs sample=%.2f step=%s versi=%s",
                 job_id, video.id, job.camera_id, fps, timing.get("fps_source"), source_frames,
                 source_duration, sample_rate, step, PROCESSED_VIDEO_VERSION)

        # CameraSession satu kamera, sink menandai event sebagai "rekaman".
        from pipeline.runner import CameraSession, LiveState
        cfg = load_config()
        sink = DbSink(session_id=job_id, source_kind="rekaman")
        session = CameraSession(job.camera_id, cfg, sink, LiveState(job.camera_id), base_dir=self.base_dir,
                                zone_provider=self._zone_provider(job.camera_id))
        session.src_fps = fps
        set_output_fps = getattr(session, "set_recorded_output_fps", None)
        if callable(set_output_fps):
            set_output_fps(fps)
        clips = getattr(session, "clips", None)
        if clips:
            # Worker rekaman memanggil CameraSession.process() pada setiap frame supaya overlay bergerak
            # mulus; karena itu klip evidence juga menerima setiap frame dan harus memakai FPS asli video.
            clips.set_fps(fps)
        detector = self._get_detector()

        self._current_id = job_id
        self._set_status(job_id, "berjalan", started=True)

        frame_idx = int(start_pos * fps)
        last_persist = time.time()
        status = "berjalan"
        writer_info = None
        writer_failed = False
        frames_read = 0
        frames_written = 0
        output_path: Path | None = None
        try:
            while not self._stop.is_set():
                ret, frame = cap.read()
                if not ret:
                    status = "selesai"
                    break

                frames_read += 1
                run_ppe = frame_idx % step == 0
                now = recording_start + timedelta(seconds=frame_idx / fps)
                with self.lock:
                    # Pose tetap dijalankan tiap frame agar posisi bounding box video hasil mengikuti
                    # gerakan pekerja. PPE/event tetap hanya dievaluasi pada cadence sample_rate.
                    pose_res, ppe_res = detector.batch_predict([frame], run_ppe)
                before = len(session.manager.tracks)
                session.process(frame, pose_res[0], ppe_res[0] if run_ppe else None, 0.0, now=now)
                render_held = getattr(session, "render_held_overlay_frame", None)
                if callable(render_held):
                    annotated = render_held(frame)
                else:
                    render_last = getattr(session, "render_last_frame", None)
                    annotated = render_last(frame) if callable(render_last) else frame
                if run_ppe:
                    # hitung event kasar: tambah saat ada track baru membuka event (perkiraan)
                    events += max(0, len(session.manager.tracks) - before)

                annotated_frame = cast(Any, annotated)
                if writer_info is None and not writer_failed:
                    writer_info = _open_output_writer(job_id, cfg, self.base_dir, fps, annotated_frame)
                    if writer_info is None:
                        writer_failed = True
                        log.warning("Gagal membuka penulis video hasil deteksi untuk job %s", job_id)
                if writer_info is not None:
                    writer, _, _, size = writer_info
                    if annotated_frame.shape[1] != size[0] or annotated_frame.shape[0] != size[1]:
                        annotated_frame = cv2.resize(annotated_frame, size)
                    writer.write(annotated_frame)
                    frames_written += 1
                frame_idx += 1

                if time.time() - last_persist >= _PERSIST_EVERY_S:
                    pos = frame_idx / fps
                    state = self._poll_and_persist(job_id, pos, events)
                    last_persist = time.time()
                    if state == "dijeda":
                        self._save_position(job_id, pos, events)
                        status = "dijeda"
                        break
                    if state in ("dibatalkan", None):
                        status = "dibatalkan"
                        break
        finally:
            if status in ("selesai", "dijeda", "dibatalkan"):
                session._handle(session.manager.flush(), None, [])
            if writer_info is not None:
                writer, tmp_path, final_path, _ = writer_info
                writer.release()
                if status == "selesai" and tmp_path.exists() and tmp_path.stat().st_size > 0:
                    tmp_path.replace(final_path)
                    output_path = final_path
                else:
                    tmp_path.unlink(missing_ok=True)
            cap.release()
            session.stop()
            self._current_id = None

        pos = frame_idx / fps
        output_meta = {
            "version": PROCESSED_VIDEO_VERSION,
            "job_id": job_id,
            "camera_id": job.camera_id,
            "video_id": video.id,
            "source_path": path,
            "source_sha256": video.sha256,
            "source_size": video.size,
            "source_fps": fps,
            "source_fps_source": timing.get("fps_source"),
            "source_frames": source_frames,
            "source_duration_s": source_duration,
            "start_pos_requested_s": round(float(resume_pos), 3),
            "effective_start_pos_s": round(float(start_pos), 3),
            "sample_rate": sample_rate,
            "step": step,
            "frames_read": frames_read,
            "frames_written": frames_written,
            "output_duration_s": round(frames_written / fps, 3) if fps and frames_written else 0.0,
            "finished_status": status,
        }
        log.info("Selesai job rekaman %s: status=%s frames_read=%s frames_written=%s output_duration=%.2fs",
                 job_id, status, frames_read, frames_written, output_meta["output_duration_s"])
        if status == "selesai":
            self._finalize(job_id, pos, events, output_path, cfg, output_meta)
        elif status == "dijeda":
            self._save_position(job_id, pos, events)
        elif status == "dibatalkan":
            self._save_position(job_id, pos, events, status="dibatalkan")

    # ---------- util database ----------

    def _zone_provider(self, camera_id: str):
        from . import zones_store
        return lambda: zones_store.snapshot(camera_id)

    def _get_detector(self):
        if self._detector is None:
            self._detector = self._detector_factory()
        return self._detector

    def _poll_and_persist(self, job_id: str, pos: float, events: int) -> str | None:
        with Session(engine) as db:
            job = db.get(AnalysisJob, job_id)
            if not job:
                return None
            if job.status in ("dijeda", "dibatalkan"):
                return job.status
            job.position_s = pos
            job.events = events
            db.add(job)
            db.commit()
            return job.status

    def _save_position(self, job_id: str, pos: float, events: int, status: str | None = None) -> None:
        with Session(engine) as db:
            job = db.get(AnalysisJob, job_id)
            if not job:
                return
            job.position_s = pos
            job.events = events
            if status:
                job.status = status
            db.add(job)
            db.commit()

    def _finalize(self, job_id: str, pos: float, events: int, output_path: Path | None = None,
                  cfg: dict | None = None, output_meta: dict | None = None) -> None:
        with Session(engine) as db:
            job = db.get(AnalysisJob, job_id)
            if not job:
                return
            job.position_s = pos
            job.events = events
            job.status = "selesai"
            job.finished_at = now_local()
            if output_path and cfg is not None:
                output_sha = sha256_file(output_path)
                job.output_video = _relative_to_evidence(output_path, cfg, self.base_dir)
                job.output_video_sha256 = output_sha
                if output_meta is not None:
                    _write_output_metadata(output_path, {**output_meta, "output_sha256": output_sha})
            db.add(job)
            db.commit()

    def _set_status(self, job_id: str, status: str, started: bool = False) -> None:
        with Session(engine) as db:
            job = db.get(AnalysisJob, job_id)
            if not job:
                return
            job.status = status
            if started and not job.started_at:
                job.started_at = now_local()
            if status in ("gagal", "dibatalkan", "selesai"):
                job.finished_at = now_local()
            db.add(job)
            db.commit()
