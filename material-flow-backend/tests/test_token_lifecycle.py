"""令牌生命周期验收测试 —— refresh token 轮转 / 重用检测 / 会话吊销。

运行：
    python3 tests/test_token_lifecycle.py

注意：本文件为顶层直执行脚本（非 pytest 用例）。

覆盖点（契约 A03）：
  - 登录同时返回 accessToken 与 refreshToken，时效不同
  - access token 可用于业务接口，refresh token 不可
  - refresh 端点可用刷新令牌换取新令牌对
  - 刷新后旧刷新令牌立即失效（一次性使用）
  - 旧刷新令牌被重放 → 整族会话吊销（泄露检测）
  - 登出仅吊销当前设备，不影响其他设备
  - 改密吊销全部会话
  - 改密必须校验旧密码，且新旧密码不得相同
"""

import os
import shutil
import sys
import time

TEST_ROOT = "/tmp/mf_token_verify"
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


ADMIN, ADMIN_PW = "owlco", os.environ["INITIAL_ADMIN_PASSWORD"]


def login(username=ADMIN, password=ADMIN_PW, device="dev-A"):
    return client.post("/api/v1/auth/login",
                       json={"username": username, "password": password, "deviceId": device},
                       headers=rid())


def auth(token):
    return {"Authorization": f"Bearer {token}", "X-Request-Id": str(__import__("uuid").uuid4())}


# ==================== 1. 登录返回令牌对 ====================
print("\n[令牌签发]")

r = login()
check("登录成功", r.status_code == 200, r.text[:200])
body = r.json()

access = body.get("accessToken", "")
refresh = body.get("refreshToken", "")

check("返回 accessToken", bool(access), str(body)[:150])
check("返回 refreshToken", bool(refresh), str(body)[:150])
check("access 与 refresh 不相同", access != refresh and access and refresh)
check("返回 refreshExpiresAt", "refreshExpiresAt" in body, str(body)[:150])

# access 短时效、refresh 长时效
a_exp = body["expiresAt"]
r_exp = body["refreshExpiresAt"]
check("access 时效短于 refresh", a_exp < r_exp, f"{a_exp} vs {r_exp}")

# ==================== 2. 两类令牌用途隔离 ====================
print("\n[用途隔离]")

