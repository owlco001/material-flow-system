"""U9C → 本地表 字段映射（字段名以用户提供的 U9C swagger 导出为准）。

U9C 响应 DTO 的字段一律是 m_ 前缀的小驼峰（m_code / m_name / m_inventoryUOM.m_name ...），
引用型字段（单位、分类、物料等）是 {m_iD, m_code, m_name} 小对象。所有取值都容错：
字段缺失、为 null、类型不对时取默认值，不抛异常（缺主键时抛 ValueError 让上层计为跳过）。

本地表（以 app/main.py 建表语句为准）：
  materials(code UNIQUE, name, specification, unit, total_quantity, available_quantity, version)
  agg_bom_batches(project_name, file_name, file_sha256, row_count, device_count, material_count,
                  created_by, created_at)
  agg_bom_items(batch_id, device_code, device_name, level_no, parent_code, material_code,
                material_name, specification, unit, quantity, material_form, line_no)
  locations(id, code UNIQUE, name) / inventory(material_id, location_id, quantity>=0)
  production_orders(order_no UNIQUE, product_name, status, created_at, updated_at)
"""
from __future__ import annotations

from datetime import datetime, timezone

import time
from typing import Any


def g(obj: Any, path: str, default: Any = "") -> Any:
    """按点路径取值：g(row, "m_inventoryUOM.m_name")。任何一层缺失返回 default。"""
    cur = obj
    for key in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def first(obj: Any, *paths: str, default: Any = "") -> Any:
    """依次尝试多个路径，返回第一个非空值。"""
    for p in paths:
        v = g(obj, p, None)
        if v not in (None, "", [], {}):
            return v
    return default


def s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _now() -> str:
    # 与主程序一致存 UTC ISO（带 +00:00）；管理台 local_dt 再转北京时间显示。
    # 0.8.2 及以前存的是无时区的本地时间，被当作 UTC 再 +8，显示晚 8 小时。
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- 料品形态属性
# U9 ItemFormAttribute 枚举（CBO_ItemMaster.ItemFormAttribute，公开资料核对）。
# 本地 BOM 需求生成只认「采购件 / 委外加工件 / 制造件」三种中文值（与人工导出的 Excel 一致）。
ITEM_FORM_LABELS = {
    0: "模型", 1: "按订单拣货", 2: "按订单装配", 3: "可选类", 4: "委外加工件", 5: "工序委外件",
    6: "虚拟", 7: "计划", 8: "系列", 9: "采购件", 10: "制造件", 11: "最终装配件", 12: "套件",
    13: "工艺", 14: "低值易耗品", 15: "商品", 16: "杂项料品", 17: "服务", 18: "资产", 19: "备件",
    20: "配方", 21: "重复", 22: "费用性料品",
}
# 需要继续往下展开 BOM 的形态（采购件等没有下层 BOM，不必再查）
EXPANDABLE_FORMS = {"制造件", "委外加工件", "工序委外件", "最终装配件", "虚拟", "套件", "按订单装配", "模型"}


def item_form_label(v: Any) -> str:
    try:
        return ITEM_FORM_LABELS.get(int(v), "")
    except (TypeError, ValueError):
        return s(v)


# ---------------------------------------------------------------- 料品 /webapi/ItemMaster/Query
def map_item(raw: dict[str, Any]) -> dict[str, Any]:
    """U9C ItemMasterDTOData → 本地 materials 行（不含数量字段）+ 预览用附加字段。"""
    code = s(first(raw, "m_code", "Code", "ItemCode"))
    if not code:
        raise ValueError("U9 料品缺少编码 m_code")
    unit = s(first(raw, "m_inventoryUOM.m_name", "m_inventoryUOM.m_code",
                   "m_manufactureUOM.m_name", "m_purchaseUOM.m_name"))
    return {
        "code": code,
        "name": s(first(raw, "m_name", "Name")) or code,
        "specification": s(first(raw, "m_sPECS", "m_specs", "m_SPECS", "SPECS")),
        "unit": unit or "个",
        "item_form": item_form_label(g(raw, "m_itemFormAttribute", None)),
        "category": s(first(raw, "m_mainItemCategory.m_name", "m_mainItemCategory.m_code")),
        "category_code": s(first(raw, "m_mainItemCategory.m_code")),
        "status": s(first(raw, "m_status.m_name", "m_status.m_code")),
        "u9_id": g(raw, "m_iD", None),
    }


