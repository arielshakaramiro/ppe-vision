import asyncio
import logging
import sys
from datetime import datetime, timezone
from sqlmodel import Session, select

from app import config
from app.alerts import telegram_enabled
from app.config import BASE_DIR
from app.db import AnalysisJob, Camera, engine, init_db
from app.jobs import JobManager
from app.main import _detector, _reconcile_live_camera, _start_pipeline, _stop_pipeline, inference_lock, runner_lock
from app.telegram_bot import TelegramPoller
from app import worker_health
import scripts.backup as backup
import scripts.retention as retention

log = logging.getLogger("ppe.worker")
jobs = JobManager(_detector, inference_lock, base_dir=BASE_DIR)
poller: TelegramPoller | None = None
_camera_signature: tuple[tuple, ...] = ()
_live_detector = None
_started_at = datetime.now(timezone.utc)


def _live_camera_signature() -> tuple[tuple, ...]:
    """Ringkasan kamera yang seharusnya berjalan di scheduler live/simulasi.

    Worker produksi adalah proses terpisah dari API. Saat admin menambah/mengubah/menghapus kamera,
    proses worker tidak menerima event langsung, jadi polling signature ini dipakai untuk mendeteksi
    perubahan konfigurasi tanpa restart container manual.
    """
    with Session(engine) as db:
        cams = db.exec(select(Camera).where(Camera.enabled == True).order_by(Camera.id)).all()  # noqa: E712
        return tuple(
            (
                cam.id,
                cam.source_kind,
                cam.source_enc,
                cam.video_id,
                cam.mode,
                cam.recording_start.isoformat() if cam.recording_start else "",
                float(cam.sample_rate or 0.0),
            )
            for cam in cams
            if not (cam.source_kind == "video" and cam.mode == "analisis")
        )


def _reload_pipeline(reason: str) -> None:
    """Restart scheduler live agar daftar kamera aktif mengikuti database terbaru."""
    global _camera_signature, _live_detector
    with runner_lock:
        log.info("Memuat ulang kamera pipeline: %s", reason)
        if _live_detector is None:
            _live_detector = _detector()
        _stop_pipeline()
        _start_pipeline(detector=_live_detector)
        _camera_signature = _live_camera_signature()
        log.info("Reload kamera selesai: %s kamera aktif", len(_camera_signature))


def _changed_camera_ids(old: tuple[tuple, ...], new: tuple[tuple, ...]) -> list[str]:
    """ID kamera yang ditambah, berubah, atau hilang antara dua signature."""
    old_map = {row[0]: row for row in old}
    new_map = {row[0]: row for row in new}
    changed = {cid for cid, row in new_map.items() if old_map.get(cid) != row}
    changed |= {cid for cid in old_map if cid not in new_map}
    return sorted(changed)


async def camera_reload_loop(interval_s: int = 5):
    """Polling perubahan kamera; reload hanya kamera yang berubah, bukan seluruh pipeline."""
    global _camera_signature
    while True:
        await asyncio.sleep(interval_s)
        try:
            latest = _live_camera_signature()
            changed = _changed_camera_ids(_camera_signature, latest)
            for cid in changed:
                _reconcile_live_camera(cid, detector=_live_detector)
            if changed:
                _camera_signature = latest
                log.info("Reload kamera selesai: %s kamera aktif", len(_camera_signature))
        except Exception:
            log.error("Gagal memeriksa perubahan kamera", exc_info=True)


async def retention_loop():
    while True:
        try:
            await asyncio.to_thread(backup.run)
        except Exception:
            log.error("Backup error", exc_info=True)
        try:
            cfg = config.load_config().get("retention", {})
            await asyncio.to_thread(
                retention.run,
                evidence_days=int(cfg.get("evidence_days", 90)),
                quality_days=int(cfg.get("quality_days", 30)),
                dry=False,
                video_days=int(cfg.get("video_days", 14)),
            )
        except Exception:
            log.error("Retention error", exc_info=True)
        await asyncio.sleep(86400)


async def heartbeat_loop(interval_s: int = 5):
    """Terbitkan liveness worker tanpa mengekspos konfigurasi kamera atau secret."""
    while True:
        try:
            worker_health.publish(
                started_at=_started_at,
                pipeline_enabled=config.RUN_PIPELINE,
                cameras=len(_camera_signature),
            )
        except Exception:
            log.error("Gagal menulis heartbeat worker", exc_info=True)
        await asyncio.sleep(interval_s)


async def main():
    global poller
    logging.basicConfig(level=logging.INFO)
    if getattr(config, "PRODUCTION", False):
        config.validate_production_config()
    init_db()
    worker_health.publish(started_at=_started_at, pipeline_enabled=config.RUN_PIPELINE, cameras=0)

    if config.RUN_PIPELINE:
        _reload_pipeline("startup worker")
        with Session(engine) as db:
            for j in db.exec(select(AnalysisJob).where(AnalysisJob.status == "berjalan")).all():
                j.status = "menunggu"
                db.add(j)
            db.commit()
        jobs.start()
        asyncio.create_task(camera_reload_loop())

    if telegram_enabled() and config.TELEGRAM_POLLING:
        poller = TelegramPoller()
        poller.start()

    asyncio.create_task(retention_loop())
    asyncio.create_task(heartbeat_loop())

    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Worker dihentikan.")
        active_poller = poller
        if active_poller is not None:
            active_poller.stop()
        jobs.stop()
        sys.exit(0)
