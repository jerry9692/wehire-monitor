"""v0.3 端到端集成测试: 多模态编排+成本统计+预算耗尽"""
import hashlib
from unittest.mock import patch, MagicMock

from wehire_monitor.pipeline.runner import PipelineRunner
from wehire_monitor.domain.status import Status
from wehire_monitor.domain.models import (
    ParsedArticle, PrefilterResult, Job, Deadline,
    ExtractionResult, MatchedJob,
)


def test_v03_model_count_tracked_in_run_logs(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """模型调用次数和成本记录到 run_logs"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=False,
        stages={"extract"},
    )

    url_hash = hashlib.sha256(b"https://vlm-test.com").hexdigest()
    runner.repo.upsert_article(
        article_id=url_hash, account_name="号V", title="长图招聘",
        url="https://vlm-test.com", publish_time="2026-06-29T10:00:00+08:00",
        status=Status.CANDIDATE,
    )

    parsed = ParsedArticle(
        article_id=url_hash, title="长图招聘",
        plain_text="短文本", images=[], content_hash="ch_vlm",
    )

    extraction = ExtractionResult(
        article_type="social_recruitment",
        jobs=[Job(
            company_name="某公司", job_name="某岗位", location="上海",
            apply_channel="hr@test.com", email="hr@test.com",
            email_chars=["h","r","@","t","e","s","t",".","c","o","m"],
            deadline=Deadline(date="2026-07-31", inferred=False),
            source_evidence={}, confidence=85,
        )],
        warnings=[], model_calls=3,
        cost_estimate=0.06,
    )

    with patch.object(runner.parser, "parse", return_value=parsed), \
         patch.object(runner, "_init_extractor") as mock_init_ext:
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = extraction
        mock_extractor.close = MagicMock()
        mock_init_ext.return_value = mock_extractor
        runner.run()

    # 检查 run_logs 中 model_count 和 cost_estimate
    import sqlite3
    conn = sqlite3.connect(tmp_db_path)
    row = conn.execute(
        "SELECT model_count, cost_estimate FROM run_logs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    assert row[0] == 3  # model_count
    assert row[1] > 0   # cost_estimate
    conn.close()
    runner.close()


def test_v03_budget_exhausted_marks_need_review(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """预算耗尽 → need_review 状态"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=False,
        stages={"extract"},
    )

    url_hash = hashlib.sha256(b"https://budget.com").hexdigest()
    runner.repo.upsert_article(
        article_id=url_hash, account_name="号B", title="预算耗尽测试",
        url="https://budget.com", publish_time="2026-06-29T10:00:00+08:00",
        status=Status.CANDIDATE,
    )

    parsed = ParsedArticle(
        article_id=url_hash, title="预算耗尽测试",
        plain_text="短", images=[], content_hash="ch_budget",
    )

    extraction = ExtractionResult(
        article_type="unknown", jobs=[],
        warnings=["need_review: model budget exhausted"],
        model_calls=0,
    )

    with patch.object(runner.parser, "parse", return_value=parsed), \
         patch.object(runner, "_init_extractor") as mock_init_ext:
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = extraction
        mock_extractor.close = MagicMock()
        mock_init_ext.return_value = mock_extractor
        runner.run()

    article = runner.repo.get_article(url_hash)
    assert article["status"] == Status.NEED_REVIEW.value
    runner.close()


def test_v03_full_pipeline_with_model_and_notify(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """完整管道: 模型提取 → 匹配 → 推送"""
    with patch("wehire_monitor.config.loader.ConfigLoader.get_feishu_webhook", return_value="http://fake"), \
         patch("wehire_monitor.config.loader.ConfigLoader.get_dingtalk_webhook", return_value=None):
        runner = PipelineRunner(
            db_path=tmp_db_path,
            accounts_path=sample_accounts_yaml,
            rules_path=sample_rules_yaml,
            dry_run=False,
            stages={"extract", "match", "notify"},
        )

    url_hash = hashlib.sha256(b"https://v03-e2e.com").hexdigest()
    runner.repo.upsert_article(
        article_id=url_hash, account_name="号V3", title="长图招聘公告",
        url="https://v03-e2e.com", publish_time="2026-06-29T10:00:00+08:00",
        status=Status.CANDIDATE,
    )

    parsed = ParsedArticle(
        article_id=url_hash, title="长图招聘公告",
        plain_text="短文本", images=[], content_hash="ch_v03",
    )

    extraction = ExtractionResult(
        article_type="social_recruitment",
        jobs=[Job(
            company_name="某证券", job_name="风控经理", location="上海",
            apply_channel="hr@test.com", email="hr@test.com",
            email_chars=["h","r","@","t","e","s","t",".","c","o","m"],
            deadline=Deadline(date="2026-07-31", inferred=False),
            source_evidence={}, confidence=90,
        )],
        warnings=[], model_calls=3,
        cost_estimate=0.06,
    )

    matched_result = [MatchedJob(
        job=extraction.jobs[0], match_score=100,
        match_reasons=["公司命中", "岗位命中", "地点命中"],
    )]

    with patch.object(runner.parser, "parse", return_value=parsed), \
         patch.object(runner, "_init_extractor") as mock_init_ext, \
         patch.object(runner, "_init_matcher") as mock_init_match, \
         patch.object(runner.notifier, "_send_feishu") as mock_feishu:
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = extraction
        mock_extractor.close = MagicMock()
        mock_init_ext.return_value = mock_extractor
        mock_matcher = MagicMock()
        mock_matcher.match.return_value = matched_result
        mock_init_match.return_value = mock_matcher
        mock_feishu.return_value = MagicMock(success=True, message="ok")

        runner.run()

    article = runner.repo.get_article(url_hash)
    assert article["status"] in (Status.MATCHED.value, Status.ARCHIVED.value)

    # 检查模型统计
    import sqlite3
    conn = sqlite3.connect(tmp_db_path)
    row = conn.execute(
        "SELECT model_count, cost_estimate FROM run_logs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    assert row[0] == 3  # model_count
    assert row[1] > 0   # cost_estimate
    conn.close()
    runner.close()
