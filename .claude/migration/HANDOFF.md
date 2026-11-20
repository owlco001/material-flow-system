# Handoff — 第六轮三Agent并发集成完成

## What changed
基线815b4a9，本地分支fix-deep-audit-138ff95。三路实现：main.py流转多订单精确筛选；Android专用每批20/每页100读取及完整性保护；机台4分区同job渐进加载与跨刷新共享Semaphore4。Lead统一接口/构建/复核/版本。后端0.22.4，Android0.5.57/64。

## What is now true
后端完整602 passed（302.81s）；Android307项/54类零失败/错误/跳过，Debug构建成功1m3s。新流转scope15项、并发7项和原20项机台回归全绿。初始Android11测10红、交叉复核4边界红后修复。合成旧10页1000条599119bytes→新1页2条1302bytes。真实Room SQL、75文件图标和所有guard通过。原listTransferRequests(status)保留，不将机台scope改动宣传为全局列表迁移。

## What remains
旧服务端忽略筛选时仍可能各批全量下载；旧无筛选流转方法的50页上限另列后续。单document_no查询仍可能SCAN，未盲加索引。HTTP多页/多批无事务快照；不能识别所有等量替换。离线owner政策、旧独立脚本基线失败、真机/加密磁盘/厂商后台/U9/远端CI限制保留。

## What blocks shipping right now
本地可审查，不宣称完整生产验收。未push、未部署、未发布APK，未连接测试服务器。服务器最后已知仍0.22.2/d40aad6，勿视为本轮已上线。

## Exact next steps
审阅docs/deep-audit-round6.md及新增量补丁。复验后端pytest、Android testDebugUnitTest/assembleDebug与Room/图标/guard；需要部署或APK发布时由用户另行明确授权。继续优化优先对全局旧流转读取做独立迁移，或量化document_no复合索引收益。

---

# Handoff — 第五轮机台响应隔离与完整分页

## What changed
基线32fa40b；本地分支fix-deep-audit-138ff95。Android0.5.56/63，后端保持0.22.3无源码变化。机台详情与聚合同job，session/request与路由归属双校验，登录/登出/401/角色预览及直接订单/扫码导航失效旧任务。任务、工时读完整20行分页，异常/总数变化提示不完整。

## What is now true
最初8项红测与独立复核补充3项红测已转绿。最终Android285项/52类零失败，新增20项，Debug构建成功55s。后端相关契约11项复验通过；后端完整598项沿用第四轮已验基线，不是本轮重跑。真实Room SQL、75文件图标及全部guard通过。测试依赖新增coroutines-test1.8.1，不新增生产依赖。

## What remains
流转全量下载/5000条上限、聚合串行、机台master/orderDevice标识历史差异单列后续。多次HTTP分页无事务快照；检测count变化和重复页不能识别所有等量替换。离线owner政策、旧独立脚本基线失败和真机/后台调度/U9/远端CI限制保留。

## What blocks shipping right now
本轮仅本地交付可供审查，未推送/部署/发布APK，未连接测试服务器。物理设备与生产验收未完成。服务器最后已知仍为0.22.2/d40aad6，勿将本轮当已部署。

## Exact next steps
审阅docs/deep-audit-round5.md及本地增量补丁；复验Android testDebugUnitTest/assembleDebug、Room/图标/guard。需要继续优化时单独处理机台流转范围筛选；推送/部署/APK发布需另行明确授权。

---

# Handoff — 第四轮机台物料优化完成

## What changed
基线d40aad6，当前本地分支fix-deep-audit-138ff95。新增可选deviceNo物料范围，四组查询同口径；Android专用每页200读取全部目标机台页，原订单API覆盖方法不变。后端0.22.3，Android0.5.55/62。

## What is now true
冻结后端598 passed，Android265项/51类零失败，Debug构建成功；新loopback机台7项全绿。真实Room SQL、图标75文件和guard/diff通过。合成20k→200行，7708617→129585字节，137.268→3.514ms，仅handler基准。独立复核发现并修复历史导入长编号兼容问题。

## What remains
旧队列owner迁移策略、旧独立脚本基线失败、真机/加密磁盘/后台调度/U9/远端CI限制仍保留。旧服务端忽略筛选时正确性兼容但没有新性能收益。机台其他聚合请求与既有生命周期不在本切片重构。

## Shipping blockers
本轮代码可审查，完整生产验收未做。未推送、未部署、未发布APK。先前测试环境仍是0.22.2/d40aad6，不应声称已上线本轮筛选。

