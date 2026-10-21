import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import cv2
import numpy as np

from ultralytics.trackers.byte_tracker import BYTETracker
from ultralytics.utils import IterableSimpleNamespace, YAML
from ultralytics.utils.checks import check_yaml

from typing import Protocol

from .association import AssocConfig, associate
from .clips import ClipRecorder
from .draw import blur_region, draw_person, draw_ppe_items, draw_zones, head_box
from .evidence import SavedEvidence, heads_for, save_evidence
from .quality import QualityTracker
from .state import ActiveEvent, HysteresisConfig, StateManager, TrackState
from .types import Box, Evidence, ITEMS, Person, area, intersection, keypoints_match_same_person
from .zones import load_zones, zone_for
from .camera import CameraReader


class EventSink(Protocol):
    def on_open(self, camera_id: str, track: TrackState, ev: SavedEvidence) -> int: ...
    def on_close(self, event_id: int, event: ActiveEvent, ended_at) -> None: ...
    def on_track_lost(self, camera_id: str, track: TrackState) -> None: ...
    def on_clip(self, event_id: int, rel_path: str, sha256: str) -> None: ...
    def on_quality(self, camera_id: str, stats: dict) -> None: ...

import json
import tempfile

def mask_source(src: str) -> str:
    from urllib.parse import urlparse
    if not src.startswith(("rtsp://", "http://", "https://")):
        return Path(src).name
    p = urlparse(src)
    return f"{p.scheme}://***@{p.hostname}{p.path}"


def _percent_box(box: Box, width: int, height: int) -> dict[str, float]:
    x1 = max(0.0, min(float(width), box[0]))
    y1 = max(0.0, min(float(height), box[1]))
    x2 = max(x1, min(float(width), box[2]))
    y2 = max(y1, min(float(height), box[3]))
    return {
        "x": round(x1 / width * 100, 3), "y": round(y1 / height * 100, 3),
        "w": round((x2 - x1) / width * 100, 3), "h": round((y2 - y1) / height * 100, 3),
    }


def _evidence_detections(track: TrackState, person: Person, evidence: Evidence | None,
                         width: int, height: int) -> str:
    checklist = track.checklist()
    items = []
    for key in ITEMS:
        detections = evidence.detections.get(key, []) if evidence else []
        items.append({
            "key": key, "status": checklist[key],
            "boxes": [
                {"box": _percent_box(det.box, width, height), "confidence": round(det.conf, 3)}
                for det in detections
            ],
        })
    return json.dumps({
        "frame": {"width": width, "height": height},
        "worker": {"box": _percent_box(person.box, width, height),
                   "confidence": round(person.conf, 3)},
        "items": items,
    })


def _box_iou(a: Box, b: Box) -> float:
    u = area(a) + area(b) - intersection(a, b)
    return intersection(a, b) / u if u > 0 else 0.0


def _box_center_dist(a: Box, b: Box) -> float:
    ax, ay = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    w = max(a[2] - a[0], b[2] - b[0], 1.0)
    h = max(a[3] - a[1], b[3] - b[1], 1.0)
    return (((ax - bx) / w) ** 2 + ((ay - by) / h) ** 2) ** 0.5


def _smooth_box(previous: Box | None, current: Box, alpha: float) -> Box:
    if previous is None or alpha >= 1.0:
        return (float(current[0]), float(current[1]), float(current[2]), float(current[3]))
    if alpha <= 0.0:
        return (float(previous[0]), float(previous[1]), float(previous[2]), float(previous[3]))
    return (
        float(previous[0] + (current[0] - previous[0]) * alpha),
        float(previous[1] + (current[1] - previous[1]) * alpha),
        float(previous[2] + (current[2] - previous[2]) * alpha),
        float(previous[3] + (current[3] - previous[3]) * alpha),
    )


def _smooth_keypoints(previous, current, alpha: float):
    if current is None:
        return previous
    curr = np.array(current, copy=True)
    if previous is None or alpha >= 1.0:
        return curr
    prev = np.array(previous, copy=True)
    if prev.shape != curr.shape:
        return curr
    return prev + (curr - prev) * max(0.0, min(1.0, alpha))


def _boxes_match(a: Box, b: Box, min_iou: float, max_center_dist: float) -> bool:
    return _box_iou(a, b) >= min_iou or _box_center_dist(a, b) <= max_center_dist


def _track_display_id(track) -> int:
    if isinstance(track, TrackState):
        return track.display_id()
    worker_id = getattr(track, "worker_id", 0)
    if isinstance(worker_id, int) and worker_id > 0:
        return worker_id
    track_id = getattr(track, "track_id", 0)
    return track_id if isinstance(track_id, int) else 0


