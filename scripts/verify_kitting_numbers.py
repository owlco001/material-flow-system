"""独立复核需求2 双口径数字：直接调 _kitting_dataset 对只读库副本复算。"""
import sqlite3
import sys

sys.path.insert(0, "/root/mf-req2/material-flow-backend")

from app import main as M  # noqa: E402

DB = "/opt/smart-factory/data/material_flow.db"
TARGET_PROJECT = "26B-013"
TARGET_MATERIAL = "30403004-00137"


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    admin = con.execute("SELECT * FROM users WHERE role='ADMIN' LIMIT 1").fetchone()
    if admin is None:
        print("库里没有 ADMIN 用户")
        return 1

    project, resolved_from = M._pmr_resolve_project(con, TARGET_PROJECT)
    print(f"项目: {project['order_no']}  {project['product_name']}  (resolved_from={resolved_from})")

    ds = M._kitting_dataset(con, admin, project, sort_key="procure", resolved_from=resolved_from)
    s = ds["summary"]
    items = ds["items"]

    print(f"\n需求行 {s['requirementLineCount']} / 齐套 {s['kittedLineCount']} / 缺料 {s['shortageLineCount']}"
          f" / 齐套率 {s['kittingRate']}%")
    print(f"采购缺口合计 {s['totalProcureGapQuantity']} / 备料缺口合计 {s['totalPrepareGapQuantity']}")

    sp = sum(i["procureGapQuantity"] for i in items)
    sq = sum(i["prepareGapQuantity"] for i in items)
    print(f"清单列求和: 采购 {sp} / 备料 {sq}")
    print(f"恒等式 需求行==齐套+缺料 : {s['requirementLineCount'] == s['kittedLineCount'] + s['shortageLineCount']}")
    print(f"合计==清单求和      : 采购 {s['totalProcureGapQuantity'] == sp} / 备料 {s['totalPrepareGapQuantity'] == sq}")

    hit = [i for i in items if i["materialCode"] == TARGET_MATERIAL]
    if hit:
        i = hit[0]
        print(f"\n{TARGET_MATERIAL}: 采购缺口 {i['procureGapQuantity']} / 备料缺口 {i['prepareGapQuantity']}"
              f" / 影响机台 {i['affectedDeviceCount']} / 在库 {i.get('inStockQuantity')}")
    else:
        print(f"\n{TARGET_MATERIAL} 不在缺料清单")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
