#!/usr/bin/env python3
"""P0-1 门禁：禁止 emoji 作为 UI 功能图标。

## 为什么需要分类判定

朴素做法是全仓扫 emoji 并报错，但本项目里 emoji 字符合法地存在于两类
**非 UI 图标**的位置，一刀切会导致门禁无法通过：

1. **数据层符号契约** —— `Models.kt` / `WorkspaceScreen.kt` 把业务状态映射为
   符号字符串，服务端契约测试（`WorkspaceApiParserTest`）直接断言这些值。
   它们是数据，不是渲染结果。
2. **翻译表与说明性注释** —— `LogisticsIcons.fromSymbol` 的 `when` 分支
   必须列出待翻译的字符；文档注释需要举例说明禁止什么。

因此本脚本只阻断**真正把 emoji 渲染到界面**的写法。

## 判定规则

- 命中 emoji 正则的**代码行**（非注释）：
  - 若该行是 `LogisticsIcons.fromSymbol` 翻译表的 `when` 分支 → 放行
  - 若该行是数据层符号映射（所在函数返回 String 且被 StatusTag 消费）→ 放行
  - 若该行是传给 `StatusTag(symbol = ...)` 的实参 → 放行
  - 其余（出现在 `Text(` / 字符串字面量直接渲染）→ **阻断**

- 命中 emoji 的**注释行**一律放行（说明性内容）。
- 测试源码里的断言放行（数据契约）。

## 用法

    python3 scripts/check_emoji_icons.py            # 全仓检查
    python3 scripts/check_emoji_icons.py --verbose  # 打印放行明细

退出码：0 = 通过，1 = 发现违规。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "\uFE00-\uFE0F"
    "\U0001F000-\U0001F02F"
    "\U0001F0A0-\U0001F0FF"
    "\U0001F100-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\u200D"
    "\u20E3"
    "\U000E0020-\U000E007F"
    "]"
)

# 项目源码根（Android 生产代码）
SRC_ROOT = Path("logistics-android/app/src/main/java")

# 允许 emoji 出现的文件：翻译表所在文件（其 when 分支必须列出字符）
TRANSLATION_TABLE_FILES = {
    "ScannerComponents.kt",  # LogisticsIcons.fromSymbol
}

# 数据层符号映射文件：这些文件返回的是契约符号字符串，交由渲染层翻译
DATA_LAYER_SYMBOL_FILES = {
    "Models.kt",
    "WorkspaceScreen.kt",
    "LogisticsViewModel.kt",
    "ApprovalAndProfileScreens.kt",  # requestStatusSymbol()
}

# 注释行判定
COMMENT_MARKERS = ("//", "*", "/*")


def is_comment(line: str) -> bool:
    s = line.strip()
    return s.startswith(COMMENT_MARKERS)


def is_translation_branch(line: str, fname: str) -> bool:
    """`LogisticsIcons.fromSymbol` 的 when 分支： "✓" -> Check, """
    if fname not in TRANSLATION_TABLE_FILES:
        return False
    s = line.strip()
    # 形如 "X" -> Something,
    return bool(re.match(r'^"[^"]*"\s*->\s*\w+', s))


def is_status_tag_arg(line: str) -> bool:
    """传给 StatusTag(symbol = ...) 的实参，或 symbol = ... 赋值。"""
    s = line.strip()
    return bool(re.match(r"^(symbol\s*=|symbol\s*=\s*if)", s)) or "symbol = " in s


def is_enum_or_mapping(line: str, fname: str) -> bool:
    """数据层的枚举值 / 状态映射条目。"""
    if fname not in DATA_LAYER_SYMBOL_FILES:
        return False
    s = line.strip()
    # 枚举构造： APPROVAL("审批", "✓", Screen.APPROVAL),
    if re.match(r'^\w+\("', s):
        return True
    # when 分支： XXX -> "✓"  或  "A", "B" -> "✓"
    if re.search(r'->\s*"[^"]*"\s*,?\s*$', s):
        return True
    return False


def scan_file(path: Path, verbose: bool) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    allowed: list[tuple[int, str, str]] = []
    fname = path.name
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"  [warn] 无法读取 {path}: {exc}", file=sys.stderr)
        return violations

    for no, line in enumerate(lines, 1):
        if not EMOJI_RE.search(line):
            continue
        if is_comment(line):
            allowed.append((no, line, "注释"))
            continue
        if is_translation_branch(line, fname):
            allowed.append((no, line, "翻译表分支"))
            continue
        if is_enum_or_mapping(line, fname):
            allowed.append((no, line, "数据层契约符号"))
            continue
        if is_status_tag_arg(line):
            allowed.append((no, line, "StatusTag 实参（渲染层已翻译）"))
            continue
        violations.append((no, line))

    if verbose and allowed:
        for no, line, reason in allowed:
            print(f"    [pass] {fname}:{no}  ({reason})")
            print(f"           {line.strip()[:100]}")
    return violations


def main() -> int:
    ap = argparse.ArgumentParser(description="P0-1 emoji 图标门禁")
    ap.add_argument("--verbose", "-v", action="store_true", help="打印放行明细")
    ap.add_argument("--root", default=str(SRC_ROOT), help="扫描根目录")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"[error] 扫描根不存在：{root}", file=sys.stderr)
        return 1

    print(f"P0-1 门禁扫描：{root}")
    total_files = 0
    total_violations = 0

    for kt in sorted(root.rglob("*.kt")):
        total_files += 1
        violations = scan_file(kt, args.verbose)
        if violations:
            total_violations += len(violations)
            rel = kt.relative_to(root)
            print(f"\n  [FAIL] {rel}")
            for no, line in violations:
                print(f"    {no}: {line.strip()[:110]}")

    print()
    print(f"扫描 {total_files} 个 .kt 文件")
    if total_violations:
        print(f"结果：FAIL —— 发现 {total_violations} 处 emoji 直接渲染为 UI 图标")
        print("修法：改用 LogisticsIcons 中对应的矢量图标（见 ui/components/ScannerComponents.kt）")
        return 1
    print("结果：PASS —— 无 emoji 直接渲染为 UI 图标")
    return 0


if __name__ == "__main__":
    sys.exit(main())
