package com.company.logistics.rendering

/**
 * GLB 零件英文名 → 中文显示名映射。
 *
 * 模型节点名多为英文（如 arm_base、Conveyor.001），直接展示工人看不懂。
 * 按词元翻译：小写后按 _ - . 分隔逐词查表，命中则替换，未命中保留原文。
 * 例：arm_base → "机械臂 底座"；Conveyor.001 → "传送带 001"。
 */
object PartNameCn {
    private val DICT: Map<String, String> = mapOf(
        // 机械臂
        "arm" to "机械臂", "base" to "底座", "column" to "立柱", "upper" to "大臂",
        "forearm" to "小臂", "wrist" to "腕部", "roll" to "旋转", "pitch" to "俯仰",
        "flange" to "法兰盘", "gripper" to "夹爪", "jaw" to "夹指",
        "shoulder" to "肩部", "elbow" to "肘部",
        // 传送 / 流水线
        "conveyor" to "传送带", "belt" to "皮带", "roller" to "滚筒", "workstation" to "工作站",
        "line" to "产线", "station" to "工位",
        // 通用机械
        "motor" to "电机", "gear" to "齿轮", "gearbox" to "减速箱", "bearing" to "轴承",
        "shaft" to "轴", "frame" to "机架", "bracket" to "支架", "plate" to "板",
        "cover" to "罩盖", "housing" to "壳体", "bolt" to "螺栓", "nut" to "螺母",
        "screw" to "螺钉", "washer" to "垫圈", "spring" to "弹簧",
        "wheel" to "轮", "cylinder" to "油缸", "piston" to "活塞", "chain" to "链条",
        "pump" to "泵", "valve" to "阀", "pipe" to "管", "tank" to "箱",
        "sensor" to "传感器", "controller" to "控制器", "cabinet" to "控制柜",
        "panel" to "面板", "screen" to "屏幕", "button" to "按钮",
        "fork" to "货叉", "mast" to "门架", "forklift" to "叉车",
        "pallet" to "托盘", "generator" to "发电机",
        "left" to "左", "right" to "右", "front" to "前", "rear" to "后",
        "top" to "上", "bottom" to "下", "inner" to "内", "outer" to "外",
        "l" to "左", "r" to "右",
    )

    private val SPLIT = Regex("[_.\\-]+")

    /** "arm_base" → "机械臂 底座"；无命中词元时返回原名 */
    fun displayName(raw: String): String {
        val tokens = raw.lowercase().split(SPLIT).filter { it.isNotBlank() }
        if (tokens.isEmpty()) return raw
        val translated = tokens.map { DICT[it] ?: it }
        // 全未命中：直接返回原名，避免 "001" 这类变成怪词
        if (translated == tokens) return raw
        return translated.joinToString(" ")
    }
}
