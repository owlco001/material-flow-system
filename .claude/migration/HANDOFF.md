# Handoff — 2026-11-20 第二轮修复完成

## What changed
基线138ff95，分支fix-deep-audit-138ff95。完成API/Repository身份epoch与提交ticket、取消claim恢复及取消不入队、装配分页与工时结构化生命周期、工时报表集合查询、工作台轻量筛选、Web物料真实分页和重复车间路由清理。后端0.22.2，App0.5.53/60。代码与报告在本地；未推送／部署。报告见docs/deep-audit-fixes.md。

## What is now true
后端最终593 passed（571.08s）；Android251项／49类零失败、零错误、零跳过，testDebugUnitTest＋assembleDebug成功（2m18s）。新增17项正确不变量通过。真实Room生成SQL验证单ID/状态CAS、不影响其他claim与已完成记录、保留退避元数据；图标75文件与优化guard通过。独立复核发现的Web读快照分叉已用红→绿并发回归修复。
1000任务工时报表VM67282000→243000、同机handler中位1660.828→24.369ms；2万状态筛选完整item20000→20、683.846→313.939ms。不是HTTP或生产负载承诺。

## What remains
队列持久化账号归属不做隐式迁移，仍需另定旧记录政策。真实Android crypto/磁盘、厂商后台调度、摄像头、生产U9与远端CI未验收。四个旧独立验收本轮再次得到19/19、5/26、28/35、24/33，与原始基线一致；后三项仍为既有失败。

## What blocks shipping right now
本轮修复已通过本地代码验收，可供审查；不能将其表述为完整生产发布验收通过。旧独立验收、owner政策、真机/现场和远端CI限制仍在。没有推送或部署授权。

## Exact next steps
1. 审阅本地分支提交、docs/deep-audit-fixes.md及基于138ff95增量／b0719b2累计补丁。
2. 后端目录运行PYTHONPATH="$PWD" /path/to/venv/bin/python -m pytest tests/ -q -p no:cacheprovider。
3. Android目录运行ANDROID_HOME=/opt/android-sdk ./gradlew --no-daemon :app:testDebugUnitTest :app:assembleDebug；根目录运行python3 scripts/check_offline_room_queries.py和bash .claude/migration/guard.sh。
4. 如用户明确授权，再推分支与PR运行远端CI；生产部署需现场验收。不要直接执行下方历史发布指令。

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
