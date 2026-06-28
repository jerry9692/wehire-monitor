"""Extractor 测试"""
import pytest
from unittest.mock import MagicMock, patch

from wehire_monitor.modules.extractor.extractor import Extractor
from wehire_monitor.domain.models import (
    ParsedArticle, ArticleMeta, ImageAsset, PrefilterResult,
    ExtractionResult, Job, Deadline,
)
from datetime import datetime, timezone


def _make_parsed_article(text: str = "德邦证券招聘数据分析师，邮箱 hr@example.com", images: list = None) -> ParsedArticle:
    return ParsedArticle(
        article_id="abc123",
        title="招聘公告",
        plain_text=text,
        images=images or [],
        content_hash="hash",
    )


def test_extract_text_llm_path():
    """正文>=500字且含岗位/投递信息 → 直接文本 LLM"""
    long_text = "德邦证券2026年社会招聘公告。" * 40 + "投递邮箱 hr@example.com"
    article = _make_parsed_article(text=long_text)
    pf = PrefilterResult(score=75, reasons=["命中"], decision="extract")

    mock_llm = MagicMock()
    mock_llm.extract_jobs.return_value = MagicMock(
        success=True,
        article_type="social_recruitment",
        jobs=[Job(
            company_name="德邦证券", job_name="数据分析师", location="上海",
            apply_channel="hr@example.com", email="hr@example.com",
            email_chars=["h","r","@","e","x","a","m","p","l","e",".","c","o","m"],
            deadline=Deadline(date="2026-07-31", inferred=False),
            source_evidence={}, confidence=85,
        )],
        warnings=[],
    )

    extractor = Extractor(llm_provider=mock_llm, ocr_provider=None)
    result = extractor.extract(article, pf)

    assert result.article_type == "social_recruitment"
    assert len(result.jobs) == 1
    assert result.llm_calls == 1
    assert result.ocr_calls == 0
    mock_llm.extract_jobs.assert_called_once()


def test_extract_ocr_then_llm_path():
    """正文短但有图片 → OCR → 质量>=0.72 → 文本 LLM"""
    article = _make_parsed_article(
        text="招聘公告",  # 短文本
        images=[ImageAsset(index=0, url="http://x", local_path="/tmp/img.png", width=800, height=600, sha256="s")],
    )
    pf = PrefilterResult(score=55, reasons=["命中"], decision="extract")

    mock_llm = MagicMock()
    mock_llm.extract_jobs.return_value = MagicMock(
        success=True,
        article_type="social_recruitment",
        jobs=[],
        warnings=[],
    )

    mock_ocr = MagicMock()
    mock_ocr.ocr.return_value = MagicMock(
        lines=[MagicMock(text="德邦证券招聘", confidence=0.9, box=[0,0,100,30])],
        full_text="德邦证券招聘 数据分析师 投递 hr@example.com",
    )

    extractor = Extractor(llm_provider=mock_llm, ocr_provider=mock_ocr)
    with patch("wehire_monitor.modules.extractor.extractor.calculate_ocr_quality", return_value=0.85):
        result = extractor.extract(article, pf)

    assert result.ocr_calls == 1
    assert result.llm_calls == 1
    mock_ocr.ocr.assert_called_once()


def test_extract_ocr_low_quality_needs_review():
    """OCR 质量<0.45 → need_review"""
    article = _make_parsed_article(
        text="短文本",
        images=[ImageAsset(index=0, url="http://x", local_path="/tmp/img.png", width=800, height=600, sha256="s")],
    )
    pf = PrefilterResult(score=55, reasons=["命中"], decision="extract")

    mock_llm = MagicMock()
    mock_ocr = MagicMock()
    mock_ocr.ocr.return_value = MagicMock(
        lines=[MagicMock(text="???", confidence=0.2, box=[0,0,50,20])],
        full_text="???",
    )

    extractor = Extractor(llm_provider=mock_llm, ocr_provider=mock_ocr)
    with patch("wehire_monitor.modules.extractor.extractor.calculate_ocr_quality", return_value=0.30):
        result = extractor.extract(article, pf)

    assert result.llm_calls == 0  # 不调用 LLM
    assert len(result.warnings) > 0
    assert any("need_review" in w or "低质" in w for w in result.warnings)


def test_extract_llm_failure_returns_error():
    """LLM 调用失败 → 返回 error"""
    article = _make_parsed_article(text="招聘" * 200)
    pf = PrefilterResult(score=75, reasons=[], decision="extract")

    mock_llm = MagicMock()
    mock_llm.extract_jobs.return_value = MagicMock(
        success=False,
        article_type="unknown",
        jobs=[],
        warnings=[],
        error="API error",
    )

    extractor = Extractor(llm_provider=mock_llm, ocr_provider=None)
    result = extractor.extract(article, pf)

    assert result.article_type == "unknown"
    assert len(result.jobs) == 0
    assert len(result.warnings) > 0
