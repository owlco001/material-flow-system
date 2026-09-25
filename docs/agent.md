# Agent 功能说明（数据分析对话 + 智能导入）

## 概述

后端内置 Agent 模块（`material-flow-backend/app/agent/`），提供两类能力：

1. **数据分析对话**：自然语言提问，Agent 调用只读工具查询业务数据并给出中文结论。
2. **智能导入**：上传 Excel/CSV，Agent 自动映射列、校验数据，预览确认后批量写入。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `AGENT_ENABLED` | `1` | 设为 `0` 关闭所有 Agent 接口 |
| `AGENT_LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容接口地址 |
| `AGENT_LLM_API_KEY` | 空 | 未配置时对话接口返回 503；导入的列映射自动降级为规则匹配 |
| `AGENT_LLM_MODEL` | `deepseek-chat` | 模型名 |
| `AGENT_LLM_TIMEOUT_S` | `60` | 单次 LLM 调用超时（秒） |
| `AGENT_MAX_ITERS` | `8` | 对话最大推理轮次 |

无新增第三方依赖，LLM 客户端为标准库实现。

## 接口

### 对话

- `POST /api/v1/agent/chat` — `{"session_id"?,"message"}` → `{"session_id","reply","iterations","usage"}`。任意登录用户可用（只读）。
- `GET /api/v1/agent/sessions` — 会话列表。
- `GET /api/v1/agent/sessions/{id}/messages` — 会话消息。

分析工具（10 个，只读）：订单详情/列表、物料库存、低库存排行、工作台汇总、流转申请、交接、异常、机台任务进度、工时汇总。

### 智能导入

- `POST /api/v1/agent/import/preview` — multipart `file` + `target`（`materials`/`inventory`/`orders`）→ 列映射、校验、生成导入任务（`jobId`），返回 `canCommit` 与错误明细。
- `GET /api/v1/agent/import/jobs/{job_id}` — 查询任务。
- `POST /api/v1/agent/import/commit` — `{"job_id","client_operation_id"}` → 批量写入。幂等：同一幂等键重放返回存档结果；有校验错误时拒绝（422）。

导入鉴权角色：`ADMIN / PLANNER / WORKSHOP_SUPERVISOR / WAREHOUSE_ADMIN / MATERIAL`。

## 安全阀

- 分析 Agent 只能读不能写；写操作只有"预览 → 人工确认 → 提交"一条路径。
- 提交走事务 + 幂等键 + `audit_events` 留痕（`event_type='AGENT_IMPORT'`）。
- 会话/用量/导入任务按用户隔离。

## 表结构

`agent_sessions`、`agent_messages`、`agent_import_jobs`、`agent_import_operations`、`agent_usage`，由 `init_db` 自动创建。

## 测试

`tests/test_agent.py`（10 用例）：鉴权、503 降级、ReAct 循环（桩 LLM）、导入三目标、校验拦截、幂等、角色隔离、禁用开关。
