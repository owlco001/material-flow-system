"""Agent 问答评测：用真实 LLM 跑一组固定问题，检查是否调用了该调用的工具。

用法（在 /srv/material-flow 或 material-flow-backend 下）：
    .venv/bin/python -m app.agent.eval                    # 用生产库副本、管理台里配置的模型
    .venv/bin/python -m app.agent.eval --only bom-where   # 只跑某几条（逗号分隔）
    .venv/bin/python -m app.agent.eval --db /path/x.db --report /tmp/eval.json

先把数据库热备到临时文件再跑，评测产生的会话不会写进生产库。
判定：expect_tools 中任一被调用即通过（expect_all=true 时需全部调用）；
expect_no_tools=true 表示应拒答且不调用任何工具。改提示词或换模型前后各跑一次对比通过率。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from app.agent import service
from app.agent.config import AgentConfig

CASES_FILE = Path(__file__).with_name("eval_cases.json")


def load_cases(path: Path = CASES_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def judge(case: dict, called: list[str]) -> bool:
    if case.get("expect_no_tools"):
        return not called
    expect = case.get("expect_tools") or []
    if not expect:
        return True
    if case.get("expect_all"):
        return all(t in called for t in expect)
    return any(t in called for t in expect)


def _resolve_cfg(conn: sqlite3.Connection) -> AgentConfig:
    try:
        from app.admin_web import _agent_cfg_effective
        return _agent_cfg_effective(conn)
    except Exception:  # noqa: BLE001
        return AgentConfig.from_env()


def run_eval(conn: sqlite3.Connection, cfg: AgentConfig, cases: list[dict], out=sys.stdout) -> dict:
    results = []
    for case in cases:
        called: list[str] = []
        t0 = time.time()
        err = ""
        reply = ""
        try:
            r = service.run_chat(conn, cfg, "eval", None, case["q"],
                                 on_event=lambda ev: called.append(ev["name"]) if ev.get("type") == "tool_start" else None)
            reply = r["reply"]
            tokens = r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"]
        except Exception as e:  # noqa: BLE001
            err = str(e)[:200]
            tokens = 0
        ok = (not err) and judge(case, called)
        results.append({"id": case["id"], "ok": ok, "tools": called, "seconds": round(time.time() - t0, 1),
                        "tokens": tokens, "error": err, "reply": reply[:300]})
        print(f"{'✓' if ok else '✗'} {case['id']:<18} {results[-1]['seconds']:>5}s  {','.join(called) or '-'}"
              f"{'  ERR ' + err if err else ''}", file=out, flush=True)
    passed = sum(r["ok"] for r in results)
    summary = {"model": cfg.model, "passed": passed, "total": len(results),
               "rate": round(passed / len(results), 3) if results else 0,
               "avg_seconds": round(sum(r["seconds"] for r in results) / max(1, len(results)), 1),
               "total_tokens": sum(r["tokens"] for r in results), "results": results}
    print(f"\n模型 {cfg.model}：通过 {passed}/{len(results)}（{summary['rate']:.0%}），"
          f"平均 {summary['avg_seconds']}s/题，共 {summary['total_tokens']} tokens", file=out)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_db = Path(os.environ.get("MATERIAL_FLOW_DATA", "/srv/material-flow/data")) / "material_flow.db"
    ap.add_argument("--db", default=str(default_db))
    ap.add_argument("--only", default="")
    ap.add_argument("--report", default="")
    args = ap.parse_args(argv)

    cases = load_cases()
    if args.only:
        want = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in want]
    with tempfile.TemporaryDirectory() as td:
        copy = Path(td) / "eval.db"
        src = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        dst = sqlite3.connect(copy)
        src.backup(dst)
        src.close()
        cfg = _resolve_cfg(dst)
        summary = run_eval(dst, cfg, cases)
        dst.close()
    if args.report:
        Path(args.report).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
