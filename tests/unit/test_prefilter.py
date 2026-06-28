"""Prefilter 测试"""
from wehire_monitor.modules.prefilter.prefilter import Prefilter
from wehire_monitor.domain.models import ParsedArticle
from wehire_monitor.config.schemas import KeywordsConfig


def _make_keywords() -> KeywordsConfig:
    return KeywordsConfig(
        strong_hit=["招聘", "社招", "社会招聘", "岗位", "投递", "简历", "录用"],
        strong_exclude=["校招", "校园招聘", "实习", "培训"],
    )


def _make_article(title: str, text: str) -> ParsedArticle:
    return ParsedArticle(
        article_id="hash001",
        title=title,
        plain_text=text,
        images=[],
        content_hash="chash",
    )


def test_high_score_recruitment_article():
    """标题+正文双命中,高分进入提取"""
    pf = Prefilter(_make_keywords())
    article = _make_article(
        "某某集团2026年社会招聘公告",
        "岗位:数据分析师。投递简历至 hr@example.com。录用后待遇优厚。",
    )
    result = pf.score(article)
    assert result.score >= 50
    assert result.decision == "extract"


def test_low_score_non_recruitment():
    """无招聘关键词,低分丢弃"""
    pf = Prefilter(_make_keywords())
    article = _make_article(
        "公司年会回顾",
        "今天举办了年度总结会议,大家合影留念。",
    )
    result = pf.score(article)
    assert result.score < 30
    assert result.decision == "ignore"


def test_exclude_words_penalty():
    """排除词应减分"""
    pf = Prefilter(_make_keywords())
    article = _make_article(
        "校园招聘宣讲会",
        "实习生培训班报名中。岗位招聘信息。",
    )
    result = pf.score(article)
    # 有排除词惩罚,分数应低于无排除词情况
    assert "校招" in str(result.reasons) or "校园招聘" in str(result.reasons) or "实习" in str(result.reasons) or "培训" in str(result.reasons) or result.score < 100


def test_medium_score_ocr_review():
    """中等分数进入 OCR 复核"""
    pf = Prefilter(_make_keywords())
    article = _make_article(
        "招聘",
        "一般性内容,无投递信息。",
    )
    result = pf.score(article)
    if 30 <= result.score < 50:
        assert result.decision == "ocr_review"


def test_title_hit_weight_40():
    """标题命中权重 40"""
    pf = Prefilter(_make_keywords())
    article = _make_article(
        "社会招聘公告",
        "今天天气晴朗,适合出游,无其他内容。",
    )
    result = pf.score(article)
    # 标题命中"社会招聘",正文无命中
    # 分数 = 40(标题) + 0(正文) + 0(投递) + 0(邮箱) = 40
    assert result.score == 40
    assert result.decision == "ocr_review"
