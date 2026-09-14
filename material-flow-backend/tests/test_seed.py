"""Tests for the opt-in, idempotent test fixture entry point."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _run(data_dir: Path, *args: str, enabled: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MATERIAL_FLOW_DATA"] = str(data_dir)
    env["MATERIAL_FLOW_UPLOADS"] = str(data_dir.parent / "uploads")
    env["INITIAL_ADMIN_PASSWORD"] = "SeedTestAdmin@2026"
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


def test_seed_twice_is_idempotent_and_validates_database(tmp_path):
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
            "users": 5, "assembly_tasks": 4, "labor_records": 4, "progress_events": 5,
        }.items():
            assert connection.execute(f"SELECT COUNT(*) FROM {table} WHERE id LIKE ?", (prefix,)).fetchone()[0] == expected
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM assembly_tasks WHERE id LIKE ? AND status NOT IN "
            "('WAITING_MATERIAL','MATERIAL_ACCEPTED','IN_PROGRESS','PAUSED_FOR_TEMPORARY_TRANSFER','COMPLETED')",
            (prefix,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM users WHERE id LIKE ? AND password_hash LIKE '%SeedTestAdmin%'", (prefix,)
        ).fetchone()[0] == 0
    finally:
        connection.close()
