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
        -- agent_import_* 表由 app.agent.importer._ensure_operation_table() 创建（DRY，此处不重复）
        -- agent_settings 独立建表（fix/admin-web-500s 未覆盖）
        CREATE TABLE IF NOT EXISTS agent_settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
        """
    )
