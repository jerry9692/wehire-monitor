"""Matcher 测试"""
from wehire_monitor.modules.matcher.matcher import Matcher
from wehire_monitor.config.schemas import (
    MatchRules, LocationRules, JobKeywordRules, CompanyRules,
)
from wehire_monitor.domain.models import Job, Deadline, MatchedJob


def _make_rules() -> MatchRules:
    return MatchRules(
        locations=LocationRules(include=["上海", "杭州"], exclude=["境外"]),
        job_keywords=JobKeywordRules(include=["金融", "数据分析", "风控"], exclude=["实习"]),
        companies=CompanyRules(include=["银行", "证券", "基金"]),
        notify_min_score=70,
    )


def _make_job(company="德邦证券", job_name="数据分析师", location="上海") -> Job:
    return Job(
        company_name=company, job_name=job_name, location=location,
        apply_channel=None, email=None, email_chars=[],
        deadline=Deadline(date=None), source_evidence={}, confidence=85,
    )


def test_match_high_score():
    """公司+岗位+地点全命中 → 高分"""
    matcher = Matcher(_make_rules())
    results = matcher.match([_make_job()])
    assert len(results) == 1
    assert results[0].match_score >= 70
    assert "证券" in " ".join(results[0].match_reasons) or any("公司" in r for r in results[0].match_reasons)


def test_match_exclude_location():
    """地点排除词命中 → 降分"""
    matcher = Matcher(_make_rules())
    job = _make_job(location="境外")
    results = matcher.match([job])
    assert results[0].match_score < 70  # 排除词惩罚


def test_match_exclude_keyword():
    """岗位排除词命中 → 降分"""
    matcher = Matcher(_make_rules())
    job = _make_job(job_name="实习生招聘")
    results = matcher.match([job])
    assert results[0].match_score < 70


def test_match_no_hits():
    """无命中 → 低分"""
    matcher = Matcher(_make_rules())
    job = _make_job(company="某公司", job_name="某岗位", location="某地")
    results = matcher.match([job])
    assert results[0].match_score < 50


def test_match_empty_jobs():
    matcher = Matcher(_make_rules())
    results = matcher.match([])
    assert results == []


def test_match_multiple_jobs():
    matcher = Matcher(_make_rules())
    jobs = [
        _make_job(company="中金公司", job_name="风控经理", location="上海"),
        _make_job(company="某餐饮", job_name="服务员", location="某地"),
    ]
    results = matcher.match(jobs)
    assert len(results) == 2
    assert results[0].match_score > results[1].match_score
