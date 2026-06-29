"""VLM 切片结果合并去重(SRS §4.4 VLM 多切片合并)

长图按切片送 VLM 后,同一岗位可能出现在相邻切片中,需要合并去重:

- 岗位唯一键 = normalize(company) + normalize(job_name) + normalize(location)
- 相邻切片重复岗位 → 保留字段更完整者
- 邮箱冲突 → 保留正则合法且置信度高者,标记 "email_conflict" 到 source_evidence["_warnings"]
- 补充缺失字段: apply_channel, deadline, email 从其他切片补充
- 置信度取较高者
"""
from __future__ import annotations

import copy

from loguru import logger

from wehire_monitor.domain.models import Deadline, Job
from wehire_monitor.modules.extractor.postprocess import validate_email

# 三个主键字段全为空时的唯一键
_EMPTY_KEY = "||"


def _normalize(text: str | None) -> str:
    """去空白并转小写"""
    if not text:
        return ""
    return "".join(text.split()).lower()


def _job_key(job: Job) -> str:
    """生成岗位唯一键: normalize(company|job_name|location)"""
    return "|".join(
        [
            _normalize(job.company_name),
            _normalize(job.job_name),
            _normalize(job.location),
        ]
    )


def _job_completeness(job: Job) -> int:
    """字段完整度评分(非空字段计数)

    考察字段: company_name / job_name / location /
    apply_channel / email / deadline.date
    """
    score = 0
    if job.company_name:
        score += 1
    if job.job_name:
        score += 1
    if job.location:
        score += 1
    if job.apply_channel:
        score += 1
    if job.email:
        score += 1
    if job.deadline and job.deadline.date:
        score += 1
    return score


def _merge_two(a: Job, b: Job) -> Job:
    """合并两个相同岗位(基于唯一键判定)

    - 以字段更完整者为基准(相同则取置信度高者)
    - 邮箱冲突 → 保留正则合法且置信度高者,标记 email_conflict
    - 缺失字段从另一岗位补充(apply_channel / deadline / email)
    - 置信度取较高者
    """
    # 1. 选基准: 完整度更高者;相同则置信度更高者
    ca, cb = _job_completeness(a), _job_completeness(b)
    if ca > cb or (ca == cb and a.confidence >= b.confidence):
        base_src, other = a, b
    else:
        base_src, other = b, a
    base = copy.deepcopy(base_src)

    # 2. 合并 source_evidence 与 _warnings
    merged_evidence: dict = dict(base.source_evidence)
    warnings: list[str] = list(merged_evidence.get("_warnings", []))
    for w in other.source_evidence.get("_warnings", []):
        if w not in warnings:
            warnings.append(w)

    # 3. 邮箱冲突检测与解决
    a_email = (a.email or "").strip()
    b_email = (b.email or "").strip()
    if a_email and b_email and _normalize(a_email) != _normalize(b_email):
        # 邮箱冲突
        warnings.append("email_conflict")
        a_valid = validate_email(a_email)
        b_valid = validate_email(b_email)
        if a_valid and not b_valid:
            base.email = a.email
            base.email_chars = list(a.email_chars)
        elif b_valid and not a_valid:
            base.email = b.email
            base.email_chars = list(b.email_chars)
        else:
            # 两者都合法或都不合法 → 取置信度高者
            if a.confidence >= b.confidence:
                base.email = a.email
                base.email_chars = list(a.email_chars)
            else:
                base.email = b.email
                base.email_chars = list(b.email_chars)
        logger.debug(f"邮箱冲突: '{a_email}' vs '{b_email}', 保留 '{base.email}'")
    else:
        # 无冲突: 补充缺失邮箱
        if not base.email and other.email:
            base.email = other.email
            base.email_chars = list(other.email_chars)

    # 4. 补充其他缺失字段
    if not base.apply_channel and other.apply_channel:
        base.apply_channel = other.apply_channel
    if (not base.deadline.date) and other.deadline.date:
        base.deadline = Deadline(
            date=other.deadline.date, inferred=other.deadline.inferred
        )

    # 5. 置信度取较高者
    base.confidence = max(base.confidence, other.confidence)

    # 6. 写回 warnings(去重保序)
    merged_evidence["_warnings"] = list(dict.fromkeys(warnings))
    base.source_evidence = merged_evidence

    return base


def merge_slice_jobs(slice_jobs: list[list[Job]]) -> list[Job]:
    """合并多个切片的岗位列表,去重

    Args:
        slice_jobs: 每个切片提取出的岗位列表(外层按切片顺序)

    Returns:
        去重合并后的岗位列表(保持首次出现顺序)
    """
    merged: list[Job] = []
    key_to_index: dict[str, int] = {}
    total = 0

    for slice_list in slice_jobs:
        for job in slice_list:
            total += 1
            key = _job_key(job)
            # 三个主键字段全为空时无法判定唯一性,直接保留不去重
            if key == _EMPTY_KEY:
                merged.append(job)
                continue
            if key in key_to_index:
                idx = key_to_index[key]
                merged[idx] = _merge_two(merged[idx], job)
                logger.debug(f"合并重复岗位: {key}")
            else:
                key_to_index[key] = len(merged)
                merged.append(job)

    if len(merged) < total:
        logger.info(f"切片合并: {total} → {len(merged)} 个岗位")
    return merged
