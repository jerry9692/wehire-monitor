"""VLM 切片结果合并去重(SRS §4.4 VLM 多切片合并)

长图按切片送 VLM 后,同一岗位可能出现在相邻切片中,需要合并去重:

- 岗位唯一键 = normalize(company) + normalize(job_name) + normalize(location)
  - 若关键字段为空,结合 source_evidence 中的 y_range/slice_index 辅助消歧
- 相邻切片重复岗位 → 保留字段更完整者
- 邮箱冲突 → 保留正则合法且置信度高者,标记 "email_conflict" 到 source_evidence["_warnings"]
- 补充缺失字段: apply_channel, deadline, email 从其他切片补充
- 置信度取较高者
- 全空字段岗位(幻觉)且置信度低的予以过滤
"""
from __future__ import annotations

import copy

from loguru import logger

from wehire_monitor.domain.models import Deadline, Job
from wehire_monitor.modules.extractor.postprocess import validate_email

# 三个主键字段全为空时的唯一键
_EMPTY_KEY = "||"
# 低置信度阈值(低于此值的全空岗位视为幻觉)
_HALLUCINATION_CONF_THRESHOLD = 30


def _normalize(text: str | None) -> str:
    """去空白并转小写"""
    if not text:
        return ""
    return "".join(text.split()).lower()


def _job_key(job: Job) -> str:
    """生成岗位唯一键: normalize(company|job_name|location)

    若三字段全空,附加 slice_index 信息避免不同切片的空岗位被错误合并。
    """
    company = _normalize(job.company_name)
    job_name = _normalize(job.job_name)
    location = _normalize(job.location)
    key = f"{company}|{job_name}|{location}"
    if key == _EMPTY_KEY:
        # 三字段全空:使用 source_evidence 中的切片信息消歧
        sl = job.source_evidence.get("slice_index", -1)
        return f"{key}__slice{sl}"
    return key


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


def _is_hallucination(job: Job) -> bool:
    """判断是否为 VLM 幻觉产生的伪岗位(关键字段全空且低置信度)"""
    has_key = bool(job.company_name or job.job_name or job.location)
    if has_key:
        return False
    return job.confidence < _HALLUCINATION_CONF_THRESHOLD


def _merge_two(base: Job, other: Job) -> Job:
    """合并两个相同岗位(基于唯一键判定)

    - 以字段更完整者为基准(相同则取置信度高者)
    - 邮箱冲突 → 保留正则合法且置信度高者,标记 email_conflict
    - 缺失字段从另一岗位补充(apply_channel / deadline / email)
    - 置信度取较高者
    """
    # 1. 选基准: 完整度更高者;相同则置信度更高者
    ca, cb = _job_completeness(base), _job_completeness(other)
    if ca < cb or (ca == cb and other.confidence > base.confidence):
        base_src, other_src = other, base
    else:
        base_src, other_src = base, other
    merged = copy.deepcopy(base_src)

    # 2. 合并 source_evidence 与 _warnings
    merged_evidence: dict = dict(merged.source_evidence)
    warnings: list[str] = list(merged_evidence.get("_warnings", []))
    for w in other_src.source_evidence.get("_warnings", []):
        if w not in warnings:
            warnings.append(w)

    # 3. 邮箱冲突检测与解决(使用 base_src/other_src 而非全局 a/b)
    base_email = (base_src.email or "").strip()
    other_email = (other_src.email or "").strip()
    if base_email and other_email and _normalize(base_email) != _normalize(other_email):
        # 邮箱冲突
        warnings.append("email_conflict")
        base_valid = validate_email(base_email)
        other_valid = validate_email(other_email)
        if base_valid and not other_valid:
            merged.email = base_src.email
            merged.email_chars = list(base_src.email_chars)
        elif other_valid and not base_valid:
            merged.email = other_src.email
            merged.email_chars = list(other_src.email_chars)
        else:
            # 两者都合法或都不合法 → 取置信度高者(使用 base_src/other_src)
            if base_src.confidence >= other_src.confidence:
                merged.email = base_src.email
                merged.email_chars = list(base_src.email_chars)
            else:
                merged.email = other_src.email
                merged.email_chars = list(other_src.email_chars)
        logger.debug(f"邮箱冲突: '{base_email}' vs '{other_email}', 保留 '{merged.email}'")
    else:
        # 无冲突: 补充缺失邮箱
        if not merged.email and other_src.email:
            merged.email = other_src.email
            merged.email_chars = list(other_src.email_chars)

    # 4. 补充其他缺失字段(从 other_src 补充,而非 other 变量)
    if not merged.apply_channel and other_src.apply_channel:
        merged.apply_channel = other_src.apply_channel
    if merged.deadline and (not merged.deadline.date) and other_src.deadline and other_src.deadline.date:
        merged.deadline = Deadline(
            date=other_src.deadline.date, inferred=other_src.deadline.inferred
        )

    # 5. 置信度取较高者
    merged.confidence = max(merged.confidence, other_src.confidence)

    # 6. 写回 warnings(去重保序)
    merged_evidence["_warnings"] = list(dict.fromkeys(warnings))
    merged.source_evidence = merged_evidence

    return merged


def merge_slice_jobs(slice_jobs: list[list[Job]]) -> list[Job]:
    """合并多个切片的岗位列表,去重

    Args:
        slice_jobs: 每个切片提取出的岗位列表(外层按切片顺序)

    Returns:
        去重合并后的岗位列表(保持首次出现顺序),过滤低置信度幻觉
    """
    merged: list[Job] = []
    key_to_index: dict[str, int] = {}
    total = 0

    for slice_list in slice_jobs:
        for job in slice_list:
            total += 1
            # 过滤幻觉伪岗位
            if _is_hallucination(job):
                logger.debug(f"过滤疑似幻觉岗位(conf={job.confidence})")
                continue
            key = _job_key(job)
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
