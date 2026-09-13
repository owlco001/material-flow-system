"""角色化工作台最小后端垂直切片契约测试。"""

import os
import sys
import tempfile
import uuid
import importlib.util

TEST_ROOT = tempfile.mkdtemp(prefix="mf_workspace_contract_")
os.environ["MATERIAL_FLOW_DATA"] = f"{TEST_ROOT}/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = f"{TEST_ROOT}/uploads"
os.environ["INITIAL_ADMIN_PASSWORD"] = "WorkspaceAdmin@2026"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "app"))

from fastapi.testclient import TestClient  # noqa: E402

# Do not reuse the ordinary ``main`` module name: another contract test loads
# the same application with a separate temporary database at collection time.
_spec = importlib.util.spec_from_file_location(
    "workspace_contract_backend", os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "main.py")
)
assert _spec is not None
backend = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = backend
_spec.loader.exec_module(backend)


ADMIN_PASSWORD = "WorkspaceAdmin@2026"
PASSWORD = "WorkspaceUser@2026"
DEMO_ORDER = "26B-013"
REQUIRED_ITEM_FIELDS = {
    "id", "requirementId", "orderNo", "productName", "orderStatus",
    "deviceId", "deviceType", "deviceNo", "materialId", "materialCode",
    "materialName", "specification", "unit", "requiredQuantity",
    "arrivedQuantity", "inStockQuantity", "issuedQuantity", "pickedQuantity",
    "statusCode", "statusLabel", "label", "colorToken", "statusDomain",
    "updatedAt", "assignedUserId", "assignedUserName", "currentOwnerUserId",
    "currentOwnerName", "responsibilitySummary", "lastHandoverId", "lastHandoverStatus",
    "lastHandover", "handoverSummary",
    "transferRequestId", "transferStatus",
}


def _headers(token: str, request_id: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Request-Id": request_id or str(uuid.uuid4()),
    }


def _add_user(user_id: str, username: str, role: str, display_name: str) -> None:
    c = backend.db()
    c.execute(
        "INSERT INTO users(id, username, display_name, role, password_hash, "
        "must_change_password, active, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (user_id, username, display_name, role, backend.hash_password(PASSWORD), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def _login(client: TestClient, username: str) -> str:
    password = ADMIN_PASSWORD if username == "owlco" else PASSWORD
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "deviceId": f"device-{username}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def _insert_workspace_facts() -> tuple[str, str, str]:
    """插入责任人、正式出库和正式交接事实，不写入第二套数量字段。"""
    requirement_a = "omr_demo_dev_demo_HZ01_mat_ctl_cabinet"
    requirement_b = "omr_demo_dev_demo_HZ02_mat_ctl_cabinet"
    requirement_material = "omr_demo_dev_demo_HZ03_mat_ctl_cabinet"
    c = backend.db()
    c.executemany(
        "INSERT INTO material_work_items(id, requirement_id, material_id, device_id, "
        "assigned_user_id, quantity) VALUES(?,?,?,?,?,?)",
        [
            ("wi_operator_a", requirement_a, "mat_ctl_cabinet", "dev_demo_HZ01", "u_operator_a", 99),
            ("wi_operator_b", requirement_b, "mat_ctl_cabinet", "dev_demo_HZ02", "u_operator_b", 88),
            ("wi_material", requirement_material, "mat_ctl_cabinet", "dev_demo_HZ03", "u_material", 77),
        ],
    )

    transfer_id = "tr_workspace_outbound"
    transfer_operation = str(uuid.uuid4())
    c.execute(
        "INSERT INTO transfer_requests(id, client_operation_id, type, document_no, status, "
        "payload_json, created_by, created_at, approved_by, approved_at, executed_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            transfer_id, transfer_operation, "OUTBOUND", DEMO_ORDER, "EXECUTED",
            '{"items":[{"materialId":"mat_ctl_cabinet","quantity":1,"expectedInventoryVersion":1}]}',
            "u_material", backend.now(), "u_warehouse", backend.now(), backend.now(),
        ),
    )
    # documentNo 可为空；摘要仍必须从正式流转申请事实计数，不能只从需求行反推。
    c.execute(
        "INSERT INTO transfer_requests(id, client_operation_id, type, document_no, status, "
        "payload_json, created_by, created_at, approved_by, approved_at, executed_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "tr_workspace_unlinked_pending", str(uuid.uuid4()), "OUTBOUND", None,
            "PENDING_APPROVAL",
            '{"items":[{"materialId":"mat_ctl_cabinet","quantity":3,"expectedInventoryVersion":1}]}',
            "u_material", backend.now(), None, None, None,
        ),
    )

    handover_time = backend.now()
    c.executemany(
        "INSERT INTO material_handovers(id, work_item_id, transfer_request_id, quantity, "
        "from_location, device_id, receiver_user_id, remark, client_operation_id, status, "
        "created_by, created_at, confirmed_by, confirmed_at, decision_reason) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "ho_workspace_confirmed", "wi_operator_a", transfer_id, 1, "A-01-03",
                "dev_demo_HZ01", "u_operator_a", "已送达机台", str(uuid.uuid4()),
                "CONFIRMED", "u_material", handover_time, "u_operator_a", handover_time, None,
            ),
            (
                "ho_workspace_pending", "wi_operator_b", transfer_id, 2, "A-01-03",
                "dev_demo_HZ02", "u_operator_b", "待领取", str(uuid.uuid4()),
                "PENDING", "u_material", backend.now(), None, None, None,
            ),
        ],
    )
    c.commit()
    c.close()
    return requirement_a, requirement_b, requirement_material


