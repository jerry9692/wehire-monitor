"""Pipeline Runner 测试"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from wehire_monitor.pipeline.runner import PipelineRunner
from wehire_monitor.domain.status import Status
from wehire_monitor.domain.models import ArticleMeta, ParsedArticle
from wehire_monitor.config.schemas import AccountConfig


def test_run_id_generated(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """run 应生成 run_id 并写入 run_logs"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=True,
    )
    runner.run()
    run = runner.repo.get_run(runner.run_id)
    assert run is not None
    assert run["started_at"] is not None


def test_dry_run_does_not_notify(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """dry_run 模式不推送"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=True,
    )
    with patch.object(runner.notifier, "send_daily") as mock_notify:
        mock_notify.return_value = MagicMock(success=True)
        runner.run()
        mock_notify.assert_not_called()


def test_single_article_failure_does_not_stop_batch(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """单篇失败不影响整批"""
    import hashlib
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=False,
    )
    runner.repo.init_db()

    # 预先入库两篇文章(discovered 状态)
    articles = [
        ArticleMeta("号A", "标题A", "https://a.com", datetime.now(timezone.utc)),
        ArticleMeta("号B", "标题B", "https://b.com", datetime.now(timezone.utc)),
    ]
    for a in articles:
        url_hash = hashlib.sha256(a.url.encode()).hexdigest()
        runner.repo.upsert_article(
            article_id=url_hash,
            account_name=a.account_name,
            title=a.title,
            url=a.url,
            publish_time=a.publish_time.isoformat(),
            status=Status.DISCOVERED,
        )

    hash_b = hashlib.sha256("https://b.com".encode()).hexdigest()
    call_count = [0]
    def mock_parse(meta):
        call_count[0] += 1
        if call_count[0] == 1:
            raise Exception("解析失败")
        return ParsedArticle(
            article_id=hash_b, title="标题B", plain_text="招聘 岗位 投递",
            images=[], content_hash="chash",
        )

    with patch.object(runner.parser, "parse", side_effect=mock_parse):
        runner._process_articles(articles)
        # 第二篇应该成功处理
        article_b = runner.repo.get_article(hash_b)
        assert article_b is not None
        assert article_b["status"] in ("candidate", "ignored")
