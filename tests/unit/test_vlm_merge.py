"""VLM 切片合并测试"""
from wehire_monitor.domain.models import Deadline, Job
from wehire_monitor.modules.extractor.vlm_merge import (
    _job_completeness,
    _job_key,
    _normalize,
    merge_slice_jobs,
)


def _make_job(
    company="某公司",
    job_name="数据分析师",
    location="上海",
    apply_channel=None,
    email=None,
    email_chars=None,
    deadline_date=None,
    confidence=80,
    evidence=None,
):
    return Job(
        company_name=company,
        job_name=job_name,
        location=location,
        apply_channel=apply_channel,
        email=email,
        email_chars=email_chars or [],
        deadline=Deadline(date=deadline_date, inferred=False),
        source_evidence=evidence if evidence is not None else {},
        confidence=confidence,
    )


def test_normalize():
    """辅助函数: 去空白转小写"""
    assert _normalize("  HR @ X.com ") == "hr@x.com"
    assert _normalize(None) == ""
    assert _normalize("") == ""


def test_job_key():
    """辅助函数: 唯一键由 company/job_name/location 组成"""
    job = _make_job(company="某公司", job_name="数据 分析师", location="上海")
    assert _job_key(job) == "某公司|数据分析师|上海"


def test_job_completeness():
    """辅助函数: 完整度评分"""
    empty_job = _make_job(company=None, job_name=None, location=None)
    assert _job_completeness(empty_job) == 0
    full_job = _make_job(
        company="某公司",
        job_name="数据分析师",
        location="上海",
        apply_channel="官网",
        email="hr@example.com",
        deadline_date="2026-07-31",
    )
    assert _job_completeness(full_job) == 6


def test_merge_no_duplicates():
    """不同岗位不合并"""
    job_a = _make_job(company="公司A", job_name="岗位甲")
    job_b = _make_job(company="公司B", job_name="岗位乙")
    result = merge_slice_jobs([[job_a], [job_b]])
    assert len(result) == 2
    assert result[0].company_name == "公司A"
    assert result[1].company_name == "公司B"


def test_merge_duplicate_same_key():
    """相同岗位保留字段更完整者,并补充缺失字段"""
    # job_a: 字段较少,但有 deadline
    job_a = _make_job(
        company="某公司",
        job_name="数据分析师",
        location="上海",
        deadline_date="2026-07-31",
        confidence=70,
    )
    # job_b: 字段更完整(apply_channel + email),但无 deadline
    job_b = _make_job(
        company="某公司",
        job_name="数据分析师",
        location="上海",
        apply_channel="官网",
        email="hr@example.com",
        email_chars=["h", "r", "@", "example", ".", "c", "o", "m"],
        confidence=85,
    )
    result = merge_slice_jobs([[job_a], [job_b]])
    assert len(result) == 1
    merged = result[0]
    # 基准为更完整者 job_b
    assert merged.apply_channel == "官网"
    assert merged.email == "hr@example.com"
    # 缺失字段从 job_a 补充
    assert merged.deadline.date == "2026-07-31"
    # 置信度取较高者
    assert merged.confidence == 85


def test_merge_email_conflict():
    """邮箱冲突标记,保留正则合法且置信度高者"""
    # job_a: 非法邮箱,低置信度
    job_a = _make_job(
        company="某公司",
        job_name="数据分析师",
        location="上海",
        email="bad-email",
        email_chars=["b", "a", "d"],
        confidence=70,
    )
    # job_b: 合法邮箱,高置信度
    job_b = _make_job(
        company="某公司",
        job_name="数据分析师",
        location="上海",
        email="hr@example.com",
        email_chars=["h", "r", "@", "example", ".", "c", "o", "m"],
        confidence=85,
    )
    result = merge_slice_jobs([[job_a], [job_b]])
    assert len(result) == 1
    merged = result[0]
    assert "email_conflict" in merged.source_evidence.get("_warnings", [])
    # 保留合法邮箱
    assert merged.email == "hr@example.com"
    # 置信度取较高者
    assert merged.confidence == 85


def test_merge_empty_lists():
    """空列表"""
    assert merge_slice_jobs([]) == []
    assert merge_slice_jobs([[]]) == []
    assert merge_slice_jobs([[], []]) == []


def test_merge_preserves_order():
    """保持原始顺序(首次出现顺序)"""
    job_a = _make_job(company="公司A", job_name="岗位")
    job_b = _make_job(company="公司B", job_name="岗位")
    job_c = _make_job(company="公司C", job_name="岗位")
    # 切片1: A, B ; 切片2: C, A(重复)
    result = merge_slice_jobs([[job_a, job_b], [job_c, job_a]])
    assert len(result) == 3
    assert [j.company_name for j in result] == ["公司A", "公司B", "公司C"]
