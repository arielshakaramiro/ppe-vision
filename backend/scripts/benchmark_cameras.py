"""Benchmark multi-kamera: pakai satu video uji sebagai beberapa kamera serentak.

    python -m scripts.benchmark_cameras --cams 1 2 3 4 --seconds 60 --source data/videos/11.mp4
    python -m scripts.benchmark_cameras --cams 1 2 4 --ppe-every-n 2 --half --imgsz 640

Keluaran: waktu per batch (avg + p50/p95), pemeriksaan per detik per kamera, CPU, memori, perangkat.
Angka HARUS ditulis ke README/PRD hanya dari run nyata (butuh weights + video uji).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import BASE_DIR, load_config
from pipeline.camera import CameraReader
from pipeline.detector import load_detector


def run(n_cams: int, seconds: int, source: str, overrides: dict | None = None,
        ppe_every_n: int = 1, warmup: float = 2.0) -> dict:
    cfg = load_config()
    if overrides:
        cfg["models"].update({k: v for k, v in overrides.items() if v is not None})
    detector = load_detector(cfg, BASE_DIR)
    readers = []
    for i in range(n_cams):
        r = CameraReader(f"BENCH-{i+1:02d}", source, loop=True, realtime=False)
        r.start()
        readers.append(r)
    time.sleep(1.0)  # beri waktu reader mengisi frame pertama

    batch_times: list[float] = []
    checks = 0
    ppe_calls = 0
    frame_i = 0
    t_start = time.time()
    t_end = t_start + seconds + warmup
    try:
        while time.time() < t_end:
            frames = [r.read() for r in readers]
            frames = [f for f in frames if f is not None]
            if not frames:
                time.sleep(0.005)
                continue
            run_ppe = frame_i % max(1, ppe_every_n) == 0
            t0 = time.time()
            detector.batch_predict(frames, run_ppe=run_ppe)
            dt = time.time() - t0
            frame_i += 1
            if time.time() - t_start < warmup:
                continue  # buang warmup dari statistik
            batch_times.append(dt)
            checks += len(frames)
            if run_ppe:
                ppe_calls += len(frames)
    finally:
        for r in readers:
            r.stop()

    elapsed = max(seconds, 1e-6)
    ms = [t * 1000 for t in batch_times]
    avg_batch_ms = round(statistics.fmean(ms), 1) if ms else 0.0
    p50 = round(statistics.median(ms), 1) if ms else 0.0
    p95 = round(sorted(ms)[max(0, int(len(ms) * 0.95) - 1)], 1) if ms else 0.0
    rate_total = round(checks / elapsed, 1)
    rate_per_cam = round(rate_total / n_cams, 1) if n_cams else 0.0
    m = cfg["models"]
    result = {
        "kamera": n_cams, "detik": seconds, "waktu_batch_ms": avg_batch_ms,
        "batch_p50_ms": p50, "batch_p95_ms": p95,
        "pemeriksaan_per_detik_total": rate_total, "pemeriksaan_per_detik_per_kamera": rate_per_cam,
        "ppe_calls_per_detik": round(ppe_calls / elapsed, 1),
        "jumlah_batch": len(batch_times), "device": m.get("device", "cpu"),
        "imgsz": m.get("imgsz"), "half": bool(m.get("half", False)), "ppe_every_n": ppe_every_n,
    }
    try:
        import psutil  # opsional
        result["cpu_persen"] = psutil.cpu_percent()
        result["memori_mb"] = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
    except ImportError:
        pass
    return result


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--source", default="data/videos/11.mp4")
    ap.add_argument("--device")
    ap.add_argument("--imgsz", type=int)
    ap.add_argument("--ppe-conf", type=float)
    ap.add_argument("--pose-conf", type=float)
    ap.add_argument("--ppe-iou", type=float)
    ap.add_argument("--pose-iou", type=float)
    ap.add_argument("--half", action="store_true")
    ap.add_argument("--ppe-every-n", type=int, default=1)
    ap.add_argument("--warmup", type=float, default=2.0)
    ap.add_argument("--json-out", help="tulis raw evidence benchmark ke JSON")
    args = ap.parse_args()

    overrides = {
        "device": args.device, "imgsz": args.imgsz, "ppe_conf": args.ppe_conf,
        "pose_conf": args.pose_conf, "ppe_iou": args.ppe_iou, "pose_iou": args.pose_iou,
        "half": True if args.half else None,
    }

    print(f"Benchmark multi-kamera, sumber {args.source}, {args.seconds} dtk per konfigurasi "
          f"(warmup {args.warmup}s, ppe_every_n={args.ppe_every_n})\n")
    print(f"{'kamera':>6} {'batch(ms)':>10} {'p95(ms)':>8} {'per-cam/dtk':>12} {'total/dtk':>10} "
          f"{'half':>5} {'device':>8}")
    results = []
    for n in args.cams:
        r = run(n, args.seconds, args.source, overrides, ppe_every_n=args.ppe_every_n, warmup=args.warmup)
        results.append(r)
        print(f"{r['kamera']:>6} {r['waktu_batch_ms']:>10} {r['batch_p95_ms']:>8} "
              f"{r['pemeriksaan_per_detik_per_kamera']:>12} {r['pemeriksaan_per_detik_total']:>10} "
              f"{str(r['half']):>5} {r['device']:>8}")

    if args.json_out:
        src = Path(args.source)
        if not src.is_absolute():
            src = (BASE_DIR / src).resolve()
        source_info = {"path": args.source}
        if src.is_file():
            source_info.update({"size": src.stat().st_size, "sha256": _sha256(src)})
        cfg = load_config()
        if overrides:
            cfg["models"].update({k: v for k, v in overrides.items() if v is not None})
        m = cfg["models"]
        evidence = {
            "timestamp": datetime.now(ZoneInfo(cfg.get("timezone", "Asia/Jakarta"))).isoformat(timespec="seconds"),
            "args": vars(args),
            "source": source_info,
            "model": {"version": m.get("version"), "device": m.get("device"), "imgsz": m.get("imgsz"),
                      "half": bool(m.get("half", False)), "ppe_every_n": args.ppe_every_n},
            "results": results,
        }
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False))
        print(f"\nraw benchmark ditulis: {out}")


if __name__ == "__main__":
    main()
