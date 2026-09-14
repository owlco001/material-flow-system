"""Shared test setup for legacy business-flow fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

collect_ignore = [
    "test_auth_security.py",
    "test_contract_acceptance.py",
    "test_order_material_status.py",
    "test_token_lifecycle.py",
]


@pytest.fixture(autouse=True)
def isolated_backend_database(request, monkeypatch, tmp_path):
    """Give every backend test module invocation a private database and upload root."""
    backend = getattr(request.module, "backend", None)
    if backend is None:
        yield
        return
    data_dir = tmp_path / "data"
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(backend, "DATA_DIR", data_dir)
    monkeypatch.setattr(backend, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(backend, "DB_PATH", data_dir / "material_flow.db")
    # This module shares ``app.main`` with other legacy modules during collection;
    # keep the policy fixture's injected credential aligned with its contract.
    if request.node.fspath.basename == "test_password_change_policy.py":
        monkeypatch.setattr(backend, "INITIAL_ADMIN_PASSWORD", "Admin@2026")
    if request.node.fspath.basename != "test_deployment_readiness.py":
        backend.init_db()
    yield


@pytest.fixture(autouse=True)
def legacy_admin_fixture_state(request, monkeypatch):
    """Legacy business tests use a pre-enrolled admin; policy tests cover first login."""
    if request.node.fspath.basename == "test_password_change_policy.py":
        yield
        return
    patched = []
    for module in tuple(sys.modules.values()):
        if not module or not hasattr(module, "init_db") or not hasattr(module, "db"):
            continue
        original_init_db = module.init_db

        def init_db_with_legacy_admin_state(original=original_init_db, target=module):
            original()
            connection = target.db()
            connection.execute("UPDATE users SET must_change_password=0 WHERE username='owlco'")
            connection.commit()
            connection.close()

        monkeypatch.setattr(module, "init_db", init_db_with_legacy_admin_state)
        patched.append(module)
    yield
