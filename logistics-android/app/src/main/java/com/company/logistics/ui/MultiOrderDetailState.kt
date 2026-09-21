package com.company.logistics.ui

import com.company.logistics.model.OrderDetail

/** Lifecycle state for one order detail request. */
data class OrderDetailLoadState(
    val isLoading: Boolean = false,
    val error: String? = null,
    val content: OrderDetail? = null,
    /** Monotonically increasing request id; results must match it before applying. */
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
