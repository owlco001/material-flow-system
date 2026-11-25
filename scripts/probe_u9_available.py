#!/usr/bin/env python3
"""验证官方接口 Invtrans/QueryQohAndAvailable（可用量）是否可用，并与 SQL 直读口径对比。"""
from __future__ import annotations

import sys
import traceback

sys.path.insert(0, "/srv/material-flow")

from app.u9.client import U9CClient  # noqa: E402
from app.u9.config import get_config  # noqa: E402

PATH_INV = "/webapi/Invtrans/QueryQohAndAvailable"
PATH_SQL = "/webapi/QueryCommon/QueryInfoBySql"


def main() -> int:
    cfg = get_config()
    client = U9CClient(cfg)
    print(f"U9: {cfg.base_url}  org={cfg.org_code!r}")

    # 取几个真实料号（从采购行里挑）
    sql = ("SELECT TOP 3 l.ItemInfo_ItemCode AS code FROM PM_POLine l "
           "WHERE l.ItemInfo_ItemCode IS NOT NULL AND l.ItemInfo_ItemCode <> ''")
    try:
        codes = [r["code"] for r in client._post(PATH_SQL, {"SqlString": sql})]
    except Exception as e:  # noqa: BLE001
        print("取料号失败:", str(e)[:150])
        codes = ["30414001-00045"]
    print(f"测试料号: {codes}\n")

    for code in codes[:3]:
        print(f"--- {code}")
        payload = {"Org": cfg.org_code or "", "ItemCode": code}
        try:
            rows = client._post(PATH_INV, payload)
            print(f"    接口 OK，返回 {len(rows)} 行")
            for r in rows[:3]:
                print(f"      wh={r.get('m_whCode')} bin={r.get('m_binCode')} "
                      f"storeQty={r.get('m_storeQty')} canUse={r.get('m_canUseQty')} "
                      f"reserv={r.get('m_reservQty')} occupy={r.get('m_resvOccupyStQty')} "
                      f"notUse={r.get('m_notUseQty')} sysState={r.get('sysState')}")
        except Exception as e:  # noqa: BLE001
            print(f"    接口失败: {str(e)[:220]}")

        # SQL 直读（现网口径）
        q = (f"SELECT SUM(q.StoreQty) AS store, SUM(CASE WHEN q.StorageType=4 "
             f"THEN q.StoreQty - ISNULL(q.ResvOccupyStQty,0) ELSE 0 END) AS canuse_approx "
             f"FROM InvTrans_WhQoh q WHERE q.ItemInfo_ItemCode = '{code.replace(chr(39), chr(39)*2)}'")
        try:
            r = client._post(PATH_SQL, {"SqlString": q})[0]
            print(f"    SQL 直读: StoreQty={r.get('store')} 可用量近似={r.get('canuse_approx')}")
        except Exception as e:  # noqa: BLE001
            print("    SQL 直读失败:", str(e)[:150])

    print("\n=== 完成 ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