# ---------------------------------------------------------------- BOM /webapi/BOM/Query
def bom_item_code(bom: dict) -> str:
    return s(first(bom, "m_itemMaster.m_code"))


def pick_bom(boms: list[dict]) -> dict | None:
    """同一母件可能返回多个 BOM（版本 / 生产目的 / 类型）。
    取法（假设，见 docs/U9C_INTEGRATION.md）：优先 m_alternateType=0（主制造）、
    m_bOMType=0，然后版本号最大、生效日期最新的那一个。"""
    if not boms:
        return None

    def key(b: dict):
        return (
            1 if int(num(g(b, "m_alternateType", 0))) == 0 else 0,
            1 if int(num(g(b, "m_bOMType", 0))) == 0 else 0,
            s(g(b, "m_bOMVersionCode")),
            s(g(b, "m_effectiveDate")),
        )
    return sorted(boms, key=key, reverse=True)[0]


def _expired(date_str: str, today: str) -> bool:
    d = s(date_str)[:10].replace("/", "-")
    return bool(d) and len(d) == 10 and d < today


def map_bom_component(comp: dict, today: str | None = None) -> dict | None:
    """BOMComponentDTO4CreateSvData → 规范化子件行；失效/删除的子件返回 None。"""
    today = today or time.strftime("%Y-%m-%d")
    if comp.get("m_isDelete") is True or comp.get("m_isEffective") is False:
        return None
    if _expired(g(comp, "m_disableDate"), today) and s(g(comp, "m_disableDate"))[:4] != "9999":
        return None
    code = s(first(comp, "m_itemMaster.m_code"))
    if not code:
        return None
    usage = num(g(comp, "m_usageQty", 0))
    parent_qty = num(g(comp, "m_parentQty", 0))
    qty = usage / parent_qty if parent_qty > 0 else usage
    return {
        "material_code": code,
        "material_name": s(first(comp, "m_itemMaster.m_name")),
        "unit": s(first(comp, "m_issueUOM.m_name", "m_issueUOM.m_code")),
        "qty": round(qty, 6),
        "sequence": int(num(g(comp, "m_sequence", 0))),
        "is_phantom": bool(comp.get("m_isPhantomPart")),
    }


def bom_components(bom: dict, today: str | None = None) -> list[dict]:
    comps = g(bom, "m_bOMComponents", []) or []
    out = [c for c in (map_bom_component(x, today) for x in comps if isinstance(x, dict)) if c]
    out.sort(key=lambda c: c["sequence"])
    return out


def map_bom_line(batch_id: str, line_no: int, line: dict[str, Any],
                 device_code: str, device_name: str) -> dict[str, Any]:
    """规范化 BOM 行（client.fetch_bom 的 lines）→ agg_bom_items 行。"""
    code = s(line.get("material_code"))
    if not code:
        raise ValueError("BOM 行缺少料号")
    return {
        "id": f"{batch_id}-{line_no}",
        "batch_id": batch_id,
        "device_code": device_code,
        "device_name": device_name,
        "level_no": int(num(line.get("level"), 0)),
        "parent_code": s(line.get("parent_code")),
        "material_code": code,
        "material_name": s(line.get("material_name")),
        "specification": s(line.get("spec")),
        "unit": s(line.get("unit")) or "个",
        "quantity": num(line.get("qty"), 0),
        "material_form": s(line.get("material_form")),
        "line_no": line_no,
    }


# ---------------------------------------------------------------- 库存 /webapi/Invtrans/QueryQohAndAvailable
def map_inventory_row(raw: dict[str, Any], qty_field: str = "store") -> dict[str, Any]:
    code = s(first(raw, "m_itemCode"))
    if not code:
        raise ValueError("U9 库存行缺少料号 m_itemCode")
    wh = s(first(raw, "m_whCode"))
    bin_code = s(first(raw, "m_binCode"))
    loc_code = f"{wh}-{bin_code}" if wh and bin_code else (wh or bin_code or "U9")
    loc_name = " / ".join(x for x in (s(g(raw, "m_whName")) or wh, s(g(raw, "m_binName")) or bin_code) if x)
    store = num(g(raw, "m_storeQty", 0))
    avail = num(g(raw, "m_canUseQty", 0))
    return {
        "material_code": code,
        "material_name": s(g(raw, "m_itemName")),
        "spec": s(g(raw, "m_itemSPECS")),
        "wh_code": wh,
        "bin_code": bin_code,
        "lot_code": s(g(raw, "m_lotCode")),
        "location_code": loc_code,
        "location_name": loc_name or loc_code,
        "store_qty": store,
        "available_qty": avail,
        "qty": avail if qty_field == "available" else store,
    }


