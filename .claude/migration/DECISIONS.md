# 决策记录

## 2026-11-19 D1：使用独立副本与合成数据
原本地仓库与远端保持不变。只在 /tmp/material-flow-optimization 本地分支修改，不部署、不推送、不读取生产数据。

## 2026-11-19 D2：四个独立实现切片
审计发现：通用 STOCKTAKE 流转误扣库存；同料多行与自己递增的版本冲突；装配 API 残留2N成员查询；离线单批20条与盘点刷新并发存在完整性问题。按四个切片处理，库存两个问题归同一事务一致性切片。
Android 装配工时逐条请求暂不加入本轮：需额外批量接口或限流/生命周期设计，避免扩大验证范围，后续由维护者确认需求后单独评估。

## 2026-11-19 D3：不按过时文档修改现场定案
NEXT.md 含历史分支、版本和环境说明。以当前代码为事实源。不重启已搁置的库存小数改造、不修改内网HTTP约定；仅更新本轮事实记录。

## 2026-11-19 D4：证据边界
本轮覆盖所选业务路径，不宣称全库审计完成。禁止把提交记录中的测试数当成本轮结果。性能用SQL trace的确定性查询预算与同机合成数据计时；不以绝对耗时阈值造成不稳定测试。

## 2026-11-19 D5：校正D2装配性能判断
实际HTTP回归和路由表确认 main.py 提前注册了已批量优化的 GET /assembly/tasks，assembly_routes.py 的同路径函数被遮蔽。因此撤销对后者的批量优化，改为删除重复注册并新增唯一入口/查询预算测试。不宣称42→3性能收益。另立库存优先排序切片，通过 EXPLAIN 与同机合成库计时验证真实排序开销。

## 2026-11-19 D6：离线账号归属不做隐式迁移
审计发现既有队列缺少持久化用户归属。完整修复需要Room schema与历史未归属记录处置决策，超出本轮多批重放切片；由维护者后续确认迁移政策后处理。本轮保持原契约并防一次重放中会话变化后继续启动请求。不得声称已实现持久化跨账号隔离。

## 2026-11-19 D7：校验Room生成实现而非只信fake
JVM替身测试通过后，读取KSP生成实现发现带默认方法体的@Query实际委托DefaultImpls，未生成到期SQL；既有scheduleRetry也受影响。保留旧接口默认体以兼容测试替身，数据库改为返回生产RoomOfflineOperationDao，在其中将pendingDue、hasPending、scheduleRetry重新声明为带@Query的抽象覆盖。验证生成数据库确实实例化该DAO，读取生成SQL在隔离SQLite执行到期分页、退避持久化、终态与快照用例；该检查接入Android CI。不改Room表结构或迁移版本。

## 2026-11-20 D8：深查后的授权修复与工作台派生
用户授权长任务修复深查问题，基线138ff95，使用fix-deep-audit-138ff95本地分支。工作台状态仍从交接、正式流转与匹配投影派生，只先读取轻量列筛选ID，再构造当前页完整item；保留有效状态白名单、statusCode或lastHandoverStatus谓词、角色预览审计与旧ID兼容。全局汇总使用同一轻量投影且不受列表过滤影响；导出不计算弃用的全局汇总。不将CONFIRMED新增为公开筛选状态。

## 2026-11-20 D9：工时归属集合与路由
工时直接任务与临调来源使用UNION建立唯一(task,labor)归属集合，来源=目标不重复计时。展示与导出复用归属和排序，count仅计算(task,worker)分组键；ACTIVE用同次查询时刻。删除后注册且被遮蔽的两个车间GET，让OpenAPI与实际main路由一致，不替换有效统计口径。

## 2026-11-20 D10：Web筛选下推仍保留字面匹配
原Jinja搜索为大小写敏感子串，SQL使用instr而非默认大小写不敏感的LIKE，百分号/下划线不变通配符。分类保持精确匹配，筛选在LIMIT之前；物料partial只读页内数据，不构造任务/工时/timeline；全页保留原订单汇总与其他详情，再替换为服务器分页物料。材料wire字段由API和Web共用函数构造，不引入新公开API参数。

## 2026-11-20 D11：装配分页与结构化工时任务
装配总数和页数以服务端为准，不用当前页长度冒充总量。工时作为workspace job子任务，跨刷新共享Semaphore(4)，每次成功失败写状态都核对session/workspace世代；手动工时详情单独取消旧任务并核对世代，登出/失效取消。页面结构与每页20条约定不变。

## 2026-11-20 D12：网络身份快照与持久化提交协议
API登录/退出/恢复变更身份epoch，刷新仍在同epoch内轮转。Repository在网络挂起前同步保留API epoch和自身ticket，再经协程上下文调用原open login，以保留现有替身与调用契约。API令牌提交和401重试校验原身份；Repo用单一monitor串行角色、用户摘要、token保存和过期投递。API回调不持session/refresh锁，Repo再按epoch及当前令牌对过滤迟到通知。网络等待不持Repo锁，锁序仅Repo到API。旧身份取消使用CancellationException，不伪装为库存409冲突；multipart、流式与小文件直接HTTP路径同样校验身份。

## 2026-11-20 D13：取消claim与显式换号部分报告
claim写SYNCING也进入try；取消时在NonCancellable中调用专用restoreCancelledClaim(id)，生产Room抽象方法SQL限定id且status仍SYNCING，保留幂等、重试次数与时间。不要修改手动resetToPending的语义，不全库重置。外部worker/job取消继续抛出；显式身份变更且外层job仍活跃时，在claim清理后终止本轮并保留部分同步报告。通用submit必须重抛取消，不能将旧身份取消变成新离线记录。真实生成SQL门禁覆盖CAS，不用fake条件更新冒充生产实现。

