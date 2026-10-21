"""Arsip hasil review supervisor untuk anotasi ulang dan training model."""
from __future__ import annotations

import csv
import io
import tempfile
import zipfile
from pathlib import Path
from typing import BinaryIO, Sequence

from pipeline.evidence import sha256_file

from . import queries as Q
from .db import Violation


MANIFEST_COLUMNS = (
    "id", "versi_model", "review", "apd_dilaporkan_kurang", "apd_dibenarkan",
    "kamera", "waktu", "file", "sha256", "cocok_hash",
)


def _safe_evidence_path(evidence_dir: Path, relative: str) -> Path | None:
    if not relative:
        return None
    root = evidence_dir.resolve()
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def build_review_zip(rows: Sequence[Violation], evidence_dir: Path) -> tuple[BinaryIO, dict[str, int]]:
    """Bangun ZIP seekable dan manifest; caller wajib menutup file hasil."""
    archive = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b")
    counts = {"confirmed": 0, "false_positive": 0, "hilang": 0, "hash_tidak_cocok": 0}
    manifest = io.StringIO(newline="")
    writer = csv.writer(manifest)
    writer.writerow(MANIFEST_COLUMNS)

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for row in rows:
            source = _safe_evidence_path(evidence_dir, row.evidence_full)
            if source is None:
                counts["hilang"] += 1
                continue
            hash_matches = sha256_file(source) == row.evidence_sha256
            if not hash_matches:
                counts["hash_tidak_cocok"] += 1
            filename = f"{row.id}_{source.name}"
            archive_path = f"{row.review}/{filename}"
            output.write(source, archive_path)
            writer.writerow([
                row.id, row.model_version, row.review, row.missing_items,
                ",".join(Q.corrected_item_codes(row)), row.camera_id,
                row.started_at.isoformat(timespec="seconds"), archive_path,
                row.evidence_sha256, hash_matches,
            ])
            counts[row.review] += 1
        output.writestr("manifest.csv", manifest.getvalue().encode("utf-8"))
        output.writestr(
            "PETUNJUK.txt",
            "Arsip hasil review APD Vision. Validasi kolom cocok_hash sebelum memakai gambar untuk anotasi ulang.\n"
            "Jangan unggah foto pekerja ke layanan publik tanpa dasar hukum dan izin pemilik lokasi.\n",
        )
    archive.seek(0)
    return archive, counts


def iter_file(file: BinaryIO, chunk_size: int = 1024 * 1024):
    while chunk := file.read(chunk_size):
        yield chunk