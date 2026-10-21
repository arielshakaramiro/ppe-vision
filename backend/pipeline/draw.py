"""Menggambar anotasi (kotak, checklist APD, zona) dan blur wajah."""
from __future__ import annotations

import cv2
import numpy as np

from .types import ITEMS, L_EAR, L_EYE, NOSE, R_EAR, R_EYE, Box

COLORS = {  # BGR, disamakan dengan token status di frontend/src/styles.css
    "compliant": (148, 203, 140),   # --good  #8CCB94
    "violation": (89, 97, 255),     # --bad   #FF6159
    "uncertain": (5, 183, 242),     # --warn  #F2B705
}
LABEL = {"helmet": "Helm", "vest": "Rompi", "gloves": "Sarung tangan", "shoes": "Sepatu"}
MARK = {"present": "OK", "missing": "X", "unknown": "?", "not_required": "-"}
ITEM_COLORS = {
    "helmet": (40, 210, 255),
    "vest": (80, 220, 120),
    "gloves": (255, 190, 80),
    "shoes": (220, 120, 255),
}


def _compact_detail_line(status: str, checklist: dict[str, str]) -> str:
    missing = [LABEL[i] for i in ITEMS if checklist.get(i) == "missing"]
    unknown = [LABEL[i] for i in ITEMS if checklist.get(i, "unknown") == "unknown"]
    if missing:
        return "Kurang: " + ", ".join(missing)
    if status == "uncertain" and unknown:
        return "Cek: " + ", ".join(unknown)
    if status == "compliant":
        return "Lengkap"
    return "Cek APD"


def head_box(box: Box, keypoints: np.ndarray | None, thr: float = 0.5) -> Box:
    x1, y1, x2, y2 = box
    h = y2 - y1
    if keypoints is not None:
        idx = [i for i in (NOSE, L_EYE, R_EYE, L_EAR, R_EAR) if keypoints[i, 2] >= thr]
        if idx:
            xs, ys = keypoints[idx, 0], keypoints[idx, 1]
            s = 0.09 * h
            return (xs.min() - s, ys.min() - s, xs.max() + s, ys.max() + s)
    return (x1, y1, x2, y1 + 0.18 * h)


