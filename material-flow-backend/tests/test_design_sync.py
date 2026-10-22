"""设计同步 API（/api/v1/design-sync）与管理台「设计包」页。

对应 SMART-FACTORY-INTEGRATION 验收清单 + 本轮修订（内网网段、整数 qty、ISO createdAt、BOM 仅归档）。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import zipfile

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main as backend  # 先导入 main（它在末尾注册 design_sync 路由），避免循环导入
from app import design_sync as ds
from tests.test_admin_web_nav import seed, web_login


# ----------------------------------------------------------------- helpers

class _Peer:
    """把 ASGI scope 的 client 改成指定来源地址（TestClient 默认是 'testclient'）。"""

    def __init__(self, app, host: str):
        self.app, self.host = app, host

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope, client=(self.host, 50000))
        await self.app(scope, receive, send)


def client_from(host: str = "192.168.1.20") -> TestClient:
    return TestClient(_Peer(backend.app, host))


def _pdf(pages: int = 1) -> bytes:
    imgs = [Image.new("RGB", (842, 595), (255, 255, 255 - i * 40)) for i in range(pages)]
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


GLB = b"glTF" + (2).to_bytes(4, "little") + (64).to_bytes(4, "little") + b"\0" * 52


def _seed():
    seed()
    c = backend.db()
    ts = backend.now()
    for did, no in (("dev_ds1", "JT-001"), ("dev_ds2", "JT-002")):
        c.execute("INSERT OR REPLACE INTO devices(id, device_no, device_name, workshop, status, created_by, created_at, updated_at)"
                  " VALUES(?,?,?,?,?,?,?,?)", (did, no, "一号机", "总装", "ACTIVE", "u_admin", ts, ts))
    c.execute("INSERT OR REPLACE INTO materials(id, code, name, unit) VALUES('m_ds1','MAT-001','轴承','个')")
    c.commit()
    c.close()


def make_key(*, device_scope: str = "", ip_scope=ds.DEFAULT_IP_SCOPE, revoked: bool = False) -> tuple[str, str]:
    prefix, plain = ds.generate_key()
    key_id = "dk_" + os.urandom(12).hex()
    c = backend.db()
    c.execute("INSERT INTO design_keys(id, key_prefix, key_hash, label, device_scope, ip_scope, created_by, created_at, revoked_at)"
              " VALUES(?,?,?,?,?,?,?,?,?)", (key_id, prefix, backend.hash_password(plain), "测试", device_scope,
                                             ",".join(ip_scope), "u_admin", backend.now(), backend.now() if revoked else None))
    c.commit()
    c.close()
    return key_id, plain


def build(files: dict[str, bytes], *, bom=None, kinds=None, project="asm-slide-cyl", extra=None,
          manifest_patch=None) -> tuple[bytes, dict]:
    kinds = kinds or {}

    def kind_of(p):
        if p in kinds:
            return kinds[p]
        ext = p.rsplit(".", 1)[-1].lower()
        return {"csv": "BOM", "pdf": "PDF", "glb": "GLTF", "dxf": "DXF", "step": "STEP", "stp": "STEP"}[ext]

    manifest = {
        "schemaVersion": 1, "projectId": project, "projectName": "滑台气缸装配体", "appVersion": "0.2.0",
        "createdAt": "2026-10-22T14:00:00Z", "bomSha256": hashlib.sha256(files.get("bom.csv", b"")).hexdigest(),
        "files": [{"path": p, "sha256": hashlib.sha256(b).hexdigest(), "size": len(b), "kind": kind_of(p)}
                  for p, b in files.items()],
        "bom": bom if bom is not None else [{"no": 1, "name": "底板", "spec": "BASE-01", "qty": 1, "mat": "AL6061"}],
    }
    if manifest_patch:
        manifest_patch(manifest)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for p, b in files.items():
            zf.writestr(p, b)
        for info, data in (extra or []):
            zf.writestr(info, data)
    return buf.getvalue(), manifest


def upload(client, key, data: bytes, manifest, device="JT-001", idem=None, headers=None):
    h = {"X-Design-Key": key, "Idempotency-Key": idem or hashlib.sha256(data).hexdigest(), **(headers or {})}
    return client.post("/api/v1/design-sync/packages", headers=h,
                       data={"deviceId": device, "manifest": manifest if isinstance(manifest, str) else json.dumps(manifest, ensure_ascii=False)},
                       files={"file": ("package.zip", io.BytesIO(data), "application/zip")})


def _rows(sql, *args):
    c = backend.db()
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def _storage_empty() -> bool:
    root = ds.storage_root()
    return not root.exists() or not any(root.iterdir())


def _token(client):
    r = client.post("/api/v1/auth/login", json={"username": "owlco", "password": "Admin@2026", "deviceId": "owlco"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


def _basic(**kw):
    files = {"bom.csv": "no,name\n1,底板\n".encode(), "report.pdf": _pdf(2)}
    files.update(kw.pop("files", {}))
    return build(files, **kw)


# ---------------------------------------------------------------- auth / ip

def test_wrong_and_revoked_key_401():
    _seed()
    _, revoked = make_key(revoked=True)
    data, m = _basic()
    with client_from() as client:
        for key in ("", "nope", ds.KEY_PREFIX + "deadbeef_" + "x" * 43, revoked):
            r = upload(client, key, data, m)
            assert r.status_code == 401, (key, r.text)
            assert r.json()["error"]["code"] == "DESIGN_KEY_INVALID"
        r = client.get("/api/v1/design-sync/packages", headers={"X-Design-Key": revoked})
        assert r.status_code == 401
    assert _rows("SELECT COUNT(*) n FROM design_packages")[0]["n"] == 0


def test_non_intranet_ip_403_and_scope_tightening():
    _seed()
    key_id, key = make_key()
    data, m = _basic()
    with client_from("8.8.8.8") as client:
        r = upload(client, key, data, m)
        assert r.status_code == 403 and r.json()["error"]["code"] == "DESIGN_KEY_IP_FORBIDDEN"
    # 经 Cloudflare 隧道进来：对端是本机反代（127.0.0.1），但 Cf-Connecting-Ip 是公网地址 → 拒绝
    with client_from("127.0.0.1") as client:
        r = client.get("/api/v1/design-sync/packages", headers={"X-Design-Key": key, "Cf-Connecting-Ip": "1.2.3.4"})
        assert r.status_code == 403
        assert client.get("/api/v1/design-sync/packages", headers={"X-Design-Key": key}).status_code == 200
    # IPv6 回环 / IPv4-mapped 内网地址在默认网段内
    with client_from("::ffff:10.1.2.3") as client:
        assert client.get("/api/v1/design-sync/packages", headers={"X-Design-Key": key}).status_code == 200
    # 收紧到单个网段后，其他内网地址也被拒
    _, narrow = make_key(ip_scope=("192.168.5.0/24",))
    with client_from("10.0.0.5") as client:
        assert client.get("/api/v1/design-sync/packages", headers={"X-Design-Key": narrow}).status_code == 403
    with client_from("192.168.5.9") as client:
        assert client.get("/api/v1/design-sync/packages", headers={"X-Design-Key": narrow}).status_code == 200
    denied = _rows("SELECT * FROM audit_logs WHERE action='DESIGN_SYNC_IP_DENIED'")
    assert denied and denied[0]["operator_id"] == f"design-key:{key_id}"


# ------------------------------------------------------------------ upload

def test_upload_duplicate_and_version_increment():
    _seed()
    key_id, key = make_key()
    data, m = _basic()
    with client_from() as client:
        r = upload(client, key, data, m)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["duplicated"] is False and body["version"] == 1 and body["deviceNo"] == "JT-001"
        pid = body["packageId"]
        r2 = upload(client, key, data, m, device="dev_ds1")  # 也接受 devices.id
        assert r2.status_code == 200 and r2.json()["duplicated"] is True and r2.json()["packageId"] == pid
        assert _rows("SELECT COUNT(*) n FROM design_packages")[0]["n"] == 1
        # 同一份 zip 推给另一台机台 → 409，提示用 bind
        assert upload(client, key, data, m, device="JT-002").status_code == 409

        data2, m2 = _basic(bom=[{"no": 1, "name": "底板", "spec": "BASE-02", "qty": 2}])
        r3 = upload(client, key, data2, m2)
        assert r3.status_code == 201 and r3.json()["version"] == 2
        # 不同项目独立计版本
        data3, m3 = _basic(project="other-proj")
        assert upload(client, key, data3, m3).json()["version"] == 1

        # 存储：原 zip + 解压文件
        d = ds.storage_root() / pid
        assert (d / "package.zip").read_bytes() == data
        assert (d / "files" / "report.pdf").is_file()

        listing = client.get("/api/v1/design-sync/packages", params={"deviceId": "JT-001", "projectId": "asm-slide-cyl"},
                             headers={"X-Design-Key": key}).json()["items"]
        assert [i["version"] for i in listing] == [2, 1]
        assert listing[0]["createdAt"] == "2026-10-22T14:00:00Z"
        detail = client.get(f"/api/v1/design-sync/packages/{pid}", headers={"X-Design-Key": key}).json()
        assert {f["path"] for f in detail["files"]} == {"bom.csv", "report.pdf"} and detail["bom"][0]["qty"] == 1
        dl = client.get(f"/api/v1/design-sync/packages/{pid}/download", headers={"X-Design-Key": key})
        assert dl.status_code == 200 and dl.content == data
        assert client.get("/api/v1/design-sync/packages/dsp_" + "0" * 32, headers={"X-Design-Key": key}).status_code == 404

    # BOM 只归档：不写 bom_versions / materials
    assert _rows("SELECT COUNT(*) n FROM bom_versions")[0]["n"] == 0
    assert _rows("SELECT COUNT(*) n FROM materials WHERE name='底板'")[0]["n"] == 0
    logs = _rows("SELECT * FROM audit_logs WHERE action='DESIGN_PACKAGE_UPLOAD'")
    assert len(logs) == 3 and logs[0]["operator_id"] == f"design-key:{key_id}"
    ev = _rows("SELECT * FROM audit_events WHERE event_type='DESIGN_PACKAGE_UPLOADED' AND entity_id=?", pid)
    assert ev[0]["actor_user_id"] == f"design-key:{key_id}" and ev[0]["source_ip"] == "192.168.1.20"
    assert ev[0]["device_id"] == "dev_ds1" and ev[0]["client_operation_id"] == hashlib.sha256(data).hexdigest()


def test_idempotency_key_must_match_zip_sha():
    _seed()
    _, key = make_key()
    data, m = _basic()
    with client_from() as client:
        assert upload(client, key, data, m, idem="abc").status_code == 400
        r = upload(client, key, data, m, idem="0" * 64)
        assert r.status_code == 400 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_MISMATCH"
    assert _storage_empty()


def test_unknown_device_404_and_device_scope_403():
    _seed()
    _, key = make_key()
    _, scoped = make_key(device_scope="dev_ds2")
    data, m = _basic()
    with client_from() as client:
        r = upload(client, key, data, m, device="NOPE-1")
        assert r.status_code == 404 and r.json()["error"]["code"] == "DEVICE_NOT_FOUND"
        r = upload(client, scoped, data, m, device="JT-001")
        assert r.status_code == 403 and r.json()["error"]["code"] == "DESIGN_KEY_DEVICE_FORBIDDEN"
        assert upload(client, scoped, data, m, device="JT-002").status_code == 201
        # 范围外机台的包对受限密钥不可见
        pid = upload(client, key, *_basic(project="p2")).json()["packageId"]
        assert client.get(f"/api/v1/design-sync/packages/{pid}", headers={"X-Design-Key": scoped}).status_code == 404
        items = client.get("/api/v1/design-sync/packages", headers={"X-Design-Key": scoped}).json()["items"]
        assert {i["deviceNo"] for i in items} == {"JT-002"}


@pytest.mark.parametrize("case", ["manifest_dotdot", "zip_dotdot", "absolute", "backslash", "symlink"])
def test_unsafe_paths_rejected_without_disk_writes(case, tmp_path):
    _seed()
    _, key = make_key()
    evil = b"pwned"
    if case == "manifest_dotdot":
        data, m = build({"bom.csv": b"x", "../evil": evil}, kinds={"../evil": "PDF"})
    elif case == "zip_dotdot":
        data, m = build({"bom.csv": b"x"}, extra=[("../evil", evil)])
    elif case == "absolute":
        data, m = build({"bom.csv": b"x"}, extra=[("/tmp/evil", evil)])
    elif case == "backslash":
        data, m = build({"bom.csv": b"x"}, extra=[("..\\evil", evil)])
    else:
        info = zipfile.ZipInfo("link.pdf")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        data, m = build({"bom.csv": b"x"}, extra=[(info, "/etc/passwd")])
    with client_from() as client:
        r = upload(client, key, data, m)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] in ("DESIGN_PACKAGE_UNSAFE_PATH",)
    assert _storage_empty()
    assert not [p for p in tmp_path.rglob("evil")]
    assert _rows("SELECT COUNT(*) n FROM design_packages")[0]["n"] == 0


def test_zip_over_limit_413(monkeypatch):
    _seed()
    _, key = make_key()
    data, m = _basic(files={"big.step": os.urandom(8192)})
    monkeypatch.setattr(ds, "ZIP_MAX_BYTES", 4096)
    with client_from() as client:
        r = upload(client, key, data, m)
    assert r.status_code == 413 and r.json()["error"]["code"] == "DESIGN_PACKAGE_TOO_LARGE"
    assert _storage_empty()


def test_manifest_over_limit_413(monkeypatch):
    _seed()
    _, key = make_key()
    data, m = _basic()
    monkeypatch.setattr(ds, "MANIFEST_MAX_BYTES", 200)
    with client_from() as client:
        assert upload(client, key, data, m).status_code == 413


def test_uncompressed_overflow_400(monkeypatch):
    _seed()
    _, key = make_key()
    data, m = build({"bom.csv": b"x", "zeros.step": b"\0" * 50000})
    assert len(data) < 5000  # 高压缩比
    monkeypatch.setattr(ds, "MAX_UNCOMPRESSED_BYTES", 20000)
    with client_from() as client:
        r = upload(client, key, data, m)
    assert r.status_code == 400 and "解压总量" in r.json()["error"]["message"]
    assert _storage_empty()


def test_too_many_entries_400(monkeypatch):
    _seed()
    _, key = make_key()
    data, m = build({f"f{i}.step": b"s" for i in range(6)})
    monkeypatch.setattr(ds, "MAX_ENTRIES", 5)
    with client_from() as client:
        assert upload(client, key, data, m).status_code == 400


@pytest.mark.parametrize("qty", [1.5, "2", 1.0, True, 0])
def test_non_integer_qty_400(qty):
    _seed()
    _, key = make_key()
    data, m = _basic(bom=[{"no": 1, "name": "底板", "spec": "B", "qty": qty}])
    with client_from() as client:
        r = upload(client, key, data, m)
    assert r.status_code == 400 and r.json()["error"]["code"] == "DESIGN_MANIFEST_INVALID"


@pytest.mark.parametrize("created", ["2026-10-22 14:00:00", "2026-10-22T14:00:00", "昨天", 1729605600])
def test_created_at_must_be_iso8601_with_zone(created):
    _seed()
    _, key = make_key()
    data, m = _basic(manifest_patch=lambda mm: mm.update(createdAt=created))
    with client_from() as client:
        assert upload(client, key, data, m).status_code == 400
    ok, m2 = _basic(manifest_patch=lambda mm: mm.update(createdAt="2026-10-22T22:00:00+08:00"))
    with client_from() as client:
        assert upload(client, key, ok, m2).status_code == 201


def test_sha_and_size_mismatch_400():
    _seed()
    _, key = make_key()

    def bad_sha(mm):
        mm["files"][1]["sha256"] = "f" * 64

    def bad_size(mm):
        mm["files"][0]["size"] += 1

    def missing(mm):
        mm["files"].append({"path": "ghost.pdf", "sha256": "a" * 64, "size": 1, "kind": "PDF"})

    with client_from() as client:
        for patch in (bad_sha, bad_size, missing):
            data, m = _basic(manifest_patch=patch)
            r = upload(client, key, data, m)
            assert r.status_code == 400 and r.json()["error"]["code"] == "DESIGN_PACKAGE_FILE_MISMATCH", r.text
        r = upload(client, key, b"not a zip", _basic()[1])
        assert r.status_code == 400 and r.json()["error"]["code"] == "DESIGN_PACKAGE_INVALID"
        assert upload(client, key, _basic()[0], "{bad json").status_code == 400
    assert _storage_empty()


# -------------------------------------------------------------------- diff

def test_diff_add_remove_change():
    _seed()
    _, key = make_key()
    bom1 = [{"no": 1, "name": "底板", "spec": "B1", "qty": 1, "mat": "AL"},
            {"no": 2, "name": "气缸", "spec": "C1", "qty": 2, "mat": "ST"},
            {"no": 3, "name": "螺钉", "spec": "M4", "qty": 8, "mat": "ST"}]
    bom2 = [{"no": 1, "name": "底板", "spec": "B1", "qty": 1, "mat": "AL"},
            {"no": 2, "name": "气缸", "spec": "C1", "qty": 5, "mat": "ST"},
            {"no": 4, "name": "导轨", "spec": "R1", "qty": 1, "mat": "ST"}]
    d1, m1 = build({"bom.csv": b"v1", "report.pdf": _pdf(1)}, bom=bom1)
    d2, m2 = build({"bom.csv": b"v2", "drawing.dxf": b"0\nSECTION\n"}, bom=bom2)
    with client_from() as client:
        p1 = upload(client, key, d1, m1).json()["packageId"]
        p2 = upload(client, key, d2, m2).json()["packageId"]
        h = {"X-Design-Key": key}
        diff = client.get(f"/api/v1/design-sync/packages/{p2}/diff", params={"against": "latest"}, headers=h).json()
        assert diff["base"] == {"packageId": p1, "version": 1}
        assert [r["no"] for r in diff["bomAdded"]] == [4]
        assert [r["no"] for r in diff["bomRemoved"]] == [3]
        assert diff["bomChanged"] == [{"no": 2, "field": "qty", "from": 2, "to": 5}]
        assert [f["path"] for f in diff["filesAdded"]] == ["drawing.dxf"]
        assert [f["path"] for f in diff["filesRemoved"]] == ["report.pdf"]
        assert [f["path"] for f in diff["filesChanged"]] == ["bom.csv"]
        assert diff["filesChanged"][0]["fromSha256"] == hashlib.sha256(b"v1").hexdigest()
        by_id = client.get(f"/api/v1/design-sync/packages/{p2}/diff", params={"against": p1}, headers=h).json()
        assert by_id["bomChanged"] == diff["bomChanged"]
        # v1 对 latest（v2）方向相反
        rev = client.get(f"/api/v1/design-sync/packages/{p1}/diff", headers=h).json()
        assert [r["no"] for r in rev["bomAdded"]] == [3] and [r["no"] for r in rev["bomRemoved"]] == [4]
        first = client.get(f"/api/v1/design-sync/packages/{p1}/diff", params={"against": "previous"}, headers=h).json()
        assert first["base"] is None and len(first["bomAdded"]) == 3


# -------------------------------------------------------------------- bind

def test_bind_device_registers_drawings_and_model_idempotently(monkeypatch):
    monkeypatch.delenv("DRAWING_CAD_TO_PDF", raising=False)
    _seed()
    key_id, key = make_key()
    data, m = build({"bom.csv": b"b", "report.pdf": _pdf(2), "model.glb": GLB, "part.step": b"ISO-10303-21;",
                     "drawing.dxf": b"0\nSECTION\n"})
    with client_from() as client:
        pid = upload(client, key, data, m).json()["packageId"]
        h = {"X-Design-Key": key}
        r = client.post("/api/v1/design-sync/bind", json={"packageId": pid, "targetType": "DEVICE", "targetKey": "JT-001"}, headers=h)
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["bound"] is True and res["alreadyBound"] is False and res["targetKey"] == "dev_ds1"
        assert {d["path"] for d in res["drawings"]} == {"report.pdf", "drawing.dxf"}
        assert res["model"] == {"modelCode": "DS-JT-001", "version": 1}
        assert any("drawing.dxf" in w for w in res["warnings"])  # 未配置 CAD 转换器

        again = client.post("/api/v1/design-sync/bind", json={"packageId": pid, "targetType": "DEVICE", "targetKey": "dev_ds1"}, headers=h).json()
        assert again["alreadyBound"] is True and again["drawings"] == res["drawings"]
        assert _rows("SELECT COUNT(*) n FROM drawings")[0]["n"] == 2
        assert _rows("SELECT COUNT(*) n FROM assembly_model_versions")[0]["n"] == 1

        # 物料绑定复用同一张图纸，不重复入库；GLB 不参与物料绑定
        mat = client.post("/api/v1/design-sync/bind", json={"packageId": pid, "targetType": "MATERIAL", "targetKey": "MAT-001"}, headers=h).json()
        assert mat["model"] is None and {d["drawingId"] for d in mat["drawings"]} == {d["drawingId"] for d in res["drawings"]}
        assert _rows("SELECT COUNT(*) n FROM drawings")[0]["n"] == 2

        assert client.post("/api/v1/design-sync/bind", json={"packageId": pid, "targetType": "DEVICE", "targetKey": "NOPE"}, headers=h).status_code == 404
        assert client.post("/api/v1/design-sync/bind", json={"packageId": pid, "targetType": "X", "targetKey": "JT-001"}, headers=h).status_code == 400

    with TestClient(backend.app) as client:
        auth = _token(client)
        drawings = client.get("/api/v1/devices/JT-001/drawings", headers=auth).json()["drawings"]
        assert len(drawings) == 1 and drawings[0]["pageCount"] == 2 and drawings[0]["revision"] == "V1"
        assert drawings[0]["drawingNo"] == "asm-slide-cyl"
        page = client.get(drawings[0]["pages"][0]["url"], headers=auth)
        assert page.status_code == 200 and page.content.startswith(b"%PDF")
        mats = client.get("/api/v1/materials/MAT-001/drawings", headers=auth).json()["drawings"]
        assert [x["drawingId"] for x in mats] == [drawings[0]["drawingId"]]
        dev = client.get("/api/v1/devices/JT-001", headers=auth).json()
        assert dev["model3dCode"] == "DS-JT-001"
        pub = client.get("/api/v1/assembly-models/DS-JT-001/published", headers=auth).json()
        assert pub["sha256"] == hashlib.sha256(GLB).hexdigest()
        glb = client.get(pub["downloadPath"], headers=auth)
        assert glb.status_code == 200 and glb.content == GLB

    created = _rows("SELECT created_by FROM drawings")
    assert {r["created_by"] for r in created} == {f"design-key:{key_id}"}
    assert _rows("SELECT COUNT(*) n FROM audit_logs WHERE action='DESIGN_PACKAGE_BIND'")[0]["n"] == 2


# ------------------------------------------------------------------- admin

def _csrf(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def test_admin_key_lifecycle_and_rbac():
    _seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        assert client.get("/admin/design-packages", follow_redirects=False).status_code == 403
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        page = client.get("/admin/design-packages")
        assert page.status_code == 200 and "设计同步密钥" in page.text and 'href="/admin/design-packages"' in page.text
        csrf = _csrf(page.text)
        assert client.post("/admin/design-packages/keys/create", data={"csrf_token": "bad", "label": "x"}).status_code == 403
        r = client.post("/admin/design-packages/keys/create",
                        data={"csrf_token": csrf, "label": "设计部工作站", "device_scope": "JT-001", "ip_scope": ""})
        assert r.status_code == 200 and "只显示这一次" in r.text
        plain = re.search(r'value="(mfds_[0-9a-f]{8}_[^"]+)"', r.text).group(1)
        row = _rows("SELECT * FROM design_keys")[0]
        assert plain not in json.dumps(dict(row)) and row["key_hash"].startswith("$argon2id$")
        assert row["device_scope"] == "dev_ds1" and row["ip_scope"].split(",") == list(
            str(__import__("ipaddress").ip_network(s)) for s in ds.DEFAULT_IP_SCOPE)
        again = client.get("/admin/design-packages")
        assert plain not in again.text and "设计部工作站" in again.text

        bad = client.post(f"/admin/design-packages/keys/{row['id']}/update",
                          data={"csrf_token": csrf, "label": "y", "device_scope": "", "ip_scope": "not-a-cidr"},
                          follow_redirects=False)
        assert "key_bad_scope" in bad.headers["location"]
        ok = client.post(f"/admin/design-packages/keys/{row['id']}/update",
                         data={"csrf_token": csrf, "label": "y", "device_scope": "", "ip_scope": "192.168.1.0/24\n10.8.0.0/16"},
                         follow_redirects=False)
        assert "key_updated" in ok.headers["location"]
        assert _rows("SELECT ip_scope, device_scope FROM design_keys")[0]["ip_scope"] == "192.168.1.0/24,10.8.0.0/16"

    with client_from("10.9.0.1") as api:
        assert api.get("/api/v1/design-sync/packages", headers={"X-Design-Key": plain}).status_code == 403
    with client_from("10.8.3.3") as api:
        assert api.get("/api/v1/design-sync/packages", headers={"X-Design-Key": plain}).status_code == 200

    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        csrf = _csrf(client.get("/admin/design-packages").text)
        r = client.post(f"/admin/design-packages/keys/{row['id']}/revoke", data={"csrf_token": csrf}, follow_redirects=False)
        assert "key_revoked" in r.headers["location"]
    with client_from("10.8.3.3") as api:
        assert api.get("/api/v1/design-sync/packages", headers={"X-Design-Key": plain}).status_code == 401
    actions = {r["action"] for r in _rows("SELECT action FROM audit_logs WHERE resource_type='DESIGN_KEY'")}
    assert {"DESIGN_KEY_CREATE", "DESIGN_KEY_UPDATE", "DESIGN_KEY_REVOKE"} <= actions


def test_admin_list_detail_diff_download_and_bind():
    _seed()
    _, key = make_key()
    d1, m1 = build({"bom.csv": b"v1", "report.pdf": _pdf(1)}, bom=[{"no": 1, "name": "底板", "spec": "B", "qty": 1}])
    d2, m2 = build({"bom.csv": b"v2", "report.pdf": _pdf(1)}, bom=[{"no": 1, "name": "底板", "spec": "B", "qty": 3}])
    with client_from() as api:
        upload(api, key, d1, m1)
        p2 = upload(api, key, d2, m2).json()["packageId"]
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        lst = client.get("/admin/design-packages", params={"device": "JT-001", "project": "asm"})
        assert lst.status_code == 200 and p2 in lst.text and "V2" in lst.text
        assert p2 not in client.get("/admin/design-packages", params={"device": "JT-002"}).text
        detail = client.get(f"/admin/design-packages/{p2}")
        assert detail.status_code == 200 and "V1 → V2" in detail.text and "qty：1 → 3" in detail.text
        dl = client.get(f"/admin/design-packages/{p2}/download")
        assert dl.status_code == 200 and dl.content == d2
        r = client.post(f"/admin/design-packages/{p2}/bind",
                        data={"csrf_token": _csrf(detail.text), "target_type": "DEVICE", "target_key": "JT-002"},
                        follow_redirects=False)
        assert r.status_code == 303 and "notice=bound" in r.headers["location"]
        r = client.post(f"/admin/design-packages/{p2}/bind",
                        data={"csrf_token": _csrf(detail.text), "target_type": "DEVICE", "target_key": "JT-002"},
                        follow_redirects=False)
        assert "already_bound" in r.headers["location"]
        r = client.post(f"/admin/design-packages/{p2}/bind",
                        data={"csrf_token": _csrf(detail.text), "target_type": "DEVICE", "target_key": "NOPE"},
                        follow_redirects=False)
        assert "bind_failed" in r.headers["location"]
        assert "机台 JT-002" in client.get(f"/admin/design-packages/{p2}").text
        auth = _token(client)
        assert len(client.get("/api/v1/devices/JT-002/drawings", headers=auth).json()["drawings"]) == 1
    binds = _rows("SELECT created_by FROM design_package_bindings")
    assert [b["created_by"] for b in binds] == ["u_admin"]


def test_verify_cache_skips_argon2_but_keeps_revocation_immediate(monkeypatch):
    """校验缓存只省 Argon2 计算：同一把钥匙重复请求不打哈希器，吊销后立刻 401，错钥匙永不吃缓存。"""
    _seed()
    ds._verify_cache.clear()
    key_id, plain = make_key()
    calls = {"n": 0}
    real = ds._verify_key_hash

    def counting(stored, raw):
        calls["n"] += 1
        return real(stored, raw)

    monkeypatch.setattr(ds, "_verify_key_hash", counting)
    with client_from() as api:
        for _ in range(3):
            assert api.get("/api/v1/design-sync/packages", headers={"X-Design-Key": plain}).status_code == 200
        assert calls["n"] == 1, f"第二次起应命中缓存，实际调用了 {calls['n']} 次"

        # 另一把钥匙（同进程、缓存已存在）不能被缓存放行
        _, other = make_key()
        assert api.get("/api/v1/design-sync/packages", headers={"X-Design-Key": other}).status_code == 200
        assert api.get("/api/v1/design-sync/packages", headers={"X-Design-Key": plain[:-1] + "x"}).status_code == 401

    c = backend.db()
    c.execute("UPDATE design_keys SET revoked_at=? WHERE id=?", (backend.now(), key_id))
    c.commit()
    c.close()
    with client_from() as api:  # 缓存仍在内存里，但吊销必须即时生效
        assert api.get("/api/v1/design-sync/packages", headers={"X-Design-Key": plain}).status_code == 401


def test_admin_key_update_blank_ip_scope_falls_back_to_default():
    _seed()
    key_id, _ = make_key(ip_scope=["192.168.1.0/24"])
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        csrf = _csrf(client.get("/admin/design-packages").text)
        r = client.post(f"/admin/design-packages/keys/{key_id}/update",
                        data={"csrf_token": csrf, "label": "默认网段", "device_scope": "", "ip_scope": "   "},
                        follow_redirects=False)
        assert "key_updated" in r.headers["location"]
    row = _rows("SELECT ip_scope FROM design_keys WHERE id=?", key_id)[0]
    assert row["ip_scope"].split(",") == [str(__import__("ipaddress").ip_network(s)) for s in ds.DEFAULT_IP_SCOPE]
