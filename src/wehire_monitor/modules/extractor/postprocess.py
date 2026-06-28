"""后处理校验(SRS §4.4 后处理)

- 邮箱正则校验
- email 与 email_chars 一致性
- 截止日期早于发布时间标记异常
- confidence < 60 进复核区
- 地点空但正文含城市词 → 二次抽取
"""
from __future__ import annotations
import re
from datetime import datetime

from wehire_monitor.domain.models import Job

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# 常见城市词(用于地点二次抽取)
_CITY_WORDS = [
    "北京", "上海", "广州", "深圳", "杭州", "苏州", "南京", "成都", "武汉",
    "西安", "重庆", "天津", "青岛", "大连", "宁波", "厦门", "长沙", "郑州",
    "合肥", "济南", "福州", "昆明", "贵阳", "南昌", "太原", "兰州", "南宁",
    "海口", "沈阳", "长春", "哈尔滨", "石家庄", "呼和浩特", "乌鲁木齐",
    "拉萨", "银川", "西宁", "香港", "澳门",
]


def validate_email(email: str | None) -> bool:
    """邮箱正则校验(先 strip 空白)"""
    if not email:
        return False
    return bool(_EMAIL_RE.match(email.strip()))


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


def extract_location_from_text(text: str, job: Job) -> str | None:
    """地点空但正文含城市词 → 二次抽取(SRS §4.4)

    从 source_evidence 的 raw_text 或 job 字段中搜索城市词。
    返回找到的城市名,未找到返回 None。
    """
    # 搜索范围: source_evidence 中的原始文本片段
    search_text = ""
    if "raw_text" in job.source_evidence:
        search_text = job.source_evidence["raw_text"]
    elif "text_snippet" in job.source_evidence:
        search_text = job.source_evidence["text_snippet"]
    # 也检查 job_name 和 company_name 中可能包含的城市信息
    if not search_text:
        parts = [job.company_name or "", job.job_name or ""]
        search_text = " ".join(parts)

    for city in _CITY_WORDS:
        if city in search_text:
            return city
    return None


def postprocess_jobs(
    jobs: list[Job], publish_time: str, article_text: str = ""
) -> list[Job]:
    """对提取的岗位列表执行后处理校验,在 source_evidence['_warnings'] 中追加警告

    Args:
        jobs: 岗位列表
        publish_time: 发布时间(ISO8601)
        article_text: 文章正文(供地点二次抽取使用)
    """
    for job in jobs:
        warnings: list[str] = []
        if "_warnings" not in job.source_evidence:
            job.source_evidence["_warnings"] = []
        else:
            # 保留 LLM 已产生的警告,追加后处理警告
            warnings = list(job.source_evidence["_warnings"])

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

        # 地点二次抽取: 地点空但含城市词
        if not job.location:
            # 优先从文章正文中搜索
            search_src = article_text or job.source_evidence.get("raw_text", "")
            for city in _CITY_WORDS:
                if city in search_src:
                    job.location = city
                    break
            if not job.location:
                # 也检查 company_name/job_name
                combined = f"{job.company_name or ''} {job.job_name or ''}"
                for city in _CITY_WORDS:
                    if city in combined:
                        job.location = city
                        break

        # 去重后写回
        job.source_evidence["_warnings"] = list(dict.fromkeys(warnings))

    return jobs


def needs_review(job: Job) -> bool:
    """判断岗位是否需要人工复核

    触发条件: email_mismatch / email_invalid / low_confidence / deadline_before_publish
    (SRS §4.4: confidence<60 进复核区; email_mismatch→need_review;
     截止日期早于发布时间标记异常)
    """
    warnings = job.source_evidence.get("_warnings", [])
    review_triggers = (
        "email_mismatch", "low_confidence",
        "email_invalid", "deadline_before_publish",
    )
    return any(w in warnings for w in review_triggers)
