"""Extractor 测试 — 统一多模态模型架构(v0.3)

原三路提取(LLM/OCR+LLM/VLM)已演进为统一多模态模型单路提取:
同一个 provider 同时接收文本和图片,完成结构化岗位信息提取。
"""
import pytest
from unittest.mock import MagicMock, Mock

from wehire_monitor.modules.extractor.extractor import Extractor
from wehire_monitor.providers.multimodal.base import (
    MultimodalProvider,
    MultimodalResponse,
)
from wehire_monitor.domain.models import (
    ParsedArticle, ImageAsset, PrefilterResult, Job, Deadline,
)


def _make_parsed_article(text: str = "德邦证券招聘数据分析师，邮箱 hr@example.com", images: list = None) -> ParsedArticle:
    return ParsedArticle(
        article_id="abc123",
        title="招聘公告",
        plain_text=text,
        images=images or [],
        content_hash="hash",
    )


def _make_job():
    return Job(
        company_name="德邦证券", job_name="数据分析师", location="上海",
        apply_channel="hr@example.com", email="hr@example.com",
        email_chars=["h", "r", "@", "e", "x", "a", "m", "p", "l", "e", ".", "c", "o", "m"],
        deadline=Deadline(date="2026-07-31", inferred=False),
        source_evidence={}, confidence=85,
    )


def _make_provider(response: MultimodalResponse) -> Mock:
    """构造带 spec 的 MultimodalProvider mock,extract_jobs 返回指定响应"""
    provider = Mock(spec=MultimodalProvider)
    provider.name = "mock-multimodal"
    provider.model = "mock-model"
    provider.extract_jobs.return_value = response
    return provider


def test_extract_text_only_path():
    """纯文本文章 → 多模态模型提取,验证 model_calls 与 cost_estimate"""
    long_text = "德邦证券2026年社会招聘公告。" * 40 + "投递邮箱 hr@example.com"
    article = _make_parsed_article(text=long_text)
    pf = PrefilterResult(score=75, reasons=["命中"], decision="extract")

    response = MultimodalResponse(
        success=True,
        article_type="social_recruitment",
        jobs=[_make_job()],
        warnings=[],
        cost_estimate=0.05,
        model_calls=1,
    )
    mock_provider = _make_provider(response)

    extractor = Extractor(multimodal_provider=mock_provider)
    result = extractor.extract(article, pf)

    assert result.article_type == "social_recruitment"
    assert len(result.jobs) == 1
    assert result.jobs[0].company_name == "德邦证券"
    assert result.model_calls == 1
    assert result.cost_estimate == pytest.approx(0.05)
    mock_provider.extract_jobs.assert_called_once()
    # 纯文本:images 为空列表,text 为正文
    _, kwargs = mock_provider.extract_jobs.call_args
    assert kwargs["text"] == long_text
    assert kwargs["images"] == []


def test_extract_with_images_path():
    """有图片文章 → 短图封装为单切片,文本和图片一起传给 provider"""
    article = _make_parsed_article(
        text="招聘公告",
        images=[ImageAsset(index=0, url="http://x", local_path="/tmp/img.png", width=800, height=600, sha256="s")],
    )
    pf = PrefilterResult(score=55, reasons=["命中"], decision="extract")

    response = MultimodalResponse(
        success=True,
        article_type="social_recruitment",
        jobs=[_make_job()],
        warnings=[],
        cost_estimate=0.08,
        model_calls=1,
    )
    mock_provider = _make_provider(response)

    extractor = Extractor(multimodal_provider=mock_provider)
    result = extractor.extract(article, pf)

    assert result.article_type == "social_recruitment"
    assert len(result.jobs) == 1
    assert result.model_calls == 1
    assert result.cost_estimate == pytest.approx(0.08)
    mock_provider.extract_jobs.assert_called_once()
    # 图片被封装为 ImageSlice 传入,text 与正文一致
    _, kwargs = mock_provider.extract_jobs.call_args
    assert kwargs["text"] == "招聘公告"
    assert len(kwargs["images"]) == 1


def test_extract_budget_exhausted_needs_review():
    """预算耗尽 → 标记 need_review,不调用 provider"""
    article = _make_parsed_article(text="招聘" * 200)
    pf = PrefilterResult(score=75, reasons=[], decision="extract")

    response = MultimodalResponse(success=True, jobs=[_make_job()], model_calls=1)
    mock_provider = _make_provider(response)

    mock_budget = MagicMock()
    mock_budget.is_exhausted.return_value = True

    extractor = Extractor(
        multimodal_provider=mock_provider, budget_manager=mock_budget,
    )
    result = extractor.extract(article, pf)

    assert result.article_type == "unknown"
    assert len(result.jobs) == 0
    assert result.model_calls == 0
    assert any("need_review" in w for w in result.warnings)
    mock_provider.extract_jobs.assert_not_called()


def test_extract_provider_failure_returns_error():
    """provider 返回失败 → 返回 error,透传 model_calls 与 cost_estimate"""
    article = _make_parsed_article(text="招聘" * 200)
    pf = PrefilterResult(score=75, reasons=[], decision="extract")

    response = MultimodalResponse(
        success=False,
        article_type="unknown",
        jobs=[],
        warnings=[],
        error="API error",
        cost_estimate=0.02,
        model_calls=1,
    )
    mock_provider = _make_provider(response)

    extractor = Extractor(multimodal_provider=mock_provider)
    result = extractor.extract(article, pf)

    assert result.article_type == "unknown"
    assert len(result.jobs) == 0
    assert result.model_calls == 1
    assert result.cost_estimate == pytest.approx(0.02)
    assert any("error" in w for w in result.warnings)
