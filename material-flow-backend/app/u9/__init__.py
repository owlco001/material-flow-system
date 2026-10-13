"""用友 U9 ERP 集成模块。

设计目标：智慧工厂（本地 SQLite + FastAPI）与用友 U9 之间的数据同步，
替代目前"U9 导出 Excel → 人工导入"的手工链路。

当前状态：U9 Cloud（U9C）REST 只读同步已实现（client.U9CClient，字段按 U9C swagger 导出），
详见 docs/U9C_INTEGRATION.md；经典 U9（SOAP）仍为占位。

分期：
  Phase 1（只读同步，U9 → 本地）：料品档案、物料清单 BOM、库存现存量、生产订单
  Phase 2（回写，本地 → U9）：完工汇报、领料出库（需 U9 业务校验，待实施商确认）

安全约定：
  - 默认关闭（U9_ENABLED=0），所有同步接口在未配置时返回 503，不写任何数据
  - 凭证只从环境变量读取，绝不落盘、不进日志
  - 同步接口默认 dry_run=true，先预览再真正写入
"""
