"""Statistik kualitas input per menit untuk mendeteksi drift.

Model yang bagus saat diuji bisa memburuk diam-diam setelah dipasang: kamera bergeser, lensa kotor,
lampu mati di malam hari. Modul ini mencatat sinyal sederhana per frame lalu meringkasnya per menit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np


@dataclass
class MinuteStats:
    minute: datetime
    frames: int = 0
    persons: int = 0
    person_conf_sum: float = 0.0
    ppe: int = 0
    ppe_conf_sum: float = 0.0
    brightness_sum: float = 0.0
    sharpness_sum: float = 0.0

    def summary(self) -> dict:
        n = max(1, self.frames)
        return {
            "minute": self.minute,
            "frames": self.frames,
            "persons_per_frame": round(self.persons / n, 3),
            "mean_person_conf": round(self.person_conf_sum / self.persons, 4) if self.persons else 0.0,
            "mean_ppe_conf": round(self.ppe_conf_sum / self.ppe, 4) if self.ppe else 0.0,
            "brightness": round(self.brightness_sum / n, 2),
            "sharpness": round(self.sharpness_sum / n, 2),
        }


def frame_signals(frame: np.ndarray) -> tuple[float, float]:
    """(kecerahan rata-rata, ketajaman). Dihitung di gambar kecil supaya murah."""
    small = cv2.resize(frame, (320, int(320 * frame.shape[0] / frame.shape[1])))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return float(gray.mean()), float(cv2.Laplacian(gray, cv2.CV_64F).var())


@dataclass
class QualityTracker:
    current: MinuteStats | None = None
    _closed: list[dict] = field(default_factory=list)

    def add(self, now: datetime, frame: np.ndarray, persons, items) -> list[dict]:
        """Tambahkan satu frame. Mengembalikan ringkasan menit yang baru selesai (0 atau 1 item)."""
        minute = now.replace(second=0, microsecond=0)
        out = []
        if self.current is not None and minute != self.current.minute:
            out.append(self.current.summary())
            self.current = None
        if self.current is None:
            self.current = MinuteStats(minute)
        s = self.current
        brightness, sharpness = frame_signals(frame)
        s.frames += 1
        s.brightness_sum += brightness
        s.sharpness_sum += sharpness
        s.persons += len(persons)
        s.person_conf_sum += sum(p.conf for p in persons)
        s.ppe += len(items)
        s.ppe_conf_sum += sum(i.conf for i in items)
        return out

    def flush(self) -> list[dict]:
        if self.current is None or self.current.frames == 0:
            return []
        out = [self.current.summary()]
        self.current = None
        return out
