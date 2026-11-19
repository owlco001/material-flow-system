# 深查问题第二轮修复记录

基线提交：138ff95。工作分支：fix-deep-audit-138ff95。版本：后端0.22.2，Android0.5.53／60。本轮按用户授权实施本地修复，不推送或部署，不改变内网HTTP及库存整数约定，不隐式迁移离线队列账号归属。

## 改动与契约

### 会话身份与取消恢复

API显式登录、退出、恢复身份时切换epoch，普通令牌刷新不切换身份。请求使用原子身份快照；401仅能刷新与重试同一epoch，旧刷新200／4xx不能覆盖或清除新账号。Repository在网络挂起之前保留身份ticket，再通过协程上下文传给原有可覆盖login入口，保持测试替身兼容；旧结果不能提交角色、令牌、用户摘要或deviceId。

Repository用同一monitor串行显式身份变更、SessionStore提交及过期回调，API回调不持网络或session锁，避免锁序反转；同epoch旧令牌通知也需匹配当前令牌对才落盘。远程登出绑定捕获的身份，不得在晚到响应后清除新登录。

取消从claim写SYNCING之前进入保护区，NonCancellable清理通过独立Room CAS仅恢复指定且仍为SYNCING的记录；保留重试次数、下次时间及原幂等键，不影响其他claim或已完成记录。取消向上传播，不能被通用提交当网络故障转为新离线操作。

最终边界检查还补齐了multipart、模型／图纸流式下载及小文件路径的相同身份保护；流处理按块检查身份，旧返回不交给后续业务链。使用真实API客户端＋本地HTTP、真实Repository／SessionStore方法＋内存SharedPreferences验证；未把JVM替身称作Android加密磁盘验收。

### 装配分页和工时生命周期

保留服务端total、totalPages，不再以当前页长度覆盖；设备扫码回退路径也保留服务端分页元数据。每页20条和服务端设备筛选不变。

工作台工时加载改为workspace job的结构化子任务。共享Semaphore最多4个调用，刷新／翻页／登出取消旧job，每次成功或失败写状态均检查session和workspace世代。手动工时详情有独立可取消job和世代，也使用同一并发上限；登出／失效取消。列表内容先显示，不等待全部工时完成。

原5项正确不变量修复前全部失败；修复后6项通过（增加手动详情取消案例）。包括0／20／21／41条分页、next／previous、设备过滤、旧成功／失败响应和并发限制。

### 工时报表与路由

将直接归属与临调来源构造成唯一(task, worker, labor)集合，UNION对来源等于目标去重；NULL来源不虚构任务。分页与导出复用同一归属、筛选、排序，ACTIVE工时用同次查询时刻。count仅统计(task, worker)键，不再重复计算分钟数与排序。

删除被main遮蔽的两个车间GET，使实际路由和OpenAPI一致。新增方法／路径唯一性、schema及权限回归，不替换main已有车间范围与统计口径。

### 工作台筛选

仍按正式交接／流转与匹配投影派生状态；先读取轻量列和事实，筛选出需求ID，再仅为页内ID构造完整item。保留合法状态白名单、statusCode或lastHandoverStatus匹配、旧work-item ID、排序、角色与预览审计；没有新增CONFIRMED筛选状态。

全局summary仍不受列表orderNo／status过滤，使用同一轻量投影；导出提前应用订单范围且不计算不用的全局summary。页与全局summary分开读后，新增并发回归发现快照可能分叉，已为无写的Web helper加入显式只读事务。该事务不应用于有预览审计写入的API，避免读转写升级问题。

### Web物料真实分页

原模板先全量读取，再逐条筛选和切片。现SQL使用instr保持大小写敏感、字面子串搜索，百分号／下划线不充当通配符；分类保持精确匹配，筛选先于LIMIT。模板只呈现页内记录和服务端元数据。

materials partial不再计算任务、工时、timeline或订单列表；完整订单页仍保留原有摘要和其余详情，复用同一物料wire转换函数。空筛选结果统一安全页码，防极端页码转SQLite整数溢出。批量勾选、CSRF和页面结构保留。

基于121条需求的回归证明：partial上下文材料记录121→20，实际SQL带LIMIT20；搜索大小写、百分号、分类后分页、末页及空结果一致。修复前10项均失败，实施后相关Web／订单／批量操作58项通过，后补极端空页边界。

## 同机合成基准

使用深查相同数据和真实handler；时间不含HTTP、鉴权、网络或生产硬件差异。查询数、完整item构造与VM步数比绝对时间更适合作回归预算。

| 场景 | 修复前 | 修复后 |
|---|---:|---:|
| 1000任务／4000工时，第一页中位耗时 | 1660.828 ms | 24.369 ms |
| 同上SQLite近似VM步数 | 67,282,000 | 243,000 |
| 100任务／400工时SQLite近似VM步数 | 716,000 | 26,000 |
| 2万需求＋IN_STOCK，第一页完整item构造 | 20,000 | 20 |
| 同上handler中位耗时 | 683.846 ms | 313.939 ms |
| 1000需求＋IN_STOCK，返回10条完整item构造 | 1,000 | 10 |

工时任务／记录同增10倍，优化后的VM约增9.3倍，原约增94倍。状态筛选仍需扫描轻量事实，不能宣称O(1)；完整item只为实际页内结果构造。

## 验证状态

- 已有基线138ff95：后端560项、Android234项通过；不是本轮新跑结果。
- 后端最终冻结集合：593 passed，0失败／0错误／0跳过，571.08s；包含空页极端输入及并发读快照。
- 工作台42项、Web／订单／批量58项局部通过；工时报表、归属、路由与旧工时契约通过，最终快照／物料页相关33项通过。
- Android完整251项／49类：0失败／0错误／0跳过；testDebugUnitTest＋assembleDebug为BUILD SUCCESSFUL（2m18s）。本轮新增17项：身份8、取消2、真实SessionStore方法＋内存存储1、装配分页／生命周期6，全部通过。
- 完整回归发现并修正了两项兼容差异：BOM无效输入先于鉴权校验；显式换号终止drain返回部分报告，外部job取消仍抛出。
- 真实Room生成SQL门禁通过，包含按id且仍SYNCING的CAS、其他claim／已完成记录不变、保留退避次数与时间；图标扫描75文件PASS，优化guard与git diff --check通过。
- 四个独立脚本本轮再次对照：auth19/19，contract5/26，order status28/35，token lifecycle24/33，与原始基线相同，没有额外回归。

## 保留限制

既有队列没有持久化账号归属，仍需另定历史未归属记录和Room迁移政策。本轮会话修复不能被表述为解决该队列归属问题。

三个旧独立验收脚本在原版和前轮优化版已有相同失败（contract5/26、order status28/35、token lifecycle24/33；auth19/19通过），不修改认证政策迎合它们。真机、Android加密存储／磁盘、厂商后台调度、摄像头和生产U9链路未验收；JVM内存替身及生成SQL测试不能替代现场。

## 验证命令

```bash
cd material-flow-backend
PYTHONPATH="$PWD" /path/to/venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

```bash
cd logistics-android
ANDROID_HOME=/opt/android-sdk ./gradlew --no-daemon :app:testDebugUnitTest :app:assembleDebug
```

```bash
# 仓库根
python3 scripts/check_offline_room_queries.py
python3 scripts/check_emoji_icons.py
bash .claude/migration/guard.sh
git diff --check
```
