from app.audit_labels import action_label, resource_label, result_label, role_label


def test_known_and_fallback_labels():
    assert action_label("LOGIN") == "登录"
    assert action_label("BATCH_OUTBOUND") == "批量出库"
    assert action_label("SOMETHING_WEIRD") == "SOMETHING_WEIRD"
    assert action_label(None) is None
    assert resource_label("app_release") == "App 版本"
    assert resource_label("system") == "系统"
    assert result_label("SUCCESS") == "成功"
    assert result_label("PARTIAL") == "部分成功"
    assert role_label("WAREHOUSE_ADMIN") == "仓库管理员"
