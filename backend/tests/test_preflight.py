"""Test untuk modul pemeriksaan pra-deploy (scripts/preflight.py)."""
from __future__ import annotations

import importlib
from pathlib import Path

preflight = importlib.import_module("scripts.preflight")
_read_env_file = preflight._read_env_file
print_report = preflight.print_report
run_checks = preflight.run_checks


def test_read_env_file(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text('FOO="bar"\nBAZ=qux\n# COMMENT=1\nEMPTY=\n')
    data = _read_env_file(env)
    assert data["FOO"] == "bar"
    assert data["BAZ"] == "qux"
    assert "COMMENT" not in data
    assert data["EMPTY"] == ""


def test_preflight_passes_with_valid_env(tmp_path: Path, monkeypatch):
    from collections import namedtuple
    import shutil
    Usage = namedtuple("usage", ["total", "used", "free"])
    monkeypatch.setattr(shutil, "disk_usage", lambda _: Usage(100 * 1024**3, 50 * 1024**3, 50 * 1024**3))

    env = tmp_path / ".env"
    env.write_text(
        "DOMAIN=k3.example.com\n"
        "CAMERA_SECRET_KEY=12345678901234567890123456789012\n"
        "AGENT_HISTORY_KEY=secret-token-min-16-chars\n"
        "COOKIE_SECURE=1\n"
        "API_DOCS=0\n"
        "DASHBOARD_USER=admin\n"
        "DASHBOARD_PASSWORD=\n"
    )

    results = run_checks(env_path=env)
    report_ok = print_report(results)
    # Blockers harus lolos (kecuali jika bobot model di environment ini hilang, tapi di repo ada)
    blockers = [r for r in results if r.is_blocker and r.status == "GAGAL"]
    assert not blockers, f"Ada blocker yang gagal: {blockers}"
    assert report_ok is True


def test_preflight_blocks_when_key_missing(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "DOMAIN=k3.example.com\n"
        "COOKIE_SECURE=0\n"
        "AGENT_HISTORY_KEY=\n"
    )

    results = run_checks(env_path=env)
    report_ok = print_report(results)
    assert report_ok is False
    failed_items = {r.item for r in results if r.status == "GAGAL"}
    assert "AGENT_HISTORY_KEY" in failed_items
    assert "COOKIE_SECURE" in failed_items
