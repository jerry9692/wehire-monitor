"""关键词预过滤与评分

评分公式(承接 spec §4.3):
    招聘分 = 标题命中*40 + 正文命中*30 + 投递词命中*20 + 邮箱/报名链接命中*10 - 排除词惩罚

门控:
    score >= 50 → extract
    30 <= score < 50 → ocr_review
    score < 30 → ignore
"""
import re
from dataclasses import dataclass
from typing import Literal

from loguru import logger

from wehire_monitor.config.schemas import KeywordsConfig
from wehire_monitor.domain.models import ParsedArticle

# 投递相关词
_DELIVERY_WORDS = ["投递", "报名", "邮箱", "应聘", "简历投递", "邮件"]

# 邮箱正则
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 报名链接正则
_URL_RE = re.compile(r"https?://[^\s]+")


@dataclass
class PrefilterResult:
    """预过滤结果"""
    score: int
    reasons: list[str]
    decision: Literal["extract", "ocr_review", "ignore"]


class Prefilter:
    """关键词预过滤器"""

    def __init__(self, keywords: KeywordsConfig):
        self.hit_words = keywords.strong_hit
        self.exclude_words = keywords.strong_exclude

    def score(self, article: ParsedArticle) -> PrefilterResult:
        """计算招聘分并决定门控"""
        reasons: list[str] = []
        score = 0

        title = article.title
        text = article.plain_text
        text_head = text[:1000]  # 正文前 1000 字

        # 标题命中 * 40
        title_hits = [w for w in self.hit_words if w in title]
        if title_hits:
            score += 40
            reasons.append(f"标题命中: {', '.join(title_hits)}")

        # 正文命中 * 30
        body_hits = [w for w in self.hit_words if w in text_head]
        if body_hits:
            score += 30
            reasons.append(f"正文命中: {', '.join(body_hits)}")

        # 投递词命中 * 20
        delivery_hits = [w for w in _DELIVERY_WORDS if w in text_head]
        if delivery_hits:
            score += 20
            reasons.append(f"投递词命中: {', '.join(delivery_hits)}")

        # 邮箱/报名链接命中 * 10
        has_email = bool(_EMAIL_RE.search(text))
        has_url = bool(_URL_RE.search(text))
        if has_email or has_url:
            score += 10
            if has_email:
                reasons.append("正文包含邮箱")
            if has_url:
                reasons.append("正文包含链接")

        # 排除词惩罚(每个 -15)
        exclude_hits = [w for w in self.exclude_words if w in title or w in text_head]
        for w in exclude_hits:
            score -= 15
            reasons.append(f"排除词命中: {w}")

        score = max(0, score)

        # 门控
        if score >= 50:
            decision = "extract"
        elif score >= 30:
            decision = "ocr_review"
        else:
            decision = "ignore"

        logger.debug(f"预过滤: {article.title} → score={score}, decision={decision}")
        return PrefilterResult(score=score, reasons=reasons, decision=decision)
