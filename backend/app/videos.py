"""Penyimpanan dan validasi file video (upload bertahap + video di folder server).

Video disimpan dengan nama acak di `backend/data/videos/` (di luar folder yang disajikan web).
Isi divalidasi dengan OpenCV (bukan dari ekstensi/nama). Jalur selalu divalidasi tidak keluar folder.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from .config import BASE_DIR, load_config

log = logging.getLogger("ppe.videos")

VIDEO_DIR = BASE_DIR / "data" / "videos"
UPLOAD_TMP = BASE_DIR / "data" / "uploads"


def _cfg():
    return load_config().get("uploads", {})


def max_bytes() -> int:
    return int(os.getenv("UPLOAD_MAX_GB", _cfg().get("max_gb", 20))) * 1024 ** 3


def reserve_bytes() -> int:
    return int(os.getenv("UPLOAD_RESERVE_GB", _cfg().get("reserve_gb", 10))) * 1024 ** 3


def chunk_bytes() -> int:
    return int(os.getenv("UPLOAD_CHUNK_MB", _cfg().get("chunk_mb", 10))) * 1024 ** 2


def clean_label(name: str) -> str:
    """Bersihkan nama asli jadi label aman (tanpa path, karakter aneh dibuang)."""
    base = Path(name).name
    base = re.sub(r"[^\w.\- ]", "_", base).strip()
    return base[:120] or "video"


def ensure_dirs() -> None:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_TMP.mkdir(parents=True, exist_ok=True)


def free_bytes() -> int:
    ensure_dirs()
    return shutil.disk_usage(VIDEO_DIR).free


def fits_on_disk(size: int) -> bool:
    """True jika file seukuran `size` muat tanpa menembus cadangan disk."""
    return size <= max_bytes() and size <= (free_bytes() - reserve_bytes())


def tmp_path(upload_id: str) -> Path:
    ensure_dirs()
    # upload_id selalu uuid hex buatan server; tidak pernah dari input mentah
    return UPLOAD_TMP / f"{upload_id}.part"


def resolve_server_video(path_str: str) -> Path:
    """Validasi path video di folder server tidak keluar dari VIDEO_DIR (anti path traversal)."""
    ensure_dirs()
    root = VIDEO_DIR.resolve()
    candidate = (root / path_str).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Jalur video di luar folder yang diizinkan")
    return candidate


def _sane_fps(fps: float) -> bool:
    return fps == fps and 1.0 <= fps <= 120.0


def effective_video_timing(cap, video_fps: float = 0.0, duration_s: float = 0.0) -> dict:
    """Pilih FPS kerja yang menjaga durasi playback tetap sama dengan video sumber.

    Beberapa file rekaman punya metadata FPS yang keliru/aneh. Jika frame_count dan duration
    tersedia, `frame_count / duration` lebih aman untuk writer, timestamp, dan progress.
    """
    import cv2

    cap_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    derived = frames / duration_s if frames > 0 and duration_s and duration_s > 0 else 0.0
    candidates = ((derived, "frame_count/duration"), (float(video_fps or 0.0), "database"), (cap_fps, "opencv"))
    fps, source = next(((f, src) for f, src in candidates if _sane_fps(f)), (25.0, "fallback"))
    if source == "fallback":
        log.warning("Metadata FPS video tidak valid (db=%s, cv=%s, frames=%s, duration=%s); pakai 25 FPS",
                    video_fps, cap_fps, frames, duration_s)
    elif source != "opencv" and _sane_fps(cap_fps) and abs(cap_fps - fps) / max(fps, 1.0) > 0.10:
        log.warning("FPS metadata berbeda: pilih %s=%.3f (opencv=%.3f, frames=%s, duration=%s)",
                    source, fps, cap_fps, frames, duration_s)
    return {"fps": float(fps), "fps_source": source, "cap_fps": cap_fps, "frame_count": frames,
            "duration_s": float(duration_s or (frames / fps if frames and fps else 0.0)),
            "width": width, "height": height}


def _probe(path: Path) -> dict:
    """Validasi isi video dengan OpenCV. Lempar ValueError bila bukan video yang bisa dibaca."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        raise ValueError("File tidak bisa dibaca sebagai video")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    ok, _ = cap.read()
    cap.release()
    if not ok or width <= 0 or height <= 0:
        raise ValueError("File tidak berisi frame video yang valid")
    duration = round(frames / fps, 2) if fps else 0.0
    derived = frames / duration if frames > 0 and duration > 0 else 0.0
    timing_fps = derived if _sane_fps(derived) else (fps if _sane_fps(fps) else 25.0)
    return {"fps": round(timing_fps, 2), "width": width, "height": height, "duration_s": duration}


def _hash_file(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def probe_and_store(part: Path, label: str) -> dict:
    """Buka file dengan OpenCV; jika video valid, pindahkan ke VIDEO_DIR dengan nama acak + hash."""
    info = _probe(part)
    sha = _hash_file(part)
    ensure_dirs()
    vid = uuid.uuid4().hex
    dest = VIDEO_DIR / f"{vid}.mp4"
    shutil.move(str(part), str(dest))
    return {
        "id": vid, "path": dest.name, "size": dest.stat().st_size,
        "sha256": sha, "label": clean_label(label), **info,
    }


def list_server_files() -> list[str]:
    """Nama file video (relatif VIDEO_DIR) yang ada di disk, untuk didaftarkan manual."""
    ensure_dirs()
    return sorted(p.name for p in VIDEO_DIR.iterdir()
                  if p.is_file() and p.suffix.lower() in (".mp4", ".mov", ".mkv", ".avi", ".webm"))


def probe_server_video(path_str: str) -> dict:
    """Validasi + metadata video yang sudah ada di folder server (anti path traversal)."""
    path = resolve_server_video(path_str)
    if not path.is_file():
        raise ValueError("Video tidak ditemukan di folder server")
    info = _probe(path)
    return {"path": path.name, "size": path.stat().st_size, "sha256": _hash_file(path), **info}
