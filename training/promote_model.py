# pyright: reportMissingImports=false
"""Gate promosi model: ganti backend/weights/best.pt HANYA jika candidate lolos gate gold.

    python training/promote_model.py --candidate runs/train/weights/best.pt \
        --data datasets/gold/data.yaml --min-map 0.45 --min-class-map shoes=0.6 gloves=0.7
    python training/promote_model.py --candidate best.pt --data datasets/gold/data.yaml \
        --min-map 0.45 --dry-run          # cek gate tanpa menyalin

Penegak aturan promosi di docs/AUDIT.md: bobot baru tidak menggantikan best.pt
sebelum evaluate_model.py lolos gate pada gold set (gloves/shoes tidak boleh regresi).
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from evaluate_model import evaluate, gate_problems, print_metrics


def promote(candidate, data, dest, *, min_map=0.0, min_class_map=None, imgsz=960,
            split="test", json_out=None, dry_run=False, _evaluate=evaluate) -> dict:
    """Eval candidate pada gold, tegakkan gate, salin ke dest bila lolos (kecuali dry_run).

    Kembalikan result metrik. Raise SystemExit(1) bila gate gagal — best.pt tak tersentuh.
    """
    result = _evaluate(candidate, data, imgsz=imgsz, split=split)
    print_metrics(result)
    if json_out:
        Path(json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(json_out).write_text(json.dumps(result, indent=2))

    problems = gate_problems(result, min_map, min_class_map)
    if problems:
        print("GATE GAGAL:", "; ".join(problems), file=sys.stderr)
        print(f"best.pt TIDAK diganti ({dest}).", file=sys.stderr)
        sys.exit(1)

    print("GATE LOLOS.")
    if dry_run:
        print("--dry-run: candidate tidak disalin.")
        return result
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate, dest)
    print(f"Candidate disalin ke {dest}. Naikkan models.version di backend/config.yaml lalu commit.")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True, help="Path bobot .pt hasil training baru")
    ap.add_argument("--data", required=True, help="Path data.yaml gold set beku")
    ap.add_argument("--dest", default="backend/weights/best.pt", help="Tujuan promosi")
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--split", default="test", choices=["train", "val", "test"])
    ap.add_argument("--json-out")
    ap.add_argument("--min-map", type=float, default=0.0)
    ap.add_argument("--min-class-map", nargs="*", default=[], help="mis. shoes=0.6 gloves=0.7")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    promote(a.candidate, a.data, a.dest, min_map=a.min_map, min_class_map=a.min_class_map,
            imgsz=a.imgsz, split=a.split, json_out=a.json_out, dry_run=a.dry_run)


if __name__ == "__main__":
    main()
