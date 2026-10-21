"""Bantu siapkan dataset APD sebelum training: cek kesehatan, tulis manifest, ambil sampel anotasi.

    python training/prepare_dataset.py check --data datasets/ppe4/data.yaml
    python training/prepare_dataset.py manifest --data datasets/ppe4/data.yaml --out manifest.csv
    python training/prepare_dataset.py sample --src datasets/raw_frames --out datasets/to_annotate --count 200

Fokus pada kelas lemah `gloves` dan `shoes`: `check` menandai jika count-nya rendah/nol,
supaya jelas kapan perlu kumpulkan/anotasi lebih banyak atau pakai copy_paste_augment.py.
Tanpa dependency tambahan (stdlib + yaml + cv2 yang sudah dipakai backend).
"""
from __future__ import annotations

import argparse
import csv
import random
import shutil
import sys
from pathlib import Path

import yaml

IMG_EXT = (".jpg", ".jpeg", ".png")
WEAK = ("gloves", "shoes")
WEAK_MIN = 50  # di bawah ini dianggap perlu tambahan data


def _names(data_yaml: Path) -> list[str]:
    data = yaml.safe_load(data_yaml.read_text())
    names = data["names"]
    return [names[k] for k in sorted(names)] if isinstance(names, dict) else list(names)


def _split_dirs(root: Path):
    """Yield (split, img_dir, lbl_dir) untuk layout Ultralytics images/<split>."""
    for split in ("train", "val", "test"):
        img_dir = root / "images" / split
        if img_dir.exists():
            yield split, img_dir, root / "labels" / split


def _count(data_yaml: Path) -> dict:
    root = data_yaml.parent
    names = _names(data_yaml)
    per_split = {}
    class_counts = {n: 0 for n in names}
    missing_labels = []
    for split, img_dir, lbl_dir in _split_dirs(root):
        imgs = [p for p in img_dir.iterdir() if p.suffix.lower() in IMG_EXT]
        labeled = 0
        for img in imgs:
            lbl = lbl_dir / f"{img.stem}.txt"
            if not lbl.exists():
                missing_labels.append(str(img.relative_to(root)))
                continue
            text = lbl.read_text().strip()
            if text:
                labeled += 1
            for line in text.splitlines():
                parts = line.split()
                if parts:
                    cid = int(parts[0])
                    if 0 <= cid < len(names):
                        class_counts[names[cid]] += 1
        per_split[split] = {"images": len(imgs), "labeled": labeled}
    return {"names": names, "per_split": per_split, "class_counts": class_counts,
            "missing_labels": missing_labels}


def cmd_check(a) -> int:
    data_yaml = Path(a.data)
    r = _count(data_yaml)
    print("Kelas:", r["names"])
    for split, c in r["per_split"].items():
        print(f"  {split}: {c['images']} image, {c['labeled']} berlabel")
    print("Anotasi per kelas:", r["class_counts"])
    problems = []
    for w in WEAK:
        n = r["class_counts"].get(w, 0)
        if n == 0:
            problems.append(f"kelas lemah '{w}' KOSONG")
        elif n < WEAK_MIN:
            problems.append(f"kelas lemah '{w}' rendah ({n} < {WEAK_MIN})")
    if r["missing_labels"]:
        problems.append(f"{len(r['missing_labels'])} image tanpa file label")
    if problems:
        print("PERINGATAN:", "; ".join(problems))
        return 1
    print("Dataset OK")
    return 0


def cmd_manifest(a) -> int:
    data_yaml = Path(a.data)
    root = data_yaml.parent
    names = _names(data_yaml)
    rows = []
    for split, img_dir, lbl_dir in _split_dirs(root):
        for img in sorted(img_dir.iterdir()):
            if img.suffix.lower() not in IMG_EXT:
                continue
            lbl = lbl_dir / f"{img.stem}.txt"
            counts = {n: 0 for n in names}
            if lbl.exists():
                for line in lbl.read_text().splitlines():
                    parts = line.split()
                    if parts:
                        cid = int(parts[0])
                        if 0 <= cid < len(names):
                            counts[names[cid]] += 1
            rows.append({"image": str(img.relative_to(root)), "split": split,
                         "has_label": lbl.exists(), **counts})
    out = Path(a.out)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["image", "split", "has_label", *names])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} baris ditulis ke {out}")
    return 0


def cmd_sample(a) -> int:
    src = Path(a.src)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    imgs = [p for p in src.rglob("*") if p.suffix.lower() in IMG_EXT]
    rnd = random.Random(a.seed)
    rnd.shuffle(imgs)
    picked = imgs[: a.count]
    with (out / "manifest.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "source"])
        for i, img in enumerate(picked):
            dest = out / f"{i:05d}{img.suffix.lower()}"
            shutil.copy2(img, dest)
            w.writerow([dest.name, str(img)])
    print(f"{len(picked)} image disalin ke {out} untuk anotasi (CVAT/Roboflow)")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check"); p.add_argument("--data", required=True); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("manifest"); p.add_argument("--data", required=True)
    p.add_argument("--out", default="manifest.csv"); p.set_defaults(fn=cmd_manifest)
    p = sub.add_parser("sample"); p.add_argument("--src", required=True); p.add_argument("--out", required=True)
    p.add_argument("--count", type=int, default=200); p.add_argument("--seed", type=int, default=42)
    p.set_defaults(fn=cmd_sample)
    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
