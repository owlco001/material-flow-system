from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import main as backend


def login(client, username, password):
    r = client.post('/api/v1/auth/login', json={'username': username, 'password': password, 'deviceId': username})
    assert r.status_code == 200, r.text
    return r.json()


def headers(token, operation):
    return {'Authorization': f'Bearer {token}', 'X-Request-Id': str(uuid.uuid4()), 'Idempotency-Key': operation}


def add_user(uid='u_edit', username='edit-user', role='OPERATOR'):
    c = backend.db()
    c.execute('INSERT INTO users(id,username,display_name,role,password_hash,must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)',
              (uid, username, username, role, backend.hash_password('User@2026'), 0, 1, backend.now()))
    c.commit(); c.close()


@pytest.fixture(autouse=True)
def admin_fixture():
    c = backend.db()
    c.execute("DELETE FROM sessions")
    c.execute("INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)", ('u_admin', 'owlco', '管理员', 'ADMIN', backend.hash_password('Admin@2026'), 0, 1, backend.now()))
    c.commit(); c.close()
    yield


def test_admin_can_edit_and_replay_without_username_change():
    with TestClient(backend.app) as client:
        token = login(client, 'owlco', 'Admin@2026')['accessToken']
        add_user()
        op = str(uuid.uuid4()); payload = {'clientOperationId': op, 'displayName': '新名字', 'role': 'MATERIAL', 'active': True}
        first = client.patch('/api/v1/admin/users/u_edit', json=payload, headers=headers(token, op))
        assert first.status_code == 200, first.text
        assert first.json()['displayName'] == '新名字'
        assert 'password' not in first.text.lower()
        replay = client.patch('/api/v1/admin/users/u_edit', json=payload, headers=headers(token, op))
        assert replay.status_code == 200 and replay.json()['idempotent'] is True
        conflict = client.patch('/api/v1/admin/users/u_edit', json={**payload, 'displayName': 'other'}, headers=headers(token, op))
        assert conflict.status_code == 409
        assert backend.db().execute("SELECT username FROM users WHERE id='u_edit'").fetchone()[0] == 'edit-user'


def test_edit_rejects_admin_self_disable_and_forbidden_role():
    with TestClient(backend.app) as client:
        token = login(client, 'owlco', 'Admin@2026')['accessToken']; add_user()
        op = str(uuid.uuid4())
        assert client.patch('/api/v1/admin/users/u_edit', json={'clientOperationId': op, 'role': 'ADMIN'}, headers=headers(token, op)).status_code == 400
        op = str(uuid.uuid4())
        assert client.patch('/api/v1/admin/users/u_admin', json={'clientOperationId': op, 'active': False}, headers=headers(token, op)).status_code == 409


def test_password_reset_revokes_sessions_forces_change_and_stores_argon2id():
    with TestClient(backend.app) as client:
        token = login(client, 'owlco', 'Admin@2026')['accessToken']; add_user()
        target = login(client, 'edit-user', 'User@2026'); op = str(uuid.uuid4())
        response = client.post('/api/v1/admin/users/u_edit/password-reset', json={'newPassword': 'NewUser@2026', 'clientOperationId': op}, headers=headers(token, op))
        assert response.status_code == 200 and 'NewUser@2026' not in response.text and 'password_hash' not in response.text
        assert client.get('/api/v1/setup/status', headers={'Authorization': f"Bearer {target['accessToken']}"}).status_code in (200, 401)
        c = backend.db(); row = c.execute("SELECT password_hash,must_change_password FROM users WHERE id='u_edit'").fetchone(); assert row['password_hash'].startswith('$argon2id$') and 'NewUser@2026' not in row['password_hash'] and row['must_change_password'] == 1; assert c.execute("SELECT COUNT(*) FROM sessions WHERE user_id='u_edit'").fetchone()[0] == 0; c.close()
        replay = client.post('/api/v1/admin/users/u_edit/password-reset', json={'newPassword': 'NewUser@2026', 'clientOperationId': op}, headers=headers(token, op))
        assert replay.status_code == 200 and replay.json()['idempotent'] is True
