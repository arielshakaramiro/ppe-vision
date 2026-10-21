"""State per pekerja: hysteresis per item APD + event pelanggaran.

Skor tiap item naik saat APD terlihat dipakai, turun saat bagian tubuh terlihat
tanpa APD, dan meluruh setiap frame. Status baru berubah setelah melewati ambang,
jadi satu frame deteksi yang meleset tidak langsung jadi pelanggaran.

Status orang:
- compliant : semua APD wajib terkonfirmasi dipakai
- violation : minimal satu APD wajib terkonfirmasi tidak dipakai
- uncertain : tidak ada yang kurang, tapi ada APD wajib yang tidak bisa dinilai
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
from datetime import datetime

from .types import ITEMS, Box, Evidence, Person, area, intersection, keypoints_match_same_person

SEVERITY = {"compliant": 0, "uncertain": 1, "violation": 2}


def _iou(a: Box, b: Box) -> float:
    u = area(a) + area(b) - intersection(a, b)
    return intersection(a, b) / u if u > 0 else 0.0


def _center_dist(a: Box, b: Box) -> float:
    ax, ay = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    w = max(a[2] - a[0], b[2] - b[0], 1.0)
    h = max(a[3] - a[1], b[3] - b[1], 1.0)
    return (((ax - bx) / w) ** 2 + ((ay - by) / h) ** 2) ** 0.5


def _new_person_key(track_id: int, first_seen: datetime) -> str:
    return f"p{track_id}:{first_seen:%Y%m%d%H%M%S%f}"


@dataclass
class HysteresisConfig:
    decay: float = 0.90
    present_threshold: float = 1.0
    missing_threshold: float = -1.5
    min_evaluations: int = 5
    unknown_after_frames: int = 45
    event_min_frames: int = 8
    uncertain_min_frames: int = 30
    event_close_grace_frames: int = 12
    track_lost_frames: int = 30
    reentry_enabled: bool = False
    reentry_max_gap_frames: int = 90
    reentry_min_iou: float = 0.15
    reentry_max_center_dist: float = 1.5
    duplicate_active_min_iou: float = 0.80
    duplicate_active_max_center_dist: float = 0.25


@dataclass
class ItemState:
    score: float = 0.0
    evaluations: int = 0
    frames_without_evidence: int = 0
    status: str = "unknown"  # present | missing | unknown

    def update(self, value: float | None, cfg: HysteresisConfig) -> None:
        self.score *= cfg.decay
        if value is None:
            self.frames_without_evidence += 1
        else:
            self.frames_without_evidence = 0
            self.evaluations += 1
            self.score = max(-3.0, min(3.0, self.score + value))

        if self.frames_without_evidence >= cfg.unknown_after_frames:
            self.status = "unknown"
        elif self.score >= cfg.present_threshold:
            self.status = "present"
        elif self.score <= cfg.missing_threshold and self.evaluations >= cfg.min_evaluations:
            self.status = "missing"
        # di antara dua ambang: status sebelumnya dipertahankan (hysteresis)


@dataclass
class ActiveEvent:
    kind: str  # violation | uncertain
    started_at: datetime
    missing: set[str] = field(default_factory=set)
    uncertain: set[str] = field(default_factory=set)
    db_id: int | None = None


@dataclass
class TrackState:
    track_id: int
    first_seen: datetime
    last_seen: datetime
    last_frame: int
    box: Box
    conf: float
    keypoints: object | None = None
    worker_id: int = 0
    zone: str = ""
    required: tuple[str, ...] = ITEMS
    items: dict[str, ItemState] = field(default_factory=lambda: {i: ItemState() for i in ITEMS})
    pending_status: str = ""
    streak: int = 0
    stable_status: str = ""  # kosong sampai status pertama kali stabil
    worst_status: str = "compliant"
    event: ActiveEvent | None = None
    event_clear_streak: int = 0
    missing_ever: set[str] = field(default_factory=set)
    person_key: str = ""

    def raw_status(self) -> str:
        req = [self.items[i] for i in self.required]
        if any(s.status == "missing" for s in req):
            return "violation"
        if any(s.status == "unknown" for s in req):
            return "uncertain"
        return "compliant"

    def missing_items(self) -> set[str]:
        return {i for i in self.required if self.items[i].status == "missing"}

    def unknown_items(self) -> set[str]:
        return {i for i in self.required if self.items[i].status == "unknown"}

    def display_id(self) -> int:
        # ID negatif dipakai internal runner untuk fallback pose saat ByteTrack menolak pekerja
        # parsial/tertutup. Label operator dan `worker_id` database tetap harus positif.
        return abs(self.worker_id or self.track_id)

    def worker_label(self) -> str:
        return f"W{self.display_id():03d}"

    def checklist(self) -> dict[str, str]:
        return {i: (self.items[i].status if i in self.required else "not_required") for i in ITEMS}


@dataclass
class StepResult:
    opened: list[TrackState] = field(default_factory=list)
    closed: list[tuple[TrackState, ActiveEvent, datetime]] = field(default_factory=list)
    lost: list[TrackState] = field(default_factory=list)


class StateManager:
    def __init__(self, cfg: HysteresisConfig):
        self.cfg = cfg
        self.tracks: dict[int, TrackState] = {}
        self.recent_lost: list[TrackState] = []

    def _canonical_track(self, a: TrackState, b: TrackState) -> tuple[TrackState, TrackState]:
        """Pilih identitas kanonis saat dua track terbukti orang yang sama."""
        a_key = (a.first_seen, a.display_id(), a.track_id)
        b_key = (b.first_seen, b.display_id(), b.track_id)
        return (a, b) if a_key <= b_key else (b, a)

    def _merge_track_state(self, keep: TrackState, drop: TrackState, ended_at: datetime,
                           res: StepResult | None = None) -> None:
        """Gabungkan status APD/event dari track duplikat ke identitas kanonis."""
        for item in ITEMS:
            keep_item = keep.items[item]
            drop_item = drop.items[item]
            if abs(drop_item.score) > abs(keep_item.score):
                keep_item.score = drop_item.score
            keep_item.evaluations = max(keep_item.evaluations, drop_item.evaluations)
            keep_item.frames_without_evidence = min(keep_item.frames_without_evidence, drop_item.frames_without_evidence)
            if keep_item.status == "unknown" and drop_item.status != "unknown":
                keep_item.status = drop_item.status

        if SEVERITY[drop.worst_status] > SEVERITY[keep.worst_status]:
            keep.worst_status = drop.worst_status
        if not keep.stable_status or SEVERITY.get(drop.stable_status or "", -1) > SEVERITY.get(keep.stable_status, -1):
            keep.stable_status = drop.stable_status
        keep.missing_ever |= drop.missing_ever

        if drop.event:
            if keep.event:
                keep.event.missing |= drop.event.missing
                keep.event.uncertain |= drop.event.uncertain
            else:
                keep.missing_ever |= drop.event.missing
            if res is not None and drop.event.db_id is not None:
                res.closed.append((drop, drop.event, ended_at))
            drop.event = None

    def _merge_existing_duplicate_track(self, p: Person, frame_idx: int, now: datetime, zone: str,
                                        current_ids: set[int], consumed_ids: set[int],
                                        res: StepResult) -> TrackState | None:
        """Satukan dua track aktif yang sudah terlanjur hidup sebagai orang berbeda.

        Re-entry bisa gagal pada beberapa frame pertama oklusi sehingga ID baru sempat dibuat
        sebagai pekerja baru. Ketika keypoint/box berikutnya membuktikan keduanya orang yang sama,
        identitas lama wajib dipertahankan agar label seperti W024/W037 tidak berjalan paralel.
        """
        current = self.tracks.get(p.track_id)
        if current is None:
            return None
        if not self.cfg.reentry_enabled:
            return current
        best_tid = None
        best = None
        best_score = -1.0
        for tid, other in self.tracks.items():
            if tid == p.track_id or tid not in current_ids or tid in consumed_ids:
                continue
            if frame_idx - other.last_frame > max(1, self.cfg.track_lost_frames):
                continue
            if zone and other.zone and zone != other.zone:
                continue
            iou = _iou(p.box, other.box)
            dist = _center_dist(p.box, other.box)
            same_pose = keypoints_match_same_person(p.keypoints, other.keypoints, p.box, other.box)
            if not same_pose and iou < self.cfg.duplicate_active_min_iou and dist > self.cfg.duplicate_active_max_center_dist:
                continue
            score = iou - dist * 0.05 + (0.5 if same_pose else 0.0)
            if score > best_score:
                best_tid, best, best_score = tid, other, score
        if best_tid is None or best is None:
            return current

        keep, drop = self._canonical_track(current, best)
        self._merge_track_state(keep, drop, now, res)
        drop_tid = drop.track_id
        if drop_tid in self.tracks:
            del self.tracks[drop_tid]
        consumed_ids.add(drop_tid)
        self.tracks[keep.track_id] = keep
        return keep if keep.track_id == p.track_id else None

    def _match_lost(self, p: Person, frame_idx: int, zone: str) -> TrackState | None:
        if not self.cfg.reentry_enabled:
            return None
        best = None
        best_score = -1.0
        for old in self.recent_lost:
            gap = frame_idx - old.last_frame
            if gap <= 0 or gap > self.cfg.reentry_max_gap_frames:
                continue
            if zone and old.zone and zone != old.zone:
                continue
            iou = _iou(p.box, old.box)
            dist = _center_dist(p.box, old.box)
            same_pose = keypoints_match_same_person(p.keypoints, old.keypoints, p.box, old.box)
            if not same_pose and iou < self.cfg.reentry_min_iou and dist > self.cfg.reentry_max_center_dist:
                continue
            score = iou - dist * 0.05 + (0.5 if same_pose else 0.0)
            if score > best_score:
                best, best_score = old, score
        if best:
            self.recent_lost.remove(best)
        return best

    def _match_inactive_track(self, p: Person, frame_idx: int, zone: str, current_ids: set[int]) -> TrackState | None:
        """Sambungkan ID ByteTrack baru ke track aktif yang baru saja tidak muncul.

        ByteTrack kadang mengganti ID walau orangnya masih sama. Jika track lama belum melewati
        `track_lost_frames`, ia belum masuk `recent_lost`, sehingga tanpa pencocokan ini satu pekerja
        bisa dihitung sebagai dua identitas dan membuka evidence pelanggaran yang sama lagi.
        """
        if not self.cfg.reentry_enabled:
            return None
        best_tid = None
        best = None
        best_score = -1.0
        for tid, old in self.tracks.items():
            if tid in current_ids:
                continue
            gap = frame_idx - old.last_frame
            if gap <= 0 or gap > self.cfg.reentry_max_gap_frames:
                continue
            if zone and old.zone and zone != old.zone:
                continue
            iou = _iou(p.box, old.box)
            dist = _center_dist(p.box, old.box)
            same_pose = keypoints_match_same_person(p.keypoints, old.keypoints, p.box, old.box)
            if not same_pose and iou < self.cfg.reentry_min_iou and dist > self.cfg.reentry_max_center_dist:
                continue
            score = iou - dist * 0.05 + (0.5 if same_pose else 0.0)
            if score > best_score:
                best_tid, best, best_score = tid, old, score
        if best_tid is not None:
            del self.tracks[best_tid]
        return best

    def _duplicate_active_track(self, p: Person, frame_idx: int, zone: str, current_ids: set[int],
                                consumed_ids: set[int]) -> TrackState | None:
        """Cari track aktif yang sangat berimpit dengan ID baru pada frame yang sama.

        Pada beberapa footage, ByteTrack sesaat mengembalikan ID lama dan ID baru untuk orang yang
        sama. Jika keduanya diproses, state membuka dua pekerja/evidence paralel. Fungsi ini hanya
        menggabungkan kandidat yang overlap sangat tinggi dan zona sama, sehingga dua pekerja yang
        benar-benar berdampingan tidak mudah tercampur.
        """
        if not self.cfg.reentry_enabled:
            return None
        best_tid = None
        best = None
        best_score = -1.0
        for tid, old in self.tracks.items():
            if tid == p.track_id or tid not in current_ids or tid in consumed_ids:
                continue
            if frame_idx - old.last_frame > max(1, self.cfg.track_lost_frames):
                continue
            if zone and old.zone and zone != old.zone:
                continue
            iou = _iou(p.box, old.box)
            dist = _center_dist(p.box, old.box)
            same_pose = keypoints_match_same_person(p.keypoints, old.keypoints, p.box, old.box)
            if not same_pose and iou < self.cfg.duplicate_active_min_iou and dist > self.cfg.duplicate_active_max_center_dist:
                continue
            score = iou - dist * 0.05 + (0.5 if same_pose else 0.0)
            if score > best_score:
                best_tid, best, best_score = tid, old, score
        if best_tid is not None:
            del self.tracks[best_tid]
            consumed_ids.add(best_tid)
        return best

    def step(
        self,
        frame_idx: int,
        now: datetime,
        persons: list,  # list[Person]
        evidence: Mapping[int, Evidence],
        zones: Mapping[int, tuple[str, tuple[str, ...]]],
        evaluated: bool = True,
    ) -> StepResult:
        res = StepResult()
        cfg = self.cfg
        current_ids = {p.track_id for p in persons}
        consumed_duplicate_ids: set[int] = set()

        for p in persons:
            zone, required = zones.get(p.track_id, ("", ITEMS))
            if p.track_id in consumed_duplicate_ids:
                continue
            t = self.tracks.get(p.track_id)
            if t is not None:
                t = self._merge_existing_duplicate_track(p, frame_idx, now, zone, current_ids,
                                                         consumed_duplicate_ids, res)
                if t is None or p.track_id in consumed_duplicate_ids:
                    continue
            if t is None:
                t = (self._duplicate_active_track(p, frame_idx, zone, current_ids, consumed_duplicate_ids)
                     or self._match_inactive_track(p, frame_idx, zone, current_ids)
                     or self._match_lost(p, frame_idx, zone)
                     or TrackState(p.track_id, now, now, frame_idx, p.box, p.conf, p.keypoints))
                if not t.worker_id:
                    t.worker_id = t.track_id or p.track_id
                t.track_id = p.track_id
                if not t.person_key:
                    t.person_key = _new_person_key(t.display_id(), t.first_seen)
                self.tracks[p.track_id] = t
            if not t.worker_id:
                t.worker_id = t.track_id or p.track_id
            if not t.person_key:
                t.person_key = _new_person_key(t.display_id(), t.first_seen)
            t.last_seen, t.last_frame, t.box, t.conf, t.keypoints = now, frame_idx, p.box, p.conf, p.keypoints
            t.zone, t.required = zone, required

            if not evaluated:
                # Tetap perbarui lokasi/lifecycle saja. Frame tanpa evaluasi APD tidak boleh
                # mengubah skor, streak, atau status; jika tidak, analisis rekaman yang menjalankan
                # pose tiap frame tetapi PPE per sample_rate akan membuat status/event goyah.
                continue

            ev = evidence.get(p.track_id, Evidence())
            for item in ITEMS:
                t.items[item].update(ev.values.get(item), cfg)

            status = t.raw_status()
            if status == t.pending_status:
                t.streak += 1
            else:
                t.pending_status, t.streak = status, 1
            need = cfg.uncertain_min_frames if t.pending_status == "uncertain" else cfg.event_min_frames
            if t.streak >= need:
                t.stable_status = t.pending_status
            if not t.stable_status:
                continue

            if SEVERITY[t.stable_status] > SEVERITY[t.worst_status]:
                t.worst_status = t.stable_status

            # buka / tutup event sesuai status stabil
            if t.event and t.event.kind != t.stable_status:
                t.event_clear_streak += 1
                if t.event_clear_streak < cfg.event_close_grace_frames:
                    continue
                res.closed.append((t, t.event, now))
                t.event = None
                t.event_clear_streak = 0
            elif t.event:
                t.event_clear_streak = 0
            if t.event is None and t.stable_status in ("violation", "uncertain"):
                t.event = ActiveEvent(t.stable_status, now)
                res.opened.append(t)
            if t.event:
                t.event.missing |= t.missing_items()
                t.event.uncertain |= t.unknown_items()
                t.missing_ever |= t.event.missing

        for tid in list(self.tracks):
            t = self.tracks[tid]
            if frame_idx - t.last_frame > cfg.track_lost_frames:
                if t.event:
                    res.closed.append((t, t.event, t.last_seen))
                    t.event = None
                res.lost.append(t)
                if cfg.reentry_enabled:
                    self.recent_lost.append(t)
                    self.recent_lost = [x for x in self.recent_lost if frame_idx - x.last_frame <= cfg.reentry_max_gap_frames]
                del self.tracks[tid]
        return res

    def flush(self) -> StepResult:
        """Tutup semua track (misalnya saat video selesai)."""
        res = StepResult()
        for t in self.tracks.values():
            if t.event:
                res.closed.append((t, t.event, t.last_seen))
                t.event = None
            res.lost.append(t)
        self.tracks.clear()
        return res
