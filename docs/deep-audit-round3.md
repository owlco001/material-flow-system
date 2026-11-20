# 深查延续报告：订单缓存身份与物料分页世代（第三轮）

## 本轮范围与基线

- 代码基线：`53b1b67`（第二轮修复后），分支：`fix-deep-audit-138ff95`。
- Android `0.5.54 / 61`；本轮只改Android ViewModel/纯内存订单状态容器及测试，后端版本与代码未改。
- 不读生产会话/数据库、不联网、不推送或部署，不触及库存小数或离线队列owner迁移。

## 新发现及修复

### P1：同一ViewModel内订单缓存跨身份保存

`MultiOrderDetailState`以orderNo为键并常驻ViewModel。先前logout、401失效、再登录与管理员角色预览切换只重置部分UI state，不清该LRU。低权限的后继身份可先看到此前管理员缓存订单的列表条目，并通过本地select直接恢复其完整detail/材料，无需重新请求服务端。服务端在将来的请求上仍有权限控制，故本问题界定为客户端缓存信息暴露/权限预览失真，不描述为绕过API认证。

订单LRU仍可在同一账号内保留按订单隔离的detail/material状态；`MultiOrderSnapshot`暴露的是最近订单列表元数据，不直接暴露完整物料明细，但清理能同步清除条目错误/加载状态，并避免后续本地选择跨身份命中。低权限重新打开旧订单必须重新经过Repository及服务端授权。

### P1/P2：非选中订单的迟到刷新覆盖当前订单全局视图

每订单requestId只能保证旧结果不能覆盖同一订单的较新请求，不能说明该订单仍是当前UI所选订单。先前A的刷新即使在用户已切到B后完成，也会无条件写全局orderDetail/orderStatus。

现在迟到结果只更新仍属于本身份的A缓存；全局状态只由当前选中订单写入。失效或换身份后缓存被清空，旧请求token不再命中。

### P2：切换订单后，material-status分页和权威摘要丢失

detail和material-status是两个不同的服务端事实面。先前LRU只保存OrderDetail；切回tab用detail.materials重建OrderMaterialStatus，丢掉分页total/page/hasMore、全量齐套率/缺料汇总和handover/workflow投影。

现在每订单缓存独立的服务端OrderMaterialStatus；UI不再通过`toOrderStatus(OrderDetail)`转换。切换订单恢复完整分页/投影，加载下一页继续请求下一页。

### P2：同订单旧分页响应可混入刷新后的第一页

旧page2在途时新首刷完成，先前只比较documentNo，仍可合并旧行并覆盖新page/summary。现每订单具有单独材料刷新generation和唯一append token；首刷使旧append失效。页结果还需匹配同文档号、预期连续页码、当前token及身份/选中订单；失败结果采用同样代际校验。

此外，页面只对当前订单显示其append loading，A的迟到结果不能清除B自己正在进行的加载标记。无调用方的旧`loadOrder`全局写状态路径已删除。

## 复现与最终验证

新增`MultiOrderSessionPagingRegressionTest`，修复前以同一ViewModel和Deferred Fake Repository复现账号切换旧缓存恢复、A刷新覆盖B、旧page2污染新page1、切回缓存丢失server metadata。另覆盖角色预览清缓存及同订单在途刷新避免重复请求。修复后的订单专项7项通过，包括迟到失败不污染当前订单错误状态。

最终Android完整验证：**258项／50类，0失败、0错误、0跳过**；`testDebugUnitTest + assembleDebug`为BUILD SUCCESSFUL。后端593 passed沿用第二轮最终结果（第三轮无后端源码改动）。Room原生取消CAS、75文件图标、migration guard及diff-check均通过。第三轮已本地提交`ba5a3e4`，增量与累计补丁按各自基线校验可应用。所有数据取自对应命令结果/JUnit XML，不把构建成功等同于现场或生产验收。

## 保留限制

- 订单缓存只存在当前ViewModel内存，未持久化；账号切换/失效/preview边界会清除。
- 离线队列历史owner归属仍按独立D6待业务定迁移策略。
- 真实Android加密SharedPreferences磁盘、系统进程回收、真机UI与厂商后台调度未验收。
- 旧独立验收脚本基线失败、生产U9/远端CI限制沿用上一轮记录。
