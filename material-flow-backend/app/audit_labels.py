"""审计记录的中文显示名（App 与管理台共用）。

动作/资源/结果/角色码保持英文存库，接口另给 *Label 字段；
不在表里的码按单词拆开翻译，仍有未知单词时原样返回。
"""
from __future__ import annotations

ACTION_LABELS = {
    "LOGIN": "登录",
    "LOGOUT": "退出登录",
    "LOGOUT_ALL": "退出全部设备",
    "REFRESH": "刷新登录",
    "REFRESH_REUSE_DETECTED": "登录令牌被重复使用",
    "CHANGE_PASSWORD": "修改密码",
    "INITIALIZE_ADMIN": "初始化管理员",
    "CREATE": "新建",
    "UPDATE": "修改",
    "DELETE": "删除",
    "HARD_DELETE": "彻底删除",
    "APPROVE": "批准",
    "EXECUTE": "执行",
    "CONFIRM": "确认",
    "REVIEW": "审核",
    "ASSIGN": "分配",
    "BIND": "绑定",
    "TRANSFER_HANDOVER": "流转交接",
    "DRAWING_UPLOAD": "上传图纸",
    "DRAWING_BIND": "绑定图纸",
    "DRAWING_UNBIND": "解绑图纸",
    "DRAWING_DELETE": "删除图纸",
    "DESIGN_PACKAGE_UPLOAD": "上传设计包",
    "DESIGN_PACKAGE_UPLOADED": "上传设计包",
    "DESIGN_PACKAGE_BIND": "绑定设计包",
    "DESIGN_PACKAGE_BOUND": "绑定设计包",
    "DESIGN_KEY_CREATE": "生成设计同步密钥",
    "DESIGN_KEY_UPDATE": "修改设计同步密钥",
    "DESIGN_KEY_REVOKE": "吊销设计同步密钥",
    "DESIGN_SYNC_IP_DENIED": "设计同步来源地址被拒绝",
    "PURGE_DEMO_DATA": "清除演示数据",
    "APP_RELEASE_SYNC": "同步 App 版本",
    "FLOW_TRANSFER_CREATED": "发起流转申请",
    "FLOW_TRANSFER_APPROVED": "批准流转申请",
    "FLOW_TRANSFER_EXECUTED": "执行流转申请",
    "TRANSFER_REJECTED": "驳回流转申请",
    "TRANSFER_HANDOVER_CONFIRMED": "确认流转交接",
    "HANDOVER_CREATED": "发起交接",
    "HANDOVER_CONFIRMED": "确认交接",
    "HANDOVER_REJECTED": "拒绝交接",
    "HANDOVER_CANCELLED": "取消交接",
    "OUTBOUND_APPROVED": "批准出库",
    "OUTBOUND_CONFIRMED": "确认出库",
    "MATERIAL_ACCEPTED": "确认领料",
    "MATERIAL_PICKED_UP": "领取物料",
    "MATERIAL_MASTER_IMPORTED": "导入物料主档",
    "ORDER_CREATED": "新建订单",
    "ORDER_STATUS_CHANGED": "订单状态变更",
    "ASSEMBLY_TASK_CREATED": "新建装配任务",
    "ASSEMBLY_TASK_ASSIGNED": "分配装配任务",
    "ASSEMBLY_TASK_MEMBER_REMOVED": "移除装配人员",
    "ASSEMBLY_STARTED": "开始装配",
    "ASSEMBLY_COMPLETED": "完成装配",
    "ASSEMBLY_STAGE_STARTED": "开始装配工序",
    "ASSEMBLY_STAGE_COMPLETED": "完成装配工序",
    "ASSEMBLY_PROGRESS_UPDATED": "更新装配进度",
    "TEMPORARY_TRANSFER_STARTED": "开始临时调拨",
    "TEMPORARY_TRANSFER_COMPLETED": "完成临时调拨",
}

