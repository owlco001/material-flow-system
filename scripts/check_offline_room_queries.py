"""Validate the SQL that Room actually generated, using an isolated SQLite database.

Run after :app:assembleDebug. No Android runtime, third-party packages or production data needed.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    generated = root / "logistics-android/app/build/generated/ksp/debug/java/com/company/logistics/data"
    implementation = (generated / "RoomOfflineOperationDao_Impl.java").read_text()
    database = (generated / "LogisticsDatabase_Impl.java").read_text()
    assert "new RoomOfflineOperationDao_Impl" in database, "Database must use the production DAO"
    for method in ("pendingDue", "hasPending", "scheduleRetry"):
        assert not re.search(rf"DefaultImpls\.{method}\(", implementation), (
            f"{method} delegates to a default body instead of generated SQL"
        )
    literals = [json.loads(match.group()) for match in re.finditer(r'"(?:\\.|[^"\\])*"', implementation)]

    def query(prefix: str, marker: str) -> str:
        matches = [value for value in literals if value.startswith(prefix) and marker in value]
        assert len(set(matches)) == 1, f"Expected one generated query: {prefix} / {marker}"
        return matches[0]

    due = query("SELECT * FROM offline_operations", "nextRetryAt")
    pending = query("SELECT EXISTS", "offline_operations")
    retry = query("UPDATE offline_operations", "retryCount")
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE offline_operations (id TEXT PRIMARY KEY, status TEXT, "
                           "createdAt INTEGER, nextRetryAt INTEGER, retryCount INTEGER, errorMessage TEXT)")
        connection.executemany("INSERT INTO offline_operations VALUES(?,?,?,?,?,?)", [
            (str(i), "PENDING", i, 2000 if i <= 20 else None, 0, None) for i in range(1, 48)
        ])
        connection.execute("INSERT INTO offline_operations VALUES('terminal','FAILED',0,NULL,5,NULL)")
        first = connection.execute(due, (1000, 1000)).fetchall()
        assert [row["id"] for row in first] == [str(i) for i in range(21, 41)]
        connection.executemany("UPDATE offline_operations SET status='SYNCED' WHERE id=?", [(r["id"],) for r in first])
        second = connection.execute(due, (1000, 1000)).fetchall()
        assert [row["id"] for row in second] == [str(i) for i in range(41, 48)]
        connection.execute(retry, ("PENDING", "network", 1, 3000, "21"))
        retried = connection.execute("SELECT * FROM offline_operations WHERE id='21'").fetchone()
        assert (retried["status"], retried["retryCount"], retried["nextRetryAt"], retried["errorMessage"]) == (
            "PENDING", 1, 3000, "network"
        )
        assert "21" not in {row["id"] for row in connection.execute(due, (1000, 1000))}
        assert connection.execute(pending).fetchone()[0] == 1
        connection.execute("UPDATE offline_operations SET status='FAILED'")
        assert connection.execute(pending).fetchone()[0] == 0, "Terminal rows must not trigger worker retries"
        connection.execute("INSERT INTO offline_operations VALUES('new','PENDING',2001,NULL,0,NULL)")
        assert not connection.execute(due, (3000, 1000)).fetchall(), "New enqueues belong to the next snapshot"
    print("Room generated SQL PASS: native DAO, due-before-limit, later batches, retry persistence, pending-only scheduling, snapshot cutoff")


if __name__ == "__main__":
    main()
