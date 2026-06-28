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
    """Cookie 超过 24h 应返回需告警"""
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setenv("COOKIE_UPDATED_AT", stale_time)
    loader = ConfigLoader()
    assert loader.is_cookie_stale() is True


def test_cookie_age_check_ok_when_fresh(monkeypatch):
    """Cookie 在 24h 内不应告警"""
    fresh_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setenv("COOKIE_UPDATED_AT", fresh_time)
    loader = ConfigLoader()
    assert loader.is_cookie_stale() is False


def test_cookie_age_check_handles_missing(monkeypatch):
    """无 COOKIE_UPDATED_AT 时不崩溃,返回需告警"""
    monkeypatch.delenv("COOKIE_UPDATED_AT", raising=False)
    loader = ConfigLoader()
    assert loader.is_cookie_stale() is True
