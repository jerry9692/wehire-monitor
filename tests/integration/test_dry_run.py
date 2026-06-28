"""端到端 dry-run 集成测试"""
import os
import sqlite3
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from wehire_monitor.pipeline.runner import PipelineRunner
from wehire_monitor.domain.models import ArticleMeta


def test_dry_run_full_pipeline_no_side_effects(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """dry-run 模式不产生副作用:不推送、不写文章数据、不发 HTTP 请求"""
    with PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=True,
    ) as runner:
        with patch.object(runner.notifier, "send_daily") as mock_notify, \
             patch.object(runner.notifier, "send_alert") as mock_alert:
            mock_notify.return_value = MagicMock(success=True, pushed_count=0)
            mock_alert.return_value = MagicMock(success=True)
            runner.run()

            # 不应调用推送或告警
            mock_notify.assert_not_called()
            mock_alert.assert_not_called()

    # run_log 应存在且有 ended_at
    conn = sqlite3.connect(tmp_db_path)
    conn.row_factory = sqlite3.Row
    run = conn.execute("SELECT * FROM run_logs WHERE run_id = ?", (runner.run_id,)).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    conn.close()
    assert run is not None
    assert run["ended_at"] is not None
    assert run["error_summary"] is None  # dry-run 不应有错误
    assert count == 0  # dry-run 不写文章


def test_dry_run_processes_injected_articles(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """dry-run 可以处理注入的文章列表(用于测试)而不写库"""
    import hashlib
    mock_articles = [
        ArticleMeta("号A", "招聘公告", "https://a.com", datetime.now(timezone.utc)),
    ]

    with PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=True,
    ) as runner:
        runner.prefilter.hit_words = ["招聘", "岗位", "投递"]
        runner.prefilter.exclude_words = ["实习"]

        with patch.object(runner.parser, "parse") as mock_parse, \
             patch.object(runner.notifier, "send_daily") as mock_notify:
            mock_parse.return_value = MagicMock(
                article_id=hashlib.sha256(b"https://a.com").hexdigest(),
                title="招聘公告",
                plain_text="招聘 岗位 投递 hr@test.com",
                images=[],
                content_hash="chash",
            )
            mock_notify.return_value = MagicMock(success=True, pushed_count=0)

            # dry-run 下 all_articles 为空(跳过抓取),手动注入
            runner._process_articles(mock_articles)
            runner._notify(candidate_count=1, fetched_count=1)

            # parse 被调用(dry-run 走解析)
            mock_parse.assert_called_once()
            # dry-run 不推送
            mock_notify.assert_not_called()

    # 验证文章未写入 DB
    conn = sqlite3.connect(tmp_db_path)
    count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    conn.close()
    assert count == 0