def blur_region(img: np.ndarray, box: Box) -> None:
    H, W = img.shape[:2]
    x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
    x2, y2 = min(W, int(box[2])), min(H, int(box[3]))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return
    roi = img[y1:y2, x1:x2]
    k = max(15, ((x2 - x1) // 3) | 1)
    img[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (k, k), 0)


def _text_style(img: np.ndarray, scale: float, min_scale: float = 0.55) -> tuple[float, int]:
    factor = max(1.0, min(img.shape[1], img.shape[0]) / 640.0)
    scale = max(min_scale, scale * factor)
    thickness = max(2, int(round(2 * factor)))
    return scale, thickness


def _label_box(img: np.ndarray, text: str, x: int, y: int, color: tuple[int, int, int], scale: float = 0.62) -> None:
    scale, thickness = _text_style(img, scale, 0.58)
    max_w = max(40, img.shape[1] - 12)
    while scale > 0.42:
        (w, h), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
        if w + 10 <= max_w:
            break
        scale *= 0.9
        thickness = max(1, int(round(thickness * 0.9)))
    (w, h), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    panel_w = min(w + 10, img.shape[1] - 1)
    x = max(0, min(x, img.shape[1] - panel_w - 1))
    y = max(h + base + 7, min(y, img.shape[0] - 6))
    top = max(0, y - h - base - 7)
    bottom = min(img.shape[0] - 1, y + 5)
    cv2.rectangle(img, (x, top), (x + panel_w, bottom), (20, 20, 20), -1)
    cv2.rectangle(img, (x, top), (x + panel_w, bottom), color, 2)
    cv2.putText(img, text, (x + 5, min(img.shape[0] - 4, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def _draw_item_box(img: np.ndarray, item: str, box: Box, status: str) -> None:
    if status == "not_required":
        return
    color = ITEM_COLORS.get(item, (220, 220, 220))
    if status == "missing":
        color = COLORS["violation"]
    elif status == "unknown":
        color = COLORS["uncertain"]
    x1, y1, x2, y2 = map(int, box)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    _label_box(img, f"{LABEL[item]} {MARK[status]}", x1, y1 - 4, color, 0.52)


def draw_ppe_items(img: np.ndarray, items) -> None:
    """Gambar kotak APD mentah agar setiap part terlihat, bukan hanya kotak orang."""
    for det in items or []:
        if det.item not in ITEM_COLORS:
            continue
        x1, y1, x2, y2 = map(int, det.box)
        color = ITEM_COLORS[det.item]
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        _label_box(img, f"{LABEL[det.item]} {det.conf:.2f}", x1, y1 - 4, color, 0.5)


def draw_person(img: np.ndarray, box: Box, display_id: int, status: str, checklist: dict[str, str], keypoints=None,
                assoc_cfg=None, draw_regions: bool = True, compact: bool = False) -> None:
    color = COLORS.get(status, COLORS["uncertain"])
    x1, y1, x2, y2 = map(int, box)
    _, box_thickness = _text_style(img, 0.62, 0.62)
    cv2.rectangle(img, (x1, y1), (x2, y2), color, max(3, box_thickness))
    title = f"W{display_id:03d} " + {"compliant": "APD OK", "violation": "PELANGGARAN", "uncertain": "TIDAK PASTI"}[status]
    lines = [title, _compact_detail_line(status, checklist)] if compact else [title] + [f"{LABEL[i]}: {MARK[checklist.get(i, 'unknown')]}" for i in ITEMS]
    scale, thickness = _text_style(img, 0.74 if compact else 0.68, 0.66 if compact else 0.68)
    max_panel_w = max(48, img.shape[1] - 2)
    max_panel_h = max(32, img.shape[0] - 2)
    while scale > 0.42:
        text_w = max(cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0][0] for t in lines)
        row_h = max(22, int(round(30 * scale)))
        panel_w = text_w + 16
        panel_h = row_h * len(lines) + 8
        if panel_w <= max_panel_w and panel_h <= max_panel_h:
            break
        scale *= 0.9
        thickness = max(1, int(round(thickness * 0.9)))
    text_w = max(cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0][0] for t in lines)
    row_h = max(22, int(round(30 * scale)))
    w = min(text_w + 16, max_panel_w)
    h_panel = min(row_h * len(lines) + 8, max_panel_h)
    above_y = y1 - h_panel - 8
    below_y = y2 + 8
    if above_y >= 0:
        y = above_y
    elif below_y + h_panel < img.shape[0]:
        y = below_y
    else:
        y = max(0, min(above_y, img.shape[0] - h_panel - 1))
    label_x = max(0, min(x1, img.shape[1] - w - 1))
    cv2.rectangle(img, (label_x, y), (label_x + w, y + h_panel), (20, 20, 20), -1)
    cv2.rectangle(img, (label_x, y), (label_x + w, y + h_panel), color, 2)
    for n, text in enumerate(lines):
        c = color if n == 0 else (230, 230, 230)
        baseline = min(y + h_panel - 4, y + int(row_h * 0.76) + row_h * n)
        cv2.putText(img, text, (label_x + 6, baseline), cv2.FONT_HERSHEY_SIMPLEX, scale, c, thickness, cv2.LINE_AA)

    if draw_regions:
        from .association import AssocConfig, body_regions
        from .types import Person
        regions = body_regions(Person(display_id, box, 1.0, keypoints), assoc_cfg or AssocConfig())
        for item in ITEMS:
            status_item = checklist.get(item, "unknown")
            if status_item in ("present", "missing", "unknown"):
                for reg in regions[item]:
                    if reg.visible or status_item != "unknown":
                        _draw_item_box(img, item, reg.box, status_item)


def draw_zones(img: np.ndarray, zones) -> None:
    H, W = img.shape[:2]
    for z in zones:
        pts = np.array([[int(x * W), int(y * H)] for x, y in z.polygon], np.int32)
        if len(pts) == 4 and pts.min() <= 0 and (pts[:, 0].max() >= W - 1):
            continue  # zona seluruh frame tidak perlu digambar
        cv2.polylines(img, [pts], True, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(img, z.name, tuple(pts[0] + [6, 18]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
