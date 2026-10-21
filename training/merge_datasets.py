"""Gabungkan beberapa dataset YOLO (SH17, Roboflow, data sendiri) menjadi 4 kelas APD.

Nama kelas dibaca dari data.yaml masing-masing dataset, lalu dipetakan lewat
CLASS_MAP. Kelas lain (person, face, tools, dll.) dibuang.

    python merge_datasets.py --src datasets/sh17 datasets/roboflow-ppe datasets/lokal --out datasets/ppe4

Struktur yang diharapkan tiap sumber (format ekspor YOLO standar):
    <src>/data.yaml
    <src>/<split>/images/*.jpg  dan  <src>/<split>/labels/*.txt     (gaya Roboflow)
atau
    <src>/images/<split>/*.jpg  dan  <src>/labels/<split>/*.txt     (gaya Ultralytics)
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

TARGET = ["helmet", "vest", "gloves", "shoes"]
CLASS_MAP = {
    # helmet
    "helmet": "helmet", "helmets": "helmet", "hardhat": "helmet", "hard hat": "helmet",
    "hard-hat": "helmet", "hard_hat": "helmet", "safety helmet": "helmet", "safety-helmet": "helmet", "head": "helmet",
    "hat": "helmet",
    # vest
    "vest": "vest", "vests": "vest", "safety-vest": "vest", "safety vest": "vest",
    "safety_vest": "vest", "safety-vests": "vest", "reflective vest": "vest", "hi-vis": "vest",
    # gloves
    "gloves": "gloves", "glove": "gloves", "hand glove": "gloves", "hand-gloves": "gloves",
    "safety gloves": "gloves", "safety-gloves": "gloves", "ppe-gloves": "gloves",
    # shoes
    "shoes": "shoes", "shoe": "shoes", "boots": "shoes", "boot": "shoes",
    "safety shoes": "shoes", "safety-shoes": "shoes", "safety_shoes": "shoes",
    "safety shoe": "shoes", "safety_shoe": "shoes", "safety boots": "shoes", "safety-boots": "shoes", "safety_boots": "shoes", "footwear": "shoes",
    "black-safety-shoe": "shoes", "0": "shoes" # '0' in dataset bns218
}
# Kelas yang sengaja diabaikan (bukan APD target) — tak perlu diperingatkan.
IGNORE = {"person", "people", "worker", "face", "head-no-helmet", "no-helmet", "no-vest",
          "no-gloves", "no-shoes", "no_helmet", "no_vest", "person-no-ppe", "tools",
          "machinery", "goggles", "mask", "ear-protection", "glasses", "no-goggles",
          "not_safety_shoe", "no hat", "no vest", "no-hat"}
SPLITS = {"train": "train", "valid": "val", "val": "val", "test": "test"}


def names_of(src: Path) -> list[str]:
    data = yaml.safe_load((src / "data.yaml").read_text())
    names = data["names"]
    return [names[k] for k in sorted(names)] if isinstance(names, dict) else list(names)


def split_dirs(src: Path):
    for s_in, s_out in SPLITS.items():
        for img_dir, lbl_dir in ((src / s_in / "images", src / s_in / "labels"),
                                 (src / "images" / s_in, src / "labels" / s_in)):
            if img_dir.exists():
                yield s_out, img_dir, lbl_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    counts = {c: 0 for c in TARGET}

    for src in map(Path, a.src):
        names = names_of(src)
        remap = {i: TARGET.index(CLASS_MAP[n.lower()]) for i, n in enumerate(names) if n.lower() in CLASS_MAP}
        print(f"{src.name}: {len(names)} kelas, dipakai -> {[names[i] for i in remap]}")
        # Peringatkan nama yang tak ter-map DAN tak sengaja diabaikan — mungkin varian APD
        # yang terlewat (mis. 'Shoe ' dengan spasi/typo). Jangan silent-drop kelas target.
        unknown = [n for i, n in enumerate(names) if i not in remap and n.lower().strip() not in IGNORE]
        if unknown:
            print(f"  ! kelas tak dikenal (dibuang): {unknown} — cek apakah ada varian gloves/shoes "
                  f"yang perlu ditambah ke CLASS_MAP", file=sys.stderr)
        for split, img_dir, lbl_dir in split_dirs(src):
            (out / "images" / split).mkdir(parents=True, exist_ok=True)
            (out / "labels" / split).mkdir(parents=True, exist_ok=True)
            for img in img_dir.iterdir():
                if img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                    continue
                lines = []
                lbl = lbl_dir / f"{img.stem}.txt"
                if lbl.exists():
                    for line in lbl.read_text().splitlines():
                        parts = line.split()
                        if parts and int(parts[0]) in remap:
                            new = remap[int(parts[0])]
                            lines.append(" ".join([str(new)] + parts[1:5]))
                            counts[TARGET[new]] += 1
                stem = f"{src.name}__{img.stem}"
                shutil.copy2(img, out / "images" / split / f"{stem}{img.suffix}")
                (out / "labels" / split / f"{stem}.txt").write_text("\n".join(lines))

    (out / "data.yaml").write_text(yaml.safe_dump({
        "path": str(out.resolve()), "train": "images/train", "val": "images/val", "test": "images/test",
        "names": dict(enumerate(TARGET)),
    }, sort_keys=False))
    print("Jumlah anotasi per kelas:", counts)


if __name__ == "__main__":
    main()
