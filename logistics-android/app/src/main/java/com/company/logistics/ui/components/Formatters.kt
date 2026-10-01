package com.company.logistics.ui.components

/**
 * 服务端时间戳精简为 "MM-dd HH:mm"。
 * 服务端时间戳实为东八区本地时间，+00:00/Z 后缀不可信，不做时区换算。
 * （提取自 OrderDetailScreen.formatTimelineTime，供订单/工作台/审批/流转详情共用）
 */
fun formatServerTime(iso: String?): String {
    val s = iso?.trim().orEmpty()
    if (s.isBlank()) return "服务端未提供"
    return try {
        java.time.LocalDateTime.parse(s.take(19))
            .format(java.time.format.DateTimeFormatter.ofPattern("MM-dd HH:mm"))
    } catch (_: Exception) {
        s.take(16).replace('T', ' ')
    }
}
