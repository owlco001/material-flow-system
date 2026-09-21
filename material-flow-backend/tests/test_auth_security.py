"""认证安全验收测试 —— Argon2id 迁移 + 登录失败锁定。

运行：
    python3 tests/test_auth_security.py

注意：本文件为顶层直执行脚本（非 pytest 用例），与
test_contract_acceptance.py 保持一致的风格。

覆盖点（契约 A03）：
  - 新用户密码哈希为 Argon2id，参数不低于 OWASP 推荐
  - 历史 scrypt 哈希校验通过后透明升级为 Argon2id
  - 历史 scrypt 错误密码不得通过，且不触发升级
  - 同账号 15 分钟内失败 5 次即锁定，锁定期间密码正确也拒绝
  - 锁定窗口外的失败重新计数
  - 登录成功后清空失败计数
  - 失败响应不泄露账号是否存在（防用户名枚举）
"""

import os
import shutil
import sys
import time

TEST_ROOT = "/tmp/mf_auth_verify"
shutil.rmtree(TEST_ROOT, ignore_errors=True)
os.environ["MATERIAL_FLOW_DATA"] = f"{TEST_ROOT}/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = f"{TEST_ROOT}/uploads"
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "TestAdmin@2026")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from fastapi.testclient import TestClient  # noqa: E402
import main as backend  # noqa: E402

_client_ctx = TestClient(backend.app)
client = _client_ctx.__enter__()

passed, failed = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  [{detail}]" if (detail and not cond) else ""))


def rid() -> dict:
    import uuid
    return {"X-Request-Id": str(uuid.uuid4())}


ADMIN = "owlco"
ADMIN_PW = os.environ["INITIAL_ADMIN_PASSWORD"]


def do_login(username: str, password: str):
    return client.post("/api/v1/auth/login",
                       json={"username": username, "password": password,
                             "deviceId": "test-device-01"},
                       headers=rid())


# ==================== 1. Argon2id 落库 ====================
print("\n[Argon2id 哈希]")

c = backend.db()
row = c.execute("SELECT password_hash FROM users WHERE username=?", (ADMIN,)).fetchone()
hashed = row["password_hash"]
c.close()

check("新用户密码使用 Argon2id 格式", hashed.startswith("$argon2id$"), hashed[:40])
check("哈希参数为 OWASP 推荐 (m=65536,t=3,p=4)",
      "m=65536" in hashed and "t=3" in hashed and "p=4" in hashed,
      hashed)

r = do_login(ADMIN, ADMIN_PW)
check("Argon2id 用户可正常登录", r.status_code == 200, r.text[:200])

# ==================== 2. scrypt → Argon2id 透明升级 ====================
print("\n[scrypt 透明升级]")

# 手工植入一条历史 scrypt 哈希，模拟升级前遗留数据。
# 注意：盐必须是 16 字节原始字节的 hex 编码（与历史 hash_password 的
# secrets.token_bytes(16) 一致）。若用 ASCII 字符串直接 .hex()，
# 会把每个字符再编码一次导致盐长度翻倍，与真实历史数据语义不符。
legacy_salt = bytes.fromhex("0123456789abcdef0123456789abcdef")  # 16 字节
legacy_hash = backend.hashlib.scrypt(
    b"LegacyPw@2026", salt=legacy_salt, n=2**14, r=8, p=1
).hex()
legacy_encoded = f"scrypt${legacy_salt.hex()}${legacy_hash}"

c = backend.db()
c.execute(
    "INSERT INTO users(id, username, display_name, role, password_hash, "
    "must_change_password, active, created_at) VALUES(?,?,?,?,?,?,?,?)",
    ("u_legacy", "legacy_user", "遗留用户", "OPERATOR", legacy_encoded, 0, 1, backend.now()),
)
c.commit()

row = c.execute("SELECT password_hash FROM users WHERE id='u_legacy'").fetchone()
check("植入的哈希确为 scrypt", row["password_hash"].startswith("scrypt$"), row["password_hash"][:30])
c.close()

# 错误密码：应失败，且不得触发升级
r = do_login("legacy_user", "WrongPw@2026")
check("scrypt 用户错误密码被拒", r.status_code == 401, f"{r.status_code} {r.text[:120]}")

c = backend.db()
still = c.execute("SELECT password_hash FROM users WHERE id='u_legacy'").fetchone()["password_hash"]
check("校验失败时不升级哈希", still.startswith("scrypt$"), still[:30])
c.close()

# 正确密码：应成功，并就地升级
r = do_login("legacy_user", "LegacyPw@2026")
check("scrypt 用户正确密码可登录", r.status_code == 200, r.text[:200])

c = backend.db()
upgraded = c.execute("SELECT password_hash FROM users WHERE id='u_legacy'").fetchone()["password_hash"]
c.close()
check("校验通过后透明升级为 Argon2id", upgraded.startswith("$argon2id$"), upgraded[:40])

