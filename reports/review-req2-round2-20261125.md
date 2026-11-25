# 复审 · 需求 2 修复提交 `c502a29`

- 仓库：`/root/mf-req2`　分支：`feature/kitting-shortage`　范围：`git diff bb27f14..c502a29`（5 文件 +582/−169）
- 方式：只读。逐行读 diff 与当前工作树；`/opt/smart-factory/data/material_flow.db` 以 `mode=ro` 直接调 `_kitting_dataset` / `_kitting_export_sheets` 复算 7 个项目；跑 `tests/test_kitting_shortage.py` + `tests/test_project_material_requirements.py`（37 passed）与 4 个相邻回归文件（12 passed）。未启动服务、未改仓库源码/测试/文档、未做 git 写操作。

## 结论：**仍有问题**（新发现 P1×1、P2×3）

上一轮两个 P1 与 P2-1/P2-3 **已正确修复、无回归**，测试也补上了。但 C 方案把「备料缺口」搬进缺料清单后，**单料号的备料缺口列只累加采购缺口>0 的行**，与页面图例「含已到货未入库的行」直接矛盾，真实数据上把 500 件说成 180 件（26B-013，项目级 1543 vs 清单列 538）。这是仓库照单备料就会少备的错数，建议修完再合（改动约 5 行）。

---

## 一、必须逐项结论

### 1. P1-1 搜索 403 —— 已修复

