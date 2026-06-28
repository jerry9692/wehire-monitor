"""v0.2 领域模型测试"""
from wehire_monitor.domain.models import (
    Job, ExtractionResult, Deadline, MatchedJob, OCRResult, OCRLine
)


def test_deadline_defaults():
    d = Deadline(date=None, inferred=False)
    assert d.date is None
    assert d.inferred is False


def test_job_minimal():
    job = Job(
        company_name="德邦证券",
        job_name="数据分析师",
        location="上海",
        apply_channel="hr@example.com",
        email="hr@example.com",
        email_chars=["h","r","@","e","x","a","m","p","l","e",".","c","o","m"],
        deadline=Deadline(date="2026-07-31", inferred=False),
        source_evidence={"company_name": "德邦证券"},
        confidence=85,
    )
    assert job.company_name == "德邦证券"
    assert job.confidence == 85


def test_extraction_result_with_warnings():
    result = ExtractionResult(
        article_type="social_recruitment",
        jobs=[],
        warnings=["邮箱格式异常"],
        llm_calls=1,
        vlm_calls=0,
        ocr_calls=0,
    )
    assert result.article_type == "social_recruitment"
    assert len(result.warnings) == 1


def test_matched_job():
    job = Job(
        company_name="中金公司", job_name="风控经理", location="北京",
        apply_channel=None, email=None, email_chars=[],
        deadline=Deadline(date=None, inferred=False),
        source_evidence={}, confidence=90,
    )
    matched = MatchedJob(job=job, match_score=85, match_reasons=["地点命中:北京"])
    assert matched.match_score == 85


def test_ocr_result():
    line = OCRLine(text="招聘岗位", confidence=0.95, box=[0, 0, 100, 30])
    result = OCRResult(lines=[line], full_text="招聘岗位")
    assert result.lines[0].confidence == 0.95
    assert result.full_text == "招聘岗位"