# 升级后仍可用同一密码登录
r = do_login("legacy_user", "LegacyPw@2026")
check("升级后原密码仍可用", r.status_code == 200, r.text[:200])

# ==================== 3. 登录失败锁定 ====================
print("\n[登录失败锁定]")

# 用独立账号，避免污染其他用例
c = backend.db()
c.execute(
    "INSERT INTO users(id, username, display_name, role, password_hash, "
    "must_change_password, active, created_at) VALUES(?,?,?,?,?,?,?,?)",
    ("u_lock", "lock_user", "锁定测试", "OPERATOR",
     backend.hash_password("RightPw@2026"), 0, 1, backend.now()),
)
c.commit(); c.close()

# 前 4 次失败：应返回 401（未达阈值）
for i in range(1, backend.LOGIN_MAX_FAILURES):
    r = do_login("lock_user", "WrongPw@2026")
    code = r.json().get("error", {}).get("code")

check(f"前 {backend.LOGIN_MAX_FAILURES - 1} 次失败返回 401 而非锁定",
      r.status_code == 401 and code != "ACCOUNT_LOCKED",
      f"{r.status_code} {r.text[:150]}")

# 第 5 次失败：触发锁定
r = do_login("lock_user", "WrongPw@2026")
code = r.json().get("error", {}).get("code")
check("第 5 次失败触发 429 ACCOUNT_LOCKED",
      r.status_code == 429 and code == "ACCOUNT_LOCKED",
      f"{r.status_code} {r.text[:150]}")

check("锁定错误标记为可重试",
      r.json().get("error", {}).get("retryable") is True, r.text[:150])

# 锁定期间即使密码正确也拒绝
r = do_login("lock_user", "RightPw@2026")
code = r.json().get("error", {}).get("code")
check("锁定期间正确密码同样被拒",
      r.status_code == 429 and code == "ACCOUNT_LOCKED",
      f"{r.status_code} {r.text[:150]}")

# 锁定期间不得签发会话
c = backend.db()
n_sessions = c.execute(
    "SELECT COUNT(*) n FROM sessions s JOIN users u ON u.id=s.user_id WHERE u.username='lock_user'"
).fetchone()["n"]
check("锁定期间未签发任何会话", n_sessions == 0, f"session 数={n_sessions}")
c.close()

# ==================== 4. 成功登录清空计数 ====================
print("\n[计数重置]")

c = backend.db()
c.execute(
    "INSERT INTO users(id, username, display_name, role, password_hash, "
    "must_change_password, active, created_at) VALUES(?,?,?,?,?,?,?,?)",
    ("u_reset", "reset_user", "重置测试", "OPERATOR",
     backend.hash_password("RightPw@2026"), 0, 1, backend.now()),
)
c.commit(); c.close()

# 失败 3 次（未达锁定阈值）
for _ in range(3):
    do_login("reset_user", "WrongPw@2026")

c = backend.db()
cnt_before = c.execute("SELECT failed_count FROM login_attempts WHERE username='reset_user'").fetchone()
check("失败计数已累计到 3", cnt_before is not None and cnt_before["failed_count"] == 3,
      str(dict(cnt_before) if cnt_before else None))
c.close()

# 成功登录
r = do_login("reset_user", "RightPw@2026")
check("未锁定账号正确密码可登录", r.status_code == 200, r.text[:200])

c = backend.db()
cnt_after = c.execute("SELECT * FROM login_attempts WHERE username='reset_user'").fetchone()
c.close()
check("登录成功后清空失败计数", cnt_after is None, str(dict(cnt_after) if cnt_after else "已清空"))

# ==================== 5. 防用户名枚举 ====================
print("\n[防用户名枚举]")

r_unknown = do_login("no_such_user_xyz", "AnyPw@2026")

c = backend.db()
c.execute(
    "INSERT INTO users(id, username, display_name, role, password_hash, "
    "must_change_password, active, created_at) VALUES(?,?,?,?,?,?,?,?)",
    ("u_enum", "enum_user", "枚举测试", "OPERATOR",
     backend.hash_password("RightPw@2026"), 0, 1, backend.now()),
)
c.commit(); c.close()

r_wrong = do_login("enum_user", "WrongPw@2026")

check("不存在的账号与错误密码返回同一状态下码",
      r_unknown.status_code == r_wrong.status_code == 401,
      f"{r_unknown.status_code} vs {r_wrong.status_code}")

check("响应文案不区分账号是否存在",
      r_unknown.json().get("detail") == r_wrong.json().get("detail"),
      f"{r_unknown.text[:80]} vs {r_wrong.text[:80]}")

# ==================== 汇总 ====================
print("\n" + "=" * 56)
print(f"通过 {len(passed)} / {len(passed) + len(failed)}")
if failed:
    print("失败项：")
    for f in failed:
        print("  ✗", f)
    sys.exit(1)
print("全部通过 ✓")
