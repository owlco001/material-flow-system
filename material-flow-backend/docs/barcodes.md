# 条码生成与打印

订单、机台、物料三类业务编号的二维码（QR）与一维码（Code128）生成，
支持 PNG / SVG 两种图片格式，以及管理台的预览、下载与批量打印。

## 接口

### `GET /api/v1/barcodes/{entity}/{key}`

需要 Bearer Token 鉴权（任意已登录用户）。

| 参数 | 位置 | 取值 | 说明 |
|---|---|---|---|
| `entity` | 路径 | `order` / `device` / `material` | 订单 / 机台 / 物料 |
| `key` | 路径 | 业务编号 | 订单号 / 机台号 / 物料编码 |
| `kind` | 查询 | `qr`（默认）/ `code128` | 二维码 / 一维码 |
| `image` | 查询 | `png`（默认）/ `svg` | 位图 / 矢量 |

实体映射：

- `order` → `production_orders.order_no`
- `device` → `devices.device_no`
- `material` → `materials.code`

编号不存在返回 404；`kind` / `image` 非法返回 422。

示例：

```
GET /api/v1/barcodes/order/26B-013?kind=qr&image=png
GET /api/v1/barcodes/material/MTR-001?kind=code128&image=svg
GET /api/v1/barcodes/device/DEV-01?kind=qr
```

响应为图片字节流（`image/png` / `image/svg+xml`），附带：

- `Content-Disposition: inline; filename="order-26B-013.png"`（文件名仅保留 ASCII 安全字符）
- `Cache-Control: private, max-age=86400`
- `X-Barcode-Entity` / `X-Barcode-Kind` 响应头

### 条码内容与扫码兼容

条码直接编码业务编号本身，不做二次封装。订单号与物料编码可被
`POST /api/v1/scan/resolve` 直接识别（`PRODUCTION_ORDER` / `MATERIAL_CODE`）。
机台号按现有冻结契约在扫码解析中保持 `UNKNOWN`，其条码仅用于展示与打印。

## 管理台

管理台「条码」入口（需管理员会话登录）：

- `GET /admin/barcodes` —— 预览页：选择实体、输入编号、切换二维码/一维码，
  预览 PNG 并可直接下载。
- `GET /admin/barcodes/print?entity=order&key=26B-013&kind=qr&copies=10` ——
  打印页：A4 标签布局，按 `copies`（1–50）份平铺，页面加载后自动弹出打印对话框。
- `GET /admin/barcodes/image?entity=...&key=...&kind=...&image=...` ——
  会话鉴权的图片下载，供浏览器直接点击（API 接口需要 Bearer Token，
  浏览器地址栏直接访问会 401，因此管理台下载走此路由）。

## 打印说明

当前为浏览器打印方案：在打印页用 A4 纸打印标签，由浏览器打印对话框选择
打印机。不包含 ZPL / TSPL 等标签打印机直连指令。如需直连热敏/标签打印机，
可基于本接口的 PNG / SVG 输出另行对接。

## 实现位置

- 生成逻辑：`app/barcodes.py`（`render` / `data_uri` / `resolve_payload`）
- API 路由：`app/main.py` → `GET /api/v1/barcodes/{entity}/{key}`
- 管理台路由：`app/admin_web.py` → `/admin/barcodes`、`/admin/barcodes/print`、
  `/admin/barcodes/image`
- 模板：`app/templates/barcodes.html`、`app/templates/barcodes_print.html`
- 测试：`tests/test_barcodes.py`

依赖：`qrcode`、`python-barcode`、`pillow`（见 `requirements.txt`）。
