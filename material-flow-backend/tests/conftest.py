"""Shared test setup for legacy business-flow fixtures."""
from __future__ import annotations

import re
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

# app.main 在 import 时就把 INITIAL_ADMIN_PASSWORD 固化成模块常量，而 app.main
# 整个 pytest 进程只被 import 一次。各测试文件又在 import 期用 os.environ
# 写入互不相同的密码（硬写/update 先到先得，setdefault 则在已存在时静默失效），
# 于是全量收集时「首个被导入的文件」的密码胜出，其余文件用自己的密码建库、
# 却拿别人的密码登录 -> 401 连锁失败。逐文件隔离运行不会触发，故长期被掩盖。
#
# 修复：集中在此处按「测试文件声明的期望密码」对齐 backend 模块常量，
# 不改动 24 个测试文件顶部散落的 os.environ 写法（散改易漏，单点拦截更稳）。
_PASSWORD_PATTERNS = (
    # os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "...")
    re.compile(r"""setdefault\(\s*["']INITIAL_ADMIN_PASSWORD["']\s*,\s*["']([^"']+)["']"""),
    # os.environ["INITIAL_ADMIN_PASSWORD"] = "..."
    re.compile(r"""os\.environ\[["']INITIAL_ADMIN_PASSWORD["']\]\s*=\s*["']([^"']+)["']"""),
    # os.environ.update(..., INITIAL_ADMIN_PASSWORD="...", ...)
    re.compile(r"""INITIAL_ADMIN_PASSWORD\s*[:=]\s*["']([^"']+)["']"""),
)


def _declared_admin_password(module_file: Path) -> str | None:
    """从测试文件源码里读出它自己声明的 INITIAL_ADMIN_PASSWORD 字面量。"""
    try:
        source = module_file.read_text(encoding="utf-8")
    except OSError:
        return None
    for pattern in _PASSWORD_PATTERNS:
        match = pattern.search(source)
        if match:
            return match.group(1)
    return None


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
    # 让 backend 的初始管理员密码与该模块自己声明的密码一致，消除跨模块快照污染。
    declared = _declared_admin_password(Path(str(request.node.fspath)))
    if declared is not None:
        monkeypatch.setattr(backend, "INITIAL_ADMIN_PASSWORD", declared)
    elif request.node.fspath.basename == "test_password_change_policy.py":
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
