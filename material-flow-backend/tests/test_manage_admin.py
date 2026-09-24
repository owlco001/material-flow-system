"""管理员运维 CLI 契约测试（docs/DEPLOYMENT.md「Administrator provisioning」）。"""
from __future__ import annotations

import io
import sys

import pytest

from app import main as backend
from app import manage_admin


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    c.commit()
    c.close()
    yield


def test_create_admin_persists_hash_and_forces_password_change():
    result = manage_admin.create_admin("boss1", "厂长", "ADMIN", "StrongPass1")
    assert result["username"] == "boss1" and result["mustChangePassword"] is True
    c = backend.db()
    row = c.execute("SELECT * FROM users WHERE username='boss1'").fetchone()
    audit = c.execute(
        "SELECT 1 FROM audit_events WHERE event_type='ADMIN_PROVISIONED_CLI' AND entity_id=?",
        (result["userId"],),
    ).fetchone()
    c.close()
    assert row["role"] == "ADMIN" and row["must_change_password"] == 1 and row["active"] == 1
    assert backend.check_password("StrongPass1", row["password_hash"])
    assert audit is not None


def test_create_admin_rejects_duplicates_short_passwords_and_bad_roles():
    manage_admin.create_admin("boss1", "厂长", "ADMIN", "StrongPass1")
    with pytest.raises(ValueError, match="工号已存在"):
        manage_admin.create_admin("boss1", "另一个", "ADMIN", "StrongPass2")
    with pytest.raises(ValueError, match="至少 8 位"):
        manage_admin.create_admin("boss2", "短密码", "ADMIN", "short")
    with pytest.raises(ValueError, match="role 只能是"):
        manage_admin.create_admin("boss3", "装配工", "ASSEMBLER", "StrongPass3")


def test_reset_password_rotates_hash_and_revokes_sessions():
    created = manage_admin.create_admin("boss1", "厂长", "ADMIN", "OldPass123")
    c = backend.db()
    c.execute(
        "INSERT INTO sessions(token,user_id,expires_at,token_type,device_id) VALUES(?,?,?,?,?)",
        ("tok-1", created["userId"], 4102444800, "ACCESS", "dev-1"),
    )
    c.execute(
        "INSERT INTO web_sessions(id,user_id,csrf_token,created_at,expires_at,last_seen_at)"
        " VALUES(?,?,?,?,?,?)",
        ("ws-1", created["userId"], "csrf", backend.now(), 4102444800, 4102444800),
    )
    c.commit()
    c.close()

    result = manage_admin.reset_password("boss1", "NewPass456")
    assert result["revokedSessions"] == 1 and result["revokedWebSessions"] == 1

    c = backend.db()
    row = c.execute("SELECT * FROM users WHERE username='boss1'").fetchone()
    remaining = c.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (created["userId"],)).fetchone()[0]
    web_remaining = c.execute("SELECT COUNT(*) FROM web_sessions WHERE user_id=?", (created["userId"],)).fetchone()[0]
    c.close()
    assert not backend.check_password("OldPass123", row["password_hash"])
    assert backend.check_password("NewPass456", row["password_hash"])
    assert row["must_change_password"] == 1
    assert remaining == 0 and web_remaining == 0


def test_reset_password_unknown_user_and_short_password():
    with pytest.raises(ValueError, match="工号不存在"):
        manage_admin.reset_password("ghost", "NewPass456")
    manage_admin.create_admin("boss1", "厂长", "ADMIN", "StrongPass1")
    with pytest.raises(ValueError, match="至少 8 位"):
        manage_admin.reset_password("boss1", "short")


def test_read_password_from_stdin_enforces_minimum(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("tiny\n"))
    with pytest.raises(ValueError, match="至少 8 位"):
        manage_admin.read_password(from_stdin=True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("LongEnough1\n"))
    assert manage_admin.read_password(from_stdin=True) == "LongEnough1"


def test_list_and_status_shapes():
    manage_admin.create_admin("boss1", "厂长", "WAREHOUSE_ADMIN", "StrongPass1")
    users = manage_admin.list_users()
    assert any(u["username"] == "boss1" and u["role"] == "WAREHOUSE_ADMIN" for u in users)
    status = manage_admin.setup_status()
    assert "state" in status
