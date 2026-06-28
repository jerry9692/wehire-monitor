"""后处理校验测试"""
from wehire_monitor.modules.extractor.postprocess import (
    validate_email,
    validate_email_chars_consistency,
    validate_deadline,
    check_confidence,
    postprocess_jobs,
    needs_review,
)
from wehire_monitor.domain.models import Job, Deadline


def test_valid_email():
    assert validate_email("hr@example.com") is True


def test_invalid_email():
    assert validate_email("not-an-email") is False
    assert validate_email(None) is False


def test_email_chars_consistent():
    job = Job(
        company_name=None, job_name=None, location=None,
        apply_channel=None, email="hr@x.com",
        email_chars=["h", "r", "@", "x", ".", "c", "o", "m"],
        deadline=Deadline(date=None), source_evidence={}, confidence=80,
    )
    assert validate_email_chars_consistency(job) is True


def test_email_chars_mismatch():
    job = Job(
        company_name=None, job_name=None, location=None,
        apply_channel=None, email="hr@x.com",
        email_chars=["h", "r", "@", "y", ".", "c", "o", "m"],  # y ≠ x
        deadline=Deadline(date=None), source_evidence={}, confidence=80,
    )
    assert validate_email_chars_consistency(job) is False


def test_deadline_before_publish():
    """截止日期早于发布时间"""
    assert validate_deadline("2026-06-01", "2026-06-28") is False


def test_deadline_after_publish():
    assert validate_deadline("2026-07-31", "2026-06-28") is True


def test_deadline_none():
    assert validate_deadline(None, "2026-06-28") is True


def test_low_confidence_flagged():
    assert check_confidence(50) is False  # <60 需复核
    assert check_confidence(60) is True
    assert check_confidence(85) is True


def test_postprocess_adds_warnings():
    """后处理应添加 warnings"""
    jobs = [
        Job(
            company_name="某公司", job_name="某岗位", location=None,
            apply_channel=None, email="bad-email",
            email_chars=[],
            deadline=Deadline(date="2026-06-01", inferred=False),  # 早于发布
            source_evidence={}, confidence=50,  # 低置信度
        )
    ]
    result = postprocess_jobs(jobs, publish_time="2026-06-28")
    assert len(result) == 1
    assert "email_invalid" in result[0].source_evidence.get("warnings", []) or \
        any("email" in w.lower() for w in result[0].source_evidence.get("_warnings", []))


def test_postprocess_clean_job_no_warnings():
    """合规岗位不应产生警告"""
    jobs = [
        Job(
            company_name="某公司", job_name="某岗位", location="上海",
            apply_channel="hr@example.com", email="hr@example.com",
            email_chars=["h", "r", "@", "e", "x", "a", "m", "p", "l", "e", ".", "c", "o", "m"],
            deadline=Deadline(date="2026-07-31", inferred=False),
            source_evidence={}, confidence=85,
        )
    ]
    result = postprocess_jobs(jobs, publish_time="2026-06-28")
    assert result[0].source_evidence.get("_warnings", []) == []


def test_needs_review_true():
    """低置信度/邮箱不一致/邮箱非法 → 需复核"""
    job = Job(
        company_name="某公司", job_name=None, location=None,
        apply_channel=None, email="bad-email", email_chars=[],
        deadline=Deadline(date=None), source_evidence={"_warnings": ["email_invalid"]},
        confidence=50,
    )
    assert needs_review(job) is True

    job2 = Job(
        company_name=None, job_name=None, location=None,
        apply_channel=None, email="hr@x.com",
        email_chars=["h", "r", "@", "y", ".", "c", "o", "m"],
        deadline=Deadline(date=None), source_evidence={"_warnings": ["email_mismatch"]},
        confidence=80,
    )
    assert needs_review(job2) is True

    job3 = Job(
        company_name=None, job_name=None, location=None,
        apply_channel=None, email=None, email_chars=[],
        deadline=Deadline(date=None), source_evidence={"_warnings": ["low_confidence"]},
        confidence=40,
    )
    assert needs_review(job3) is True


def test_needs_review_false():
    """无相关警告 → 不需复核"""
    job = Job(
        company_name="某公司", job_name="某岗位", location="上海",
        apply_channel="hr@example.com", email="hr@example.com",
        email_chars=["h", "r", "@", "e", "x", "a", "m", "p", "l", "e", ".", "c", "o", "m"],
        deadline=Deadline(date="2026-07-31", inferred=False),
        source_evidence={"_warnings": []}, confidence=85,
    )
    assert needs_review(job) is False


def test_needs_review_deadline_before_publish():
    """截止日期早于发布时间 → 需复核(H2 修复)"""
    job = Job(
        company_name="某公司", job_name="某岗位", location="上海",
        apply_channel="hr@example.com", email="hr@example.com",
        email_chars=["h", "r", "@", "e", "x", "a", "m", "p", "l", "e", ".", "c", "o", "m"],
        deadline=Deadline(date="2026-06-01", inferred=False),
        source_evidence={"_warnings": ["deadline_before_publish"]}, confidence=85,
    )
    assert needs_review(job) is True


def test_location_extraction_from_article_text():
    """地点空但正文含城市词 → 二次抽取(M2 修复)"""
    jobs = [
        Job(
            company_name="某公司", job_name="某岗位", location=None,
            apply_channel=None, email=None, email_chars=[],
            deadline=Deadline(date="2026-07-31", inferred=False),
            source_evidence={}, confidence=85,
        )
    ]
    article_text = "某公司在北京招聘数据分析师,工作地点位于朝阳区。"
    result = postprocess_jobs(jobs, publish_time="2026-06-28", article_text=article_text)
    assert result[0].location == "北京"


def test_location_extraction_from_company_name():
    """地点空但公司名含城市词 → 二次抽取"""
    jobs = [
        Job(
            company_name="上海某证券公司", job_name="某岗位", location=None,
            apply_channel=None, email=None, email_chars=[],
            deadline=Deadline(date="2026-07-31", inferred=False),
            source_evidence={}, confidence=85,
        )
    ]
    result = postprocess_jobs(jobs, publish_time="2026-06-28", article_text="")
    assert result[0].location == "上海"


def test_location_not_overwritten_when_present():
    """地点已有值 → 不被二次抽取覆盖"""
    jobs = [
        Job(
            company_name="某公司", job_name="某岗位", location="杭州",
            apply_channel=None, email=None, email_chars=[],
            deadline=Deadline(date="2026-07-31", inferred=False),
            source_evidence={}, confidence=85,
        )
    ]
    article_text = "某公司在北京招聘"
    result = postprocess_jobs(jobs, publish_time="2026-06-28", article_text=article_text)
    assert result[0].location == "杭州"
