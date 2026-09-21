# Multi-order detail state

`MultiOrderDetailState` is the UI-independent state layer for a future multi-order detail surface. It keeps up to five order numbers in access-order LRU storage and gives each order independent loading, error, and `OrderDetail` content state.

Refresh integration points:

1. Call `select(orderNo)` when the user changes the active order.
2. Call `beginRefresh(orderNo)` for the target order and retain the returned request id in the coroutine/request closure.
3. Feed success to `applySuccess(orderNo, requestId, detail)` and failure to `applyError(orderNo, requestId, message)`.
4. Render `state(orderNo)` rather than a single global detail/loading/error tuple.

Results are accepted only when the order exists and its request id still matches. Thus a late response from an older refresh cannot replace a newer result, and a response for one order cannot alter another order. Navigation and Compose wiring remain intentionally unchanged.
