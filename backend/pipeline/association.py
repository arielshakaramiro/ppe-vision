"""Mengaitkan deteksi APD ke orang yang tepat berdasarkan wilayah tubuh.

Helm dicek di wilayah kepala, rompi di torso, sarung tangan di sekitar
pergelangan tangan, sepatu di sekitar pergelangan kaki. Wilayah dihitung dari
keypoint YOLO-pose; kalau keypoint tidak ada, dipakai pita tetap pada kotak orang
(hanya untuk helm dan rompi).

APD yang tidak masuk wilayah tubuh siapa pun dibuang, supaya helm yang
tergantung di dinding tidak dianggap dipakai orang di dekatnya.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .types import (
    ITEMS, L_ANKLE, L_EAR, L_EYE, L_HIP, L_SHOULDER, L_WRIST, NOSE, R_ANKLE,
    R_EAR, R_EYE, R_HIP, R_SHOULDER, R_WRIST, Box, Evidence, PPEDet, Person, ioa,
)

HEAD_KPTS = (NOSE, L_EYE, R_EYE, L_EAR, R_EAR)


@dataclass
class Region:
    box: Box
    visible: bool
    fallback: bool = False


@dataclass
class AssocConfig:
    min_ioa: float = 0.40
    keypoint_conf: float = 0.50
    hand_radius: float = 0.07
    foot_radius: float = 0.07


def _visible(kpts: np.ndarray, idx: int, thr: float) -> bool:
    return bool(kpts[idx, 2] >= thr)


def _around(x: float, y: float, rx: float, ry_up: float, ry_down: float) -> Box:
    return (x - rx, y - ry_up, x + rx, y + ry_down)


def _band(x1: float, y1: float, x2: float, h: float, top: float, bottom: float, inset: float) -> Box:
    """Pita fallback pada inti bbox orang.

    Fallback sengaja tidak memakai seluruh lebar bbox. Pada kamera top-down yang ramai,
    bbox pekerja belakang dapat mencakup APD pekerja di depan/sampingnya. Pita inti tetap
    fail-safe untuk mencatat APD wajib yang hilang, tetapi lebih sulit mencuri APD orang lain.
    """
    w = x2 - x1
    return (x1 + inset * w, y1 + top * h, x2 - inset * w, y1 + bottom * h)


def body_regions(p: Person, cfg: AssocConfig) -> dict[str, list[Region]]:
    x1, y1, x2, y2 = p.box
    w, h = x2 - x1, y2 - y1
    k = p.keypoints
    thr = cfg.keypoint_conf

    # Tanpa keypoint: pita tetap untuk helm & rompi, tangan/kaki tidak dinilai.
    # Helm dan rompi adalah APD wajib utama pada kamera demo/top-down. Jika kotak orang
    # sudah terlihat tetapi keypoint kepala/torso tidak stabil (misalnya pekerja jauh atau
    # sebagian tertutup meja), jangan biarkan status menjadi `unknown` permanen; pakai pita
    # fallback dari kotak orang supaya absennya APD wajib tetap tercatat sebagai pelanggaran.
    if k is None:
        return {
            "helmet": [Region(_band(x1, y1, x2, h, -0.05, 0.22, 0.25), True, True)],
            "vest": [Region(_band(x1, y1, x2, h, 0.22, 0.65, 0.18), True, True)],
            "gloves": [Region((x1, y1, x1, y1), False)],
            "shoes": [Region((x1, y2 - 0.15 * h, x2, y2), False)],
        }

    regions: dict[str, list[Region]] = {}

    head = [i for i in HEAD_KPTS if _visible(k, i, thr)]
    s = 0.12 * h  # perkiraan ukuran kepala
    if head:
        hx, hy = k[head, 0], k[head, 1]
        box = (hx.min() - 0.6 * s, hy.min() - 1.2 * s, hx.max() + 0.6 * s, hy.max() + 0.4 * s)
        regions["helmet"] = [Region(box, True)]
    else:
        regions["helmet"] = [Region(_band(x1, y1, x2, h, -0.05, 0.22, 0.25), True, True)]

    torso = [i for i in (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP) if _visible(k, i, thr)]
    shoulders_ok = _visible(k, L_SHOULDER, thr) or _visible(k, R_SHOULDER, thr)
    if len(torso) >= 2 and shoulders_ok:
        tx, ty = k[torso, 0], k[torso, 1]
        pad = 0.08 * w
        bottom = ty.max() if len(torso) > 2 else ty.max() + 0.30 * h
        regions["vest"] = [Region((tx.min() - pad, ty.min() - 0.03 * h, tx.max() + pad, bottom), True)]
    else:
        regions["vest"] = [Region(_band(x1, y1, x2, h, 0.22, 0.65, 0.18), True, True)]

    r = cfg.hand_radius * h
    regions["gloves"] = [
        Region(_around(k[i, 0], k[i, 1], r, r * 0.6, r * 1.4), _visible(k, i, thr))
        for i in (L_WRIST, R_WRIST)
    ]

    r = cfg.foot_radius * h
    regions["shoes"] = [
        Region(_around(k[i, 0], k[i, 1], r, r * 0.5, r * 1.3), _visible(k, i, thr))
        for i in (L_ANKLE, R_ANKLE)
    ]
    return regions


def associate(persons: list[Person], dets: list[PPEDet], cfg: AssocConfig) -> dict[int, Evidence]:
    """Kembalikan bukti per orang: {track_id: Evidence}."""
    regions = {p.track_id: body_regions(p, cfg) for p in persons}
    hits: dict[int, dict[str, list[float]]] = {
        pid: {item: [0.0] * len(regs[item]) for item in ITEMS} for pid, regs in regions.items()
    }
    matched: dict[int, dict[str, list[list[PPEDet]]]] = {
        pid: {item: [[] for _ in regs[item]] for item in ITEMS} for pid, regs in regions.items()
    }

    for d in dets:
        if d.item not in ITEMS:
            continue
        best = (0, 0.0, 0.0, None, None)
        for pid, regs in regions.items():
            for ri, reg in enumerate(regs[d.item]):
                score = ioa(d.box, reg.box)
                if score < cfg.min_ioa:
                    continue
                # Wilayah dari keypoint lebih presisi daripada pita fallback bbox. Pada kamera
                # top-down yang ramai, fallback bbox pekerja belakang bisa mencakup helm/rompi
                # pekerja depan. Karena itu deteksi APD harus diberikan dulu ke kandidat presisi;
                # fallback hanya dipakai bila tidak ada kandidat keypoint yang cukup cocok.
                precise = 0 if reg.fallback else 1
                region_area = max(0.0, (reg.box[2] - reg.box[0]) * (reg.box[3] - reg.box[1]))
                candidate = (precise, score, -region_area, pid, ri)
                if candidate > best:
                    best = candidate
        _, score, _, pid, ri = best
        if pid is not None and ri is not None and score >= cfg.min_ioa:
            hits[pid][d.item][ri] = max(hits[pid][d.item][ri], d.conf)
            matched[pid][d.item][ri].append(d)

    out: dict[int, Evidence] = {}
    for pid, regs in regions.items():
        ev = Evidence()
        for item in ITEMS:
            ev.detections[item] = [det for region in matched[pid][item] for det in region]
            judged = [
                hits[pid][item][ri]
                for ri, reg in enumerate(regs[item])
                if reg.visible or hits[pid][item][ri] > 0
            ]
            if not judged:
                ev.values[item] = None
            elif all(c > 0 for c in judged):
                ev.values[item] = float(sum(judged) / len(judged))
            else:
                ev.values[item] = -1.0
        out[pid] = ev
    return out
