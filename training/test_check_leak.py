# pyright: reportMissingImports=false
"""Smoke test check_leak tanpa dataset nyata (fixture sintetis di /tmp).

    python training/test_check_leak.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_leak import leaked  # noqa: E402

ROOT = Path("/tmp/_leak_test")


def _write_img(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def main() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    gold = ROOT / "gold"
    train = ROOT / "train"

    # gold: satu frame + manifest berisi sha256-nya
    g_img = gold / "images" / "test" / "gold_00000.jpg"
    _write_img(g_img, b"SHARED-FRAME-BYTES")
    g_sha = hashlib.sha256(b"SHARED-FRAME-BYTES").hexdigest()
    (gold / "manifest.jsonl").write_text(json.dumps({"id": "gold_00000", "sha256": g_sha}) + "\n")

    # 1. train bersih (frame beda) -> tak bocor
    _write_img(train / "a.jpg", b"TRAIN-ONLY-1")
    assert leaked(gold, [train]) == [], "train bersih tak boleh dilaporkan bocor"

    # 2. train mengandung frame gold yang sama -> bocor terdeteksi
    _write_img(train / "leak.jpg", b"SHARED-FRAME-BYTES")
    hits = leaked(gold, [train])
    assert len(hits) == 1 and hits[0].endswith("leak.jpg"), f"harus deteksi 1 bocor, dapat {hits}"

    shutil.rmtree(ROOT, ignore_errors=True)
    print("OK: check_leak smoke test lolos (bersih + bocor terdeteksi)")


if __name__ == "__main__":
    main()
