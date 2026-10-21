"""Tipe data dan geometri dasar yang dipakai seluruh pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

ITEMS = ("helmet", "vest", "gloves", "shoes")

# Indeks keypoint COCO-17 (dipakai YOLO-pose)
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SHOULDER, R_SHOULDER = 5, 6
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_ANKLE, R_ANKLE = 15, 16

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 (piksel)


@dataclass
class Person:
    track_id: int
    box: Box
    conf: float
    keypoints: np.ndarray | None = None  # (17, 3): x, y, conf


@dataclass
class PPEDet:
    item: str
    box: Box
    conf: float


@dataclass
class Evidence:
    """Bukti per item untuk satu orang di satu frame.

    +conf  -> APD terlihat dipakai
    -1.0   -> bagian tubuh terlihat, APD tidak ada
    None   -> bagian tubuh tidak terlihat (tidak bisa dinilai)
    """

    values: dict[str, float | None] = field(default_factory=dict)
    detections: dict[str, list[PPEDet]] = field(default_factory=dict)


def area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def intersection(a: Box, b: Box) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def keypoint_mean_normalized_distance(
    a_points: np.ndarray | None,
    b_points: np.ndarray | None,
    a_box: Box,
    b_box: Box,
    *,
    min_conf: float = 0.35,
    min_points: int = 3,
) -> float | None:
    """Jarak rata-rata keypoint yang sama, dinormalisasi ukuran orang.

    Dipakai untuk membedakan duplikat ID orang yang sama dari dua pekerja yang berdempetan.
    Bbox bisa berubah banyak saat orang tertutup sebagian, tetapi keypoint yang masih terlihat
    biasanya tetap berada di posisi hampir sama. Jika keypoint yang valid terlalu sedikit,
    kembalikan None agar pemanggil tetap memakai aturan bbox.
    """
    if a_points is None or b_points is None:
        return None
    a = np.asarray(a_points)
    b = np.asarray(b_points)
    if a.shape[0] < 1 or b.shape[0] < 1 or a.shape[1] < 3 or b.shape[1] < 3:
        return None
    n = min(a.shape[0], b.shape[0])
    visible = (a[:n, 2] >= min_conf) & (b[:n, 2] >= min_conf)
    if int(visible.sum()) < min_points:
        return None
    deltas = a[:n, :2][visible] - b[:n, :2][visible]
    distances = np.sqrt((deltas * deltas).sum(axis=1))
    norm = max(
        a_box[2] - a_box[0],
        a_box[3] - a_box[1],
        b_box[2] - b_box[0],
        b_box[3] - b_box[1],
        1.0,
    )
    return float(distances.mean() / norm)


def keypoints_match_same_person(
    a_points: np.ndarray | None,
    b_points: np.ndarray | None,
    a_box: Box,
    b_box: Box,
    *,
    max_distance: float = 0.08,
    min_conf: float = 0.35,
    min_points: int = 3,
) -> bool:
    distance = keypoint_mean_normalized_distance(
        a_points,
        b_points,
        a_box,
        b_box,
        min_conf=min_conf,
        min_points=min_points,
    )
    return distance is not None and distance <= max_distance


def ioa(item: Box, region: Box) -> float:
    """Intersection over Area kotak APD: berapa bagian APD yang masuk wilayah."""
    a = area(item)
    return intersection(item, region) / a if a > 0 else 0.0


def foot_point(b: Box) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2, b[3])
