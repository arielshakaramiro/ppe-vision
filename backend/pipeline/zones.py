"""Aturan APD per zona. Zona ditentukan dari titik kaki (tengah bawah kotak orang)."""
from __future__ import annotations

from dataclasses import dataclass

from .types import ITEMS, Box, foot_point


@dataclass
class Zone:
    name: str
    polygon: list[tuple[float, float]]  # koordinat ternormalisasi 0..1
    required: tuple[str, ...]


def point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def zone_for(box: Box, width: int, height: int, zones: list[Zone]) -> Zone | None:
    fx, fy = foot_point(box)
    nx, ny = fx / width, min(fy / height, 0.999)
    for z in zones:
        if point_in_polygon(nx, ny, z.polygon):
            return z
    return None


def load_zones(raw: list[dict]) -> list[Zone]:
    zones = []
    for z in raw:
        required = tuple(i for i in z.get("required", ITEMS) if i in ITEMS)
        zones.append(Zone(z["name"], [tuple(p) for p in z["polygon"]], required))
    return zones