## Exact next steps
审查docs/deep-audit-round4.md及本地增量补丁。重验后端pytest和Android testDebugUnitTest/assembleDebug；经用户明确授权再推送/部署/发布APK。

---

# Handoff — 2026-11-20 第三轮修复完成

## What changed
基线53b1b67，分支fix-deep-audit-138ff95。后端之前第二轮完成会话epoch/取消claim、工时报表、工作台与Web分页；第三轮修复同ViewModel订单缓存跨账号/预览泄露、非选中详情响应覆盖、缓存切回丢material-status分页/汇总、同订单旧append与迟到错误。后端0.22.2，Android0.5.54/61。报告docs/deep-audit-fixes.md与docs/deep-audit-round3.md。已本地提交，不推送/部署。

## What is now true
第三轮订单正确不变量7项通过。Android最终全套258项/50类，0失败/0错误/0跳过，testDebugUnitTest+assembleDebug成功。后端593项全绿沿用第二轮最终测试（第三轮无后端运行代码变化）。Room原生取消CAS、75文件图标、guard、diff-check通过。

## What remains
队列持久化owner仍不做隐式迁移；Android加密磁盘、厂商后台调度、真机UI/摄像头、生产U9及远端CI未验收。旧独立脚本与原始基线相同：auth19/19，contract5/26，order status28/35，token lifecycle24/33，后三项为既有失败。

## What blocks shipping right now
本地代码和自动回归可供审查；不能宣称完成生产发布验收。现场、远端CI及队列owner政策仍未处理。没有推送或部署授权。

## Exact next steps
1. 审阅docs/deep-audit-fixes.md和docs/deep-audit-round3.md及本地提交。
2. 如需复验，后端PYTHONPATH="$PWD" /path/to/venv/bin/python -m pytest tests/ -q -p no:cacheprovider；Android运行testDebugUnitTest+assembleDebug。
3. 如用户明确授权，再推送并建立PR执行远端CI；部署前完成现场验收。

---

# Handoff — 2026-11-19（历史已交付）

## What changed

基于main b0719b2，在独立副本/tmp/material-flow-optimization的optimize-function-performance分支完成五个切片：流转快照与库存一致性、库存排序索引、删除装配列表遮蔽路由、离线多批与后台退避、盘点列表响应世代隔离。后端0.22.1，Android0.5.52／59。增加行为测试、Room真实生成SQL检查和CI门禁。所有路径登记MAP.csv，原始/root/mf-main保持不变。未推送／未部署。

## What is now true

- 后端冻结后全量560 passed（657.82s），基线542 passed。
- Android全量234项／45类，0失败、0错误、0跳过；Debug构建成功（4m21s），基线220项。
- 真实Room生产DAO生成SQL已实跑检查：到期前置过滤、后续批次、退避次数／时间持久化、终态不重试、创建快照均PASS。
- 图标门禁75文件PASS，优化guard和git diff --check通过。
- 库存20,004条合成数据的第一页排序查询中位数：全量2.8175→0.0359ms，精确分类2.3623→0.0384ms，均消除临时排序，返回数据一致。不是整页或生产响应耗时。
- 装配有效列表原已批量化；清理后唯一GET入口，1／20条实测均7次相关SELECT，没有虚报42→3收益。

## What remains

旧独立脚本对照原版／优化版完全相同：auth19/19，contract5/26，order status28/35，token lifecycle24/33；后三者既有失败需单独维护，未修改认证政策来迁就旧脚本。队列持久化账号归属缺失需维护者另定迁移政策；本轮仅防运行中会话切换继续启动旧批次，不宣称完整账号隔离。分类树前缀查询、装配工时逐任务请求可作为后续独立性能课题。

## What blocks shipping right now

本轮代码可供审查；不能宣称完整生产发布验收通过：三个旧独立脚本仍有基线失败，真机摄像头／厂商后台调度／实际UI／U9生产链路未验收，远端CI尚未运行。没有自动推送或部署权限。已知队列账号归属风险见DECISIONS D6。

## Exact next steps

1. 审阅docs/optimization-report.md、代码差异和本地补丁。
2. 重验：后端目录运行`PYTHONPATH="$PWD" /path/to/venv/bin/python -m pytest tests/ -q -p no:cacheprovider`。
3. Android目录运行`ANDROID_HOME=/opt/android-sdk ./gradlew --no-daemon :app:testDebugUnitTest :app:assembleDebug`；根目录运行`python3 scripts/check_offline_room_queries.py`与`bash .claude/migration/guard.sh`。
4. 若获得明确授权，再将本地优化分支推送GitHub并建立PR运行远端CI。部署前做现场验收；不要直接执行NEXT.md中的历史发布指令。
