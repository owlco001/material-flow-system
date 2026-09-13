package com.company.logistics.data

import android.content.Context
import com.company.logistics.data.remote.ApiException
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.data.remote.TransferItem
import com.company.logistics.model.LoginResult
import com.company.logistics.model.MaterialInventory
import com.company.logistics.model.OfflineOpType
import com.company.logistics.model.OfflineOperation
import com.company.logistics.model.OrderMaterialStatus
import com.company.logistics.model.ScanResult
import com.company.logistics.model.SyncStatus
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import java.util.UUID

/**
 * 数据仓库 —— 串联网络层与本地离线队列。
 *
 * 离线优先策略：
 *  - 网络可用：直接提交，成功后不落本地队列；
 *  - 网络失败（连接异常 / 5xx 可重试）：写入本地队列，标记 PENDING，联网后重放；
 *  - 业务失败（4xx）：不重放（重放也不会成功），直接返回错误给用户。
 *
 * 幂等保证：离线记录保存 clientOperationId，重放时原样回传，
 *          由服务端幂等键去重，避免重复建单（契约第 5 节）。
 */
class LogisticsRepository(
    private val api: MaterialFlowApi,
    private val dao: OfflineOperationDao
) {

    /**
     * 契约 API 句柄。
     *
     * 暴露给摄像头扫码链路（[com.company.logistics.data.CameraXScannerRepository]）复用同一份
     * 会话与 baseUrl 配置 —— 避免出现两个 ApiConfig 实例导致 token / 环境不一致。
     */
    val apiHandle: MaterialFlowApi get() = api

    // ==================== 会话 ====================

    suspend fun login(username: String, password: String, deviceId: String): LoginResult =
        api.login(username, password, deviceId, CLIENT_VERSION)

    fun logout() = api.updateToken(null)

    val isLoggedIn: Boolean get() = api.accessToken != null

    // ==================== 扫码 ====================

    suspend fun resolveScan(rawValue: String): Result<ScanResult> = runCatching {
        api.resolveScan(rawValue)
    }

    suspend fun materialInventory(code: String): Result<MaterialInventory> = runCatching {
        api.materialInventory(code)
    }

    suspend fun orderMaterialStatus(documentNo: String): Result<OrderMaterialStatus> = runCatching {
        api.orderMaterialStatus("PRODUCTION_ORDER", documentNo)
    }

    // ==================== 写操作（离线优先） ====================

    /**
     * 提交厂内入库申请。
     * 失败时按错误类型决定是否落本地队列。
     */
    suspend fun submitInbound(
        material: MaterialInventory,
        quantity: Int,
        targetLocation: String?,
        remark: String?
    ): SubmitResult = submit(
        opType = OfflineOpType.INBOUND,
        materialCode = material.material.code,
        materialId = material.material.id,
        quantity = quantity,
        targetLocation = targetLocation ?: material.primaryLocation,
        expectedInventoryVersion = material.version,
        remark = remark,
        remoteCall = { clientOpId ->
            api.createTransferRequest(
                clientOperationId = clientOpId,
                type = "INBOUND",
                documentNo = null,
                items = listOf(
                    TransferItem(
                        materialId = material.material.id,
                        quantity = quantity,
                        targetLocationCode = targetLocation ?: material.primaryLocation,
                        expectedInventoryVersion = material.version
                    )
                ),
                remark = remark
            )
        }
    )

    /** 提交库位绑定（简化流程，无需审批） */
    suspend fun submitLocationBinding(
        materialCode: String,
        locationCode: String,
        quantity: Int
    ): SubmitResult = submit(
        opType = OfflineOpType.LOCATION_BIND,
        materialCode = materialCode,
        materialId = null,
        quantity = quantity,
        targetLocation = locationCode,
        expectedInventoryVersion = null,
        remark = null,
        remoteCall = { _ ->
            api.bindLocation(materialCode, locationCode, quantity)
        }
    )

    /**
     * 通用提交逻辑：先尝试远程，失败按可重试性决定是否入本地队列。
     */
    private suspend fun submit(
        opType: OfflineOpType,
        materialCode: String,
        materialId: String?,
        quantity: Int,
        targetLocation: String?,
        expectedInventoryVersion: Int?,
        remark: String?,
        remoteCall: suspend (String) -> Any
    ): SubmitResult {
        val clientOpId = UUID.randomUUID().toString()

        // 数量校验（契约：非负整数）
        if (quantity < 0) {
            return SubmitResult.Failure("数量必须是非负整数", retryable = false)
        }

        return try {
            val result = remoteCall(clientOpId)
            SubmitResult.Success(result, serverTime = null)
        } catch (e: ApiException) {
            when {
                // 业务失败：不入队，直接报错
                !e.retryable && e.statusCode in 400..499 ->
                    SubmitResult.Failure(e.message, retryable = false, traceId = e.traceId)
                // 可重试错误：落本地队列
                else -> {
                    enqueue(opType, clientOpId, materialCode, materialId, quantity,
                        targetLocation, expectedInventoryVersion, remark, e.message)
                    SubmitResult.Queued(clientOpId, e.message)
                }
            }
        } catch (e: Exception) {
            // 网络异常：落本地队列
            enqueue(opType, clientOpId, materialCode, materialId, quantity,
                targetLocation, expectedInventoryVersion, remark, e.message)
            SubmitResult.Queued(clientOpId, e.message ?: "网络不可用")
        }
    }

    private suspend fun enqueue(
        opType: OfflineOpType,
        clientOpId: String,
        materialCode: String,
        materialId: String?,
        quantity: Int,
        targetLocation: String?,
        expectedInventoryVersion: Int?,
        remark: String?,
        error: String?
    ) {
        dao.insert(
            OfflineOperationEntity(
                id = clientOpId,
                clientOperationId = clientOpId,
                materialCode = materialCode,
                operationType = opType.name,
                status = SyncStatus.PENDING.name,
                createdAt = System.currentTimeMillis(),
                quantity = quantity,
                targetLocation = targetLocation,
                materialId = materialId,
                expectedInventoryVersion = expectedInventoryVersion,
                remark = remark,
                errorMessage = error
            )
        )
    }

    // ==================== 离线队列 ====================

    /** 观察队列（UI 展示用） */
    fun observeQueue(): Flow<List<OfflineOperation>> =
        dao.observeAll().map { list -> list.map { it.toDomain() } }

    /** 观察待同步数量（顶部横幅用） */
    fun observePendingCount(): Flow<Int> = dao.observePendingCount()

    /**
     * 同步全部待处理记录。
     * 逐条重放，成功标记 SYNCED，失败标记 FAILED 并记录原因；
     * 冲突（409）标记 CONFLICT，交由用户决定。
     */
    suspend fun syncPending(): SyncReport {
        val pending = dao.pending()
        var success = 0
        var failed = 0
        var conflict = 0

        for (item in pending) {
            dao.updateStatus(item.id, SyncStatus.SYNCING.name)
            try {
                when (OfflineOpType.valueOf(item.operationType)) {
                    OfflineOpType.INBOUND, OfflineOpType.OUTBOUND -> {
                        if (item.materialId == null) {
                            dao.updateStatus(item.id, SyncStatus.FAILED.name, "缺少物料 ID，无法重放")
                            failed++
                            continue
                        }
                        api.createTransferRequest(
                            clientOperationId = item.clientOperationId,
                            type = if (item.operationType == OfflineOpType.INBOUND.name) "INBOUND" else "OUTBOUND",
                            documentNo = null,
                            items = listOf(
                                TransferItem(
                                    materialId = item.materialId,
                                    quantity = item.quantity,
                                    targetLocationCode = item.targetLocation,
                                    expectedInventoryVersion = item.expectedInventoryVersion
                                )
                            ),
                            remark = item.remark
                        )
                    }
                    OfflineOpType.LOCATION_BIND -> {
                        api.bindLocation(
                            materialCode = item.materialCode,
                            locationCode = item.targetLocation ?: "",
                            quantity = item.quantity
                        )
                    }
                    else -> {
                        // 调拨 / 盘点 / 异常：V1 API 已就绪但 UI 尚未接入，标记待处理
                        dao.updateStatus(item.id, SyncStatus.FAILED.name, "该操作类型暂未接入同步")
                        failed++
                        continue
                    }
                }
                dao.markSynced(item.id, SyncStatus.SYNCED.name, serverTime = null)
                success++
            } catch (e: ApiException) {
                val status = if (e.isConflict) SyncStatus.CONFLICT else SyncStatus.FAILED
                dao.updateStatus(item.id, status.name, e.message)
                if (e.isConflict) conflict++ else failed++
            } catch (e: Exception) {
                dao.updateStatus(item.id, SyncStatus.FAILED.name, e.message)
                failed++
            }
        }
        return SyncReport(success = success, failed = failed, conflict = conflict)
    }

    /** 清理已同步记录 */
    suspend fun clearSynced() = dao.clearSynced()

    companion object {
        /** 契约 4.1 请求中的 clientVersion 字段 */
        const val CLIENT_VERSION = "0.3.0"

        @Volatile
        private var instance: LogisticsRepository? = null

        fun get(context: Context): LogisticsRepository = instance ?: synchronized(this) {
            instance ?: LogisticsRepository(
                api = MaterialFlowApi(),
                dao = DatabaseProvider.get(context).offlineOperationDao()
            ).also { instance = it }
        }
    }
}

/** 提交结果 */
sealed interface SubmitResult {
    /** 直接提交成功 */
    data class Success(val data: Any?, val serverTime: String?) : SubmitResult
    /** 已写入本地队列，等待联网同步 */
    data class Queued(val clientOperationId: String, val reason: String?) : SubmitResult
    /** 提交失败且不可重试（业务错误） */
    data class Failure(
        val message: String,
        val retryable: Boolean,
        val traceId: String? = null
    ) : SubmitResult
}

/** 同步结果报告 */
data class SyncReport(val success: Int, val failed: Int, val conflict: Int) {
    val hasIssue: Boolean get() = failed > 0 || conflict > 0
}

/** 本地实体 → 领域模型 */
private fun OfflineOperationEntity.toDomain(): OfflineOperation = OfflineOperation(
    id = id,
    clientOperationId = clientOperationId,
    opType = runCatching { OfflineOpType.valueOf(operationType) }.getOrDefault(OfflineOpType.INBOUND),
    materialCode = materialCode,
    quantity = quantity,
    targetLocation = targetLocation,
    status = runCatching { SyncStatus.valueOf(status) }.getOrDefault(SyncStatus.PENDING),
    createdAt = createdAt,
    serverTime = serverTime,
    errorMessage = errorMessage
)
