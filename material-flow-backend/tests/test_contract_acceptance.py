"""后端契约验收测试 —— 对照《物料流转系统-V1-API契约冻结补遗》第 7 节。

运行：
    MATERIAL_FLOW_DATA=/tmp/mf_verify/data \
    MATERIAL_FLOW_UPLOADS=/tmp/mf_verify/uploads \
    INITIAL_ADMIN_PASSWORD='TestAdmin@2026' \
    python3 /tmp/verify_backend.py
"""

import os
import shutil
import sys
import uuid

TEST_ROOT = "/tmp/mf_verify"
shutil.rmtree(TEST_ROOT, ignore_errors=True)
os.environ["MATERIAL_FLOW_DATA"] = f"{TEST_ROOT}/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = f"{TEST_ROOT}/uploads"
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "TestAdmin@2026")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from fastapi.testclient import TestClient  # noqa: E402
import main as backend  # noqa: E402

# TestClient 作为上下文管理器时才会触发 startup 事件（建表 + 种子数据）
_client_ctx = TestClient(backend.app)
client = _client_ctx.__enter__()

passed, failed = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(name)
    mark = "✓" if cond else "✗"
    print(f"  {mark} {name}" + (f"  — {detail}" if detail and not cond else ""))


# ---------- 登录 ----------
r = client.post("/api/v1/auth/login", json={
    "username": "owlco", "password": "TestAdmin@2026",
    "deviceId": "test-device", "clientVersion": "0.3.0",
})
check("登录成功", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
token = r.json().get("accessToken", "")

H = {"Authorization": f"Bearer {token}"}
rid_h = {"X-Request-Id": str(uuid.uuid4())}


def write_headers(op_id: str) -> dict:
    return {**H, "X-Request-Id": str(uuid.uuid4()), "Idempotency-Key": op_id}


# ---------- 7.1 扫码枚举 ----------
print("\n[扫码枚举]")
r = client.post("/api/v1/scan/resolve",
                json={"rawValue": "SO202609120001", "clientOperationId": str(uuid.uuid4())},
                headers=H)
check("PRODUCTION_ORDER 解析", r.status_code == 200 and r.json()["type"] == "PRODUCTION_ORDER",
      r.text[:150])

r = client.post("/api/v1/scan/resolve",
                json={"rawValue": "FL0001", "clientOperationId": str(uuid.uuid4())},
                headers=H)
check("FLOW_NO 解析（契约枚举）", r.status_code == 200 and r.json()["type"] == "FLOW_NO",
      r.text[:150])

r = client.post("/api/v1/scan/resolve",
                json={"rawValue": "MTR-001", "clientOperationId": str(uuid.uuid4())},
                headers=H)
check("MATERIAL_CODE 解析", r.status_code == 200 and r.json()["type"] == "MATERIAL_CODE",
      r.text[:150])

r = client.post("/api/v1/scan/resolve",
                json={"rawValue": "A-01-03", "clientOperationId": str(uuid.uuid4())},
                headers=H)
check("LOCATION_CODE 解析", r.status_code == 200 and r.json()["type"] == "LOCATION_CODE",
      r.text[:150])

r = client.post("/api/v1/scan/resolve",
                json={"rawValue": "???", "clientOperationId": str(uuid.uuid4())},
                headers=H)
check("UNKNOWN 兜底", r.status_code == 200 and r.json()["type"] == "UNKNOWN", r.text[:150])

check("不存在 FLOW_RECORD 枚举",
      "FLOW_RECORD" not in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "main.py"), encoding="utf-8").read())

# ---------- 7.1 订单物料状态 ----------
print("\n[订单物料状态]")
r = client.post("/api/v1/orders/material-status",
                json={"documentType": "PRODUCTION_ORDER", "documentNo": "SO202609120001"},
                headers=H)
ok = r.status_code == 200 and r.json()["documentType"] == "PRODUCTION_ORDER" and len(r.json()["items"]) >= 1
check("PRODUCTION_ORDER 查询成功且按订单过滤", ok, r.text[:200])

r = client.post("/api/v1/orders/material-status",
                json={"documentType": "ORDER_NO", "documentNo": "SO202609120001"},
                headers=H)
check("ORDER_NO 返回 400 INVALID_SCAN_TYPE",
      r.status_code == 400 and r.json().get("error", {}).get("code") == "INVALID_SCAN_TYPE",
      r.text[:200])

r = client.post("/api/v1/orders/material-status",
                json={"documentType": "PRODUCTION_ORDER", "documentNo": "SO_NOT_EXIST"},
                headers=H)
check("不存在的订单返回空清单而非全量物料",
      r.status_code == 200 and r.json()["items"] == [], r.text[:200])

# ---------- 2. 请求头校验 ----------
print("\n[请求头校验]")
op_id = str(uuid.uuid4())
r = client.post("/api/v1/transfer-requests",
                json={"clientOperationId": op_id, "type": "OUTBOUND", "items": [
                    {"materialId": "mat_001", "quantity": 5, "expectedInventoryVersion": 1}
                ]}, headers=H)  # 缺 X-Request-Id
