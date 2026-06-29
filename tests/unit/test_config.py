"""配置加载测试"""
import os
from datetime import datetime, timezone, timedelta

from wehire_monitor.config.loader import ConfigLoader


def test_load_accounts(sample_accounts_yaml):
    loader = ConfigLoader(accounts_path=sample_accounts_yaml)
    accounts = loader.load_accounts()
    assert len(accounts) == 2
    assert accounts[0].name == "上海国资招聘"
    assert accounts[0].alias == ["上海国资", "国资招聘"]
    assert accounts[0].priority == "high"
    assert accounts[0].enabled is True
    assert accounts[1].alias == []


def test_load_rules(sample_rules_yaml):
    loader = ConfigLoader(rules_path=sample_rules_yaml)
    rules = loader.load_rules()
    assert rules.match_rules.notify_min_score == 70
    assert "上海" in rules.match_rules.locations.include
    assert "境外" in rules.match_rules.locations.exclude
    assert rules.notify.max_per_run == 20
    assert rules.schedule.window_hours == 36
    assert rules.schedule.daily_at == ["08:30", "20:30"]


def test_cookie_age_check_alerts_when_stale(monkeypatch):
    """Cookie 超过 24h 应返回需告警(使用上海时区时间)"""
    from zoneinfo import ZoneInfo
    tz_sh = ZoneInfo("Asia/Shanghai")
    stale_time = (datetime.now(tz_sh) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setenv("COOKIE_UPDATED_AT", stale_time)
    loader = ConfigLoader()
    assert loader.is_cookie_stale() is True


def test_cookie_age_check_ok_when_fresh(monkeypatch):
    """Cookie 在 24h 内不应告警(使用上海时区时间)"""
    from zoneinfo import ZoneInfo
    tz_sh = ZoneInfo("Asia/Shanghai")
    fresh_time = datetime.now(tz_sh).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setenv("COOKIE_UPDATED_AT", fresh_time)
    loader = ConfigLoader()
    assert loader.is_cookie_stale() is False


def test_cookie_age_check_timezone_consistency(monkeypatch):
    """同一时刻用 ISO8601+时区 和 空格格式(上海时间) 应得到接近一致的结果"""
    from zoneinfo import ZoneInfo
    tz_sh = ZoneInfo("Asia/Shanghai")
    now_sh = datetime.now(tz_sh) - timedelta(hours=12)

    # 方式1: ISO8601 带时区
    iso_str = now_sh.isoformat()
    monkeypatch.setenv("COOKIE_UPDATED_AT", iso_str)
    loader1 = ConfigLoader()
    result1 = loader1.is_cookie_stale()

    # 方式2: 空格格式(上海时间)
    space_str = now_sh.strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setenv("COOKIE_UPDATED_AT", space_str)
    loader2 = ConfigLoader()
    result2 = loader2.is_cookie_stale()

    assert result1 == result2  # 两种格式应产生一致结果


def test_cookie_age_check_handles_missing(monkeypatch):
    """无 COOKIE_UPDATED_AT 时不崩溃,返回需告警

    注意: 必须在 ConfigLoader() 构造之后删除 env,因为构造函数会调用
    load_dotenv() 从 config/.env 重新加载环境变量。
    """
    loader = ConfigLoader()
    monkeypatch.delenv("COOKIE_UPDATED_AT", raising=False)
    assert loader.is_cookie_stale() is True