| 检查点 | 结论 |
| --- | --- |
| `_pmr_search_projects` 是否不鉴权 | 是。`main.py:5185-5219`，无 `_pmr_authorize`；docstring 明写「可见性由调用方负责」 |
| API 是否仍鉴权 | 是。`list_projects` 仍先 `_pmr_authorize(user)`（`main.py:5232`）再调 helper；SQL/排序/limit/ASSEMBLER 分支与原实现逐字相同（只把 `\` 转义写法保留），响应字段与 `count` 语义未变 |
| admin 调用处是否捕获异常 | 是。`admin_web.py:1944-1950` 包在 `try/except ApiError` 里并落 `error=`（helper 目前不抛，属防御性，可接受） |
| WAREHOUSE 能否搜索 | 能。`test_warehouse_admin_keyword_search_lists_candidates`（tests:617-641）断言 200、`text/html`、出现候选与 `project_no=JSBY-26B-201`、不出现 403 文案 |
| 是否仍不能访需求 1 | 是。同用例断言 `/material-requirements` 仍 403；`tests/test_project_material_requirements.py:468` 断言 `/api/v1/projects` 对仓库仍 403（两个用例合起来才覆盖 API 那一半，单看 kitting 用例抓不到「顺手把 API 鉴权删了」） |

补充：`_pmr_search_projects` 目前只有两个调用点（`main.py:5235`、`admin_web.py:1948`），后者所在页有 `KITTING_WEB_ROLES` 门槛，没有第二处无门槛入口。

### 2. P1-2 C 方案双口径 —— 口径正确，但清单列的口径与图例不符（见 P1-新）

| 检查点 | 结论 |
| --- | --- |
| `_kitting_procure_gap` = 需求−已到货 | 正确，`main.py:5409-5415` |
| `_kitting_prepare_gap` = 需求−在库 | 正确，`main.py:5418-5424` |
| 齐套行 = 采购缺口==0（arrived≥required） | 正确，`main.py:5501-5511`：`procure_gap>0` 进缺料并 `return`，否则 `kittedLineCount+=1` |
| `statusAnomalyLineCount` 只作子集 | 正确，`main.py:5511-5513` 只在「已算齐套且状态 ∉ (ARRIVED,IN_STOCK)」时 +1，不参与加法 |
| 恒等式 需求行 = 齐套 + 缺料 | 成立。真实库 7 个项目全部 `identity=True`（含 26B-012 的 20,552 行）；导出逐行断言亦过 |
| 已到货未入库行不进缺料清单 | 是。`A-ARRIVED-FULL` 用例断言整份响应无该料号；26B-013 清单只剩 4 个料号 |
| 但计入备料缺口合计 | 是。26B-013 `totalPrepareGapQuantity=1543`，而清单列合计 538，差 1005 恰为「已到货未入库」——项目级正确 |

**问题在单料号列**（P1-新）：清单每行的 `prepareGapQuantity` 由 `_kitting_merge_shortages` 只累加 `short_rows`（采购缺口>0 的行，`main.py:5570`），而一个料号在本项目里常有「已全量到货的行」被排除在外。实测 26B-013 料号 `30403004-00137`：25 行×20 件，其中 9 行未到货（采购缺口 180），**25 行 `in_stock` 全为 0**：

```
清单行：采购缺口 180、备料缺口 180、影响机台 9 台
真实：  采购缺口 180、备料缺口 500（25 行×20，全部未入库）
```

同项目 `30406002-00067`：清单 180 / 实际 513；`30403001-00001`：162 / 514。页面图例（`project_kitting.html:80`）明写备料缺口「含已到货未入库的行」，导出/API 的列名也是「备料缺口」，与数据不符；`sort=prepare` 又按这个低估值排序，仓库照单备料会少备 320 件。文档把 1543 vs 538 解释成「差额即已到货未入库」（`docs/kitting-shortage.md:60-64`），实际是这个漏算造成的。

### 3. 移除 `shortageQuantity` / `zeroGapLineCount` / `totalShortageQuantity` —— 无外部引用

- 全仓 grep（排除 `reports/`、`.venv`）：仅剩 `main.py:3443/3454/3490/3500` 与 `tests/test_order_material_summary.py` —— 那是需求 1 `/api/v1/orders/material-status` 自己的字段，本次未动（该用例通过）。
- 管理台模板、`project_kitting.html`、导出、`admin_web.py` 已全部改用 `procureGapQuantity/prepareGapQuantity/procureGapTotal`，无残留引用；仓库内无 JS / App / 其它模块消费齐套接口。
- 属破坏性字段变更，但该模块 0.24.0 尚未发布、无外部消费者，可接受；文档已注明移除原因。

### 4. P2-1 / P2-2 / P2-3

- **P2-1 到位**：导出「齐套概览」新增「状态异常行数」（`main.py:5797/5811`），13 列表头/列宽/行宽一致；项目合计与每台机台逐行满足 `需求行 = 齐套 + 缺料`（测试对 8 行全断言，真实 26B-012 的 120 行 0 违例）；空项目齐套率写 `""`。
- **P2-2 基本到位**：料号过滤下沉到 `_kitting_dataset` 行循环（`main.py:5633-5636`），概览/清单/导出同作用域；测试断言过滤后概览 18 = 清单列和 18、导出两 sheet 也 18/18，真实数据 `totalProcureGapQuantity == procureGapTotal` 全部相等。**残留**：`unmappedRequirementCount`（`main.py:5681-5686`）不带料号过滤，页面 `:84` 却写「过滤同时作用于上方概览」。
- **P2-3 到位**：`projectDeviceCount` 对 ASSEMBLER 返回 `None`（`main.py:5676-5677`），`kitting-summary` 用例断言为 `None`；模板 `:59` 改为 `dataset.deviceFilter` 优先、「无过滤且角色为 ASSEMBLER」次之，管理员按机台过滤不再显示「其余不在你的权限内」（用例 `test_admin_device_filter_hint_is_not_permission_limit` 过）。注：`user.role == "ASSEMBLER"` 这支在管理台不可达（`_kitting_web_user` 直接 403），属无害死代码。

### 5. 新测试质量 —— 断言够硬，但漏了「同料号混合行」这一形状

抽查：
- `test_summary_counts_rate_and_device_grouping`：15=2+13、rate 13.3、`totalProcureGapQuantity==96` 与 `totalPrepareGapQuantity==98` 并列断言、显式 `"zeroGapLineCount" not in summary`、恒等式显式断言、`statusCounts` 精确值 —— 强。
- `test_arrived_full_line_is_kitted_and_not_short`：计数四元组 (2,1,1,0)、rate 50.0、采购 3/备料 10、清单只剩 A-PARTIAL、整份响应无 A-ARRIVED-FULL、导出两个 sheet 单元格 —— 强，正是 P1-2 的锁。
- `test_export_content_types_matches_zip_parts`：Override→part 存在、sheet 数一致、rId 能在 rels 解析 —— 强。
- `test_shortage_list_sort_switches_metric`：两种排序的完整序列 + 集合相等 + 422 —— 强。

无 skip、无 `pytest.raises` 放水、没有把期望值改成实现值。上轮 4 个缺口①仓库搜索 ②arrived==required&in_stock==0 ③空项目导出+合计行 ④恒等式 全部覆盖；⑤（`unmapped>0` / `assembly_task_members` 路径）仍未覆盖。**新缺口**：没有「同一料号既有全到货行又有缺料行」的用例（26B-013 的形状），所以上面那个 P1 逃过了。

瑕疵（不影响结论）：`tests/test_kitting_shortage.py:330-362` 的 `assert` 被 dedent 出 `with` 块，语义正确但结构乱；`test_admin_device_filter_hint_is_not_permission_limit` 里「其余不在你的权限内」现在模板全局已删，该断言恒真（靠正向断言「已按机台 P1 过滤」兜住）。

### 6. 回归 —— 未发现

- `tests/test_kitting_shortage.py` + `tests/test_project_material_requirements.py`：**37 passed**（51s）。
- `test_admin_web_nav.py` / `test_admin_web_exports.py` / `test_order_material_summary.py` / `test_material_status_paging.py`：**12 passed**。
- diff 只触及齐套段 + `list_projects` 抽取；需求 1 的 `/material-requirements`、`/export`、`/api/v1/orders/material-status` 代码零改动，行为等价（搜索 SQL/排序/limit/`count` 逐字保持）。

---

## 二、新发现（按严重度）

### P1-新 缺料清单「备料缺口」列只算采购缺口>0 的行，与图例矛盾且单料号低估

- **文件:行**：`app/main.py:5570`（合并时 `group["prepareGapQuantity"] += row["prepareGapQuantity"]`，而 `row` 只来自 `short_rows`）；相关：`app/main.py:5504-5505`（只有缺料摘要才带 prepare gap）、`app/templates/project_kitting.html:80`（图例「含已到货未入库的行」）、`app/main.py:5705`（`prepareGapListTotal`）、`docs/kitting-shortage.md:60-64`（把差额解释成正常口径）。
- **实测**：26B-013 `30403004-00137` 25 行×20，9 行未到货、25 行在库为 0 → 清单显示采购 180/备料 180，真实备料缺口 500；项目 `totalPrepareGapQuantity=1543` vs `prepareGapListTotal=538`。26B-012 因在库≈到货未暴露。
- **影响**：仓库按清单列备料少备（本料号少 320 件）；`sort=prepare` 排序基于低估值；概览卡片 1543 与清单列 538 同屏并存，正是 P2-2 想消灭的那类矛盾换了个字段。
- **修法（推荐 A）**：
  - A：在 `_kitting_dataset` 行循环里另建 `prepare_by_material[code] += _kitting_prepare_gap(row)`（对**全部**行累计，不受采购缺口限制），`_kitting_merge_shortages` 合并完成后用它覆盖 `group["prepareGapQuantity"]`；此时 `prepareGapListTotal` 与 `totalPrepareGapQuantity` 相等，可删 `listPrepareGapQuantity` 与文档里的差额解释；补一条「同料号既有一行全到货、一行缺料」的用例（26B-013 形状）。
  - B（最小改动，必须同时改文案）：列名改「缺料行备料缺口」，图例删「含已到货未入库的行」，并在表头注明「全量备料缺口看上方卡片」，`docs` 同步。
- **判断**：A 更符合图例与仓库用法，改动约 5 行 + 1 条用例。

### P2-a `unmappedRequirementCount` 不受料号过滤，与「过滤同时作用于概览」文案不一致

- **文件:行**：`app/main.py:5681-5686`（SQL 无 `material_code` 条件）vs `app/templates/project_kitting.html:60`（计数）、`:84`（过滤说明）、`docs/kitting-shortage.md:206-209`、`:211-213`。
- **修法**：SQL join `materials` 后加 `AND m.code LIKE ? ESCAPE '\'`；或过滤时页面隐藏该计数并文档注明「项目级、不受过滤影响」。

