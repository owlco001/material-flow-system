# 交付检查

在仓库根运行，PY=/root/mf-main/material-flow-backend/.venv/bin/python。

- [x] 优化前后完整后端基线：`cd material-flow-backend && env -u PYTHONPATH "$PY" -m pytest tests/ -q -p no:cacheprovider`
- [ ] 四个独立脚本：在后端目录运行 `$PY tests/test_auth_security.py`、`$PY tests/test_contract_acceptance.py`、`$PY tests/test_order_material_status.py`、`$PY tests/test_token_lifecycle.py`
- [x] 新增库存与查询预算回归：`cd material-flow-backend && "$PY" -m pytest tests/test_transfer_execution_consistency.py tests/test_assembly_list_performance.py -q`
- [x] Android完整单测及Debug包：`cd logistics-android && ANDROID_HOME=/opt/android-sdk ./gradlew --no-daemon :app:testDebugUnitTest :app:assembleDebug`
- [x] UI图标门禁：`python3 scripts/check_emoji_icons.py`；记录已有失败与本轮增量。
- [x] 优化guard：`bash .claude/migration/guard.sh`
- [x] 格式差异：`git diff --check`
- [x] 所有变更文件登记MAP.csv；SLICES.yaml状态与实际一致；无临时日志/副本/调试路径/新增迁移TODO。
- [x] 后端/Android版本按现有规则递增，测试断言一致。
- [x] 报告包含复现、查询数/时间前后对比、测试结果与限制、可复制命令。
- [x] HANDOFF.md包含变更、当前事实、剩余事项、交付阻塞、下一步。

## 无法由本机替代的验收
扫码摄像头、真机网络切换、后台worker系统调度、UI实际渲染与生产U9链路未实测时必须说明；不据此阻止交付可审查代码，但不得宣称生产验收完成。

## 最终结果

后端560 passed；Android234项零失败、Debug构建成功；Room真实生成SQL、图标及优化guard通过。独立脚本已逐个运行并与基线对照：auth19/19，其余5/26、28/35、24/33均为原有失败，故该发布门禁保持未勾选，不宣称完整发布验收通过。CI配置已审阅，尚未推送执行。

## 2026-11-20 第二轮最终验收

- [x] 冻结后端全套593 passed，新增状态/分页/快照/归属与路由测试覆盖。
- [x] Android全套251项、49类、零失败/错误/跳过，Debug构建成功（2m18s）。
- [x] 新增17项身份/持久化/取消/分页生命周期正确不变量全部绿测。
- [x] 真实生成Room SQL验证单ID且仍SYNCING的取消CAS及退避元数据保留。
- [x] 图标75文件PASS；guard与diff格式通过；新预算接入既有CI门禁。
- [x] 四个独立旧验收本轮再次对照19/19、5/26、28/35、24/33，与原始基线相同。后三项原有失败不勾选为发布通过。
- [x] SQL基准和完整item构造预算有前后数据，时间仅指同机合成handler，不外推生产。
- [ ] 真机/加密磁盘/生产U9/远端CI验收；队列owner迁移政策仍需独立处理。
