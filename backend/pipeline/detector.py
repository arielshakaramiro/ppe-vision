"""Pembungkus model Ultralytics: pose (orang + keypoint) dan detektor APD. (Batch processing)"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .types import PPEDet, Person
from ultralytics.engine.results import Results

log = logging.getLogger("ppe.detector")


def load_detector(cfg: dict, base_dir: Path) -> "Detector":
    """Buat Detector dari config, dengan pesan error jelas jika bobot APD belum ada."""
    m = cfg["models"]
    weights = Path(m["ppe"])
    weights = weights if weights.is_absolute() else base_dir / weights
    if not weights.exists():
        raise RuntimeError(f"Model APD tidak ditemukan ({m['ppe']}). Latih model dulu, lihat folder training/.")
    pose = Path(m["pose"])
    pose = pose if pose.is_absolute() or len(pose.parts) == 1 else base_dir / pose
    return Detector(str(weights), str(pose), cfg["class_map"], m["device"],
                    m["imgsz"], m["ppe_conf"], m["pose_conf"],
                    m.get("ppe_iou", 0.45), m.get("pose_iou", 0.7), bool(m.get("half", False)),
                    m.get("ppe_conf_item", {}) or {})


class Detector:
    def __init__(self, ppe_weights: str, pose_weights: str, class_map: dict[str, list[str]],
                 device: str = "cpu", imgsz: int = 960, ppe_conf: float = 0.35, pose_conf: float = 0.4,
                 ppe_iou: float = 0.45, pose_iou: float = 0.7, half: bool = False,
                 ppe_conf_item: dict[str, float] | None = None):
        from ultralytics import YOLO

        self.pose = YOLO(pose_weights)
        self.ppe = YOLO(ppe_weights)
        self.device, self.imgsz = device, imgsz
        self.ppe_conf, self.pose_conf = ppe_conf, pose_conf
        self.ppe_iou, self.pose_iou, self.half = ppe_iou, pose_iou, half
        # Ambang conf per-item (opsional). Detektor YOLO tetap memakai `ppe_conf` global yang rendah
        # supaya kelas ber-conf wajar rendah (mis. sarung tangan) tetap keluar; item tertentu yang
        # rawan false positive (mis. helm "hantu" pada kepala telanjang di cahaya redup) bisa diberi
        # ambang lebih tinggi di sini tanpa mematikan kelas lain. Item tak tercantum mengikuti ppe_conf.
        self.ppe_conf_item = {str(k): float(v) for k, v in (ppe_conf_item or {}).items()}
        names = self.ppe.names
        lookup = {alias.lower(): item for item, aliases in class_map.items() for alias in aliases}
        self.cls_to_item = {cid: lookup.get(str(n).lower()) for cid, n in names.items()}
        missing = [i for i in class_map if i not in self.cls_to_item.values()]
        if missing:
            log.warning("Kelas model APD tidak memuat %s. Cek class_map di config.yaml", missing)

    def batch_predict(self, frames: list[np.ndarray], run_ppe: bool = True) -> tuple[list[Results], list[list[PPEDet]]]:
        if not frames:
            return [], []

        # Batch predict for pose
        pose_res = self.pose.predict(frames, conf=self.pose_conf, iou=self.pose_iou, device=self.device,
                                     quantize=16 if self.half else None, verbose=False)

        # Batch predict for PPE
        out_ppe = [[] for _ in frames]
        if run_ppe:
            ppe_res = self.ppe.predict(frames, conf=self.ppe_conf, iou=self.ppe_iou, imgsz=self.imgsz,
                                       device=self.device, quantize=16 if self.half else None, verbose=False)
            for i, r in enumerate(ppe_res):
                if r.boxes is not None:
                    for b, c, cls in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy(), r.boxes.cls.int().cpu().numpy()):
                        item = self.cls_to_item.get(int(cls))
                        if item and float(c) >= self.ppe_conf_item.get(item, 0.0):
                            out_ppe[i].append(PPEDet(item, tuple(map(float, b)), float(c)))

        return pose_res, out_ppe
