"""U9 → 本地表 字段映射。

映射到的本地表（以 material-flow-backend/app/main.py 建表语句为准）：
  materials(code UNIQUE, name, specification, unit, ...)
  agg_bom_batches / agg_bom_items(device_code, level_no, parent_code, material_code,
      material_name, specification, unit, quantity, material_form, line_no)
  inventory(material_id, location_id, quantity)  -- 经 materials.code 解析 material_id
  production_orders(order_no UNIQUE, product_name, status)

注意：
  - U9 侧字段名（code/name/spec/unit 等）为"期望名"，真实字段名待实施商《接口文档》确认后
    在此处一次性改掉，sync 层不动。
  - materials 的 total_quantity / available_quantity 不由料品档案同步改写，
    只由库存现存量同步（sync_inventory）更新，避免把本地流转中的数量冲掉。
  - 本地状态码保持英文（如 IN_PROGRESS），中文只在展示层做（项目既有约定）。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

# ---------------------------------------------------------------- 料品档案 → materials
# U9 期望字段（待实施商确认真实字段名）：
#   code: 料品编码 / name: 料品名称 / spec: 规格型号 / unit: 计量单位
# ----------------------------------------------------------------
ITEM_FIELD_MAP = {
    "code": "code",
    "name": "name",
    "spec": "spec",
    "unit": "unit",
}


def map_item(u9_item: dict[str, Any]) -> dict[str, Any]:
    """U9 料品 → materials 行（不含数量字段）。"""
    get = lambda *keys: next((u9_item[k] for k in keys if u9_item.get(k) not in (None, "")), "")
    code = str(get("code", "Code", "ItemCode", "料品编码")).strip()
    if not code:
        raise ValueError("U9 料品缺少编码，跳过")
    return {
        "id": f"u9-{code}",
        "code": code,
        "name": str(get("name", "Name", "ItemName", "料品名称")).strip(),
        "specification": str(get("spec", "Spec", "Specification", "规格型号")).strip(),
        "unit": str(get("unit", "Unit", "计量单位")).strip() or "个",
    }


# ---------------------------------------------------------------- BOM → agg_bom_batches / agg_bom_items
# U9 期望：一张多层 BOM（对应现在人工导出的聚合 Excel 口径：device_code 为设备编码行，
# level_no 为层级，parent_code 为父项编码，material_form 区分采购件/制造件/委外件）。
# ----------------------------------------------------------------
def map_bom_batch(product_code: str, product_name: str = "") -> dict[str, Any]:
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    return {
        "id": f"u9bom-{product_code}-{int(time.time())}",
        "product_code": product_code,
        "product_name": product_name,
        "source": "u9",
        "created_at": now,
    }


def map_bom_line(batch_id: str, line_no: int, u9_line: dict[str, Any]) -> dict[str, Any]:
    get = lambda *keys: next((u9_line[k] for k in keys if u9_line.get(k) not in (None, "")), "")
    return {
        "id": f"{batch_id}-{line_no}",
        "batch_id": batch_id,
        "device_code": str(get("device_code", "DeviceCode", "设备编码")).strip(),
        "device_name": str(get("device_name", "DeviceName", "设备名称")).strip(),
        "level_no": int(get("level", "Level", "层级") or 0),
        "parent_code": str(get("parent_code", "ParentCode", "父项编码")).strip(),
        "material_code": str(get("material_code", "MaterialCode", "料品编码")).strip(),
        "material_name": str(get("material_name", "MaterialName", "料品名称")).strip(),
        "specification": str(get("spec", "Spec", "规格型号")).strip(),
        "unit": str(get("unit", "Unit", "计量单位")).strip() or "个",
        "quantity": float(get("qty", "Qty", "数量") or 0),
        "material_form": str(get("material_form", "MaterialForm", "形态")).strip(),
        "line_no": line_no,
    }


# ---------------------------------------------------------------- 库存现存量 → inventory
# 期望字段：material_code, location(库位编码), qty。location_id 直接用 U9 库位编码；
# locations 表如无对应行，sync 层自动补一行（名称=编码）。
# ----------------------------------------------------------------
def map_inventory_row(u9_row: dict[str, Any]) -> dict[str, Any]:
    get = lambda *keys: next((u9_row[k] for k in keys if u9_row.get(k) not in (None, "")), "")
    code = str(get("material_code", "MaterialCode", "料品编码")).strip()
    if not code:
        raise ValueError("U9 库存行缺少料品编码，跳过")
    return {
        "material_code": code,
        "location_id": str(get("location", "Location", "库位")).strip() or "U9",
        "quantity": int(float(get("qty", "Qty", "现存量") or 0)),
    }


# ---------------------------------------------------------------- 生产订单 → production_orders
# 期望字段：order_no(生产订单号), product_name, status(U9状态码→本地状态码映射见下)
# ----------------------------------------------------------------
U9_MO_STATUS_MAP = {
    # U9 状态码 → 本地 production_orders.status（待实施商确认 U9 真实状态值后补全）
    "open": "IN_PROGRESS",
    "released": "IN_PROGRESS",
    "closed": "COMPLETED",
    "cancelled": "CANCELLED",
}


def map_production_order(u9_mo: dict[str, Any]) -> dict[str, Any]:
    get = lambda *keys: next((u9_mo[k] for k in keys if u9_mo.get(k) not in (None, "")), "")
    order_no = str(get("order_no", "OrderNo", "生产订单号")).strip()
    if not order_no:
        raise ValueError("U9 生产订单缺少订单号，跳过")
    raw_status = str(get("status", "Status", "状态")).strip().lower()
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    return {
        "id": f"u9mo-{order_no}",
        "order_no": order_no,
        "product_name": str(get("product_name", "ProductName", "产品名称")).strip(),
        "status": U9_MO_STATUS_MAP.get(raw_status, "IN_PROGRESS"),
        "_u9_raw_status": raw_status,  # 进 sync 日志，不入库
        "created_at": now,
        "updated_at": now,
    }
