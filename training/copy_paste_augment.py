"""Copy-paste augmentation untuk kelas yang lemah (biasanya sarung tangan dan sepatu).

Objek kecil seperti sarung tangan jarang muncul dan sulit dipelajari model. Script ini mengambil
potongan objek kelas target dari data TRAIN, lalu menempelkannya ke gambar train lain di posisi acak
dengan tepi yang dihaluskan. Label YOLO diperbarui otomatis. Data val/test TIDAK disentuh, supaya
evaluasi tetap jujur.

    python copy_paste_augment.py --data datasets/ppe4/data.yaml --classes gloves shoes --out datasets/ppe4_cp

Cara membuktikan manfaatnya: latih dua model dengan pengaturan sama (tanpa vs dengan augmentasi),
bandingkan mAP per kelas di test set. Laporkan apa adanya, termasuk jika tidak membantu.
"""
from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path

import cv2
import numpy as np
import yaml


def read_labels(path: Path) -> list[list[float]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        p = line.split()
        if len(p) >= 5:
            rows.append([int(p[0])] + [float(x) for x in p[1:5]])
    return rows


def to_xyxy(row, w, h):
    _, cx, cy, bw, bh = row
    return (cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h


def iou(a, b) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def collect_crops(img_dir: Path, lbl_dir: Path, class_ids: set[int], min_px: int = 12) -> list[tuple[int, np.ndarray]]:
    crops = []
    for img_path in sorted(img_dir.iterdir()):
        rows = [r for r in read_labels(lbl_dir / f"{img_path.stem}.txt") if r[0] in class_ids]
        if not rows:
            continue
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        for r in rows:
            x1, y1, x2, y2 = map(int, to_xyxy(r, w, h))
            x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
            if x2 - x1 >= min_px and y2 - y1 >= min_px:
                crops.append((r[0], img[y1:y2, x1:x2].copy()))
    return crops


def paste(img: np.ndarray, crop: np.ndarray, x: int, y: int) -> None:
    """Tempel dengan tepi halus (feathering) supaya model tidak belajar mengenali garis potong."""
    ch, cw = crop.shape[:2]
    mask = np.zeros((ch, cw), np.float32)
    pad = max(1, min(ch, cw) // 8)
    mask[pad:ch - pad, pad:cw - pad] = 1
    k = pad * 2 + 1
    mask = cv2.GaussianBlur(mask, (k, k), 0)[..., None]
    roi = img[y:y + ch, x:x + cw].astype(np.float32)
    img[y:y + ch, x:x + cw] = (crop.astype(np.float32) * mask + roi * (1 - mask)).astype(np.uint8)


def augment_image(img: np.ndarray, labels: list, crops: list, per_image: int, rng: random.Random) -> list:
    h, w = img.shape[:2]
    boxes = [to_xyxy(r, w, h) for r in labels]
    new = []
    for _ in range(per_image * 5):  # beberapa percobaan untuk mencari posisi kosong
        if len(new) >= per_image:
            break
        cls, crop = rng.choice(crops)
        scale = rng.uniform(0.7, 1.3)
        cw, ch = max(4, int(crop.shape[1] * scale)), max(4, int(crop.shape[0] * scale))
        if cw >= w * 0.5 or ch >= h * 0.5:
            continue
        x, y = rng.randint(0, w - cw), rng.randint(0, h - ch)
        box = (x, y, x + cw, y + ch)
        if any(iou(box, b) > 0.05 for b in boxes):
            continue
        piece = cv2.resize(crop, (cw, ch))
        if rng.random() < 0.5:
            piece = cv2.flip(piece, 1)
        paste(img, piece, x, y)
        boxes.append(box)
        new.append([cls, (x + cw / 2) / w, (y + ch / 2) / h, cw / w, ch / h])
    return new


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--classes", nargs="+", default=["gloves", "shoes"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--fraction", type=float, default=0.5, help="bagian gambar train yang diberi salinan augmentasi")
    ap.add_argument("--per-image", type=int, default=3)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    rng = random.Random(a.seed)
    data = yaml.safe_load(Path(a.data).read_text())
    root = Path(data.get("path") or Path(a.data).parent)
    names = data["names"]
    names = [names[k] for k in sorted(names)] if isinstance(names, dict) else list(names)
    class_ids = {names.index(c) for c in a.classes if c in names}
    if not class_ids:
        raise SystemExit(f"Kelas {a.classes} tidak ada di data.yaml ({names})")

    out = Path(a.out)
    if out.exists():
        raise SystemExit(f"Folder output sudah ada: {out}")
    shutil.copytree(root, out, ignore=shutil.ignore_patterns("*.cache"))
    img_dir, lbl_dir = out / data["train"], out / data["train"].replace("images", "labels", 1)

    crops = collect_crops(img_dir, lbl_dir, class_ids)
    if not crops:
        raise SystemExit("Tidak ada potongan objek kelas target di data train")
    originals = sorted(p for p in img_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    chosen = rng.sample(originals, int(len(originals) * a.fraction))
    added = 0
    for p in chosen:
        img = cv2.imread(str(p))
        if img is None:
            continue
        labels = read_labels(lbl_dir / f"{p.stem}.txt")
        new = augment_image(img, labels, crops, a.per_image, rng)
        if not new:
            continue
        cv2.imwrite(str(img_dir / f"{p.stem}_cp{p.suffix}"), img)
        rows = [f"{int(r[0])} {r[1]:.6f} {r[2]:.6f} {r[3]:.6f} {r[4]:.6f}" for r in labels + new]
        (lbl_dir / f"{p.stem}_cp.txt").write_text("\n".join(rows))
        added += len(new)

    data["path"] = str(out.resolve())
    (out / "data.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    print(f"{len(crops)} potongan sumber, {len(chosen)} gambar baru, {added} objek ditambahkan -> {out}")


if __name__ == "__main__":
    main()
