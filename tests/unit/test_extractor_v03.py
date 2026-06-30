"""Extractor v0.3 统一多模态提取测试

统一多模态架构(v0.3):
  短图拼接(stitcher) → 长图切片(slicer) → 多模态模型提取
  预算耗尽 → need_review
  一个模型同时处理文本和图片,不再有 OCR 质量评分与三路切换
"""
from unittest.mock import MagicMock, Mock, patch

from wehire_monitor.modules.extractor.extractor import Extractor
from wehire_monitor.providers.multimodal.base import (
    MultimodalProvider,
    MultimodalResponse,
)
from wehire_monitor.domain.models import (
    ParsedArticle,
    ImageAsset,
    PrefilterResult,
    Job,
    Deadline,
    ImageSlice,
    SliceMeta,
)


# ----------------------------------------------------------------------
# 辅助构造函数
# ----------------------------------------------------------------------

def _make_parsed_article(text="短文本", images=None):
    return ParsedArticle(
        article_id="abc123",
        title="招聘公告",
        plain_text=text,
        images=images or [],
        content_hash="hash",
    )


def _make_image(index=0, height=600, local_path="/tmp/test_img.png"):
    return ImageAsset(
        index=index,
        url="http://x",
        local_path=local_path,
        width=800,
        height=height,
        sha256="sha",
    )


def _make_job(company="德邦证券"):
    return Job(
        company_name=company,
        job_name="数据分析师",
        location="上海",
        apply_channel="hr@example.com",
        email="hr@example.com",
        email_chars=list("hr@example.com"),
        deadline=Deadline(date="2026-07-31", inferred=False),
        source_evidence={},
        confidence=85,
    )


def _make_slice(image_index=0, slice_index=0, y_start=0, y_end=1500,
                local_path="/tmp/slice.png"):
    """构造单个 ImageSlice"""
    return ImageSlice(
        pil_image=None,
        local_path=local_path,
        meta=SliceMeta(
            image_index=image_index,
            slice_index=slice_index,
            y_start=y_start,
            y_end=y_end,
            is_bottom=(slice_index == 1),
        ),
    )


def _make_response(jobs=None, success=True, model_calls=1, cost_estimate=0.03):
    """构造 MultimodalResponse"""
    return MultimodalResponse(
        success=success,
        article_type="social_recruitment",
        jobs=jobs if jobs is not None else [_make_job()],
        warnings=[],
        cost_estimate=cost_estimate,
        model_calls=model_calls,
        error="" if success else "API error",
    )


def _make_provider(response=None):
    """构造带 spec 的 MultimodalProvider mock,extract_jobs 返回指定响应"""
    provider = Mock(spec=MultimodalProvider)
    provider.name = "mock-multimodal"
    provider.model = "mock-model"
    provider.extract_jobs.return_value = response or _make_response()
    return provider


# ----------------------------------------------------------------------
# 1. 长图触发切片
# ----------------------------------------------------------------------
def test_long_image_triggers_slicing():
    """长图(高度>3000)→ 触发 slicer 切片,多切片传给 provider"""
    article = _make_parsed_article(
        text="短文本",
        images=[_make_image(height=4000, local_path="/tmp/long_img.png")],
    )
    pf = PrefilterResult(score=55, reasons=["命中"], decision="extract")

    mock_slicer = MagicMock()
    mock_slicer.slice_image.return_value = [
        _make_slice(slice_index=0, y_start=0, y_end=1500),
        _make_slice(slice_index=1, y_start=1280, y_end=4000),
    ]

    response = _make_response(model_calls=1, cost_estimate=0.06)
    mock_provider = _make_provider(response)

    extractor = Extractor(
        multimodal_provider=mock_provider, slicer=mock_slicer,
    )
    with patch(
        "wehire_monitor.modules.extractor.extractor.should_slice_image",
        return_value=True,
    ):
        result = extractor.extract(article, pf)

    # slicer 被调用,传入长图路径与 image_index
    mock_slicer.slice_image.assert_called_once_with(
        "/tmp/long_img.png", image_index=0,
    )
    # provider 收到 2 个切片
    mock_provider.extract_jobs.assert_called_once()
    _, kwargs = mock_provider.extract_jobs.call_args
    assert len(kwargs["images"]) == 2
    # 结果
    assert result.article_type == "social_recruitment"
    assert len(result.jobs) == 1
    assert result.model_calls == 1
    assert result.cost_estimate == 0.06


# ----------------------------------------------------------------------
# 2. 预算耗尽 → 跳过模型调用
# ----------------------------------------------------------------------
def test_budget_exhausted_skips_model():
    """预算耗尽 → 标记 need_review,不调用 provider"""
    article = _make_parsed_article(
        text="短文本",
        images=[_make_image(height=600)],
    )
    pf = PrefilterResult(score=55, reasons=["命中"], decision="extract")

    mock_provider = _make_provider()
    mock_budget = MagicMock()
    mock_budget.is_exhausted.return_value = True

    extractor = Extractor(
        multimodal_provider=mock_provider, budget_manager=mock_budget,
    )
    result = extractor.extract(article, pf)

    # provider 未被调用, 标记 need_review
    mock_provider.extract_jobs.assert_not_called()
    assert result.model_calls == 0
    assert any("need_review" in w for w in result.warnings)
    assert result.article_type == "unknown"
    assert len(result.jobs) == 0


# ----------------------------------------------------------------------
# 3. 文本和图片同时传给 provider
# ----------------------------------------------------------------------
def test_text_and_images_both_passed_to_provider():
    """有文本且有图片 → 文本和图片切片同时传给 provider"""
    article = _make_parsed_article(
        text="德邦证券2026年社会招聘公告,岗位数据分析师,投递 hr@example.com",
        images=[_make_image(height=600, local_path="/tmp/img.png")],
    )
    pf = PrefilterResult(score=55, reasons=["命中"], decision="extract")

    response = _make_response(model_calls=1, cost_estimate=0.05)
    mock_provider = _make_provider(response)

    extractor = Extractor(multimodal_provider=mock_provider)
    result = extractor.extract(article, pf)

    mock_provider.extract_jobs.assert_called_once()
    _, kwargs = mock_provider.extract_jobs.call_args
    # 文本和图片都传给了 provider
    assert kwargs["text"] == article.plain_text
    assert len(kwargs["images"]) == 1
    # 结果
    assert result.article_type == "social_recruitment"
    assert result.model_calls == 1
    assert result.cost_estimate == 0.05