check("缺 X-Request-Id 返回 400 INVALID_REQUEST_ID",
      r.status_code == 400 and r.json().get("error", {}).get("code") == "INVALID_REQUEST_ID",
      r.text[:200])

r = client.post("/api/v1/transfer-requests",
                json={"clientOperationId": op_id, "type": "OUTBOUND", "items": [
                    {"materialId": "mat_001", "quantity": 5, "expectedInventoryVersion": 1}
                ]}, headers={**H, "X-Request-Id": "not-a-uuid"})
check("非法 X-Request-Id 返回 400", r.status_code == 400, r.text[:200])

r = client.post("/api/v1/transfer-requests",
                json={"clientOperationId": op_id, "type": "OUTBOUND", "items": [
                    {"materialId": "mat_001", "quantity": 5, "expectedInventoryVersion": 1}
                ]}, headers=H)  # 缺 Idempotency-Key
check("缺 Idempotency-Key 返回 400", r.status_code == 400, r.text[:200])

r = client.post("/api/v1/transfer-requests",
                json={"clientOperationId": op_id, "type": "OUTBOUND", "items": [
                    {"materialId": "mat_001", "quantity": 5, "expectedInventoryVersion": 1}
                ]},
                headers={**H, "X-Request-Id": str(uuid.uuid4()),
                         "Idempotency-Key": str(uuid.uuid4())})
check("Idempotency-Key 与 clientOperationId 不一致返回 400 IDEMPOTENCY_KEY_MISMATCH",
      r.status_code == 400 and r.json().get("error", {}).get("code") == "IDEMPOTENCY_KEY_MISMATCH",
      r.text[:200])

# ---------- 数量校验 ----------
print("\n[数量校验]")
for bad, label in [(0, "零"), (-1, "负数")]:
    op = str(uuid.uuid4())
    r = client.post("/api/v1/transfer-requests",
                    json={"clientOperationId": op, "type": "OUTBOUND", "items": [
                        {"materialId": "mat_001", "quantity": bad, "expectedInventoryVersion": 1}
                    ]}, headers=write_headers(op))
    check(f"quantity={label} 被拒绝", r.status_code == 422 or r.status_code == 400, r.text[:120])

# ---------- 7.3 幂等 ----------
print("\n[幂等]")
op_id = str(uuid.uuid4())
payload = {"clientOperationId": op_id, "type": "OUTBOUND", "documentNo": "SO202609120001",
           "items": [{"materialId": "mat_001", "quantity": 10, "expectedInventoryVersion": 1}]}
r1 = client.post("/api/v1/transfer-requests", json=payload, headers=write_headers(op_id))
r2 = client.post("/api/v1/transfer-requests", json=payload, headers=write_headers(op_id))
same = (r1.status_code == 200 and r2.status_code == 200
        and r1.json()["requestId"] == r2.json()["requestId"])
check("相同 clientOperationId + 相同 body 返回同一 requestId", same,
      f"{r1.text[:100]} | {r2.text[:100]}")
tr_id = r1.json().get("requestId", "")

bad_payload = {**payload, "items": [
    {"materialId": "mat_001", "quantity": 99, "expectedInventoryVersion": 1}]}
r3 = client.post("/api/v1/transfer-requests", json=bad_payload, headers=write_headers(op_id))
check("同幂等键换 quantity 返回 409 IDEMPOTENCY_PAYLOAD_MISMATCH",
      r3.status_code == 409
      and r3.json().get("error", {}).get("code") == "IDEMPOTENCY_PAYLOAD_MISMATCH",
      r3.text[:200])

# ---------- 库存快照 ----------
c = backend.db()
before = c.execute("SELECT available_quantity,version FROM materials WHERE id='mat_001'").fetchone()
before_qty, before_ver = before["available_quantity"], before["version"]
c.close()

# ---------- 7.5 乐观锁 ----------
print("\n[乐观锁 409]")
op_id = str(uuid.uuid4())
payload = {"clientOperationId": op_id, "type": "OUTBOUND",
           "items": [{"materialId": "mat_001", "quantity": 5,
                      "sourceLocationCode": "A-01-03", "expectedInventoryVersion": 999}]}
r = client.post("/api/v1/transfer-requests", json=payload, headers=write_headers(op_id))
stale_tr = r.json().get("requestId", "")
r = client.post(f"/api/v1/transfer-requests/{stale_tr}/approve",
                json={"decision": "APPROVE", "comment": "ok"},
                headers={**H, "X-Request-Id": str(uuid.uuid4())})
# 需要另一个用户执行，否则先被同人隔离拦截
c0 = backend.db()
if not c0.execute("SELECT 1 FROM users WHERE username='wh1'").fetchone():
    c0.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)",
               ("u_wh1", "wh1", "仓管一号", "WAREHOUSE_ADMIN",
                backend.hash_password("Wh1@2026"), 0, 1, backend.now()))
    c0.commit()
