"""Menyimpan foto evidence: frame utuh beranotasi + crop orang, wajah di-blur, plus hash SHA-256."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .draw import blur_region, draw_person, head_box
from .types import Box


@dataclass
class SavedEvidence:
    full_path: str   # relatif terhadap folder evidence
    crop_path: str
    sha256: str      # hash file full frame
    clean_path: str = ""
    detections_json: str = ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def save_evidence(
    frame: np.ndarray,
    out_dir: Path,
    camera_id: str,
    display_id: int,
    when: datetime,
    box: Box,
    status: str,
    checklist: dict[str, str],
    all_heads: list[Box],
    blur_faces: bool = True,
    detections_json: str = "",
) -> SavedEvidence:
    img = frame.copy()
    if blur_faces:
        for hb in all_heads:
            blur_region(img, hb)

    H, W = img.shape[:2]
    x1, y1, x2, y2 = box
    pad = 0.1 * (x2 - x1)
    crop = img[max(0, int(y1 - pad)):min(H, int(y2 + pad)), max(0, int(x1 - pad)):min(W, int(x2 + pad))].copy()
    frame_clean = img.copy()

    draw_person(img, box, display_id, status, checklist)

    day = when.strftime("%Y-%m-%d")
    stem = f"{camera_id}_{when.strftime('%H%M%S')}_W{display_id:03d}"
    folder = out_dir / day
    folder.mkdir(parents=True, exist_ok=True)
    # evidence tidak boleh ditimpa (hash event lama jadi tidak cocok), jadi beri akhiran bila nama sudah dipakai
    n, base = 1, stem
    while (folder / f"{stem}_full.jpg").exists():
        n += 1
        stem = f"{base}_{n}"
    full, cropf = folder / f"{stem}_full.jpg", folder / f"{stem}_crop.jpg"
    clean = folder / f"{stem}_clean.jpg"
    # Frame bersih tetap melalui blur wajah, tetapi belum digambari kotak/label.
    cv2.imwrite(str(clean), frame_clean, [cv2.IMWRITE_JPEG_QUALITY, 88])
    cv2.imwrite(str(full), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    cv2.imwrite(str(cropf), crop if crop.size else img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return SavedEvidence(
        full_path=str(full.relative_to(out_dir)),
        crop_path=str(cropf.relative_to(out_dir)),
        sha256=sha256_file(full),
        clean_path=str(clean.relative_to(out_dir)),
        detections_json=detections_json,
    )


def heads_for(persons) -> list[Box]:
    return [head_box(p.box, p.keypoints) for p in persons]
