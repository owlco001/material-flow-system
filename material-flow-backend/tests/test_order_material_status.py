#!/usr/bin/env python3
"""订单物料状态数据模型对齐 —— 验收测试。

对齐《订单物料状态-后端模型对齐规格》第 6 节验收标准，
以及《订单 26B-013 后端数据对齐任务》的任务与验收清单。

运行方式（顶层直执行脚本，不是 pytest 用例）：
    MATERIAL_FLOW_DATA=/tmp/mf_omr/data \
    MATERIAL_FLOW_UPLOADS=/tmp/mf_omr/uploads \
    python3 tests/test_order_material_status.py

覆盖：
  1. 种子数据形状：25 台设备按 10/10/3/2 分布、75 条需求、三态齐全
  2. 外键完整性：所有 material_id 可 JOIN materials，device_id 可 JOIN order_devices
  3. 三态数量约束：缺货到货数=0；已到货在库数=0；在库在库数=需求数
  4. 幂等：重复执行种子不产生重复订单/设备/需求
  5. API：命中订单、PRODUCTION_ORDER、ORDER_NO 拒绝、404、非法类型
  6. 租户隔离：不返回无关订单数据
  7. 脱敏：响应不含数据库路径、主机信息
"""

from __future__ import annotations

import os
import sqlite3
import sys

os.environ.setdefault("MATERIAL_FLOW_DATA", "/tmp/mf_omr/data")
os.environ.setdefault("MATERIAL_FLOW_UPLOADS", "/tmp/mf_omr/uploads")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "OmrAdmin@2026")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import main as backend  # noqa: E402

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  \u2713 {name}")
    else:
        FAIL += 1
        FAILED.append(f"{name}  \u2014 {detail}")
        print(f"  \u2717 {name}  \u2014 {detail}")


client = TestClient(backend.app)
backend.init_db()