def test_workspace_role_scope_status_and_pagination():
    with TestClient(backend.app) as client:
        _add_user("u_operator_a", "operator_a", "OPERATOR", "操作员甲")
        _add_user("u_operator_b", "operator_b", "OPERATOR", "操作员乙")
        _add_user("u_material", "material_a", "MATERIAL", "物料员甲")
        _add_user("u_warehouse", "warehouse_a", "WAREHOUSE_ADMIN", "仓库管理员甲")
        _add_user("u_empty", "operator_empty", "OPERATOR", "空范围操作员")
        _add_user("u_unknown", "unknown_role", "UNKNOWN_ROLE", "未知角色")
        requirement_a, requirement_b, _ = _insert_workspace_facts()

        admin_token = _login(client, "owlco")
        warehouse_token = _login(client, "warehouse_a")
        operator_a_token = _login(client, "operator_a")
        operator_b_token = _login(client, "operator_b")
        material_token = _login(client, "material_a")
        empty_token = _login(client, "operator_empty")
        unknown_login = client.post(
            "/api/v1/auth/login",
            json={"username": "unknown_role", "password": PASSWORD, "deviceId": "unknown"},
        )
        assert unknown_login.status_code == 200
        unknown_token = unknown_login.json()["accessToken"]

        # 未认证与未知角色均不能得到工作台数据。
        assert client.get("/api/v1/workspace/summary").status_code == 401
        assert client.get("/api/v1/workspace/material-items").status_code == 401
        unknown_response = client.get("/api/v1/workspace/summary", headers=_headers(unknown_token))
        assert unknown_response.status_code == 403
        unknown_items = client.get(
            "/api/v1/workspace/material-items", headers=_headers(unknown_token)
        )
        assert unknown_items.status_code == 403

        # 管理员与仓库管理员看到订单需求全量，但数量仍来自正式需求表。
        for token in (admin_token, warehouse_token):
            response = client.get(
                "/api/v1/workspace/material-items?orderNo=26B-013&page=1&pageSize=50",
                headers=_headers(token),
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["page"] == 1
            assert body["pageSize"] == 50
            assert body["total"] == 75
            assert body["totalPages"] == 2
            assert len(body["items"]) == 50
            assert {item["orderNo"] for item in body["items"]} == {DEMO_ORDER}

        # 操作员只能看到服务端登记的本人责任范围，不能看见另一个操作员的需求。
        operator_a = client.get(
            "/api/v1/workspace/material-items", headers=_headers(operator_a_token)
        )
        assert operator_a.status_code == 200
        operator_a_body = operator_a.json()
        assert operator_a_body["total"] == 1
        assert operator_a_body["items"][0]["id"] == "wi_operator_a"
        assert operator_a_body["items"][0]["requirementId"] == requirement_a

        operator_b = client.get(
            "/api/v1/workspace/material-items", headers=_headers(operator_b_token)
        )
        assert operator_b.status_code == 200
        assert operator_b.json()["total"] == 1
        assert operator_b.json()["items"][0]["id"] == "wi_operator_b"
        assert operator_b.json()["items"][0]["requirementId"] == requirement_b

        # 物料员只能看到本人责任/本人创建的出库交接范围；本测试的正式出库单
        # 没有跨订单能力，因此不应出现演示订单之外的数据。
        material_items = client.get(
            "/api/v1/workspace/material-items", headers=_headers(material_token)
        )
        assert material_items.status_code == 200
        material_body = material_items.json()
        assert material_body["total"] == 25
        assert {item["orderNo"] for item in material_body["items"]} == {DEMO_ORDER}

        # 四种服务端角色都必须返回自己的角色摘要；汇总不能由客户端
        # 用全局数据自行拼接。
        for token, expected_role in (
            (admin_token, "ADMIN"),
            (warehouse_token, "WAREHOUSE_ADMIN"),
            (material_token, "MATERIAL"),
            (operator_a_token, "OPERATOR"),
        ):
            role_summary = client.get(
                "/api/v1/workspace/summary", headers=_headers(token)
            )
            assert role_summary.status_code == 200
            summary_body = role_summary.json()
            assert summary_body["role"] == expected_role
            assert {
                "pendingApprovalCount", "pendingOutboundCount", "pendingHandoverCount",
                "atStationCount", "pickedUpCount", "outOfStockCount", "generatedAt",
            } <= set(summary_body)

        # 空责任范围仍返回稳定的空分页结构。
        empty_summary = client.get(
            "/api/v1/workspace/summary", headers=_headers(empty_token)
        )
        assert empty_summary.status_code == 200
        assert empty_summary.json()["role"] == "OPERATOR"
        assert all(empty_summary.json()[key] == 0 for key in (
            "pendingApprovalCount", "pendingOutboundCount", "pendingHandoverCount",
            "atStationCount", "pickedUpCount", "outOfStockCount",
        ))
        empty_items = client.get(
            "/api/v1/workspace/material-items?orderNo=NO-SUCH-ORDER",
            headers=_headers(empty_token),
        )
        assert empty_items.status_code == 200
        assert empty_items.json()["items"] == []
        assert empty_items.json()["total"] == 0
        assert empty_items.json()["totalPages"] == 0

        # 分页、过滤和参数边界。
        page_2 = client.get(
            "/api/v1/workspace/material-items?page=2&pageSize=20",
            headers=_headers(admin_token),
        )
        assert page_2.status_code == 200
        assert page_2.json()["total"] == 75
        assert len(page_2.json()["items"]) == 20
        page_5 = client.get(
            "/api/v1/workspace/material-items?page=5&pageSize=20",
            headers=_headers(admin_token),
        )
        assert page_5.status_code == 200
        assert page_5.json()["items"] == []
        assert page_5.json()["total"] == 75
        assert page_5.json()["totalPages"] == 4
        assert client.get(
            "/api/v1/workspace/material-items?pageSize=51", headers=_headers(admin_token)
        ).status_code == 422
        assert client.get(
            "/api/v1/workspace/material-items?page=0", headers=_headers(admin_token)
        ).status_code == 422
        bad_status = client.get(
            "/api/v1/workspace/material-items?status=NOT_A_STATUS",
            headers=_headers(admin_token),
        )
        assert bad_status.status_code == 400
        assert bad_status.json()["error"]["code"] == "VALIDATION_ERROR"

        # 工作项字段契约与服务端交接摘要。
        confirmed = client.get(
            "/api/v1/workspace/material-items?status=AT_STATION",
            headers=_headers(operator_a_token),
        )
        assert confirmed.status_code == 200
        item = confirmed.json()["items"][0]
        assert REQUIRED_ITEM_FIELDS <= set(item)
        assert item["statusCode"] == "AT_STATION"
        assert item["statusLabel"] == "已到机台"
        assert item["label"] == "已到机台"
        assert item["colorToken"] == "status-green"
        assert item["statusDomain"] == "WORKSPACE"
        assert item["requiredQuantity"] == 20
        assert item["arrivedQuantity"] == 20
        # 工作台交接/出库投影不能覆盖订单需求事实；HZ01 的正式需求仍是 ARRIVED。
        assert item["inStockQuantity"] == 0
        assert item["issuedQuantity"] == 1
        assert item["pickedQuantity"] == 1
        assert item["currentOwnerUserId"] == "u_operator_a"
        assert item["currentOwnerName"] == "操作员甲"
        assert item["responsibilitySummary"] == {
            "assignedUserId": "u_operator_a",
            "assignedUserName": "操作员甲",
            "currentOwnerUserId": "u_operator_a",
            "currentOwnerName": "操作员甲",
        }
        assert item["lastHandoverId"] == "ho_workspace_confirmed"
        assert item["lastHandover"]["transferRequestId"] == "tr_workspace_outbound"
        assert item["lastHandover"]["senderUserId"] == "u_material"
        assert item["lastHandover"]["receiverUserId"] == "u_operator_a"
        assert item["handoverSummary"]["lastHandoverId"] == "ho_workspace_confirmed"
        assert item["handoverSummary"]["lastStatus"] == "CONFIRMED"
        assert item["handoverSummary"]["count"] == 1

        summary = client.get(
            "/api/v1/workspace/summary",
            headers=_headers(operator_a_token, "summary-request-id"),
        )
        assert summary.status_code == 200
        assert summary.json()["role"] == "OPERATOR"
        assert summary.json()["atStationCount"] == 1
        assert summary.json()["traceId"] == "summary-request-id"

        admin_summary = client.get(
            "/api/v1/workspace/summary", headers=_headers(admin_token)
        )
        assert admin_summary.status_code == 200
        assert admin_summary.json()["role"] == "ADMIN"
        assert admin_summary.json()["pendingHandoverCount"] == 1
        assert admin_summary.json()["pendingApprovalCount"] == 1
        assert admin_summary.json()["pendingOutboundCount"] == 1

        material_summary = client.get(
            "/api/v1/workspace/summary", headers=_headers(material_token)
        )
        assert material_summary.status_code == 200
        assert material_summary.json()["role"] == "MATERIAL"
        assert material_summary.json()["pendingApprovalCount"] == 1
        assert material_summary.json()["pendingOutboundCount"] == 1
