"""v0.2 端到端集成测试(mock LLM/OCR)"""
import hashlib
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from wehire_monitor.pipeline.runner import PipelineRunner
from wehire_monitor.domain.status import Status
from wehire_monitor.domain.models import (
    ArticleMeta, ParsedArticle, ExtractionResult, Job, Deadline,
)


def test_full_pipeline_extract_match_with_mock(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """完整管道: CANDIDATE → extract → match → MATCHED"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=False,
        stages={"extract", "match"},
    )

    # 预入库 CANDIDATE 文章
    url_hash = hashlib.sha256(b"https://a.com").hexdigest()
    runner.repo.upsert_article(
        article_id=url_hash, account_name="号A", title="德邦证券2026招聘",
        url="https://a.com", publish_time="2026-06-28T10:00:00+08:00",
        status=Status.CANDIDATE, prefilter_score=75,
    )

    parsed = ParsedArticle(
        article_id=url_hash, title="德邦证券2026招聘",
        plain_text="德邦证券招聘数据分析师" * 100,
        images=[], content_hash="ch",
    )

    extraction = ExtractionResult(
        article_type="social_recruitment",
        jobs=[Job(
            company_name="德邦证券", job_name="数据分析师", location="上海",
            apply_channel="hr@example.com", email="hr@example.com",
            email_chars=["h","r","@","x",".","c","o","m"],
            deadline=Deadline(date="2026-07-31", inferred=False),
            source_evidence={}, confidence=85,
        )],
        warnings=[], model_calls=1,
    )

    with patch.object(runner.parser, "parse", return_value=parsed), \
         patch.object(runner, "_init_extractor") as mock_ext, \
         patch.object(runner, "_init_matcher") as mock_match:
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = extraction
        mock_ext.return_value = mock_extractor
        from wehire_monitor.modules.matcher.matcher import Matcher
        real_matcher = Matcher(runner.rules.match_rules)
        mock_match.return_value = real_matcher

        runner.run()

    # 验证 jobs 已写入
    jobs = runner.repo.query_jobs_by_article(url_hash)
    assert len(jobs) == 1
    assert jobs[0]["company_name"] == "德邦证券"
    assert jobs[0]["match_score"] > 0

    # 验证文章状态推进
    article = runner.repo.get_article(url_hash)
    assert article["status"] in (Status.MATCHED.value, Status.ARCHIVED.value)
    assert article["article_type"] == "social_recruitment"
    runner.close()


def test_extract_error_does_not_crash(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """提取失败不崩溃,标记 ERROR_LLM"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=False,
        stages={"extract", "match"},
    )

    url_hash = hashlib.sha256(b"https://b.com").hexdigest()
    runner.repo.upsert_article(
        article_id=url_hash, account_name="号A", title="测试",
        url="https://b.com", publish_time="2026-06-28T10:00:00+08:00",
        status=Status.CANDIDATE,
    )

    with patch.object(runner.parser, "parse", side_effect=Exception("解析失败")), \
         patch.object(runner, "_init_extractor") as mock_ext, \
         patch.object(runner, "_init_matcher") as mock_match:
        mock_extractor = MagicMock()
        mock_ext.return_value = mock_extractor
        mock_match.return_value = MagicMock()

        runner.run()  # 不应崩溃

    article = runner.repo.get_article(url_hash)
    assert article["status"] == Status.ERROR_LLM.value
    runner.close()
