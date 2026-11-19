# 功能与性能优化报告

日期：2026-11-19。基线：`b0719b2`，本地分支：`optimize-function-performance`。

## 交付范围

本轮是五个有明确验收条件的修复切片，不代表全仓审计完毕。修改在独立本地副本完成，没有推送远端、操作生产数据库或部署。后端版本0.22.1，Android版本0.5.52／59。

### 1. 流转库存一致性

旧实现逐行读取库存、比较expectedInventoryVersion后递增版本。同一单据同料多库位的两行通常携带同一快照版本，第二行会与第一行产生的版本变化冲突；反而给第二行传入虚构的递增版本会被错误接受。

现实现先按每个物料的事务起始版本校验所有行，再按物料合并汇总变更并递增一次版本，最后按原始明细顺序调整各库位。累计库存不足、任一库位不足、真正旧版本、物料不存在时整单回滚；保留审批人与执行人隔离及幂等记录。

另一个已复现问题是通用流转执行将STOCKTAKE落入出库扣减分支，却不改库位，导致汇总／明细漂移。现在该入口明确拒绝STOCKTAKE及未知类型，盘点仍通过专用确认接口按差异调整。没有把盘点单的quantity擅自解释为实盘数量。

新增13个事务行为回归：修复前9失败／4通过，修复后13通过。加上既有盘点与批量操作，局部25项全通过。

### 2. 库存列表的真实排序性能

仓库默认按available_quantity、code升序，以缺料／低库存优先。原库缺少匹配排序索引，每页需扫描物料并创建临时排序树。新增：

- `idx_materials_available_code(available_quantity, code)`。
- `idx_materials_category_available_code(category, available_quantity, code)`，覆盖精确分类筛选。

由现有幂等启动迁移补齐，旧库缺列仍跳过。没有改变筛选、计数、库存状态、排序或分页字段，没有额外依赖。

同一SQLite连接、20,004条合成物料、第一页20条、11次重复取中位数的实测结果：

| 场景 | 原排序查询 | 加索引后 | 查询计划 |
|---|---:|---:|---|
| 默认库存列表 | 2.8175 ms | 0.0359 ms | 全表扫描＋临时排序 → 顺序索引扫描 |
| 精确分类列表 | 2.3623 ms | 0.0384 ms | 全表扫描＋临时排序 → 分类组合索引查找 |

这是选取列表数据的SQL耗时，不含HTTP、权限、计数与模板渲染，不能据此外推整页或生产环境加速倍数。新增索引增加一定存储与写入成本；排序测试以确定性的查询计划／结果一致性作断言，不用易波动的耗时阈值。

### 3. 装配列表路由收敛

初步静态审计曾将assembly_routes.py内逐条成员查询视为有效热点；实际HTTP回归发现main.py提前注册的同路径接口已经批量查询。后注册函数被遮蔽，不能声称优化使42次查询降到3次。

因此删除被遮蔽的重复GET注册，保留其他成员变更逻辑。行为测试锁定唯一入口、DESC分页、可见性和OPERATOR拒绝访问；有效接口在1项／20项的实测相关SELECT均为7条。本项收益是消除死路由与维护歧义，不是新增查询性能提升。

### 4. 离线积压重放

旧队列DAO只取最早20条，Repository只处理一次，导致第21条之后缺少后续触发；若最早20条都在退避，后续到期项也会被挡住。

现在在SQL中先筛选到期项、再LIMIT分批读取，排空本轮快照中的到期操作；维持幂等键、冲突／终态和逐项退避。同步互斥及会话代际校验避免同一Repository重叠排空和会话变化后继续启动旧批次。队列不做破坏性Room schema迁移。

测试使用回环HTTP服务与内存DAO，覆盖47条积压、旧退避项不阻挡后续到期项、失败跨批退避、重叠同步去重、本轮新入队留待下轮，以及会话变化场景。后台Worker现在只要仍有PENDING（含未到期退避）就保持重试，不再被同轮冲突短路；FAILED／CONFLICT终态本身不触发无效重试。

生成代码复核还发现Room对带默认方法体的@Query只调用DefaultImpls，并未生成预期SQL；已有scheduleRetry也因此没有真实持久化重试时间。生产数据库现返回独立的RoomOfflineOperationDao，对pendingDue／hasPending／scheduleRetry声明抽象覆盖，保留测试接口默认体但强制生成生产SQL，不变更表结构。新增scripts/check_offline_room_queries.py读取实际KSP生成SQL，并在隔离SQLite中验证到期前置过滤、后续批次、退避次数／时间持久化、终态不调度与创建快照。已实跑PASS，并接入CI；不以fake通过替代生产查询验证。

### 5. 盘点列表刷新／分页一致性

新增可控Deferred响应测试，原实现6个场景中5个失败：旧追加页能混入新第一页、旧失败能覆盖新内容／清除新加载状态、刷新期间额外发分页、退出登录后旧响应回填。

现刷新产生新世代；第一页与追加页的成功／失败响应均检查会话及世代。刷新期间禁止追加，发起追加前同步占位防连续点击；当前追加失败保留列表与页码供重试。没有改变UI结构或后端契约。

## 验证记录

- 优化前完整后端：542 passed（660.52s）；Android：219项／43个测试类，BUILD SUCCESSFUL。
- 优化后后端完整测试：560 passed；冻结后的最终重跑560 passed（657.82s，0失败）。
- 新增库存排序3项＋装配入口／查询预算2项：5 passed。
- Android最终完整单测：234项／45个测试类，0失败、0错误、0跳过；含离线队列9项、盘点竞态6项。最终testDebugUnitTest＋assembleDebug为BUILD SUCCESSFUL（4m21s）。
- 真实Room生成SQL行为检查PASS；图标门禁扫描75个Kotlin文件PASS；优化guard与git diff --check均通过。后端与Android CI已加入相应门禁。
- 4个旧独立脚本已在未修改基线及优化版本逐个对照：auth 19/19，contract 5/26，order material status 28/35，token lifecycle 24/33；退出码及通过数完全一致。后三项原本就失败，主要涉及旧验收未适配首次改密策略。运行须设置`PYTHONPATH`为后端目录。未修改产品认证策略来迎合旧脚本。

## 复核与限制

- 未验证真机摄像头、实际UI渲染、后台WorkManager在特定厂商系统上的调度、生产U9与网络链路；JVM测试及Debug构建不能替代这些验收。
- 既有离线队列缺少持久化账号归属。完整隔离需要另定旧记录归属与Room迁移政策，本轮不实施，也不声称已解决。建议现场继续限制共享设备切换账号前的积压队列处理，后续单列迁移任务。
- Android装配工时逐任务请求仍可另行优化；需要批量接口或生命周期内限流设计，本轮未扩大范围。
- 旧独立脚本的基线失败仍在，故不将本地代码回归通过表述为所有发布验收通过。

## 可复制的主要验证命令

```bash
cd material-flow-backend
PYTHONPATH="$PWD" /path/to/venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

```bash
cd logistics-android
ANDROID_HOME=/opt/android-sdk ./gradlew --no-daemon :app:testDebugUnitTest :app:assembleDebug
```

```bash
# 仓库根目录
python3 scripts/check_emoji_icons.py
python3 scripts/check_offline_room_queries.py
bash .claude/migration/guard.sh
git diff --check
```

测试／发布门禁、切片、文件清单与决策在`.claude/migration/`；下次接续先读HANDOFF.md和CHARTER.md。
