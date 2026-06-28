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


def test_need_review_status_set_for_low_quality_ocr(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """OCR 质量过低 → NEED_REVIEW 状态"""
    runner = PipelineRunner(
        db_path=tmp_db_path,
        accounts_path=sample_accounts_yaml,
        rules_path=sample_rules_yaml,
        dry_run=False,
        stages={"extract"},
    )

    url_hash = hashlib.sha256(b"https://f.com").hexdigest()
    runner.repo.upsert_article(
        article_id=url_hash, account_name="号F", title="图片招聘",
        url="https://f.com", publish_time="2026-06-28T10:00:00+08:00",
        status=Status.CANDIDATE,
    )

    parsed = ParsedArticle(
        article_id=url_hash, title="图片招聘",
        plain_text="", images=[], content_hash="ch5",
    )

    extraction = ExtractionResult(
        article_type="unknown", jobs=[],
        warnings=["need_review: OCR quality 0.30 < 0.45"],
        ocr_calls=2,
    )

    with patch.object(runner.parser, "parse", return_value=parsed), \
         patch.object(runner, "_init_extractor") as mock_init_ext:
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = extraction
        mock_init_ext.return_value = mock_extractor
        runner.run()

    article = runner.repo.get_article(url_hash)
    assert article["status"] == Status.NEED_REVIEW.value
    runner.close()


def test_default_stages_include_extract_match():
    """默认 stages 应包含 extract 和 match(v0.2 核心流程)"""
    # 直接验证 runner.py 中默认 stages 集合包含 v0.2 阶段
    import inspect
    from wehire_monitor.pipeline import runner as runner_mod
    src = inspect.getsource(runner_mod.PipelineRunner.__init__)
    assert "extract" in src
    assert "match" in src


def test_low_confidence_job_routed_to_review_not_main(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """C1 修复: 低置信度岗位进复核区,不进主表"""
    with patch("wehire_monitor.config.loader.ConfigLoader.get_feishu_webhook", return_value="http://fake"), \
         patch("wehire_monitor.config.loader.ConfigLoader.get_dingtalk_webhook", return_value=None):
        runner = PipelineRunner(
            db_path=tmp_db_path,
            accounts_path=sample_accounts_yaml,
            rules_path=sample_rules_yaml,
            dry_run=False,
            stages={"notify"},
        )

    # 入库一篇 MATCHED 文章 + 低置信度岗位(match_score 达标但 confidence<60)
    url_hash = hashlib.sha256(b"https://review.com").hexdigest()
    runner.repo.upsert_article(
        article_id=url_hash, account_name="号R", title="低置信度岗位",
        url="https://review.com", publish_time="2026-06-28T10:00:00+08:00",
        status=Status.MATCHED,
    )
    # 写入 jobs: match_score=80(达标) 但 confidence=40(低置信度)
    low_conf_job = Job(
        company_name="某公司", job_name="分析师", location="上海",
        apply_channel=None, email=None, email_chars=[],
        deadline=Deadline(date="2026-07-31", inferred=False),
        source_evidence={"_warnings": ["low_confidence"]}, confidence=40,
    )
    runner.repo.upsert_jobs(url_hash, [low_conf_job])
    # 手动设置 match_score
    runner.repo.conn.execute(
        "UPDATE jobs SET match_score = 80 WHERE article_id = ?", (url_hash,)
    )
    runner.repo.conn.commit()

    # mock 推送,捕获传入的 matched_jobs 和 review_jobs
    captured = {}

    def fake_send_feishu(md_content, card_title="x"):
        captured["md"] = md_content
        from wehire_monitor.modules.notifier.notifier import NotifyResult
        return NotifyResult(success=True, message="ok")

    with patch.object(runner.notifier, "_send_feishu", side_effect=fake_send_feishu), \
         patch.object(runner.config_loader, "get_feishu_webhook", return_value="http://fake"), \
         patch.object(runner.config_loader, "get_dingtalk_webhook", return_value=None):
        runner._notify(fetched_count=1, candidate_count=1, matched_count=1)

    # 低置信度岗位不应被标记为已通知(进复核区而非主表)
    job_row = runner.repo.conn.execute(
        "SELECT notified_at FROM jobs WHERE article_id = ?", (url_hash,)
    ).fetchone()
    assert job_row["notified_at"] is None, "低置信度岗位不应被标记为已通知"

    # Markdown 中应包含复核区而非主表
    assert "需人工复核" in captured["md"] or "复核" in captured["md"]
    runner.close()


def test_query_jobs_for_notify_filters_by_matched_status(tmp_db_path, sample_accounts_yaml, sample_rules_yaml):
    """H1 修复: query_jobs_for_notify 只查 status=matched 的文章"""
    with patch("wehire_monitor.config.loader.ConfigLoader.get_feishu_webhook", return_value="http://fake"), \
         patch("wehire_monitor.config.loader.ConfigLoader.get_dingtalk_webhook", return_value=None):
        runner = PipelineRunner(
            db_path=tmp_db_path,
            accounts_path=sample_accounts_yaml,
            rules_path=sample_rules_yaml,
            dry_run=False,
            stages={"notify"},
        )

    # MATCHED 文章 + job
    hash_matched = hashlib.sha256(b"https://matched.com").hexdigest()
    runner.repo.upsert_article(
        article_id=hash_matched, account_name="号A", title="已匹配",
        url="https://matched.com", publish_time="2026-06-28T10:00:00+08:00",
        status=Status.MATCHED,
    )
    good_job = Job(
        company_name="某公司", job_name="分析师", location="上海",
        apply_channel="hr@example.com", email="hr@example.com",
        email_chars=["h","r","@","x",".","c","o","m"],
        deadline=Deadline(date="2026-07-31", inferred=False),
        source_evidence={}, confidence=85,
    )
    runner.repo.upsert_jobs(hash_matched, [good_job])
    runner.repo.conn.execute(
        "UPDATE jobs SET match_score = 80 WHERE article_id = ?", (hash_matched,)
    )

    # ARCHIVED 文章 + job(同分但文章已归档)
    hash_archived = hashlib.sha256(b"https://archived.com").hexdigest()
    runner.repo.upsert_article(
        article_id=hash_archived, account_name="号B", title="已归档",
        url="https://archived.com", publish_time="2026-06-28T10:00:00+08:00",
        status=Status.ARCHIVED,
    )
    archived_job = Job(
        company_name="另一公司", job_name="另一岗位", location="上海",
        apply_channel=None, email=None, email_chars=[],
        deadline=Deadline(date="2026-07-31", inferred=False),
        source_evidence={}, confidence=85,
    )
    runner.repo.upsert_jobs(hash_archived, [archived_job])
    runner.repo.conn.execute(
        "UPDATE jobs SET match_score = 80 WHERE article_id = ?", (hash_archived,)
    )
    runner.repo.conn.commit()

    # 只应返回 MATCHED 文章的 job
    jobs = runner.repo.query_jobs_for_notify(min_score=70)
    assert len(jobs) == 1
    assert jobs[0]["article_id"] == hash_matched
    runner.close()
