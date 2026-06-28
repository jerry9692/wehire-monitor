"""端到端 dry-run 集成测试"""
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from wehire_monitor.pipeline.runner import PipelineRunner
from wehire_monitor.domain.models import ArticleMeta


def test_dry_run_full_pipeline_no_side_effects(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """dry-run 模式不产生副作用:不推送、不写库(除了 run_log)"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=True,
    )

    with patch.object(runner.notifier, "send_daily") as mock_notify:
        mock_notify.return_value = MagicMock(success=True)
        runner.run()

        # 不应调用推送
        mock_notify.assert_not_called()

    # run_log 应存在
    run = runner.repo.get_run(runner.run_id)
    assert run is not None
    assert run["ended_at"] is not None


def test_dry_run_with_mocked_fetch(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """dry-run + mock 抓取:验证流程跑通"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=True,
    )

    mock_articles = [
        ArticleMeta("号A", "招聘公告", "https://a.com", datetime.now(timezone.utc)),
    ]

    with patch.object(runner, "_init_fetcher"), \
         patch.object(runner, "_fetch_account", return_value=mock_articles), \
         patch.object(runner.parser, "parse") as mock_parse:
        mock_parse.return_value = MagicMock(
            article_id="hashA",
            title="招聘公告",
            plain_text="招聘 岗位 投递 hr@test.com",
            images=[],
            content_hash="chash",
        )
        # 跳过 Cookie 检测
        with patch.object(runner.config_loader, "is_cookie_stale", return_value=False):
            runner.run()

    # dry-run 不写文章,但流程应跑通不崩溃
    assert runner.run_id is not None
