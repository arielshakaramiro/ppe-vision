"""Buat paket ppe-colab-tools.zip untuk training Colab tanpa kloning seluruh repo.

Memuat:
- training/fetch_datasets.py
- training/merge_datasets.py
- training/evaluate_model.py
"""
import zipfile
from pathlib import Path

TARGET_FILES = [
    "training/fetch_datasets.py",
    "training/merge_datasets.py",
    "training/evaluate_model.py",
]

def make_zip(out_path: str = "ppe-colab-tools.zip") -> Path:
    root = Path(__file__).resolve().parent.parent
    zip_out = root / out_path

    with zipfile.ZipFile(zip_out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel_path in TARGET_FILES:
            abs_path = root / rel_path
            if not abs_path.is_file():
                raise FileNotFoundError(f"File tidak ditemukan: {abs_path}")
            zf.write(abs_path, arcname=rel_path)

    print(f"Berhasil membuat {zip_out.name} ({zip_out.stat().st_size} bytes):")
    with zipfile.ZipFile(zip_out, "r") as zf:
        for item in zf.infolist():
            print(f"  {item.filename} ({item.file_size} bytes)")
    return zip_out

if __name__ == "__main__":
    make_zip()
