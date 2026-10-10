package com.company.logistics.model

import org.json.JSONObject

/**
 * 物料主档（U9 ItemMaster 导入，GET /api/v1/materials/{code}/master）。
 * 只取现场用得上的字段；其余字段服务端保留，终端不展示。
 */
data class MaterialMaster(
    val code: String,
    val itemForm: String?,
    val storageLocation: String?,
    val drawingNo: String?,
    val t6Code: String?,
    val t6DrawingNo: String?,
    val deviceNo: String?,
    val projectNo: String?,
    val brand: String?,
    val mainCategoryName: String?,
    val printCategory: String?,
    val warehouseClerkName: String?,
    val buyerName: String?,
    val productionDeptName: String?,
    val unitWeight: Double?,
    val weightUnitName: String?,
    val effectiveUntil: String?,
    val effective: Boolean,
) {
    /** 详情页按顺序展示的「标签-值」，空值不出现。 */
    val rows: List<Pair<String, String>>
        get() = listOfNotNull(
            itemForm?.let { "形态" to it },
            storageLocation?.let { "存储地点" to it },
            drawingNo?.let { "U9图号" to it },
            t6DrawingNo?.let { "T6图号" to it },
            t6Code?.let { "T6料号" to it },
            deviceNo?.let { "设备编号" to it },
            projectNo?.let { "项目号" to it },
            brand?.let { "品牌" to it },
            mainCategoryName?.let { "主分类" to it },
            printCategory?.let { "打单分类" to it },
            productionDeptName?.let { "生产部门" to it },
            warehouseClerkName?.let { "仓管员" to it },
            buyerName?.let { "采购员" to it },
            unitWeight?.takeIf { it > 0 }?.let { "单位重量" to (fmt(it) + (weightUnitName?.let { u -> " $u" } ?: "")) },
            effectiveUntil?.takeIf { it != FOREVER }?.let { "失效日期" to it },
        )

    companion object {
        const val FOREVER = "9999-12-31"

        fun parse(json: String): MaterialMaster = from(JSONObject(json))

        fun from(o: JSONObject): MaterialMaster {
            fun s(k: String): String? = if (o.isNull(k)) null else o.optString(k).trim().takeIf { it.isNotEmpty() }
            fun d(k: String): Double? = if (o.isNull(k) || !o.has(k)) null else o.optDouble(k).takeIf { !it.isNaN() }
            return MaterialMaster(
                code = s("code").orEmpty(),
                itemForm = s("itemForm"),
                storageLocation = s("storageLocation"),
                drawingNo = s("drawingNo"),
                t6Code = s("t6Code"),
                t6DrawingNo = s("t6DrawingNo"),
                deviceNo = s("deviceNo"),
                projectNo = s("projectNo"),
                brand = s("brand"),
                mainCategoryName = s("mainCategoryName"),
                printCategory = s("printCategory"),
                warehouseClerkName = s("warehouseClerkName"),
                buyerName = s("buyerName"),
                productionDeptName = s("productionDeptName"),
                unitWeight = d("unitWeight"),
                weightUnitName = s("weightUnitName"),
                effectiveUntil = s("effectiveUntil"),
                effective = o.optBoolean("effective", true),
            )
        }

        private fun fmt(v: Double): String =
            if (v == Math.floor(v)) v.toLong().toString() else v.toBigDecimal().stripTrailingZeros().toPlainString()
    }
}
