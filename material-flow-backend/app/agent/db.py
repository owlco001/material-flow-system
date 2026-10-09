"""Agent 持久化表：会话、消息、导入任务、用量。"""

from __future__ import annotations

import sqlite3


def ensure_agent_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_sessions(
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_messages(
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tool_calls_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_import_jobs(
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            target TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_sha256 TEXT NOT NULL,
            column_map_json TEXT NOT NULL,
            rows_json TEXT NOT NULL,
            errors_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            committed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS agent_usage(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            user_id TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL,
            completion_tokens INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_import_error_logs(
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            target TEXT NOT NULL,
            file_name TEXT NOT NULL,
            line_no INTEGER NOT NULL,
            error_msg TEXT NOT NULL,
            row_data TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            handled_at TEXT,
            handled_by TEXT,
            handle_note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_errlog_status ON agent_import_error_logs(status);
        """
    )
