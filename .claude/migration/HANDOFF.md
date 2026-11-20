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
- Android全量234项／45类，0失败、0错误、0跳过；Debug构建成功（4m21s），基线219项。
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
