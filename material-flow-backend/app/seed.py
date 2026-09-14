"""Opt-in, repeatable test-fixture data for local/staging verification."""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
from typing import Any

from app.main import ASSEMBLY_STATUSES, db, hash_password, init_db, now

SEED_PREFIX = "MF_TEST_SEED_V1"
SEED_USER_PASSWORD_ENV = "TEST_DATA_SEED_PASSWORD"
TASK_STATUSES = ASSEMBLY_STATUSES
SEED_ACTOR = f"{SEED_PREFIX}_ACTOR"

SEED_MATERIALS = (
    ("钢板", "Q235-钢板-001", "Q235钢板", "1200x600x3mm", "张", 120),
    ("铝型材", "AL-型材-002", "工业铝型材", "4040", "米", 240),
    ("伺服电机", "SERVO-电机-003", "伺服电机", "750W-220V", "台", 36),
    ("减速机", "GEAR-减速-004", "精密减速机", "1:15", "台", 24),
    ("接近开关", "SENSOR-接近-005", "接近开关", "M18-PNP", "只", 80),
    ("控制电缆", "CABLE-控制-006", "屏蔽控制电缆", "4芯-1.5mm²", "米", 600),
    ("同步带", "BELT-同步-007", "聚氨酯同步带", "HTD-8M-1200", "条", 48),
    ("紧固件套装", "FASTENER-套装-008", "不锈钢紧固件套装", "M6-M12", "套", 150),
)
SEED_LOCATIONS = (("原料库一号位", "A-01"), ("原料库二号位", "A-02"), ("机台备料区", "B-01"), ("成品暂存区", "C-01"))
SEED_ORDER_PLAN = (
    (1, "MF_TEST_ORDER_01", "输送线装配一单", "已发布"),
    (2, "MF_TEST_ORDER_02", "分拣机装配二单", "生产中"),
    (3, "MF_TEST_ORDER_03", "包装机装配三单", "已完成"),
)


def _enabled() -> bool:
    return os.environ.get("ENABLE_TEST_DATA_SEED", "").strip().lower() == "true"


