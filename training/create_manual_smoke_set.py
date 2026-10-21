# pyright: reportMissingImports=false
"""Buat dan cek dataset YOLO kecil untuk smoke-test mAP APD.

Contoh:
    python training/create_manual_smoke_set.py extract --videos backend/data/videos/11.mp4 --out datasets/manual_smoke_ppe_v2
    python training/create_manual_smoke_set.py draft --weights backend/weights/best.pt --data datasets/manual_smoke_ppe_v2/data.yaml
    python training/create_manual_smoke_set.py preview --data datasets/manual_smoke_ppe_v2/data.yaml
    python training/create_manual_smoke_set.py check --data datasets/manual_smoke_ppe_v2/data.yaml
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import math
import random
from collections import Counter
from pathlib import Path

import cv2
import yaml

NAMES = {0: "helmet", 1: "vest", 2: "gloves", 3: "shoes"}
COLORS = {
    0: (255, 60, 20),
    1: (20, 220, 220),
    2: (245, 245, 245),
    3: (20, 220, 120),
}
IMG_EXTS = (".jpg", ".jpeg", ".png")


def write_data_yaml(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "images" / "test").mkdir(parents=True, exist_ok=True)
    (out / "labels" / "test").mkdir(parents=True, exist_ok=True)
    (out / "data.yaml").write_text(
        yaml.safe_dump(
            {"path": str(out.resolve()), "train": "images/test", "val": "images/test", "test": "images/test", "names": NAMES},
            sort_keys=False,
            allow_unicode=True,
        )
    )


def load_data(data_yaml: Path) -> tuple[Path, dict[int, str]]:
    if not data_yaml.exists():
        raise SystemExit(f"data.yaml tidak ditemukan: {data_yaml}")
    data = yaml.safe_load(data_yaml.read_text())
    root = Path(data.get("path", data_yaml.parent))
    if not root.is_absolute():
        root = (data_yaml.parent / root).resolve()
    names = data.get("names", NAMES)
    if isinstance(names, list):
        names = dict(enumerate(names))
    return root, {int(k): str(v) for k, v in names.items()}


def image_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for ext in IMG_EXTS:
        paths.extend((root / "images" / "test").glob(f"*{ext}"))
    return sorted(paths)


def yolo_label_path(root: Path, image: Path) -> Path:
    return root / "labels" / "test" / f"{image.stem}.txt"


def yolo_to_xyxy(row: list[float], w: int, h: int) -> tuple[int, int, int, int]:
    _, cx, cy, bw, bh = row
    x1 = int(round((cx - bw / 2) * w))
    y1 = int(round((cy - bh / 2) * h))
    x2 = int(round((cx + bw / 2) * w))
    y2 = int(round((cy + bh / 2) * h))
    return max(0, x1), max(0, y1), min(w - 1, x2), min(h - 1, y2)


def xyxy_to_yolo(cls: int, xyxy: tuple[float, float, float, float], w: int, h: int) -> str:
    x1, y1, x2, y2 = xyxy
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(float(w - 1), x2), min(float(h - 1), y2)
    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)
    cx = x1 + bw / 2
    cy = y1 + bh / 2
    return f"{cls} {cx / w:.6f} {cy / h:.6f} {bw / w:.6f} {bh / h:.6f}"


def read_labels(path: Path) -> list[list[float]]:
    rows: list[list[float]] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5:
            raise ValueError(f"{path}: label harus 5 field: {line}")
        cls = int(parts[0])
        vals = [float(x) for x in parts[1:]]
        rows.append([float(cls), *vals])
    return rows


def write_readme(out: Path, videos: list[Path]) -> None:
    names = "\n".join(f"- {v}" for v in videos)
    (out / "README.md").write_text(
        f"""# Manual smoke-test APD v2

Dataset lokal untuk smoke-test mAP, bukan benchmark final.

Sumber video:
{names}

Class ID:
- 0 helmet
- 1 vest
- 2 gloves
- 3 shoes

