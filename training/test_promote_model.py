# pyright: reportMissingImports=false
"""Smoke test gate promosi tanpa menjalankan YOLO (fake evaluator).

    python training/test_promote_model.py        # exit 0 = lolos
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from promote_model import promote  # noqa: E402

GOLD = {"map50_95": 0.277, "map50": 0.415, "precision": 0.5, "recall": 0.4,
        "per_class": {"helmet": 0.604, "vest": 0.432, "gloves": 0.045, "shoes": 0.026},
        "weakest_class": "shoes"}
PASS = {"map50_95": 0.72, "map50": 0.9, "precision": 0.8, "recall": 0.8,
        "per_class": {"helmet": 0.9, "vest": 0.85, "gloves": 0.75, "shoes": 0.65},
        "weakest_class": "shoes"}


def _fake(result):
    def ev(*_a, **_k):
        return result
    return ev


def main() -> None:
    dest = Path("/tmp/_promote_test_best.pt")
    dest.write_bytes(b"old")

    # 1. gate gagal (gloves/shoes di bawah ambang) -> SystemExit(1), dest tak berubah
    try:
        promote("cand.pt", "gold/data.yaml", dest, min_map=0.45,
                min_class_map=["shoes=0.6", "gloves=0.7"], _evaluate=_fake(GOLD))
        raise AssertionError("gate seharusnya gagal untuk GOLD")
    except SystemExit as e:
        assert e.code == 1, f"expected exit 1, got {e.code}"
    assert dest.read_bytes() == b"old", "dest tak boleh berubah saat gate gagal"

    # 2. gate lolos + dry_run -> tidak menyalin
    promote("cand.pt", "gold/data.yaml", dest, min_map=0.45,
            min_class_map=["shoes=0.6", "gloves=0.7"], dry_run=True, _evaluate=_fake(PASS))
    assert dest.read_bytes() == b"old", "dry_run tak boleh menyalin"

    # 3. gate lolos + bukan dry_run -> menyalin candidate
    cand = Path("/tmp/_promote_test_cand.pt")
    cand.write_bytes(b"new")
    promote(str(cand), "gold/data.yaml", dest, min_map=0.45,
            min_class_map=["shoes=0.6", "gloves=0.7"], _evaluate=_fake(PASS))
    assert dest.read_bytes() == b"new", "candidate harus disalin saat gate lolos"

    dest.unlink(missing_ok=True)
    cand.unlink(missing_ok=True)
    print("OK: promote_model gate smoke test lolos (gagal/dry-run/lolos)")


if __name__ == "__main__":
    main()
