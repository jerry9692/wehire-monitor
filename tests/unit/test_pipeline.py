"""Pipeline Runner 测试"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from wehire_monitor.pipeline.runner import PipelineRunner
from wehire_monitor.domain.status import Status
from wehire_monitor.domain.models import ArticleMeta, ParsedArticle
from wehire_monitor.config.schemas import AccountConfig, KeywordsConfig


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
    runner.close()


def test_dry_run_does_not_notify(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """dry_run 模式不推送"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=True,
    )
    with patch.object(runner.notifier, "send_daily") as mock_notify:
        mock_notify.return_value = MagicMock(success=True, pushed_count=0)
        runner.run()
        mock_notify.assert_not_called()
    runner.close()


def test_single_article_failure_does_not_stop_batch(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """单篇失败不影响整批,失败文章标记为错误状态"""
    import hashlib
    # stages 不含 fetch,避免触发 Cookie 配置校验(测试直接调用 _process_articles)
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=False,
        stages={"parse", "prefilter", "notify"},
    )
    # 设置有命中词的 keywords
    runner.prefilter.hit_words = ["招聘", "岗位", "投递", "报名", "简历"]
    runner.prefilter.exclude_words = ["实习", "校招"]
    runner.prefilter.title_veto_words = ["校招", "校园招聘", "实习生", "实习招聘", "宣讲会", "培训班"]

    # 预先入库两篇文章(discovered 状态)
    articles = [
        ArticleMeta("号A", "标题A", "https://a.com", datetime.now(timezone.utc)),
        ArticleMeta("号B", "招聘公告", "https://b.com", datetime.now(timezone.utc)),
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
            article_id=hash_b, title="招聘公告", plain_text="招聘 岗位 投递 简历",
            images=[], content_hash="chash_b",
        )

    with patch.object(runner.parser, "parse", side_effect=mock_parse):
        # _process_articles 会跳过 FETCHED 状态迁移(因为文章初始是 DISCOVERED)
        # 但我们需要先 transition 到 FETCHED 才能正确测试流程
        for a in articles:
            url_hash = hashlib.sha256(a.url.encode()).hexdigest()
            runner.repo.force_status(url_hash, Status.FETCHED)
        runner._process_articles(articles)

        # 第二篇应该成功处理为 candidate
        article_b = runner.repo.get_article(hash_b)
        assert article_b is not None
        assert article_b["status"] == "candidate"

        # 第一篇(失败)应该标记为 error_parse
        hash_a = hashlib.sha256("https://a.com".encode()).hexdigest()
        article_a = runner.repo.get_article(hash_a)
        assert article_a is not None
        assert article_a["status"] == "error_parse"
    runner.close()


def test_dry_run_does_not_write_db(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """dry_run 模式不写文章数据到 DB"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=True,
    )
    runner.run()
    all_articles = runner.repo.query_by_status(Status.DISCOVERED)
    assert len(all_articles) == 0
    runner.close()
