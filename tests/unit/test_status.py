"""Status 枚举测试"""
from wehire_monitor.domain.status import Status


def test_status_has_all_main_states():
    """状态机必须包含所有主流程状态"""
    expected = {
        "discovered", "fetched", "parsed", "ignored", "candidate",
        "notified", "archived",
    }
    actual = {s.value for s in Status}
    assert expected.issubset(actual)


def test_status_has_all_error_states():
    """状态机必须包含所有错误/待处理状态"""
    expected = {
        "error_fetch", "error_parse",
        "need_cookie", "need_captcha", "need_review",
    }
    actual = {s.value for s in Status}
    assert expected.issubset(actual)


def test_status_is_string_enum():
    """Status 值必须是字符串,用于 SQLite 存储"""
    assert Status.DISCOVERED.value == "discovered"
    assert isinstance(Status.DISCOVERED.value, str)