c0.close()
_r = client.post("/api/v1/auth/login", json={"username": "wh1", "password": "Wh1@2026",
                                            "deviceId": "d1", "clientVersion": "0.3.0"})
H1 = {"Authorization": f"Bearer {_r.json().get('accessToken','')}"}
r = client.post(f"/api/v1/transfer-requests/{stale_tr}/execute",
                headers={**H1, "X-Request-Id": str(uuid.uuid4())})
check("旧版本执行返回 409 INVENTORY_VERSION_CONFLICT",
      r.status_code == 409
      and r.json().get("error", {}).get("code") == "INVENTORY_VERSION_CONFLICT",
      r.text[:200])

c = backend.db()
after = c.execute("SELECT available_quantity,version FROM materials WHERE id='mat_001'").fetchone()
check("409 后库存未变",
      after["available_quantity"] == before_qty and after["version"] == before_ver,
      f"before={before_qty}/{before_ver} after={after['available_quantity']}/{after['version']}")
c.close()

# ---------- 7.6 审批/执行同人隔离 ----------
print("\n[同人隔离]")
_op = str(uuid.uuid4())
_pl = {"clientOperationId": _op, "type": "OUTBOUND",
       "items": [{"materialId": "mat_001", "quantity": 1,
                  "sourceLocationCode": "A-01-03",
                  "expectedInventoryVersion": before_ver}]}
_r1 = client.post("/api/v1/transfer-requests", json=_pl, headers=write_headers(_op))
_same_tr = _r1.json().get("requestId", "")
client.post(f"/api/v1/transfer-requests/{_same_tr}/approve",
            json={"decision": "APPROVE", "comment": "ok"},
            headers={**H, "X-Request-Id": str(uuid.uuid4())})
_r2 = client.post(f"/api/v1/transfer-requests/{_same_tr}/execute",
                  headers={**H, "X-Request-Id": str(uuid.uuid4())})
check("ADMIN 自审自执行被拒（409 APPROVAL_EXECUTOR_SAME_USER）",
      _r2.status_code == 409
      and _r2.json().get("error", {}).get("code") == "APPROVAL_EXECUTOR_SAME_USER",
      _r2.text[:200])

# ---------- 7.7 库存不足 ----------
print("\n[库存不足]")
op_id = str(uuid.uuid4())
payload = {"clientOperationId": op_id, "type": "OUTBOUND",
           "items": [{"materialId": "mat_001", "quantity": 999999,
                      "sourceLocationCode": "A-01-03", "expectedInventoryVersion": before_ver}]}
r = client.post("/api/v1/transfer-requests", json=payload, headers=write_headers(op_id))
short_tr = r.json().get("requestId", "")
client.post(f"/api/v1/transfer-requests/{short_tr}/approve",
            json={"decision": "APPROVE", "comment": "ok"},
            headers={**H, "X-Request-Id": str(uuid.uuid4())})
# 换一个用户执行以绕开同人隔离
c = backend.db()
if not c.execute("SELECT 1 FROM users WHERE username='wh2'").fetchone():
    c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)",
              ("u_wh2", "wh2", "仓管二号", "WAREHOUSE_ADMIN",
               backend.hash_password("Wh2@2026"), 0, 1, backend.now()))
    c.commit()
c.close()
r = client.post("/api/v1/auth/login", json={"username": "wh2", "password": "Wh2@2026",
                                            "deviceId": "d2", "clientVersion": "0.3.0"})
H2 = {"Authorization": f"Bearer {r.json().get('accessToken','')}"}
r = client.post(f"/api/v1/transfer-requests/{short_tr}/execute",
                headers={**H2, "X-Request-Id": str(uuid.uuid4())})
check("库存不足返回 409 INSUFFICIENT_INVENTORY",
      r.status_code == 409
      and r.json().get("error", {}).get("code") == "INSUFFICIENT_INVENTORY",
      r.text[:200])
c = backend.db()
after = c.execute("SELECT available_quantity FROM materials WHERE id='mat_001'").fetchone()
check("库存不足整单回滚", after["available_quantity"] == before_qty,
      f"{after['available_quantity']} != {before_qty}")
c.close()

# ---------- 7.8 统一错误结构 ----------
print("\n[统一错误结构]")
r = client.post("/api/v1/orders/material-status",
                json={"documentType": "ORDER_NO", "documentNo": "X"}, headers=H)
err = r.json().get("error", {})
check("错误结构含 code/retryable/traceId",
      all(k in err for k in ("code", "message", "retryable", "traceId")), r.text[:200])

# ---------- 汇总 ----------
print("\n" + "=" * 56)
print(f"通过 {len(passed)} / {len(passed) + len(failed)}")
if failed:
    print("失败项：")
    for f in failed:
        print("  ✗", f)
    sys.exit(1)
print("全部通过 ✓")
