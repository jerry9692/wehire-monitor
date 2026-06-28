"""v0.2 Pipeline extract+match 阶段测试"""
import hashlib
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from wehire_monitor.pipeline.runner import PipelineRunner
from wehire_monitor.domain.status import Status
from wehire_monitor.domain.models import (
    ArticleMeta, ParsedArticle, PrefilterResult, Job, Deadline, ExtractionResult,
    MatchedJob,
)


def test_extract_stage_processes_candidate_articles(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """extract 阶段处理 CANDIDATE 状态文章"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=False,
        stages={"extract", "match"},
    )

    # 预入库一篇 CANDIDATE 文章
    url_hash = hashlib.sha256(b"https://a.com").hexdigest()
    runner.repo.upsert_article(
        article_id=url_hash, account_name="号A", title="招聘公告",
        url="https://a.com", publish_time="2026-06-28T10:00:00+08:00",
        status=Status.CANDIDATE,
    )

    # mock parser 返回 ParsedArticle
    parsed = ParsedArticle(
        article_id=url_hash, title="招聘公告",
        plain_text="德邦证券招聘数据分析师" * 100,
        images=[], content_hash="ch",
    )

    # mock extractor 返回 ExtractionResult
    extraction = ExtractionResult(
        article_type="social_recruitment",
        jobs=[Job(
            company_name="德邦证券", job_name="数据分析师", location="上海",
            apply_channel="hr@example.com", email="hr@example.com",
            email_chars=["h","r","@","x",".","c","o","m"],
            deadline=Deadline(date="2026-07-31", inferred=False),
            source_evidence={}, confidence=85,
        )],
        warnings=[], llm_calls=1,
    )

    with patch.object(runner.parser, "parse", return_value=parsed), \
         patch.object(runner, "_init_extractor") as mock_init_ext, \
         patch.object(runner, "_init_matcher") as mock_init_match:
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = extraction
        mock_init_ext.return_value = mock_extractor
        mock_matcher = MagicMock()
        mock_matcher.match.return_value = []
        mock_init_match.return_value = mock_matcher

        runner.run()

    # 文章状态应推进到 MATCHED 或 ARCHIVED 或 VALIDATED
    article = runner.repo.get_article(url_hash)
    assert article["status"] in (Status.MATCHED.value, Status.ARCHIVED.value, Status.VALIDATED.value)
    runner.close()


def test_extract_stage_matched_article_transitions_to_matched(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """提取+匹配达标 → MATCHED 状态"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=False,
        stages={"extract", "match"},
    )

    url_hash = hashlib.sha256(b"https://b.com").hexdigest()
    runner.repo.upsert_article(
        article_id=url_hash, account_name="号B", title="银行招聘",
        url="https://b.com", publish_time="2026-06-28T10:00:00+08:00",
        status=Status.CANDIDATE,
    )

    parsed = ParsedArticle(
        article_id=url_hash, title="银行招聘",
        plain_text="某银行招聘金融分析师" * 100,
        images=[], content_hash="ch2",
    )

    job = Job(
        company_name="某银行", job_name="金融分析师", location="上海",
        apply_channel=None, email=None, email_chars=[],
        deadline=Deadline(date="2026-08-31", inferred=False),
        source_evidence={}, confidence=90,
    )
    extraction = ExtractionResult(
        article_type="social_recruitment", jobs=[job], warnings=[], llm_calls=1,
    )

    # 匹配分 >= notify_min_score(70)
    matched_result = [MatchedJob(job=job, match_score=80, match_reasons=["公司命中"])]

    with patch.object(runner.parser, "parse", return_value=parsed), \
         patch.object(runner, "_init_extractor") as mock_init_ext, \
         patch.object(runner, "_init_matcher") as mock_init_match:
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = extraction
        mock_init_ext.return_value = mock_extractor
        mock_matcher = MagicMock()
        mock_matcher.match.return_value = matched_result
        mock_init_match.return_value = mock_matcher

        runner.run()

    article = runner.repo.get_article(url_hash)
    assert article["status"] == Status.MATCHED.value
    runner.close()


def test_extract_stage_no_jobs_archived(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """提取无岗位 → ARCHIVED 状态"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=False,
        stages={"extract", "match"},
    )

    url_hash = hashlib.sha256(b"https://c.com").hexdigest()
    runner.repo.upsert_article(
        article_id=url_hash, account_name="号C", title="非招聘内容",
        url="https://c.com", publish_time="2026-06-28T10:00:00+08:00",
        status=Status.CANDIDATE,
    )

    parsed = ParsedArticle(
        article_id=url_hash, title="非招聘内容",
        plain_text="这是一篇普通文章" * 100,
        images=[], content_hash="ch3",
    )

    extraction = ExtractionResult(
        article_type="non_recruitment", jobs=[], warnings=[], llm_calls=1,
    )

    with patch.object(runner.parser, "parse", return_value=parsed), \
         patch.object(runner, "_init_extractor") as mock_init_ext, \
         patch.object(runner, "_init_matcher") as mock_init_match:
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = extraction
        mock_init_ext.return_value = mock_extractor
        mock_matcher = MagicMock()
        mock_matcher.match.return_value = []
        mock_init_match.return_value = mock_matcher

        runner.run()

    article = runner.repo.get_article(url_hash)
    assert article["status"] == Status.ARCHIVED.value
    runner.close()


def test_extract_stage_error_marks_error_llm(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """提取异常 → ERROR_LLM 状态"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=False,
        stages={"extract", "match"},
    )

    url_hash = hashlib.sha256(b"https://d.com").hexdigest()
    runner.repo.upsert_article(
        article_id=url_hash, account_name="号D", title="出错文章",
        url="https://d.com", publish_time="2026-06-28T10:00:00+08:00",
        status=Status.CANDIDATE,
    )

    parsed = ParsedArticle(
        article_id=url_hash, title="出错文章",
        plain_text="内容" * 100,
        images=[], content_hash="ch4",
    )

    with patch.object(runner.parser, "parse", return_value=parsed), \
         patch.object(runner, "_init_extractor") as mock_init_ext, \
         patch.object(runner, "_init_matcher") as mock_init_match:
        mock_extractor = MagicMock()
        mock_extractor.extract.side_effect = RuntimeError("LLM 超时")
        mock_init_ext.return_value = mock_extractor
        mock_matcher = MagicMock()
        mock_init_match.return_value = mock_matcher

        runner.run()

    article = runner.repo.get_article(url_hash)
    assert article["status"] == Status.ERROR_LLM.value
    runner.close()


def test_extract_dry_run_skips(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """dry-run 模式跳过提取+匹配"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=True,
        stages={"extract", "match"},
    )

    url_hash = hashlib.sha256(b"https://e.com").hexdigest()
    runner.repo.upsert_article(
        article_id=url_hash, account_name="号E", title="dry-run 文章",
        url="https://e.com", publish_time="2026-06-28T10:00:00+08:00",
        status=Status.CANDIDATE,
    )

    # dry-run 不应调用 extractor
    with patch.object(runner, "_init_extractor") as mock_init_ext:
        runner.run()
        mock_init_ext.assert_not_called()

    # 状态不变
    article = runner.repo.get_article(url_hash)
    assert article["status"] == Status.CANDIDATE.value
    runner.close()
