"""Order detail aggregate contract and role scoping."""
from __future__ import annotations
import importlib.util, os, sys, tempfile, uuid
from fastapi.testclient import TestClient

ROOT = tempfile.mkdtemp(prefix="mf_order_detail_")
os.environ.update(MATERIAL_FLOW_DATA=f"{ROOT}/data", MATERIAL_FLOW_UPLOADS=f"{ROOT}/uploads", INITIAL_ADMIN_PASSWORD="Admin@2026")
APP = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app")
sys.path.insert(0, APP)
spec = importlib.util.spec_from_file_location("order_detail_backend", os.path.join(APP, "main.py"))
backend = importlib.util.module_from_spec(spec); sys.modules[spec.name] = backend; spec.loader.exec_module(backend); backend.init_db()

def add(uid, name, role):
    c=backend.db(); c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", (uid,name,name,role,backend.hash_password("Worker@2026"),0,1,backend.now())); c.commit(); c.close()
def login(client, name, password="Worker@2026"):
    r=client.post("/api/v1/auth/login",json={"username":name,"password":password,"deviceId":name}); assert r.status_code==200,r.text; return r.json()["accessToken"]
def test_detail_roles_pagination_and_sensitive_fields():
    add("asm-detail","asm-detail","ASSEMBLER"); add("asm-other","asm-other","ASSEMBLER"); add("sup-detail","sup-detail","WORKSHOP_SUPERVISOR")
    c=backend.db(); now=backend.now()
    c.execute("INSERT INTO production_orders VALUES(?,?,?,?,?,?)",("ord-detail","OD-1","产品","IN_PROGRESS",now,now))
    c.execute("INSERT INTO order_devices VALUES(?,?,?,?,?,?)",("dev-detail","ord-detail","机台","OD-1-D1",1,now))
    c.execute("INSERT INTO assembly_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",("task-detail","OD-1","dev-detail","OD-1-D1","asm-detail","COMPLETED",3,5,None,None,now,now))
    c.commit(); c.close()
    with TestClient(backend.app) as client:
        admin=login(client,"owlco","Admin@2026"); asm=login(client,"asm-detail"); other=login(client,"asm-other"); sup=login(client,"sup-detail")
        for token in (admin,sup,asm):
            r=client.get("/api/v1/orders/OD-1/detail?page=1&pageSize=1",headers={"Authorization":f"Bearer {token}"}); assert r.status_code==200,r.text
            body=r.json(); assert set(body)=={"orderId","orderNo","productName","orderStatus","materials","materialSummary","assemblyTasks","laborSummary","timeline","page","pageSize","total"}; assert body["total"]==1
            assert "source_ip" not in r.text and "before_json" not in r.text and "password_hash" not in r.text
        denied=client.get("/api/v1/orders/OD-1/detail",headers={"Authorization":f"Bearer {other}"}); assert denied.status_code==403
        missing=client.get("/api/v1/orders/NOPE/detail",headers={"Authorization":f"Bearer {admin}"}); assert missing.status_code==404