class LiveState:
    def __init__(self, camera_id: str = "default"):
        self.camera_id = camera_id
        self.jpeg: bytes | None = None
        self.clean_jpeg: bytes | None = None
        self.people: list[dict] = []
        self.fps: float = 0.0
        self.running: bool = False
        self.error: str | None = None
        self.lock = threading.Lock()
        self.tmp_dir = Path(tempfile.gettempdir()) / "ppe_live"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def update(self, jpeg: bytes | None, people: list, fps: float, metrics: dict | None = None,
               clean_jpeg: bytes | None = None):
        with self.lock:
            if jpeg: self.jpeg = jpeg
            if clean_jpeg: self.clean_jpeg = clean_jpeg
            self.people = people
            self.fps = fps
        try:
            if jpeg:
                self._atomic_write(self.tmp_dir / f"{self.camera_id}.jpg", jpeg)
            if clean_jpeg:
                self._atomic_write(self.tmp_dir / f"{self.camera_id}.clean.jpg", clean_jpeg)
            payload = {"people": people, "fps": fps, "running": self.running, "error": self.error}
            if metrics:
                payload.update(metrics)
            self._atomic_write(self.tmp_dir / f"{self.camera_id}.json", json.dumps(payload).encode())
        except Exception:
            pass

    def _atomic_write(self, path: Path, data: bytes) -> None:
        tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