Revisi label:
- Edit `labels/test/*.txt` dalam format YOLO: `<class_id> <x_center> <y_center> <width> <height>`.
- Koordinat ternormalisasi 0..1.
- Jangan pindah gambar ke folder kelas; ini dataset deteksi, label ada di `.txt`.
- Render ulang preview: `python training/create_manual_smoke_set.py preview --data datasets/manual_smoke_ppe_v2/data.yaml`.
- Cek: `python training/create_manual_smoke_set.py check --data datasets/manual_smoke_ppe_v2/data.yaml`.
- Untuk gold-standard, anotasi manual penuh lalu bekukan dengan `python training/make_gold_test.py --src <folder_ini> --out datasets/gold`.
- Jangan pakai folder ini untuk training atau augmentasi. Simpan `manifest.csv` sebagai jejak sumber frame.
"""
    )


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def pick_frames(start: int, end: int, count: int, seed: int | None) -> list[int]:
    if count <= 0:
        return []
    if seed is None:
        return [int(round(x)) for x in linspace(start, end, count)]
    pool = list(range(start, end + 1))
    rnd = random.Random(seed)
    # ponytail: sampling belum scene/class-balanced; tambah strata saat gold set mencakup banyak kamera/kondisi.
    return sorted(rnd.sample(pool, min(count, len(pool))))


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["image", "label", "video", "frame", "timestamp_s", "sha256"])
        writer.writeheader()
        writer.writerows(rows)


def cmd_extract(args: argparse.Namespace) -> None:
    out = Path(args.out)
    videos = [Path(v) for v in args.videos]
    for video in videos:
        if not video.exists():
            raise SystemExit(f"Video tidak ditemukan: {video}")
    write_data_yaml(out)
    write_readme(out, videos)

    written = 0
    rows: list[dict[str, str]] = []
    for video in videos:
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            print(f"PERINGATAN: video dilewati karena tidak bisa dibuka: {video}")
            continue
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        if frames <= 0:
            cap.release()
            continue
        # Avoid very first/last frames; use evenly spaced samples unless seed requests random reproducible picks.
        start = max(0, int(frames * 0.08))
        end = max(start, int(frames * 0.92) - 1)
        picks = pick_frames(start, end, args.frames_per_video, args.seed)
        for frame_no in picks:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
            ok, frame = cap.read()
            if not ok:
                continue
            t = frame_no / fps
            timestamp = f"{t:08.3f}".replace(".", "p")
            name = f"{video.stem}_t{timestamp}.jpg"
            image = out / "images" / "test" / name
            label = out / "labels" / "test" / f"{Path(name).stem}.txt"
            cv2.imwrite(str(image), frame, [cv2.IMWRITE_JPEG_QUALITY, args.quality])
            label.write_text("")
            rows.append({
                "image": str(image.relative_to(out)),
                "label": str(label.relative_to(out)),
                "video": str(video),
                "frame": str(frame_no),
                "timestamp_s": f"{t:.3f}",
                "sha256": sha256(image),
            })
            written += 1
        cap.release()
    write_manifest(Path(args.manifest) if args.manifest else out / "manifest.csv", rows)
    print(f"frames ditulis: {written} -> {out / 'images' / 'test'}")
    print(f"manifest ditulis: {Path(args.manifest) if args.manifest else out / 'manifest.csv'}")


def linspace(start: int, end: int, n: int) -> list[float]:
    if n <= 1:
        return [float(start)]
    step = (end - start) / (n - 1)
    return [start + i * step for i in range(n)]


def cmd_draft(args: argparse.Namespace) -> None:
    from ultralytics import YOLO

    root, names = load_data(Path(args.data))
    weights = Path(args.weights)
    if not weights.exists():
        raise SystemExit(f"Bobot tidak ditemukan: {weights}")
    model = YOLO(str(weights))
    model_names = {int(k): str(v) for k, v in model.names.items()}
    wanted = {v: k for k, v in names.items()}
    model_to_target = {i: wanted[name] for i, name in model_names.items() if name in wanted}
    if not model_to_target:
        raise SystemExit(f"Nama kelas model tidak cocok: {model_names} vs {names}")

    total = Counter()
    for img_path in image_paths(root):
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        result = model.predict(img, conf=args.conf, imgsz=args.imgsz, verbose=False)[0]
        lines: list[str] = []
        for box in result.boxes:
            model_cls = int(box.cls)
            if model_cls not in model_to_target:
                continue
            cls = model_to_target[model_cls]
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            lines.append(xyxy_to_yolo(cls, (x1, y1, x2, y2), w, h))
            total[names[cls]] += 1
        yolo_label_path(root, img_path).write_text("\n".join(lines) + ("\n" if lines else ""))
    print_counts(total)


def cmd_preview(args: argparse.Namespace) -> None:
    root, names = load_data(Path(args.data))
    preview = root / "preview"
    preview.mkdir(parents=True, exist_ok=True)
    total = Counter()
    for img_path in image_paths(root):
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        try:
            rows = read_labels(yolo_label_path(root, img_path))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        for row in rows:
            cls = int(row[0])
            total[names.get(cls, str(cls))] += 1
            x1, y1, x2, y2 = yolo_to_xyxy(row, w, h)
            color = COLORS.get(cls, (0, 255, 0))
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            label = names.get(cls, str(cls))
            cv2.putText(img, label, (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
        cv2.imwrite(str(preview / img_path.name), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(f"preview ditulis: {preview}")
    print_counts(total)


def cmd_check(args: argparse.Namespace) -> None:
    root, names = load_data(Path(args.data))
    images = image_paths(root)
    if not images:
        raise SystemExit(f"Tidak ada gambar di {root / 'images' / 'test'}")
    total = Counter()
    empty = 0
    errors: list[str] = []
    for img_path in images:
        label_path = yolo_label_path(root, img_path)
        if not label_path.exists():
            errors.append(f"label hilang: {label_path}")
            continue
        try:
            rows = read_labels(label_path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not rows:
            empty += 1
        for row in rows:
            cls = int(row[0])
            vals = row[1:]
            if cls not in names:
                errors.append(f"{label_path}: class id di luar names: {cls}")
            if any(not math.isfinite(v) or v < 0 or v > 1 for v in vals):
                errors.append(f"{label_path}: koordinat di luar 0..1: {row}")
            if row[3] <= 0 or row[4] <= 0:
                errors.append(f"{label_path}: width/height tidak positif: {row}")
            total[names.get(cls, str(cls))] += 1
    if errors:
        raise SystemExit("\n".join(errors[:20]))
    print(f"gambar: {len(images)}; label kosong: {empty}")
    print_counts(total)
    for name in names.values():
        if total[name] == 0:
            print(f"PERINGATAN: kelas tanpa label: {name}")


def print_counts(total: Counter) -> None:
    print("label per kelas:")
    for _, name in NAMES.items():
        print(f"  {name}: {total[name]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract", help="ambil frame video ke dataset YOLO test")
    p.add_argument("--videos", nargs="+", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--frames-per-video", type=int, default=8)
    p.add_argument("--quality", type=int, default=92)
    p.add_argument("--seed", type=int, help="acak deterministik; tanpa ini frame diambil merata")
    p.add_argument("--manifest", help="path manifest CSV; default <out>/manifest.csv")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("draft", help="buat label draft dari model")
    p.add_argument("--weights", required=True)
    p.add_argument("--data", required=True)
    p.add_argument("--conf", type=float, default=0.20)
    p.add_argument("--imgsz", type=int, default=960)
    p.set_defaults(func=cmd_draft)

    p = sub.add_parser("preview", help="render preview label YOLO")
    p.add_argument("--data", required=True)
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("check", help="validasi dataset YOLO")
    p.add_argument("--data", required=True)
    p.set_defaults(func=cmd_check)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
