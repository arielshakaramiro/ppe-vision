# pyright: reportMissingImports=false
"""Fine-tune detektor APD. Jalankan di Colab Pro (GPU).

    pip install ultralytics
    python train.py --data datasets/ppe4/data.yaml --weights yolo11s.pt
    python train.py --data datasets/ppe4/data.yaml --export-format onnx coreml --copy-best-to backend/weights/best.pt

Untuk mulai dari bobot pretrained SH17, unduh dari Releases repo
github.com/ahmadmughees/SH17dataset lalu isi --weights dengan path file itu.
"""
import argparse
import shutil
from pathlib import Path

from ultralytics import YOLO


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--weights", default="yolo11s.pt")
    ap.add_argument("--imgsz", type=int, default=960)   # sarung tangan & sepatu kecil, 640 sering luput
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device")
    ap.add_argument("--project", default="runs")
    ap.add_argument("--name", default="ppe4")
    ap.add_argument("--patience", type=int, default=20)
    ap.add_argument("--export-format", nargs="*", default=[],
                    help="format ekspor opsional, mis. onnx coreml torchscript")
    ap.add_argument("--export-half", action="store_true")
    ap.add_argument("--copy-best-to", default="", help="salin best.pt ke path ini setelah training")
    a = ap.parse_args()

    model = YOLO(a.weights)
    model.train(data=a.data, imgsz=a.imgsz, epochs=a.epochs, batch=a.batch, patience=a.patience,
                device=a.device, project=a.project, name=a.name, cos_lr=True, close_mosaic=10)
    metrics = model.val(data=a.data, split="test", imgsz=a.imgsz, device=a.device)
    print("mAP50-95 per kelas:", dict(zip(model.names.values(), metrics.box.maps.round(3))))

    for fmt in a.export_format:
        print(f"Ekspor model ke {fmt}...")
        model.export(format=fmt, imgsz=a.imgsz, half=a.export_half)

    best = Path(a.project) / a.name / "weights" / "best.pt"
    if a.copy_best_to and best.exists():
        dest = Path(a.copy_best_to)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, dest)
        print(f"best.pt disalin ke {dest}")
    print(f"Salin {best} ke backend/weights/best.pt lalu naikkan models.version di backend/config.yaml")


if __name__ == "__main__":
    main()
