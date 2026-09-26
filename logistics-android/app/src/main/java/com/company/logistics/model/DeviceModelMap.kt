package com.company.logistics.model

/**
 * 机台 → 3D 装配模型编号映射。
 *
 * 设备数据里没有模型字段时，用这张小映射表解析：
 * 1. 机台编号前缀优先（不区分大小写，如 ROBOT-TEST-01 → MACHINE-ROBOT-01）；
 * 2. 机型名称兜底。
 *
 * 解析不到返回 null，调用方把"3D 模型"按钮置灰并提示"该机型暂无 3D 模型"，
 * 不要硬映射一个不相关的模型上去。
 */
object DeviceModelMap {
    /** 机器人手臂测试机（BrainStem 样例模型）。 */
    const val MODEL_ROBOT = "MACHINE-ROBOT-01"

    /** 头盔测试机（DamagedHelmet 样例模型）。 */
    const val MODEL_HELMET = "MACHINE-HELMET-01"

    /** 工人测试机（CesiumMan 样例模型）。 */
    const val MODEL_WORKER = "MACHINE-WORKER-01"

    /** 机台编号前缀 → modelCode。新增测试机型时在这里加一行即可。 */
    private val PREFIX_MAP = listOf(
        "ROBOT" to MODEL_ROBOT,
        "HELMET" to MODEL_HELMET,
        "WORKER" to MODEL_WORKER,
        "CESIUM" to MODEL_WORKER,
    )

    /**
     * 机型名称 → modelCode。未知机型不要硬映射，保持为空，
     * 让 UI 置灰提示，避免点开一个完全不相关的模型。
     */
    private val TYPE_MAP: Map<String, String> = mapOf(
        // 示例："SHEET_ASSEMBLER" to MODEL_ROBOT,
    )

    /** 解析机台对应的已发布模型编号；无模型返回 null。 */
    fun modelCodeFor(deviceType: String, deviceNo: String): String? {
        val no = deviceNo.trim().uppercase()
        for ((prefix, code) in PREFIX_MAP) {
            if (no.startsWith(prefix)) return code
        }
        return TYPE_MAP[deviceType.trim()]
    }
}
