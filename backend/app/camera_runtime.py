"""Menyusun sumber runtime kamera dari baris database `Camera`.

Modul ini milik `app/` (boleh menyentuh database dan dekripsi URL). Hasilnya berupa
config turunan per kamera yang bisa diberikan ke `pipeline.CameraSession`, sehingga
pipeline tetap tidak perlu tahu soal database.

Pemetaan sumber -> source_kind runtime:
- stream (RTSP/HTTP)            -> "live"      (realtime, menyambung ulang)
- video + mode "simulasi"       -> "simulasi"  (diputar ulang seperti live)
- video + mode "analisis"       -> "rekaman"   (diproses sekali oleh worker job)
"""
from __future__ import annotations

import copy
import logging
from pathlib import PurePath

from . import camera_store, videos
from .config import load_config
from .db import Camera, VideoFile

log = logging.getLogger("ppe.camera_runtime")


def source_kind_for(cam: Camera) -> str:
    """source_kind yang dicatat di event untuk kamera ini."""
    if cam.source_kind == "stream":
        return "live"
    if cam.mode == "analisis":
        return "rekaman"
    return "simulasi"


def resolve_source(cam: Camera, video: VideoFile | None) -> str:
    """Kembalikan string sumber yang bisa dibuka OpenCV (URL stream atau path video).

    URL stream didekripsi dari `source_enc`. Path video terdaftar divalidasi tidak keluar VIDEO_DIR.
    `seed_camera_if_empty()` bisa menyimpan path demo lama langsung di `video_id`; itu tetap relatif BASE_DIR.
    """
    if cam.source_kind == "stream":
        return camera_store.decrypt_url(cam.source_enc) if cam.source_enc else ""
    if video:
        return str(videos.resolve_server_video(video.path))
    # Kompatibilitas install lama/fresh: config camera.source seperti data/videos/11.mp4 belum punya baris VideoFile.
    if cam.video_id and not PurePath(cam.video_id).is_absolute() and ".." not in PurePath(cam.video_id).parts:
        return cam.video_id
    return ""


def live_camera_config(cam: Camera, video: VideoFile | None, base_cfg: dict | None = None) -> dict:
    """Config turunan untuk kamera live/simulasi (dipakai scheduler live).

    Kamera mode analisis TIDAK dijalankan di sini; itu tugas worker job.
    """
    cfg = copy.deepcopy(base_cfg or load_config())
    cfg["camera"]["id"] = cam.id
    cfg["camera"]["source"] = resolve_source(cam, video)
    is_sim = cam.source_kind == "video" and cam.mode == "simulasi"
    # Stream disambung ulang terus; video simulasi diputar ulang dengan kecepatan asli.
    cfg["camera"]["loop"] = is_sim
    cfg["camera"]["realtime"] = is_sim
    cfg["camera"]["start_time"] = None  # live/simulasi memakai jam sekarang
    return cfg