def _insert_fixture(c: sqlite3.Connection) -> None:
    rnd = random.Random(20260914)
    ts = now()
    user_password = os.environ.get(SEED_USER_PASSWORD_ENV)
    assemblers = [
        (f"{SEED_PREFIX}_ASM_{i}", f"{SEED_PREFIX.lower()}_assembler_{i}", "装配测试工")
        for i in range(1, 4)
    ]
    users = [
        (f"{SEED_PREFIX}_SUP_{i}", f"{SEED_PREFIX.lower()}_supervisor_{i}", "车间测试主管", "WORKSHOP_SUPERVISOR")
        for i in range(1, 3)
    ] + [(uid, username, display_name, "ASSEMBLER") for uid, username, display_name in assemblers]
    if user_password:
        password_hash = hash_password(user_password)
        for uid, username, display_name, role in users:
            c.execute(
                """INSERT OR IGNORE INTO users
                   (id, username, display_name, role, password_hash, must_change_password, active, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (uid, username, display_name, role, password_hash, 1, 1, ts),
            )
    else:
        # Tasks remain useful without creating accounts that could be logged into.
        assemblers = []

    assembler_ids = [row[0] for row in assemblers]
    statuses = ["WAITING_MATERIAL", "MATERIAL_ACCEPTED", "IN_PROGRESS", "COMPLETED"]
    for i in range(1, 5):
        task_id = f"{SEED_PREFIX}_TASK_{i}"
        status = statuses[i - 1]
        stage = {"WAITING_MATERIAL": 0, "MATERIAL_ACCEPTED": 0, "IN_PROGRESS": 2, "COMPLETED": 3}[status]
        c.execute(
            """INSERT OR IGNORE INTO assembly_tasks
               (id, order_no, device_id, device_no, assigned_assembler_id, status,
                progress_stage, task_version, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (task_id, f"{SEED_PREFIX}_ORDER_{i}", f"{SEED_PREFIX}_MACHINE_{i}",
             f"{SEED_PREFIX}-M{i:02d}", assembler_ids[(i - 1) % len(assembler_ids)] if assembler_ids else None,
             status, stage, stage + 1, ts, ts),
        )
        if not assembler_ids:
            continue
        duration = rnd.randint(35, 120)
        labor_status = "COMPLETED" if status == "COMPLETED" else "ACTIVE"
        ended = ts if labor_status == "COMPLETED" else None
        duration_value = duration if labor_status == "COMPLETED" else None
        labor_id = f"{SEED_PREFIX}_LABOR_{i}"
        operation_id = f"{SEED_PREFIX}_OP_{i}"
        worker_id = assembler_ids[(i - 1) % len(assembler_ids)]
        c.execute(
            """INSERT OR IGNORE INTO labor_records
               (id, task_id, worker_user_id, type, status, started_at, ended_at,
                duration_minutes, remark, client_operation_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (labor_id, task_id, worker_id, "ASSEMBLY", labor_status, ts, ended,
             duration_value, f"{SEED_PREFIX} fixture", operation_id, ts),
        )
        if stage >= 1:
            for to_stage in range(1, stage + 1):
                c.execute(
                    """INSERT OR IGNORE INTO progress_events
                       (id, task_id, worker_user_id, from_stage, to_stage, task_version,
                        server_time, client_operation_id)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (f"{SEED_PREFIX}_PROGRESS_{i}_{to_stage}", task_id, worker_id,
                     to_stage - 1, to_stage, to_stage + 1, ts,
                     f"{SEED_PREFIX}_PROGRESS_OP_{i}_{to_stage}"),
                )
        if i <= 2 and assembler_ids:
            transfer_labor_id = f"{SEED_PREFIX}_TRANSFER_LABOR_{i}"
            transfer_id = f"{SEED_PREFIX}_TEMP_TRANSFER_{i}"
            worker_id = assembler_ids[(i - 1) % len(assembler_ids)]
            c.execute("""INSERT OR IGNORE INTO labor_records
               (id, task_id, worker_user_id, type, status, started_at, ended_at, duration_minutes, remark, client_operation_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (transfer_labor_id, None, worker_id, "TEMPORARY_TRANSFER", "COMPLETED", ts, ts, rnd.randint(10, 45),
                 f"{SEED_PREFIX} transfer fixture", f"{SEED_PREFIX}_TRANSFER_OP_{i}", ts))
            c.execute("""INSERT OR IGNORE INTO temporary_transfers
               (id, worker_user_id, source_task_id, labor_record_id, status, remark, started_at, ended_at, client_operation_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
                (transfer_id, worker_id, task_id, transfer_labor_id, "COMPLETED", f"{SEED_PREFIX} transfer fixture", ts, ts,
                 f"{SEED_PREFIX}_TRANSFER_OP_{i}"))

    _insert_operational_fixture(c, ts)


def _insert_operational_fixture(c: sqlite3.Connection, ts: str) -> None:
    """Write the relational order/material/warehouse fixture without accounts."""
    rnd = random.Random(20260915)
    for index, (_label, code, name, spec, unit, quantity) in enumerate(SEED_MATERIALS, 1):
        material_id = f"{SEED_PREFIX}_MATERIAL_{index}"
        c.execute(
            """INSERT INTO materials(id,code,name,specification,unit,batch_no,expiry_date,total_quantity,available_quantity,version)
               VALUES(?,?,?,?,?,?,?,?,?,1)
               ON CONFLICT(id) DO UPDATE SET code=excluded.code,name=excluded.name,specification=excluded.specification,
                 unit=excluded.unit,total_quantity=excluded.total_quantity,available_quantity=excluded.available_quantity,version=1""",
            (material_id, code, name, spec, unit, f"{SEED_PREFIX}_BATCH_{index}", "2028-12-31", quantity, quantity),
        )
    for index, (name, code) in enumerate(SEED_LOCATIONS, 1):
        c.execute("INSERT INTO locations(id,code,name) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET code=excluded.code,name=excluded.name",
                  (f"{SEED_PREFIX}_LOCATION_{index}", code, name))
    for mi, (_label, _code, _name, _spec, _unit, quantity) in enumerate(SEED_MATERIALS, 1):
        material_id = f"{SEED_PREFIX}_MATERIAL_{mi}"
        for li in (1, 2):
            location_id = f"{SEED_PREFIX}_LOCATION_{li}"
            c.execute("""INSERT INTO inventory(id,material_id,location_id,quantity) VALUES(?,?,?,?)
                       ON CONFLICT(material_id,location_id) DO UPDATE SET quantity=excluded.quantity""",
                      (f"{SEED_PREFIX}_INVENTORY_{mi}_{li}", material_id, location_id, quantity // 2 + (mi % 3 if li == 1 else 0)))

    device_rows = []
    status_plan = ("RELEASED", "IN_PROGRESS", "COMPLETED")
    for order_index, order_no, product, order_status in SEED_ORDER_PLAN:
        order_id = f"{SEED_PREFIX}_ORDER_{order_index}"
        c.execute("""INSERT INTO production_orders(id,order_no,product_name,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET order_no=excluded.order_no,product_name=excluded.product_name,
                   status=excluded.status,updated_at=excluded.updated_at""",
                  (order_id, order_no, product, status_plan[order_index - 1], ts, ts))
        for machine_index in (1, 2):
            device_id = f"{SEED_PREFIX}_DEVICE_{order_index}_{machine_index}"
            style_no = rnd.randint(1000, 9999)
            device_no = f"{order_no}-中文机台-{style_no:04d}"
            c.execute("""INSERT INTO order_devices(id,order_id,device_type,device_no,sequence_no,created_at)
                       VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET device_type=excluded.device_type,device_no=excluded.device_no""",
                      (device_id, order_id, "中文装配机台", device_no, machine_index, ts))
            device_rows.append((order_index, device_id, device_no))

    for order_index, device_id, device_no in device_rows:
        for material_index in range(1, 4):
            material_id = f"{SEED_PREFIX}_MATERIAL_{((order_index + material_index - 2) % len(SEED_MATERIALS)) + 1}"
            required = 4 + order_index + material_index
            arrived = 0 if material_index == 1 else required
            in_stock = 0 if material_index < 3 else required
            status = "OUT_OF_STOCK" if material_index == 1 else ("ARRIVED" if material_index == 2 else "IN_STOCK")
            c.execute("""INSERT INTO order_material_requirements
                       (id,order_id,device_id,material_id,required_quantity,arrived_quantity,in_stock_quantity,status_code,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET required_quantity=excluded.required_quantity,
                       arrived_quantity=excluded.arrived_quantity,in_stock_quantity=excluded.in_stock_quantity,status_code=excluded.status_code""",
                      (f"{SEED_PREFIX}_REQUIREMENT_{order_index}_{device_no[-4:]}_{material_index}", f"{SEED_PREFIX}_ORDER_{order_index}", device_id,
                       material_id, required, arrived, in_stock, status, ts, ts))

    for order_index, device_id, _device_no in device_rows[:3]:
        task_id = f"{SEED_PREFIX}_OP_TASK_{order_index}"
        task_status = TASK_STATUSES[order_index - 1]
        stage = {"WAITING_MATERIAL": 0, "MATERIAL_ACCEPTED": 0, "IN_PROGRESS": 2}[task_status]
        c.execute("""INSERT OR IGNORE INTO assembly_tasks
                   (id,order_no,device_id,device_no,assigned_assembler_id,status,progress_stage,task_version,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""", (task_id, f"MF_TEST_ORDER_0{order_index}", device_id,
                   c.execute("SELECT device_no FROM order_devices WHERE id=?", (device_id,)).fetchone()[0], None, task_status, stage, stage + 1, ts, ts))

    for index in range(1, 4):
        material_id = f"{SEED_PREFIX}_MATERIAL_{index}"
        request_id = f"{SEED_PREFIX}_TRANSFER_REQUEST_{index}"
        c.execute("""INSERT INTO transfer_requests(id,client_operation_id,type,document_no,status,payload_json,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload_json=excluded.payload_json""",
                  (request_id, f"{SEED_PREFIX}_TRANSFER_OP_{index}", "TRANSFER", f"{SEED_PREFIX}_DOC_{index}",
                   ("PENDING_APPROVAL", "APPROVED", "EXECUTED")[index - 1], json.dumps({"materialId": material_id, "quantity": index}, ensure_ascii=False), SEED_ACTOR, ts))
        work_id = f"{SEED_PREFIX}_WORK_ITEM_{index}"
        c.execute("INSERT OR IGNORE INTO material_work_items(id,requirement_id,material_id,device_id,assigned_user_id,quantity) VALUES(?,?,?,?,?,?)",
                  (work_id, f"{SEED_PREFIX}_REQUIREMENT_{index}_{device_rows[index-1][2][-4:]}_1", material_id, device_rows[index-1][1], None, index))
        c.execute("INSERT OR IGNORE INTO material_work_item_projections(work_item_id,status_code,target_device_id,status_updated_at,updated_at) VALUES(?,?,?,?,?)",
                  (work_id, ("PENDING", "PICKED_UP", "AT_STATION")[index - 1], device_rows[index-1][1], ts, ts))
        c.execute("INSERT OR IGNORE INTO material_handovers(id,work_item_id,transfer_request_id,quantity,from_location,device_id,remark,client_operation_id,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (f"{SEED_PREFIX}_HANDOVER_{index}", work_id, request_id, index, f"{SEED_PREFIX}_LOCATION_1", device_rows[index-1][1], "脱敏测试交接", f"{SEED_PREFIX}_HANDOVER_OP_{index}", ("PENDING", "CONFIRMED", "CANCELLED")[index - 1], SEED_ACTOR, ts))
        c.execute("INSERT OR IGNORE INTO exceptions(id,material_id,type,book_quantity,actual_quantity,difference,status,description,evidence_ids,created_by,created_at,order_no,device_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (f"{SEED_PREFIX}_EXCEPTION_{index}", material_id, "库存盘点", 10 + index, 9 + index, -1, ("OPEN", "RESOLVED", "CLOSED")[index - 1], "脱敏测试异常", "[]", SEED_ACTOR, ts, f"MF_TEST_ORDER_0{index}", device_rows[index-1][1]))
        c.execute("INSERT OR IGNORE INTO location_bindings(id,material_id,location_id,quantity,evidence_ids,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                  (f"{SEED_PREFIX}_BINDING_{index}", material_id, f"{SEED_PREFIX}_LOCATION_1", index, "[]", SEED_ACTOR, ts))

def validate_seed(c: sqlite3.Connection) -> dict[str, Any]:
    """Validate fixture counts and relational/enumeration invariants."""
    tables = ("users", "assembly_tasks", "labor_records", "progress_events", "temporary_transfers")
    counts = {table: c.execute(f"SELECT COUNT(*) FROM {table} WHERE id LIKE ?", (f"{SEED_PREFIX}%",)).fetchone()[0]
              for table in tables}
    has_users = bool(c.execute("SELECT COUNT(*) FROM users WHERE id LIKE ?", (f"{SEED_PREFIX}%",)).fetchone()[0])
    expected = {"users": 5 if has_users else 0, "assembly_tasks": 6,
                "labor_records": 6 if has_users else 0, "progress_events": 5 if has_users else 0,
                "temporary_transfers": 2 if has_users else 0}
    if counts != expected:
        raise RuntimeError(f"fixture counts mismatch: {counts} != {expected}")
    if c.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("foreign key violations")
    invalid = c.execute(
        "SELECT COUNT(*) FROM assembly_tasks WHERE id LIKE ? AND status NOT IN "
        "('WAITING_MATERIAL','MATERIAL_ACCEPTED','IN_PROGRESS','PAUSED_FOR_TEMPORARY_TRANSFER','COMPLETED')",
        (f"{SEED_PREFIX}%",),
    ).fetchone()[0]
    if invalid:
        raise RuntimeError(f"invalid task statuses: {invalid}")
    transfer_invalid = c.execute(
        "SELECT COUNT(*) FROM temporary_transfers WHERE id LIKE ? AND status NOT IN ('ACTIVE','COMPLETED')",
        (f"{SEED_PREFIX}%",),
    ).fetchone()[0]
    if transfer_invalid:
        raise RuntimeError(f"invalid transfer statuses: {transfer_invalid}")
    return {"prefix": SEED_PREFIX, "counts": counts, "foreign_keys": "ok", "statuses": "ok"}


def seed_test_fixtures() -> dict[str, Any]:
    """Migrate first, then insert and validate the fixed test fixture set."""
    init_db()
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        _insert_fixture(c)
        result = validate_seed(c)
        c.commit()
        return result
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in Material Flow test fixture seed")
    parser.add_argument("--seed", action="store_true", help="explicitly enable test fixture insertion")
    args = parser.parse_args(argv)
    if not (args.seed or _enabled()):
        print(json.dumps({"enabled": False, "message": "test data seed disabled"}))
        return 0
    print(json.dumps(seed_test_fixtures(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
