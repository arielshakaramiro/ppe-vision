"""Penghubung pipeline -> database + alert."""
from __future__ import annotations

import logging
import time
from datetime import datetime

from sqlmodel import Session, col, or_, select

from pipeline.evidence import SavedEvidence
from pipeline.state import ActiveEvent, TrackState

from . import alerts
from .config import EVIDENCE_DIR, load_config
from .db import QualityStat, TrackRecord, Violation, engine
from .health import camera_health
from .queries import now_local

log = logging.getLogger("ppe.sink")


def _csv(items) -> str:
    return ",".join(sorted(items))


class DbSink:
    def __init__(self, session_id: str | None = None, model_version: str | None = None,
                 source_kind: str = "live"):
        self.session_id = session_id or now_local().strftime("%Y%m%d-%H%M%S")
        self.model_version = model_version or str(load_config()["models"].get("version", "v1"))
        self.source_kind = source_kind  # live | rekaman | simulasi
        self._fp_cache: dict[tuple[str, int], tuple[float, set[str]]] = {}

    def _get_supervisor_false_positive_items(self, camera_id: str, track: TrackState) -> set[str]:
        now = time.monotonic()
        wid = track.display_id()
        cached = self._fp_cache.get((camera_id, wid))
        if cached and (now - cached[0] < 5.0):
            return cached[1]

        base_key = track.person_key.split(":", 1)[0] if track.person_key else f"p{wid}"
        with Session(engine) as db:
            q = select(Violation.missing_items, Violation.corrected_items).where(
                Violation.camera_id == camera_id,
                Violation.review == "false_positive",
                or_(col(Violation.worker_id) == wid, col(Violation.person_key).like(f"{base_key}%"))
            )
            rows = db.exec(q).all()
            suppressed: set[str] = set()
            for missing, corrected in rows:
                items_str = corrected or missing
                for item in items_str.split(","):
                    item = item.strip()
                    if item:
                        suppressed.add(item)
            self._fp_cache[(camera_id, wid)] = (now, suppressed)
            return suppressed

    def on_open(self, camera_id: str, track: TrackState, ev: SavedEvidence) -> int:
        e = track.event
        if e is None:
            raise RuntimeError("Event track belum terbuka")

        # Active Learning Feedback: sesuaikan deteksi dengan koreksi supervisor sebelumnya
        fp_items = self._get_supervisor_false_positive_items(camera_id, track)
        effective_missing = set(e.missing) - fp_items

        if fp_items and not effective_missing and e.kind == "violation":
            # Seluruh APD yang dilaporkan kurang telah dikoreksi oleh pengawas sebagai false positive!
            # Terapkan pembelajaran aktif (active learning feedback):
            # 1. Sesuaikan status track di memori menjadi patuh (compliant)
            for item in fp_items:
                if item in track.items:
                    track.items[item].status = "present"
                track.missing_ever.discard(item)
            track.stable_status = "compliant"
            track.worst_status = "compliant"
            track.event = None  # tutup event

            # 2. Catat ke database sebagai false positive yang sudah dipelajari, TANPA mengirim alert baru
            row = Violation(
                session=self.session_id, camera_id=camera_id, source_kind=self.source_kind,
                zone=track.zone, track_id=track.track_id, worker_id=track.display_id(), person_key=track.person_key,
                kind="uncertain", started_at=e.started_at, missing_items="",
                uncertain_items="", confidence=round(track.conf, 3),
                evidence_full=ev.full_path, evidence_crop=ev.crop_path, evidence_clean=ev.clean_path,
                evidence_sha256=ev.sha256, detections_json=ev.detections_json,
                model_version=self.model_version,
                review="false_positive", reviewed_at=now_local(),
                corrected_items=_csv(fp_items)
            )
            with Session(engine) as db:
                db.add(row)
                db.commit()
                db.refresh(row)
            log.info("Active Feedback K3: Pelanggaran W%03d di %s ditekan otomatis karena telah dikoreksi pengawas sebagai false positive (%s).",
                     track.display_id(), camera_id, ", ".join(fp_items))
            return int(row.id or 0)

        reported_missing = effective_missing if effective_missing else e.missing
        row = Violation(
            session=self.session_id, camera_id=camera_id, source_kind=self.source_kind,
            zone=track.zone, track_id=track.track_id, worker_id=track.display_id(), person_key=track.person_key,
            kind=e.kind if effective_missing else "uncertain", started_at=e.started_at, missing_items=_csv(reported_missing),
            uncertain_items=_csv(e.uncertain), confidence=round(track.conf, 3),
            evidence_full=ev.full_path, evidence_crop=ev.crop_path, evidence_clean=ev.clean_path,
            evidence_sha256=ev.sha256, detections_json=ev.detections_json,
            model_version=self.model_version,
        )
        with Session(engine) as db:
            db.add(row)
            db.commit()
            db.refresh(row)
        event_id = int(row.id or 0)
        if e.kind == "violation" and effective_missing:
            alerts.send_violation_alert(EVIDENCE_DIR / ev.full_path, camera_id, track.zone, track.display_id(),
                                        e.started_at, sorted(effective_missing), event_id=event_id)
        return event_id

    def on_close(self, event_id: int, event: ActiveEvent, ended_at: datetime) -> None:
        with Session(engine) as db:
            row = db.get(Violation, event_id)
            if row:
                row.ended_at = ended_at
                row.duration_s = (ended_at - row.started_at).total_seconds()
                row.missing_items = _csv(event.missing)
                row.uncertain_items = _csv(event.uncertain)
                db.add(row)
                db.commit()

    def on_track_lost(self, camera_id: str, track: TrackState) -> None:
        if not track.stable_status:
            return  # track terlalu singkat untuk dinilai (biasanya noise)
        with Session(engine) as db:
            db.add(TrackRecord(
                session=self.session_id, camera_id=camera_id, source_kind=self.source_kind,
                zone=track.zone, track_id=track.track_id, worker_id=track.display_id(), person_key=track.person_key,
                first_seen=track.first_seen, last_seen=track.last_seen,
                final_status=track.worst_status, missing_items=_csv(track.missing_ever),
            ))
            db.commit()

    def on_clip(self, event_id: int, rel_path: str, sha256: str) -> None:
        with Session(engine) as db:
            row = db.get(Violation, event_id)
            if row:
                row.evidence_clip, row.evidence_clip_sha256 = rel_path, sha256
                db.add(row)
                db.commit()

    def on_quality(self, camera_id: str, stats: dict) -> None:
        with Session(engine) as db:
            db.add(QualityStat(camera_id=camera_id, **stats))
            db.commit()
            health = camera_health(db, camera_id, stats["minute"])
        if health["status"] == "peringatan":
            alerts.send_camera_alert(camera_id, health["masalah"])
