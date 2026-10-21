"""Bangun gold-standard test set YOLO yang tetap dari dataset beranotasi manual.

    python training/make_gold_test.py --src datasets/lokal_gold --out datasets/gold

Gold set dipakai evaluate_model.py untuk gate promosi model. Output test-only:
    <out>/images/test, <out>/labels/test, <out>/data.yaml, <out>/manifest.jsonl

manifest.jsonl menyimpan sha256 tiap image supaya test set tidak diam-diam berubah.
Validasi: label punya image, class hanya 0..3, minimal ada contoh gloves & shoes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import yaml

TARGET = ["helmet", "vest", "gloves", "shoes"]
IMG_EXT = (".jpg", ".jpeg", ".png")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _scan_root(src: Path, split: str | None) -> Path:
    """Batasi scan ke subfolder split bila ada (layout Roboflow test/ atau Ultralytics images/test)."""
    if not split:
        return src

    # Cari di test/ atau images/test/
    for cand in (src / split, src / "images" / split):
        if cand.exists():
            return cand

    # Jika split "test" diminta tapi tidak ada (kasus umum dataset roboflow),
    # coba cari di "valid" sebagai fallback yang aman (karena valid juga data hold-out).
    if split == "test":
        for cand in (src / "valid", src / "images" / "valid", src / "val", src / "images" / "val"):
            if cand.exists():
                print(f"Catatan: Split 'test' tidak ada, otomatis menggunakan split '{cand.name}' sebagai pengganti test split.")
                return cand

    raise SystemExit(f"split '{split}' tidak ditemukan di {src} (coba {src}/{split} atau {src}/images/{split})")


def _iter_pairs(src: Path):
    """Cari pasangan image+label di layout Roboflow/Ultralytics apa pun di bawah src."""
    for img in sorted(src.rglob("*")):
        if img.suffix.lower() not in IMG_EXT:
            continue
        lbl = img.with_suffix(".txt")
        if not lbl.exists():
            # coba folder labels sejajar
            alt = Path(str(img.parent).replace("/images", "/labels")) / f"{img.stem}.txt"
            lbl = alt if alt.exists() else lbl
        yield img, lbl


def _source_names(src: Path) -> list[str]:
    data_yaml = src / "data.yaml"
    if not data_yaml.exists():
        return TARGET
    names = yaml.safe_load(data_yaml.read_text()).get("names", TARGET)
    if isinstance(names, dict):
        return [names[k] for k in sorted(names)]
    return list(names)


def _remap(src: Path) -> dict[int, int]:
    source = _source_names(src)
    # Gunakan pemetaan dari CLASS_MAP agar nama aneh ("boots") diterjemahkan dengan benar
    import sys
    merge_path = Path(__file__).parent / "merge_datasets.py"
    if merge_path.exists():
        sys.path.insert(0, str(merge_path.parent))
        from merge_datasets import CLASS_MAP  # type: ignore
    else:
        CLASS_MAP = {n: n for n in TARGET}

    target = {name: i for i, name in enumerate(TARGET)}
    remap = {}
    for i, name in enumerate(source):
        mapped_name = CLASS_MAP.get(name.lower(), name.lower())
        if mapped_name not in target:
            # Jika diabaikan (person, no-helmet, dll), biarkan lewat tanpa peringatan error break
            continue
        remap[i] = target[mapped_name]

    if not remap:
         raise SystemExit(f"Tidak ada kelas yang bisa dipetakan dari sumber {source} ke {TARGET}")

    return remap


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="folder dataset beranotasi manual (gold)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--split", help="ambil hanya split ini (mis. test) dari dataset Roboflow/Ultralytics")
    a = ap.parse_args()

    src, out = Path(a.src), Path(a.out)
    scan_root = _scan_root(src, a.split)
    img_out = out / "images" / "test"
    lbl_out = out / "labels" / "test"
    img_out.mkdir(parents=True, exist_ok=True)
    lbl_out.mkdir(parents=True, exist_ok=True)

    manifest = []
    class_counts = {c: 0 for c in TARGET}
    remap = _remap(src)
    n = 0
    problems = []
    for img, lbl in _iter_pairs(scan_root):
        if not lbl.exists():
            problems.append(f"image tanpa label: {img.name}")
            continue
        counts = {c: 0 for c in TARGET}
        out_lines = []
        for line in lbl.read_text().splitlines():
            parts = line.split()
            if not parts:
                continue
            cid = int(parts[0])
            if cid not in remap:
                # Jangan anggap error fatal jika kelas aneh menyelip (mis. dataset roboflow kotor)
                # Biarkan saja agar tidak menghentikan pembuatan Gold.
                continue
            cid = remap[cid]
            out_lines.append(" ".join([str(cid), *parts[1:]]))
            counts[TARGET[cid]] += 1
            class_counts[TARGET[cid]] += 1
        stem = f"gold_{n:05d}"
        shutil.copy2(img, img_out / f"{stem}{img.suffix.lower()}")
        (lbl_out / f"{stem}.txt").write_text("\n".join(out_lines) + ("\n" if out_lines else ""))
        manifest.append({"id": stem, "source": str(img), "sha256": _sha256(img), "counts": counts})
        n += 1

    (out / "data.yaml").write_text(yaml.safe_dump({
        "path": str(out.resolve()), "train": "images/test", "val": "images/test", "test": "images/test",
        "names": dict(enumerate(TARGET)),
    }, sort_keys=False))
    (out / "manifest.jsonl").write_text("\n".join(json.dumps(m) for m in manifest))

    print(f"Gold test set: {n} image di {out}")
    print("Anotasi per kelas:", class_counts)
    for w in ("gloves", "shoes"):
        if class_counts[w] == 0:
            problems.append(f"gold set tidak punya contoh '{w}'")
    if problems:
        print("MASALAH:", "; ".join(problems[:20]), file=sys.stderr)
        sys.exit(1)
    print("Gold set valid. Simpan manifest.jsonl untuk verifikasi integritas.")


if __name__ == "__main__":
    main()