### P2-b `sort=prepare` 时页脚仍只显示「采购缺口合计」

- **文件:行**：`app/templates/project_kitting.html:138`。
- **修法**：按当前排序显示对应合计（`dataset.procureGapTotal` / `dataset.prepareGapListTotal`），或两个都显示；否则「已按备料缺口降序」下面跟一个采购合计，读数容易被当成排序依据。

### P2-c 测试补口令

- 补「同料号混合行」用例（锁 P1-新）；补上轮缺口⑤（`unmappedRequirementCount>0`、`assembly_task_members` 协作成员）；顺手把 `tests:330-362` 的 assert 收回 `with` 块。

---

## 三、验证记录（可复现）

```bash
cd /root/mf-req2/material-flow-backend
env -u PYTHONPATH -u PYTHONHOME ./.venv/bin/python -m pytest \
  tests/test_kitting_shortage.py tests/test_project_material_requirements.py -q -p no:cacheprovider
# 37 passed, 3 warnings in 51.08s

env -u PYTHONPATH -u PYTHONHOME ./.venv/bin/python -m pytest \
  tests/test_admin_web_nav.py tests/test_admin_web_exports.py \
  tests/test_order_material_summary.py tests/test_material_status_paging.py -q -p no:cacheprovider
# 12 passed in 8.71s
```

真实库（只读连接直接调 `_kitting_dataset`，ADMIN 视角）：

```
26B-012   lines=20552 kit=139  short=20413 anom=0 rate=0.7  proc=239132 prep=239132 items=2750 ident=True 0.69s
26B-013   lines=90    kit=61   short=29    anom=0 rate=67.8 proc=531    prep=1543   items=4    ident=True
MF_TEST_ORDER_02 lines=8 kit=8 short=0 anom=0 rate=100.0 proc=0 prep=11 items=0 ident=True
（MF_TEST_ORDER_01/03、TEST-3D-01、TEST-20261009-001 均 ident=True；空项目 rate=None）
导出：概览 13 列/行宽一致，26B-013 与 26B-012 全部行 需求=齐套+缺料，0 违例
```

P1-新 的算术证据（26B-013，料号 `30403004-00137`）：25 行 req=20、arr=0/20、stk=0 → 采购缺口 180（9 行）、备料缺口 500（25 行）；清单行显示 180/180。

---

## 四、合并建议

- P1-1、P1-2（判定口径）、P2-1、P2-3：**通过**，实现与测试都对，无回归。
- **P1-新**建议合并前修（约 5 行 + 1 条用例）；P2-a/P2-b 可同批顺手改。
- 修完只需重跑 `test_kitting_shortage.py` 与真实库 26B-013 复算，不需要全量。
