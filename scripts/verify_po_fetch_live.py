#!/usr/bin/env python3
"""部署后用真实 U9 验证 fetch_purchase_orders（新代码路径）。

跑在 221 上，导入 /srv/material-flow 的已部署代码，因此验证的是真实客户端实现。
"""
from __future__ import annotations

import sys
import time
import traceback

sys.path.insert(0, "/srv/material-flow")

from app.u9.client import U9CClient  # noqa: E402
from app.u9.config import get_config  # noqa: E402


def summarize(rows, label):
    docs = len(rows)
    lines = sum(len(r.get("lines") or []) for r in rows) if rows and "lines" in rows[0] else None
    print(f"\n--- {label}: {docs} 张单" + (f" / {lines} 行" if lines is not None else ""))
    if not rows:
        return
    sample = rows[0]
    print(f"    首单字段: {sorted(sample.keys())}")
    statuses = {}
    for r in rows:
        k = (r.get("status_name") or r.get("status_value") or "?")
        statuses[k] = statuses.get(k, 0) + 1
    print(f"    状态分布: {statuses}")
    if lines is not None:
        print(f"    平均行/单: {lines / docs:.1f}")


def main() -> int:
    cfg = get_config()
    client = U9CClient(cfg)
    print(f"U9: {cfg.base_url}")

    t0 = time.time()
    try:
        rows_all = client.fetch_purchase_orders(since_days=30, only_open=False)
    except Exception:
        traceback.print_exc()
        return 1
    print(f"耗时 {time.time()-t0:.1f}s")
    summarize(rows_all, "近 30 天（含已结案）")

    t0 = time.time()
    rows_open = client.fetch_purchase_orders(since_days=30, only_open=True)
    print(f"耗时 {time.time()-t0:.1f}s")
    summarize(rows_open, "近 30 天（仅未结案）")

    print("\n结论:")
    print(f"  含结案单数 {len(rows_all)} ≥ 仅未结案 {(len(rows_open))} 且多出 {len(rows_all)-len(rows_open)} 张"
          f" → {'符合预期（关闭单能同步回来）' if len(rows_all) > len(rows_open) else '两边相同，请检查'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
