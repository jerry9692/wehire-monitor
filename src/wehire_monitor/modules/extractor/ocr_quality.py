"""OCR 质量评分(SRS §4.4)

ocr_quality_score =
  0.35 * 平均识别置信度
+ 0.25 * 中英文有效字符比例
+ 0.20 * 邮箱/电话/日期/地点正则命中
+ 0.10 * 文本行顺序稳定性
+ 0.10 * 岗位关键词覆盖率
"""
from __future__ import annotations
import re

from wehire_monitor.domain.models import OCRResult

# 正则模式
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_DATE_RE = re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}")
_LOCATION_RE = re.compile(r"(北京|上海|广州|深圳|杭州|苏州|南京|成都|武汉|西安|天津|重庆)")
_VALID_CHAR_RE = re.compile(r"[\u4e00-\u9fa5a-zA-Z0-9@.]")

# 岗位关键词
_JOB_KEYWORDS = {"招聘", "岗位", "职位", "应聘", "投递", "简历", "报名", "录用", "薪资", "待遇"}


def calculate_ocr_quality(ocr_result: OCRResult) -> float:
    """计算 OCR 质量评分,返回 0.0-1.0"""
    if not ocr_result.lines:
        return 0.0

    full_text = ocr_result.full_text
    lines = ocr_result.lines

    # 1. 平均识别置信度 (0.35)
    avg_conf = sum(l.confidence for l in lines) / len(lines)

    # 2. 中英文有效字符比例 (0.25)
    valid_chars = len(_VALID_CHAR_RE.findall(full_text))
    total_chars = len(full_text) if full_text else 1
    valid_ratio = min(valid_chars / total_chars, 1.0)

    # 3. 邮箱/电话/日期/地点正则命中 (0.20)
    hits = 0
    if _EMAIL_RE.search(full_text):
        hits += 1
    if _PHONE_RE.search(full_text):
        hits += 1
    if _DATE_RE.search(full_text):
        hits += 1
    if _LOCATION_RE.search(full_text):
        hits += 1
    regex_score = hits / 4.0

    # 4. 文本行顺序稳定性 (0.10) — 基于 y 坐标是否递增
    y_coords = [l.box[1] for l in lines if len(l.box) >= 2]
    if len(y_coords) >= 2:
        increasing = sum(1 for i in range(1, len(y_coords)) if y_coords[i] >= y_coords[i - 1])
        order_stability = increasing / (len(y_coords) - 1)
    else:
        order_stability = 1.0

    # 5. 岗位关键词覆盖率 (0.10)
    matched_keywords = sum(1 for kw in _JOB_KEYWORDS if kw in full_text)
    keyword_coverage = min(matched_keywords / 5.0, 1.0)  # 命中5个即满分

    score = (
        0.35 * avg_conf
        + 0.25 * valid_ratio
        + 0.20 * regex_score
        + 0.10 * order_stability
        + 0.10 * keyword_coverage
    )

    return round(min(score, 1.0), 4)