# ---------------------------------------------------------------- 生产订单 /webapi/MODoc/Query
# 用户提供的 MODTOData 没有单据状态字段（2026-10-14 用真实完工单 MO-2608130010 实测确认：
# 返回 51 个顶层字段，无 DocState/完工数量）。若现场版本返回了 m_docState / m_status / DocState，
# 按 U9 MODocState 常见取值映射（假设，见文档）；没有则新单按 RELEASED（已下达）入库、已有单不改本地状态。
U9_MO_STATUS_MAP = {
    0: "DRAFT",        # 开立
    1: "RELEASED",     # 已核准
    2: "IN_PROGRESS",  # 开工
    3: "COMPLETED",    # 完工
    4: "COMPLETED",    # 关闭（本地无「关闭」，视为已完成）
}
U9_MO_STATUS_LABELS = {0: "开立", 1: "已核准", 2: "开工", 3: "完工", 4: "关闭"}
# 2026-10-14 实库核对：MO_MO.DocState=3 的 5526 单中 5398 单完工数量≥生产数量，=2 的 1712 单基本未完工，
# 即 2=开工、3=完工 与上表一致。
# 状态只「往前走」：本地已完成/已取消的单不会被 U9 拉回较早状态
STATUS_RANK = {"DRAFT": 0, "RELEASED": 1, "IN_PROGRESS": 2, "PAUSED": 2,
               "COMPLETED": 3, "CANCELLED": 3}


def map_production_order(raw: dict[str, Any]) -> dict[str, Any]:
    order_no = s(first(raw, "m_docNo", "DocNo"))
    if not order_no:
        raise ValueError("U9 生产订单缺少单号 m_docNo")
    raw_state = first(raw, "m_docState", "m_status", "DocState", "m_state", default=None)
    status = None
    state_label = ""
    if raw_state not in (None, ""):
        try:
            st = int(raw_state)
            status = U9_MO_STATUS_MAP.get(st)
            state_label = U9_MO_STATUS_LABELS.get(st, str(st))
        except (TypeError, ValueError):
            state_label = s(raw_state)
    product_code = s(first(raw, "m_itemMaster.m_code"))
    product_name = s(first(raw, "m_itemMaster.m_name")) or product_code
    return {
        "order_no": order_no,
        "product_code": product_code,
        "product_name": product_name,
        "qty": num(g(raw, "m_productQty", 0)),
        "uom": s(first(raw, "m_productUOM.m_name", "m_productUOM.m_code")),
        "start_date": s(g(raw, "m_startDate"))[:10],
        "complete_date": s(g(raw, "m_completeDate"))[:10],
        "actual_start_date": s(g(raw, "m_actualStartDate"))[:10],
        "department": s(first(raw, "m_department.m_name")),
        "u9_state": state_label,
        "status": status,
        "picklist": map_mo_picklist(raw),
    }