class CameraSession:
    def __init__(self, camera_id: str, cfg: dict, sink: EventSink, live: LiveState | None = None,
                 base_dir: Path = Path("."), zone_provider=None):
        self.camera_id = camera_id
        self.cfg = cfg
        self.sink = sink
        # setiap kamera punya LiveState sendiri; satu kamera tidak menimpa frame kamera lain
        self.live = live if live is not None else LiveState(camera_id)
        self.base = base_dir
        self.tz = ZoneInfo(cfg.get("timezone", "Asia/Jakarta"))

        static_zones = load_zones(cfg.get("zones", []))
        self.zone_provider = zone_provider or (lambda: static_zones)
        self.assoc_cfg = AssocConfig(**cfg.get("association", {}))
        hyst = dict(cfg.get("hysteresis", {}))
        tracker_cfg = cfg.get("tracking", {}) or {}
        for k in (
            "reentry_enabled", "reentry_max_gap_frames", "reentry_min_iou", "reentry_max_center_dist",
            "duplicate_active_min_iou", "duplicate_active_max_center_dist",
        ):
            if k in tracker_cfg:
                hyst[k] = tracker_cfg[k]
        self.manager = StateManager(HysteresisConfig(**hyst))
        self.evidence_dir = base_dir / cfg.get("evidence", {}).get("dir", "data/evidence")
        self.blur = cfg.get("evidence", {}).get("blur_faces", True)
        overlay_cfg = cfg.get("overlay", {}) or {}
        self.draw_raw_ppe_boxes = bool(overlay_cfg.get("raw_ppe_boxes", False))
        self.draw_check_regions = bool(overlay_cfg.get("check_regions", False))
        default_recorded_hold = int(hyst.get("track_lost_frames", 30))
        self.recorded_overlay_hold_inference_frames = max(
            0,
            int(overlay_cfg.get("recorded_hold_inference_frames", default_recorded_hold)),
        )
        self.recorded_overlay_hold_seconds = max(
            0.0,
            float(overlay_cfg.get("recorded_hold_seconds", 3.0)),
        )
        self.recorded_output_fps = 25.0
        self.recorded_overlay_smooth_alpha = max(
            0.0,
            min(1.0, float(overlay_cfg.get("recorded_smooth_alpha", 0.35))),
        )
        self.recorded_overlay_match_min_iou = max(
            0.0,
            min(1.0, float(overlay_cfg.get("recorded_match_min_iou", 0.20))),
        )
        self.recorded_overlay_match_max_center_dist = max(
            0.0,
            float(overlay_cfg.get("recorded_match_max_center_dist", 0.80)),
        )

        self.ppe_every_n = max(1, int(cfg.get("models", {}).get("ppe_every_n", 1)))
        self.frame_idx = 0
        self._last_items = None
        stream_cfg = cfg.get("stream", {}) or {}
        self.stream_target_fps = max(1.0, min(30.0, float(stream_cfg.get("target_fps", 24))))
        default_overlay_age = int(round(self.stream_target_fps * 0.5))
        self.overlay_max_age_frames = max(0, int(stream_cfg.get("overlay_max_age_frames", default_overlay_age)))
        self.display_raw_when_stale = bool(stream_cfg.get("display_raw_when_stale", True))
        # Blur khusus tampilan live (stream MJPEG). Terpisah dari `self.blur` yang mengatur evidence/klip:
        # mematikan ini membuat wajah terlihat di layar operator, tetapi foto evidence dan klip tetap diblur.
        # Default mengikuti `blur_faces` agar perilaku lama tidak berubah bila flag ini tidak diset.
        self.blur_live = bool(stream_cfg.get("blur_faces_live", self.blur))
        self._render_lock = threading.Lock()
        self._last_people = []
        self._last_live_confidence: dict[int, dict[str, float]] = {}
        self._held_overlay_people = []
        self._recorded_overlay_snapshots = {}
        self._recorded_mode = getattr(sink, "source_kind", "") == "rekaman"
        self._last_overlay_seq: int | None = None
        self._fallback_tracks: dict[int, dict] = {}
        self._next_fallback_track_id = -1

        # Tracker ByteTrack. Nilai YAML bawaan Ultralytics bisa ditimpa dari config.yaml -> tracking.
        tracker_yaml = check_yaml("bytetrack.yaml")
        track_args = dict(YAML().load(tracker_yaml))
        for k in ("track_buffer", "match_thresh", "track_high_thresh", "track_low_thresh", "new_track_thresh"):
            if k in tracker_cfg:
                track_args[k] = tracker_cfg[k]
        self.tracker = BYTETracker(args=IterableSimpleNamespace(**track_args))

        clip_cfg = cfg.get("evidence", {}).get("clip", {})
        self.clips = None
        if clip_cfg.get("enabled") and hasattr(sink, "on_clip"):
            self.clips = ClipRecorder(float(clip_cfg.get("before_s", 2)), float(clip_cfg.get("after_s", 3)),
                                      int(clip_cfg.get("width", 640)), on_done=self._clip_done)
        self.quality = QualityTracker() if cfg.get("quality", {}).get("enabled") and hasattr(sink, "on_quality") else None

        # Reader
        source = str(cfg.get("camera", {}).get("source", ""))
        self.reader = CameraReader(camera_id, source, loop=cfg.get("camera", {}).get("loop", False),
                                   realtime=cfg.get("camera", {}).get("realtime", False), base_dir=base_dir)

        self.video_start = None
        start = cfg.get("camera", {}).get("start_time")
        if start:
            self.video_start = datetime.fromisoformat(str(start)).replace(tzinfo=None)
        self.src_fps = 25.0  # dipakai untuk waktu event rekaman; diisi dari fps kamera saat tersedia
        self._active_zones = self.zone_provider()
        self._stats_lock = threading.Lock()
        self.stats = {"processed_frames": 0, "batch_times": [], "processed_at": deque(maxlen=500)}
        self._frame_size = {"width": 0, "height": 0}

    def set_recorded_output_fps(self, fps: float) -> None:
        """Set FPS video hasil rekaman untuk menghitung umur overlay dalam detik."""
        try:
            self.recorded_output_fps = max(1.0, min(60.0, float(fps)))
        except (TypeError, ValueError):
            self.recorded_output_fps = 25.0

    def start(self):
        self.live.running = True
        self.reader.start()
        self._display_stop = threading.Event()
        self._display_thread = threading.Thread(target=self._display_loop, daemon=True, name=f"display-{self.camera_id}")
        self._display_thread.start()

    def stop(self):
        was_running = self.live.running
        self.live.running = False
        if hasattr(self, "_display_stop"):
            self._display_stop.set()
        self.reader.stop()
        if hasattr(self, "_display_thread") and self._display_thread.is_alive():
            self._display_thread.join(timeout=2)
        # Worker rekaman menutup event sebelum stop(); untuk live, tutup event yang masih terbuka.
        if was_running:
            self._handle(self.manager.flush(), None, [])
        self.live.update(None, self.live.people, self.reader.fps, self._metrics())
        if self.clips:
            self.clips.flush()

    def needs_ppe(self) -> bool:
        return self.frame_idx % self.ppe_every_n == 0 or self._last_items is None

    def _new_fallback_track_id(self) -> int:
        tid = self._next_fallback_track_id
        self._next_fallback_track_id -= 1
        return tid

    def _prune_fallback_tracks(self) -> None:
        max_age = max(
            int(self.manager.cfg.track_lost_frames),
            int(self.manager.cfg.reentry_max_gap_frames if self.manager.cfg.reentry_enabled else 0),
            1,
        )
        stale = [tid for tid, tr in self._fallback_tracks.items()
                 if self.frame_idx - int(tr.get("last_frame", self.frame_idx)) > max_age]
        for tid in stale:
            del self._fallback_tracks[tid]

    def _fallback_person_for_candidate(self, candidate: Person, consumed: set[int]) -> Person:
        """Beri ID stabil pada pose yang tidak diambil ByteTrack.

        ByteTrack sengaja konservatif terhadap deteksi confidence rendah atau orang yang hanya tampak
        sebagian. Pada kamera meja/occlusion, akibatnya pose mentah bisa terlihat, tetapi tidak pernah
        masuk `StateManager` dan tidak pernah menghasilkan Evidence. Fallback ini hanya dipakai untuk
        kandidat pose yang tidak dipakai ByteTrack dan bukan duplikat hampir identik pada frame yang
        sama, lalu distabilkan dengan overlap/center-distance antarinferensi.
        """
        best_tid = None
        best_score = -1.0
        for tid, tr in self._fallback_tracks.items():
            if tid in consumed:
                continue
            box = tr["box"]
            same_pose = keypoints_match_same_person(candidate.keypoints, tr.get("keypoints"), candidate.box, box)
            if not same_pose and not _boxes_match(candidate.box, box, self.recorded_overlay_match_min_iou,
                                                 self.recorded_overlay_match_max_center_dist):
                continue
            gap = max(0, self.frame_idx - int(tr.get("last_frame", self.frame_idx)))
            score = _box_iou(candidate.box, box) - _box_center_dist(candidate.box, box) * 0.05 - gap * 0.001
            if same_pose:
                score += 0.5
            if score > best_score:
                best_tid = tid
                best_score = score
        if best_tid is None:
            best_tid = self._new_fallback_track_id()
        consumed.add(best_tid)
        self._fallback_tracks[best_tid] = {"box": candidate.box, "keypoints": candidate.keypoints, "last_frame": self.frame_idx}
        return Person(int(best_tid), candidate.box, candidate.conf, candidate.keypoints)

    def _is_same_frame_duplicate_person(self, candidate: Person, tracked: Person) -> bool:
        """Cek duplikat orang pada frame yang sama dengan ambang ketat.

        Ambang overlay/re-entry sengaja lebih longgar agar kotak tidak berkedip ketika ID berubah.
        Untuk fallback raw pose, ambang longgar tersebut berbahaya: dua pekerja yang berdempetan atau
        saling menutupi bisa dianggap satu orang sehingga kandidat parsial tidak pernah masuk Evidence.
        Karena itu deduplikasi fallback hanya menggabungkan bbox yang hampir sama.
        """
        if keypoints_match_same_person(candidate.keypoints, tracked.keypoints, candidate.box, tracked.box):
            return True
        return _boxes_match(
            candidate.box,
            tracked.box,
            self.manager.cfg.duplicate_active_min_iou,
            self.manager.cfg.duplicate_active_max_center_dist,
        )

    def _add_untracked_pose_candidates(
        self,
        persons: list[Person],
        pose_candidates: list[Person],
        tracked_candidate_indices: set[int] | None = None,
    ) -> list[Person]:
        """Promosikan raw pose yang tidak punya ID tracker menjadi person fallback.

        Kandidat yang indeks raw-nya sudah dipakai ByteTrack dilewati agar tidak membuat pekerja ganda.
        Kandidat lain hanya dibuang bila bbox-nya hampir identik dengan track frame ini. Ini penting
        untuk pekerja yang tertutup sebagian/berdempetan: bbox mereka bisa overlap cukup besar dengan
        pekerja lain, tetapi tetap harus masuk state agar pelanggaran singkat dapat menjadi Evidence.
        ID negatif dipakai supaya tidak bentrok dengan ID ByteTrack/Ultralytics yang positif.
        """
        self._prune_fallback_tracks()
        if not pose_candidates:
            return persons
        tracked_candidate_indices = tracked_candidate_indices or set()
        consumed_fallback: set[int] = set()
        augmented = list(persons)
        for idx, candidate in enumerate(pose_candidates):
            if idx in tracked_candidate_indices:
                continue
            if any(self._is_same_frame_duplicate_person(candidate, tracked) for tracked in augmented):
                continue
            augmented.append(self._fallback_person_for_candidate(candidate, consumed_fallback))
        return augmented

    def _clip_done(self, event_id: int, path: Path, sha256: str) -> None:
        self.sink.on_clip(event_id, str(path.relative_to(self.evidence_dir)), sha256)

    def _now(self, frame_idx: int | None = None) -> datetime:
        if self.video_start is not None:
            idx = self.frame_idx if frame_idx is None else frame_idx
            return self.video_start + timedelta(seconds=idx / (self.src_fps or 25.0))
        return datetime.now(self.tz).replace(tzinfo=None)

    def process(self, frame: np.ndarray, pose_res, ppe_res, t_batch: float, now: datetime | None = None):
        with self._stats_lock:
            self.stats["processed_frames"] += 1
            self.stats["processed_at"].append(time.time())
            self.stats["batch_times"].append(t_batch)
            if len(self.stats["batch_times"]) > 100:
                self.stats["batch_times"].pop(0)

        # `now` bisa di-inject oleh worker analisis rekaman (waktu = jam mulai rekaman + posisi
        # frame di video). Untuk live/simulasi, pakai jam sekarang / video_start bawaan.
        if now is None:
            now = self._now()
        H, W = frame.shape[:2]
        self._frame_size = {"width": int(W), "height": int(H)}
        self._active_zones = self.zone_provider()

        persons = []
        pose_candidates = []
        if pose_res.boxes is not None and len(pose_res.boxes) > 0:
            # ByteTracker memanggil .numpy() pada box; di device mps/cuda tensor harus dipindah ke CPU dulu.
            boxes_for_tracker = pose_res.boxes.cpu()
            tracks = self.tracker.update(boxes_for_tracker, frame)
            raw_boxes = pose_res.boxes.xyxy.cpu().numpy()
            raw_confs = pose_res.boxes.conf.cpu().numpy()
            raw_kpts = pose_res.keypoints.data.cpu().numpy() if pose_res.keypoints is not None else [None] * len(raw_boxes)
            for b, c, k in zip(raw_boxes, raw_confs, raw_kpts):
                box = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
                pose_candidates.append(Person(-1, box, float(c), k))
            tracked_candidate_indices: set[int] = set()
            if len(tracks) > 0:
                idx = tracks[:, -1].astype(int)
                tracked_candidate_indices = {int(i) for i in idx}
                boxes = pose_res.boxes[idx].xyxy.cpu().numpy()
                track_ids = tracks[:, 4].astype(int)
                confs = pose_res.boxes[idx].conf.cpu().numpy()
                kpts = pose_res.keypoints[idx].data.cpu().numpy() if pose_res.keypoints is not None else [None] * len(boxes)
                for b, tid, c, k in zip(boxes, track_ids, confs, kpts):
                    box = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
                    persons.append(Person(int(tid), box, float(c), k))
            persons = self._add_untracked_pose_candidates(persons, pose_candidates, tracked_candidate_indices)

        if ppe_res is not None:
            self._last_items = ppe_res

        # Kotak PPE lama tidak boleh diasosiasikan ke pose dari frame baru.
        ppe_evaluated = ppe_res is not None
        items = ppe_res if ppe_evaluated else []
        evidence = associate(persons, items, self.assoc_cfg) if ppe_evaluated else {}

        zones = {}
        for p in persons:
            z = zone_for(p.box, W, H, self._active_zones)
            zones[p.track_id] = (z.name, z.required) if z else ("Di luar zona", ())

        with self._render_lock:
            res = self.manager.step(self.frame_idx, now, persons, evidence, zones,
                                    evaluated=ppe_evaluated)
        self._handle(res, frame, persons, evidence)
        jpeg = self._publish(frame, persons, ppe_res if ppe_evaluated else None, pose_candidates,
                             evidence if ppe_evaluated else None)

        if self.clips and jpeg:
            self.clips.push(jpeg)
        if self.quality:
            for stats in self.quality.add(now, frame, persons, items):
                self.sink.on_quality(self.camera_id, stats)

        self.frame_idx += 1

    def _handle(self, res, frame, persons, evidence=None) -> None:
        heads = self._heads_locked_to_track(persons) if persons else []
        people_by_id = {p.track_id: p for p in persons if isinstance(p, Person)}
        for t in res.opened:
            event = t.event
            if frame is None or event is None:
                continue
            person = people_by_id.get(t.track_id)
            metadata = (_evidence_detections(t, person, (evidence or {}).get(t.track_id),
                                             frame.shape[1], frame.shape[0]) if person is not None else "")
            saved = save_evidence(frame, self.evidence_dir, self.camera_id, _track_display_id(t), event.started_at,
                                  t.box, t.stable_status, t.checklist(), heads, self.blur, metadata)
            event.db_id = self.sink.on_open(self.camera_id, t, saved)
            if self.clips and event.db_id is not None:
                stem = saved.full_path.removesuffix("_full.jpg") + "_clip"
                self.clips.start(event.db_id, self.evidence_dir / stem)
        for t, event, ended in res.closed:
            if event.db_id is not None:
                self.sink.on_close(event.db_id, event, ended)
        for t in res.lost:
            self.sink.on_track_lost(self.camera_id, t)

    def _people_snapshot(self, persons, evidence=None) -> list[dict]:
        people = []
        width = int(self._frame_size["width"])
        height = int(self._frame_size["height"])
        for p in persons:
            t = self.manager.tracks.get(p.track_id)
            if not t:
                continue
            status = t.stable_status or "uncertain"
            live_evidence = (evidence or {}).get(p.track_id)
            confidence = dict(self._last_live_confidence.get(p.track_id, {}))
            if evidence is not None:
                confidence = {}
            if live_evidence is not None:
                for key in ITEMS:
                    detections = live_evidence.detections.get(key, [])
                    if detections:
                        confidence[key] = round(sum(det.conf for det in detections) / len(detections), 3)
            if evidence is not None:
                self._last_live_confidence[p.track_id] = confidence
            people.append({
                "track_id": t.track_id, "worker_id": _track_display_id(t), "pekerja": f"W{_track_display_id(t):03d}",
                "status": status, "zone": t.zone,
                "checklist": t.checklist(), "since": t.first_seen.isoformat(timespec="seconds"),
                "box": _percent_box(t.box, width, height) if width > 0 and height > 0 else None,
                "confidence": confidence,
            })
        current_ids = {person.track_id for person in persons}
        self._last_live_confidence = {
            track_id: values for track_id, values in self._last_live_confidence.items() if track_id in current_ids
        }
        return people

    def _heads_locked_to_track(self, persons) -> list[Box]:
        """Kotak blur wajah yang terkunci ke kotak tracker, bukan kotak deteksi mentah.

        Kotak yang digambar untuk pekerja memakai `track.box` (hasil tracker), jadi blur wajah harus
        memakai kotak yang sama agar keduanya tidak terpisah untuk orang yang sama. Kalau track belum
        ada (mis. kandidat pose yang belum ter-ID), jatuh ke kotak deteksi orang itu sendiri.
        """
        heads = []
        for p in persons:
            track = self.manager.tracks.get(p.track_id)
            box = track.box if track is not None else p.box
            heads.append(head_box(box, p.keypoints))
        return heads

    def render_frame(self, frame: np.ndarray, persons, items=None, compact_labels: bool = False) -> np.ndarray:
        """Render frame BGR dengan zona, blur wajah, dan overlay status APD.

        Dipakai live MJPEG dan worker analisis rekaman agar hasil video upload konsisten
        dengan tampilan operator.
        """
        img = frame.copy()
        if self.blur:
            for hb in self._heads_locked_to_track(persons):
                blur_region(img, hb)
        draw_zones(img, getattr(self, "_active_zones", []))
        if self.draw_raw_ppe_boxes:
            draw_ppe_items(img, items or [])
        for p in persons:
            t = self.manager.tracks.get(p.track_id)
            if not t:
                continue
            status = t.stable_status or "uncertain"
            draw_person(img, t.box, _track_display_id(t), status, t.checklist(), p.keypoints, self.assoc_cfg,
                        draw_regions=self.draw_check_regions, compact=compact_labels)
        return img

    def _render_recorded_snapshot_frame(self, frame: np.ndarray, items=None, compact_labels: bool = True) -> np.ndarray:
        """Render overlay dari snapshot terakhir yang masih valid.

        Snapshot dipakai agar kotak tidak hilang saat satu inferensi kosong/parsial. Berbeda dari
        `render_frame()`, fungsi ini tidak bergantung pada daftar `persons` frame terbaru sehingga
        track aktif yang sesaat tidak terdeteksi tetap tergambar sampai melewati batas hold.
        Pemanggil harus memegang `_render_lock`.
        """
        snapshots = list(self._recorded_overlay_snapshots.values())
        img = frame.copy()
        if self.blur:
            people_for_blur = [s["person"] for s in snapshots]
            for hb in heads_for(people_for_blur):
                blur_region(img, hb)
        draw_zones(img, getattr(self, "_active_zones", []))
        if self.draw_raw_ppe_boxes:
            draw_ppe_items(img, items or [])
        for s in sorted(snapshots, key=lambda x: int(x.get("worker_id", x["track_id"]))):
            draw_person(img, s["box"], int(s.get("worker_id", s["track_id"])), s["status"], s["checklist"], s["keypoints"],
                        self.assoc_cfg, draw_regions=self.draw_check_regions, compact=compact_labels)
        return img

    def render_last_frame(self, frame: np.ndarray) -> np.ndarray:
        """Render frame memakai hasil inferensi terakhir yang sinkron dengan pemanggil.

        Worker rekaman memanggil ini tepat setelah `process()` pada frame yang sama untuk menulis
        video hasil deteksi tanpa perlu decode JPEG lagi.
        """
        with self._render_lock:
            if self._recorded_overlay_snapshots:
                return self._render_recorded_snapshot_frame(frame, self._last_items or [])
            return self.render_frame(frame, list(self._held_overlay_people), self._last_items or [], compact_labels=True)

    def render_raw_frame(self, frame: np.ndarray) -> np.ndarray:
        """Render frame pengisi tanpa overlay deteksi lama.

        Dipertahankan untuk fallback/debug: frame mentah tetap boleh diterbitkan tanpa menggambar
        overlay lama yang sudah tidak sinkron.
        """
        with self._render_lock:
            heads = self._heads_locked_to_track(list(self._last_people)) if self.blur else []
        img = frame.copy()
        for hb in heads:
            blur_region(img, hb)
        draw_zones(img, getattr(self, "_active_zones", []))
        return img

    def render_held_overlay_frame(self, frame: np.ndarray) -> np.ndarray:
        """Render frame memakai snapshot overlay stabil untuk video hasil rekaman.

        Worker rekaman menjalankan pose/tracking setiap frame agar box mengikuti gerakan, sementara
        evaluasi APD tetap disampling. Snapshot ini mempertahankan status/checklist terakhir dan
        menahan overlay sebentar saat tracker drop agar kotak tidak hilang-muncul.
        """
        with self._render_lock:
            if self._recorded_overlay_snapshots:
                return self._render_recorded_snapshot_frame(frame, self._last_items or [])
            return self.render_frame(frame, list(self._held_overlay_people), self._last_items or [], compact_labels=True)

    def _refresh_recorded_overlay_cache(self, persons, pose_candidates=None) -> None:
        """Perbarui cache overlay khusus video rekaman.

        Live display tetap memakai `_last_people` yang sinkron dengan frame inferensi terbaru.
        Untuk video rekaman, deteksi sesekali bisa kosong walaupun ByteTrack/StateManager masih
        menahan track aktif. Cache ini mencegah kotak berkedip pada miss singkat, namun tetap
        dibersihkan setelah track benar-benar basi agar kotak lama tidak menempel terlalu lama.
        Pemanggil harus memegang `_render_lock`.
        """
        current = {p.track_id: p for p in persons}
        pose_candidates = list(pose_candidates or [])
        max_age = self.recorded_overlay_hold_inference_frames
        next_snapshots = {}
        next_people = []
        matched_previous = set()

        for tid, p in current.items():
            track = self.manager.tracks.get(tid)
            if track is None or self.frame_idx - track.last_frame > max_age:
                continue

            prev_tid, prev = self._matching_recorded_snapshot(tid, track.box, matched_previous)
            if prev_tid is not None:
                matched_previous.add(prev_tid)

            keypoints = _smooth_keypoints(
                prev.get("keypoints") if prev else None,
                p.keypoints,
                self.recorded_overlay_smooth_alpha,
            )
            conf = p.conf
            box = _smooth_box(prev.get("box") if prev else None,
                              (float(track.box[0]), float(track.box[1]), float(track.box[2]), float(track.box[3])),
                              self.recorded_overlay_smooth_alpha)
            status = track.stable_status or (prev.get("status") if prev else "uncertain") or "uncertain"
            checklist = dict(track.checklist()) if track.stable_status or not prev else dict(prev.get("checklist", track.checklist()))
            worker_id = _track_display_id(track)
            person = Person(int(tid), box, float(conf), keypoints)
            snapshot = {
                "track_id": int(tid),
                "worker_id": int(worker_id),
                "box": box,
                "status": status,
                "checklist": checklist,
                "keypoints": keypoints,
                "person": person,
                "last_seen_frame": int(self.frame_idx),
            }
            next_snapshots[int(tid)] = snapshot
            next_people.append(person)

        for p in pose_candidates:
            if self._overlaps_recorded_current(p.box, next_snapshots):
                continue
            prev_tid, prev = self._matching_recorded_snapshot(-1, p.box, matched_previous)
            if prev_tid is None or not prev:
                continue
            last_seen_frame = int(prev.get("last_seen_frame", self.frame_idx))
            hold_output_frames = int(round(self.recorded_overlay_hold_seconds * self.recorded_output_fps))
            if self.frame_idx - last_seen_frame > hold_output_frames:
                continue
            matched_previous.add(prev_tid)
            keypoints = _smooth_keypoints(
                prev.get("keypoints"),
                p.keypoints,
                self.recorded_overlay_smooth_alpha,
            )
            box = _smooth_box(prev.get("box"),
                              (float(p.box[0]), float(p.box[1]), float(p.box[2]), float(p.box[3])),
                              self.recorded_overlay_smooth_alpha)
            status = prev.get("status") or "uncertain"
            checklist = dict(prev.get("checklist", {}))
            worker_id = int(prev.get("worker_id", prev_tid))
            person = Person(int(prev_tid), box, float(p.conf), keypoints)
            snapshot = {
                "track_id": int(prev_tid),
                "worker_id": worker_id,
                "box": box,
                "status": status,
                "checklist": checklist,
                "keypoints": keypoints,
                "person": person,
                "last_seen_frame": int(self.frame_idx),
            }
            next_snapshots[int(prev_tid)] = snapshot
            next_people.append(person)

        for tid, prev in self._recorded_overlay_snapshots.items():
            if tid in matched_previous or tid in next_snapshots:
                continue
            last_seen_frame = int(prev.get("last_seen_frame", self.frame_idx))
            hold_output_frames = int(round(self.recorded_overlay_hold_seconds * self.recorded_output_fps))
            if self.frame_idx - last_seen_frame > hold_output_frames:
                continue
            if self._overlaps_recorded_current(prev["box"], next_snapshots):
                continue
            person = prev["person"]
            next_snapshots[int(tid)] = prev
            next_people.append(person)

        self._recorded_overlay_snapshots = next_snapshots
        self._held_overlay_people = next_people

    def _matching_recorded_snapshot(self, tid: int, box: Box, used: set[int]) -> tuple[int | None, dict]:
        prev = self._recorded_overlay_snapshots.get(tid)
        if prev is not None and tid not in used:
            return tid, prev

        best_tid = None
        best_snapshot = None
        best_score = -1.0
        for old_tid, old in self._recorded_overlay_snapshots.items():
            if old_tid in used:
                continue
            old_box = old["box"]
            iou = _box_iou(box, old_box)
            dist = _box_center_dist(box, old_box)
            if iou < self.recorded_overlay_match_min_iou and dist > self.recorded_overlay_match_max_center_dist:
                continue
            score = iou - dist * 0.05
            if score > best_score:
                best_tid = old_tid
                best_snapshot = old
                best_score = score
        return best_tid, best_snapshot or {}

    def _overlaps_recorded_current(self, box: Box, snapshots: dict[int, dict]) -> bool:
        for current in snapshots.values():
            current_box = current["box"]
            if _box_iou(box, current_box) >= self.recorded_overlay_match_min_iou:
                return True
            if _box_center_dist(box, current_box) <= self.recorded_overlay_match_max_center_dist:
                return True
        return False

    def _render_jpeg(self, frame: np.ndarray, persons, items=None) -> bytes | None:
        if self._recorded_overlay_snapshots:
            img = self._render_recorded_snapshot_frame(frame, items, compact_labels=self._recorded_mode)
        else:
            img = self.render_frame(frame, persons, items)
        return self._encode_image_jpeg(img)

    def _encode_image_jpeg(self, img: np.ndarray) -> bytes | None:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes() if ok else None

    def _encode_raw_jpeg(self, frame: np.ndarray, people_for_blur=None) -> bytes | None:
        img = frame.copy()
        if self.blur_live:
            for hb in heads_for(people_for_blur or []):
                blur_region(img, hb)
        draw_zones(img, getattr(self, "_active_zones", []))
        return self._encode_image_jpeg(img)

    def _encode_clean_jpeg(self, frame: np.ndarray, people_for_blur=None) -> bytes | None:
        """Frame untuk overlay browser: blur wajah mengikuti `blur_faces_live`, tanpa zona/kotak/label server.

        Blur dikunci ke kotak tracker (sama seperti kotak pekerja yang digambar frontend) agar blur
        mengikuti pekerja saat berpindah, bukan menempel di posisi deteksi mentah. Jalur ini hanya
        menyuplai tampilan live; evidence dan klip memakai `self.blur` yang terpisah.
        """
        img = frame.copy()
        if self.blur_live:
            for hb in self._heads_locked_to_track(people_for_blur or []):
                blur_region(img, hb)
        return self._encode_image_jpeg(img)

    def _display_loop(self) -> None:
        """Publikasikan frame terbaru pada target FPS tanpa menunggu inferensi selesai.

        Snapshot overlay terakhir yang masih segar ikut digambar agar kotak pekerja tidak berkedip
        antara frame beranotasi dan frame mentah. Jika overlay sudah terlalu tua, tampilan tetap
        diisi frame mentah + zona supaya video tidak patah-patah dan tidak menampilkan kotak basi.

        PENTING (privasi): saat blur wajah aktif, frame mentah dari kamera TIDAK boleh diterbitkan di
        sini. Posisi wajah hanya diketahui pada frame yang dievaluasi inferensi; memblur frame baru
        dengan posisi dari inferensi sebelumnya membuat blur tertinggal di tempat lama dan wajah yang
        sudah berpindah jadi terbuka. Karena itu, saat blur aktif, stream mengikuti laju inferensi
        lewat `_publish` (frame + blur + kotak tracker selalu dari momen yang sama, tidak ada bocor).
        """
        interval = 1.0 / self.stream_target_fps
        while not self._display_stop.is_set():
            start = time.time()
            if not self.blur_live and self.display_raw_when_stale:
                frame, seq = self.reader.latest_with_seq()
                if frame is not None and seq is not None:
                    with self._render_lock:
                        overlay_seq = self._last_overlay_seq
                        overlay_fresh = overlay_seq is not None and seq - overlay_seq <= self.overlay_max_age_frames
                        if overlay_fresh and self._recorded_overlay_snapshots:
                            img = self._render_recorded_snapshot_frame(frame, self._last_items or [], compact_labels=self._recorded_mode)
                            jpeg = self._encode_image_jpeg(img)
                        else:
                            jpeg = self._encode_raw_jpeg(frame, list(self._last_people))
                        clean_jpeg = self._encode_clean_jpeg(frame, list(self._last_people))
                    if jpeg:
                        self.live.update(jpeg, self.live.people, self.reader.fps, self._metrics(), clean_jpeg=clean_jpeg)
            elapsed = time.time() - start
            self._display_stop.wait(max(0.001, interval - elapsed))

    def _publish(self, frame: np.ndarray, persons, items=None, pose_candidates=None, evidence=None) -> bytes | None:
        with self._render_lock:
            self._last_people = list(persons)
            self._refresh_recorded_overlay_cache(persons, pose_candidates)
            if items is not None:
                self._last_items = items
            elif self._last_items is None:
                self._last_items = []
            people = self._people_snapshot(persons, evidence)
            jpeg = self._render_jpeg(frame, persons, self._last_items)
            clean_jpeg = self._encode_clean_jpeg(frame, persons)
            self._last_overlay_seq = self.reader.last_read_seq
        metrics = self._metrics()
        self.live.update(jpeg, people, self.reader.fps, metrics, clean_jpeg=clean_jpeg)
        return jpeg

    def _metrics(self) -> dict:
        now = time.time()
        window_s = 5.0
        with self._stats_lock:
            processed_at = list(self.stats["processed_at"])
            batch_times = list(self.stats["batch_times"])
            processed = int(self.stats["processed_frames"])
        recent = [t for t in processed_at if now - t <= window_s]
        read = max(int(getattr(self.reader, "frames_total", 0)), processed)
        skipped_pct = max(0.0, (read - processed) / read * 100.0) if read else 0.0
        return {
            "waktu_batch_ms": round((sum(batch_times) / len(batch_times)) * 1000, 1) if batch_times else 0.0,
            "pemeriksaan_per_detik": round(len(recent) / window_s, 1),
            "persen_frame_dilewati": round(skipped_pct, 1),
            "frame": dict(self._frame_size),
        }
