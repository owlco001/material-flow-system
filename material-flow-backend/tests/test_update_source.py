"""更新源：GitHub 不通时回退 Gitee 镜像。"""
import app.main  # noqa: F401  先加载主模块，避免循环导入
import app.updates as u


def _fake(responses):
    calls = []

    def f(url, timeout=15):
        calls.append(url)
        for key, val in responses.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(url)
    return f, calls


COMMIT = {"sha": "a" * 40, "commit": {"message": "msg\nbody", "author": {"date": "2026-10-10"}}}


def test_auto_falls_back_to_gitee(monkeypatch):
    f, calls = _fake({"api.github.com": OSError("timeout"), "gitee.com": COMMIT})
    monkeypatch.setattr(u, "_github_json", f)
    monkeypatch.setattr(u, "UPDATE_SOURCE", "auto")
    r = u.check_github_update()
    assert r["source"] == "gitee" and r["latestSha"] == "a" * 40 and len(calls) == 2


def test_auto_prefers_github(monkeypatch):
    f, calls = _fake({"api.github.com": COMMIT})
    monkeypatch.setattr(u, "_github_json", f)
    monkeypatch.setattr(u, "UPDATE_SOURCE", "auto")
    assert u.check_github_update()["source"] == "github" and len(calls) == 1


def test_gitee_only_and_both_fail(monkeypatch):
    f, calls = _fake({"gitee.com": COMMIT, "api.github.com": COMMIT})
    monkeypatch.setattr(u, "_github_json", f)
    monkeypatch.setattr(u, "UPDATE_SOURCE", "gitee")
    assert u.check_github_update()["source"] == "gitee" and "gitee.com" in calls[0]
    f, _ = _fake({"api.github.com": OSError("x"), "gitee.com": OSError("y")})
    monkeypatch.setattr(u, "_github_json", f)
    monkeypatch.setattr(u, "UPDATE_SOURCE", "auto")
    try:
        u.check_github_update()
        raise AssertionError("should fail")
    except u.ApiError as e:
        assert e.code == "GITHUB_ERROR" and "gitee" in str(e.detail)
