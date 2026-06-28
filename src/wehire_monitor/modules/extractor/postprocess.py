"""后处理校验(SRS §4.4 后处理)

- 邮箱正则校验
- email 与 email_chars 一致性
- 截止日期早于发布时间标记异常
- confidence < 60 进复核区
"""
from __future__ import annotations
import re
from datetime import datetime

from wehire_monitor.domain.models import Job

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def validate_email(email: str | None) -> bool:
    """邮箱正则校验"""
    if not email:
        return False
    return bool(_EMAIL_RE.match(email))


def validate_email_chars_consistency(job: Job) -> bool:
    """email 与 email_chars.join('') 是否一致"""
    if not job.email:
        return True  # email 为 null 不校验
    joined = "".join(job.email_chars)
    return joined == job.email


def validate_deadline(deadline_date: str | None, publish_time: str) -> bool:
    """截止日期是否晚于(或等于)发布时间"""
    if not deadline_date:
        return True
    try:
        deadline = datetime.fromisoformat(deadline_date)
        publish = datetime.fromisoformat(publish_time.split("T")[0])
        return deadline >= publish
    except (ValueError, IndexError):
        return True  # 解析失败不标记


def check_confidence(confidence: int) -> bool:
    """置信度是否达标(>=60)"""
    return confidence >= 60


def postprocess_jobs(
    jobs: list[Job], publish_time: str
) -> list[Job]:
    """对提取的岗位列表执行后处理校验,在 source_evidence['_warnings'] 中追加警告"""
    for job in jobs:
        warnings: list[str] = []
        if "_warnings" not in job.source_evidence:
            job.source_evidence["_warnings"] = []

        # 邮箱校验
        if job.email and not validate_email(job.email):
            warnings.append("email_invalid")
        if not validate_email_chars_consistency(job):
            warnings.append("email_mismatch")

        # 截止日期校验
        if not validate_deadline(job.deadline.date, publish_time):
            warnings.append("deadline_before_publish")

        # 置信度校验
        if not check_confidence(job.confidence):
            warnings.append("low_confidence")

        job.source_evidence["_warnings"] = warnings

    return jobs


def needs_review(job: Job) -> bool:
    """判断岗位是否需要人工复核(email_mismatch/low_confidence/email_invalid)"""
    warnings = job.source_evidence.get("_warnings", [])
    return any(w in warnings for w in ("email_mismatch", "low_confidence")) or \
        "email_invalid" in warnings
