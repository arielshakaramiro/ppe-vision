import importlib
import hashlib
import sqlite3
import tarfile
import io
from datetime import datetime, timedelta

import pytest

backup = importlib.import_module("scripts.backup")
restore_drill = importlib.import_module("scripts.restore_drill")


def test_backup_uses_sqlite_api_and_prunes_old_files(tmp_path, monkeypatch):
    db = tmp_path / "ppe.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table t (v text)")
        conn.execute("insert into t values ('ok')")

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "foto.jpg").write_bytes(b"img")
    backups = tmp_path / "backups"
    backups.mkdir()
    old = backups / ((datetime.now(backup.ZoneInfo(backup.TIMEZONE)) - timedelta(days=9)).strftime("%Y%m%d_%H%M%S") + "_db.sqlite3")
    old.write_bytes(b"old")

    monkeypatch.setattr(backup, "BASE_DIR", tmp_path)
    monkeypatch.setattr(backup, "DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr(backup, "EVIDENCE_DIR", evidence)
    monkeypatch.setattr(backup, "load_config", lambda: {"backup": {"enabled": True, "dir": "backups", "max_days": 7}})

    counts = backup.run()

    assert counts == {"database": 1, "evidence": 1, "deleted": 1}
    copied = [p for p in backups.glob("*_db.sqlite3") if p != old]
    assert copied
    with sqlite3.connect(copied[0]) as conn:
        assert conn.execute("select v from t").fetchone()[0] == "ok"
    evidence_archives = list(backups.glob("*_evidence.tar.gz"))
    assert evidence_archives
    with tarfile.open(evidence_archives[0], "r:gz") as archive:
        assert "evidence/foto.jpg" in archive.getnames()
    for artifact in (copied[0], evidence_archives[0]):
        checksum = artifact.with_name(f"{artifact.name}.sha256")
        expected = hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert checksum.read_text(encoding="ascii") == f"{expected}  {artifact.name}\n"
    assert not old.exists()


def test_backup_still_archives_evidence_when_today_database_backup_exists(tmp_path, monkeypatch):
    db = tmp_path / "ppe.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table t (v text)")
        conn.execute("insert into t values ('ok')")

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "foto.jpg").write_bytes(b"img")
    backups = tmp_path / "backups"
    backups.mkdir()
    today = datetime.now(backup.ZoneInfo(backup.TIMEZONE)).strftime("%Y%m%d")
    existing = backups / f"{today}_010000_db.sqlite3"
    existing.write_bytes(b"sudah ada")
    old = backups / ((datetime.now(backup.ZoneInfo(backup.TIMEZONE)) - timedelta(days=9)).strftime("%Y%m%d_%H%M%S") + "_evidence.tar.gz")
    old.write_bytes(b"lama")

    monkeypatch.setattr(backup, "BASE_DIR", tmp_path)
    monkeypatch.setattr(backup, "DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr(backup, "EVIDENCE_DIR", evidence)
    monkeypatch.setattr(backup, "load_config", lambda: {"backup": {"enabled": True, "dir": "backups", "max_days": 7}})

    counts = backup.run()

    assert counts == {"database": 0, "evidence": 1, "deleted": 1}
    assert list(backups.glob("*_db.sqlite3")) == [existing]
    assert list(backups.glob("*_evidence.tar.gz"))
    assert not old.exists()


def test_backup_still_copies_database_when_today_evidence_backup_exists(tmp_path, monkeypatch):
    db = tmp_path / "ppe.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table t (v text)")
        conn.execute("insert into t values ('ok')")

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "foto.jpg").write_bytes(b"img")
    backups = tmp_path / "backups"
    backups.mkdir()
    today = datetime.now(backup.ZoneInfo(backup.TIMEZONE)).strftime("%Y%m%d")
    existing = backups / f"{today}_010000_evidence.tar.gz"
    existing.write_bytes(b"sudah ada")

    monkeypatch.setattr(backup, "BASE_DIR", tmp_path)
    monkeypatch.setattr(backup, "DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr(backup, "EVIDENCE_DIR", evidence)
    monkeypatch.setattr(backup, "load_config", lambda: {"backup": {"enabled": True, "dir": "backups", "max_days": 7}})

    counts = backup.run()

    assert counts == {"database": 1, "evidence": 0, "deleted": 0}
    assert list(backups.glob("*_db.sqlite3"))
    assert list(backups.glob("*_evidence.tar.gz")) == [existing]


def test_backup_retries_evidence_after_partial_archive_failure(tmp_path, monkeypatch):
    db = tmp_path / "ppe.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table t (v text)")

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "foto.jpg").write_bytes(b"img")
    backups = tmp_path / "backups"
    backups.mkdir()
    old = backups / ((datetime.now(backup.ZoneInfo(backup.TIMEZONE)) - timedelta(days=9)).strftime("%Y%m%d_%H%M%S") + "_evidence.tar.gz")
    old.write_bytes(b"lama")

    monkeypatch.setattr(backup, "BASE_DIR", tmp_path)
    monkeypatch.setattr(backup, "DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr(backup, "EVIDENCE_DIR", evidence)
    monkeypatch.setattr(backup, "load_config", lambda: {"backup": {"enabled": True, "dir": "backups", "max_days": 7}})

    real_open = backup.tarfile.open

    def fail_after_creating_partial(path, mode):
        path.write_bytes(b"parsial")
        raise OSError("simulasi arsip terputus")

    monkeypatch.setattr(backup.tarfile, "open", fail_after_creating_partial)
    with pytest.raises(backup.BackupError, match="evidence: simulasi arsip terputus"):
        backup.run()

    assert list(backups.glob("*_db.sqlite3"))
    assert not list(backups.glob("*_evidence.tar.gz"))
    assert not list(backups.glob("*.tmp"))
    assert not old.exists()

    monkeypatch.setattr(backup.tarfile, "open", real_open)
    counts = backup.run()

    assert counts == {"database": 0, "evidence": 1, "deleted": 0}
    assert list(backups.glob("*_evidence.tar.gz"))


def test_backup_database_failure_does_not_block_evidence_or_retention(tmp_path, monkeypatch):
    db = tmp_path / "ppe.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table t (v text)")

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "foto.jpg").write_bytes(b"img")
    backups = tmp_path / "backups"
    backups.mkdir()
    old = backups / ((datetime.now(backup.ZoneInfo(backup.TIMEZONE)) - timedelta(days=9)).strftime("%Y%m%d_%H%M%S") + "_db.sqlite3")
    old.write_bytes(b"lama")

    monkeypatch.setattr(backup, "BASE_DIR", tmp_path)
    monkeypatch.setattr(backup, "DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr(backup, "EVIDENCE_DIR", evidence)
    monkeypatch.setattr(backup, "load_config", lambda: {"backup": {"enabled": True, "dir": "backups", "max_days": 7}})
    monkeypatch.setattr(backup, "_backup_database", lambda *_: (_ for _ in ()).throw(OSError("simulasi database gagal")))

    with pytest.raises(backup.BackupError, match="database: simulasi database gagal") as exc_info:
        backup.run()

    assert exc_info.value.counts == {"database": 0, "evidence": 1, "deleted": 1}
    assert list(backups.glob("*_evidence.tar.gz"))
    assert not old.exists()


def test_backup_rejects_database_that_fails_integrity_check(tmp_path, monkeypatch):
    db = tmp_path / "ppe.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table t (v text)")

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "foto.jpg").write_bytes(b"img")
    backups = tmp_path / "backups"

    monkeypatch.setattr(backup, "BASE_DIR", tmp_path)
    monkeypatch.setattr(backup, "DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr(backup, "EVIDENCE_DIR", evidence)
    monkeypatch.setattr(backup, "load_config", lambda: {"backup": {"enabled": True, "dir": "backups", "max_days": 7}})
    monkeypatch.setattr(backup, "_verify_sqlite", lambda *_: (_ for _ in ()).throw(sqlite3.DatabaseError("integritas gagal")))

    with pytest.raises(backup.BackupError, match="database: integritas gagal"):
        backup.run()

    assert not list(backups.glob("*_db.sqlite3"))
    assert list(backups.glob("*_evidence.tar.gz"))
    assert not list(backups.glob("*.tmp"))


def test_backup_rejects_evidence_archive_that_cannot_be_verified(tmp_path, monkeypatch):
    db = tmp_path / "ppe.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table t (v text)")

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "foto.jpg").write_bytes(b"img")
    backups = tmp_path / "backups"

    monkeypatch.setattr(backup, "BASE_DIR", tmp_path)
    monkeypatch.setattr(backup, "DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr(backup, "EVIDENCE_DIR", evidence)
    monkeypatch.setattr(backup, "load_config", lambda: {"backup": {"enabled": True, "dir": "backups", "max_days": 7}})
    monkeypatch.setattr(backup, "_verify_tar", lambda *_: (_ for _ in ()).throw(tarfile.ReadError("arsip rusak")))

    with pytest.raises(backup.BackupError, match="evidence: arsip rusak"):
        backup.run()

    assert list(backups.glob("*_db.sqlite3"))
    assert not list(backups.glob("*_evidence.tar.gz"))
    assert not list(backups.glob("*.tmp"))


def test_backup_refuses_parallel_execution(tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    backups.mkdir()

    monkeypatch.setattr(backup, "BASE_DIR", tmp_path)
    monkeypatch.setattr(backup, "DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(backup, "EVIDENCE_DIR", tmp_path / "evidence")
    monkeypatch.setattr(backup, "load_config", lambda: {"backup": {"enabled": True, "dir": "backups", "max_days": 7}})

    with backup._backup_lock(backups):
        with pytest.raises(backup.BackupInProgressError, match="sedang berjalan"):
            backup.run()

    assert not list(backups.glob("*_db.sqlite3"))
    assert not list(backups.glob("*_evidence.tar.gz"))


def test_restore_drill_verifies_and_restores_latest_backup_to_temporary_directory(tmp_path):
    database = tmp_path / "20261016_120000_db.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("create table audit (id integer primary key, aksi text)")
        connection.execute("insert into audit (aksi) values ('uji restore')")
    archive = tmp_path / "20261016_120000_evidence.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        content = b"bukti"
        member = tarfile.TarInfo("evidence/2026/bukti.jpg")
        member.size = len(content)
        tar.addfile(member, io.BytesIO(content))
    for artifact in (database, archive):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_name(f"{artifact.name}.sha256").write_text(
            f"{digest}  {artifact.name}\n", encoding="ascii"
        )

    result = restore_drill.run(tmp_path)

    assert result["database"] == database.name
    assert result["tabel"] == 1
    assert result["evidence"] == archive.name
    assert result["file_evidence"] == 1


def test_restore_drill_rejects_bad_checksum_and_archive_traversal(tmp_path):
    database = tmp_path / "20261016_120000_db.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("create table audit (id integer primary key)")
    sidecar = database.with_name(f"{database.name}.sha256")
    sidecar.write_text(f"{'0' * 64}  {database.name}\n", encoding="ascii")
    with pytest.raises(restore_drill.RestoreDrillError, match="Checksum tidak cocok"):
        restore_drill.run(tmp_path)

    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    sidecar.write_text(f"{digest}  {database.name}\n", encoding="ascii")
    archive = tmp_path / "20261016_120000_evidence.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        content = b"dilarang"
        member = tarfile.TarInfo("../keluar.txt")
        member.size = len(content)
        tar.addfile(member, io.BytesIO(content))
    archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(f"{archive.name}.sha256").write_text(
        f"{archive_digest}  {archive.name}\n", encoding="ascii"
    )
    with pytest.raises(restore_drill.RestoreDrillError, match="path di luar"):
        restore_drill.run(tmp_path)
