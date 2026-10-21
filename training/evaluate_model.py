# pyright: reportMissingImports=false
"""Evaluasi bobot APD pada test set YOLO (normal atau gold) dan cetak mAP per kelas.

    python training/evaluate_model.py --weights backend/weights/best.pt --data datasets/ppe4/data.yaml
    python training/evaluate_model.py --weights best.pt --data datasets/gold/data.yaml \
        --json-out runs/gold.json --min-map 0.45 --min-class-map shoes=0.6 gloves=0.7

Dipakai sebagai gate promosi model: keluar non-zero jika metrik di bawah ambang,
sehingga bisa dipasang di skrip rilis sebelum mengganti backend/weights/best.pt.
"""
import argparse
import json
import sys
from pathlib import Path

from ultralytics import YOLO


def _parse_class_min(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        if "=" in it:
            k, v = it.split("=", 1)
            out[k.strip()] = float(v)
    return out


def evaluate(weights, data, imgsz=960, split="test", device=None, conf=None, iou=None) -> dict:
    """Jalankan val YOLO, kembalikan dict metrik (map50_95, map50, precision, recall, per_class, weakest_class)."""
    weights, data = Path(weights), Path(data)
    if not weights.exists():
        raise SystemExit(f"Bobot tidak ditemukan: {weights}")
    if not data.exists():
        raise SystemExit(f"data.yaml tidak ditemukan: {data}")

    model = YOLO(str(weights))
    kw = {"data": str(data), "split": split, "imgsz": imgsz}
    if device:
        kw["device"] = device
    if conf is not None:
        kw["conf"] = conf
    if iou is not None:
        kw["iou"] = iou
    metrics = model.val(**kw)

    per_class = {name: round(float(value), 4) for name, value in zip(model.names.values(), metrics.box.maps)}
    weakest = min(per_class, key=lambda k: per_class[k]) if per_class else None
    return {
        "weights": str(weights), "data": str(data), "split": split,
        "map50_95": round(float(metrics.box.map), 4), "map50": round(float(metrics.box.map50), 4),
        "precision": round(float(metrics.box.mp), 4), "recall": round(float(metrics.box.mr), 4),
        "per_class": per_class, "weakest_class": weakest,
    }


def gate_problems(result: dict, min_map: float = 0.0, min_class_map=None) -> list[str]:
    """Daftar pelanggaran ambang (kosong = lolos). min_class_map bisa dict atau list 'cls=lo'."""
    problems = []
    if min_map and result["map50_95"] < min_map:
        problems.append(f"mAP50-95 {result['map50_95']} < {min_map}")
    thresholds = min_class_map if isinstance(min_class_map, dict) else _parse_class_min(min_class_map or [])
    for cls, lo in thresholds.items():
        got = result["per_class"].get(cls)
        if got is None:
            problems.append(f"kelas {cls} tidak ada di model")
        elif got < lo:
            problems.append(f"{cls} {got} < {lo}")
    return problems


def print_metrics(result: dict) -> None:
    print("mAP50-95 per kelas:", result["per_class"])
    print(f"mAP50-95 semua kelas: {result['map50_95']:.3f}")
    print(f"mAP50 semua kelas: {result['map50']:.3f}")
    print(f"precision: {result['precision']:.3f}  recall: {result['recall']:.3f}")
    print("kelas terlemah:", result["weakest_class"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True, help="Path bobot .pt, mis. backend/weights/best.pt")
    ap.add_argument("--data", required=True, help="Path data.yaml YOLO dengan split test beranotasi")
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--split", default="test", choices=["train", "val", "test"])
    ap.add_argument("--device")
    ap.add_argument("--conf", type=float)
    ap.add_argument("--iou", type=float)
    ap.add_argument("--json-out")
    ap.add_argument("--min-map", type=float, default=0.0, help="ambang minimum mAP50-95 rata-rata")
    ap.add_argument("--min-class-map", nargs="*", default=[], help="mis. shoes=0.6 gloves=0.7")
    a = ap.parse_args()

    result = evaluate(a.weights, a.data, imgsz=a.imgsz, split=a.split,
                      device=a.device, conf=a.conf, iou=a.iou)
    print_metrics(result)

    if a.json_out:
        Path(a.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json_out).write_text(json.dumps(result, indent=2))
        print(f"metrik ditulis ke {a.json_out}")

    problems = gate_problems(result, a.min_map, a.min_class_map)
    if problems:
        print("GATE GAGAL:", "; ".join(problems), file=sys.stderr)
        sys.exit(1)
    if a.min_map or a.min_class_map:
        print("GATE LOLOS")


if __name__ == "__main__":
    main()
