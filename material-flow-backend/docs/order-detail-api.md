# Order detail aggregate contract

`GET /api/v1/orders/{orderNo}/detail?page=1&pageSize=20` returns only server facts:

- `orderId`, `orderNo`, `productName`, `orderStatus`
- `materials`: the same requirement/material fields as `POST /api/v1/orders/material-status`, including `deviceId` and `deviceNo`
- `assemblyTasks`: `taskId`, `deviceId`, `deviceNo`, `status`, `progressStage`, `taskVersion`, `assignedAssemblerId`
- `laborSummary`: completed `assemblyLaborMinutes`, independent `temporaryTransferLaborMinutes`, and their sum
- `timeline`: whitelisted existing audit facts with `type`, `entityId`, `status`, `serverTime`, `actorId`
- `page`, `pageSize`, `total` paginate assembly tasks

`ADMIN` and `WORKSHOP_SUPERVISOR` see all order tasks and labor. `ASSEMBLER` sees only assigned tasks, own labor, and material facts tied to assigned devices. Other roles retain existing material visibility and cannot use this aggregate to broaden it. Unknown orders return `404 ORDER_NOT_FOUND`. Sensitive audit payloads, source IPs, credentials, and internal metadata are never returned.