## 2026-11-20 D15：订单缓存必须受身份与展示选择约束
多订单详情缓存按当前已验证身份在同一会话内复用；logout/401/新login和角色预览进出均清空缓存。详情刷新token可更新非选中订单的本地entry，但只有当前选中订单、当前session世代可改全局UI。删除无调用方的legacy loadOrder全局写入路径。显式打开仍刷新，select已缓存完整订单才本地恢复。

## 2026-11-20 D16：权威物料分页状态与订单详情分开缓存
OrderDetail属于任务/时间线聚合，OrderMaterialStatus属于权威库存/交接投影并承载全量summary、total/page/hasMore。不得用toOrderStatus(OrderDetail)相互转换。每订单缓存两者，并使用material refresh世代和单append token；刷新失效旧追加，检查页序/documentNo/token，排序和全量汇总字段来自最新服务器页。
优化后分页与全局summary分别查询，独立复核通过并发写入测试证明可能读到不同版本。两个无写Web helper在没有既存事务时BEGIN读事务，close或请求共享连接代理rollback结束；WAL允许并发写入。分页总数、页内行和全局summary在同一读视图，不将该事务扩展至带预览audit写入的API以免引入读转写升级。

## 2026-11-20 D17：机台物料只改变显式筛选范围
可选deviceNo在当前order_id内绑定order_devices，明细/total/汇总/缺料共用参数化谓词；旧无筛选保持整单。允许历史导入长编号，不套用手工创建的64字符限制。客户端专用方法逐页200行取完，保留旧服务器忽略筛选后的防御过滤和分页兼容。现有权限范围不在本切片调整。

## 2026-11-20 D18：机台详情及聚合共享可取消任务与路由归属
机台请求使用operationScope单一job，详情首报文与后续聚合都检查session/request世代及当前路由；新机台、登录/登出/401、角色预览切换、navigate及直接订单/扫码入口失效旧任务。世代校验与协程取消双重保护，不依赖HTTP请求总能配合取消。

## 2026-11-20 D19：机台任务工时读全页但不修改后端统计契约
沿用20条固定页与既有权限/筛选，每页验证元数据和推进，任务按task id、工时按task+assembler去重。冻结首total（任务还冻结totalPages），检测变化则失败提示，不静默发布部分总量。老工时无total以短页终止。跨HTTP分页不是事务快照，不能检测所有等量替换或ACTIVE时钟变化。新增测试依赖coroutines-test只进入JVM测试，不进生产运行依赖。

后台订单刷新与显式导航分离：只有显式打开/切换订单或前台手动刷新失效旧机台请求；写后后台刷新不取消新机台加载，非订单前台的订单回调只更新缓存，不改当前全局页面。

## 第六轮 D20：跨层订单流转过滤接口冻结
GET /api/v1/transfer-requests使用重复documentNo query参数精确过滤，权限/status/document范围对count与items一致，未传时旧契约保留。新repo.transferRequestsForOrders(Set<String>)与API.listTransferRequestsForOrders(Set<String>)为机台专用读取，空集合无HTTP；客户端每批20编号，每页100行。异常保护到上限必须显式失败，不静默返回截断集合。旧服务端忽略filter只能退化读取后过滤，不能冒充有新性能收益。

## 第六轮 D21：互斥写范围并发开发与集中构建
服务端、Android传输层、Android机台聚合UI三路文件互斥，共享接口由Lead冻结；Android Gradle只由Lead串行执行。分区请求结构化子job、共享Semaphore4、完成后独立发布并再次校验session/request/router。Lead负责版本台账、完整回归、交叉复核和最终本地提交，不推送部署。

## 第六轮 D22：规范编号、分页证明和返回未完成页面
服务端与客户端对订单编号统一trim后去重，query和响应过滤使用同集合。专用流转每批冻结分页元数据（含有无），尊重totalPages，短页与后续页矛盾时显式失败，不能假成功。返回被取消但仍有LOADING分区的缓存机台时，通过原openDeviceDetail重新启动完整受控加载，不将取消改成伪空态。

## 第七轮 D23：全局流转迁入同一安全分页器
第六轮明确保留的全局50页路径本轮独立迁移。public全局status和scope签名不变，以同一helper实现分页完整性、元数据冻结和身份检查；删除旧循环与50页静默结束分支，达到有界保护必须失败。个人列表仅写自身状态，同身份可后台完成缓存，身份/预览边界不得保留旧请求。

## 第七轮 D24：索引以真实初始化与合成VM预算决定
只在临时库量化document_no、status及多订单IN的EXPLAIN/SQLite步数，验证结果/排序不变和init_db幂等。比较候选后最多添加一条有明确收益的索引，时间只记录不用易抖阈值。不在测试服务器或生产库试验。

## 第七轮 D25：采纳document/status/time/id单索引
20k合成四组count+首页精确VM合计31131步优于doc/time候选60833步，最终只创建idx_transfer_requests_document_status_created(document_no,status,created_at DESC,id DESC)。单/多/空匹配均降为小范围，status场景count覆盖而rows仍可能走旧status索引；不强制INDEXED BY或自动ANALYZE，不宣称完全消除排序/扫描。真实init_db幂等及结果/数据不变受测试约束。
