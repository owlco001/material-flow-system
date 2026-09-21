from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import uuid

from fastapi.testclient import TestClient

ROOT = tempfile.mkdtemp(prefix='mf_stage_')
os.environ['MATERIAL_FLOW_DATA'] = ROOT + '/data'
os.environ['MATERIAL_FLOW_UPLOADS'] = ROOT + '/uploads'
os.environ['INITIAL_ADMIN_PASSWORD'] = 'Admin@2026'
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'app'))
spec = importlib.util.spec_from_file_location('stage_backend', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'app', 'main.py'))
backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend
spec.loader.exec_module(backend)


def add_user(uid, username, role):
    c = backend.db()
    c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?)', (uid, username, username, role, backend.hash_password('Worker@2026'), 0, 1, backend.now()))
    c.commit(); c.close()


def login(client, username):
    result = client.post('/api/v1/auth/login', json={'username': username, 'password': 'Worker@2026', 'deviceId': username})
    assert result.status_code == 200, result.text
    return result.json()['accessToken']


def headers(token, operation):
    return {'Authorization': f'Bearer {token}', 'Idempotency-Key': operation, 'X-Request-Id': str(uuid.uuid4())}


def test_stage_lifecycle_version_permission_and_idempotency():
    backend.init_db()
    add_user('stage-asm', 'stageasm', 'ASSEMBLER')
    add_user('stage-other', 'stageother', 'ASSEMBLER')
    c = backend.db()
    c.execute('INSERT INTO assembly_tasks(id,order_no,device_id,device_no,assigned_assembler_id,status,progress_stage,task_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)', ('stage-task', 'WO-S', 'machine-s', 'M-S', 'stage-asm', 'MATERIAL_ACCEPTED', 0, 1, backend.now(), backend.now()))
    c.commit(); c.close()
    with TestClient(backend.app) as client:
        asm, other = login(client, 'stageasm'), login(client, 'stageother')
        op = str(uuid.uuid4()); body = {'clientOperationId': op, 'expectedVersion': 1}
        started = client.post('/api/v1/assembly/tasks/stage-task/stages/1/start', json=body, headers=headers(asm, op))
        assert started.status_code == 200 and started.json()['status'] == 'IN_PROGRESS' and started.json()['version'] == 2
        replay = client.post('/api/v1/assembly/tasks/stage-task/stages/1/start', json=body, headers=headers(asm, op))
        assert replay.status_code == 200 and replay.json()['idempotent'] is True
        stale_op = str(uuid.uuid4())
        stale = client.post('/api/v1/assembly/tasks/stage-task/stages/1/complete', json={'clientOperationId': stale_op, 'expectedVersion': 1}, headers=headers(asm, stale_op))
        assert stale.status_code == 409 and stale.json()['error']['code'] == 'ASSEMBLY_STAGE_VERSION_CONFLICT'
        denied_op = str(uuid.uuid4())
        denied = client.post('/api/v1/assembly/tasks/stage-task/stages/1/complete', json={'clientOperationId': denied_op, 'expectedVersion': 2}, headers=headers(other, denied_op))
        assert denied.status_code == 403
        done_op = str(uuid.uuid4())
        done = client.post('/api/v1/assembly/tasks/stage-task/stages/1/complete', json={'clientOperationId': done_op, 'expectedVersion': 2}, headers=headers(asm, done_op))
        assert done.status_code == 200 and done.json()['status'] == 'COMPLETED'
        rework_op = str(uuid.uuid4())
        rework = client.post('/api/v1/assembly/tasks/stage-task/stages/1/rework', json={'clientOperationId': rework_op, 'expectedVersion': 3, 'reworkReason': '尺寸不符'}, headers=headers(asm, rework_op))
        assert rework.status_code == 200 and rework.json()['status'] == 'REWORK_REQUIRED'
        c = backend.db()
        assert c.execute('SELECT COUNT(*) FROM assembly_task_stages').fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM audit_events WHERE entity_type='ASSEMBLY_TASK_STAGE'").fetchone()[0] == 3
        c.close()
