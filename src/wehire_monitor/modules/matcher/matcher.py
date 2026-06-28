"""Matcher — 用户规则匹配(SRS §4.5)

匹配维度: locations / job_keywords / companies 的 include/exclude
加权得分,阈值 notify_min_score(默认 70)
"""
from __future__ import annotations

from wehire_monitor.config.schemas import MatchRules
from wehire_monitor.domain.models import Job, MatchedJob

# 权重
_W_COMPANY = 30
_W_JOB_KEYWORD = 30
_W_LOCATION = 20
_W_BASE = 20  # 基础分(有结构化信息)
_PENALTY = 25  # 排除词惩罚


class Matcher:
    """用户规则匹配器"""

    def __init__(self, rules: MatchRules):
        self.rules = rules

    def match(self, jobs: list[Job]) -> list[MatchedJob]:
        """对岗位列表计算匹配分,返回所有岗位(含分数)"""
        results: list[MatchedJob] = []
        for job in jobs:
            score, reasons = self._score_job(job)
            results.append(MatchedJob(
                job=job,
                match_score=score,
                match_reasons=reasons,
            ))
        return results

    def _score_job(self, job: Job) -> tuple[int, list[str]]:
        """计算单个岗位的匹配分"""
        score = 0
        reasons: list[str] = []

        # 基础分:有结构化信息
        if job.company_name or job.job_name:
            score += _W_BASE

        # 公司匹配
        company = job.company_name or ""
        company_hit = any(kw in company for kw in self.rules.companies.include)
        if company_hit:
            score += _W_COMPANY
            reasons.append(f"公司命中: {company}")

        # 岗位关键词匹配
        job_name = job.job_name or ""
        kw_hit = any(kw in job_name for kw in self.rules.job_keywords.include)
        if kw_hit:
            score += _W_JOB_KEYWORD
            reasons.append(f"岗位命中: {job_name}")

        # 地点匹配
        location = job.location or ""
        loc_hit = any(kw in location for kw in self.rules.locations.include)
        if loc_hit:
            score += _W_LOCATION
            reasons.append(f"地点命中: {location}")

        # 排除词惩罚
        if any(kw in company for kw in self.rules.job_keywords.exclude):
            score -= _PENALTY
            reasons.append(f"公司排除词命中")
        if any(kw in job_name for kw in self.rules.job_keywords.exclude):
            score -= _PENALTY
            reasons.append(f"岗位排除词命中")
        if any(kw in location for kw in self.rules.locations.exclude):
            score -= _PENALTY
            reasons.append(f"地点排除词命中")

        return max(score, 0), reasons
