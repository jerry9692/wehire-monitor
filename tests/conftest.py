"""共享 fixtures"""
import pytest
from pathlib import Path
import tempfile


@pytest.fixture
def tmp_db_path(tmp_path):
    """临时 SQLite 路径"""
    return str(tmp_path / "test.sqlite")


@pytest.fixture
def sample_accounts_yaml(tmp_path):
    """示例 accounts.yaml"""
    content = """
accounts:
  - name: "上海国资招聘"
    alias: ["上海国资", "国资招聘"]
    priority: high
    enabled: true
  - name: "金融招聘信息"
    priority: medium
    enabled: true
"""
    p = tmp_path / "accounts.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


@pytest.fixture
def sample_rules_yaml(tmp_path):
    """示例 rules.yaml"""
    content = """
match_rules:
  locations:
    include: ["上海", "杭州"]
    exclude: ["境外"]
  job_keywords:
    include: ["金融", "数据分析"]
    exclude: ["实习"]
  companies:
    include: ["银行", "证券"]
  notify_min_score: 70

notify:
  max_per_run: 20
  push_when_empty: false
  email_mask: true

schedule:
  daily_at: ["08:30", "20:30"]
  window_hours: 36
  max_articles_per_run: 80
  max_articles_per_account: 10
"""
    p = tmp_path / "rules.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)