RESOURCE_LABELS = {
    "USER": "用户",
    "SESSION": "登录会话",
    "SETUP": "系统初始化",
    "SYSTEM": "系统",
    "APP_RELEASE": "App 版本",
    "DEVICE": "机台",
    "DEVICE_MODEL_3D": "机台 3D 模型",
    "DRAWING": "图纸",
    "DESIGN_PACKAGE": "设计包",
    "DESIGN_KEY": "设计同步密钥",
    "EXCEPTION": "异常",
    "HANDOVER": "交接",
    "MATERIAL_HANDOVER": "物料交接",
    "LOCATION": "库位",
    "ORDER_MATERIALS": "订单物料",
    "PRODUCTION_ORDER": "生产订单",
    "STOCKTAKE": "盘点",
    "TRANSFER_REQUEST": "流转申请",
    "ASSEMBLY_TASK": "装配任务",
    "ASSEMBLY_TASK_STAGE": "装配工序",
    "TEMPORARY_TRANSFER": "临时调拨",
}

RESULT_LABELS = {
    "SUCCESS": "成功",
    "FAILED": "失败",
    "FAILURE": "失败",
    "PARTIAL": "部分成功",
    "LOCKED": "账号已锁定",
    "DENIED": "已拒绝",
    "REJECTED": "已拒绝",
    "PENDING": "处理中",
    "READY": "成功",
    "ERROR": "出错",
}

ROLE_LABELS = {
    "ADMIN": "管理员",
    "WAREHOUSE_ADMIN": "仓库管理员",
    "PLANNER": "计划员",
    "OPERATOR": "操作员",
    "MATERIAL": "物料员",
    "WORKSHOP_SUPERVISOR": "车间主管",
    "ASSEMBLER": "装配工",
    "DESIGN_KEY": "设计同步密钥",
}

_WORDS = {
    "BATCH": "批量", "CREATE": "新建", "CREATED": "新建", "UPDATE": "修改", "UPDATED": "修改",
    "DELETE": "删除", "DELETED": "删除", "APPROVE": "批准", "APPROVED": "批准",
    "EXECUTE": "执行", "EXECUTED": "执行", "REJECT": "驳回", "REJECTED": "驳回",
    "CONFIRM": "确认", "CONFIRMED": "确认", "CANCEL": "取消", "CANCELLED": "取消",
    "ASSIGN": "分配", "ASSIGNED": "分配", "REMOVED": "移除", "STARTED": "开始",
    "COMPLETED": "完成", "FAILED": "失败", "IMPORTED": "导入", "SYNC": "同步",
    "SYNCED": "同步", "BIND": "绑定", "UNBIND": "解绑", "UPLOAD": "上传",
    "OUTBOUND": "出库", "INBOUND": "入库", "RETURN": "退料", "TRANSFER": "调拨",
    "RELOCATION": "移库", "PICK": "领料", "STOCKTAKE": "盘点", "HANDOVER": "交接",
    "MATERIAL": "物料", "MATERIALS": "物料", "ORDER": "订单", "TASK": "任务",
    "ASSEMBLY": "装配", "STAGE": "工序", "DEVICE": "机台", "USER": "用户",
    "VALIDATION": "校验", "BOM": "BOM", "U9": "U9", "INVENTORY": "库存",
    "STATUS": "状态", "CHANGED": "变更", "REQUEST": "申请", "FLOW": "流转",
    "PREVIEW": "预览", "VIEW": "查看", "LOCATION": "库位", "EXCEPTION": "异常",
}


def _by_words(code: str) -> str | None:
    parts = [p for p in code.upper().split("_") if p]
    if not parts or any(p not in _WORDS for p in parts):
        return None
    return "".join(_WORDS[p] for p in parts)


def _label(table: dict[str, str], code: str | None) -> str | None:
    if not code:
        return None
    key = str(code).strip()
    upper = key.upper()
    return table.get(upper) or _by_words(upper) or key


def action_label(code: str | None) -> str | None:
    return _label(ACTION_LABELS, code)


def resource_label(code: str | None) -> str | None:
    return _label(RESOURCE_LABELS, code)


def result_label(code: str | None) -> str | None:
    return _label(RESULT_LABELS, code)


def role_label(code: str | None) -> str | None:
    return _label(ROLE_LABELS, code)
