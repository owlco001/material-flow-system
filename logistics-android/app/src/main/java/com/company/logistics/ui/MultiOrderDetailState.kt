package com.company.logistics.ui

import com.company.logistics.model.OrderDetail
import com.company.logistics.model.OrderMaterialStatus

/** Lifecycle state for one order detail request. */
data class OrderDetailLoadState(
    val isLoading: Boolean = false,
    val error: String? = null,
    val content: OrderDetail? = null,
    /** Authoritative paged material-status projection; never reconstruct it from OrderDetail. */
    val materialStatus: OrderMaterialStatus? = null,
    /** Identity of the current first-page material fetch. */
    val materialRequestId: Long = 0L,
    /** At most one append request per order/material generation. */
    val materialAppendRequestId: Long? = null,
    /** Monotonically increasing detail request token. */
    val requestId: Long = 0L,
)

data class MultiOrderEntry(
    val orderNo: String,
    val state: OrderDetailLoadState,
)

data class MultiOrderSnapshot(
    val selectedOrderNo: String?,
    val entries: List<MultiOrderEntry>,
)

/**
 * Pure, UI-independent state container for parallel order detail screens.
 *
 * The map is access ordered: selecting an order, starting a request, and applying a
 * matching result make that order most recently used. Results are keyed by orderNo,
 * so a late response cannot mutate another order's state.
 */
class MultiOrderDetailState(
    private val maxOrders: Int = DEFAULT_MAX_ORDERS,
) {
    init {
        require(maxOrders in 1..MAX_SUPPORTED_ORDERS) { "maxOrders must be between 1 and $MAX_SUPPORTED_ORDERS" }
    }

    private val entries = LinkedHashMap<String, OrderDetailLoadState>(maxOrders, 0.75f, true)
    private var nextRequestId = 0L
    private var selectedOrderNo: String? = null

    val currentOrderNo: String? get() = selectedOrderNo
    val orderNos: List<String> get() = entries.keys.toList()
    val size: Int get() = entries.size

    fun snapshot(): MultiOrderSnapshot = MultiOrderSnapshot(
        selectedOrderNo = selectedOrderNo,
        entries = entries.map { (orderNo, state) -> MultiOrderEntry(orderNo, state) },
    )

    fun state(orderNo: String): OrderDetailLoadState? = entries[orderNo]

    fun select(orderNo: String): OrderDetailLoadState {
        require(orderNo.isNotBlank()) { "orderNo must not be blank" }
        selectedOrderNo = orderNo
        return touch(orderNo)
    }

    fun beginMaterialRefresh(orderNo: String): Long {
        val old = touch(orderNo)
        val generation = ++nextRequestId
        entries[orderNo] = old.copy(materialRequestId = generation, materialStatus = null, materialAppendRequestId = null)
        return generation
    }

    fun beginMaterialAppend(orderNo: String): Pair<Long, Long>? {
        val old = entries[orderNo] ?: return null
        val status = old.materialStatus ?: return null
        if (!status.hasMore || old.materialAppendRequestId != null) return null
        val requestId = ++nextRequestId
        entries[orderNo] = old.copy(materialAppendRequestId = requestId)
        return old.materialRequestId to requestId
    }

    fun applyMaterialSuccess(orderNo: String, generation: Long, value: OrderMaterialStatus): Boolean =
        updateMaterialIfCurrent(orderNo, generation) { old ->
            require(value.page == 1) { "Material refresh must start at page 1" }
            value.copy(items = value.items.distinctBy { (it.deviceId ?: "") + "|" + it.materialId })
        }

    fun isMaterialAppendRequestCurrent(orderNo: String, generation: Long, requestId: Long): Boolean =
        entries[orderNo]?.let { it.materialRequestId == generation && it.materialAppendRequestId == requestId } == true

    fun appendMaterialSuccess(orderNo: String, generation: Long, requestId: Long, value: OrderMaterialStatus): Boolean {
        val old = entries[orderNo] ?: return false
        if (old.materialRequestId != generation || old.materialAppendRequestId != requestId) return false
        val base = old.materialStatus ?: return false
        if (value.documentNo != orderNo || value.page != base.page + 1) {
            entries[orderNo] = old.copy(materialAppendRequestId = null)
            return false
        }
        val merged = (base.items + value.items).distinctBy { (it.deviceId ?: "") + "|" + it.materialId }
        entries[orderNo] = old.copy(materialStatus = value.copy(items = merged), materialAppendRequestId = null)
        evictIfNeeded()
        return true
    }

    fun appendMaterialError(orderNo: String, generation: Long, requestId: Long): Boolean {
        val old = entries[orderNo] ?: return false
        if (old.materialRequestId != generation || old.materialAppendRequestId != requestId) return false
        entries[orderNo] = old.copy(materialAppendRequestId = null)
        return true
    }

    private fun updateMaterialIfCurrent(
        orderNo: String, generation: Long, update: (OrderMaterialStatus?) -> OrderMaterialStatus?,
    ): Boolean {
        val old = entries[orderNo] ?: return false
        if (old.materialRequestId != generation) return false
        entries[orderNo] = old.copy(materialStatus = update(old.materialStatus), materialAppendRequestId = null)
        evictIfNeeded()
        return true
    }

    /** Explicit identity boundary: an order entry must not survive logout/account changes. */
    fun clear() {
        nextRequestId++
        entries.clear()
        selectedOrderNo = null
    }

    /** Starts a refresh only for [orderNo], returning its opaque request token. */
    fun beginRefresh(orderNo: String): Long {
        require(orderNo.isNotBlank()) { "orderNo must not be blank" }
        val old = touch(orderNo)
        val requestId = ++nextRequestId
        entries[orderNo] = old.copy(isLoading = true, error = null, requestId = requestId)
        evictIfNeeded()
        return requestId
    }

    fun applySuccess(orderNo: String, requestId: Long, content: OrderDetail): Boolean =
        updateIfCurrent(orderNo, requestId) {
            it.copy(isLoading = false, error = null, content = content)
        }

    fun applyError(orderNo: String, requestId: Long, error: String): Boolean =
        updateIfCurrent(orderNo, requestId) {
            it.copy(isLoading = false, error = error, content = it.content)
        }

    private fun updateIfCurrent(orderNo: String, requestId: Long, update: (OrderDetailLoadState) -> OrderDetailLoadState): Boolean {
        val current = entries[orderNo] ?: return false
        if (current.requestId != requestId) return false
        entries[orderNo] = update(current)
        evictIfNeeded()
        return true
    }

    private fun touch(orderNo: String): OrderDetailLoadState {
        val existing = entries[orderNo]
        if (existing != null) return existing
        val created = OrderDetailLoadState()
        entries[orderNo] = created
        evictIfNeeded()
        return entries[orderNo] ?: created
    }

    private fun evictIfNeeded() {
        while (entries.size > maxOrders) {
            val eldest = entries.entries.iterator().next()
            if (eldest.key == selectedOrderNo && entries.size > 1) {
                val selected = eldest.value
                entries.remove(eldest.key)
                entries[eldest.key] = selected
            } else {
                if (eldest.key == selectedOrderNo) selectedOrderNo = null
                entries.remove(eldest.key)
            }
        }
    }

    companion object {
        const val DEFAULT_MAX_ORDERS = 5
        const val MAX_SUPPORTED_ORDERS = 5
    }
}