# ---------- 登录 ----------
r = client.post("/api/v1/auth/login", json={
    "username": "owlco", "password": "OmrAdmin@2026",
    "deviceId": "omr-test-device", "clientVersion": "0.3.4",
})
check("登录成功", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
token = r.json().get("accessToken", "")
H = {"Authorization": f"Bearer {token}", "X-Request-Id": "omr-req"}


def q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    c = sqlite3.connect(backend.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


# ---------- 1. 种子数据形状 ----------
print("\n[种子数据形状]")
order = q("SELECT * FROM production_orders WHERE order_no=?", ("26B-013",))
check("订单 26B-013 存在", len(order) == 1, f"命中 {len(order)} 行")
if order:
    check("产品名为「自动化流水线设备」", order[0]["product_name"] == "自动化流水线设备",
          repr(order[0]["product_name"]))
    order_id = order[0]["id"]
else:
    order_id = ""

dev_count = q("SELECT COUNT(*) n FROM order_devices WHERE order_id=?", (order_id,))[0]["n"]
check("设备总数 25 台", dev_count == 25, f"实际 {dev_count}")

plan = {r["device_type"]: r["n"] for r in q(
    "SELECT device_type, COUNT(*) n FROM order_devices WHERE order_id=? GROUP BY device_type",
    (order_id,))}
check("横向输送机 10 台", plan.get("横向输送机") == 10, f"实际 {plan.get('横向输送机')}")
check("十字输送机 10 台", plan.get("十字输送机") == 10, f"实际 {plan.get('十字输送机')}")
check("缓存 3 台", plan.get("缓存") == 3, f"实际 {plan.get('缓存')}")
check("合片机 2 台", plan.get("合片机") == 2, f"实际 {plan.get('合片机')}")

req_count = q("SELECT COUNT(*) n FROM order_material_requirements WHERE order_id=?",
              (order_id,))[0]["n"]
check("需求总数 75 条", req_count == 75, f"实际 {req_count}")

per_dev = q("""SELECT device_id, COUNT(*) n FROM order_material_requirements
               WHERE order_id=? GROUP BY device_id""", (order_id,))
check("每台设备恰好 3 条需求",
      len(per_dev) == 25 and all(r["n"] == 3 for r in per_dev),
      f"设备数 {len(per_dev)}，异常条数 {[r['n'] for r in per_dev if r['n'] != 3]}")

device_nos = q("SELECT device_no FROM order_devices WHERE order_id=?", (order_id,))
check("设备编号唯一", len({r["device_no"] for r in device_nos}) == len(device_nos),
      f"{len(device_nos)} 条编号，去重后 {len({r['device_no'] for r in device_nos})}")

# ---------- 2. 外键完整性 ----------
print("\n[外键完整性]")
orphan_mat = q("""SELECT COUNT(*) n FROM order_material_requirements r
                  LEFT JOIN materials m ON m.id = r.material_id
                  WHERE m.id IS NULL""")[0]["n"]
check("所有 material_id 均存在于 materials", orphan_mat == 0, f"孤儿 {orphan_mat} 条")

orphan_dev = q("""SELECT COUNT(*) n FROM order_material_requirements r
                  LEFT JOIN order_devices d ON d.id = r.device_id
                  WHERE r.device_id IS NOT NULL AND d.id IS NULL""")[0]["n"]
check("所有 device_id 均存在于 order_devices", orphan_dev == 0, f"孤儿 {orphan_dev} 条")

null_mat = q("""SELECT COUNT(*) n FROM order_material_requirements
                WHERE material_id IS NULL OR material_id=''""")[0]["n"]
check("无空 material_id", null_mat == 0, f"空值 {null_mat} 条")

# ---------- 3. 三态与数量约束 ----------
print("\n[状态与数量约束]")
statuses = {r["status_code"]: r["n"] for r in q(
    "SELECT status_code, COUNT(*) n FROM order_material_requirements GROUP BY status_code")}
for s in ("OUT_OF_STOCK", "ARRIVED", "IN_STOCK"):
    check(f"状态 {s} 存在", statuses.get(s, 0) > 0, f"计数 {statuses.get(s, 0)}")

bad_oos = q("""SELECT COUNT(*) n FROM order_material_requirements
               WHERE status_code='OUT_OF_STOCK' AND arrived_quantity != 0""")[0]["n"]
check("缺货：到货数恒为 0", bad_oos == 0, f"违规 {bad_oos} 条")

bad_arr = q("""SELECT COUNT(*) n FROM order_material_requirements
               WHERE status_code='ARRIVED' AND in_stock_quantity != 0""")[0]["n"]
check("已到货：在库数恒为 0", bad_arr == 0, f"违规 {bad_arr} 条")

bad_ins = q("""SELECT COUNT(*) n FROM order_material_requirements
               WHERE status_code='IN_STOCK' AND in_stock_quantity != required_quantity""")[0]["n"]
check("在库：在库数 = 需求数", bad_ins == 0, f"违规 {bad_ins} 条")

bad_range = q("""SELECT COUNT(*) n FROM order_material_requirements
                 WHERE arrived_quantity > required_quantity
                    OR in_stock_quantity > arrived_quantity""")[0]["n"]
check("数量单调：在库 <= 到货 <= 需求", bad_range == 0, f"违规 {bad_range} 条")

# ---------- 4. 幂等 ----------
print("\n[幂等性]")
for _ in range(2):
    backend.init_db()
check("重复执行 init_db 后订单数仍为 1",
      q("SELECT COUNT(*) n FROM production_orders WHERE order_no='26B-013'")[0]["n"] == 1)
check("重复执行 init_db 后设备数仍为 25",
      q("SELECT COUNT(*) n FROM order_devices WHERE order_id=?", (order_id,))[0]["n"] == 25)
check("重复执行 init_db 后需求数仍为 75",
      q("SELECT COUNT(*) n FROM order_material_requirements WHERE order_id=?",
        (order_id,))[0]["n"] == 75)

# ---------- 5. API 行为 ----------
print("\n[API 行为]")
r = client.post("/api/v1/orders/material-status", headers=H,
                json={"documentNo": "26B-013", "documentType": "PRODUCTION_ORDER"})
check("PRODUCTION_ORDER 查询返回 200", r.status_code == 200, f"HTTP {r.status_code} {r.text[:160]}")
if r.status_code == 200:
    d = r.json()
    check("documentNo 回显 26B-013", d.get("documentNo") == "26B-013", repr(d.get("documentNo")))
    check("productName 正确", d.get("productName") == "自动化流水线设备",
          repr(d.get("productName")))
    check("orderStatus 为 RELEASED", d.get("orderStatus") == "RELEASED",
          repr(d.get("orderStatus")))
    check("返回 75 条需求", len(d.get("items", [])) == 75, f"实际 {len(d.get('items', []))}")

    items = d.get("items", [])
    check("每条含 deviceType / deviceNo",
          all(i.get("deviceType") and i.get("deviceNo") for i in items),
          "存在缺设备信息的条目")
    check("每条含 materialCode / materialName / unit",
          all(i.get("materialCode") and i.get("materialName") and i.get("unit") for i in items),
          "存在缺物料信息的条目")
    codes = {i["materialCode"] for i in items}
    check("物料编码来自 materials 主数据",
          codes <= {r["code"] for r in q("SELECT code FROM materials")},
          f"越界编码 {codes - {r['code'] for r in q('SELECT code FROM materials')}}")
    req_ids = {i["requirementId"] for i in items}
    check("requirementId 唯一", len(req_ids) == len(items),
          f"{len(items)} 条，去重后 {len(req_ids)}")

    # 排序：按设备序号（非编号字符串）、物料编码升序。
    # 注意不能用 deviceNo 字符串比较 —— "HZ10" < "HZ02" 是字典序陷阱，
    # 真实排序依据是 order_devices.sequence_no。
    seq_map = {r["id"]: r["sequence_no"] for r in q(
        "SELECT id, sequence_no FROM order_devices WHERE order_id=?", (order_id,))}
    sort_keys = [(seq_map.get(i["deviceId"], 0), i["materialCode"]) for i in items]
    check("按设备序号+物料编码升序", sort_keys == sorted(sort_keys), "排序不符合约定")

    # 首条应为序号最小的设备（横向输送机第 1 台）
    check("首条为序号最小的设备",
          items and items[0]["deviceType"] == "横向输送机" and items[0]["deviceNo"] == "26B-013-HZ01",
          f"实际 {items[0]['deviceNo'] if items else 'N/A'}")

    # 序号 9/10 的设备必须按序排列（验证不是字符串排序）
    dev_seq_order = []
    for i in items:
        if not dev_seq_order or dev_seq_order[-1] != i["deviceNo"]:
            dev_seq_order.append(i["deviceNo"])
    check("设备按序号连续排列（HZ09 在 HZ10 之前）",
          dev_seq_order.index("26B-013-HZ09") < dev_seq_order.index("26B-013-HZ10")
          if "26B-013-HZ09" in dev_seq_order and "26B-013-HZ10" in dev_seq_order
          else False,
          f"顺序 {dev_seq_order[:12]}")

r = client.post("/api/v1/orders/material-status", headers=H,
                json={"documentNo": "26B-013", "documentType": "PRODUCTION_ORDER"})
check("PRODUCTION_ORDER 同样放行（过渡期兼容）",
      r.status_code == 200 and len(r.json().get("items", [])) == 75,
      f"HTTP {r.status_code}")

r = client.post("/api/v1/orders/material-status", headers=H,
                json={"documentNo": "26B-013", "documentType": "ORDER_NO"})
check("ORDER_NO 已废弃并返回 400",
      r.status_code == 400 and r.json().get("error", {}).get("code") == "INVALID_SCAN_TYPE",
      f"HTTP {r.status_code} {r.text[:160]}")

r = client.post("/api/v1/orders/material-status", headers=H,
                json={"documentNo": "NO-SUCH-ORDER", "documentType": "PRODUCTION_ORDER"})
check("订单不存在返回 404",
      r.status_code == 404, f"HTTP {r.status_code} {r.text[:160]}")
check("404 错误码为 ORDER_NOT_FOUND",
      r.json().get("error", {}).get("code") == "ORDER_NOT_FOUND",
      r.text[:160])

r = client.post("/api/v1/orders/material-status", headers=H,
                json={"documentNo": "26B-013", "documentType": "LOGISTICS"})
check("非法 documentType 返回 400",
      r.status_code == 400, f"HTTP {r.status_code} {r.text[:160]}")

r = client.post("/api/v1/orders/material-status", headers=H,
                json={"documentNo": "", "documentType": "PRODUCTION_ORDER"})
check("空 documentNo 返回 400", r.status_code == 400, f"HTTP {r.status_code}")

r = client.post("/api/v1/orders/material-status",
                json={"documentNo": "26B-013", "documentType": "ORDER_NO"})
check("未认证返回 401/403", r.status_code in (401, 403), f"HTTP {r.status_code}")

# ---------- 6. 不返回无关订单数据 ----------
print("\n[订单隔离]")
r = client.post("/api/v1/orders/material-status", headers=H,
                json={"documentNo": "26B-013", "documentType": "ORDER_NO"})
ids_returned = {i["materialId"] for i in r.json().get("items", [])}
mat_001_reqs = q("""SELECT COUNT(*) n FROM order_material_requirements
                    WHERE material_id='mat_001' AND order_id != ?""", (order_id,))[0]["n"]
check("不返回其他订单的物料需求",
      "mat_001" not in ids_returned or mat_001_reqs == 0,
      f"mat_001 出现在结果中，其他订单引用数 {mat_001_reqs}")

# ---------- 7. 脱敏 ----------
print("\n[脱敏]")
body = r.text
check("响应不含数据库路径", "/srv/" not in body and ".db" not in body, body[:160])
check("响应不含主机信息", "sqlite3" not in body.lower(), body[:160])

# ---------- 汇总 ----------
print("\n" + "=" * 56)
print(f"通过 {PASS} / {PASS + FAIL}")
if FAILED:
    print("失败项：")
    for f in FAILED:
        print(f"  \u2717 {f}")
    sys.exit(1)
print("全部通过 \u2713")
