# feat/ui-fusion 合并与修复执行清单

- [ ] 删除 API 硬编码地址，改为构建配置注入（占位默认）
- [ ] 统一扫码与订单类型为 PRODUCTION_ORDER，不使用 ORDER_NO
- [ ] 清理运行时文案中的“物流流转系统”/“详情页待接入”等遗留词
- [ ] 复核后端订单物料状态返回文档类型字段默认值与契约
- [ ] Android `assembleDebug` 验证
- [ ] 后端 `py_compile` 验证
- [ ] 敏感信息扫描通过
- [ ] 创建 PR：`feat/ui-fusion` -> `main`
- [ ] PR 通过后 squash merge 并回主干

## P0 运行时残留文案与部署限制

- [ ] 去掉 `LogisticsViewModel.kt` 中 `详情页待接入` 说明（改为跳转/占位实现）。
- [ ] `Theme.kt` 标题文案改为 `厂内流转系统主题`。
- [ ] `MaterialFlowApi.kt` 的 `ApiConfig` 注释可保留语义但注释中的“测试环境地址/HTTP”不得提示默认值暴露。
- [ ] `AndroidManifest.xml` 中 `usesCleartextTraffic` 改为 `false`（调试允许放行除外，后续按 flavor 控制）。