r = client.get("/api/v1/materials/MTR-001/inventory", headers=auth(access))
check("access token 可用于业务接口", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

r = client.get("/api/v1/materials/MTR-001/inventory", headers=auth(refresh))
check("refresh token 不可用于业务接口", r.status_code == 401, f"{r.status_code} {r.text[:150]}")

# ==================== 3. 刷新换取新令牌对 ====================
print("\n[令牌刷新]")

r = client.post("/api/v1/auth/refresh",
                json={"refreshToken": refresh, "deviceId": "dev-A"}, headers=rid())
check("刷新成功", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

pair2 = r.json() if r.status_code == 200 else {}
access2, refresh2 = pair2.get("accessToken", ""), pair2.get("refreshToken", "")

check("刷新返回新的 accessToken", access2 and access2 != access)
check("刷新返回新的 refreshToken", refresh2 and refresh2 != refresh)

r = client.get("/api/v1/materials/MTR-001/inventory", headers=auth(access2))
check("新 access token 可用", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

# ==================== 4. 旧刷新令牌一次性 ====================
print("\n[一次性使用与重用检测]")

r = client.post("/api/v1/auth/refresh",
                json={"refreshToken": refresh, "deviceId": "dev-A"}, headers=rid())
code = r.json().get("error", {}).get("code") if r.status_code != 200 else None
check("旧 refresh token 已失效", r.status_code == 401, f"{r.status_code} {r.text[:200]}")
check("失效原因标记为重用/无效",
      code in ("INVALID_REFRESH_TOKEN", None),
      str(r.text)[:200])

# 重用检测：上面的重放应已触发整族吊销，新令牌也应失效
r = client.post("/api/v1/auth/refresh",
                json={"refreshToken": refresh2, "deviceId": "dev-A"}, headers=rid())
check("重用旧令牌后，该族新令牌一并吊销", r.status_code == 401, f"{r.status_code} {r.text[:200]}")

r = client.get("/api/v1/materials/MTR-001/inventory", headers=auth(access2))
check("整族吊销后 access token 同步失效", r.status_code == 401, f"{r.status_code} {r.text[:150]}")

# ==================== 5. 登出仅影响当前设备 ====================
print("\n[设备级登出]")

d1 = login(device="dev-1").json()
d2 = login(device="dev-2").json()

r = client.post("/api/v1/auth/logout", headers=auth(d1["accessToken"]))
check("登出成功", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

r = client.get("/api/v1/materials/MTR-001/inventory", headers=auth(d1["accessToken"]))
check("登出后该设备 access 失效", r.status_code == 401, f"{r.status_code} {r.text[:150]}")

r = client.post("/api/v1/auth/refresh",
                json={"refreshToken": d1["refreshToken"], "deviceId": "dev-1"}, headers=rid())
check("登出后该设备 refresh 失效", r.status_code == 401, f"{r.status_code} {r.text[:150]}")

r = client.get("/api/v1/materials/MTR-001/inventory", headers=auth(d2["accessToken"]))
check("其他设备不受影响", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

# ==================== 6. 改密吊销全部会话 ====================
print("\n[改密]")

c = backend.db()
c.execute(
    "INSERT INTO users(id, username, display_name, role, password_hash, "
    "must_change_password, active, created_at) VALUES(?,?,?,?,?,?,?,?)",
    ("u_pw", "pw_user", "改密测试", "OPERATOR",
     backend.hash_password("OldPw@2026"), 1, 1, backend.now()),
)
c.commit(); c.close()

s1 = login("pw_user", "OldPw@2026", "pw-dev-1").json()
s2 = login("pw_user", "OldPw@2026", "pw-dev-2").json()

check("改密前两台设备均已登录",
      all(x.get("accessToken") for x in (s1, s2)), f"{len(s1)}/{len(s2)}")

# 旧密码错误
r = client.post("/api/v1/auth/change-password",
                json={"oldPassword": "WrongPw", "newPassword": "NewPw@2026"},
                headers=auth(s1["accessToken"]))
check("旧密码错误被拒", r.status_code == 401, f"{r.status_code} {r.text[:200]}")

# 新旧相同
r = client.post("/api/v1/auth/change-password",
                json={"oldPassword": "OldPw@2026", "newPassword": "OldPw@2026"},
                headers=auth(s1["accessToken"]))
check("新旧密码相同被拒", r.status_code == 400, f"{r.status_code} {r.text[:200]}")

# 新密码过短
r = client.post("/api/v1/auth/change-password",
                json={"oldPassword": "OldPw@2026", "newPassword": "short"},
                headers=auth(s1["accessToken"]))
check("新密码过短被拒", r.status_code == 422, f"{r.status_code} {r.text[:200]}")

# 正常改密
r = client.post("/api/v1/auth/change-password",
                json={"oldPassword": "OldPw@2026", "newPassword": "NewPw@2026"},
                headers=auth(s1["accessToken"]))
check("正常改密成功", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

# 全部会话应被吊销
r1 = client.get("/api/v1/materials/MTR-001/inventory", headers=auth(s1["accessToken"]))
r2 = client.get("/api/v1/materials/MTR-001/inventory", headers=auth(s2["accessToken"]))
check("改密后本设备会话失效", r1.status_code == 401, f"{r1.status_code}")
check("改密后其他设备会话一并失效", r2.status_code == 401, f"{r2.status_code}")

# 旧密码不可用，新密码可用
r = login("pw_user", "OldPw@2026", "pw-dev-3")
check("改密后旧密码不可登录", r.status_code == 401, f"{r.status_code}")

r = login("pw_user", "NewPw@2026", "pw-dev-3")
check("改密后新密码可登录", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
check("mustChangePassword 已清零",
      r.status_code == 200 and r.json().get("mustChangePassword") is False,
      str(r.json().get("mustChangePassword")) if r.status_code == 200 else r.text[:120])

# ==================== 7. 无效令牌 ====================
print("\n[无效令牌]")

r = client.post("/api/v1/auth/refresh",
                json={"refreshToken": "x" * 40, "deviceId": "dev"}, headers=rid())
check("不存在的 refresh token 被拒", r.status_code == 401, f"{r.status_code} {r.text[:150]}")

r = client.post("/api/v1/auth/refresh",
                json={"refreshToken": "short", "deviceId": "dev"}, headers=rid())
check("过短的 refresh token 被 422 拒绝", r.status_code == 422, f"{r.status_code}")

r = client.post("/api/v1/auth/refresh",
                json={"refreshToken": s1["accessToken"] if s1.get("accessToken") else "y" * 40,
                      "deviceId": "dev"}, headers=rid())
check("access token 不能用于刷新端点", r.status_code == 401, f"{r.status_code} {r.text[:150]}")

# ==================== 汇总 ====================
print("\n" + "=" * 56)
print(f"通过 {len(passed)} / {len(passed) + len(failed)}")
if failed:
    print("失败项：")
    for f in failed:
        print("  ✗", f)
    sys.exit(1)
print("全部通过 ✓")