def map_mo_picklist(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """生产订单备料表 m_mOPickListDTOs → 按料号汇总的物料需求。

    需求量取 m_actualReqQty（实际需求，缺省回落 m_bOMReqQty）；本地需求量是整数列，小数向上取整。
    m_isDisable=True 的行跳过。m_issuedQty（U9 已发料）只作展示，不写本地到货/在库。
    """
    agg: dict[str, dict[str, Any]] = {}
    for line in g(raw, "m_mOPickListDTOs", None) or []:
        if not isinstance(line, dict) or g(line, "m_isDisable", False) is True:
            continue
        code = s(first(line, "m_itemMaster.m_code"))
        if not code:
            continue
        qty = num(first(line, "m_actualReqQty", default=None), num(g(line, "m_bOMReqQty", 0)))
        if qty <= 0:
            continue
        row = agg.setdefault(code, {
            "material_code": code,
            "material_name": s(first(line, "m_itemMaster.m_name")) or code,
            "unit": s(first(line, "m_issueUOM.m_name", "m_issueBaseUOM.m_name")) or "件",
            "qty": 0.0, "issued_qty": 0.0,
        })
        row["qty"] += qty
        row["issued_qty"] += num(g(line, "m_issuedQty", 0))
    for row in agg.values():
        q = row["qty"]
        row["required_quantity"] = int(q) if q == int(q) else int(q) + 1
    return list(agg.values())


# ---------------------------------------------------------------- 物料分类树
def build_category_tree(rows: list[dict]) -> list[dict]:
    """[{code,name}] → [{code,name,parent_code,level}]。
    U9 分类编码按前缀分层（3 → 302 → 30203 → 30203001），父节点取“是本编码真前缀的最长编码”。"""
    cats = {}
    for r in rows:
        code = s(r.get("code"))
        if code:
            cats[code] = s(r.get("name")) or code
    out = []
    for code in sorted(cats, key=lambda c: (len(c), c)):
        parent = next((code[:i] for i in range(len(code) - 1, 0, -1) if code[:i] in cats), None)
        level = 1 if parent is None else next(o["level"] for o in out if o["code"] == parent) + 1
        out.append({"code": code, "name": cats[code], "parent_code": parent, "level": level})
    return out


# ---------------------------------------------------------------- 采购订单 /webapi/CommonEntity/Query
# 需求 3：采购订单只读同步。U9 侧字段名已实测：
#   PM_PurchaseOrder：DocNo / BusinessDate / Status / Supplier_Code / Supplier_ShortName / DeliveryDate
#   PM_POLine      ：DocLineNo / ItemInfo_ItemCode / ItemInfo_ItemName / PurQtyTU /
#                    **TotalRecievedQtyTU**（U9 自己把 Received 拼成 Recieved，照抄）
# 状态必须取 Status.Value / Status.Name：直接取 Status 只得到枚举类型名。
PO_REQUIRED_HEADER_FIELDS = ("DocNo", "BusinessDate", "Status.Value", "Status.Name",
                             "Supplier_Code", "DeliveryDate")
PO_REQUIRED_LINE_FIELDS = ("DocLineNo", "ItemInfo.ItemCode", "PurQtyTU", "TotalRecievedQtyTU")

# 已结案的采购订单不算在途。U9 现场取值以 Status.Name 为准（如 Approved / Closed / Cancel），
# 中文库可能是中文名，两套都认；Status.Value 只作为兜底（本版 U9 的 0..4 语义未全部实测，
# 因此不硬编码数字，仅按名称判定，避免把「已审核」误判成「已关闭」）。
PO_CLOSED_NAME_MARKERS = ("clos", "cancel", "void", "reject", "abandon",
                          "关闭", "结案", "作废", "取消", "终止", "驳回")


class _Missing:
    pass


_MISSING = _Missing()


def _ci_get(obj: Any, key: str) -> Any:
    """大小写不敏感取键（U9 不同接口/版本的大小写不统一）。"""
    if not isinstance(obj, dict):
        return _MISSING
    if key in obj:
        return obj[key]
    low = key.lower()
    for k, v in obj.items():
        if str(k).lower() == low:
            return v
    return _MISSING


def field_present(obj: Any, path: str) -> bool:
    """字段是否真的在响应里（支持嵌套 Status.Value 与扁平 "Status.Value" 两种形态）。

    用于 U9「不存在的字段静默忽略」的兜底校验：请求了却没回来，说明字段名不对或
    该版本没有这个字段，必须报错而不是当成 0。
    """
    if not isinstance(obj, dict):
        return False
    literal = _ci_get(obj, path)
    if literal is not _MISSING:
        return True
    cur: Any = obj
    for part in path.split("."):
        cur = _ci_get(cur, part)
        if cur is _MISSING:
            return False
    return True


def po_raw_lines(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """采购订单行：POLines 可能是列表、单个对象或扁平点号键，统一成列表。"""
    lines = _ci_get(raw, "POLines")
    if lines is _MISSING or lines is None:
        return []
    if isinstance(lines, dict):
        return [lines]
    if isinstance(lines, list):
        return [ln for ln in lines if isinstance(ln, dict)]
    return []


def validate_po_rows(rows: list[dict[str, Any]]) -> None:
    """校验采购订单响应里关键字段是否真的回来了；缺字段抛 ValueError（上层转 U9Error）。

    只看「所有记录都没这个字段」的情况：个别单据缺值（null）是正常业务数据。
    行字段只在确实返回了订单行时校验，避免「180 天内所有 PO 都没行」被误报成字段缺失。
    """
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return
    missing = [f for f in PO_REQUIRED_HEADER_FIELDS
               if not any(field_present(r, f) for r in rows)]
    line_rows = [ln for r in rows for ln in po_raw_lines(r)]
    if line_rows:
        missing += [f"POLines.{f}" for f in PO_REQUIRED_LINE_FIELDS
                    if not any(field_present(ln, f) for ln in line_rows)]
    if missing:
        raise ValueError(
            "U9 采购订单查询未返回关键字段：" + "、".join(missing)
            + "。U9 对不存在的字段会静默忽略，为避免把缺失的到货量当成 0（会把"
              "「已到货待入库」误判成「无采购计划」），本次同步已中止；请核对 "
              "UFIDA.U9.PM.PO.PurchaseOrder 的字段名（到货量是 TotalRecievedQtyTU，"
              "U9 自己拼错 Recieved）后重试")


def po_doc_no(raw: dict[str, Any]) -> str:
    return s(first(raw, "DocNo", "docNo", "m_docNo"))


def po_status_pair(raw: dict[str, Any]) -> tuple[str, str]:
    """(Status.Value, Status.Name)；缺失时返回空串（不猜状态）。"""
    status = _ci_get(raw, "Status")
    if isinstance(status, dict):
        return s(first(status, "Value", "value")), s(first(status, "Name", "name"))
    return s(first(raw, "Status.Value", default="")), s(first(raw, "Status.Name", default=""))


def po_status_closed(status_value: Any, status_name: Any) -> bool:
    """采购订单是否已结案（关闭/作废/取消…）。只按状态名判断，数字语义未实测不硬编码。"""
    name = s(status_name).lower()
    if name and any(marker in name for marker in PO_CLOSED_NAME_MARKERS):
        return True
    val = s(status_value).lower()
    return bool(val) and any(marker in val for marker in PO_CLOSED_NAME_MARKERS)


def map_purchase_order_line(raw: dict[str, Any], fallback_no: int = 0) -> dict[str, Any] | None:
    """一条采购订单行 → 本地 u9_po_lines 字段；没有行号/料号时返回 None（无法归集）。"""
    code = s(first(raw, "ItemInfo.ItemCode", "ItemCode", "m_itemMaster.m_code"))
    if not code:
        return None
    try:
        line_no = int(round(num(first(raw, "DocLineNo", "LineNo"), fallback_no)))
    except (TypeError, ValueError):
        line_no = fallback_no
    return {
        "line_no": line_no,
        "item_code": code,
        "item_name": s(first(raw, "ItemInfo.ItemName", "ItemName", "m_itemMaster.m_name")),
        "pur_qty_tu": num(first(raw, "PurQtyTU", "PurQty", default=0)),
        "recieved_qty_tu": num(first(raw, "TotalRecievedQtyTU", "TotalReceivedQtyTU", default=0)),
        "delivery_date": s(first(raw, "DeliveryDate", default=""))[:10],
    }


def map_purchase_order(raw: dict[str, Any]) -> dict[str, Any]:
    """一张采购订单（含行）→ 本地表字段。缺单号抛 ValueError（上层计为跳过）。"""
    no = po_doc_no(raw)
    if not no:
        raise ValueError("U9 采购订单缺少单号 DocNo")
    status_value, status_name = po_status_pair(raw)
    lines = []
    for index, ln in enumerate(po_raw_lines(raw), start=1):
        mapped = map_purchase_order_line(ln, index)
        if mapped is not None:
            lines.append(mapped)
    return {
        "doc_no": no,
        "status_value": status_value,
        "status_name": status_name,
        "supplier_code": s(first(raw, "Supplier_Code", "Supplier.Code")),
        "supplier_name": s(first(raw, "Supplier_ShortName", "Supplier_Name",
                                 "Supplier.Name", "Supplier.Name2")),
        "business_date": s(first(raw, "BusinessDate", default=""))[:10],
        "org_code": s(first(raw, "Org.Code", "Org", default="")),
        "delivery_date": s(first(raw, "DeliveryDate", default=""))[:10],
        "lines": lines,
    }
