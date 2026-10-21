"""Backup otomatis database dan evidence harian.

Gunakan SQLite backup API agar DB aman dikopi saat aplikasi berjalan.
Jalankan manual:
    python -m scripts.backup
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import logging
import sqlite3
import tarfile
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.config import BASE_DIR, DATABASE_URL, EVIDENCE_DIR, TIMEZONE, load_config

log = logging.getLogger("ppe.backup")


class BackupInProgressError(RuntimeError):
    """Backup lain masih memegang lock pada direktori tujuan."""


class BackupError(RuntimeError):
    """Satu atau beberapa tahap backup gagal setelah semua tahap dicoba."""

    def __init__(self, errors: list[tuple[str, Exception]], counts: dict[str, int]):
        self.errors = errors
        self.counts = counts
        details = "; ".join(f"{stage}: {error}" for stage, error in errors)
        super().__init__(f"Backup gagal pada {len(errors)} tahap: {details}")


def _sqlite_path() -> Path | None:
    if not DATABASE_URL.startswith("sqlite:///"):
        return None
    raw = DATABASE_URL.removeprefix("sqlite:///")
    if raw == ":memory:":
        return None
    path = Path(raw)
    return path if path.is_absolute() else BASE_DIR / path


@contextmanager
def _backup_lock(backup_dir: Path):
    lock_path = backup_dir / ".backup.lock"
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BackupInProgressError(
                "Proses backup lain sedang berjalan; eksekusi ini dilewati."
            ) from error
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _temporary_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.{uuid4().hex}.tmp")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_with_checksum(temp_path: Path, final_path: Path) -> None:
    checksum_path = final_path.with_name(f"{final_path.name}.sha256")
    temp_checksum_path = _temporary_path(checksum_path)
    try:
        digest = _sha256(temp_path)
        temp_checksum_path.write_text(f"{digest}  {final_path.name}\n", encoding="ascii")
        temp_checksum_path.replace(checksum_path)
        temp_path.replace(final_path)
    except Exception:
        checksum_path.unlink(missing_ok=True)
        raise
    finally:
        temp_checksum_path.unlink(missing_ok=True)
        temp_path.unlink(missing_ok=True)


def _verify_sqlite(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        results = [row[0] for row in conn.execute("PRAGMA integrity_check")]
    if results != ["ok"]:
        raise sqlite3.DatabaseError("Pemeriksaan integritas SQLite gagal: " + "; ".join(results))


def _verify_tar(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                raise tarfile.ReadError(f"Isi arsip tidak dapat dibaca: {member.name}")
            with extracted:
                for _ in iter(lambda: extracted.read(1024 * 1024), b""):
                    pass


def _backup_database(src_db: Path, db_path: Path) -> None:
    temp_db_path = _temporary_path(db_path)
    try:
        with sqlite3.connect(src_db) as src, sqlite3.connect(temp_db_path) as dst:
            src.backup(dst)
        _verify_sqlite(temp_db_path)
        _publish_with_checksum(temp_db_path, db_path)
    finally:
        temp_db_path.unlink(missing_ok=True)


def _backup_evidence(evidence_path: Path) -> None:
    temp_evidence_path = _temporary_path(evidence_path)
    try:
        with tarfile.open(temp_evidence_path, "w:gz") as archive:
            archive.add(EVIDENCE_DIR, arcname="evidence")
        _verify_tar(temp_evidence_path)
        _publish_with_checksum(temp_evidence_path, evidence_path)
    finally:
        temp_evidence_path.unlink(missing_ok=True)


def _prune(backup_dir: Path, cutoff: datetime, dry: bool) -> int:
    deleted = 0
    if not backup_dir.exists():
        return deleted
    for path in backup_dir.iterdir():
        if not path.is_file():
            continue
        try:
            stamp = datetime.strptime(path.name[:15], "%Y%m%d_%H%M%S").replace(tzinfo=cutoff.tzinfo)
        except ValueError:
            continue
        if stamp < cutoff:
            if not dry:
                path.unlink()
            deleted += 1
    return deleted


def run(dry: bool = False) -> dict:
    cfg = load_config().get("backup", {})
    counts = {"database": 0, "evidence": 0, "deleted": 0}
    if not cfg.get("enabled", True):
        log.info("Backup dimatikan di config.")
        return counts

    backup_dir = BASE_DIR / cfg.get("dir", "data/backups")
    if not dry:
        backup_dir.mkdir(parents=True, exist_ok=True)

    lock = nullcontext() if dry else _backup_lock(backup_dir)
    with lock:
        now = datetime.now(ZoneInfo(TIMEZONE))
        day_prefix = now.strftime("%Y%m%d")
        database_exists = backup_dir.exists() and any(backup_dir.glob(f"{day_prefix}_*_db.sqlite3"))
        evidence_exists = backup_dir.exists() and any(backup_dir.glob(f"{day_prefix}_*_evidence.tar.gz"))
        prefix = now.strftime("%Y%m%d_%H%M%S")
        db_path = backup_dir / f"{prefix}_db.sqlite3"
        evidence_path = backup_dir / f"{prefix}_evidence.tar.gz"
        errors: list[tuple[str, Exception]] = []

        try:
            try:
                src_db = _sqlite_path()
                if database_exists:
                    log.info("Backup database hari ini sudah ada, backup database dilewati.")
                elif src_db and src_db.exists():
                    if dry:
                        log.info("[SIMULASI] Backup database %s ke %s", src_db.name, db_path.name)
                    else:
                        _backup_database(src_db, db_path)
                        log.info("Database dibackup ke %s", db_path.name)
                    counts["database"] = 1
                elif DATABASE_URL.startswith("sqlite"):
                    log.info("Database SQLite belum ada, backup database dilewati.")
                else:
                    log.info("Bukan SQLite, backup database dilewati.")
            except Exception as error:
                errors.append(("database", error))
                log.error("Tahap backup database gagal: %s", error)

            try:
                if evidence_exists:
                    log.info("Backup evidence hari ini sudah ada, backup evidence dilewati.")
                elif EVIDENCE_DIR.exists() and any(EVIDENCE_DIR.iterdir()):
                    if dry:
                        log.info("[SIMULASI] Backup evidence ke %s", evidence_path.name)
                    else:
                        _backup_evidence(evidence_path)
                        log.info("Evidence dibackup ke %s", evidence_path.name)
                    counts["evidence"] = 1
            except Exception as error:
                errors.append(("evidence", error))
                log.error("Tahap backup evidence gagal: %s", error)
        finally:
            try:
                max_days = int(cfg.get("max_days", 7))
                counts["deleted"] = _prune(backup_dir, now - timedelta(days=max_days), dry)
                if counts["deleted"]:
                    prefix_log = "[SIMULASI] Akan menghapus" if dry else "Dihapus"
                    log.info("%s %d file backup kedaluwarsa.", prefix_log, counts["deleted"])
            except Exception as error:
                errors.append(("retensi", error))
                log.error("Tahap retensi backup gagal: %s", error)

        if errors:
            raise BackupError(errors, counts)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    counts = run(a.dry_run)
    print(("[SIMULASI] " if a.dry_run else "")
          + f"Database: {counts['database']}; evidence: {counts['evidence']}; "
          + f"backup kedaluwarsa: {counts['deleted']}.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
