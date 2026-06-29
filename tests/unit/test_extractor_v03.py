"""Extractor v0.3 三路切换测试

三路切换逻辑:
  正文>=500字 且含岗位/投递信息 → 文本 LLM (路径1)
  有图片 → OCR → 质量评分:
    score>=0.72 → OCR文本+原文 → 文本 LLM (路径2a)
    0.45<=score<0.72 → VLM 复核 (路径2b)
    score<0.45 或长图 → VLM 切片提取 (路径2c)
  预算耗尽 → need_review
"""
from unittest.mock import MagicMock, patch

from wehire_monitor.modules.extractor.extractor import Extractor
from wehire_monitor.domain.models import (
    ParsedArticle,
    ImageAsset,
    PrefilterResult,
    ExtractionResult,
    Job,
    Deadline,
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


def _mock_vlm_response(jobs=None, success=True):
    """构造 VLM mock 响应"""
    resp = MagicMock()
    resp.success = success
    resp.article_type = "social_recruitment"
    resp.jobs = jobs if jobs is not None else [_make_job()]
    resp.warnings = []
    resp.cost_estimate = 0.03
    resp.error = ""
    return resp


def _mock_llm_response(jobs=None, success=True):
    """构造 LLM mock 响应"""
    resp = MagicMock()
    resp.success = success
    resp.article_type = "social_recruitment"
    resp.jobs = jobs if jobs is not None else [_make_job()]
    resp.warnings = []
    resp.error = ""
    return resp


def _mock_ocr_result():
    """构造 OCR mock 结果"""
    return MagicMock(
        lines=[MagicMock(text="德邦证券招聘", confidence=0.9, box=[0, 0, 100, 30])],
        full_text="德邦证券招聘 数据分析师 投递 hr@example.com",
    )


# ----------------------------------------------------------------------
# 1. OCR质量<0.45 → VLM 路径(路径2c)
# ----------------------------------------------------------------------
def test_vlm_path_triggered_for_low_ocr_quality():
    """OCR 质量<0.45 → 走 VLM 切片提取路径"""
    article = _make_parsed_article(
        text="短文本",
        images=[_make_image(height=600)],
    )
    pf = PrefilterResult(score=55, reasons=["命中"], decision="extract")

    mock_llm = MagicMock()
    mock_llm.extract_jobs.return_value = _mock_llm_response()

    mock_ocr = MagicMock()
    mock_ocr.ocr.return_value = _mock_ocr_result()

    mock_vlm = MagicMock()
    mock_vlm.extract_jobs_from_slices.return_value = _mock_vlm_response()

    extractor = Extractor(
        llm_provider=mock_llm, ocr_provider=mock_ocr, vlm_provider=mock_vlm,
    )
    with patch(
        "wehire_monitor.modules.extractor.extractor.calculate_ocr_quality",
        return_value=0.30,
    ):
        result = extractor.extract(article, pf)

    # VLM 被调用, LLM 未被调用
    assert result.vlm_calls == 1
    assert result.llm_calls == 0
    mock_vlm.extract_jobs_from_slices.assert_called_once()
    mock_llm.extract_jobs.assert_not_called()
    # 结果包含 VLM 提取的岗位
    assert result.article_type == "social_recruitment"
    assert len(result.jobs) == 1
    assert result.jobs[0].company_name == "德邦证券"


# ----------------------------------------------------------------------
# 2. 预算耗尽 → need_review
# ----------------------------------------------------------------------
def test_vlm_skipped_when_budget_exhausted():
    """VLM 预算耗尽 → 标记 need_review,不调用 VLM"""
    article = _make_parsed_article(
        text="短文本",
        images=[_make_image(height=600)],
    )
    pf = PrefilterResult(score=55, reasons=["命中"], decision="extract")

    mock_llm = MagicMock()
    mock_ocr = MagicMock()
    mock_ocr.ocr.return_value = _mock_ocr_result()

    mock_vlm = MagicMock()
    mock_vlm.extract_jobs_from_slices.return_value = _mock_vlm_response()

    mock_budget = MagicMock()
    mock_budget.is_exhausted.return_value = True

    extractor = Extractor(
        llm_provider=mock_llm, ocr_provider=mock_ocr, vlm_provider=mock_vlm,
        budget_manager=mock_budget,
    )
    with patch(
        "wehire_monitor.modules.extractor.extractor.calculate_ocr_quality",
        return_value=0.30,
    ):
        result = extractor.extract(article, pf)

    # VLM 未被调用, 标记 need_review
    assert result.vlm_calls == 0
    assert result.llm_calls == 0
    mock_vlm.extract_jobs_from_slices.assert_not_called()
    assert any("need_review" in w for w in result.warnings)
    assert result.article_type == "unknown"
    assert len(result.jobs) == 0


# ----------------------------------------------------------------------
# 3. 0.45<=quality<0.72 → VLM 复核(路径2b)
# ----------------------------------------------------------------------
def test_medium_quality_uses_vlm_review():
    """OCR 质量中等(0.45<=score<0.72)→ VLM 复核模式(不切片)"""
    article = _make_parsed_article(
        text="短文本",
        images=[_make_image(height=600)],
    )
    pf = PrefilterResult(score=55, reasons=["命中"], decision="extract")

    mock_llm = MagicMock()
    mock_ocr = MagicMock()
    mock_ocr.ocr.return_value = _mock_ocr_result()

    mock_vlm = MagicMock()
    mock_vlm.extract_jobs_from_slices.return_value = _mock_vlm_response()

    mock_slicer = MagicMock()

    extractor = Extractor(
        llm_provider=mock_llm, ocr_provider=mock_ocr, vlm_provider=mock_vlm,
        slicer=mock_slicer,
    )
    with patch(
        "wehire_monitor.modules.extractor.extractor.calculate_ocr_quality",
        return_value=0.55,
    ):
        result = extractor.extract(article, pf)

    # VLM 被调用
    assert result.vlm_calls == 1
    mock_vlm.extract_jobs_from_slices.assert_called_once()
    # 复核模式(force_slice=False)不触发切片
    mock_slicer.slice_image.assert_not_called()
    # LLM 未被调用
    assert result.llm_calls == 0


# ----------------------------------------------------------------------
# 4. quality>=0.72 → 文本 LLM(路径2a,不调VLM)
# ----------------------------------------------------------------------
def test_high_quality_uses_text_llm():
    """OCR 质量>=0.72 → 走文本 LLM 路径,不调用 VLM"""
    article = _make_parsed_article(
        text="短文本",
        images=[_make_image(height=600)],
    )
    pf = PrefilterResult(score=55, reasons=["命中"], decision="extract")

    mock_llm = MagicMock()
    mock_llm.extract_jobs.return_value = _mock_llm_response()

    mock_ocr = MagicMock()
    mock_ocr.ocr.return_value = _mock_ocr_result()

    mock_vlm = MagicMock()
    mock_vlm.extract_jobs_from_slices.return_value = _mock_vlm_response()

    extractor = Extractor(
        llm_provider=mock_llm, ocr_provider=mock_ocr, vlm_provider=mock_vlm,
    )
    with patch(
        "wehire_monitor.modules.extractor.extractor.calculate_ocr_quality",
        return_value=0.85,
    ):
        result = extractor.extract(article, pf)

    # LLM 被调用, VLM 未被调用
    assert result.llm_calls == 1
    assert result.vlm_calls == 0
    mock_llm.extract_jobs.assert_called_once()
    mock_vlm.extract_jobs_from_slices.assert_not_called()
    assert result.article_type == "social_recruitment"


# ----------------------------------------------------------------------
# 5. 无VLM provider → 回退文本LLM
# ----------------------------------------------------------------------
def test_no_vlm_falls_back_to_text_llm():
    """中等质量但无 VLM provider → 回退文本 LLM"""
    article = _make_parsed_article(
        text="短文本",
        images=[_make_image(height=600)],
    )
    pf = PrefilterResult(score=55, reasons=["命中"], decision="extract")

    mock_llm = MagicMock()
    mock_llm.extract_jobs.return_value = _mock_llm_response()

    mock_ocr = MagicMock()
    mock_ocr.ocr.return_value = _mock_ocr_result()

    # 无 VLM provider
    extractor = Extractor(
        llm_provider=mock_llm, ocr_provider=mock_ocr, vlm_provider=None,
    )
    with patch(
        "wehire_monitor.modules.extractor.extractor.calculate_ocr_quality",
        return_value=0.55,
    ):
        result = extractor.extract(article, pf)

    # 回退到文本 LLM
    assert result.llm_calls == 1
    assert result.vlm_calls == 0
    mock_llm.extract_jobs.assert_called_once()
    assert result.article_type == "social_recruitment"
