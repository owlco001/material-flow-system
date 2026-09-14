"""Opt-in, repeatable test-fixture data for local/staging verification."""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
from typing import Any

from app.main import ASSEMBLY_STATUSES, db, hash_password, init_db, now

SEED_PREFIX = "MF_TEST_SEED_V1"
TEST_PASSWORD = "MF_TEST_ONLY_NOT_PRODUCTION_2026"
TASK_STATUSES = ASSEMBLY_STATUSES


def _enabled() -> bool:
    return os.environ.get("ENABLE_TEST_DATA_SEED", "").strip().lower() == "true"


def _insert_fixture(c: sqlite3.Connection) -> None:
    rnd = random.Random(20260914)
    ts = now()
    password_hash = hash_password(TEST_PASSWORD)
    supervisors = [(f"{SEED_PREFIX}_SUP_{i}", f"{SEED_PREFIX.lower()}_supervisor_{i}", "车间测试主管") for i in range(1, 3)]
    assemblers = [(f"{SEED_PREFIX}_ASM_{i}", f"{SEED_PREFIX.lower()}_assembler_{i}", "装配测试工") for i in range(1, 4)]
    for uid, username, display_name in supervisors + assemblers:
        role = "WORKSHOP_SUPERVISOR" if "SUP" in uid else "ASSEMBLER"
        c.execute(
            """INSERT OR IGNORE INTO users
               (id, username, display_name, role, password_hash, must_change_password, active, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (uid, username, display_name, role, password_hash, 0, 1, ts),
        )

    assembler_ids = [row[0] for row in assemblers]
    statuses = ["WAITING_MATERIAL", "MATERIAL_ACCEPTED", "IN_PROGRESS", "COMPLETED"]
    for i in range(1, 5):
        task_id = f"{SEED_PREFIX}_TASK_{i}"
        status = statuses[i - 1]
        stage = {"WAITING_MATERIAL": 0, "MATERIAL_ACCEPTED": 0, "IN_PROGRESS": 2, "COMPLETED": 3}[status]
        c.execute(
            """INSERT OR IGNORE INTO assembly_tasks
               (id, order_no, device_id, device_no, assigned_assembler_id, status,
                progress_stage, task_version, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (task_id, f"{SEED_PREFIX}_ORDER_{i}", f"{SEED_PREFIX}_MACHINE_{i}",
             f"{SEED_PREFIX}-M{i:02d}", assembler_ids[(i - 1) % len(assembler_ids)],
             status, stage, stage + 1, ts, ts),
        )
        duration = rnd.randint(35, 120)
        labor_status = "COMPLETED" if status == "COMPLETED" else "ACTIVE"
        ended = ts if labor_status == "COMPLETED" else None
        duration_value = duration if labor_status == "COMPLETED" else None
        labor_id = f"{SEED_PREFIX}_LABOR_{i}"
        operation_id = f"{SEED_PREFIX}_OP_{i}"
        c.execute(
            """INSERT OR IGNORE INTO labor_records
               (id, task_id, worker_user_id, type, status, started_at, ended_at,
                duration_minutes, remark, client_operation_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (labor_id, task_id, assembler_ids[(i - 1) % len(assembler_ids)], "ASSEMBLY",
             labor_status, ts, ended, duration_value, f"{SEED_PREFIX} fixture", operation_id, ts),
        )
        if stage >= 1:
            for to_stage in range(1, stage + 1):
                c.execute(
                    """INSERT OR IGNORE INTO progress_events
                       (id, task_id, worker_user_id, from_stage, to_stage, task_version,
                        server_time, client_operation_id)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (f"{SEED_PREFIX}_PROGRESS_{i}_{to_stage}", task_id,
                     assembler_ids[(i - 1) % len(assembler_ids)], to_stage - 1, to_stage,
                     to_stage + 1, ts, f"{SEED_PREFIX}_PROGRESS_OP_{i}_{to_stage}"),
                )


def validate_seed(c: sqlite3.Connection) -> dict[str, Any]:
    """Validate fixture counts and relational/enumeration invariants."""
    counts = {table: c.execute(f"SELECT COUNT(*) FROM {table} WHERE id LIKE ?", (f"{SEED_PREFIX}%",)).fetchone()[0]
              for table in ("users", "assembly_tasks", "labor_records", "progress_events")}
    expected = {"users": 5, "assembly_tasks": 4, "labor_records": 4, "progress_events": 5}
    if counts != expected:
        raise RuntimeError(f"fixture counts mismatch: {counts} != {expected}")
    violations = c.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"foreign key violations: {violations}")
    invalid = c.execute(
        "SELECT COUNT(*) FROM assembly_tasks WHERE id LIKE ? AND status NOT IN "
        "('WAITING_MATERIAL','MATERIAL_ACCEPTED','IN_PROGRESS','PAUSED_FOR_TEMPORARY_TRANSFER','COMPLETED')",
        (f"{SEED_PREFIX}%",),
    ).fetchone()[0]
    if invalid:
        raise RuntimeError(f"invalid task statuses: {invalid}")
    return {"prefix": SEED_PREFIX, "counts": counts, "foreign_keys": "ok", "statuses": "ok"}


def seed_test_fixtures() -> dict[str, Any]:
    """Migrate first, then insert and validate the fixed test fixture set."""
    init_db()
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        _insert_fixture(c)
        result = validate_seed(c)
        c.commit()
        return result
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in Material Flow test fixture seed")
    parser.add_argument("--seed", action="store_true", help="explicitly enable test fixture insertion")
    args = parser.parse_args(argv)
    if not (args.seed or _enabled()):
        print(json.dumps({"enabled": False, "message": "test data seed disabled"}))
        return 0
    print(json.dumps(seed_test_fixtures(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
