#!/usr/bin/env python3
"""验证 U9 采购订单返回形态（嵌套 POLines 还是扁平行），并与 PM_POLine 行数对账。"""
from __future__ import annotations

import sys
import traceback

sys.path.insert(0, "/srv/material-flow")

from app.u9.config import get_config  # noqa: E402
from app.u9.client import U9CClient, U9Error  # noqa: E402

PATH_QUERY = "/webapi/CommonEntity/Query"
PATH_SQL = "/webapi/QueryCommon/QueryInfoBySql"

FIELDS = ["DocNo", "BusinessDate", "Status.Value", "Status.Name",
          "Supplier_Code", "Supplier_ShortName", "DeliveryDate",
          "POLines.DocLineNo", "POLines.ItemInfo.ItemCode", "POLines.ItemInfo.ItemName",
          "POLines.PurQtyTU", "POLines.TotalRecievedQtyTU"]


def main() -> int:
    cfg = get_config()
    client = U9CClient(cfg)
    print(f"U9: {cfg.base_url}")

    payload = {"PageSize": 5, "PageIndex": 1, "Orders": "BusinessDate desc",
               "Filters": [], "EntityFullName": "UFIDA.U9.PM.PO.PurchaseOrder",
               "ReturnFields": FIELDS}
    rows = client._post(PATH_QUERY, payload)
    print(f"\n返回 {len(rows)} 张单；首单顶层键 {list(rows[0].keys())}")
    for r in rows:
        lines = r.get("POLines")
        shape = type(lines).__name__
        n = len(lines) if isinstance(lines, list) else ("单对象" if isinstance(lines, dict) else "无")
        print(f"  {r.get('DocNo')} 状态={ (r.get('Status') or {}) } POLines={shape} 行数={n}")
        if isinstance(lines, list) and lines:
            print(f"     行0 键: {list(lines[0].keys())}  值: {lines[0]}")
            break

    # 全页统计：PageSize=500 第一页的总行数
    p2 = {**payload, "PageSize": 500}
    rows2 = client._post(PATH_QUERY, p2)
    total_lines = sum(len(r.get("POLines") or []) if isinstance(r.get("POLines"), list) else
                      (1 if isinstance(r.get("POLines"), dict) else 0) for r in rows2)
    print(f"\nPageSize=500 返回 {len(rows2)} 张单，共 {total_lines} 行")

    # 对账：这 500 张单在 U9 库里应有几行
    doc_nos = [r.get("DocNo") for r in rows2 if r.get("DocNo")]
    if doc_nos:
        in_list = ",".join("'" + d.replace("'", "''") + "'" for d in doc_nos)
        sql = (f"SELECT COUNT(*) AS n FROM PM_POLine l JOIN PM_PurchaseOrder h ON h.ID=l.PurchaseOrder "
               f"WHERE h.DocNo IN ({in_list})")
        try:
            db_n = client._post(PATH_SQL, {"SqlString": sql})[0]["n"]
            print(f"U9 库中同一批单据行数 = {db_n}  → 对账 {'一致' if db_n == total_lines else '不一致!'}")
        except U9Error as e:
            print("对账 SQL 失败:", str(e)[:120])

    print("\n=== 完成 ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
