"""Assembly GLB storage and distribution contract tests."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
os.environ["INITIAL_ADMIN_PASSWORD"] = "ModelAdmin@2026"
sys.path.insert(0, str(ROOT / "app"))
spec = importlib.util.spec_from_file_location("assembly_model_backend", ROOT / "app" / "main.py")
assert spec and spec.loader
backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend
spec.loader.exec_module(backend)

ADMIN_PASSWORD = "ModelAdmin@2026"
NEW_ADMIN_PASSWORD = "ModelAdminChanged@2026"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password, "deviceId": f"test-{username}"})
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def _admin_session(client: TestClient) -> str:
    initial = _login(client, "owlco", ADMIN_PASSWORD)
    response = client.post(
        "/api/v1/auth/change-password",
        json={"oldPassword": ADMIN_PASSWORD, "newPassword": NEW_ADMIN_PASSWORD},
        headers=_headers(initial),
    )
    assert response.status_code == 200, response.text
    return _login(client, "owlco", NEW_ADMIN_PASSWORD)


def _add_user(role: str = "OPERATOR") -> str:
    user_id = f"u_model_{uuid.uuid4().hex}"
    c = backend.db()
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (user_id, user_id, "模型读取用户", role, backend.hash_password("Reader@2026"), 0, 1, backend.now()),
    )
    c.commit()
    c.close()
    return user_id


@pytest.fixture
def glb_file(tmp_path: Path) -> tuple[Path, bytes]:
    # Fixed, tiny GLB-shaped fixture; no model asset is stored in the repository.
    content = b"glTF" + (2).to_bytes(4, "little") + (12).to_bytes(4, "little") + b"fixture"
    path = tmp_path / "GearboxAssy.glb"
    path.write_bytes(content)
    return path, content


def test_upload_metadata_download_and_content_idempotency(glb_file):
    path, content = glb_file
    with TestClient(backend.app) as client:
        admin = _admin_session(client)
        key = str(uuid.uuid4())
        headers = {**_headers(admin), "Idempotency-Key": key}
        response = client.post(
            "/api/v1/assembly-models",
            data={"modelCode": "GearboxAssy", "modelName": "Gearbox Assembly"},
            files={"file": (path.name, path.read_bytes(), "model/gltf-binary")},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["version"] == 1
        assert payload["byteSize"] == len(content)
        assert payload["sha256"] == hashlib.sha256(content).hexdigest()
        assert payload["status"] == "PUBLISHED"
        assert payload["idempotent"] is False

        reader_id = _add_user()
        reader = _login(client, reader_id, "Reader@2026")
        metadata = client.get("/api/v1/assembly-models/GearboxAssy/published", headers=_headers(reader))
        assert metadata.status_code == 200
        assert metadata.json()["downloadPath"] == payload["downloadPath"]
        downloaded = client.get(payload["downloadPath"], headers=_headers(reader))
        assert downloaded.status_code == 200
        assert downloaded.content == content
        assert downloaded.headers["content-length"] == str(len(content))
        assert downloaded.headers["etag"] == f'"{payload["sha256"]}"'

        replay = client.post(
            "/api/v1/assembly-models",
            data={"modelCode": "GearboxAssy", "modelName": "Gearbox Assembly"},
            files={"file": (path.name, path.read_bytes(), "model/gltf-binary")},
            headers={**_headers(admin), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert replay.status_code == 200
        assert replay.json()["modelId"] == payload["modelId"]
        assert replay.json()["idempotent"] is True


def test_model_upload_auth_validation_and_payload_mismatch(glb_file):
    path, content = glb_file
    with TestClient(backend.app) as client:
        assert client.post(
            "/api/v1/assembly-models",
            data={"modelCode": "GearboxAssy", "modelName": "Gearbox Assembly"},
            files={"file": (path.name, content, "model/gltf-binary")},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        ).status_code == 401
        admin = _admin_session(client)
        key = str(uuid.uuid4())
        headers = {**_headers(admin), "Idempotency-Key": key}
        bad = client.post(
            "/api/v1/assembly-models",
            data={"modelCode": "../escape", "modelName": "Bad"},
            files={"file": ("bad.glb", b"not-glb", "model/gltf-binary")},
            headers=headers,
        )
        assert bad.status_code == 422
        mismatch = client.post(
            "/api/v1/assembly-models",
            data={"modelCode": "GearboxAssy", "modelName": "Gearbox Assembly"},
            files={"file": (path.name, path.read_bytes(), "model/gltf-binary")},
            headers=headers,
        )
        assert mismatch.status_code == 201
        mismatch = client.post(
            "/api/v1/assembly-models",
            data={"modelCode": "GearboxAssy", "modelName": "Different Name"},
            files={"file": (path.name, path.read_bytes(), "model/gltf-binary")},
            headers=headers,
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "MODEL_UPLOAD_PAYLOAD_MISMATCH"


def test_model_file_type_magic_and_sha_are_rejected(glb_file):
    path, content = glb_file
    with TestClient(backend.app) as client:
        admin = _admin_session(client)
        base = {"Authorization": f"Bearer {admin}"}
        wrong_type = client.post(
            "/api/v1/assembly-models", data={"modelCode": "GearboxAssy", "modelName": "Bad"},
            files={"file": ("model.txt", content, "text/plain")}, headers={**base, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert wrong_type.status_code == 415
        wrong_magic = client.post(
            "/api/v1/assembly-models", data={"modelCode": "GearboxAssy", "modelName": "Bad"},
            files={"file": ("model.glb", b"BAD!", "model/gltf-binary")}, headers={**base, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert wrong_magic.status_code == 422
        wrong_sha = client.post(
            "/api/v1/assembly-models", data={"modelCode": "GearboxAssy", "modelName": "Bad", "sha256": "0" * 64},
            files={"file": (path.name, content, "model/gltf-binary")}, headers={**base, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert wrong_sha.status_code == 422
