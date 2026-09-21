"""Tests for the opt-in, idempotent test fixture entry point."""
from __future__ import annotations

import os
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _run(data_dir: Path, *args: str, enabled: str | None = None, password: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MATERIAL_FLOW_DATA"] = str(data_dir)
    env["MATERIAL_FLOW_UPLOADS"] = str(data_dir.parent / "uploads")
    env["INITIAL_ADMIN_PASSWORD"] = secrets.token_urlsafe(24)
    if password is not None:
        env["TEST_DATA_SEED_PASSWORD"] = password
    if enabled is not None:
        env["ENABLE_TEST_DATA_SEED"] = enabled
    else:
        env.pop("ENABLE_TEST_DATA_SEED", None)
    return subprocess.run(
        [sys.executable, "-m", "app.seed", *args], cwd=BACKEND_ROOT,
        env=env, capture_output=True, text=True, check=False,
    )


def test_seed_is_disabled_by_default(tmp_path):
    data_dir = tmp_path / "data"
    result = _run(data_dir)
    assert result.returncode == 0
    assert '"enabled": false' in result.stdout
    assert not (data_dir / "material_flow.db").exists()


def test_seed_without_password_skips_users_but_keeps_tasks(tmp_path):
    data_dir = tmp_path / "data"
    result = _run(data_dir, "--seed")
    assert result.returncode == 0, result.stderr
    assert '"users": 0' in result.stdout
    assert '"assembly_tasks": 6' in result.stdout
    assert '"temporary_transfers": 0' in result.stdout
    connection = sqlite3.connect(data_dir / "material_flow.db")
    try:
        prefix = "MF_TEST_SEED_V1%"
        assert connection.execute("SELECT COUNT(*) FROM production_orders WHERE id LIKE ?", (prefix,)).fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM order_devices WHERE id LIKE ?", (prefix,)).fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM materials WHERE id LIKE ?", (prefix,)).fetchone()[0] == 8
        assert connection.execute("SELECT COUNT(*) FROM locations WHERE id LIKE ?", (prefix,)).fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM order_material_requirements WHERE id LIKE ?", (prefix,)).fetchone()[0] == 24
        assert connection.execute("SELECT COUNT(*) FROM material_handovers WHERE id LIKE ?", (prefix,)).fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM exceptions WHERE id LIKE ?", (prefix,)).fetchone()[0] == 3
    finally:
        connection.close()


def test_seed_without_password_reuses_existing_seed_users(tmp_path):
    data_dir = tmp_path / "data"
    password = secrets.token_urlsafe(24)
    first = _run(data_dir, "--seed", password=password)
    second = _run(data_dir, "--seed")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert '"users": 5' in second.stdout
    assert '"labor_records": 6' in second.stdout
    assert '"progress_events": 5' in second.stdout
    assert '"temporary_transfers": 2' in second.stdout
    connection = sqlite3.connect(data_dir / "material_flow.db")
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM assembly_tasks WHERE id LIKE ? AND assigned_assembler_id IS NOT NULL",
            ("MF_TEST_SEED_V1%",),
        ).fetchone()[0] == 6
    finally:
        connection.close()


def test_seed_twice_is_idempotent_and_validates_database(tmp_path, monkeypatch):
    # Generate a process-local value; no credential is stored in the test source.
    monkeypatch.setenv("TEST_DATA_SEED_PASSWORD", secrets.token_urlsafe(24))
    data_dir = tmp_path / "data"
    first = _run(data_dir, "--seed")
    second = _run(data_dir, enabled="true")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert '"foreign_keys": "ok"' in second.stdout
    connection = sqlite3.connect(data_dir / "material_flow.db")
    try:
        prefix = "MF_TEST_SEED_V1%"
        for table, expected in {
            "users": 5, "assembly_tasks": 6, "labor_records": 6,
            "progress_events": 5, "temporary_transfers": 2,
        }.items():
            assert connection.execute(f"SELECT COUNT(*) FROM {table} WHERE id LIKE ?", (prefix,)).fetchone()[0] == expected
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM assembly_tasks WHERE id LIKE ? AND status NOT IN "
            "('WAITING_MATERIAL','MATERIAL_ACCEPTED','IN_PROGRESS','PAUSED_FOR_TEMPORARY_TRANSFER','COMPLETED')",
            (prefix,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM temporary_transfers WHERE id LIKE ? AND status NOT IN ('ACTIVE','COMPLETED')",
            (prefix,),
        ).fetchone()[0] == 0
    finally:
        connection.close()
