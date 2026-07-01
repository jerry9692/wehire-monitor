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
    """Cookie 超过 48h 应返回需告警(使用上海时区时间)"""
    from zoneinfo import ZoneInfo
    tz_sh = ZoneInfo("Asia/Shanghai")
    stale_time = (datetime.now(tz_sh) - timedelta(hours=49)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setenv("COOKIE_UPDATED_AT", stale_time)
    loader = ConfigLoader()
    assert loader.is_cookie_stale() is True


def test_cookie_age_check_ok_when_fresh(monkeypatch):
    """Cookie 在 48h 内不应告警(使用上海时区时间)"""
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


# ========== v0.4: update_env_cookie 测试 ==========

def test_update_env_cookie_writes_new_values(tmp_path):
    """update_env_cookie 写入新的 Cookie/Token/时间戳"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WECHAT_MP_COOKIE=old_cookie\n"
        "WECHAT_MP_TOKEN=old_token\n"
        "COOKIE_UPDATED_AT=2026-06-01 00:00:00\n"
        "OTHER_VAR=keep_me\n"
    )
    loader = ConfigLoader(config_dir=str(tmp_path))
    loader.update_env_cookie(
        cookie="new_cookie",
        token="new_token",
    )
    content = env_file.read_text()
    assert "WECHAT_MP_COOKIE=new_cookie" in content
    assert "WECHAT_MP_TOKEN=new_token" in content
    assert "OTHER_VAR=keep_me" in content
    # COOKIE_UPDATED_AT 应被更新为当前时间
    assert "2026-06-01" not in content


def test_update_env_cookie_creates_missing_keys(tmp_path):
    """.env 中缺少 KEY 时自动添加"""
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER_VAR=keep_me\n")
    loader = ConfigLoader(config_dir=str(tmp_path))
    loader.update_env_cookie(cookie="abc", token="123")
    content = env_file.read_text()
    assert "WECHAT_MP_COOKIE=abc" in content
    assert "WECHAT_MP_TOKEN=123" in content
    assert "COOKIE_UPDATED_AT=" in content
    assert "OTHER_VAR=keep_me" in content


def test_update_env_cookie_empty_env(tmp_path):
    """.env 不存在时创建新文件"""
    env_file = tmp_path / ".env"
    loader = ConfigLoader(config_dir=str(tmp_path))
    loader.update_env_cookie(cookie="abc", token="123")
    content = env_file.read_text()
    assert "WECHAT_MP_COOKIE=abc" in content
    assert "WECHAT_MP_TOKEN=123" in content
    assert "COOKIE_UPDATED_AT=" in content


# ========== v0.4: Cookie 过期阈值 48h 测试 ==========

def test_is_cookie_stale_default_48h(tmp_path, monkeypatch):
    """默认阈值应为 48 小时"""
    env_file = tmp_path / ".env"
    env_file.write_text("")
    loader = ConfigLoader(config_dir=str(tmp_path))
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Shanghai")
    # 47 小时前 → 不应过期
    past = (datetime.now(tz) - timedelta(hours=47)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setenv("COOKIE_UPDATED_AT", past)
    assert loader.is_cookie_stale() is False

    # 49 小时前 → 应过期
    past2 = (datetime.now(tz) - timedelta(hours=49)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setenv("COOKIE_UPDATED_AT", past2)
    assert loader.is_cookie_stale() is True
