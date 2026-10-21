"""Klip video evidence: beberapa detik sebelum dan sesudah pelanggaran dimulai.

Satu foto mudah diperdebatkan ("helmnya cuma dilepas sebentar"); klip pendek jauh lebih sulit dibantah.
Frame yang dipakai adalah frame live yang SUDAH diblur dan dianotasi, jadi klip tidak memuat wajah utuh.

Buffer menyimpan JPEG (bukan frame mentah) supaya memori tetap kecil: ~50 frame x ~100 KB.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger("ppe.clips")

# VP8/WebM bisa diputar langsung di browser dan tersedia di build OpenCV pip.
# H.264 sering tidak tersedia (lisensi), mp4v tidak bisa diputar browser.
CODEC, EXT = "VP80", ".webm"


@dataclass
class _Pending:
    event_id: int
    out_path: Path
    frames: list[bytes]
    remaining: int
    target_duration_s: float


@dataclass
class ClipRecorder:
    before_s: float = 2.0
    after_s: float = 3.0
    width: int = 640
    fps: float = 25.0
    on_done: Callable[[int, Path, str], None] | None = None   # (event_id, path, sha256)
    _buffer: deque = field(default_factory=deque)
    _pending: list[_Pending] = field(default_factory=list)
    _threads: list[threading.Thread] = field(default_factory=list)
    _write_lock: threading.Lock = field(default_factory=threading.Lock)

    def set_fps(self, fps: float) -> None:
        self.fps = max(1.0, min(60.0, fps))
        self._buffer = deque(self._buffer, maxlen=max(1, int(self.before_s * self.fps)))

    def push(self, jpeg: bytes) -> None:
        if self._buffer.maxlen is None:
            self.set_fps(self.fps)
        self._buffer.append(jpeg)
        done = []
        for p in self._pending:
            p.frames.append(jpeg)
            p.remaining -= 1
            if p.remaining <= 0:
                done.append(p)
        for p in done:
            self._pending.remove(p)
            self._write_async(p)

    def start(self, event_id: int, out_path: Path) -> None:
        after = max(1, int(self.after_s * self.fps))
        target_duration_s = len(self._buffer) / self.fps + self.after_s
        self._pending.append(_Pending(event_id, out_path, list(self._buffer), after, target_duration_s))

    def flush(self) -> None:
        """Tulis klip yang belum lengkap (misalnya video habis) dan tunggu semua penulisan selesai.

        Wajib dipanggil sebelum proses berhenti: thread yang terputus di tengah menulis video
        menghasilkan file rusak dan bisa membuat interpreter abort.
        """
        for p in self._pending:
            if p.frames:
                self._write(p)
        self._pending.clear()
        for t in self._threads:
            t.join(timeout=30)
        self._threads.clear()

    def _write_async(self, p: _Pending) -> None:
        self._threads = [t for t in self._threads if t.is_alive()]
        t = threading.Thread(target=self._write, args=(p,), daemon=True)
        self._threads.append(t)
        t.start()

    def _write(self, p: _Pending) -> None:
        try:
            # Encoding OpenCV dan decode frame mentah dibatasi satu klip per kamera agar lonjakan
            # beberapa event serentak tidak menghabiskan RAM worker.
            with self._write_lock:
                path = write_clip(p.frames, p.out_path, self.width, self.fps, p.target_duration_s)
                if path and self.on_done:
                    self.on_done(p.event_id, path, sha256_file(path))
        except Exception as e:  # klip opsional: kegagalan tidak boleh menghentikan pipeline
            log.warning("Gagal menulis klip event %s: %s", p.event_id, type(e).__name__)


def _effective_clip_fps(frame_count: int, fps: float, target_duration_s: float | None = None) -> float:
    """Pilih FPS tulis klip tanpa membuat playback lebih cepat dari durasi evidence.

    Pada jalur rekaman lama/worker belum restart, frame klip bisa masuk hanya pada cadence sampling
    tetapi `fps` sudah diatur ke FPS sumber. Jika langsung ditulis pada FPS sumber, klip 5 detik dapat
    terkompres menjadi sekitar 1 detik. Target durasi menjaga playback tetap normal untuk frame yang
    jarang, sementara jalur baru yang benar-benar push setiap frame tetap memakai FPS sumber.
    """
    base = max(1.0, min(60.0, float(fps or 25.0)))
    if not target_duration_s or target_duration_s <= 0 or frame_count <= 0:
        return base
    duration_fps = max(1.0, min(60.0, frame_count / target_duration_s))
    return min(base, duration_fps)


def write_clip(jpegs: list[bytes], out_path: Path, width: int, fps: float,
               target_duration_s: float | None = None) -> Path | None:
    first_index = -1
    first = None
    for index, jpeg in enumerate(jpegs):
        first = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if first is not None:
            first_index = index
            break
    if first is None:
        return None
    h, w = first.shape[:2]
    if w > width:
        h, w = int(h * width / w) // 2 * 2, width
    out_path = out_path.with_suffix(EXT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*CODEC),
                             _effective_clip_fps(len(jpegs), fps, target_duration_s), (w, h))
    if not writer.isOpened():
        return None
    try:
        writer.write(cv2.resize(first, (w, h)) if first.shape[1] != w or first.shape[0] != h else first)
        first = None
        for jpeg in jpegs[first_index + 1:]:
            frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            writer.write(cv2.resize(frame, (w, h)) if frame.shape[1] != w or frame.shape[0] != h else frame)
    finally:
        writer.release()
    return out_path if out_path.exists() and out_path.stat().st_size > 0 else None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
