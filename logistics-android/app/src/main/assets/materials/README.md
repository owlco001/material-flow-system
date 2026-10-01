# 材质源码

- `ghost.mat`: 半透明 ghost 材质源码（零件隔离/剖面用）
- 编译工具：filament 1.75.1 的 matc（material version 75）
- 编译命令：`matc -o ../ghost.filamat ghost.mat`
- 注意：filament 升级后必须用对应版本 matc 重编，否则运行时报
  "Material version mismatch" 直接 SIGABRT（Java 层 catch 不住）
