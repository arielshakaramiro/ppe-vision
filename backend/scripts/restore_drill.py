"""Drill restore backup ke direktori sementara tanpa menyentuh data aktif."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from app.config import BASE_DIR, load_config


class RestoreDrillError(RuntimeError):
    """Backup tidak dapat diverifikasi atau dipulihkan dengan aman."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_checksum(path: Path) -> None:
    sidecar = path.with_name(f"{path.name}.sha256")
    if not sidecar.is_file():
        raise RestoreDrillError(f"Sidecar checksum tidak ditemukan untuk {path.name}.")
    parts = sidecar.read_text(encoding="ascii").strip().split(maxsplit=1)
    if len(parts) != 2 or parts[1].lstrip("*") != path.name:
        raise RestoreDrillError(f"Format sidecar checksum tidak valid untuk {path.name}.")
    if not hmac.compare_digest(parts[0].lower(), _sha256(path)):
        raise RestoreDrillError(f"Checksum tidak cocok untuk {path.name}.")


def _verify_database(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        tables = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
    if integrity != ["ok"]:
        raise RestoreDrillError("Pemeriksaan integritas database hasil restore gagal.")
    if tables < 1:
        raise RestoreDrillError("Database hasil restore tidak memiliki tabel aplikasi.")
    return int(tables)


def _extract_evidence(archive_path: Path, target: Path) -> int:
    target = target.resolve()
    files = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            destination = (target / member.name).resolve()
            if destination != target and target not in destination.parents:
                raise RestoreDrillError("Arsip evidence memuat path di luar direktori restore.")
            if member.issym() or member.islnk():
                raise RestoreDrillError("Arsip evidence memuat tautan yang tidak diizinkan.")
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            source = archive.extractfile(member)
            if source is None:
                raise RestoreDrillError(f"Isi arsip tidak dapat dibaca: {member.name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            files += 1
    return files


def run(backup_dir: Path | None = None) -> dict:
    configured = load_config().get("backup", {}).get("dir", "data/backups")
    directory = (backup_dir or (BASE_DIR / configured)).expanduser().resolve()
    if not directory.is_dir():
        raise RestoreDrillError("Direktori backup tidak ditemukan.")

    databases = sorted(directory.glob("*_db.sqlite3"))
    if not databases:
        raise RestoreDrillError("Backup database tidak ditemukan.")
    database = databases[-1]
    archives = sorted(directory.glob("*_evidence.tar.gz"))
    evidence = archives[-1] if archives else None

    _verify_checksum(database)
    if evidence is not None:
        _verify_checksum(evidence)

    with tempfile.TemporaryDirectory(prefix="apd-restore-drill-") as temporary:
        root = Path(temporary).resolve()
        restored_database = root / "ppe.db"
        shutil.copy2(database, restored_database)
        tables = _verify_database(restored_database)
        evidence_files = _extract_evidence(evidence, root / "evidence") if evidence else 0

    return {
        "database": database.name,
        "tabel": tables,
        "evidence": evidence.name if evidence else None,
        "file_evidence": evidence_files,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Uji restore backup terbaru ke direktori sementara.")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    result = run(args.backup_dir)
    evidence = result["evidence"] or "tidak ada"
    print(
        f"Drill restore berhasil: database {result['database']} ({result['tabel']} tabel); "
        f"evidence {evidence} ({result['file_evidence']} file)."
    )


if __name__ == "__main__":
    main()