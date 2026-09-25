"""订单机台详情、批量机台码的 API 与管理台页面。"""
from __future__ import annotations
import os, re, tempfile
from fastapi.testclient import TestClient

from app import main as backend

ROOT = tempfile.mkdtemp(prefix="mf_order_task_")
os.environ.update(MATERIAL_FLOW_DATA=f"{ROOT}/data", MATERIAL_FLOW_UPLOADS=f"{ROOT}/uploads", INITIAL_ADMIN_PASSWORD="Admin@2026")

def add(uid, name, role):
    c=backend.db(); c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", (uid,name,name,role,backend.hash_password("Worker@2026"),0,1,backend.now())); c.commit(); c.close()
def login(client, name, password="Worker@2026"):
    r=client.post("/api/v1/auth/login",json={"username":name,"password":password,"deviceId":name}); assert r.status_code==200,r.text; return r.json()["accessToken"]
def form_csrf(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
def web_login(client, username, password):
    page = client.get("/admin/login")
    return client.post("/admin/login", data={"username": username, "password": password, "csrf_token": form_csrf(page.text)}, follow_redirects=False)

def seed():
    add("asm-task", "asm-task", "ASSEMBLER"); add("asm-other2", "asm-other2", "ASSEMBLER")
    c = backend.db(); now = backend.now()
    c.execute("INSERT INTO production_orders VALUES(?,?,?,?,?,?)", ("ord-task", "OT-1", "产品T", "IN_PROGRESS", now, now))
    c.execute("INSERT INTO devices VALUES(?,?,?,?,?,?,?,?,?)", ("dev-t1", "OT-1-M1", "机床T1", "一车间", None, "ACTIVE", "u_admin", now, now))
    c.execute("INSERT INTO assembly_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("task-t1", "OT-1", "dev-t1", "OT-1-M1", "asm-task", "IN_PROGRESS", 2, 1, None, None, now, now))
    c.execute("INSERT INTO assembly_task_stages VALUES(?,?,?,?,?,?,?,?)", ("task-t1", 1, "COMPLETED", 1, None, now, None, now))
    c.execute("INSERT INTO assembly_task_stages VALUES(?,?,?,?,?,?,?,?)", ("task-t1", 2, "IN_PROGRESS", 1, now, None, None, now))
    c.execute("INSERT INTO order_devices VALUES(?,?,?,?,?,?)", ("od-t1", "ord-task", "机台", "OT-1-M1", 1, now))
    c.execute("INSERT INTO materials VALUES(?,?,?,?,?,?,?,?,?,?)", ("mat-t1", "MTR-T1", "螺栓T", None, "件", None, None, 100, 100, 1))
    c.execute("INSERT INTO order_material_requirements VALUES(?,?,?,?,?,?,?,?,?,?)", ("req-t1", "ord-task", "od-t1", "mat-t1", 50, 50, 50, "IN_STOCK", now, now))
    c.commit(); c.close()

def test_task_detail_contract_and_scoping():
    seed()
    with TestClient(backend.app) as client:
        admin = login(client, "owlco", "Admin@2026"); asm = login(client, "asm-task"); other = login(client, "asm-other2")
        r = client.get("/api/v1/orders/OT-1/tasks/task-t1", headers={"Authorization": f"Bearer {admin}"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["deviceNo"] == "OT-1-M1" and d["status"] == "IN_PROGRESS" and d["progressStage"] == 2
        assert [(s["stageNo"], s["status"]) for s in d["stages"]] == [(1, "COMPLETED"), (2, "IN_PROGRESS")]
        assert d["materials"][0]["materialCode"] == "MTR-T1"
        assert "password_hash" not in r.text
        r2 = client.get("/api/v1/orders/OT-1/tasks/task-t1", headers={"Authorization": f"Bearer {asm}"})
        assert r2.status_code == 200
        denied = client.get("/api/v1/orders/OT-1/tasks/task-t1", headers={"Authorization": f"Bearer {other}"})
        assert denied.status_code == 403
        assert client.get("/api/v1/orders/OT-1/tasks/nope", headers={"Authorization": f"Bearer {admin}"}).status_code == 404
        assert client.get("/api/v1/orders/NOPE/tasks/task-t1", headers={"Authorization": f"Bearer {admin}"}).status_code == 404

def test_device_barcodes_batch():
    seed()
    with TestClient(backend.app) as client:
        admin = login(client, "owlco", "Admin@2026"); asm = login(client, "asm-task")
        r = client.get("/api/v1/orders/OT-1/device-barcodes", headers={"Authorization": f"Bearer {admin}"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["orderNo"] == "OT-1" and d["count"] == 1
        assert d["devices"][0]["qrUrl"] == "/api/v1/barcodes/device/OT-1-M1?kind=qr&image=png"
        assert d["devices"][0]["code128Url"].endswith("kind=code128&image=png")
        assert client.get("/api/v1/orders/OT-1/device-barcodes", headers={"Authorization": f"Bearer {asm}"}).status_code == 403
        assert client.get("/api/v1/orders/NOPE/device-barcodes", headers={"Authorization": f"Bearer {admin}"}).status_code == 404

def test_admin_order_task_and_batch_pages():
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        for url in ("/admin/orders/OT-1/tasks/task-t1", "/admin/orders/OT-1/barcodes",
                    "/admin/orders/OT-1/barcodes/print?kind=both", "/admin/orders?order_no=OT-1"):
            r = client.get(url)
            assert r.status_code == 200, url
        task_html = client.get("/admin/orders/OT-1/tasks/task-t1").text
        assert "OT-1-M1" in task_html and "机台物料" in task_html and "机台码" in task_html
        orders_html = client.get("/admin/orders?order_no=OT-1").text
        assert "生成订单码" in orders_html and "批量生成机台码" in orders_html
        assert client.get("/admin/orders/OT-1/tasks/nope").status_code == 200  # 错误页同样渲染
