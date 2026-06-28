"""v0.2 Notifier 结构化日报测试"""
from wehire_monitor.modules.notifier.notifier import Notifier
from wehire_monitor.domain.models import Job, Deadline, MatchedJob


def _make_matched_job(company="德邦证券", job="数据分析师", location="上海",
                      email="hr@example.com", confidence=85, score=80) -> MatchedJob:
    return MatchedJob(
        job=Job(
            company_name=company, job_name=job, location=location,
            apply_channel=email, email=email,
            email_chars=["h","r","@","x",".","c","o","m"],
            deadline=Deadline(date="2026-07-31", inferred=False),
            source_evidence={}, confidence=confidence,
        ),
        match_score=score,
        match_reasons=["地点命中:上海"],
    )


def test_structured_markdown_has_table():
    """v0.2 日报应包含 Markdown 表格"""
    notifier = Notifier(feishu_webhook=None, dingtalk_webhook=None, max_per_run=20, email_mask=False)
    items = [_make_matched_job()]
    md = notifier.build_structured_markdown(
        date="2026-06-28",
        matched_jobs=items,
        review_jobs=[],
        total_fetched=10,
        total_candidates=5,
    )
    assert "|" in md  # 表格分隔符
    assert "公司" in md
    assert "岗位" in md
    assert "德邦证券" in md


def test_structured_markdown_review_section():
    """低置信度岗位应进复核区"""
    notifier = Notifier(feishu_webhook=None, dingtalk_webhook=None, max_per_run=20, email_mask=True)
    matched = [_make_matched_job(confidence=85, score=80)]
    review = [_make_matched_job(company="某公司", confidence=50, score=40)]
    md = notifier.build_structured_markdown(
        date="2026-06-28",
        matched_jobs=matched,
        review_jobs=review,
        total_fetched=10,
        total_candidates=5,
    )
    assert "复核" in md or "需人工" in md


def test_email_mask():
    """邮箱脱敏"""
    notifier = Notifier(feishu_webhook=None, dingtalk_webhook=None, max_per_run=20, email_mask=True)
    items = [_make_matched_job(email="hr@example.com")]
    md = notifier.build_structured_markdown(
        date="2026-06-28",
        matched_jobs=items,
        review_jobs=[],
        total_fetched=1,
        total_candidates=1,
    )
    assert "hr@example.com" not in md  # 已脱敏
    assert "hr***" in md or "hr*" in md or "hr" in md  # 脱敏后保留前缀


def test_empty_structured_markdown():
    """无命中时的日报"""
    notifier = Notifier(feishu_webhook=None, dingtalk_webhook=None, max_per_run=20, push_when_empty=True)
    md = notifier.build_structured_markdown(
        date="2026-06-28",
        matched_jobs=[],
        review_jobs=[],
        total_fetched=0,
        total_candidates=0,
    )
    assert "无新增" in md or "无命中" in md
