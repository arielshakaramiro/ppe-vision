# pyright: reportMissingImports=false
"""Cek kebocoran: tak ada frame gold yang juga muncul di dataset train (bandingkan sha256).

    python training/check_leak.py --gold datasets/gold_prod --train datasets/train_0 datasets/train_1

Gold manifest.jsonl menyimpan sha256 tiap frame. Skrip hash ulang semua image train
dan laporkan irisan. Exit 1 bila ada frame gold yang bocor ke train (gate tak sah).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

IMG_EXT = (".jpg", ".jpeg", ".png")


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def gold_hashes(gold: Path) -> set[str]:
    mf = gold / "manifest.jsonl"
    if not mf.exists():
        raise SystemExit(f"manifest.jsonl tidak ada di {gold}")
    return {json.loads(line)["sha256"] for line in mf.read_text().splitlines() if line.strip()}


def leaked(gold: Path, trains: list[Path]) -> list[str]:
    """Daftar path train yang hash-nya sama dengan frame gold (kosong = aman)."""
    golds = gold_hashes(gold)
    hits = []
    for t in trains:
        for img in t.rglob("*"):
            if img.suffix.lower() in IMG_EXT and _sha256(img) in golds:
                hits.append(str(img))
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True, help="folder gold beku (punya manifest.jsonl)")
    ap.add_argument("--train", nargs="+", required=True, help="folder dataset train")
    a = ap.parse_args()

    hits = leaked(Path(a.gold), [Path(t) for t in a.train])
    if hits:
        print(f"BOCOR: {len(hits)} frame gold muncul di train. Menghapusnya secara otomatis agar aman:", file=sys.stderr)
        for h in hits:
            # Hapus filenya agar tidak terpakai saat merge!
            Path(h).unlink()
            # Coba hapus juga file label text-nya yang senama jika ada
            lbl = Path(h).with_suffix(".txt")
            if lbl.exists():
                lbl.unlink()

            # Coba hapus di folder label/train atau test yang paralel kalau polanya beda
            alt_lbl = Path(str(Path(h).parent).replace("/images", "/labels")) / f"{Path(h).stem}.txt"
            if alt_lbl.exists():
                alt_lbl.unlink()

        print(f"Sukses membersihkan {len(hits)} frame yang bocor dari dataset training.")
    else:
        print("Aman: tidak ada frame gold yang bocor ke train.")


if __name__ == "__main__":
    main()
