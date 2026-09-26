# Release 混淆 keep 规则。
#
# 说明：
# - Room / OkHttp / okio 的 AAR 自带 consumer 规则，这里只补业务侧必须显式保留的部分；
# - Filament 的 AAR 自带 consumer 规则，下面再加一层兜底：JNI 回调的 Java 类若被 R8 改名/删减，
#   渲染器初始化会直接崩（UnsatisfiedLinkError），兜底规则保证 3D 页面在 release 可用；
# - org.json 走 Android 框架实现（android.jar），测试用的 org.json:json 仅 testImplementation；
# - 未使用 kotlinx.serialization / Gson / Moshi，无反射序列化规则需求。

# ---- Filament：3D 渲染（机台页面正式入口，release 需要）----
-keep class com.google.android.filament.** { *; }
-keep class com.google.android.filament.gltfio.** { *; }
-keep class com.company.logistics.rendering.** { *; }

# ---- Room：实体、DAO 与生成的数据库实现 ----
-keep class com.company.logistics.data.OfflineOperationEntity { *; }
-keep interface com.company.logistics.data.OfflineOperationDao { *; }
-keep class * extends androidx.room.RoomDatabase { *; }

# ---- 契约 DTO：ApiParser 逐字段读取，不依赖反射，但防止 R8 重命名导致日志/排查困难 ----
-keepnames class com.company.logistics.model.** { *; }

# ---- BuildConfig：API_BASE_URL 等构建期注入字段 ----
-keep class com.company.logistics.BuildConfig { *; }
