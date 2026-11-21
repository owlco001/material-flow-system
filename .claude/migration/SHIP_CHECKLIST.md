# 第三轮交付验收清单

代码基线：`53b1b67`。修复提交：`ba5a3e4364950cce9e7ea8db8c06e3b54f29b955`，分支`fix-deep-audit-138ff95`。

## 自动化验收

- [x] 第三轮红/绿订单回归：`MultiOrderSessionPagingRegressionTest` 7项全部通过（跨账号缓存、角色预览、同单在途切回、旧刷新成功/失败、旧append、分页/摘要恢复）。
- [x] Android全量：`ANDROID_HOME=/opt/android-sdk ./gradlew --no-daemon :app:testDebugUnitTest :app:assembleDebug` → **258项、50 suite，0失败/错误/跳过；BUILD SUCCESSFUL**。
- [x] 后端全量沿用第二轮无后端源码改动基线：**593 passed**。
- [x] Room实际生成SQL：`python3 scripts/check_offline_room_queries.py` → 含取消claim id+SYNCING CAS、其他claim/已完成行隔离、退避元数据不变。
- [x] 图标门禁：`python3 scripts/check_emoji_icons.py` → 扫描75个Kotlin文件PASS。
- [x] 反回归ratchets：`bash .claude/migration/guard.sh` → 全部PASS。
- [x] 代码格式：`git diff --check` → PASS。
- [x] 本地diff commit与修复补丁已在各自基线索引中验证可应用；未推送/部署。

## 已知非发布验收项

- [ ] 三个独立旧脚本仍存在原始基线失败：contract 5/26、order material status 28/35、token lifecycle 24/33；auth 19/19。已在基线及各轮结果中对照，无新增失败。
- [ ] 真实Android加密SharedPreferences磁盘、厂商进程杀死/重启、摄像头、UI实机、生产U9和远端CI未验收。
- [ ] 离线队列历史记录没有持久化账号owner；需业务明确旧数据处置和迁移策略后单列处理。本轮未隐式迁移。

因此本轮本地代码与自动化回归通过，可供审阅；**不宣称完整现场/生产发布验收通过**。任何push、PR、部署需用户另行授权。

## 第四轮验收

- [x] 后端598 passed；Android265项/51类零失败，Debug构建成功。
- [x] 目标机台范围红→绿；历史长编号红→绿；7项Android旧/新服务端分页兼容测试。
- [x] 真实Room、图标、guard、diff通过；独立只读复核完成。
- [ ] 本轮未推送、未部署、未发布APK；现场与旧验收限制仍保留。

## 第五轮验收

- [x] 原始机台8项与后续3项边界红测，最终20项专项全绿。
- [x] Android完整285项/52类零失败/错误/跳过，Debug构建成功。
- [x] 后端源码未变；装配、工时、物料相关11项契约复验通过，全量598项沿用第四轮基线。
- [x] 真实Room SQL、图标75文件、ratchet和差异格式通过。
- [ ] 真机、生产、远端CI验收未做，本轮未推送、部署或发布APK。

## 第六轮并发验收

- [x] 三路互斥写范围，统一跨层contract与集中Gradle，完成交叉只读复核。
- [x] 后端完整602 passed；Android完整307项/54类，0失败/错误/跳过，Debug构建成功。
- [x] 新scope15项、并发7项与第五轮原20项回归全绿；额外4项边界先红后绿。
- [x] 1000条可见流转2条目标的HTTP请求/JSON字节预算通过；未宣称索引或生产延迟收益。
- [x] 真实Room SQL、图标75文件、ratchet、YAML与变更文件映射通过。
- [ ] 本轮未推送、部署、发布APK；现场/远端CI等既有未验收边界保留。

## 第七轮验收

- [x] 初始Android21项14红，集中64项和完整329项/56类零失败，Debug成功49s。
- [x] 新global13、个人9通过；原scope15/机台20/并发7测试文件未改。
- [x] 索引局部7项；20k真实索引初始化、精确VM、排序分页及幂等对照通过。
- [x] 后端完整603 passed（424.65s）。
- [x] 真实Room SQL、图标75文件、ratchet、YAML/变更文件映射和格式检查通过。
- [ ] 本轮未推送部署或发布APK；现场及既有未验收边界保留。
